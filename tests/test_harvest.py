"""The Mine skill's decision logic, exercised with hand-built observations."""

from anima2.contract import (
    ItemView,
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
