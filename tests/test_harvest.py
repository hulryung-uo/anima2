"""The Mine skill's decision logic, exercised with hand-built observations."""

from anima2.contract import (
    ItemView,
    JournalEntry,
    Observation,
    PlayerView,
    Position,
    TargetGround,
    TargetCursor,
    Use,
)
from anima2.persona import Persona
from anima2.skills import Mine
from anima2.skills.base import SkillContext, Status

PICKAXE = 0x0E86
BACKPACK = 0x40001453


def _item(serial, graphic, *, layer=0, container=None):
    return ItemView(serial=serial, graphic=graphic, amount=1, pos=Position(),
                    container=container, layer=layer, distance=0)


def _ctx(items=(), pending=None, journal=(), direction=2):
    obs = Observation(
        player=PlayerView(serial=1, pos=Position(100, 100, 0), direction=direction),
        items=list(items),
        new_journal=list(journal),
        pending_target=pending,
    )
    return SkillContext(obs=obs, persona=Persona(name="Grimm"), memory={})


def test_swings_pickaxe_when_tool_visible():
    ctx = _ctx(items=[_item(0x222, PICKAXE, container=BACKPACK)])
    res = Mine().step(ctx)
    assert isinstance(res.action, Use) and res.action.serial == 0x222


def test_opens_backpack_when_no_tool_visible():
    ctx = _ctx(items=[_item(BACKPACK, 0x0E75, layer=0x15)])  # only the closed pack
    res = Mine().step(ctx)
    assert isinstance(res.action, Use) and res.action.serial == BACKPACK


def test_answers_cursor_with_facing_tile():
    # Facing East (dir 2 → +1 x): target should be (101, 100).
    ctx = _ctx(
        items=[_item(0x222, PICKAXE)],
        pending=TargetCursor(target_type=1, cursor_id=7, cursor_flag=0),
        direction=2,
    )
    res = Mine().step(ctx)
    assert isinstance(res.action, TargetGround)
    assert (res.action.x, res.action.y) == (101, 100)


def test_success_journal_rewards_and_continues():
    ctx = _ctx(
        items=[_item(0x222, PICKAXE)],
        journal=[JournalEntry(0, "System", "You dig some ore and put it in your backpack.", 0, 0)],
    )
    res = Mine().step(ctx)
    assert res.status is Status.RUNNING and res.reward > 0


def test_barren_tile_fails():
    ctx = _ctx(
        items=[_item(0x222, PICKAXE)],
        journal=[JournalEntry(0, "System", "There is no metal here to mine.", 0, 0)],
    )
    assert Mine().step(ctx).status is Status.FAILURE


def test_not_runnable_without_tool_or_pack():
    assert not Mine().can_run(_ctx(items=[]))
