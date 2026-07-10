"""The Mine skill's decision logic, exercised with hand-built observations."""

from anima2.contract import (
    ItemView,
    JournalEntry,
    Observation,
    PlayerView,
    Position,
    SkillView,
    TargetCursor,
    TargetGround,
    Use,
)
from anima2.persona import Persona
from anima2.skills import Mine
from anima2.skills.base import SkillContext

PICKAXE = 0x0E86
BACKPACK = 0x40001453


def _item(serial, graphic, *, layer=0, container=None):
    return ItemView(serial=serial, graphic=graphic, amount=1, pos=Position(),
                    container=container, layer=layer, distance=0)


def _ctx(items=(), pending=None, mining=None, direction=2, memory=None):
    skills = [SkillView(id=45, value=mining, base=mining, cap=100.0, lock=0)] if mining else []
    obs = Observation(
        player=PlayerView(serial=1, pos=Position(100, 100, 0), direction=direction),
        items=list(items),
        skills=skills,
        pending_target=pending,
    )
    return SkillContext(obs=obs, persona=Persona(name="Grimm"),
                        memory=memory if memory is not None else {})


def test_swings_pickaxe_when_tool_visible():
    ctx = _ctx(items=[_item(0x222, PICKAXE, container=BACKPACK)])
    res = Mine().step(ctx)
    assert isinstance(res.action, Use) and res.action.serial == 0x222


def test_opens_backpack_when_no_tool_visible():
    # container=1 — a worn item's container is its wearer (here, the player, serial 1).
    ctx = _ctx(items=[_item(BACKPACK, 0x0E75, layer=0x15, container=1)])  # only the closed pack
    res = Mine().step(ctx)
    assert isinstance(res.action, Use) and res.action.serial == BACKPACK


def test_answers_cursor_with_probed_tile():
    # With a cursor open, target the current probe offset (PROBE_OFFSETS[0] = (-1,-1)).
    from anima2.skills.harvest import PROBE_OFFSETS

    ctx = _ctx(
        items=[_item(0x222, PICKAXE)],
        pending=TargetCursor(target_type=1, cursor_id=7, cursor_flag=0),
    )
    res = Mine().step(ctx)
    assert isinstance(res.action, TargetGround)
    odx, ody = PROBE_OFFSETS[0]
    assert (res.action.x, res.action.y) == (100 + odx, 100 + ody)
    # The probe ring covers reach 2 (24 tiles around the player).
    assert len(PROBE_OFFSETS) == 24


def test_skill_gain_rewards():
    skill = Mine()
    mem = {}
    skill.step(_ctx(items=[_item(0x222, PICKAXE)], mining=35.0, memory=mem))  # seed baseline
    res = skill.step(_ctx(items=[_item(0x222, PICKAXE)], mining=35.2, memory=mem))
    assert abs(res.reward - 0.2) < 1e-3  # rewarded the skill gain


def test_probe_rotates_each_swing():
    skill = Mine()
    mem = {}
    skill.step(_ctx(items=[_item(0x222, PICKAXE)], memory=mem))
    assert mem["harvest_probe"] == 1
    skill.step(_ctx(items=[_item(0x222, PICKAXE)], memory=mem))
    assert mem["harvest_probe"] == 2


def test_not_runnable_without_tool_or_pack():
    assert not Mine().can_run(_ctx(items=[]))


def test_chop_cycles_grove_on_depletion():
    from anima2.contract import (
        JournalEntry,
        Observation,
        PlayerView,
        Position,
        TargetCursor,
        TargetGround,
    )
    from anima2.skills import Chop
    from anima2.skills.harvest import NODE_DEPLETED_CLILOC

    nodes = [(10, 10, 0, 0x0CCA), (20, 20, 0, 0x0CCB)]
    mem: dict = {"harvest_nodes": nodes}

    def ctx(journal=()):
        obs = Observation(
            player=PlayerView(serial=9, pos=Position(11, 11, 0)),
            pending_target=TargetCursor(target_type=1, cursor_id=7, cursor_flag=0),
            new_journal=list(journal),
        )
        return SkillContext(obs=obs, persona=Persona(name="B"), memory=mem)

    # Targets the first tree in the grove.
    r = Chop().step(ctx())
    assert isinstance(r.action, TargetGround) and (r.action.x, r.action.y) == (10, 10)
    # A "not enough wood" message advances to the next tree (no walking).
    depleted = JournalEntry(0, "System", "", 0, 0, cliloc=NODE_DEPLETED_CLILOC)
    r2 = Chop().step(ctx([depleted]))
    assert (r2.action.x, r2.action.y) == (20, 20)


def _mine_ticker(mem, mining=35.0):
    """Drive `Mine` tick by tick against a shared `mem`, mirroring the real
    swing → cursor → reply state machine (a `pickaxe` always visible, so the
    only variable across calls is `pending`/`journal`)."""
    skill = Mine()
    pickaxe = _item(0x222, PICKAXE, container=BACKPACK)

    def tick(pending=None, journal=()):
        obs = Observation(
            player=PlayerView(serial=1, pos=Position(100, 100, 0)),
            items=[pickaxe],
            skills=[SkillView(id=45, value=mining, base=mining, cap=100.0, lock=0)],
            pending_target=pending,
            new_journal=list(journal),
        )
        return skill.step(SkillContext(obs=obs, persona=Persona(name="Grimm"), memory=mem))

    return tick


def _run_rotations(tick, mem, *, rotations, ring, stuck_cliloc=None):
    """`rotations` full probe-ring rotations of swing → cursor → reply: swing
    → cursor opens → answer → reply carries `stuck_cliloc` (or nothing, a
    plain miss, if `None`). Returns the last action seen.

    Runs one fewer than `rotations * ring` reply cycles: the very first
    `tick()` call below (priming the initial swing, before any cursor has
    ever opened) also records one "not stuck" window sample of its own
    (`pending_target is None` and an empty journal) — harmless/correct in
    real use (a session's first-ever tick has nothing to report yet either),
    but it means the window fills one reply earlier than a naive
    `rotations * ring` count of *this loop's own* replies would suggest.
    """
    from anima2.contract import TargetCursor

    stuck = [JournalEntry(0, "System", "", 0, 0, cliloc=stuck_cliloc)] if stuck_cliloc else []
    cursor = TargetCursor(target_type=1, cursor_id=1, cursor_flag=0)
    last_action = tick().action  # prime the first swing
    for _ in range(rotations * ring - 1):
        answer = tick(pending=cursor)
        assert isinstance(answer.action, TargetGround)
        last_action = tick(journal=stuck).action
    return last_action


def test_mine_relocates_after_sustained_no_resource():
    """PHASE4.md item 4's freeze, condition 1 (live-confirmed): every probed
    tile shares one exhausted `HarvestBank` — mining's `NoResourcesMessage`
    (cliloc 503040, "There is no metal here to mine.") on every reply, zero
    reward, for the whole rate window. Before the fix this looped forever,
    spending a real `Use`/`TargetGround` every tick for nothing; the fix now
    walks away (`WalkTo`) instead of continuing to probe the same dead ring."""
    from anima2.skills.harvest import PROBE_OFFSETS, RELOCATE_OFFSETS
    from anima2.contract import WalkTo

    mem: dict = {}
    tick = _mine_ticker(mem)
    ring = len(PROBE_OFFSETS)

    last_action = _run_rotations(tick, mem, stuck_cliloc=503040, rotations=Mine.stuck_window_rotations, ring=ring)
    assert isinstance(last_action, WalkTo)
    assert mem["harvest_relocating"] is True
    dx, dy = RELOCATE_OFFSETS[0]
    assert (last_action.x, last_action.y) == (100 + dx, 100 + dy)
    diag = Mine().diagnose(SkillContext(
        obs=Observation(player=PlayerView(serial=1, pos=Position(100, 100, 0)),
                        items=[_item(0x222, PICKAXE, container=BACKPACK)]),
        persona=Persona(name="Grimm"), memory=mem,
    ))
    assert diag is not None and "relocating" in diag


def test_mine_relocates_after_sustained_pack_full():
    """PHASE4.md item 4's freeze, condition 2 (live-confirmed): the dig
    succeeds server-side but the pack has no room (cliloc 1010481, "Your
    backpack is full, so the ore you mined is lost.") — a different "no" than
    resource exhaustion, same busy-wait risk, same gate."""
    from anima2.skills.harvest import PROBE_OFFSETS
    from anima2.contract import WalkTo

    mem: dict = {}
    tick = _mine_ticker(mem)
    ring = len(PROBE_OFFSETS)

    last_action = _run_rotations(tick, mem, stuck_cliloc=1010481, rotations=Mine.stuck_window_rotations, ring=ring)
    assert isinstance(last_action, WalkTo)
    assert mem["harvest_relocating"] is True


def test_mine_relocate_arrives_and_resumes_harvesting():
    """A relocation leg isn't a permanent give-up: once the walk actually
    arrives (position deltas, mirroring `GoTo`'s own progress signal — see
    that class's docstring for why distance-to-target isn't used), the skill
    resumes swinging on its own — no external actor needed."""
    from anima2.skills.harvest import PROBE_OFFSETS, RELOCATE_OFFSETS

    mem: dict = {}
    tick = _mine_ticker(mem)
    ring = len(PROBE_OFFSETS)
    _run_rotations(tick, mem, stuck_cliloc=503040, rotations=Mine.stuck_window_rotations, ring=ring)
    assert mem["harvest_relocating"] is True
    dx, dy = RELOCATE_OFFSETS[0]
    tx, ty = 100 + dx, 100 + dy

    skill = Mine()
    pickaxe = _item(0x222, PICKAXE, container=BACKPACK)

    def tick_at(x, y):
        obs = Observation(
            player=PlayerView(serial=1, pos=Position(x, y, 0)),
            items=[pickaxe],
            skills=[SkillView(id=45, value=35.0, base=35.0, cap=100.0, lock=0)],
        )
        return skill.step(SkillContext(obs=obs, persona=Persona(name="Grimm"), memory=mem))

    # Walking closer each tick (position changes -> no stall) until arrival.
    r = None
    for step_i in range(1, 13):
        x = 100 + round(dx * step_i / 12)
        y = 100 + round(dy * step_i / 12)
        r = tick_at(x, y)
    r = tick_at(tx, ty)  # exact arrival
    assert mem.get("harvest_relocating") is not True
    # Resumes ordinary harvesting from the new spot — the very next tick swings.
    r2 = tick_at(tx, ty)
    assert isinstance(r2.action, Use)
    assert r is not None


def test_mine_relocate_gives_up_after_stall_and_resumes():
    """A relocation route that never moves at all (blocked, or no route
    driver — e.g. under `MockBody`) gives up after `relocate_stall_limit`
    unmoved ticks and resumes harvesting from wherever it is, rather than
    wedging forever — no worse than the pre-fix behaviour."""
    from anima2.skills.harvest import PROBE_OFFSETS

    mem: dict = {}
    tick = _mine_ticker(mem)
    ring = len(PROBE_OFFSETS)
    _run_rotations(tick, mem, stuck_cliloc=503040, rotations=Mine.stuck_window_rotations, ring=ring)
    assert mem["harvest_relocating"] is True

    r = None
    for _ in range(Mine.relocate_stall_limit + 1):
        r = tick()  # player position never changes (stuck at (100,100) always)
    assert mem.get("harvest_relocating") is not True  # gave up
    assert r is not None


def test_mine_detects_partial_exhaustion_despite_interspersed_skill_gain():
    """The bug a first fix attempt missed (P0 hardening's own live gate,
    docs/PHASE4.md item 4's follow-up): a probe ring straddling a bank
    boundary is only *partly* dead — most swings fail with the "no metal"
    cliloc, but the occasional one still lands on a live tile and gains
    skill. A strict "any reward resets the streak" design (the first attempt)
    never crossed its threshold under this interleaving, even after hundreds
    of ticks, netting only a handful of ore over a full session — this is
    exactly the failure the P0 hardening's own live gate exposed. The
    windowed *rate* design must still trigger: skill gain lowers the rate, it
    doesn't zero out the window's memory the way a streak-reset did."""
    from anima2.contract import TargetCursor, WalkTo
    from anima2.skills.harvest import PROBE_OFFSETS

    mem: dict = {}
    ring = len(PROBE_OFFSETS)
    window = ring * Mine.stuck_window_rotations
    skill = Mine()
    pickaxe = _item(0x222, PICKAXE, container=BACKPACK)
    cursor = TargetCursor(target_type=1, cursor_id=1, cursor_flag=0)
    stuck = JournalEntry(0, "System", "", 0, 0, cliloc=503040)

    def tick(pending=None, journal=(), mining=35.0):
        obs = Observation(
            player=PlayerView(serial=1, pos=Position(100, 100, 0)),
            items=[pickaxe],
            skills=[SkillView(id=45, value=mining, base=mining, cap=100.0, lock=0)],
            pending_target=pending,
            new_journal=list(journal),
        )
        return skill.step(SkillContext(obs=obs, persona=Persona(name="Grimm"), memory=mem))

    # 40% of replies are "no metal" (well above stuck_rate_threshold=0.3); the
    # other 60% gain skill (a reward every time) — a mix a strict streak would
    # reset on every single one of those "good" replies, never accumulating.
    # (window - 1): the priming `tick()` call below also records one "not
    # stuck" sample of its own — see `_run_rotations`'s own docstring.
    last_action = tick().action
    mining = 35.0
    for i in range(window - 1):
        answer = tick(pending=cursor, mining=mining)
        assert isinstance(answer.action, TargetGround)
        if i % 5 < 2:  # 2 of every 5 replies are a confirmed "no resource"
            last_action = tick(journal=[stuck], mining=mining).action
        else:  # the rest gain skill — ordinary, expected mining variance
            mining += 0.1
            last_action = tick(mining=mining).action
    assert isinstance(last_action, WalkTo)
    assert mem["harvest_relocating"] is True


def test_chop_unaffected_by_mining_no_resource_cliloc():
    """`Chop` has no `no_resource_clilocs` configured (wood depletion is
    already handled by `NODE_DEPLETED_CLILOC`'s own node-cycling) — feeding it
    mining's cliloc (503040, meaningless to lumberjacking) must never trip the
    new relocate machinery. Confirms the mechanism is genuinely opt-in, not a
    blanket behavior change for every `Harvest` subclass."""
    from anima2.contract import TargetCursor
    from anima2.skills import Chop
    from anima2.skills.harvest import PROBE_OFFSETS

    axe = _item(0x333, 0x0F43, layer=2, container=1)  # already equipped
    mem: dict = {}
    skill = Chop()
    cursor = TargetCursor(target_type=1, cursor_id=1, cursor_flag=0)
    stray = JournalEntry(0, "System", "", 0, 0, cliloc=503040)

    def tick(pending=None, journal=()):
        obs = Observation(
            player=PlayerView(serial=1, pos=Position(100, 100, 0)),
            items=[axe],
            pending_target=pending,
            new_journal=list(journal),
        )
        return skill.step(SkillContext(obs=obs, persona=Persona(name="B"), memory=mem))

    last_action = tick().action
    for _ in range(len(PROBE_OFFSETS) * Chop.stuck_window_rotations * 2):  # well past any window
        answer = tick(pending=cursor)
        assert isinstance(answer.action, TargetGround)
        last_action = tick(journal=[stray]).action
    assert isinstance(last_action, Use)  # never relocated — kept swinging normally
    assert mem.get("harvest_relocating") is not True


def test_fish_rewards_each_catch():
    from anima2.contract import ItemView, JournalEntry, Observation, PlayerView
    from anima2.skills import Fish
    from anima2.skills.harvest import CATCH_CLILOC, FISH_OFFSETS

    pole = ItemView(serial=1, graphic=0x0DC0, amount=1, pos=Position(),
                    container=None, layer=0, distance=0)
    obs = Observation(
        player=PlayerView(serial=9, pos=Position(0, 0, 0)),
        items=[pole],
        new_journal=[JournalEntry(0, "", ": fish", 0, 0, cliloc=CATCH_CLILOC)],
    )
    res = Fish().step(SkillContext(obs=obs, persona=Persona(name="M"), memory={}))
    assert res.reward >= 1.0  # the catch was rewarded
    assert len(FISH_OFFSETS) == 80  # casts up to 4 tiles (reach-4 ring)
