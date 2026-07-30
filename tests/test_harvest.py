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

    Runs exactly `rotations * ring` reply cycles: under `Mine`'s outcome-only
    windowing (see `Harvest.productive_clilocs`) the priming `tick()` below —
    no cursor, empty journal — carries no swing verdict and records NO sample,
    so every window entry comes from this loop's own replies.
    """
    from anima2.contract import TargetCursor

    stuck = [JournalEntry(0, "System", "", 0, 0, cliloc=stuck_cliloc)] if stuck_cliloc else []
    cursor = TargetCursor(target_type=1, cursor_id=1, cursor_flag=0)
    last_action = tick().action  # prime the first swing (no verdict, no sample)
    for _ in range(rotations * ring):
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

    # 4 of 5 replies are "no metal" (above Mine's outcome-only threshold, 0.75);
    # the fifth is a PRODUCTIVE reply — "you loosen some rocks" (503043, the bank
    # still yields) with the skill gain that rides along. A strict streak would
    # reset on every one of those good replies, never accumulating; the windowed
    # rate keeps its memory of the many "no" verdicts around them. (Below 75%
    # failure the ring is deliberately left alone now: a spot producing on a
    # quarter of its verdicts is a working spot, not a dead one.)
    productive = JournalEntry(0, "System", "", 0, 0, cliloc=503043)
    last_action = tick().action
    mining = 35.0
    for i in range(window):
        answer = tick(pending=cursor, mining=mining)
        assert isinstance(answer.action, TargetGround)
        if i % 5 < 4:  # 4 of every 5 replies are a confirmed "no resource"
            last_action = tick(journal=[stuck], mining=mining).action
        else:  # the fifth still finds ore-bearing rock — and gains skill
            mining += 0.1
            last_action = tick(journal=[productive], mining=mining).action
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


# --- the tool-gone confession (forge4, 2026-07-30) ------------------------------------
#
# Both staged pickaxes wore out (ServUO Pickaxe = 50 uses) and the miner spun the
# open-the-pack loop silently for 320+ ticks: no swings means no swing replies, so the
# relocation window NEVER fills — a toolless harvester is invisible to relocation BY
# CONSTRUCTION and must confess through `diagnose` instead.

def test_toolless_mining_confesses_after_the_threshold():
    mine = Mine()
    mem: dict = {}
    pack = _item(BACKPACK, 0x0E75, layer=0x15, container=1)
    for _ in range(mine.tool_missing_confess - 1):
        res = mine.step(_ctx(items=[pack], memory=mem))
        assert isinstance(res.action, Use) and res.action.serial == BACKPACK
    assert mine.diagnose(_ctx(items=[pack], memory=mem)) is None  # still "revealing"
    mine.step(_ctx(items=[pack], memory=mem))  # the tick that crosses the threshold
    diag = mine.diagnose(_ctx(items=[pack], memory=mem))
    assert diag is not None and "no tool" in diag and "cannot swing" in diag


def test_tool_reappearing_withdraws_the_confession():
    mine = Mine()
    mem: dict = {}
    pack = _item(BACKPACK, 0x0E75, layer=0x15, container=1)
    for _ in range(mine.tool_missing_confess + 3):
        mine.step(_ctx(items=[pack], memory=mem))
    assert mine.diagnose(_ctx(items=[pack], memory=mem)) is not None
    # A fresh pickaxe lands in the pack (bought, delivered, or GM-staged).
    ctx = _ctx(items=[pack, _item(0x222, PICKAXE, container=BACKPACK)], memory=mem)
    mine.step(ctx)
    assert mine.diagnose(ctx) is None
    assert "harvest_tool_missing" not in mem


def test_mine_smelt_deliver_surfaces_the_confession_not_just_its_own_layers():
    # MineSmeltDeliver.diagnose used to end in `return None`, swallowing Harvest's
    # layered diagnostics for every mine-chain agent — exactly the agent forge4 ran.
    from anima2.skills.smelt import MineSmeltDeliver

    skill = MineSmeltDeliver()
    mem: dict = {"harvest_tool_missing": skill.tool_missing_confess}
    pack = _item(BACKPACK, 0x0E75, layer=0x15, container=1)
    diag = skill.diagnose(_ctx(items=[pack], memory=mem))
    assert diag is not None and "no tool" in diag


def test_worn_out_equipped_tool_still_reaches_the_confession():
    # Review-caught BEFORE this ever ran live: for requires_equipped harvesters
    # (Chop) a worn-out axe leaves a stale remembered serial, and the equip
    # two-step consumes every tick — a counter inside the open-the-pack branch
    # would be unreachable exactly where the confession matters. The count now
    # happens at the tool lookup, before any branch can eat the tick.
    from anima2.skills.harvest import Chop

    chop = Chop()
    mem: dict = {"harvest_tool": 0x1234}  # remembered serial of the DELETED axe
    pack = _item(BACKPACK, 0x0E75, layer=0x15, container=1)
    for _ in range(chop.tool_missing_confess):
        res = chop.step(_ctx(items=[pack], memory=mem))
        assert res.action is not None  # it spins (PickUp/Equip) — that is the trap
    diag = chop.diagnose(_ctx(items=[pack], memory=mem))
    assert diag is not None and "no tool" in diag


def test_confession_is_cross_checked_against_the_observation_in_hand():
    # A stale counter alone must not confess: the counter only resets inside
    # step(), so a tool re-armed between skill turns (bought, delivered,
    # GM-staged) would otherwise keep confessing until step() next ran.
    mine = Mine()
    mem: dict = {"harvest_tool_missing": Mine.tool_missing_confess + 5}
    pack = _item(BACKPACK, 0x0E75, layer=0x15, container=1)
    armed = _ctx(items=[pack, _item(0x222, PICKAXE, container=BACKPACK)], memory=mem)
    assert mine.diagnose(armed) is None


def test_tool_gone_confession_outranks_the_delivery_giveup():
    # The giveup only clears by smelting past the giveup count — which needs the
    # very tool that is gone — so the other order masks the confession forever.
    from anima2.skills.smelt import MineSmeltDeliver

    skill = MineSmeltDeliver()
    pack = _item(BACKPACK, 0x0E75, layer=0x15, container=1)
    both = {"harvest_tool_missing": skill.tool_missing_confess,
            "smithy_drop": (10, 10), "deliver_giveup_ingots": 8}
    diag = skill.diagnose(_ctx(items=[pack], memory=both))
    assert diag is not None and "no tool" in diag
    # With the tool back in hand, the self-healing giveup layer speaks again.
    healed = _ctx(items=[pack, _item(0x222, PICKAXE, container=BACKPACK)],
                  memory=dict(both))
    diag2 = skill.diagnose(healed)
    assert diag2 is not None and "delivery route blocked" in diag2


# --- outcome-only relocation sampling (the forge5 probe, 2026-07-30) ------------------
#
# A live probe at the trade mine spot watched the legacy sampler crawl: the probe
# ring there is ~half unmineable tiles ("You can't mine that" 501862 / LOS 500237),
# and those replies appended `0` — "not stuck" — so a fully DEAD bank rated ~0.5
# and needed 140+ ticks to confess. Samples are now real swing verdicts only.

def _swing_reply(mine, mem, cliloc, pack_items=()):
    """One swing-reply tick: pending_target None, journal carrying `cliloc`."""
    items = [_item(0x222, PICKAXE, container=BACKPACK), *pack_items]
    ctx = _ctx(items=items, memory=mem)
    ctx.obs.new_journal.append(
        JournalEntry(serial=0, name="", text="", msg_type=0xC1, hue=0, cliloc=cliloc))
    return mine.step(ctx)


def test_dead_bank_confesses_within_one_window_despite_invalid_tiles():
    from anima2.contract import WalkTo

    mine = Mine()
    mem: dict = {}
    window = len(mine.probe_offsets) * mine.stuck_window_rotations
    fired = None
    for i in range(window + 2):
        # A dead spot alternates "no metal" with "can't mine that" — BOTH are
        # failure evidence now; neither dilutes the other.
        res = _swing_reply(mine, mem, 503040 if i % 2 == 0 else 501862)
        if isinstance(res.action, WalkTo):
            fired = i
            break
    assert fired is not None, "a dead bank never triggered relocation"
    assert fired <= window + 1


def test_healthy_half_invalid_spot_stays_put():
    mine = Mine()
    mem: dict = {}
    window = len(mine.probe_offsets) * mine.stuck_window_rotations
    from anima2.contract import WalkTo

    for i in range(window * 3):
        # Half the ring is unmineable (501862) but the OTHER half yields ore
        # (1007072 success / 503043 fail-with-ore): rate ~0.5, below the 0.75
        # threshold — relocating away from a producing spot would be a bug.
        cliloc = 501862 if i % 2 == 0 else (1007072 if i % 4 == 1 else 503043)
        res = _swing_reply(mine, mem, cliloc)
        assert not isinstance(res.action, WalkTo), f"relocated at i={i} from a producing spot"


def test_reply_less_ticks_carry_no_verdict_and_do_not_dilute():
    mine = Mine()
    mem: dict = {}
    # 10 pure no-metal verdicts...
    for _ in range(10):
        _swing_reply(mine, mem, 503040)
    # ...then a burst of silent (server-lag) ticks with NO reply at all.
    for _ in range(10):
        ctx = _ctx(items=[_item(0x222, PICKAXE, container=BACKPACK)], memory=mem)
        mine.step(ctx)
    recent = mem["harvest_recent_stuck"]
    assert sum(recent) == 10 and len(recent) == 10, (
        "silence must append nothing — the legacy sampler's 0-on-no-reply dilution")


def test_completed_relocation_moves_the_delivery_return_spot():
    # Without this, every haul ends with a walk back to the condemned tile, a
    # full re-confession window, and a rotated-direction re-relocation — a
    # random-walk between hauls.
    from anima2.contract import PlayerView, Position, SkillView
    from anima2.skills.smelt import MineSmeltDeliver

    skill = MineSmeltDeliver()
    mem: dict = {"smithy_drop": (90, 90), "miner_home": (100, 100),
                 "harvest_relocating": True,
                 "harvest_relocate_target": (108, 106)}
    pickaxe = _item(0x222, PICKAXE, container=BACKPACK)

    def _obs_at(x, y):
        return Observation(
            player=PlayerView(serial=1, pos=Position(x, y, 0)),
            items=[pickaxe],
            skills=[SkillView(id=45, value=35.0, base=35.0, cap=100.0, lock=0)],
        )

    # Tick 1: arrival tile — the relocation leg completes and leaves its note.
    skill.step(SkillContext(obs=_obs_at(108, 106), persona=Persona(name="Grimm"),
                            memory=mem))
    # Tick 2: the wrapper consumes the note and re-homes the delivery return.
    skill.step(SkillContext(obs=_obs_at(108, 106), persona=Persona(name="Grimm"),
                            memory=mem))
    assert mem["miner_home"] == (108, 106)
    assert "harvest_relocated_to" not in mem


def test_verdict_landing_on_a_cursor_open_tick_still_counts():
    # forge8 (2026-07-30): at real agent cadence the verdict cliloc reliably
    # lands in the same observation batch as the NEXT swing's already-open
    # cursor — the legacy `pending_target is None` gate discarded essentially
    # every verdict, so a full healthy mining day ran with an EMPTY window and
    # the end-of-day dead vein sat unrelocated for 278 status samples. The
    # probe missed it because 0.4s ticks observe faster than the reply cycle.
    from anima2.contract import WalkTo

    mine = Mine()
    mem: dict = {}
    window = len(mine.probe_offsets) * mine.stuck_window_rotations
    cursor = TargetCursor(target_type=1, cursor_id=1, cursor_flag=0)
    pickaxe = _item(0x222, PICKAXE, container=BACKPACK)
    for _ in range(window):
        ctx = _ctx(items=[pickaxe], pending=cursor, memory=mem)
        ctx.obs.new_journal.append(
            JournalEntry(serial=0, name="", text="", msg_type=0xC1, hue=0,
                         cliloc=503040))
        res = mine.step(ctx)
        assert isinstance(res.action, TargetGround)  # still answers the cursor
    recent = mem["harvest_recent_stuck"]
    assert len(recent) == window and sum(recent) == window
    # The trigger itself fires on the next between-swings tick.
    res = mine.step(_ctx(items=[pickaxe], memory=mem))
    assert isinstance(res.action, WalkTo)
