"""BlacksmithMarket's sell/bank phases — hand-built observations, no live server."""

from anima2.contract import (
    BuyItems,
    Drop,
    GumpView,
    ItemView,
    MobileView,
    Observation,
    PickUp,
    PlayerView,
    PopupEntry,
    PopupMenu,
    PopupRequest,
    PopupSelect,
    Position,
    SellItems,
    ShopBuy,
    ShopBuyEntry,
    ShopSell,
    ShopSellItem,
    Use,
    Walk,
)
import pytest

from anima2.persona import Persona
from anima2.skills import Blacksmith
from anima2.skills.base import SkillContext
from anima2.skills.tinkering import BuyIron, BuyTinkerTool
from anima2.skills.craft import DAGGER_GRAPHIC
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills import market as market_module
from anima2.skills.market import (
    ASK_RETRY,
    BANK_CLILOC,
    BUY_AMOUNT,
    BUY_CLILOC,
    BUY_CONFIRM_TIMEOUT,
    BANK_DEPOSIT_ATTEMPTS,
    BANK_SETTLE_TICKS,
    BANKBOX_LAYER,
    FIND_MOBILE_TIMEOUT,
    GOLD_GRAPHIC,
    IRON_INGOT_GRAPHIC,
    POPUP_TIMEOUT,
    SELL_CLILOC,
    SELL_CONFIRM_TIMEOUT,
    SMITH_TONGS_GRAPHIC,
    TOOL_BUY_AMOUNT,
    TOOL_BUY_CONFIRM_TIMEOUT,
    WALK_PHASES,
    BankGold,
    BlacksmithMarket,
    BuyIngots,
    BuyTool,
    SellDaggers,
    _bank_reserve,
    walk_readout,
)

HAMMER = 0x13E3
BACKPACK = 0x50
VENDOR = (10, 0)
BANKER = (0, 10)
VENDOR_SERIAL = 0xAAA1
VENDOR_MOBILE = 0xBBB1
BANKER_MOBILE = 0xBBB2


def _item(serial, graphic, *, layer=0, container=None, amount=1, distance=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=distance)


def _backpack():
    return _item(BACKPACK, 0x0E75, layer=BACKPACK_LAYER, container=1)


def _tool():
    return _item(0x40, HAMMER, container=0x99)


def _dagger(serial, amount, bp=BACKPACK):
    return _item(serial, DAGGER_GRAPHIC, amount=amount, container=bp)


def _gold(serial, amount, bp=BACKPACK):
    return _item(serial, GOLD_GRAPHIC, amount=amount, container=bp)


def _bankbox(serial=0x900):
    return _item(serial, 0x0E7C, layer=BANKBOX_LAYER, container=1)


def _mobile(serial, x, y, distance=0):
    return MobileView(serial=serial, name="", pos=Position(x, y, 0), body=0x190,
                      notoriety=1, hits=10, hits_max=10, distance=distance)


def _popup(serial, clilocs):
    return PopupMenu(serial=serial, entries=[PopupEntry(index=i, cliloc=c) for i, c in enumerate(clilocs)])


def _ctx(
    items,
    *,
    memory=None,
    pos=Position(0, 0, 0),
    gumps=(),
    shop_sell=None,
    shop_buy=None,
    mobiles=(),
    popup=None,
    goal_id=None,
):
    obs = Observation(player=PlayerView(serial=1, pos=pos), items=[_tool(), *items],
                      gumps=list(gumps), shop_sell=shop_sell, shop_buy=shop_buy,
                      mobiles=list(mobiles), popup=popup)
    return SkillContext(
        obs=obs,
        persona=Persona(name="T"),
        memory=memory if memory is not None else {},
        goal_id=goal_id,
    )


# --- opt-in / backwards compatibility -------------------------------------------


def test_no_market_configured_is_byte_for_byte_blacksmith():
    items = [_backpack(), _dagger(0x700, amount=99)]  # plenty to sell, if it mattered
    ctx1 = _ctx(items, memory={})
    ctx2 = _ctx(items, memory={})
    r1 = BlacksmithMarket().step(ctx1)
    r2 = Blacksmith().step(ctx2)
    assert r1.action == r2.action
    assert ctx1.memory == ctx2.memory
    assert "mkt_phase" not in ctx1.memory


def test_vendor_only_configured_never_checks_bank_threshold():
    # A vendor but no banker — plenty of gold must never trigger a bank trip.
    items = [_backpack(), _gold(0x800, amount=9999)]
    mem = {"vendor_spot": VENDOR, "bs_state": "loop"}
    ctx = _ctx(items, memory=mem)
    BlacksmithMarket().step(ctx)
    assert mem.get("mkt_phase", "craft") != "bank"


# --- sell: trigger + walk ---------------------------------------------------------


def test_below_sell_threshold_stays_in_craft():
    items = [_backpack(), _dagger(0x700, amount=4)]  # default threshold is 5
    mem = {"vendor_spot": VENDOR, "bs_state": "loop"}
    ctx = _ctx(items, memory=mem)
    res = BlacksmithMarket().step(ctx)
    assert mem.get("mkt_phase", "craft") == "craft"
    # Falls straight through to Blacksmith.step() — no ingots staged here, so
    # it presses on with the MAKE loop the same way `Blacksmith` alone would.
    assert isinstance(res.action, Use) and res.action.serial == 0x40


def test_sell_threshold_triggers_a_walk_toward_the_vendor():
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "bs_state": "loop"}
    ctx = _ctx(items, memory=mem, pos=Position(0, 0, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell"
    assert isinstance(res.action, Walk)


def test_sell_does_not_trigger_while_a_gump_is_open():
    g = GumpView(serial=0xAB, gump_id=0xCD, layout="")
    items = [_backpack(), _dagger(0x700, amount=99)]
    mem = {"vendor_spot": VENDOR, "bs_state": "loop"}
    ctx = _ctx(items, memory=mem, gumps=[g])
    BlacksmithMarket().step(ctx)
    assert mem.get("mkt_phase", "craft") == "craft"  # never hijacked the open gump


def test_sell_does_not_trigger_mid_ingot_fetch():
    # `bs_state == "fetch"` means an ingot may be lifted on the cursor —
    # abandoning that trip mid-air would strand it.
    items = [_backpack(), _dagger(0x700, amount=99)]
    mem = {"vendor_spot": VENDOR, "bs_state": "fetch"}
    ctx = _ctx(items, memory=mem)
    BlacksmithMarket().step(ctx)
    assert mem.get("mkt_phase", "craft") == "craft"


def test_sell_walk_continues_until_in_reach():
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell"}
    ctx = _ctx(items, memory=mem, pos=Position(5, 0, 0))  # still short of SELL_REACH
    res = BlacksmithMarket().step(ctx)
    assert isinstance(res.action, Walk)
    assert mem["mkt_phase"] == "sell"


# --- sell: find vendor -------------------------------------------------------------


def test_sell_waits_for_the_vendor_mobile_to_appear():
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell"}  # arrived, no mobiles in view yet
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0))
    res = BlacksmithMarket().step(ctx)
    assert res.action is None
    assert mem["sell_find_wait"] == 1
    assert mem.get("sell_stage", "find_vendor") == "find_vendor"


def test_sell_gives_up_if_the_vendor_mobile_never_appears():
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell", "bs_stand": (0, 0),
          "sell_find_wait": FIND_MOBILE_TIMEOUT}
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell_return"  # gave up — walks home
    assert isinstance(res.action, Walk)


def test_sell_locks_onto_the_vendor_mobile_near_the_route_end():
    vendor = _mobile(VENDOR_MOBILE, *VENDOR)
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell"}
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor])
    res = BlacksmithMarket().step(ctx)
    assert mem["sell_vendor"] == VENDOR_MOBILE
    assert mem["sell_stage"] == "popup"
    assert isinstance(res.action, PopupRequest) and res.action.serial == VENDOR_MOBILE


# --- sell: popup / select / list / confirm -----------------------------------------


def test_sell_waits_quietly_for_the_popup_after_requesting():
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell", "sell_stage": "popup",
          "sell_vendor": VENDOR_MOBILE, "sell_popup_wait": 0}
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0))
    res = BlacksmithMarket().step(ctx)
    assert res.action is None
    assert mem["sell_popup_wait"] == 1


def test_sell_re_requests_the_popup_after_it_never_arrives():
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell", "sell_stage": "popup",
          "sell_vendor": VENDOR_MOBILE, "sell_popup_wait": ASK_RETRY}
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0))
    res = BlacksmithMarket().step(ctx)
    assert isinstance(res.action, PopupRequest) and res.action.serial == VENDOR_MOBILE
    assert mem["sell_popup_wait"] == 0
    assert mem["sell_stage"] == "popup"  # still waiting — a request isn't a select


def test_sell_popup_gives_up_after_total_timeout_if_the_menu_never_arrives():
    # `_popup_click` re-requests the menu every `ASK_RETRY` ticks forever on
    # its own — nothing bounds the *total* number of cycles without
    # `POPUP_TIMEOUT`. A menu that genuinely never arrives (the vendor killed
    # or wiped after `_find_market_mobile` already locked its serial, or a
    # menu-less mobile locked onto by mistake) must not wedge the smith at
    # the vendor forever.
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "sell", "bs_stand": (0, 0),
        "sell_stage": "popup", "sell_vendor": VENDOR_MOBILE, "sell_popup_total": POPUP_TIMEOUT,
    }
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell_return"  # gave up — walks home
    assert isinstance(res.action, Walk)


def test_sell_selects_the_sell_entry_once_the_popup_is_open():
    popup = _popup(VENDOR_MOBILE, [BUY_CLILOC, SELL_CLILOC])  # Buy, then Sell
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell", "sell_stage": "popup", "sell_vendor": VENDOR_MOBILE}
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0), popup=popup)
    res = BlacksmithMarket().step(ctx)
    assert isinstance(res.action, PopupSelect)
    assert res.action.serial == VENDOR_MOBILE and res.action.index == 1
    assert mem["sell_stage"] == "list"


def test_sell_bails_if_the_popup_has_no_sell_entry():
    popup = _popup(VENDOR_MOBILE, [BUY_CLILOC])  # Buy only — not an active buyer
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell", "sell_stage": "popup",
          "sell_vendor": VENDOR_MOBILE, "bs_stand": (0, 0)}
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0), popup=popup)
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell_return"
    assert isinstance(res.action, Walk)


def test_sell_waits_for_the_selllist_after_selecting():
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell", "sell_stage": "list", "sell_vendor": VENDOR_MOBILE}
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0))
    res = BlacksmithMarket().step(ctx)
    assert res.action is None
    assert mem["sell_ask_wait"] == 1


def test_sell_gives_up_if_the_selllist_never_arrives():
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "sell", "sell_stage": "list", "sell_vendor": VENDOR_MOBILE,
        "sell_ask_wait": ASK_RETRY - 1, "bs_stand": (0, 0),
    }
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell_return"
    assert isinstance(res.action, Walk)


def test_sell_answers_with_dagger_entries_only():
    # The vendor's list also carries the smith's tongs (SBBlacksmith buys those
    # too) — must never offer to sell a tool, only the daggers.
    sell = ShopSell(vendor=VENDOR_SERIAL, items=[
        ShopSellItem(serial=0x700, graphic=DAGGER_GRAPHIC, hue=0, amount=5, price=10, name="dagger"),
        ShopSellItem(serial=0x40, graphic=HAMMER, hue=0, amount=1, price=10, name="smith hammer"),
    ])
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell", "sell_stage": "list", "sell_vendor": VENDOR_MOBILE,
          "sell_daggers_start": 5}
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0), shop_sell=sell)
    res = BlacksmithMarket().step(ctx)
    assert isinstance(res.action, SellItems)
    assert res.action.vendor == VENDOR_SERIAL
    assert res.action.items == [(0x700, 5)]
    assert mem["sell_stage"] == "confirm"


def test_sell_bails_when_the_vendor_recognizes_no_dagger():
    sell = ShopSell(vendor=VENDOR_SERIAL, items=[])  # nothing sellable recognized
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell", "sell_stage": "list", "sell_vendor": VENDOR_MOBILE,
          "bs_stand": (0, 0)}
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0), shop_sell=sell)
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell_return"  # gave up — walks home
    assert isinstance(res.action, Walk)


def test_sell_reward_pays_only_on_confirmed_gold_gain():
    vendor = _mobile(VENDOR_MOBILE, *VENDOR)
    popup = _popup(VENDOR_MOBILE, [SELL_CLILOC])
    sell = ShopSell(vendor=VENDOR_SERIAL, items=[
        ShopSellItem(serial=0x700, graphic=DAGGER_GRAPHIC, hue=0, amount=5, price=10, name="dagger"),
    ])
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell", "bs_stand": (0, 0)}
    pos = Position(*VENDOR, 0)
    items = [_backpack(), _dagger(0x700, amount=5)]

    res1 = BlacksmithMarket().step(_ctx(items, memory=mem, pos=pos, mobiles=[vendor]))
    assert res1.reward == 0.0
    assert isinstance(res1.action, PopupRequest)  # seeds the gold/dagger baseline too

    res2 = BlacksmithMarket().step(_ctx(items, memory=mem, pos=pos, mobiles=[vendor], popup=popup))
    assert res2.reward == 0.0
    assert isinstance(res2.action, PopupSelect)

    res3 = BlacksmithMarket().step(_ctx(items, memory=mem, pos=pos, mobiles=[vendor], shop_sell=sell))
    assert res3.reward == 0.0
    assert isinstance(res3.action, SellItems)

    # tick 4: the sale landed — daggers gone, gold gained. Reward fires once.
    items4 = [_backpack(), _gold(0x900, amount=50)]
    res4 = BlacksmithMarket().step(_ctx(items4, memory=mem, pos=pos, mobiles=[vendor], shop_sell=sell))
    assert res4.reward == 50.0
    assert mem["mkt_phase"] == "sell_return"


def test_sell_capability_owns_exact_vendor_sequence_and_goal_evidence():
    vendor = _mobile(VENDOR_MOBILE, *VENDOR)
    popup = _popup(VENDOR_MOBILE, [SELL_CLILOC])
    sell = ShopSell(
        vendor=VENDOR_SERIAL,
        items=[
            ShopSellItem(
                serial=0x700,
                graphic=DAGGER_GRAPHIC,
                hue=0,
                amount=5,
                price=10,
                name="dagger",
            ),
            ShopSellItem(
                serial=0x40,
                graphic=HAMMER,
                hue=0,
                amount=1,
                price=10,
                name="smith hammer",
            ),
        ],
    )
    mem = {"vendor_spot": VENDOR, "bs_stand": (0, 0)}
    skill = SellDaggers()
    before = [_backpack(), _dagger(0x700, amount=5)]

    request = skill.step(
        _ctx(before, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor], goal_id=17)
    )
    select = skill.step(
        _ctx(
            before,
            memory=mem,
            pos=Position(*VENDOR, 0),
            mobiles=[vendor],
            popup=popup,
            goal_id=17,
        )
    )
    offer = skill.step(
        _ctx(
            before,
            memory=mem,
            pos=Position(*VENDOR, 0),
            mobiles=[vendor],
            shop_sell=sell,
            goal_id=17,
        )
    )

    assert isinstance(request.action, PopupRequest)
    assert isinstance(select.action, PopupSelect)
    assert isinstance(offer.action, SellItems)
    assert offer.action.items == [(0x700, 5)]
    assert mem["cap_sell_sent_goal_id"] == 17
    assert mem["cap_sell_sent_daggers"] == 5
    assert mem["cap_sell_expected_gold"] == 50
    assert mem["cap_sell_offered_items"] == ((0x700, 5, 10),)

    after = [_backpack(), _gold(0x900, amount=50)]
    return_step = skill.step(
        _ctx(after, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor], goal_id=17)
    )
    finish = skill.step(
        _ctx(after, memory=mem, pos=Position(0, 0, 0), goal_id=17)
    )

    assert isinstance(return_step.action, Walk)
    assert finish.action is None
    assert mem["mkt_phase"] == "craft"
    assert mem["cap_sell_finished_goal_id"] == 17
    assert mem["cap_sell_dagger_delta"] == 5
    assert mem["cap_sell_gold_delta"] == 50
    assert mem["cap_sell_offered_removed"] == 5
    assert mem["cap_sell_offered_cleared"] is True
    assert not any(isinstance(result.action, (Use, Drop)) for result in (request, select, offer, return_step, finish))


def test_sell_capability_failed_frame_does_not_replay_or_leak_into_next_goal():
    skill = SellDaggers()
    mem = {
        "vendor_spot": VENDOR,
        "bs_stand": (0, 0),
        "mkt_phase": "sell_return",
        "cap_sell_goal_id": 17,
        "cap_sell_route": (VENDOR,),
        "cap_sell_start_daggers": 5,
        "cap_sell_start_gold": 0,
        "cap_sell_sent_goal_id": 17,
        "cap_sell_sent_daggers": 5,
        "cap_sell_expected_gold": 50,
        "cap_sell_offered_items": ((0x700, 5, 10),),
    }
    unchanged = [_backpack(), _dagger(0x700, amount=5)]

    finish = skill.step(_ctx(unchanged, memory=mem, goal_id=17))
    repeat = skill.step(_ctx(unchanged, memory=mem, goal_id=17))

    assert finish.action is None and repeat.action is None
    assert mem["cap_sell_finished_goal_id"] == 17
    assert mem["cap_sell_dagger_delta"] == 0
    assert mem["cap_sell_gold_delta"] == 0

    skill.step(_ctx(unchanged, memory=mem, goal_id=18))

    assert mem["cap_sell_goal_id"] == 18
    assert "cap_sell_sent_goal_id" not in mem
    assert "cap_sell_finished_goal_id" not in mem


def test_sell_capability_return_stall_never_claims_verified_homecoming():
    skill = SellDaggers()
    mem = {
        "vendor_spot": VENDOR,
        "bs_stand": (0, 0),
        "mkt_phase": "sell_return",
        "sell_return_stall": skill.stall_limit - 1,
        "sell_return_last_pos": (5, 5),
        "cap_sell_goal_id": 17,
        "cap_sell_route": (VENDOR,),
        "cap_sell_start_daggers": 5,
        "cap_sell_start_gold": 0,
        "cap_sell_sent_goal_id": 17,
        "cap_sell_sent_daggers": 5,
        "cap_sell_expected_gold": 50,
        "cap_sell_offered_items": ((0x700, 5, 10),),
    }
    sold = [_backpack(), _gold(0x900, amount=50)]

    result = skill.step(
        _ctx(sold, memory=mem, pos=Position(5, 5, 0), goal_id=17)
    )

    assert result.action is None
    assert mem["mkt_phase"] == "craft"
    assert mem["cap_sell_finished_goal_id"] == 17
    assert "cap_sell_returned_goal_id" not in mem


def test_sell_capability_tracks_each_offered_serial_not_only_aggregate_delta():
    skill = SellDaggers()
    offered = tuple((0x700 + index, 1, 10) for index in range(5))
    mem = {
        "cap_sell_goal_id": 17,
        "cap_sell_start_daggers": 6,
        "cap_sell_start_gold": 0,
        "cap_sell_sent_goal_id": 17,
        "cap_sell_sent_daggers": 5,
        "cap_sell_expected_gold": 50,
        "cap_sell_offered_items": offered,
        "cap_sell_finished_goal_id": 17,
        "mkt_phase": "craft",
    }
    # Aggregate totals look complete (6 -> 1 daggers, 0 -> 50 gold), but one
    # exact serial offered to the vendor still remains in the backpack.
    items = [_backpack(), _dagger(0x700, amount=1), _gold(0x900, amount=50)]

    skill.step(_ctx(items, memory=mem, goal_id=17))

    assert mem["cap_sell_dagger_delta"] == 5
    assert mem["cap_sell_gold_delta"] == 50
    assert mem["cap_sell_offered_removed"] == 4
    assert mem["cap_sell_offered_cleared"] is False


def test_sell_confirm_gives_up_after_a_bounded_wait():
    # SellItems was sent, but the pack never confirms the sale (a rejected
    # transaction, or a server hiccup) — must not freeze the MAKE loop forever.
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "sell", "bs_stand": (0, 0),
        "sell_stage": "confirm", "sell_daggers_start": 5, "sell_confirm_wait": SELL_CONFIRM_TIMEOUT - 1,
    }
    ctx = _ctx(items, memory=mem, pos=Position(*VENDOR, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell_return"
    assert isinstance(res.action, Walk)


def test_sell_wedged_walk_gives_up_and_advances_the_phase():
    # `bs_stand` is deliberately *not* the wedge position, so the same-tick
    # cascade into `sell_return` doesn't also immediately resolve to "home".
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "sell", "bs_stand": (50, 50),
        "sell_stall": 5, "sell_last_pos": (0, 0),
    }
    ctx = _ctx(items, memory=mem, pos=Position(0, 0, 0))  # far from VENDOR = (10, 0)
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell_return"  # gave up selling, now walking home
    assert "sell_stall" not in mem
    assert isinstance(res.action, Walk)
    assert mem["sell_giveup_daggers"] == 5  # backoff floor recorded — see below
    assert mem["bs_stand"] == (50, 50), "mid-trip must not repin home"


def test_opening_a_sell_trip_repins_bs_stand_to_where_it_left():
    """forge-20260818-0039: setdefault froze an early craft tile.

    Pim sold from (2609,474) with `bs_stand` still (2611,473), then
    `trip=sell_return to=(2611,473) d=2>0` gave up at age 11 after the gold
    was already taken. Opening a trip must overwrite the stale pin.
    """
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "craft",
        "bs_stand": (2611, 473),
    }
    ctx = _ctx(items, memory=mem, pos=Position(2609, 474, 0))
    BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell"
    assert mem["bs_stand"] == (2609, 474)


def test_capability_sell_repins_bs_stand_on_open_not_mid_return():
    """Same pin on the capability wrapper — the forge-pair path.

    Needs `craft_spot`: an open already at the vendor must keep a seeded
    home (vendor-sequence tests), so refresh is gated on craft radius.
    """
    from anima2.skills.tinkering import TONGS_GRAPHIC, SellTongs

    items = [_backpack(), ItemView(
        serial=0x700, graphic=TONGS_GRAPHIC, amount=5,
        pos=Position(), container=0x50, layer=0, distance=0)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "craft",
        "craft_spot": (2609, 474),
        "bs_stand": (2611, 473),
        "cap_sell_goal_id": 17,
        "cap_sell_route": (VENDOR,),
        "cap_sell_start_daggers": 5,
        "cap_sell_start_gold": 0,
    }
    ctx = _ctx(items, memory=mem, pos=Position(2609, 474, 0), goal_id=17)
    SellTongs().step(ctx)
    assert mem["mkt_phase"] == "sell"
    assert mem["bs_stand"] == (2609, 474)
    # Mid-return must keep the pin the trip opened with.
    mem["mkt_phase"] = "sell_return"
    mem["bs_stand"] = (2609, 474)
    ctx = _ctx(items, memory=mem, pos=Position(2610, 473, 0), goal_id=17)
    SellTongs().step(ctx)
    assert mem["bs_stand"] == (2609, 474)


def test_sell_backoff_prevents_an_immediate_retrigger_after_a_give_up():
    # A permanently unreachable/missing vendor must not turn into a permanent
    # commute: once a trip gives up with the pack daggers unchanged, the very
    # next craft-phase check (still over threshold) must not send the smith
    # right back out on an identical trip (mirrors `MineSmeltDeliver`'s own
    # `deliver_giveup_ingots` backoff).
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "sell", "bs_stand": (50, 50),
        "sell_stall": 5, "sell_last_pos": (0, 0),
    }
    ctx = _ctx(items, memory=mem, pos=Position(0, 0, 0))
    BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell_return"
    assert mem["sell_giveup_daggers"] == 5
    assert mem["sell_giveup_tick"] == 1  # the cooldown floor's other half — see below

    # Back at the forge with the same 5 daggers still in the pack — must not
    # immediately walk right back out.
    mem["mkt_phase"] = "craft"
    mem["bs_state"] = "loop"
    res2 = BlacksmithMarket().step(_ctx(items, memory=mem, pos=Position(50, 50, 0)))
    assert mem.get("mkt_phase", "craft") == "craft"
    assert not isinstance(res2.action, Walk)

    # Once the pack grows past the give-up count (a new dagger got crafted),
    # it's allowed to try again.
    items_more = [_backpack(), _dagger(0x700, amount=6)]
    BlacksmithMarket().step(_ctx(items_more, memory=mem, pos=Position(50, 50, 0)))
    assert mem["mkt_phase"] == "sell"
    assert "sell_giveup_daggers" not in mem


def test_sell_backoff_cooldown_still_blocks_before_it_elapses():
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "craft", "bs_state": "loop",
        "sell_giveup_daggers": 5, "sell_giveup_tick": 100, "mkt_tick": 100,
    }
    ctx = _ctx(items, memory=mem, pos=Position(50, 50, 0))
    BlacksmithMarket().step(ctx)
    assert mem.get("mkt_phase", "craft") == "craft"  # cooldown hasn't elapsed yet


def test_sell_backoff_cooldown_allows_a_retry_once_elapsed_without_new_progress():
    # A give-up must not be permanent: once `giveup_cooldown_ticks` have
    # passed, the smith tries again even with the exact same dagger count —
    # a transient hiccup (a momentarily-blocked tile, a slow vendor — not a
    # permanently missing one) must eventually self-heal instead of
    # stranding the smith in `craft` forever.
    items = [_backpack(), _dagger(0x700, amount=5)]
    skill = BlacksmithMarket()
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "craft", "bs_state": "loop",
        "sell_giveup_daggers": 5, "sell_giveup_tick": 100 - skill.giveup_cooldown_ticks,
        "mkt_tick": 100,
    }
    ctx = _ctx(items, memory=mem, pos=Position(50, 50, 0))
    skill.step(ctx)
    assert mem["mkt_phase"] == "sell"
    assert "sell_giveup_daggers" not in mem
    assert "sell_giveup_tick" not in mem


# --- sell_return -------------------------------------------------------------------


def test_sell_return_walks_home_then_resumes_crafting():
    items = [_backpack()]
    mem = {"vendor_spot": VENDOR, "mkt_phase": "sell_return", "bs_stand": (0, 0)}
    en_route = BlacksmithMarket().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0)))
    assert mem["mkt_phase"] == "sell_return"
    assert isinstance(en_route.action, Walk)

    home = BlacksmithMarket().step(_ctx(items, memory=mem, pos=Position(0, 0, 0)))
    assert mem["mkt_phase"] == "craft"
    assert isinstance(home.action, Use) and home.action.serial == 0x40


# --- bank: trigger + walk -----------------------------------------------------------


def test_bank_capability_freezes_the_admitted_route_for_the_whole_goal():
    skill = BankGold()
    original_route = [(3, 0), (3, 3)]
    items = [_backpack(), _gold(0x800, amount=40), _bankbox(0x900)]
    mem = {"banker_spot": original_route}

    outbound = skill.step(
        _ctx(items, memory=mem, pos=Position(0, 0, 0), goal_id=17)
    )
    mem["banker_spot"] = [(99, 99)]
    second_leg = skill.step(
        _ctx(items, memory=mem, pos=Position(3, 0, 0), goal_id=17)
    )

    assert isinstance(outbound.action, Walk)
    assert isinstance(second_leg.action, Walk)
    assert mem["cap_bank_route"] == ((3, 0), (3, 3))
    assert mem["bank_leg"] == 1


def test_finished_bank_capability_never_reenters_with_remaining_pack_gold():
    skill = BankGold()
    items = [_backpack(), _gold(0x800, amount=40), _bankbox(0x900)]
    mem = {
        "banker_spot": BANKER,
        "mkt_phase": "craft",
        "cap_bank_goal_id": 17,
        "cap_bank_route": (BANKER,),
        "cap_bank_start_piles": ((0x800, 40),),
        "cap_bank_expected_gold": 40,
        "cap_bank_start_pack_gold": 40,
        "cap_bank_finished_goal_id": 17,
    }

    first = skill.step(_ctx(items, memory=mem, goal_id=17))
    second = skill.step(_ctx(items, memory=mem, goal_id=17))

    assert first.action is None and second.action is None
    assert mem["mkt_phase"] == "craft"
    assert "bank_stage" not in mem
    assert "mkt_tick" not in mem


def test_bank_capability_resets_retry_budget_after_each_confirmed_stack():
    skill = BankGold()
    pos = Position(*BANKER, 0)
    bank_gold = _gold(0xA00, amount=200, bp=0x900)
    before = [
        _backpack(),
        _gold(0x800, amount=60),
        _gold(0x801, amount=40),
        _bankbox(0x900),
        bank_gold,
    ]
    mem = {
        "banker_spot": BANKER,
        "bs_stand": BANKER,
        "mkt_phase": "bank",
        "bank_stage": "settle",
        "bank_banker": BANKER_MOBILE,
        "bank_settle": BANK_SETTLE_TICKS - 1,
    }

    lift_first = skill.step(_ctx(before, memory=mem, pos=pos, goal_id=17))
    after_lift = [
        _backpack(),
        _gold(0x801, amount=40),
        _bankbox(0x900),
        bank_gold,
    ]
    drop_first = skill.step(
        _ctx(after_lift, memory=mem, pos=pos, goal_id=17)
    )
    # Even if the first pile consumed its entire retry budget before finally
    # landing, that budget must not strand the independent second pile.
    mem["bank_deposit_attempts"] = BANK_DEPOSIT_ATTEMPTS
    first_confirmed = [
        _backpack(),
        _gold(0x801, amount=40),
        _bankbox(0x900),
        _gold(0xA00, amount=260, bp=0x900),
    ]
    lift_second = skill.step(
        _ctx(first_confirmed, memory=mem, pos=pos, goal_id=17)
    )

    assert lift_first.action == PickUp(serial=0x800, amount=60)
    assert drop_first.action == Drop(serial=0x800, container=0x900)
    assert lift_second.action == PickUp(serial=0x801, amount=40)
    assert mem["bank_deposit_attempts"] == 1
    assert mem["cap_bank_lifted_items"] == ((0x800, 60), (0x801, 40))
    assert mem["cap_bank_confirmed"] == 60


def test_new_bank_goal_resets_prior_goal_evidence_and_captures_all_pack_stacks():
    skill = BankGold()
    items = [
        _backpack(),
        _gold(0x810, amount=25),
        _gold(0x811, amount=15),
        _bankbox(0x900),
    ]
    mem = {
        "banker_spot": BANKER,
        "mkt_phase": "craft",
        "cap_bank_goal_id": 17,
        "cap_bank_sent_goal_id": 17,
        "cap_bank_finished_goal_id": 17,
        "cap_bank_lifted_items": ((0x800, 100),),
        "cap_bank_dropped_items": ((0x800, 100, 0x900),),
    }

    skill.step(_ctx(items, memory=mem, goal_id=18))

    assert mem["cap_bank_goal_id"] == 18
    assert mem["cap_bank_start_piles"] == ((0x810, 25), (0x811, 15))
    assert mem["cap_bank_expected_gold"] == 40
    assert "cap_bank_sent_goal_id" not in mem
    assert "cap_bank_finished_goal_id" not in mem
    assert "cap_bank_lifted_items" not in mem
    assert "cap_bank_dropped_items" not in mem


def test_below_bank_threshold_stays_in_craft():
    items = [_backpack(), _gold(0x800, amount=50)]  # default threshold is 100
    mem = {"banker_spot": BANKER, "bs_state": "loop"}
    ctx = _ctx(items, memory=mem)
    BlacksmithMarket().step(ctx)
    assert mem.get("mkt_phase", "craft") == "craft"


def test_bank_threshold_triggers_a_walk_toward_the_banker():
    items = [_backpack(), _gold(0x800, amount=150)]
    mem = {"banker_spot": BANKER, "bs_state": "loop"}
    ctx = _ctx(items, memory=mem, pos=Position(0, 0, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "bank"
    assert isinstance(res.action, Walk)


def test_vendor_takes_priority_over_banker_when_both_thresholds_are_met():
    items = [_backpack(), _dagger(0x700, amount=5), _gold(0x800, amount=150)]
    mem = {"vendor_spot": VENDOR, "banker_spot": BANKER, "bs_state": "loop"}
    ctx = _ctx(items, memory=mem)
    BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell"


# --- bank: find banker / popup / settle / deposit -----------------------------------


def test_bank_waits_for_the_banker_mobile_to_appear():
    items = [_backpack(), _gold(0x800, amount=150)]
    mem = {"banker_spot": BANKER, "mkt_phase": "bank"}
    ctx = _ctx(items, memory=mem, pos=Position(*BANKER, 0))
    res = BlacksmithMarket().step(ctx)
    assert res.action is None
    assert mem["bank_find_wait"] == 1


def test_bank_gives_up_if_the_banker_mobile_never_appears():
    items = [_backpack(), _gold(0x800, amount=150)]
    mem = {"banker_spot": BANKER, "mkt_phase": "bank", "bs_stand": (0, 0),
          "bank_find_wait": FIND_MOBILE_TIMEOUT}
    ctx = _ctx(items, memory=mem, pos=Position(*BANKER, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "bank_return"
    assert isinstance(res.action, Walk)


def test_bank_locks_onto_the_banker_mobile_near_the_route_end():
    banker = _mobile(BANKER_MOBILE, *BANKER)
    items = [_backpack(), _gold(0x800, amount=150)]
    mem = {"banker_spot": BANKER, "mkt_phase": "bank"}
    ctx = _ctx(items, memory=mem, pos=Position(*BANKER, 0), mobiles=[banker])
    res = BlacksmithMarket().step(ctx)
    assert mem["bank_banker"] == BANKER_MOBILE
    assert mem["bank_stage"] == "popup"
    assert isinstance(res.action, PopupRequest) and res.action.serial == BANKER_MOBILE


def test_bank_popup_gives_up_after_total_timeout_if_the_menu_never_arrives():
    # Mirrors the matching sell-side test — the `popup` stage has no exit
    # besides `_NO_ENTRY`/`PopupSelect` without a total-wait bound.
    items = [_backpack(), _gold(0x800, amount=150)]
    mem = {
        "banker_spot": BANKER, "mkt_phase": "bank", "bs_stand": (0, 0),
        "bank_stage": "popup", "bank_banker": BANKER_MOBILE, "bank_popup_total": POPUP_TIMEOUT,
    }
    ctx = _ctx(items, memory=mem, pos=Position(*BANKER, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "bank_return"  # gave up — walks home
    assert isinstance(res.action, Walk)


def test_bank_selects_the_bank_entry_once_the_popup_is_open():
    popup = _popup(BANKER_MOBILE, [BANK_CLILOC])
    items = [_backpack(), _gold(0x800, amount=150)]
    mem = {"banker_spot": BANKER, "mkt_phase": "bank", "bank_stage": "popup", "bank_banker": BANKER_MOBILE}
    ctx = _ctx(items, memory=mem, pos=Position(*BANKER, 0), popup=popup)
    res = BlacksmithMarket().step(ctx)
    assert isinstance(res.action, PopupSelect)
    assert res.action.serial == BANKER_MOBILE and res.action.index == 0
    assert mem["bank_stage"] == "settle"
    assert mem["bank_settle"] == 0


def test_bank_bails_if_the_popup_has_no_bank_entry():
    popup = _popup(BANKER_MOBILE, [BUY_CLILOC])  # some unrelated entry only
    items = [_backpack(), _gold(0x800, amount=150)]
    mem = {"banker_spot": BANKER, "mkt_phase": "bank", "bank_stage": "popup",
          "bank_banker": BANKER_MOBILE, "bs_stand": (0, 0)}
    ctx = _ctx(items, memory=mem, pos=Position(*BANKER, 0), popup=popup)
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "bank_return"
    assert isinstance(res.action, Walk)


def test_bank_waits_out_the_settle_period_before_touching_the_box():
    # Even though the bank box's ItemView is already visible (mirrors the
    # backpack's own always-present layer item), a deposit must not be
    # attempted before `BANK_SETTLE_TICKS` — `BankBox.Open()` needs a beat.
    items = [_backpack(), _gold(0x800, amount=150), _bankbox()]
    mem = {"banker_spot": BANKER, "mkt_phase": "bank", "bank_stage": "settle",
          "bank_banker": BANKER_MOBILE, "bank_settle": 0}
    ctx = _ctx(items, memory=mem, pos=Position(*BANKER, 0))
    res = BlacksmithMarket().step(ctx)
    assert res.action is None
    assert mem["bank_settle"] == 1


def test_bank_lifts_then_drops_gold_into_the_bankbox_after_settling():
    items = [_backpack(), _gold(0x800, amount=150), _bankbox(0x900)]
    mem = {
        "banker_spot": BANKER, "mkt_phase": "bank", "bank_stage": "settle",
        "bank_banker": BANKER_MOBILE, "bank_settle": BANK_SETTLE_TICKS,
    }
    pos = Position(*BANKER, 0)
    res1 = BlacksmithMarket().step(_ctx(items, memory=mem, pos=pos))
    assert isinstance(res1.action, PickUp) and res1.action.serial == 0x800 and res1.action.amount == 150
    assert mem["bank_held"] == 0x800
    assert mem["bank_stage"] == "deposit"

    # Next tick: the lift succeeded (gold gone from the pack) — place it.
    items2 = [_backpack(), _bankbox(0x900)]
    res2 = BlacksmithMarket().step(_ctx(items2, memory=mem, pos=pos))
    assert isinstance(res2.action, Drop)
    assert res2.action.serial == 0x800 and res2.action.container == 0x900
    assert "bank_held" not in mem


def test_bank_gives_up_if_the_bankbox_never_shows_up():
    items = [_backpack(), _gold(0x800, amount=150)]  # no bankbox item at all
    mem = {
        "banker_spot": BANKER, "mkt_phase": "bank", "bank_stage": "deposit",
        "bank_banker": BANKER_MOBILE, "bs_stand": (0, 0),
    }
    ctx = _ctx(items, memory=mem, pos=Position(*BANKER, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "bank_return"
    assert isinstance(res.action, Walk)


def test_bank_reward_pays_only_once_gold_is_confirmed_inside_the_bank_box():
    # Reward must not fire on the lift, nor merely because the pack shows the
    # gold gone once `Drop` is issued — a `Drop` into a bank box that never
    # actually opened server-side is silently rejected and bounces the gold
    # straight back into the pack (see the module docstring); only the bank
    # box's own container contents actually showing the deposit counts.
    mem = {
        "banker_spot": BANKER, "mkt_phase": "bank", "bs_stand": (0, 0),
        "bank_stage": "settle", "bank_banker": BANKER_MOBILE, "bank_settle": BANK_SETTLE_TICKS - 1,
    }
    pos = Position(*BANKER, 0)
    items = [_backpack(), _gold(0x800, amount=150), _bankbox(0x900)]  # box empty so far

    # Settle finishes this tick — seeds the box baseline (0, box is empty).
    res_settle = BlacksmithMarket().step(_ctx(items, memory=mem, pos=pos))
    assert isinstance(res_settle.action, PickUp) and res_settle.action.serial == 0x800
    assert res_settle.reward == 0.0
    assert mem["bank_box_start"] == 0
    assert mem["bank_stage"] == "deposit"

    # Lift confirmed (gold gone from the pack) — Drop is issued, but the box
    # doesn't show it yet, so still no reward.
    items_lifted = [_backpack(), _bankbox(0x900)]
    res_drop = BlacksmithMarket().step(_ctx(items_lifted, memory=mem, pos=pos))
    assert isinstance(res_drop.action, Drop)
    assert res_drop.reward == 0.0

    # The drop actually lands: the box's own contents now show the gold, and
    # the pack has nothing left to deposit — reward fires now, alongside the
    # phase ending and walking home.
    items_deposited = [_backpack(), _bankbox(0x900), _gold(0x801, amount=150, bp=0x900)]
    res_done = BlacksmithMarket().step(_ctx(items_deposited, memory=mem, pos=pos))
    assert res_done.reward == 150.0
    assert mem["mkt_phase"] == "bank_return"


def test_bank_reward_does_not_pay_on_a_drop_that_bounces_back_into_the_pack():
    # Mirrors `test_smelt.py`'s matching ingot-delivery test, but for the
    # stronger box-confirmed signal: a server-rejected `Drop` bounces the gold
    # straight back into the pack (`Item.Bounce`) without ever reaching the
    # box — must not pay for it, and must re-lift the bounced gold to retry.
    mem = {
        "banker_spot": BANKER, "mkt_phase": "bank", "bs_stand": (0, 0),
        "bank_stage": "deposit", "bank_banker": BANKER_MOBILE, "bank_box_start": 0,
    }
    pos = Position(*BANKER, 0)
    items_at_arrival = [_backpack(), _gold(0x800, amount=150), _bankbox(0x900)]  # box still empty
    items_lifted = [_backpack(), _bankbox(0x900)]

    res1 = BlacksmithMarket().step(_ctx(items_at_arrival, memory=mem, pos=pos))  # lifts
    assert res1.reward == 0.0
    assert isinstance(res1.action, PickUp)
    assert mem["bank_deposit_attempts"] == 1

    res2 = BlacksmithMarket().step(_ctx(items_lifted, memory=mem, pos=pos))  # issues the drop
    assert res2.reward == 0.0  # not yet confirmed inside the box
    assert isinstance(res2.action, Drop)

    # The drop bounces: the box never shows the gold, and it lands back in the pack.
    res3 = BlacksmithMarket().step(_ctx(items_at_arrival, memory=mem, pos=pos))
    assert res3.reward == 0.0  # nothing confirmed — no reward for a bounce
    assert isinstance(res3.action, PickUp)  # re-lifts the bounced gold
    assert mem["bank_deposit_attempts"] == 2


def test_bank_deposit_gives_up_after_bounded_attempts_when_every_drop_bounces():
    # A bank box that never actually opened server-side bounces every `Drop`
    # back into the pack — must not retry the lift-then-place cycle forever.
    mem = {
        "banker_spot": BANKER, "mkt_phase": "bank", "bs_stand": (0, 0),
        "bank_stage": "deposit", "bank_banker": BANKER_MOBILE, "bank_box_start": 0,
        "bank_deposit_attempts": BANK_DEPOSIT_ATTEMPTS,
    }
    items = [_backpack(), _gold(0x800, amount=150), _bankbox(0x900)]  # still bounced back
    ctx = _ctx(items, memory=mem, pos=Position(*BANKER, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "bank_return"  # gave up — walks home
    assert isinstance(res.action, Walk)
    assert res.reward == 0.0  # nothing was ever confirmed in the box — nothing to pay


def test_bank_wedged_walk_gives_up_and_advances_the_phase():
    # `bs_stand` is deliberately *not* the wedge position (see the matching
    # sell-side test's comment).
    items = [_backpack(), _gold(0x800, amount=150)]
    mem = {
        "banker_spot": BANKER, "mkt_phase": "bank", "bs_stand": (50, 50),
        "bank_stall": 5, "bank_last_pos": (0, 0),
    }
    ctx = _ctx(items, memory=mem, pos=Position(0, 0, 0))  # far from BANKER = (0, 10)
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "bank_return"
    assert "bank_stall" not in mem
    assert isinstance(res.action, Walk)
    assert mem["bank_giveup_gold"] == 150  # backoff floor recorded — see below


def test_bank_backoff_prevents_an_immediate_retrigger_after_a_give_up():
    # Mirrors `test_sell_backoff_prevents_an_immediate_retrigger_after_a_give_up`.
    items = [_backpack(), _gold(0x800, amount=150)]
    mem = {
        "banker_spot": BANKER, "mkt_phase": "bank", "bs_stand": (50, 50),
        "bank_stall": 5, "bank_last_pos": (0, 0),
    }
    ctx = _ctx(items, memory=mem, pos=Position(0, 0, 0))
    BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "bank_return"
    assert mem["bank_giveup_gold"] == 150
    assert mem["bank_giveup_tick"] == 1

    mem["mkt_phase"] = "craft"
    mem["bs_state"] = "loop"
    res2 = BlacksmithMarket().step(_ctx(items, memory=mem, pos=Position(50, 50, 0)))
    assert mem.get("mkt_phase", "craft") == "craft"
    assert not isinstance(res2.action, Walk)

    items_more = [_backpack(), _gold(0x800, amount=200)]
    BlacksmithMarket().step(_ctx(items_more, memory=mem, pos=Position(50, 50, 0)))
    assert mem["mkt_phase"] == "bank"
    assert "bank_giveup_gold" not in mem


def test_bank_backoff_cooldown_allows_a_retry_once_elapsed_without_new_progress():
    # Mirrors `test_sell_backoff_cooldown_allows_a_retry_once_elapsed_without_new_progress`.
    items = [_backpack(), _gold(0x800, amount=150)]
    skill = BlacksmithMarket()
    mem = {
        "banker_spot": BANKER, "mkt_phase": "craft", "bs_state": "loop",
        "bank_giveup_gold": 150, "bank_giveup_tick": 100 - skill.giveup_cooldown_ticks,
        "mkt_tick": 100,
    }
    ctx = _ctx(items, memory=mem, pos=Position(50, 50, 0))
    skill.step(ctx)
    assert mem["mkt_phase"] == "bank"
    assert "bank_giveup_gold" not in mem
    assert "bank_giveup_tick" not in mem


# --- bank_return ---------------------------------------------------------------------


def test_bank_return_walks_home_then_resumes_crafting():
    items = [_backpack()]
    mem = {"banker_spot": BANKER, "mkt_phase": "bank_return", "bs_stand": (0, 0)}
    en_route = BlacksmithMarket().step(_ctx(items, memory=mem, pos=Position(*BANKER, 0)))
    assert mem["mkt_phase"] == "bank_return"
    assert isinstance(en_route.action, Walk)

    home = BlacksmithMarket().step(_ctx(items, memory=mem, pos=Position(0, 0, 0)))
    assert mem["mkt_phase"] == "craft"
    assert isinstance(home.action, Use) and home.action.serial == 0x40


# --- bank working-capital reserve (opt-in; default 0 == whole-pile B7) -------------


def test_bank_reserve_above_pack_gold_never_begins_a_bank_goal():
    # Reserve larger than the pack gold -> no surplus -> the manifest is empty,
    # the goal never begins, and nothing is banked.
    skill = BankGold()
    items = [_backpack(), _gold(0x800, amount=50), _bankbox(0x900)]
    mem = {"banker_spot": BANKER, "bank_reserve": 88}
    res = skill.step(_ctx(items, memory=mem, pos=Position(*BANKER, 0), goal_id=17))
    assert res.action is None
    assert "cap_bank_goal_id" not in mem
    assert "cap_bank_start_piles" not in mem


def test_bank_reserve_single_pile_partial_pickup_lifts_exactly_the_surplus():
    skill = BankGold()
    pos = Position(*BANKER, 0)
    before = [_backpack(), _gold(0x800, amount=200), _bankbox(0x900)]
    mem = {
        "banker_spot": BANKER, "bs_stand": BANKER, "bank_reserve": 88,
        "mkt_phase": "bank", "bank_stage": "settle", "bank_banker": BANKER_MOBILE,
        "bank_settle": BANK_SETTLE_TICKS - 1,
    }
    lift = skill.step(_ctx(before, memory=mem, pos=pos, goal_id=17))

    assert lift.action == PickUp(serial=0x800, amount=112)  # 200 - 88 reserve
    assert mem["bank_held"] == 0x800
    # The frozen manifest / start values bind to the BANKED surplus (112), while
    # the full starting pack (200) is retained for the pack-delta proof.
    assert mem["cap_bank_start_piles"] == ((0x800, 112),)
    assert mem["cap_bank_expected_gold"] == 112
    assert mem["cap_bank_start_pack_gold"] == 112
    assert mem["cap_bank_start_full_pack"] == 200


def test_bank_reserve_multi_pile_manifest_banks_whole_piles_then_partials_the_last():
    skill = BankGold()
    pos = Position(*BANKER, 0)
    # total 110, reserve 30 -> surplus 80: whole 0x800 (60), partial 0x801 (20).
    before = [_backpack(), _gold(0x800, amount=60), _gold(0x801, amount=50), _bankbox(0x900)]
    mem = {
        "banker_spot": BANKER, "bs_stand": BANKER, "bank_reserve": 30,
        "mkt_phase": "bank", "bank_stage": "settle", "bank_banker": BANKER_MOBILE,
        "bank_settle": BANK_SETTLE_TICKS - 1,
    }
    lift = skill.step(_ctx(before, memory=mem, pos=pos, goal_id=17))

    assert mem["cap_bank_start_piles"] == ((0x800, 60), (0x801, 20))
    assert mem["cap_bank_expected_gold"] == 80
    assert mem["cap_bank_start_full_pack"] == 110
    assert lift.action == PickUp(serial=0x800, amount=60)  # smallest serial, whole


def test_bank_reserve_partials_the_last_pile_to_leave_exactly_the_reserve():
    # The whole first pile is already banked; only 0x801 (50) remains with a
    # reserve of 30 -> lift exactly 20, leaving the 30 reserve behind.
    skill = BankGold()
    pos = Position(*BANKER, 0)
    items = [_backpack(), _gold(0x801, amount=50), _bankbox(0x900), _gold(0xA00, amount=60, bp=0x900)]
    mem = {
        "banker_spot": BANKER, "bs_stand": BANKER, "bank_reserve": 30,
        "mkt_phase": "bank", "bank_stage": "deposit", "bank_banker": BANKER_MOBILE,
        "bank_box_start": 0,
        "cap_bank_goal_id": 17, "cap_bank_route": (BANKER,),
        "cap_bank_start_piles": ((0x800, 60), (0x801, 20)),
        "cap_bank_expected_gold": 80, "cap_bank_start_pack_gold": 80,
        "cap_bank_start_full_pack": 110,
        "cap_bank_lifted_items": ((0x800, 60),),
        "cap_bank_dropped_items": ((0x800, 60, 0x900),),
    }
    lift = skill.step(_ctx(items, memory=mem, pos=pos, goal_id=17))

    assert lift.action == PickUp(serial=0x801, amount=20)  # 50 - 30 reserve


def test_bank_reserve_zero_is_byte_identical_whole_pile_deposit():
    # The explicit reserve-0 case must lift the whole pile, exactly as B7.
    skill = BankGold()
    pos = Position(*BANKER, 0)
    before = [_backpack(), _gold(0x800, amount=150), _bankbox(0x900)]
    mem = {
        "banker_spot": BANKER, "bs_stand": BANKER, "bank_reserve": 0,
        "mkt_phase": "bank", "bank_stage": "settle", "bank_banker": BANKER_MOBILE,
        "bank_settle": BANK_SETTLE_TICKS - 1,
    }
    lift = skill.step(_ctx(before, memory=mem, pos=pos, goal_id=17))

    assert lift.action == PickUp(serial=0x800, amount=150)  # whole pile
    assert mem["cap_bank_start_piles"] == ((0x800, 150),)
    assert mem["cap_bank_expected_gold"] == 150
    assert mem["cap_bank_start_full_pack"] == 150


def test_bank_reserve_helper_clamps_negative_and_nonint_to_zero():
    # The single shared read point: negative, float, bool, str, or missing all
    # clamp to 0 (no reserve); only a positive int passes through.
    assert _bank_reserve({}) == 0
    assert _bank_reserve({"bank_reserve": 0}) == 0
    assert _bank_reserve({"bank_reserve": -50}) == 0
    assert _bank_reserve({"bank_reserve": 88}) == 88
    assert _bank_reserve({"bank_reserve": 1.5}) == 0
    assert _bank_reserve({"bank_reserve": True}) == 0
    assert _bank_reserve({"bank_reserve": "88"}) == 0


def test_bank_reserve_equal_to_pack_gold_banks_nothing_through_the_fsm():
    # Surplus exactly 0 (reserve == pack gold) drives the `<= 0` branch through
    # `_pack_gold_manifest`/`_begin_goal`, not just readiness: the manifest is
    # empty, the goal never begins, and nothing is banked.
    skill = BankGold()
    items = [_backpack(), _gold(0x800, amount=88), _bankbox(0x900)]
    mem = {"banker_spot": BANKER, "bank_reserve": 88}
    res = skill.step(_ctx(items, memory=mem, pos=Position(*BANKER, 0), goal_id=17))
    assert res.action is None
    assert "cap_bank_goal_id" not in mem
    assert "cap_bank_start_piles" not in mem


def test_bank_reserve_whole_pile_boundary_retains_the_last_pile_intact():
    # total 200, reserve 100 -> surplus 100 == the first pile exactly; the second
    # pile is fully retained and no pile is partialed.
    skill = BankGold()
    pos = Position(*BANKER, 0)
    before = [_backpack(), _gold(0x800, amount=100), _gold(0x801, amount=100), _bankbox(0x900)]
    mem = {
        "banker_spot": BANKER, "bs_stand": BANKER, "bank_reserve": 100,
        "mkt_phase": "bank", "bank_stage": "settle", "bank_banker": BANKER_MOBILE,
        "bank_settle": BANK_SETTLE_TICKS - 1,
    }
    lift = skill.step(_ctx(before, memory=mem, pos=pos, goal_id=17))

    assert mem["cap_bank_start_piles"] == ((0x800, 100),)  # only the first pile
    assert mem["cap_bank_expected_gold"] == 100
    assert mem["cap_bank_start_full_pack"] == 200
    assert lift.action == PickUp(serial=0x800, amount=100)  # whole, never partialed


def test_bank_negative_reserve_is_clamped_to_zero_and_banks_the_whole_pile():
    # A negative reserve must clamp to 0 (bank normally) rather than compute a
    # surplus larger than the pack and wedge the goal.
    skill = BankGold()
    pos = Position(*BANKER, 0)
    before = [_backpack(), _gold(0x800, amount=150), _bankbox(0x900)]
    mem = {
        "banker_spot": BANKER, "bs_stand": BANKER, "bank_reserve": -50,
        "mkt_phase": "bank", "bank_stage": "settle", "bank_banker": BANKER_MOBILE,
        "bank_settle": BANK_SETTLE_TICKS - 1,
    }
    lift = skill.step(_ctx(before, memory=mem, pos=pos, goal_id=17))

    assert lift.action == PickUp(serial=0x800, amount=150)  # whole pile, not >pack
    assert mem["cap_bank_start_piles"] == ((0x800, 150),)
    assert mem["cap_bank_expected_gold"] == 150
    assert mem["cap_bank_start_full_pack"] == 150


# --- multi-leg routes (a `[Add`-narrow workplace like TRADE_SMITH_SPOT can't
# be reached by a single straight line — see the module docstring) -----------

HUB = (5, 0)
FAR_VENDOR_ROUTE = [HUB, (5, -6)]  # hub, then straight north — two distinct legs


def test_route_heads_for_the_first_waypoint_not_the_final_target():
    from anima2.geometry import direction_toward

    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": FAR_VENDOR_ROUTE, "bs_state": "loop"}
    ctx = _ctx(items, memory=mem, pos=Position(0, 0, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell"
    assert isinstance(res.action, Walk)
    assert res.action.dir == direction_toward(Position(0, 0, 0), Position(*HUB, 0))


def test_route_requires_exact_arrival_at_an_intermediate_waypoint():
    # Adjacent to the hub (would satisfy SELL_REACH if this were the final
    # leg) but not exactly on it — must still walk, not advance to leg 2.
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": FAR_VENDOR_ROUTE, "mkt_phase": "sell", "sell_leg": 0}
    ctx = _ctx(items, memory=mem, pos=Position(HUB[0] - 1, HUB[1], 0))
    res = BlacksmithMarket().step(ctx)
    assert isinstance(res.action, Walk)
    assert mem["sell_leg"] == 0  # still on leg 1


def test_route_advances_to_the_next_leg_on_exact_arrival():
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": FAR_VENDOR_ROUTE, "mkt_phase": "sell", "sell_leg": 0}
    ctx = _ctx(items, memory=mem, pos=Position(*HUB, 0))  # exactly on the hub
    res = BlacksmithMarket().step(ctx)
    assert mem["sell_leg"] == 1  # advanced to the final leg, same tick
    assert isinstance(res.action, Walk)  # final target is still a few tiles off


def test_route_final_leg_only_needs_the_usual_reach_radius():
    vx, vy = FAR_VENDOR_ROUTE[-1]
    vendor = _mobile(VENDOR_MOBILE, vx, vy)
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": FAR_VENDOR_ROUTE, "mkt_phase": "sell", "sell_leg": 1}
    # Adjacent to the final waypoint — within SELL_REACH, so this "arrives"
    # without needing to stand exactly on it — and finds the vendor waiting there.
    ctx = _ctx(items, memory=mem, pos=Position(vx, vy + 1, 0), mobiles=[vendor])
    res = BlacksmithMarket().step(ctx)
    # The leg stays pinned at the final leg for the rest of the trip (see
    # `_walk_route`'s docstring) — it isn't retired until the whole trip's
    # own end-of-phase cleanup in `step()` runs, once the trip is over.
    assert mem["sell_leg"] == 1
    assert isinstance(res.action, PopupRequest) and res.action.serial == VENDOR_MOBILE


def test_route_leg_stays_pinned_across_the_whole_trip_not_just_on_arrival():
    # Regression: `_walk_route` runs again on every later tick of the same
    # trip (popup/list/confirm all call it first, same as the initial walk).
    # If arrival popped the leg index immediately, the very next call would
    # default back to leg 0 and walk toward the hub mid-interaction instead
    # of re-confirming it's still within reach of the final waypoint.
    vx, vy = FAR_VENDOR_ROUTE[-1]
    vendor = _mobile(VENDOR_MOBILE, vx, vy)
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": FAR_VENDOR_ROUTE, "mkt_phase": "sell", "sell_leg": 1}
    pos = Position(vx, vy, 0)

    res1 = BlacksmithMarket().step(_ctx(items, memory=mem, pos=pos, mobiles=[vendor]))
    assert mem["sell_leg"] == 1
    assert isinstance(res1.action, PopupRequest)

    # Still mid-interaction (waiting quietly on the popup) — must not
    # re-evaluate the route from leg 0 and walk back toward the hub.
    res2 = BlacksmithMarket().step(_ctx(items, memory=mem, pos=pos, mobiles=[vendor]))
    assert mem["sell_leg"] == 1
    assert not isinstance(res2.action, Walk)


def test_wedged_return_trip_does_not_leak_its_leg_into_the_next_trip():
    # A wedged return (stall_limit reached before ever reaching the hub) must
    # not leave a stale `sell_return_leg` behind — a later, fresh sell trip's
    # own return would otherwise resume mid-route (skip the curated hub
    # waypoint) instead of starting over at the first leg.
    from anima2.geometry import direction_toward

    vx, vy = FAR_VENDOR_ROUTE[-1]
    items = [_backpack()]
    mem = {
        "vendor_spot": FAR_VENDOR_ROUTE, "mkt_phase": "sell_return", "bs_stand": (0, 0),
        "sell_return_leg": 1,  # already past the hub leg from a previous partial walk
        "sell_return_stall": BlacksmithMarket.stall_limit - 1,
        "sell_return_last_pos": (vx, vy),
    }
    ctx = _ctx(items, memory=mem, pos=Position(vx, vy, 0))  # stuck here long enough to wedge
    BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "craft"  # gave up, resumed crafting from wherever it is
    assert "sell_return_leg" not in mem

    # A brand-new trip's return must start the route fresh (heading for the
    # hub first), not resume from the leftover leg index.
    mem2 = {"vendor_spot": FAR_VENDOR_ROUTE, "mkt_phase": "sell_return", "bs_stand": (0, 0)}
    ctx2 = _ctx(items, memory=mem2, pos=Position(vx, vy, 0))
    res2 = BlacksmithMarket().step(ctx2)
    assert isinstance(res2.action, Walk)
    assert res2.action.dir == direction_toward(Position(vx, vy, 0), Position(*HUB, 0))


def test_sell_return_walks_the_route_in_reverse():
    vx, vy = FAR_VENDOR_ROUTE[-1]
    items = [_backpack()]
    mem = {"vendor_spot": FAR_VENDOR_ROUTE, "mkt_phase": "sell_return", "bs_stand": (0, 0)}
    # At the vendor's final tile — the first return leg should aim back at
    # the hub, not straight at `bs_stand`.
    ctx = _ctx(items, memory=mem, pos=Position(vx, vy, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell_return"
    assert isinstance(res.action, Walk)

    # Exactly at the hub — the next leg heads for `bs_stand` (the corridor's
    # own confirmed-open straight line), and finishing it resumes crafting.
    ctx2 = _ctx(items, memory=mem, pos=Position(*HUB, 0))
    res2 = BlacksmithMarket().step(ctx2)
    assert mem["mkt_phase"] == "sell_return"
    assert isinstance(res2.action, Walk)

    ctx3 = _ctx(items, memory=mem, pos=Position(0, 0, 0))  # arrived home
    res3 = BlacksmithMarket().step(ctx3)
    assert mem["mkt_phase"] == "craft"
    assert isinstance(res3.action, Use) and res3.action.serial == 0x40


def test_single_point_vendor_spot_is_a_one_leg_route_unchanged():
    # A plain tuple (the common/open-terrain case) must behave exactly as
    # before the route mechanism was added — no behaviour change.
    items = [_backpack(), _dagger(0x700, amount=5)]
    mem = {"vendor_spot": VENDOR, "bs_state": "loop"}
    ctx = _ctx(items, memory=mem, pos=Position(0, 0, 0))
    res = BlacksmithMarket().step(ctx)
    assert mem["mkt_phase"] == "sell"
    assert isinstance(res.action, Walk)
    assert "sell_leg" not in mem  # single-leg route never surfaces leg tracking


# --- buy (B8): the sell side inverted — gold leaves, iron ingots arrive --------

VENDOR_CONTAINER = 0xCCC1
SHIELD_SERIAL = 0xDD00
IRON_SERIAL = 0xDD01
TONGS_SERIAL = 0xDD02
SHIELD_GRAPHIC = 0x1B76  # a heater shield — the vendor's other stock, never bought
IRON_STOCK = 16   # SBBlacksmith GenericBuyInfo stock amount — above BUY_AMOUNT
TONGS_STOCK = 14  # SBBlacksmith GenericBuyInfo(typeof(Tongs), 13, 14, 0x0FBB, 0)
TONGS_PRICE = 13


def _buy_window(iron_amount=IRON_STOCK, tongs_amount=TONGS_STOCK):
    """The BUY window, symmetric with the SELL window: every `ShopBuyEntry`
    carries the for-sale item's serial/graphic/amount/price inline, so each offer
    is matched by graphic, never by an `obs.items` index. Iron is 5 gold, tongs
    (a valid smithing tool) 13 gold (SBBlacksmith's own `GenericBuyInfo`s); the
    shield is the vendor's other stock and must never be bought.
    """
    return ShopBuy(
        vendor=VENDOR_SERIAL,
        container=VENDOR_CONTAINER,
        entries=[
            ShopBuyEntry(price=50, name="heater shield", serial=SHIELD_SERIAL,
                         graphic=SHIELD_GRAPHIC, amount=1),
            ShopBuyEntry(price=5, name="iron ingot", serial=IRON_SERIAL,
                         graphic=IRON_INGOT_GRAPHIC, amount=iron_amount),
            ShopBuyEntry(price=TONGS_PRICE, name="tongs", serial=TONGS_SERIAL,
                         graphic=SMITH_TONGS_GRAPHIC, amount=tongs_amount),
        ],
    )


def _iron_pack(serial=0xA00, amount=BUY_AMOUNT):
    return _item(serial, IRON_INGOT_GRAPHIC, container=BACKPACK, amount=amount)


# --- buy: iron-serial + live-price resolution from the buy window ------------------


def test_buy_resolves_the_iron_offer_by_graphic_and_reads_its_serial_price_amount():
    # The enriched entry carries everything: the iron offer is the one entry
    # whose graphic is 0x1BF2 — matched by graphic, never by list index.
    entry = BlacksmithMarket._iron_offer(_buy_window())
    assert entry is not None
    assert entry.serial == IRON_SERIAL
    assert entry.price == 5
    assert entry.amount == IRON_STOCK
    assert entry.graphic == IRON_INGOT_GRAPHIC


def test_buy_resolve_bails_when_the_window_has_no_iron_offer():
    buy = ShopBuy(
        vendor=VENDOR_SERIAL,
        container=VENDOR_CONTAINER,
        entries=[
            ShopBuyEntry(price=50, name="shield", serial=SHIELD_SERIAL,
                         graphic=SHIELD_GRAPHIC, amount=1),
            ShopBuyEntry(price=TONGS_PRICE, name="tongs", serial=TONGS_SERIAL,
                         graphic=SMITH_TONGS_GRAPHIC, amount=TONGS_STOCK),
        ],
    )
    assert BlacksmithMarket._iron_offer(buy) is None


def test_buy_resolve_fails_closed_on_a_malformed_iron_entry():
    # The iron entry is matched by graphic but has no usable serial/stock —
    # a half-filled window must abandon the trip, not order against zeros.
    for bad in (
        ShopBuyEntry(price=5, name="iron", serial=0, graphic=IRON_INGOT_GRAPHIC, amount=IRON_STOCK),
        ShopBuyEntry(price=5, name="iron", serial=IRON_SERIAL, graphic=IRON_INGOT_GRAPHIC, amount=0),
        ShopBuyEntry(price=0, name="iron", serial=IRON_SERIAL, graphic=IRON_INGOT_GRAPHIC, amount=IRON_STOCK),
    ):
        buy = ShopBuy(vendor=VENDOR_SERIAL, container=VENDOR_CONTAINER, entries=[bad])
        assert BlacksmithMarket._iron_offer(buy) is None


# --- buy: full capability vendor sequence + goal evidence -------------------------


def test_buy_capability_owns_exact_vendor_sequence_and_goal_evidence():
    vendor = _mobile(VENDOR_MOBILE, *VENDOR)
    popup = _popup(VENDOR_MOBILE, [BUY_CLILOC, SELL_CLILOC])  # Buy, then Sell
    mem = {"vendor_spot": VENDOR, "bs_stand": (0, 0)}
    skill = BuyIngots()
    before = [_backpack(), _gold(0x900, amount=100)]  # 100 gold, 0 iron

    request = skill.step(
        _ctx(before, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor], goal_id=17)
    )
    select = skill.step(
        _ctx(before, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor], popup=popup, goal_id=17)
    )
    window_items = [_backpack(), _gold(0x900, amount=100)]
    order = skill.step(
        _ctx(
            window_items,
            memory=mem,
            pos=Position(*VENDOR, 0),
            mobiles=[vendor],
            shop_buy=_buy_window(),
            goal_id=17,
        )
    )

    assert isinstance(request.action, PopupRequest)
    assert isinstance(select.action, PopupSelect)
    assert select.action.serial == VENDOR_MOBILE and select.action.index == 0  # Buy
    assert isinstance(order.action, BuyItems)
    assert order.action.vendor == VENDOR_SERIAL
    assert order.action.items == [(IRON_SERIAL, BUY_AMOUNT)]  # iron only, exact batch
    assert mem["cap_buy_sent_goal_id"] == 17
    assert mem["cap_buy_bought_ingots"] == BUY_AMOUNT
    assert mem["cap_buy_expected_cost"] == BUY_AMOUNT * 5
    assert mem["cap_buy_offer"] == (IRON_SERIAL, BUY_AMOUNT, 5)

    # The buy lands: iron in the pack, gold spent by exactly the quoted cost.
    after = [
        _backpack(),
        _gold(0x900, amount=100 - BUY_AMOUNT * 5),
        _iron_pack(),
    ]
    return_step = skill.step(
        _ctx(after, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor], goal_id=17)
    )
    finish = skill.step(_ctx(after, memory=mem, pos=Position(0, 0, 0), goal_id=17))

    assert isinstance(return_step.action, Walk)
    assert finish.action is None
    assert mem["mkt_phase"] == "craft"
    assert mem["cap_buy_finished_goal_id"] == 17
    assert mem["cap_buy_returned_goal_id"] == 17
    assert mem["cap_buy_ingot_delta"] == BUY_AMOUNT
    assert mem["cap_buy_gold_delta"] == BUY_AMOUNT * 5
    # Never a hammer, a drop, or a sale — buying only ever emits popup/BuyItems.
    assert not any(
        isinstance(r.action, (Use, Drop, SellItems))
        for r in (request, select, order, return_step, finish)
    )


def test_buy_capability_waits_without_a_configured_vendor_route():
    items = [_backpack(), _gold(0x900, amount=100)]
    mem = {}  # no vendor_spot
    res = BuyIngots().step(_ctx(items, memory=mem, goal_id=17))
    assert res.action is None
    assert "cap_buy_goal_id" not in mem


def test_buy_capability_never_buys_a_non_iron_item_and_bails():
    # The vendor's window offers only a shield — no iron entry. The buy must
    # resolve nothing, emit no BuyItems, and walk home rather than buy the shield.
    buy = ShopBuy(
        vendor=VENDOR_SERIAL,
        container=VENDOR_CONTAINER,
        entries=[ShopBuyEntry(price=50, name="shield", serial=SHIELD_SERIAL,
                              graphic=SHIELD_GRAPHIC, amount=1)],
    )
    items = [_backpack(), _gold(0x900, amount=100)]
    mem = {
        "vendor_spot": VENDOR, "bs_stand": (0, 0), "mkt_phase": "buy",
        "buy_stage": "window", "buy_vendor": VENDOR_MOBILE,
        "cap_buy_goal_id": 17, "cap_buy_route": (VENDOR,),
        "cap_buy_start_ingots": 0, "cap_buy_start_gold": 100,
    }
    skill = BuyIngots()
    ctx_args = dict(memory=mem, pos=Position(*VENDOR, 0), shop_buy=buy, goal_id=17)
    # A window WITHOUT our offer is usually an unlucky partial subset rather than a vendor
    # that lacks the material (see market.OFFER_REOPEN_ATTEMPTS), so re-roll it a bounded
    # number of times — never buying anything else meanwhile...
    # The assertion is on the ORDER, not on the action type: a `BuyItems` with an EMPTY
    # item list is ServUO's EndVendorBuy — a close, not a purchase — and the re-roll now
    # emits exactly that, because a window left OPEN re-reads the identical entry list
    # and the "re-roll" re-rolls nothing (forge18 live). The intent this test was written
    # for, "never mis-buys the shield", is `res.action.items` being empty every time.
    from anima2.skills.market import OFFER_REOPEN_ATTEMPTS
    for _ in range(OFFER_REOPEN_ATTEMPTS):
        mem["buy_stage"] = "window"                  # the reopened window, still unlucky
        res = skill.step(_ctx(items, **ctx_args))
        assert not (isinstance(res.action, BuyItems) and res.action.items)  # no shield
        assert res.action == BuyItems(vendor=VENDOR_SERIAL, items=[])  # close = re-roll
        assert mem["buy_stage"] == "popup"           # reopen, not abandon
    # ...and only then give the trip up.
    mem["buy_stage"] = "window"
    res = skill.step(_ctx(items, **ctx_args))
    assert not (isinstance(res.action, BuyItems) and res.action.items)
    assert mem["mkt_phase"] == "buy_return"
    assert "cap_buy_sent_goal_id" not in mem


def test_buy_capability_clamps_the_order_to_the_vendors_available_stock():
    # The vendor only stocks 10 iron; the order clamps to it, and the goal
    # evidence binds to the clamped amount (not the fixed BUY_AMOUNT).
    vendor = _mobile(VENDOR_MOBILE, *VENDOR)
    mem = {
        "vendor_spot": VENDOR, "bs_stand": (0, 0), "mkt_phase": "buy",
        "buy_stage": "window", "buy_vendor": VENDOR_MOBILE,
        "cap_buy_goal_id": 17, "cap_buy_route": (VENDOR,),
        "cap_buy_start_ingots": 0, "cap_buy_start_gold": 100,
    }
    items = [_backpack(), _gold(0x900, amount=100)]
    res = BuyIngots().step(
        _ctx(items, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor],
             shop_buy=_buy_window(iron_amount=10), goal_id=17)
    )
    assert isinstance(res.action, BuyItems)
    assert res.action.items == [(IRON_SERIAL, 10)]  # clamped to stock, not BUY_AMOUNT
    assert mem["cap_buy_bought_ingots"] == 10
    assert mem["cap_buy_expected_cost"] == 10 * 5
    assert mem["cap_buy_offer"] == (IRON_SERIAL, 10, 5)


# --- buy: popup / window / confirm stages ----------------------------------------


def test_buy_selects_the_buy_entry_once_the_popup_is_open():
    popup = _popup(VENDOR_MOBILE, [BUY_CLILOC, SELL_CLILOC])
    items = [_backpack(), _gold(0x900, amount=100)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "buy", "buy_stage": "popup", "buy_vendor": VENDOR_MOBILE,
        "cap_buy_goal_id": 17, "cap_buy_route": (VENDOR,),
        "cap_buy_start_ingots": 0, "cap_buy_start_gold": 100,
    }
    res = BuyIngots().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), popup=popup, goal_id=17))
    assert isinstance(res.action, PopupSelect)
    assert res.action.serial == VENDOR_MOBILE and res.action.index == 0
    assert mem["buy_stage"] == "window"


def test_buy_bails_if_the_popup_has_no_buy_entry():
    popup = _popup(VENDOR_MOBILE, [SELL_CLILOC])  # sell only — not a seller
    items = [_backpack(), _gold(0x900, amount=100)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "buy", "buy_stage": "popup", "buy_vendor": VENDOR_MOBILE,
        "bs_stand": (0, 0), "cap_buy_goal_id": 17, "cap_buy_route": (VENDOR,),
        "cap_buy_start_ingots": 0, "cap_buy_start_gold": 100,
    }
    res = BuyIngots().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), popup=popup, goal_id=17))
    assert mem["mkt_phase"] == "buy_return"
    assert isinstance(res.action, Walk)


def test_buy_gives_up_if_the_buy_window_never_arrives():
    items = [_backpack(), _gold(0x900, amount=100)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "buy", "buy_stage": "window", "buy_vendor": VENDOR_MOBILE,
        "buy_ask_wait": ASK_RETRY - 1, "bs_stand": (0, 0), "cap_buy_goal_id": 17,
        "cap_buy_route": (VENDOR,), "cap_buy_start_ingots": 0, "cap_buy_start_gold": 100,
    }
    res = BuyIngots().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), goal_id=17))
    assert mem["mkt_phase"] == "buy_return"
    assert isinstance(res.action, Walk)


def test_buy_popup_gives_up_after_total_timeout_if_the_menu_never_arrives():
    items = [_backpack(), _gold(0x900, amount=100)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "buy", "bs_stand": (0, 0),
        "buy_stage": "popup", "buy_vendor": VENDOR_MOBILE, "buy_popup_total": POPUP_TIMEOUT,
        "cap_buy_goal_id": 17, "cap_buy_route": (VENDOR,),
        "cap_buy_start_ingots": 0, "cap_buy_start_gold": 100,
    }
    res = BuyIngots().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), goal_id=17))
    assert mem["mkt_phase"] == "buy_return"
    assert isinstance(res.action, Walk)


def test_buy_confirm_gives_up_after_a_bounded_wait():
    # BuyItems was sent, gold spent, but no iron ever arrived — must not freeze.
    items = [_backpack(), _gold(0x900, amount=25)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "buy", "buy_stage": "confirm", "buy_vendor": VENDOR_MOBILE,
        "buy_iron_start": 0, "buy_confirm_wait": BUY_CONFIRM_TIMEOUT - 1, "bs_stand": (0, 0),
        "cap_buy_goal_id": 17, "cap_buy_route": (VENDOR,),
        "cap_buy_start_ingots": 0, "cap_buy_start_gold": 100,
    }
    res = BuyIngots().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), goal_id=17))
    assert mem["mkt_phase"] == "buy_return"
    assert isinstance(res.action, Walk)


def test_buy_reward_pays_only_on_confirmed_iron_gain():
    vendor = _mobile(VENDOR_MOBILE, *VENDOR)
    mem = {"vendor_spot": VENDOR, "bs_stand": (0, 0)}
    skill = BuyIngots()
    pos = Position(*VENDOR, 0)
    before = [_backpack(), _gold(0x900, amount=100)]

    res1 = skill.step(_ctx(before, memory=mem, pos=pos, mobiles=[vendor], goal_id=17))
    assert res1.reward == 0.0
    assert isinstance(res1.action, PopupRequest)

    popup = _popup(VENDOR_MOBILE, [BUY_CLILOC])
    res2 = skill.step(_ctx(before, memory=mem, pos=pos, mobiles=[vendor], popup=popup, goal_id=17))
    assert res2.reward == 0.0
    assert isinstance(res2.action, PopupSelect)

    window_items = [_backpack(), _gold(0x900, amount=100)]
    res3 = skill.step(
        _ctx(window_items, memory=mem, pos=pos, mobiles=[vendor], shop_buy=_buy_window(), goal_id=17)
    )
    assert res3.reward == 0.0
    assert isinstance(res3.action, BuyItems)

    # The buy lands — iron arrived. Reward fires once (= ingots gained).
    after = [_backpack(), _gold(0x900, amount=25), _iron_pack()]
    res4 = skill.step(_ctx(after, memory=mem, pos=pos, mobiles=[vendor], goal_id=17))
    assert res4.reward == float(BUY_AMOUNT)
    assert mem["mkt_phase"] == "buy_return"


def test_buy_capability_new_goal_resets_prior_goal_evidence():
    skill = BuyIngots()
    items = [_backpack(), _gold(0x900, amount=100), _iron_pack(amount=5)]
    mem = {
        "vendor_spot": VENDOR,
        "mkt_phase": "craft",
        "cap_buy_goal_id": 17,
        "cap_buy_sent_goal_id": 17,
        "cap_buy_finished_goal_id": 17,
        "cap_buy_offer": (IRON_SERIAL, BUY_AMOUNT, 5),
        "cap_buy_bought_ingots": BUY_AMOUNT,
    }

    skill.step(_ctx(items, memory=mem, goal_id=18))

    assert mem["cap_buy_goal_id"] == 18
    assert mem["cap_buy_start_ingots"] == 5
    assert mem["cap_buy_start_gold"] == 100
    assert "cap_buy_sent_goal_id" not in mem
    assert "cap_buy_finished_goal_id" not in mem
    assert "cap_buy_offer" not in mem
    assert "cap_buy_bought_ingots" not in mem


# --- buy_smith_tool (B8): buy one replacement smithing tool (non-stacking) --------


def _tongs_pack(serial=0xA10):
    return _item(serial, SMITH_TONGS_GRAPHIC, container=BACKPACK, amount=1)


# --- toolbuy: tongs-serial + live-price resolution from the buy window -------------


def test_toolbuy_resolves_the_tongs_offer_by_graphic_and_reads_its_serial_price():
    entry = BlacksmithMarket._tool_offer(_buy_window())
    assert entry is not None
    assert entry.serial == TONGS_SERIAL
    assert entry.price == TONGS_PRICE
    assert entry.amount == TONGS_STOCK
    assert entry.graphic == SMITH_TONGS_GRAPHIC


def test_toolbuy_resolve_bails_when_the_window_has_no_tongs_offer():
    buy = ShopBuy(
        vendor=VENDOR_SERIAL,
        container=VENDOR_CONTAINER,
        entries=[
            ShopBuyEntry(price=50, name="shield", serial=SHIELD_SERIAL,
                         graphic=SHIELD_GRAPHIC, amount=1),
            ShopBuyEntry(price=5, name="iron ingot", serial=IRON_SERIAL,
                         graphic=IRON_INGOT_GRAPHIC, amount=IRON_STOCK),
        ],
    )
    assert BlacksmithMarket._tool_offer(buy) is None


def test_toolbuy_resolve_fails_closed_on_a_malformed_tongs_entry():
    for bad in (
        ShopBuyEntry(price=TONGS_PRICE, name="tongs", serial=0,
                     graphic=SMITH_TONGS_GRAPHIC, amount=TONGS_STOCK),
        ShopBuyEntry(price=TONGS_PRICE, name="tongs", serial=TONGS_SERIAL,
                     graphic=SMITH_TONGS_GRAPHIC, amount=0),
        ShopBuyEntry(price=0, name="tongs", serial=TONGS_SERIAL,
                     graphic=SMITH_TONGS_GRAPHIC, amount=TONGS_STOCK),
    ):
        buy = ShopBuy(vendor=VENDOR_SERIAL, container=VENDOR_CONTAINER, entries=[bad])
        assert BlacksmithMarket._tool_offer(buy) is None


# --- toolbuy: full capability vendor sequence + goal evidence ----------------------


def test_toolbuy_capability_owns_exact_vendor_sequence_and_goal_evidence():
    vendor = _mobile(VENDOR_MOBILE, *VENDOR)
    popup = _popup(VENDOR_MOBILE, [BUY_CLILOC, SELL_CLILOC])  # Buy, then Sell
    mem = {"vendor_spot": VENDOR, "bs_stand": (0, 0)}
    skill = BuyTool()
    before = [_backpack(), _gold(0x900, amount=100)]  # 100 gold, 0 pack tools

    request = skill.step(
        _ctx(before, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor], goal_id=17)
    )
    select = skill.step(
        _ctx(before, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor], popup=popup, goal_id=17)
    )
    window_items = [_backpack(), _gold(0x900, amount=100)]
    order = skill.step(
        _ctx(
            window_items,
            memory=mem,
            pos=Position(*VENDOR, 0),
            mobiles=[vendor],
            shop_buy=_buy_window(),
            goal_id=17,
        )
    )

    assert isinstance(request.action, PopupRequest)
    assert isinstance(select.action, PopupSelect)
    assert select.action.serial == VENDOR_MOBILE and select.action.index == 0  # Buy
    assert isinstance(order.action, BuyItems)
    assert order.action.vendor == VENDOR_SERIAL
    assert order.action.items == [(TONGS_SERIAL, TOOL_BUY_AMOUNT)]  # tongs only, one tool
    assert mem["cap_toolbuy_sent_goal_id"] == 17
    assert mem["cap_toolbuy_bought_tools"] == TOOL_BUY_AMOUNT
    assert mem["cap_toolbuy_expected_cost"] == TOOL_BUY_AMOUNT * TONGS_PRICE
    assert mem["cap_toolbuy_offer"] == (TONGS_SERIAL, TOOL_BUY_AMOUNT, TONGS_PRICE)

    # The buy lands: a tongs in the pack, gold spent by exactly the quoted cost.
    after = [
        _backpack(),
        _gold(0x900, amount=100 - TOOL_BUY_AMOUNT * TONGS_PRICE),
        _tongs_pack(),
    ]
    return_step = skill.step(
        _ctx(after, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor], goal_id=17)
    )
    finish = skill.step(_ctx(after, memory=mem, pos=Position(0, 0, 0), goal_id=17))

    assert isinstance(return_step.action, Walk)
    assert finish.action is None
    assert mem["mkt_phase"] == "craft"
    assert mem["cap_toolbuy_finished_goal_id"] == 17
    assert mem["cap_toolbuy_returned_goal_id"] == 17
    assert mem["cap_toolbuy_tool_delta"] == 1  # a tool arrived (count 0 -> 1)
    assert mem["cap_toolbuy_gold_delta"] == TOOL_BUY_AMOUNT * TONGS_PRICE
    # Never a hammer, a drop, or a sale — buying only ever emits popup/BuyItems.
    assert not any(
        isinstance(r.action, (Use, Drop, SellItems))
        for r in (request, select, order, return_step, finish)
    )


def test_toolbuy_capability_waits_without_a_configured_vendor_route():
    items = [_backpack(), _gold(0x900, amount=100)]
    mem = {}  # no vendor_spot
    res = BuyTool().step(_ctx(items, memory=mem, goal_id=17))
    assert res.action is None
    assert "cap_toolbuy_goal_id" not in mem


def test_toolbuy_capability_never_buys_a_non_tongs_item_and_bails():
    # The vendor's window offers only iron + a shield — no tongs entry. The buy
    # must resolve nothing, emit no BuyItems, and walk home rather than mis-buy.
    buy = ShopBuy(
        vendor=VENDOR_SERIAL,
        container=VENDOR_CONTAINER,
        entries=[
            ShopBuyEntry(price=50, name="shield", serial=SHIELD_SERIAL,
                         graphic=SHIELD_GRAPHIC, amount=1),
            ShopBuyEntry(price=5, name="iron ingot", serial=IRON_SERIAL,
                         graphic=IRON_INGOT_GRAPHIC, amount=IRON_STOCK),
        ],
    )
    items = [_backpack(), _gold(0x900, amount=100)]
    mem = {
        "vendor_spot": VENDOR, "bs_stand": (0, 0), "mkt_phase": "toolbuy",
        "toolbuy_stage": "window", "toolbuy_vendor": VENDOR_MOBILE,
        "cap_toolbuy_goal_id": 17, "cap_toolbuy_route": (VENDOR,),
        "cap_toolbuy_start_tools": 0, "cap_toolbuy_start_gold": 100,
    }
    skill = BuyTool()
    ctx_args = dict(memory=mem, pos=Position(*VENDOR, 0), shop_buy=buy, goal_id=17)
    # A window WITHOUT our offer is usually an unlucky partial subset, not a vendor that
    # lacks the item (see market.OFFER_REOPEN_ATTEMPTS) — so re-roll it a bounded number
    # of times, never buying anything else meanwhile...
    # As in the material buy: the order is what must stay empty. A `BuyItems` carrying
    # NO items is EndVendorBuy — the close that makes a re-roll re-roll anything at all.
    from anima2.skills.market import OFFER_REOPEN_ATTEMPTS
    for _ in range(OFFER_REOPEN_ATTEMPTS):
        mem["toolbuy_stage"] = "window"               # the reopened window, still unlucky
        res = skill.step(_ctx(items, **ctx_args))
        assert not (isinstance(res.action, BuyItems) and res.action.items)  # no shield
        assert res.action == BuyItems(vendor=VENDOR_SERIAL, items=[])  # close = re-roll
        assert mem["toolbuy_stage"] == "popup"        # reopen, not abandon
    # ...and only then give the trip up.
    mem["toolbuy_stage"] = "window"
    res = skill.step(_ctx(items, **ctx_args))
    assert not (isinstance(res.action, BuyItems) and res.action.items)
    assert mem["mkt_phase"] == "toolbuy_return"
    assert "cap_toolbuy_sent_goal_id" not in mem


# --- toolbuy: popup / window / confirm stages -------------------------------------


def test_toolbuy_selects_the_buy_entry_once_the_popup_is_open():
    popup = _popup(VENDOR_MOBILE, [BUY_CLILOC, SELL_CLILOC])
    items = [_backpack(), _gold(0x900, amount=100)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "toolbuy", "toolbuy_stage": "popup",
        "toolbuy_vendor": VENDOR_MOBILE, "cap_toolbuy_goal_id": 17,
        "cap_toolbuy_route": (VENDOR,), "cap_toolbuy_start_tools": 0, "cap_toolbuy_start_gold": 100,
    }
    res = BuyTool().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), popup=popup, goal_id=17))
    assert isinstance(res.action, PopupSelect)
    assert res.action.serial == VENDOR_MOBILE and res.action.index == 0
    assert mem["toolbuy_stage"] == "window"


def test_toolbuy_bails_if_the_popup_has_no_buy_entry():
    popup = _popup(VENDOR_MOBILE, [SELL_CLILOC])  # sell only — not a seller
    items = [_backpack(), _gold(0x900, amount=100)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "toolbuy", "toolbuy_stage": "popup",
        "toolbuy_vendor": VENDOR_MOBILE, "bs_stand": (0, 0), "cap_toolbuy_goal_id": 17,
        "cap_toolbuy_route": (VENDOR,), "cap_toolbuy_start_tools": 0, "cap_toolbuy_start_gold": 100,
    }
    res = BuyTool().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), popup=popup, goal_id=17))
    assert mem["mkt_phase"] == "toolbuy_return"
    assert isinstance(res.action, Walk)


def test_toolbuy_gives_up_if_the_buy_window_never_arrives():
    items = [_backpack(), _gold(0x900, amount=100)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "toolbuy", "toolbuy_stage": "window",
        "toolbuy_vendor": VENDOR_MOBILE, "toolbuy_ask_wait": ASK_RETRY - 1, "bs_stand": (0, 0),
        "cap_toolbuy_goal_id": 17, "cap_toolbuy_route": (VENDOR,),
        "cap_toolbuy_start_tools": 0, "cap_toolbuy_start_gold": 100,
    }
    res = BuyTool().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), goal_id=17))
    assert mem["mkt_phase"] == "toolbuy_return"
    assert isinstance(res.action, Walk)


def test_toolbuy_popup_gives_up_after_total_timeout_if_the_menu_never_arrives():
    items = [_backpack(), _gold(0x900, amount=100)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "toolbuy", "bs_stand": (0, 0),
        "toolbuy_stage": "popup", "toolbuy_vendor": VENDOR_MOBILE, "toolbuy_popup_total": POPUP_TIMEOUT,
        "cap_toolbuy_goal_id": 17, "cap_toolbuy_route": (VENDOR,),
        "cap_toolbuy_start_tools": 0, "cap_toolbuy_start_gold": 100,
    }
    res = BuyTool().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), goal_id=17))
    assert mem["mkt_phase"] == "toolbuy_return"
    assert isinstance(res.action, Walk)


def test_toolbuy_confirm_gives_up_after_a_bounded_wait():
    # BuyItems was sent, gold spent, but no tool ever arrived — must not freeze.
    items = [_backpack(), _gold(0x900, amount=87)]
    mem = {
        "vendor_spot": VENDOR, "mkt_phase": "toolbuy", "toolbuy_stage": "confirm",
        "toolbuy_vendor": VENDOR_MOBILE, "toolbuy_tools_start": 0,
        "toolbuy_confirm_wait": TOOL_BUY_CONFIRM_TIMEOUT - 1, "bs_stand": (0, 0),
        "cap_toolbuy_goal_id": 17, "cap_toolbuy_route": (VENDOR,),
        "cap_toolbuy_start_tools": 0, "cap_toolbuy_start_gold": 100,
    }
    res = BuyTool().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), goal_id=17))
    assert mem["mkt_phase"] == "toolbuy_return"
    assert isinstance(res.action, Walk)


def test_toolbuy_reward_pays_only_on_confirmed_tool_arrival():
    vendor = _mobile(VENDOR_MOBILE, *VENDOR)
    mem = {"vendor_spot": VENDOR, "bs_stand": (0, 0)}
    skill = BuyTool()
    pos = Position(*VENDOR, 0)
    before = [_backpack(), _gold(0x900, amount=100)]

    res1 = skill.step(_ctx(before, memory=mem, pos=pos, mobiles=[vendor], goal_id=17))
    assert res1.reward == 0.0
    assert isinstance(res1.action, PopupRequest)

    popup = _popup(VENDOR_MOBILE, [BUY_CLILOC])
    res2 = skill.step(_ctx(before, memory=mem, pos=pos, mobiles=[vendor], popup=popup, goal_id=17))
    assert res2.reward == 0.0
    assert isinstance(res2.action, PopupSelect)

    window_items = [_backpack(), _gold(0x900, amount=100)]
    res3 = skill.step(
        _ctx(window_items, memory=mem, pos=pos, mobiles=[vendor], shop_buy=_buy_window(), goal_id=17)
    )
    assert res3.reward == 0.0
    assert isinstance(res3.action, BuyItems)

    # The buy lands — a tool arrived. Reward fires once (= one tool gained).
    after = [_backpack(), _gold(0x900, amount=87), _tongs_pack()]
    res4 = skill.step(_ctx(after, memory=mem, pos=pos, mobiles=[vendor], goal_id=17))
    assert res4.reward == 1.0
    assert mem["mkt_phase"] == "toolbuy_return"


def test_toolbuy_capability_new_goal_resets_prior_goal_evidence():
    skill = BuyTool()
    items = [_backpack(), _gold(0x900, amount=100), _tongs_pack()]
    mem = {
        "vendor_spot": VENDOR,
        "mkt_phase": "craft",
        "cap_toolbuy_goal_id": 17,
        "cap_toolbuy_sent_goal_id": 17,
        "cap_toolbuy_finished_goal_id": 17,
        "cap_toolbuy_offer": (TONGS_SERIAL, TOOL_BUY_AMOUNT, TONGS_PRICE),
        "cap_toolbuy_bought_tools": TOOL_BUY_AMOUNT,
    }

    skill.step(_ctx(items, memory=mem, goal_id=18))

    assert mem["cap_toolbuy_goal_id"] == 18
    assert mem["cap_toolbuy_start_tools"] == 1  # the pack's existing tongs counts
    assert mem["cap_toolbuy_start_gold"] == 100
    assert "cap_toolbuy_sent_goal_id" not in mem
    assert "cap_toolbuy_finished_goal_id" not in mem
    assert "cap_toolbuy_offer" not in mem
    assert "cap_toolbuy_bought_tools" not in mem


# --- an ALREADY-OPEN vendor window (forge15-18's wedge, at the FSM layer) -------------
#
# A window left behind by an earlier trip blocks the popup stage completely: the
# server ignores a fresh popup request while one is up, so the counter just runs to
# its timeout and the trip is thrown away. Live that cost a full goal lifetime per
# recurrence — the Life's stale-UI repair can only act once NO goal owns the surface.
# The trip itself can act immediately, so it does.

def _buy_popup_mem():
    return {"vendor_spot": VENDOR, "bs_stand": (0, 0), "mkt_phase": "buy",
            "buy_stage": "popup", "buy_vendor": VENDOR_MOBILE,
            "cap_buy_goal_id": 17, "cap_buy_route": (VENDOR,),
            "cap_buy_start_ingots": 0, "cap_buy_start_gold": 100}


def test_an_already_open_window_from_OUR_vendor_is_used_not_re_requested():
    buy = ShopBuy(vendor=VENDOR_MOBILE, container=VENDOR_CONTAINER,
                  entries=[ShopBuyEntry(price=5, name="iron", serial=0xABC,
                                        graphic=IRON_INGOT_GRAPHIC, amount=100)])
    mem = _buy_popup_mem()
    res = BuyIngots().step(_ctx([_backpack(), _gold(0x900, amount=100)], memory=mem,
                                pos=Position(*VENDOR, 0), shop_buy=buy, goal_id=17))
    # Straight to the order: no PopupRequest, no waiting out the timeout.
    assert isinstance(res.action, BuyItems) and res.action.items
    assert mem["buy_stage"] != "popup"


def test_an_already_open_window_from_ANOTHER_vendor_is_cancelled():
    stranger = 0xDEAD
    buy = ShopBuy(vendor=stranger, container=VENDOR_CONTAINER, entries=[])
    mem = _buy_popup_mem()
    res = BuyIngots().step(_ctx([_backpack(), _gold(0x900, amount=100)], memory=mem,
                                pos=Position(*VENDOR, 0), shop_buy=buy, goal_id=17))
    # An EMPTY list is ServUO's cancel (EndVendorBuy) — clear the blocker, keep the trip.
    assert isinstance(res.action, BuyItems)
    assert res.action.vendor == stranger and not res.action.items
    assert mem["buy_stage"] == "popup"  # still ours to finish once the way is clear


def test_no_open_window_still_takes_the_ordinary_popup_path():
    mem = _buy_popup_mem()
    res = BuyIngots().step(_ctx([_backpack(), _gold(0x900, amount=100)], memory=mem,
                                pos=Position(*VENDOR, 0), goal_id=17))
    assert not isinstance(res.action, BuyItems)  # popup request/wait, unchanged


# --- forge16-18: the stale BUY window that ate the last 556 ticks of a live run ------
#
# `ui=shopbuy` on the last 75 of 208 samples of the 1800-tick forge run of 2026-08-03;
# three complete `buy_iron` frames, each burning its full 180-tick budget and expiring;
# position and steps frozen; the runner printing `NO PROGRESS for 560 ticks`. Two
# defects, both in this file and both about the same window:
#
#   1. NOTHING closed it on any give-up path. Seven `return None` give-ups in
#      `_buy_step` and not one cancelled the window the trip had opened, so the trip
#      walked home leaving a surface that refuses all sixteen capability gates.
#   2. `buy_offer_reopens` — the counter that decides how fast the partial-subset
#      re-roll gives up — SURVIVED the trip. One unlucky trip poisoned every later one
#      for the life of the process.
#
# `8cdd2f0` had already fixed the ENTRY edge (a window already up when a trip ARRIVES)
# and measurement of that run showed it working exactly as designed; it never touched
# the EXIT edge.

def _shieldy_window(vendor=VENDOR_SERIAL):
    """A vendor window whose partial subset shows a shield and nothing we came for —
    the documented ~15-of-45-entry pairing bug (`market.OFFER_REOPEN_ATTEMPTS`)."""
    return ShopBuy(
        vendor=vendor,
        container=VENDOR_CONTAINER,
        entries=[ShopBuyEntry(price=50, name="shield", serial=SHIELD_SERIAL,
                              graphic=SHIELD_GRAPHIC, amount=1)],
    )


def _buy_trip_mem(goal_id, *, vendor=VENDOR_SERIAL):
    return {
        "vendor_spot": VENDOR, "bs_stand": (0, 0), "mkt_phase": "buy",
        "buy_stage": "window", "buy_vendor": vendor,
        "cap_buy_goal_id": goal_id, "cap_buy_route": (VENDOR,),
        "cap_buy_start_ingots": 0, "cap_buy_start_gold": 100,
    }


def _rerolls_this_trip(skill, items, mem, buy, goal_id, stage_key="buy_stage"):
    """Run the `window` stage until the trip gives up; count the re-rolls it managed.

    Resetting the stage to `window` each iteration is what the surrounding tests already
    do: it stands for "the close landed, the popup cycle ran, a fresh — still unlucky —
    window arrived", which is the only thing a re-roll can mean.
    """
    from anima2.skills.market import OFFER_REOPEN_ATTEMPTS

    rerolls = 0
    for _ in range(OFFER_REOPEN_ATTEMPTS + 2):
        mem[stage_key] = "window"
        res = skill.step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0),
                              shop_buy=buy, goal_id=goal_id))
        if mem.get(stage_key) != "popup":
            return rerolls, res  # the trip gave up
        rerolls += 1
    raise AssertionError("the re-roll budget is not bounded")


def test_an_unlucky_buy_trip_no_longer_poisons_every_later_trip():
    """`buy_offer_reopens` was written and read in `_buy_step` and NOWHERE else in the
    repo — not in `_CLEANUP_KEYS`, not in `_begin_goal`'s pop list, not in the gate's
    transaction keys. It therefore lived in the economy agent's memory for the life of
    the process. Measured offline on the FSM alone before the fix: trip 1 gave up after
    its full budget, trips 2 and 3 (fresh goal ids, same memory) gave up on tick 1."""
    from anima2.skills.market import OFFER_REOPEN_ATTEMPTS

    items = [_backpack(), _gold(0x900, amount=100)]
    buy = _shieldy_window()
    skill = BuyIngots()
    mem = _buy_trip_mem(17)
    budgets = []
    for goal_id in (17, 18, 19):
        mem.update(_buy_trip_mem(goal_id))
        rerolls, _res = _rerolls_this_trip(skill, items, mem, buy, goal_id)
        budgets.append(rerolls)
        assert mem["mkt_phase"] == "buy_return"
        assert "buy_offer_reopens" not in mem, (
            f"a counter scoped to ONE trip outlived it: {mem.get('buy_offer_reopens')}")
    assert budgets == [OFFER_REOPEN_ATTEMPTS] * 3, (
        f"every trip must get its own re-roll budget, not just the first: {budgets}")


def test_a_given_up_buy_trip_closes_the_window_it_opened():
    """The EXIT edge. Before this the trip walked home leaving `ui=shopbuy` up, and only
    the Life's own stale-UI repair could clear it — which deliberately waits while a goal
    owns the surface, so the frame had to expire first. Three frames x 180 ticks."""
    items = [_backpack(), _gold(0x900, amount=100)]
    buy = _shieldy_window()
    skill = BuyIngots()
    mem = _buy_trip_mem(17)
    _rerolls, res = _rerolls_this_trip(skill, items, mem, buy, 17)
    assert mem["mkt_phase"] == "buy_return"
    assert res.action == BuyItems(vendor=VENDOR_SERIAL, items=[]), (
        f"the trip must cancel its own window before walking home: {res.action}")


def test_a_given_up_buy_trip_never_closes_a_window_it_does_not_own():
    """The hard constraint: a repair must never close a surface a live goal owns. This
    one closes ONLY `obs.shop_buy` whose `vendor` equals the serial THIS trip recorded,
    so another vendor's window — which could belong to anybody — is left alone."""
    items = [_backpack(), _gold(0x900, amount=100)]
    stranger = _shieldy_window(vendor=VENDOR_MOBILE)   # a window we never opened
    skill = BuyIngots()
    mem = _buy_trip_mem(17, vendor=VENDOR_SERIAL)      # ...and OUR vendor is a different one
    _rerolls, res = _rerolls_this_trip(skill, items, mem, stranger, 17)
    assert mem["mkt_phase"] == "buy_return"
    assert not isinstance(res.action, BuyItems), (
        f"a stranger's window is not ours to cancel: {res.action}")


def test_a_buy_trip_that_gave_up_with_no_window_open_emits_no_cancel():
    """No surface, no action. The close is conditional on there being one of ours, not
    a routine emitted on every give-up — an unconditional cancel would be a repair, and
    a repair is exactly what this must not be."""
    items = [_backpack(), _gold(0x900, amount=100)]
    skill = BuyIngots()
    mem = _buy_trip_mem(17)
    mem["buy_stage"] = "window"
    mem["buy_ask_wait"] = ASK_RETRY          # the window never arrived — give up
    res = skill.step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), goal_id=17))
    assert mem["mkt_phase"] == "buy_return"
    assert not isinstance(res.action, BuyItems), res.action


def test_a_SUCCESSFUL_buy_also_leaves_no_window_behind():
    """The confirm stage's own `return None` is a give-up path too — the good one, taken
    the moment the iron lands. It goes through the same exit edge, so a trip that WORKED
    also clears the surface that would otherwise refuse the next sixteen gates."""
    items = [_backpack(), _gold(0x900, amount=50), _iron_pack(0x901, amount=BUY_AMOUNT)]
    buy = _buy_window()
    skill = BuyIngots()
    mem = _buy_trip_mem(17)
    mem["buy_stage"] = "confirm"
    mem["buy_iron_start"] = 0                # the order landed: pack iron rose above it
    res = skill.step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0),
                          shop_buy=buy, goal_id=17))
    assert mem["mkt_phase"] == "buy_return"
    assert res.action == BuyItems(vendor=VENDOR_SERIAL, items=[]), res.action


def test_an_unlucky_toolbuy_trip_no_longer_poisons_every_later_trip():
    """`toolbuy_offer_reopens` is the same counter in a different memory namespace, and
    it had the identical defect. Fixed together rather than waiting to be caught on its
    own live run — the tool buy rides the same flagship tinker path."""
    from anima2.skills.market import OFFER_REOPEN_ATTEMPTS

    items = [_backpack(), _gold(0x900, amount=100)]
    buy = _shieldy_window()
    skill = BuyTool()
    budgets = []
    # ONE memory across all three trips — that is the whole point. A live economy agent
    # never gets a fresh dict between goal frames, so anything a trip leaves behind is
    # what the next trip starts from.
    mem: dict = {}
    for goal_id in (17, 18, 19):
        mem.update({
            "vendor_spot": VENDOR, "bs_stand": (0, 0), "mkt_phase": "toolbuy",
            "toolbuy_stage": "window", "toolbuy_vendor": VENDOR_SERIAL,
            "cap_toolbuy_goal_id": goal_id, "cap_toolbuy_route": (VENDOR,),
            "cap_toolbuy_start_tools": 0, "cap_toolbuy_start_gold": 100,
        })
        rerolls, res = _rerolls_this_trip(skill, items, mem, buy, goal_id,
                                          stage_key="toolbuy_stage")
        budgets.append(rerolls)
        assert mem["mkt_phase"] == "toolbuy_return"
        assert "toolbuy_offer_reopens" not in mem
        assert res.action == BuyItems(vendor=VENDOR_SERIAL, items=[]), (
            f"the tool trip must cancel its own window too: {res.action}")
    assert budgets == [OFFER_REOPEN_ATTEMPTS] * 3, budgets


# --- the two live acceptance gates count ORDERS, not `BuyItems` ----------------------
#
# Both empty-list cancels above are `BuyItems` addressed to the vendor, and both live
# gates collected every such action and passed only on `len(buys) == 1`
# (`live_buy_goal.py`, `live_toolbuy_goal.py`). So the exit edge made a CORRECT buy fail
# its own gate, and the re-roll — the behaviour those attempts exist for — became
# ungateable: measured against a shard simulator driving the real FSM, a run that bought
# 15 iron at the quoted price with an exact gold delta scored
# `transaction_actions_once=False, only_iron_bought=False` purely because one trailing
# cancel made `len(buys) == 2`. `market.is_vendor_cancel` is the one predicate both
# gates now filter through.

def test_is_vendor_cancel_separates_a_close_from_a_purchase():
    from anima2.skills.market import is_vendor_cancel

    assert is_vendor_cancel(BuyItems(vendor=VENDOR_SERIAL, items=[]))
    assert not is_vendor_cancel(
        BuyItems(vendor=VENDOR_SERIAL, items=[(IRON_SERIAL, BUY_AMOUNT)]))
    # Only ever asked about actions; anything else is not a buy at all.
    assert not is_vendor_cancel(None)
    assert not is_vendor_cancel(SellItems(vendor=VENDOR_SERIAL, items=[]))


def test_every_cancel_this_fsm_emits_is_recognised_as_one():
    """The gates' filter is only correct if it catches EVERY close the FSM produces —
    a missed one is counted as a purchase and fails the gate again. Three producers:
    the entry edge (8cdd2f0), the partial-subset re-roll, and the exit edge."""
    from anima2.skills.market import is_vendor_cancel

    items = [_backpack(), _gold(0x900, amount=100)]
    skill = BuyIngots()

    # 1. ENTRY edge — a window belonging to somebody else, seen on arrival.
    mem = _buy_trip_mem(17)
    mem["buy_stage"] = "popup"
    res = skill.step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0),
                          shop_buy=_shieldy_window(vendor=VENDOR_MOBILE), goal_id=17))
    assert is_vendor_cancel(res.action), res.action

    # 2. RE-ROLL, and 3. EXIT edge (the last action `_rerolls_this_trip` returns).
    mem = _buy_trip_mem(18)
    buy = _shieldy_window()
    mem["buy_stage"] = "window"
    res = skill.step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0),
                          shop_buy=buy, goal_id=18))
    assert mem["buy_stage"] == "popup" and is_vendor_cancel(res.action), res.action
    _rerolls, giveup = _rerolls_this_trip(skill, items, mem, buy, 18)
    assert mem["mkt_phase"] == "buy_return" and is_vendor_cancel(giveup.action)


def test_the_gate_counting_rule_passes_a_correct_trip_that_re_rolled_and_cancelled():
    """The gates' arithmetic, over the actions a real trip emits.

    `orders` is what `live_buy_goal.py`/`live_toolbuy_goal.py` now count, and the pass
    criterion is exactly one of them. Every shape below is a CORRECT buy of 15 iron:
    with and without a trailing exit-edge cancel, and after one or two subset re-rolls.
    The old `len(buys) == 1` rule passed only the first."""
    from anima2.skills.market import OFFER_REOPEN_ATTEMPTS, is_vendor_cancel

    order = BuyItems(vendor=VENDOR_SERIAL, items=[(IRON_SERIAL, BUY_AMOUNT)])
    cancel = BuyItems(vendor=VENDOR_SERIAL, items=[])
    shapes = {
        "first opening, window already gone": ([order], 1),
        "first opening, window lingers":      ([order, cancel], 1),
        "one re-roll":                        ([cancel, order], 2),
        "two re-rolls":                       ([cancel, cancel, order], 3),
    }
    for label, (buys, opens) in shapes.items():
        orders = [b for b in buys if not is_vendor_cancel(b)]
        assert (len(orders) == 1
                and opens <= 1 + OFFER_REOPEN_ATTEMPTS), label
        assert orders[0].items == [(IRON_SERIAL, BUY_AMOUNT)], label
        # ...and the old rule, kept here as the reason this test exists.
        assert (len(buys) == 1) is (label == "first opening, window already gone")


# --- follow-up 20: the entry edge the tool buy never got -----------------------------
#
# `8cdd2f0` gave `_buy_step` an already-open-window branch: the server ignores a fresh
# popup request while a window is up, so a window left behind by anything else runs the
# popup counter to its timeout and throws the whole trip away. It was never mirrored into
# `_toolbuy_step` — "a real asymmetry between two copies of the same FSM", and two copies
# of one FSM with one fix applied to one of them is this project's headline defect class.
#
# The stage is now ONE method (`_buy_popup_stage`) with a namespace argument, so the two
# cannot diverge again by construction. These tests drive it through BOTH FSMs.

def _popup_trip_mem(ns, *, vendor=VENDOR_SERIAL):
    return {"vendor_spot": VENDOR, "bs_stand": (0, 0), "mkt_phase": "buy",
            f"{ns}_stage": "popup", f"{ns}_vendor": vendor}


@pytest.mark.parametrize("ns,skill", [("buy", BuyIron()), ("toolbuy", BuyTinkerTool())])
def test_a_window_already_open_is_adopted_by_BOTH_buy_fsms(ns, skill):
    """OUR window, already up when the trip reaches the popup stage: use it instead of
    requesting a menu the server will ignore."""
    mem = _popup_trip_mem(ns)
    step = skill._buy_step if ns == "buy" else skill._toolbuy_step
    ctx = _ctx([_backpack(), _gold(0x900, amount=500)], memory=mem,
               pos=Position(*VENDOR, 0), mobiles=[_mobile(VENDOR_SERIAL, *VENDOR)],
               shop_buy=_shieldy_window(), goal_id=7)
    step(ctx, [VENDOR])
    assert mem[f"{ns}_stage"] in ("popup", "window"), mem[f"{ns}_stage"]
    # It reached the `window` stage this very tick rather than emitting a PopupRequest.
    assert mem.get(f"{ns}_popup_wait") is None


@pytest.mark.parametrize("ns,skill", [("buy", BuyIron()), ("toolbuy", BuyTinkerTool())])
def test_a_FOREIGN_window_is_cancelled_and_the_cancel_is_re_sent_if_it_is_lost(ns, skill):
    """Someone else's window blocks us just as completely, so cancel it — but ONCE. The
    branch used to re-send the cancel every tick while the observation lag lasted, and
    (because it returned before the stage's own counter was touched) a window that never
    closed produced a cancel every tick FOREVER: bounded only by the frame deadline
    outside this FSM, never by the POPUP_TIMEOUT that exists to bound it."""
    from anima2.skills.market import ASK_RETRY, POPUP_TIMEOUT

    mem = _popup_trip_mem(ns)
    step = skill._buy_step if ns == "buy" else skill._toolbuy_step
    items = [_backpack(), _gold(0x900, amount=500)]
    foreign = _shieldy_window(vendor=0xDEAD)

    def tick():
        return step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0),
                         mobiles=[_mobile(VENDOR_SERIAL, *VENDOR)],
                         shop_buy=foreign, goal_id=7), [VENDOR])

    first = tick()
    assert first.action == BuyItems(vendor=0xDEAD, items=[]), "cancel the foreign window"
    # ...and the following ticks wait rather than re-sending on every one of them.
    # `closing_wait` reaches ASK_RETRY on the tick the cancel is re-sent, so the
    # quiet stretch between two sends is ASK_RETRY-1 ticks long.
    assert all(tick().action is None for _ in range(ASK_RETRY - 1))

    # But a cancel is a PACKET and a packet can be dropped, so it is re-sent on
    # `_popup_click`'s own cadence. Sending it exactly once was review-caught: a lost
    # cancel ended the trip on POPUP_TIMEOUT with the blocking window still open, and
    # `_close_own_vendor_window` deliberately refuses another vendor's window, so nothing
    # else would have closed it either. Unbounded re-sending was self-healing; this is
    # self-healing AND bounded.
    assert tick().action == BuyItems(vendor=0xDEAD, items=[]), "the cancel is never retried"

    # The whole stage is bounded now: a window that never closes ends the trip.
    for _ in range(POPUP_TIMEOUT + 5):
        if tick() is None:
            break
    else:
        raise AssertionError("a foreign window that never closes never ends the trip")


@pytest.mark.parametrize("ns,skill", [("buy", BuyIron()), ("toolbuy", BuyTinkerTool())])
def test_the_reroll_budget_survives_the_close_s_own_observation_lag(ns, skill):
    """The marker follow-up 20 names, and mirroring the branch WITHOUT it would have made
    the tool buy worse rather than better.

    A window is a SNAPSHOT, so re-reading one that is still visible cannot show a
    different subset. Without a marker the pre-check re-adopts the window this trip just
    cancelled and the re-roll budget burns on one snapshot. Driving the real FSMs against
    a simulated shard, counting FRESH window openings per trip (1 initial + 4 re-rolls):

        lag ticks | 0 | 1 | 2 | 3 | 5
        no marker | 3 | 3 | 2 | 2 | 1
        with      | 5 | 5 | 5 | 5 | 5

    identical on both FSMs. The lag costs ticks now, and no attempts.
    """
    mem = _popup_trip_mem(ns)
    mem[f"{ns}_stage"] = "window"
    step = skill._buy_step if ns == "buy" else skill._toolbuy_step
    items = [_backpack(), _gold(0x900, amount=500)]
    window = _shieldy_window()

    # The re-roll fires and marks the window it just cancelled.
    res = step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0),
                    mobiles=[_mobile(VENDOR_SERIAL, *VENDOR)],
                    shop_buy=window, goal_id=7), [VENDOR])
    assert res.action == BuyItems(vendor=VENDOR_SERIAL, items=[])
    assert mem[f"{ns}_stage"] == "popup"
    assert mem[f"{ns}_closing_window"] is True
    assert mem[f"{ns}_offer_reopens"] == 1

    # Lag: the cancelled window is still visible. The pre-check must NOT re-adopt it,
    # and must not spend another attempt on the same snapshot.
    for _ in range(3):
        res = step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0),
                        mobiles=[_mobile(VENDOR_SERIAL, *VENDOR)],
                        shop_buy=window, goal_id=7), [VENDOR])
        assert res.action is None, "it re-adopted the window it just cancelled"
        assert mem[f"{ns}_stage"] == "popup"
        assert mem[f"{ns}_offer_reopens"] == 1

    # The close lands: the marker clears and the popup cycle resumes.
    res = step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0),
                    mobiles=[_mobile(VENDOR_SERIAL, *VENDOR)],
                    shop_buy=None, goal_id=7), [VENDOR])
    assert f"{ns}_closing_window" not in mem
    assert type(res.action).__name__ == "PopupRequest"


@pytest.mark.parametrize("ns", ["buy", "toolbuy"])
def test_the_closing_marker_is_scoped_to_ONE_trip(ns):
    """`buy_offer_reopens` is in `_CLEANUP_KEYS` because it was NOT, and one unlucky trip
    poisoned every later one for the life of the process. Left behind, this key would
    make the next trip's popup stage wait for a window nobody is closing until
    POPUP_TIMEOUT. Added to the tuple in the same change that introduced it."""
    from anima2.skills.market import BuyMaterialCapability, BuyToolCapability

    owner = BuyMaterialCapability if ns == "buy" else BuyToolCapability
    assert f"{ns}_closing_window" in owner._CLEANUP_KEYS
    # ...and the whole namespace really is cleaned up together, so a key added to the
    # FSM without a line here is visible as an asymmetry rather than as a live defect
    # three weeks later.
    assert f"{ns}_offer_reopens" in owner._CLEANUP_KEYS


def test_a_torn_down_trip_leaves_no_per_trip_counter_for_the_next_one():
    """`_CLEANUP_KEYS` is popped when a trip ends NORMALLY — the step function returns
    `None` and the phase switches to the walk home. A frame torn down MID-trip never
    reaches that line, and bound 2 (a frame expiring on its deadline) makes that a
    measured shape rather than a hypothetical: audit §6.3 recorded `buy_iron` frames
    closing exactly that way on a shard.

    Left behind, a spent `offer_reopens` makes the next trip give up on its first
    `window` tick (measured before the key was added to `_CLEANUP_KEYS` at all: trips 2
    and 3 gave up on tick 1), and a stale `closing_window` makes its popup stage wait out
    POPUP_TIMEOUT for a window nobody is closing. Review-caught."""
    from anima2.skills.market import OFFER_REOPEN_ATTEMPTS

    for ns, skill in (("buy", BuyIron()), ("toolbuy", BuyTinkerTool())):
        # A trip's wreckage, exactly as a mid-trip teardown leaves it.
        mem = {"vendor_spot": VENDOR, "bs_stand": (0, 0),
               f"{ns}_offer_reopens": OFFER_REOPEN_ATTEMPTS,
               f"{ns}_closing_window": True, f"{ns}_closing_wait": 3,
               f"cap_{ns}_goal_id": 7}
        skill._begin_goal(_ctx([_backpack()], memory=mem, goal_id=8))  # a NEW goal
        for key in (f"{ns}_offer_reopens", f"{ns}_closing_window", f"{ns}_closing_wait"):
            assert key not in mem, f"{key} survived into the next trip"

    # ...and the SAME goal is not a new trip, so an in-flight one is left alone.
    mem = {"vendor_spot": VENDOR, "bs_stand": (0, 0), "buy_offer_reopens": 2,
           "cap_buy_goal_id": 7}
    BuyIron()._begin_goal(_ctx([_backpack()], memory=mem, goal_id=7))
    assert mem["buy_offer_reopens"] == 2, "a continuing trip lost its own counter"


# --- walk_readout: where a market trip's walk actually is (follow-up 32) --------------


def _pos(x, y, z=0):
    return Position(x, y, z)


def test_walk_readout_reports_a_wedged_approach_as_short_of_reach():
    """The 203-give-up signature (§30.2/§31), on one line.

    Pim's trips died INSIDE the walk: `mkt_phase=sell` on 134 samples with `sell_stage`
    never written once. Every field the status line carried described the FRAME — admitted,
    ready, unfrozen, age 8 of a 180 budget — and none of them could say he was four tiles
    from a vendor he needed to be two from, not closing.

    Kills the mutant that drops the reach from the render: `d=4` alone is not a diagnosis,
    because a reader cannot tell it from a healthy mid-walk sample without knowing
    SELL_REACH by heart. The comparator has to be ON the line (the `!overdue` lesson).
    """
    mem = {"mkt_phase": "sell", "cap_sell_route": ((2611, 473),),
           "bs_stand": (2607, 477), "sell_stall": 3}
    out = walk_readout(mem, _pos(2607, 477))
    assert out == "trip=sell to=(2611,473) d=4>2 stall=3/6", out


def test_walk_readout_targets_the_leg_being_walked_not_the_final_waypoint():
    """`route[leg]`, not `route[-1]` — and the difference is LIVE PRODUCTION CONFIG.

    `profession.VENDOR_SPOT` is `[TRADE_HUB, (2610, 473)]`, a two-leg route through a
    walled corridor, installed verbatim into `run_village`'s trade blacksmith. On leg 0 the
    walk steers at the HUB and must reach it EXACTLY (`reach = final_reach if last_leg else
    0`, `_walk_route`), so a readout aimed at the final waypoint reports both the wrong
    tile and the wrong threshold.

    Kills two mutants at once: `route[-1]` as the target, and `final_reach` as the
    intermediate leg's reach.
    """
    mem = {"mkt_phase": "sell", "cap_sell_route": ((2610, 474), (2610, 473)),
           "sell_leg": 0, "sell_stall": 0}
    out = walk_readout(mem, _pos(2605, 474))
    assert "to=(2610,474)+1" in out, out          # the hub, and one waypoint still after it
    assert "d=5>0" in out, out                    # intermediate legs need chebyshev 0
    # On the last leg the same route reports the vendor, at the real reach.
    mem["sell_leg"] = 1
    assert "to=(2610,473) d=5>2" in walk_readout(mem, _pos(2605, 474))


def test_walk_readout_calls_it_arrived_off_the_final_waypoint_even_with_a_stale_leg():
    """Mirrors `_walk_route`'s OWN ordering: it reach-tests `route[-1]` before it looks at
    any leg cursor, so a trip standing beside its vendor is arrived regardless of where the
    cursor sits. `{tag}_leg` is only written when a leg COMPLETES, so on the single-waypoint
    routes `stage_shops` produces it is never written at all — reading it first would report
    a walk toward waypoint 0 for the entire vendor interaction.

    Kills the mutant that checks the leg before the final reach.
    """
    mem = {"mkt_phase": "sell", "cap_sell_route": ((2610, 474), (2610, 473)),
           "sell_leg": 0}
    out = walk_readout(mem, _pos(2611, 473))
    assert out == "trip=sell to=(2610,473) d=1<=2 stall=-/6", out


def test_walk_readout_follows_the_return_leg_home_at_reach_zero():
    """`_market_return_step` walks `reversed(route[:-1]) + [bs_stand]` and needs the stand
    tile EXACTLY. A readout that kept pointing at the vendor would show a return trip
    getting further from its target the closer it got to finishing.

    Kills the mutant that treats `<ns>_return` as the outbound phase.
    """
    mem = {"mkt_phase": "sell_return", "cap_sell_route": ((2610, 474), (2610, 473)),
           "bs_stand": (2609, 474), "sell_return_leg": 1}
    out = walk_readout(mem, _pos(2610, 473))
    assert out == "trip=sell_return to=(2609,474) d=1>0 stall=-/6", out


def test_walk_readout_renders_standing_on_an_intermediate_waypoint_as_arrived_at_it():
    """`_walk_route` advances the leg on the tick the cursor's waypoint is reached, so
    `d == reach == 0` is a real, observable state. The comparator is COMPUTED; hardcoding
    `>` on the non-final branch would print `d=0>0`, a lie, once per leg per route.

    The hub must be far enough from the final waypoint that the arrival branch does NOT
    win first — a fixture standing 1 tile from a reach-2 vendor is ARRIVED, and would pin
    nothing (§35.3: a test can pass while never reaching the case it names).
    """
    mem = {"mkt_phase": "bank", "cap_bank_route": ((10, 0), (20, 0)), "bank_leg": 0}
    assert "to=(10,0)+1 d=0<=0" in walk_readout(mem, _pos(10, 0))


def test_walk_readout_never_renders_a_state_as_empty():
    """The `deaths=` rule: an absent field is ambiguous between "nothing happened" and "a
    build that could not compute one", and on a wedged run the whole value of this group is
    that it is PRESENT and says so. Every state has a rendering, including the failures.
    """
    assert walk_readout({}, _pos(0, 0)) == "trip=none"          # never traded
    assert walk_readout({"mkt_phase": "craft"}, _pos(0, 0)) == "trip=craft"   # between trips
    # A phase whose route cannot be read at all — never silence, and never a fake tile.
    assert walk_readout({"mkt_phase": "sell"}, _pos(0, 0)) == "trip=sell to=?"
    assert walk_readout({"mkt_phase": "sell_return", "cap_sell_route": ((1, 1),)},
                        _pos(0, 0)) == "trip=sell_return to=?"   # no bs_stand
    # And anything at all going wrong is still a token, not an exception and not "".
    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("memory exploded")
    assert walk_readout(_Boom(), _pos(0, 0)) == "trip=?"
    assert walk_readout({"mkt_phase": "sell", "cap_sell_route": ((1, 1),)}, None) == "trip=?"


def test_walk_readout_falls_back_to_the_configured_spot_for_the_legacy_market():
    """`BlacksmithMarket` (the non-capability skill) never writes `cap_{ns}_route` — it
    calls `_route(vendor_spot)` inline every tick. Reading only the capability key would
    blank the group for every `run_village` blacksmith.
    """
    out = walk_readout({"mkt_phase": "sell", "vendor_spot": VENDOR}, _pos(0, 0))
    assert out == f"trip=sell to=({VENDOR[0]},{VENDOR[1]}) d=10>2 stall=-/6", out
    out = walk_readout({"mkt_phase": "bank", "banker_spot": BANKER}, _pos(0, 0))
    assert out == f"trip=bank to=({BANKER[0]},{BANKER[1]}) d=10>2 stall=-/6", out


def test_walk_readout_covers_every_walking_phase_the_market_fsms_write():
    """`mkt_phase`'s value IS the walk tag, on all eight walking phases. A phase missing
    from `WALK_PHASES` renders as a bare `trip=<phase>` — indistinguishable from the idle
    `craft`, i.e. a frozen buy trip reported as an idle agent.

    Pins the table against the FSMs rather than against itself: every `mkt_phase` value
    assigned anywhere in market.py must either be `craft` or resolve to a route.
    """
    import re
    from pathlib import Path
    src = Path(market_module.__file__).read_text()
    written = set(re.findall(r'mkt_phase"\]\s*=\s*"(\w+)"', src))
    assert "sell" in written and "toolbuy_return" in written, written  # the regex still works
    for phase in written:
        if phase == "craft":
            continue
        ns = phase[:-7] if phase.endswith("_return") else phase
        assert ns in WALK_PHASES, f"{phase} is a walking phase with no route row"


def test_walk_readout_reverses_a_multi_leg_return_route():
    """`_market_return_step` hands `_walk_route` `reversed(route[:-1]) + [bs_stand]`, so on
    a three-waypoint outbound route the first leg home is the SECOND-to-last waypoint, not
    the first.

    This needs THREE waypoints to test at all: on a two-waypoint route `route[:-1]` is a
    single element and reversing it is a no-op, so the obvious fixture pins nothing while
    passing — the §35.3 trap, caught here by a mutant that survived it.
    """
    mem = {"mkt_phase": "sell_return", "cap_sell_route": ((10, 0), (20, 0), (30, 0)),
           "bs_stand": (0, 0), "sell_return_leg": 0}
    assert "to=(20,0)+2 d=5>0" in walk_readout(mem, _pos(25, 0))


def test_walk_readout_clamps_a_leg_cursor_that_outruns_its_route():
    """A stale `{tag}_leg` — left by a longer route, or a non-int — must not index off the
    end. Unclamped this raises `IndexError`, which the guard turns into `trip=?`: the one
    outcome that loses the whole diagnosis, on a line whose entire purpose is being readable
    when everything else has failed.
    """
    mem = {"mkt_phase": "sell", "cap_sell_route": ((10, 0),), "sell_leg": 5}
    assert walk_readout(mem, _pos(0, 0)) == "trip=sell to=(10,0) d=10>2 stall=-/6"
    mem["sell_leg"] = None
    assert walk_readout(mem, _pos(0, 0)) == "trip=sell to=(10,0) d=10>2 stall=-/6"


def test_walk_readout_reads_the_return_leg_s_own_stall_counter():
    """`_market_return_step` passes tag `"sell_return"` down to `_market_walk_toward`, which
    writes `sell_return_stall`. Reading `sell_stall` instead would report the OUTBOUND leg's
    counter — a value not in `_CLEANUP_KEYS`, so stale rather than absent.

    Kills the mutant that strips `_return` off the stall key: a tinker wedged on the walk
    HOME would otherwise render `stall=-/6` forever, and `stall=-` is documented to mean no
    greedy step is running — the readout would say the walk is not happening while it is
    wedged inside it, which is the exact inversion this field exists to prevent.
    """
    mem = {"mkt_phase": "sell_return", "cap_sell_route": ((10, 0),), "bs_stand": (0, 0),
           "sell_stall": 0, "sell_return_stall": 4}
    assert walk_readout(mem, _pos(5, 0)) == "trip=sell_return to=(0,0) d=5>0 stall=4/6"


@pytest.mark.parametrize("phase,route_key,spot_key,spot", [
    ("sell", "cap_sell_route", "vendor_spot", VENDOR),
    ("bank", "cap_bank_route", "banker_spot", BANKER),
    ("buy", "cap_buy_route", "vendor_spot", VENDOR),
    ("toolbuy", "cap_toolbuy_route", "vendor_spot", VENDOR),
])
def test_walk_readout_covers_all_four_families_by_value(phase, route_key, spot_key, spot):
    """Every `WALK_PHASES` row, exercised through BOTH of its sources.

    Without this only `sell` and `bank` had value coverage, so a row whose route key or
    spot key was wrong — `buy` pointed at `banker_spot`, `toolbuy` at `cap_buy_route` —
    rendered `to=?` on a live buy trip and no test noticed. `to=?` reads as "this walk has
    no route", which is a real and different failure.
    """
    assert walk_readout({"mkt_phase": phase, route_key: ((7, 0),)},
                        _pos(0, 0)) == f"trip={phase} to=(7,0) d=7>2 stall=-/6"
    assert walk_readout({"mkt_phase": phase, spot_key: spot},
                        _pos(0, 0)) == f"trip={phase} to=({spot[0]},{spot[1]}) d=10>2 stall=-/6"


def test_walk_readout_calls_exactly_final_reach_arrived():
    """The boundary the field exists to report. `_walk_route` arrives on `<=`, so standing
    at exactly `SELL_REACH` is ARRIVED — off by one in either direction and the line
    disagrees with the walk at the only distance where the answer changes.
    """
    mem = {"mkt_phase": "sell", "cap_sell_route": ((10, 0),)}
    assert "d=2<=2" in walk_readout(mem, _pos(8, 0))    # exactly at reach: arrived
    assert "d=3>2" in walk_readout(mem, _pos(7, 0))     # one further: still walking


def test_walk_readout_leaves_the_stall_counter_behind_on_an_ordinary_arrival():
    """`stall=-` does NOT mean arrived. `_walk_route` returns `_ARRIVED` from its
    final-reach test without touching the counter, so a trip that walked and then arrived
    still shows its last value. Pinned because the docstring said otherwise for a day, and
    a reader who takes `stall=-` for "not walking" reads a wedge as an idle agent.
    """
    mem = {"mkt_phase": "sell", "cap_sell_route": ((10, 0),), "sell_stall": 0}
    assert walk_readout(mem, _pos(9, 0)) == "trip=sell to=(10,0) d=1<=2 stall=0/6"


def test_walk_readout_arrival_boundary_is_inclusive_on_a_multi_leg_route():
    """`_walk_route` arrives on `<=`, and the single-waypoint case cannot tell `<=` from
    `<`: the leg branch renders an identical string there, so the boundary mutant is
    EQUIVALENT and survives. On a multi-waypoint route the two branches name different
    tiles, which is the only fixture that can see the difference.

    Standing exactly `SELL_REACH` from the FINAL waypoint is arrived at the final waypoint,
    even though the leg cursor still points at the hub.
    """
    mem = {"mkt_phase": "sell", "cap_sell_route": ((0, 0), (10, 0)), "sell_leg": 0}
    assert walk_readout(mem, _pos(8, 0)) == "trip=sell to=(10,0) d=2<=2 stall=-/6"
