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
from anima2.persona import Persona
from anima2.skills import Blacksmith
from anima2.skills.base import SkillContext
from anima2.skills.craft import DAGGER_GRAPHIC
from anima2.skills.harvest import BACKPACK_LAYER
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
    BankGold,
    BlacksmithMarket,
    BuyIngots,
    BuyTool,
    SellDaggers,
    _bank_reserve,
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
    res = BuyIngots().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), shop_buy=buy, goal_id=17))
    assert not isinstance(res.action, BuyItems)
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
    res = BuyTool().step(_ctx(items, memory=mem, pos=Position(*VENDOR, 0), shop_buy=buy, goal_id=17))
    assert not isinstance(res.action, BuyItems)
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
