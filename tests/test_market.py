"""BlacksmithMarket's sell/bank phases — hand-built observations, no live server."""

from anima2.contract import (
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
    ShopSell,
    ShopSellItem,
    Use,
    Walk,
)
from anima2.persona import Persona
from anima2.skills import Blacksmith
from anima2.skills.base import SkillContext
from anima2.skills.craft import DAGGER_GRAPHIC
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.market import (
    ASK_RETRY,
    BANK_CLILOC,
    BUY_CLILOC,
    BANK_DEPOSIT_ATTEMPTS,
    BANK_SETTLE_TICKS,
    BANKBOX_LAYER,
    FIND_MOBILE_TIMEOUT,
    GOLD_GRAPHIC,
    POPUP_TIMEOUT,
    SELL_CLILOC,
    SELL_CONFIRM_TIMEOUT,
    BlacksmithMarket,
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


def _ctx(items, *, memory=None, pos=Position(0, 0, 0), gumps=(), shop_sell=None, mobiles=(), popup=None):
    obs = Observation(player=PlayerView(serial=1, pos=pos), items=[_tool(), *items],
                      gumps=list(gumps), shop_sell=shop_sell, mobiles=list(mobiles), popup=popup)
    return SkillContext(obs=obs, persona=Persona(name="T"),
                        memory=memory if memory is not None else {})


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
