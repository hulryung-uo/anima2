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


# --- Brick 2: the lumberjack capability skills (generalized market machinery) ------

from anima2.contract import (  # noqa: E402
    BuyItems,
    MobileView,
    PopupEntry,
    PopupMenu,
    SellItems,
    ShopBuy,
    ShopBuyEntry,
    ShopSell,
    ShopSellItem,
)
from anima2.skills.market import SELL_CLILOC  # noqa: E402
from anima2.skills.woodwork import (  # noqa: E402
    HATCHET_GRAPHIC,
    BuyHatchet,
    ProcessLogsGoal,
    SellBoards,
)

CARPENTER = (10, 0)
WEAPONSMITH = (0, 10)
VENDOR_SERIAL = 0xAAA1
VENDOR_MOBILE = 0xBBB1
BOARD_SERIAL = 0x700
TOOL_CONTAINER = 0xCCC1
HATCHET_OFFER_SERIAL = 0xDD44


def _mobile(serial, x, y):
    return MobileView(serial=serial, name="", pos=Position(x, y, 0), body=0x190,
                      notoriety=1, hits=10, hits_max=10, distance=0)


def _popup(serial, clilocs):
    return PopupMenu(serial=serial, entries=[PopupEntry(index=i, cliloc=c) for i, c in enumerate(clilocs)])


def _mctx(items, *, memory, pos, goal_id, mobiles=(), popup=None, shop_sell=None, shop_buy=None):
    obs = Observation(player=PlayerView(serial=1, pos=pos), items=list(items),
                      mobiles=list(mobiles), popup=popup, shop_sell=shop_sell, shop_buy=shop_buy)
    return SkillContext(obs=obs, persona=Persona(name="Bjorn"), memory=memory, goal_id=goal_id)


# --- sell_boards: sells boards (not daggers) via the generalized sold_graphic -----


def test_sell_boards_is_configured_for_boards_at_the_sell_vendor():
    assert SellBoards.sold_graphic == BOARD_GRAPHIC
    assert SellBoards.sell_threshold == 20
    assert SellBoards.vendor_spot_key == "vendor_spot"  # the Carpenter (sell vendor)


def test_sell_boards_offers_boards_from_the_shop_sell_window():
    # The vendor's SELL list carries boards (+ an unrelated item); SellBoards must
    # offer only the boards, by `self.sold_graphic`, and record goal evidence.
    vendor = _mobile(VENDOR_MOBILE, *CARPENTER)
    sell = ShopSell(vendor=VENDOR_SERIAL, items=[
        ShopSellItem(serial=BOARD_SERIAL, graphic=BOARD_GRAPHIC, hue=0, amount=20, price=2, name="board"),
        ShopSellItem(serial=0x40, graphic=0x0F52, hue=0, amount=1, price=10, name="dagger"),
    ])
    mem = {"vendor_spot": CARPENTER, "bs_stand": (0, 0)}
    before = [_backpack(), _boards(BOARD_SERIAL, amount=20)]
    skill = SellBoards()

    skill.step(_mctx(before, memory=mem, pos=Position(*CARPENTER, 0), mobiles=[vendor], goal_id=17))  # request
    popup = _popup(VENDOR_MOBILE, [SELL_CLILOC])
    skill.step(_mctx(before, memory=mem, pos=Position(*CARPENTER, 0), mobiles=[vendor], popup=popup, goal_id=17))
    offer = skill.step(_mctx(before, memory=mem, pos=Position(*CARPENTER, 0), mobiles=[vendor], shop_sell=sell, goal_id=17))

    assert isinstance(offer.action, SellItems)
    assert offer.action.items == [(BOARD_SERIAL, 20)]  # boards only, never the dagger
    assert mem["cap_sell_sent_goal_id"] == 17
    assert mem["cap_sell_expected_gold"] == 20 * 2
    assert mem["cap_sell_offered_items"] == ((BOARD_SERIAL, 20, 2),)


# --- buy_hatchet: buys a hatchet from a SEPARATE tool vendor -----------------------


def _tool_buy_window(hatchet_amount=20):
    # The live WeaponSmith sells EIGHT distinct axes whose graphic is in
    # AXE_GRAPHICS (a saw distractor too). buy_hatchet must resolve ONLY the
    # single scalar `offer_graphic` (0x0F44), never the AXE_GRAPHICS set.
    return ShopBuy(vendor=VENDOR_SERIAL, container=TOOL_CONTAINER, entries=[
        ShopBuyEntry(price=15, name="saw", serial=0xDD00, graphic=0x1034, amount=1),
        ShopBuyEntry(price=27, name="axe", serial=0xDD43, graphic=0x0F43, amount=20),
        ShopBuyEntry(price=25, name="hatchet", serial=HATCHET_OFFER_SERIAL,
                     graphic=HATCHET_GRAPHIC, amount=hatchet_amount),
        ShopBuyEntry(price=52, name="battle axe", serial=0xDD4B, graphic=0x0F4B, amount=20),
        ShopBuyEntry(price=33, name="war axe", serial=0xDDFB, graphic=0x13FB, amount=20),
    ])


def test_buy_hatchet_is_configured_for_a_separate_tool_vendor():
    assert BuyHatchet.owned_tool_graphics == AXE_GRAPHICS
    assert BuyHatchet.offer_graphic == HATCHET_GRAPHIC
    assert BuyHatchet.offer_graphic == 0x0F44  # a SCALAR, not the AXE_GRAPHICS set
    assert BuyHatchet.vendor_spot_key == "tool_vendor_spot"  # the WeaponSmith, not the Carpenter
    assert BuyHatchet.tool_price_estimate == 25


def test_buy_hatchet_reads_the_tool_vendor_spot_route():
    # `_begin_goal` must freeze the WeaponSmith route from `tool_vendor_spot`,
    # never the Carpenter `vendor_spot`.
    skill = BuyHatchet()
    items = [_backpack(), _item(0x900, 0x0EED, container=BACKPACK, amount=100)]  # gold, no axe
    mem = {"vendor_spot": CARPENTER, "tool_vendor_spot": WEAPONSMITH}
    skill.step(_mctx(items, memory=mem, pos=Position(0, 0, 0), goal_id=17))
    assert mem["cap_toolbuy_route"] == (WEAPONSMITH,)


def test_buy_hatchet_buys_the_hatchet_offer_by_graphic():
    vendor = _mobile(VENDOR_MOBILE, *WEAPONSMITH)
    mem = {
        "tool_vendor_spot": WEAPONSMITH, "bs_stand": (0, 0), "mkt_phase": "toolbuy",
        "toolbuy_stage": "window", "toolbuy_vendor": VENDOR_MOBILE,
        "cap_toolbuy_goal_id": 17, "cap_toolbuy_route": (WEAPONSMITH,),
        "cap_toolbuy_start_tools": 0, "cap_toolbuy_start_gold": 100,
    }
    items = [_backpack(), _item(0x900, 0x0EED, container=BACKPACK, amount=100)]  # gold, no axe
    res = BuyHatchet().step(
        _mctx(items, memory=mem, pos=Position(*WEAPONSMITH, 0), mobiles=[vendor],
              shop_buy=_tool_buy_window(), goal_id=17)
    )
    assert isinstance(res.action, BuyItems)
    # Exactly the 0x0F44 hatchet, resolved by the scalar offer_graphic among the
    # window's FIVE entries (saw + 3 other axes + the hatchet) — never another axe.
    assert res.action.items == [(HATCHET_OFFER_SERIAL, 1)]
    assert mem["cap_toolbuy_offer"] == (HATCHET_OFFER_SERIAL, 1, 25)


def test_buy_hatchet_trigger_counts_axes_not_smith_tools():
    # `_pack_tools` for BuyHatchet counts AXE_GRAPHICS: an axe present means the
    # trigger (no tool) is NOT met.
    skill = BuyHatchet()
    with_axe = _ctx([_backpack(), _axe()])
    without_axe = _ctx([_backpack()])
    assert skill._pack_tools(with_axe) == 1
    assert skill._pack_tools(without_axe) == 0


# --- process_logs: the produce capability (goal-scoped log->board conversion) -----


def test_process_logs_goal_without_logs_never_begins():
    skill = ProcessLogsGoal()
    mem = {}
    res = skill.step(_mctx([_backpack(), _axe()], memory=mem, pos=Position(100, 100, 0), goal_id=17))
    assert res.action is None
    assert "cap_process_goal_id" not in mem  # no logs -> no goal frozen


def test_process_logs_goal_freezes_the_total_log_amount_as_needed():
    skill = ProcessLogsGoal()
    mem = {}
    skill.step(_mctx([_backpack(), _axe(), _logs(0x400, amount=18)], memory=mem,
                     pos=Position(100, 100, 0), goal_id=17))
    assert mem["cap_process_goal_id"] == 17
    assert mem["cap_process_start_logs"] == 18
    assert mem["cap_process_needed"] == 18


def _cursor_ctx(items, mem, goal_id):
    return SkillContext(
        obs=Observation(player=PlayerView(serial=1, pos=Position(100, 100, 0)),
                        items=list(items), pending_target=_cursor()),
        persona=Persona(name="Bjorn"), memory=mem, goal_id=goal_id)


def test_process_logs_goal_converts_then_marks_finished():
    skill = ProcessLogsGoal()
    mem = {}
    axe = _axe()

    # Tick 1: begin (freeze 20) + Use(axe).
    use = skill.step(_mctx([_backpack(), axe, _logs(0x400, amount=20)], memory=mem,
                           pos=Position(100, 100, 0), goal_id=17))
    assert isinstance(use.action, Use) and use.action.serial == AXE_SERIAL
    assert mem["cap_process_needed"] == 20

    # Tick 2: the axe opened a target cursor -> TargetObject(log).
    target = skill.step(_cursor_ctx([_backpack(), axe, _logs(0x400, amount=20)], mem, 17))
    assert isinstance(target.action, TargetObject) and target.action.serial == 0x400

    # Tick 3: conversion landed — 20 boards, no logs, no cursor -> finished + evidence.
    done = skill.step(_mctx([_backpack(), axe, _boards(0x500, amount=20)], memory=mem,
                            pos=Position(100, 100, 0), goal_id=17))
    assert done.action is None
    assert mem["cap_process_finished_goal_id"] == 17
    assert mem["cap_process_board_delta"] == 20
    assert mem["cap_process_logs_remaining"] == 0
