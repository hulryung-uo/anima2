"""ProcessLogs (log->board conversion) decision logic, hand-built observations.

Mirrors the smelt-phase tests in `test_smelt.py`, inverted: smelting USES the ore
pile and TARGETS the forge; ProcessLogs USES the axe and TARGETS the log pile.
"""

from anima2.contract import (
    ItemView,
    Observation,
    PlayerView,
    Position,
    TargetCursor,
    TargetObject,
    Use,
)
from anima2.persona import Persona
from anima2.skills import ProcessLogs
from anima2.skills.base import SkillContext, Status
from anima2.skills.harvest import AXE_GRAPHICS, BACKPACK_LAYER
from anima2.skills.woodwork import BOARD_GRAPHIC, BOARD_GRAPHICS, LOG_GRAPHIC

HATCHET = 0x0F43  # a hatchet — a graphic in AXE_GRAPHICS
BACKPACK = 0x40001453
AXE_SERIAL = 0x300


def _item(serial, graphic, *, layer=0, container=None, amount=1, distance=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=distance)


def _backpack():
    # container=1 — a worn item's container is its wearer (here, the player, serial 1).
    return _item(BACKPACK, 0x0E75, layer=BACKPACK_LAYER, container=1)


def _axe():
    return _item(AXE_SERIAL, HATCHET, container=BACKPACK)


def _logs(serial, amount=1):
    return _item(serial, LOG_GRAPHIC, container=BACKPACK, amount=amount)


def _boards(serial, amount=1):
    return _item(serial, BOARD_GRAPHIC, container=BACKPACK, amount=amount)


def _cursor():
    return TargetCursor(target_type=0, cursor_id=1, cursor_flag=0)


def _ctx(items, pending=None, memory=None, pos=Position(100, 100, 0)):
    obs = Observation(player=PlayerView(serial=1, pos=pos),
                      items=list(items), pending_target=pending)
    return SkillContext(obs=obs, persona=Persona(name="Bjorn"),
                        memory=memory if memory is not None else {})


def test_log_and_board_graphics_match_servuo():
    # ServUO Scripts/Items/Resource/Log.cs base(0x1BDD) and Board.cs base(0x1BD7).
    assert LOG_GRAPHIC == 0x1BDD
    assert BOARD_GRAPHIC == 0x1BD7
    # A single graphic, not the four stack-art variants ore/ingots use.
    assert BOARD_GRAPHICS == frozenset({0x1BD7})
    # The gesture's tool is the lumberjack's axe (harvest.py's AXE_GRAPHICS).
    assert HATCHET in AXE_GRAPHICS


def test_uses_the_axe_when_a_log_pile_is_in_the_pack():
    items = [_backpack(), _axe(), _logs(0x400, amount=20)]
    res = ProcessLogs().step(_ctx(items))
    assert isinstance(res.action, Use) and res.action.serial == AXE_SERIAL


def test_targets_the_log_pile_when_the_cursor_opens():
    logs = _logs(0x400, amount=20)
    items = [_backpack(), _axe(), logs]
    res = ProcessLogs().step(_ctx(items, pending=_cursor()))
    assert isinstance(res.action, TargetObject) and res.action.serial == logs.serial


def test_no_axe_fails_closed():
    # Logs but no axe — cannot process without the tool.
    items = [_backpack(), _logs(0x400, amount=20)]
    skill = ProcessLogs()
    ctx = _ctx(items)
    assert skill.can_run(ctx) is False
    res = skill.step(ctx)
    assert res.status is Status.FAILURE
    assert res.action is None


def test_no_logs_is_idle():
    # An axe but nothing to process — idle (no action), still runnable.
    items = [_backpack(), _axe()]
    skill = ProcessLogs()
    ctx = _ctx(items)
    assert skill.can_run(ctx) is True
    res = skill.step(ctx)
    assert res.status is Status.RUNNING
    assert res.action is None


def test_stray_cursor_without_logs_idles_rather_than_targeting_nothing():
    # A cursor is open but no logs remain to target — idle and let it clear,
    # never emit a TargetObject at nothing.
    items = [_backpack(), _axe()]
    res = ProcessLogs().step(_ctx(items, pending=_cursor()))
    assert res.action is None
    assert res.status is Status.RUNNING


def test_rewards_board_gain_on_conversion():
    axe = _axe()
    mem = {}

    items1 = [_backpack(), axe, _logs(0x400, amount=20)]
    res1 = ProcessLogs().step(_ctx(items1, memory=mem))  # seeds the board-count baseline
    assert res1.reward == 0.0

    # The conversion landed: the logs are gone and 20 boards arrived.
    items2 = [_backpack(), axe, _boards(0x500, amount=20)]
    res2 = ProcessLogs().step(_ctx(items2, memory=mem))
    assert res2.reward == 20.0


def test_final_board_reward_survives_logs_running_out_same_tick():
    # One tick of observation lag: the board gain from the last TargetObject and
    # the log-pile scan coming up empty land on the same observation. That reward
    # must still reach this tick's (idle) result, not be silently dropped.
    axe = _axe()
    mem = {"process_boards": 0}
    items = [_backpack(), axe, _boards(0x500, amount=20)]  # boards arrived, no logs left
    res = ProcessLogs().step(_ctx(items, memory=mem))
    assert res.action is None  # nothing left to process
    assert res.reward == 20.0  # the conversion is still credited


def test_processes_each_log_pile_one_at_a_time():
    axe = _axe()
    mem = {}
    log1 = _logs(0x400, amount=10)
    log2 = _logs(0x401, amount=8)
    items = [_backpack(), axe, log1, log2]

    # First pile: Use(axe) -> cursor -> TargetObject(pile 1).
    use = ProcessLogs().step(_ctx(items, memory=mem))
    assert isinstance(use.action, Use) and use.action.serial == AXE_SERIAL

    target = ProcessLogs().step(_ctx(items, memory=mem, pending=_cursor()))
    assert isinstance(target.action, TargetObject) and target.action.serial == log1.serial

    # Pile 1 converted to boards; pile 2 remains -> Use(axe) again for the next.
    items2 = [_backpack(), axe, log2, _boards(0x500, amount=10)]
    use2 = ProcessLogs().step(_ctx(items2, memory=mem))
    assert isinstance(use2.action, Use) and use2.action.serial == AXE_SERIAL

    target2 = ProcessLogs().step(_ctx(items2, memory=mem, pending=_cursor()))
    assert isinstance(target2.action, TargetObject) and target2.action.serial == log2.serial


def test_no_backpack_visible_reports_no_logs():
    # With no backpack in view, there are no pack logs to process — idle, not a crash.
    items = [_axe()]  # axe visible (worn/loose), but no backpack item
    skill = ProcessLogs()
    ctx = _ctx(items)
    res = skill.step(ctx)
    assert res.action is None and res.status is Status.RUNNING


def test_diagnose_reports_missing_axe_then_missing_logs():
    skill = ProcessLogs()
    assert skill.diagnose(_ctx([_backpack(), _logs(0x400, amount=5)])) == (
        "no axe to process logs with"
    )
    assert skill.diagnose(_ctx([_backpack(), _axe()])) == "no logs in the pack to process"
    assert skill.diagnose(_ctx([_backpack(), _axe(), _logs(0x400, amount=5)])) is None
