"""A vendor in `MockBody`, and the live artifacts it finally makes reproducible offline.

Audit follow-up 22, deferred twice on the argument that "adding a vendor to `MockBody` is
a production-code change for a test's benefit; the wedge is covered by FSM-level tests
plus that harness". Two live artifacts eventually stood on the other side of it: §8.2's
Life-level wedge reproduction was run from a scratch harness that no longer exists, and
§19's bound-1 gate — the ONLY live proof of the give-up ladder this project has — shipped
with no offline reproduction at all, where the bound-3 gate has seven.

These tests reproduce the bound-1 gate's own sequence against the real `_buy_step`, with
nothing injected into memory: the FSM meets a vendor whose window genuinely lacks its
offer, exactly as it met a Healer on the shard.
"""

import pytest

from anima2.contract import (
    BuyItems,
    ItemView,
    MobileView,
    PlayerView,
    PopupSelect,
    Position,
    ShopBuyEntry,
)
from anima2.mock_body import MockBody, MockVendor
from anima2.persona import Persona
from anima2.skills.base import SkillContext
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.hunt import GOLD_GRAPHIC
from anima2.skills.market import OFFER_REOPEN_ATTEMPTS, is_vendor_cancel
from anima2.skills.smelt import INGOT_GRAPHICS
from anima2.skills.tinkering import BuyIron

PLAYER, BP, VENDOR = 0x1, 0x50, 0xBEEF
SPOT = (10, 10)
#: The vendor's FOR-SALE display graphic, read off the skill so a fixture cannot drift
#: from it. It is NOT one of `INGOT_GRAPHICS` — those are the pack pile-size variants a
#: bought stack merges into, and `live_buy_goal._pack_iron` carries the same distinction.
#: Getting this wrong is invisible: the FSM simply never finds its offer and re-rolls,
#: which looks exactly like the give-up this file is testing.
IRON = BuyIron.buy_offer_graphic
BANDAGE = 0x0E21


def _entry(serial, graphic, price, amount=20, name="x"):
    return ShopBuyEntry(price=price, name=name, serial=serial, graphic=graphic,
                        amount=amount)


def _world(windows, gold=500):
    body = MockBody(player=PlayerView(serial=PLAYER, name="T", pos=Position(*SPOT, 0),
                                      hits=80, hits_max=80, body=0x190))
    body.items[BP] = ItemView(serial=BP, graphic=0x0E75, amount=1, pos=Position(),
                              container=PLAYER, layer=BACKPACK_LAYER, distance=0)
    body.items[0x800] = ItemView(serial=0x800, graphic=GOLD_GRAPHIC, amount=gold,
                                 pos=Position(), container=BP, layer=0, distance=0)
    body.mobiles[VENDOR] = MobileView(serial=VENDOR, name="V", body=0x190, notoriety=1,
                                      hits=50, hits_max=50, distance=0,
                                      pos=Position(*SPOT, 0))
    body.vendors[VENDOR] = MockVendor(serial=VENDOR, windows=windows)
    return body


def _drive(body, skill, memory, ticks=200):
    """Tick the real `_buy_step` against the real mock until the trip ends."""
    for _ in range(ticks):
        obs = body.observe()
        ctx = SkillContext(obs=obs, persona=Persona(name="T"), memory=memory, goal_id=7)
        res = skill._buy_step(ctx, [SPOT])
        if res is None:
            return "trip over"
        if res.action is not None:
            body.act(res.action)
    return "never ended"


def test_the_mock_vendor_answers_the_three_packets_a_buy_trip_sends():
    """`PopupRequest` -> menu, `PopupSelect` -> window, `BuyItems` -> goods in, coin out.
    Nothing else: a body double owes the FSM the packets it sends and no more."""
    from anima2.contract import PopupRequest
    from anima2.skills.market import BUY_CLILOC

    body = _world([[_entry(0x11, IRON, 5, amount=99, name="iron ingot")]])
    assert body.observe().popup is None and body.observe().shop_buy is None

    body.act(PopupRequest(serial=VENDOR))
    popup = body.observe().popup
    assert popup is not None and popup.serial == VENDOR
    assert [e.cliloc for e in popup.entries] == [BUY_CLILOC]

    body.act(PopupSelect(serial=VENDOR, index=0))
    window = body.observe().shop_buy
    assert body.observe().popup is None
    assert window is not None and window.vendor == VENDOR
    assert [e.graphic for e in window.entries] == [IRON]

    body.act(BuyItems(vendor=VENDOR, items=[(0x11, 15)]))
    obs = body.observe()
    assert obs.shop_buy is None, "a completed order closes the window"
    assert sum(i.amount for i in obs.items if i.graphic == IRON) == 15
    assert sum(i.amount for i in obs.items if i.graphic == GOLD_GRAPHIC) == 500 - 15 * 5


def test_windows_are_OPENINGS_so_a_reroll_can_find_what_the_first_one_lacked():
    """The design decision this class exists for. ServUO shows a vendor's goods in partial
    SUBSETS — one opening can lack an item the next carries, which is the pairing bug
    `OFFER_REOPEN_ATTEMPTS` exists for. Modelling a flat stock instead would make the
    re-roll path untestable by construction."""
    from anima2.contract import PopupRequest

    body = _world([[_entry(0x22, BANDAGE, 5)],                       # opening 1: no iron
                   [_entry(0x11, IRON, 5, name="iron ingot")]])      # opening 2: iron
    seen = []
    for _ in range(4):
        body.act(PopupRequest(serial=VENDOR))
        body.act(PopupSelect(serial=VENDOR, index=0))
        seen.append([e.graphic for e in body.observe().shop_buy.entries])
        body.act(BuyItems(vendor=VENDOR, items=[]))                  # EndVendorBuy
    assert seen == [[BANDAGE], [IRON], [BANDAGE], [IRON]], seen
    assert body.vendors[VENDOR].opens == 4


def test_the_bound_1_gate_reproduced_offline_against_the_real_buy_fsm():
    """§19's live gate, offline. A vendor that stocks bandages and no iron — the Healer —
    so the FSM re-rolls its full budget, cancels rather than buys, and gives up.

    The live verdict was `rerolls=4/4 cancels=5 iron=0` and a frame retiring
    `-> giveup` at age 21 of 180. The frame half needs a Life; this is the FSM half, and
    it is the part that had no offline reproduction at all."""
    body = _world([[_entry(0x22, BANDAGE, 5, name="clean bandage")]])
    memory = {"vendor_spot": (SPOT,), "bs_stand": SPOT, "mkt_phase": "buy",
              "cap_buy_goal_id": 7, "cap_buy_route": (SPOT,)}
    outcome = _drive(body, BuyIron(), memory)

    assert outcome == "trip over", "the trip must END, not spin until the deadline"
    assert memory.get("buy_offer_reopens") == OFFER_REOPEN_ATTEMPTS, (
        f"the whole re-roll budget must be spent: {memory.get('buy_offer_reopens')}")
    assert body.vendors[VENDOR].opens >= 2, (
        f"a re-roll must REOPEN the window, not re-read one snapshot: "
        f"{body.vendors[VENDOR].opens} openings")
    cancels = [a for a in body.actions if isinstance(a, BuyItems) and is_vendor_cancel(a)]
    assert cancels, "every re-roll closes the window it read — an empty-list EndVendorBuy"
    assert not [a for a in body.actions if isinstance(a, BuyItems) and a.items], (
        "nothing may be BOUGHT from a vendor that never offered it")
    assert sum(i.amount for i in body.observe().items if i.graphic in INGOT_GRAPHICS) == 0
    assert sum(i.amount for i in body.observe().items
               if i.graphic == GOLD_GRAPHIC) == 500, "and no coin may leave"


def test_a_vendor_that_stocks_the_offer_still_completes_the_buy():
    """The control. Without it the test above passes just as well against an FSM that
    can no longer buy anything at all."""
    body = _world([[_entry(0x22, BANDAGE, 5),
                    _entry(0x11, IRON, 5, amount=99, name="iron ingot")]])
    memory = {"vendor_spot": (SPOT,), "bs_stand": SPOT, "mkt_phase": "buy",
              "cap_buy_goal_id": 7, "cap_buy_route": (SPOT,)}
    _drive(body, BuyIron(), memory)
    bought = sum(i.amount for i in body.observe().items if i.graphic in INGOT_GRAPHICS)
    assert bought == BuyIron.buy_amount, f"the FSM must still buy when it can: {bought}"
    assert memory.get("buy_offer_reopens", 0) == 0, "and must not re-roll a good window"


@pytest.mark.parametrize("windows", [[], [[]]])
def test_a_vendor_with_an_empty_window_is_a_give_up_not_a_crash(windows):
    """Degenerate stock: no entries at all. The FSM must still end its trip."""
    body = _world(windows)
    memory = {"vendor_spot": (SPOT,), "bs_stand": SPOT, "mkt_phase": "buy",
              "cap_buy_goal_id": 7, "cap_buy_route": (SPOT,)}
    assert _drive(body, BuyIron(), memory) == "trip over"


# --- the FRAME half of §19: bound 1 end to end, offline --------------------------------
#
# §20.4 named this as the piece still missing: the tests above drive `_buy_step`, and a
# `-> giveup` RETIREMENT needs a Life with a goal stack. This is that, over the same
# vendor — so the whole of the live gate's sequence, from "the window has no iron" to
# "the frame retired through the give-up ladder", now exists offline.


def _tinker_that_can_only_buy(windows):
    """A tinker whose rule can reach exactly ONE branch: `buy_iron`.

    Every other branch is closed by construction rather than by luck, which is the same
    discipline the live gate stages with: a tool (so `buy_tinker_tool` is unready), no
    tongs (no `sell_tongs`), no iron anywhere (no `craft_tongs`, no `fetch_iron`), and NO
    `banker_spot` at all — which closes BOTH bank branches, the urgent one and the patient
    one, without depending on a gold threshold that a knob could move.
    """
    from anima2.skills.tinkering import TINKERTOOLS_GRAPHICS
    from anima2.tinker_life import TinkerLife

    body = _world(windows, gold=500)
    body.items[0x900] = ItemView(serial=0x900, graphic=sorted(TINKERTOOLS_GRAPHICS)[0],
                                 amount=1, pos=Position(), container=BP, layer=0,
                                 distance=0)
    life = TinkerLife(body=body, persona=Persona(name="Pim"),
                      routes={"vendor_spot": (SPOT,)})
    life.set_leash(SPOT, 3)
    return body, life


def _retire(life, frame, limit=None):
    from anima2.life_runner import frame_retirements

    budget = frame.deadline_tick - frame.created_tick
    for _ in range(limit or budget * 3):
        life.tick()
        rows = frame_retirements(life)
        if rows:
            return rows[0], budget
    return None, budget


def _admitted_buy(life, limit=40):
    for _ in range(limit):
        life.tick()
        frame = life.econ_agent.goal_stack.current
        if frame is not None and frame.goal.params.get("capability") == "buy_iron":
            return frame
    return None


def test_a_LIFE_retires_its_buy_frame_through_bound_1_when_the_vendor_has_no_iron():
    """§19's live verdict was `retired=(1, 'buy_ingots', 21, 180, 'giveup')` — age 21
    against a 180-tick budget. This is that retirement offline, over a Life, against a
    vendor stocking bandages and no iron.

    It is the test follow-up 19 could not have: before that fix the same world rode the
    frame to its DEADLINE, which is the 540 dead ticks §17.4 measured live."""
    body, life = _tinker_that_can_only_buy([[_entry(0x22, BANDAGE, 5, name="bandage")]])
    frame = _admitted_buy(life)
    assert frame is not None, "the fixture must actually admit buy_iron"
    row, budget = _retire(life, frame)
    assert row is not None, "the frame must RETIRE, not ride to the deadline and beyond"
    assert row[1] == "buy_iron" and row[4] == "giveup", row
    assert row[2] < budget, (
        f"bound 1 must close it EARLY — paying the full {budget}-tick deadline for a "
        f"decision the FSM already made is the defect follow-up 19 removed: {row}")
    assert sum(i.amount for i in body.observe().items
               if i.graphic in INGOT_GRAPHICS) == 0, "and nothing was bought"


def test_the_same_LIFE_retires_ACHIEVED_when_the_vendor_stocks_the_iron():
    """The control, and the one that matters most for the marker's safety: follow-up 19
    writes `cap_run_finished_goal_id` UNCONDITIONALLY, so if `CapabilityGoalComplete` ever
    stopped testing achievement first, every successful buy would start reporting as a
    give-up. Live, `live_buy_goal`'s `exact_goal_frame_succeeded_once` is this assertion;
    offline, it is this test."""
    body, life = _tinker_that_can_only_buy(
        [[_entry(0x22, BANDAGE, 5), _entry(0x11, IRON, 5, amount=99, name="iron ingot")]])
    frame = _admitted_buy(life)
    assert frame is not None
    row, _ = _retire(life, frame)
    assert row is not None and row[1] == "buy_iron", row
    assert row[4] == "achieved", f"a buy that got its iron is not a give-up: {row}"
    assert sum(i.amount for i in body.observe().items
               if i.graphic in INGOT_GRAPHICS) > 0


# --- follow-up 25: a vendor that just proved it is dry ---------------------------------
#
# §22.2, live: a tinker whose vendor sold out after two purchases re-admitted `buy_iron`
# 49 MORE TIMES in one 1800-tick day. Every give-up was correct — there really was no
# iron — and the world-fact "this vendor is dry" was one nothing carried from one trip to
# the next. It was invisible until follow-up 19 made a failed buy cost 17 ticks instead of
# 180: at the old price the run could not have fitted 49 of them.


def test_the_fsm_marks_a_vendor_dry_only_when_the_OFFER_was_absent():
    """The marker means "this vendor does not stock it", not "the trip failed". A menu
    that never arrived, or a vendor that could not be found, are different world-facts
    and must not stand the branch down — the rule would then skip a shop that is fine."""
    from anima2.skills.market import VENDOR_DRY_BACKOFF, vendor_dry

    # Offer absent from every window -> dry.
    body = _world([[_entry(0x22, BANDAGE, 5, name="bandage")]])
    memory = {"vendor_spot": (SPOT,), "bs_stand": SPOT, "mkt_phase": "buy",
              "cap_buy_goal_id": 7, "cap_buy_route": (SPOT,)}
    _drive(body, BuyIron(), memory)
    until, now = memory.get("buy_dry_until"), int(memory.get("mkt_tick", 0))
    assert type(until) is int and until > now, (until, now)
    assert until - now <= VENDOR_DRY_BACKOFF, (
        f"the stand-down is one backoff from the give-up, not from zero: {until} vs {now}")
    assert vendor_dry(memory) is True

    # No vendor near the route's end at all -> NOT dry; the shop was never seen.
    lonely = _world([[_entry(0x11, IRON, 5, name="iron ingot")]])
    lonely.mobiles.clear()
    mem2 = {"vendor_spot": (SPOT,), "bs_stand": SPOT, "mkt_phase": "buy",
            "cap_buy_goal_id": 8, "cap_buy_route": (SPOT,)}
    _drive(lonely, BuyIron(), mem2)
    assert "buy_dry_until" not in mem2, (
        "a vendor that never appeared says nothing about its stock")
    assert vendor_dry(mem2) is False


def test_a_dry_vendor_stands_the_rule_down_and_the_backoff_expires():
    """The rule stops ASKING — deliberately not a gate. A gate refusing here while the
    rule still wanted the buy would be a manufactured rule-vs-gate disagreement, and the
    detector built to catch those would fire every time a shop ran out."""
    from anima2.skills.market import VENDOR_DRY_BACKOFF, vendor_dry

    memory = {"mkt_tick": 100, "buy_dry_until": 100 + VENDOR_DRY_BACKOFF}
    assert vendor_dry(memory) is True
    memory["mkt_tick"] = 100 + VENDOR_DRY_BACKOFF - 1
    assert vendor_dry(memory) is True
    memory["mkt_tick"] = 100 + VENDOR_DRY_BACKOFF
    assert vendor_dry(memory) is False, "the stand-down must EXPIRE, not be permanent"
    # A malformed marker is not a stand-down: a Life must never be talked out of trading
    # by a bad write.
    for bad in (None, "soon", 12.5, True):
        assert vendor_dry({"mkt_tick": 0, "buy_dry_until": bad}) is False, repr(bad)


def test_every_material_buy_rule_consults_it_not_just_the_one_that_was_measured():
    """The tinker is where the 49 trips were measured. Wiring only the tinker would leave
    four copies of one rule with the fix in one of them, which is the defect class §12 and
    §13 are both instances of."""
    import ast
    import inspect

    import anima2.carpenter_life as cl
    import anima2.mage_life as ml
    import anima2.tinker_life as tl
    import anima2.warrior_life as wl

    for mod, cap in ((tl, "buy_iron"), (cl, "buy_boards"),
                     (wl, "buy_bandage"), (ml, "buy_reagent")):
        src = inspect.getsource(mod)
        assert "vendor_dry(" in src, f"{mod.__name__} never consults it"
        # ...and it guards the BUY branch, not something else in the file.
        tree = ast.parse(src)
        calls = {n.func.id for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "vendor_dry" in calls, mod.__name__
        assert cap in src, mod.__name__


def test_a_LIFE_stops_walking_back_to_a_vendor_that_has_nothing():
    """The end-to-end shape the live day showed, offline: with the vendor dry the tinker
    must not keep re-admitting `buy_iron` forever. Before follow-up 25 this world produced
    one give-up per trip for as long as the run lasted."""
    from anima2.life_runner import frame_retirements

    body, life = _tinker_that_can_only_buy([[_entry(0x22, BANDAGE, 5, name="bandage")]])
    frame = _admitted_buy(life)
    assert frame is not None
    for _ in range(600):
        life.tick()
    rows = [r for r in frame_retirements(life) if r[1] == "buy_iron"]
    assert rows, "the first trip must still happen — the tinker cannot know in advance"
    assert all(r[4] == "giveup" for r in rows), rows
    assert len(rows) <= 4, (
        f"after learning the vendor is dry the rule must stop asking; it made "
        f"{len(rows)} trips in 600 ticks")


def test_the_stand_down_is_counted_in_a_clock_its_own_decision_cannot_halt():
    """The bug this fix was FIRST written with, kept as a test.

    `vendor_dry` originally measured its backoff in `mkt_tick`, which only advances inside
    a market skill's `step`. Standing the buy branch down sends the Life to hunt mode, which
    stops ticking the economy agent, which stops advancing `mkt_tick` — so the expiry was
    never reached. Measured on this exact fixture before the fix: `mkt_tick=18
    dry_until=196` still frozen after 1200 ticks, i.e. a PERMANENT stand-down wearing a
    timer.

    It is the same shape §5 records for `expire_due` ("frozen, the deadline is unreachable
    by construction") and the one §18 discarded a bound-2 experiment over. A threshold must
    never be counted in a clock the decision it gates can halt."""
    from anima2.life_runner import frame_retirements
    from anima2.skills.market import VENDOR_DRY_BACKOFF, market_clock

    body, life = _tinker_that_can_only_buy([[_entry(0x22, BANDAGE, 5, name="bandage")]])
    assert _admitted_buy(life) is not None
    for _ in range(1200):
        life.tick()
    mem = life.econ_agent.memory

    # The clock kept moving even though the economy agent mostly did not.
    assert market_clock(mem) > life.econ_agent.ticks, (
        f"the clock must not be the one the stand-down halts: clock={market_clock(mem)} "
        f"econ_ticks={life.econ_agent.ticks}")
    assert market_clock(mem) > VENDOR_DRY_BACKOFF * 2

    # ...so the branch was retried repeatedly, not silenced forever.
    rows = [r for r in frame_retirements(life) if r[1] == "buy_iron"]
    assert len(rows) >= 3, (
        f"a stand-down that never expires is a permanent refusal, not a backoff: "
        f"{len(rows)} trips in 1200 ticks")
    # ...and still far below the 49 the live day made with no backoff at all.
    assert len(rows) <= 1200 // VENDOR_DRY_BACKOFF + 2, len(rows)


def test_a_bare_agent_with_no_life_still_gets_a_clock():
    """`market_clock` falls back to `mkt_tick` for a capability villager with no
    orchestrator. Its buy is chosen by the gate rather than by a rule that can stand down,
    so nothing it decides can halt that clock — the fallback is safe for the same reason
    the primary is."""
    from anima2.skills.market import market_clock

    assert market_clock({"life_tick": 40, "mkt_tick": 7}) == 40   # a Life wins
    assert market_clock({"mkt_tick": 7}) == 7                     # bare Agent
    assert market_clock({}) == 0
    for bad in (None, "5", 2.5, True):                            # malformed -> fall on
        assert market_clock({"life_tick": bad, "mkt_tick": 7}) == 7, repr(bad)


def test_the_mock_walks_at_real_uo_coordinates():
    """The default `bounds` spans UO's map, not a 1000x1000 box, and this pins it.

    Every production coordinate in this project is real UO ground — the trade mine is
    ~(2611,474) — so under the old default a fixture built at a real spot had every `Walk`
    refused as an edge bump and never moved. It cost a wrong conclusion: audit §31.2
    reported three hypotheses eliminated by a reproduction that could not walk at all,
    when walking was the thing under investigation. Silent immobility teaches the wrong
    thing twice, once when it passes and once when it is believed."""
    from anima2.contract import Walk

    body = MockBody(player=PlayerView(serial=PLAYER, name="T",
                                      pos=Position(2609, 474, 0), hits=80, hits_max=80))
    start = (body.player.pos.x, body.player.pos.y)
    for _ in range(5):
        body.act(Walk(dir=2))
    moved = (body.player.pos.x, body.player.pos.y)
    assert moved != start, (
        f"a walk at real UO coordinates did not move: {start} -> {moved}; the default "
        f"bounds are excluding the ground every production fixture stands on")
    assert body.player.pos.x == start[0] + 5, moved


def test_a_wedged_market_walk_is_distinguishable_from_a_walking_one():
    """The readout follow-up 29's next run depends on. `{tag}_leg` cannot do it —
    `_walk_route` returns `_ARRIVED` before touching the leg cursor, and on the
    single-waypoint routes `stage_shops` produces leg 0 is also the last leg, so the key is
    never written on any path. `{tag}_stall` is what `_market_walk_toward` maintains."""
    from anima2.contract import MobileView
    from anima2.skills.market import BlacksmithMarket
    from anima2.skills.tinkering import TINKERTOOLS_GRAPHICS, TONGS_GRAPHIC
    from anima2.tinker_life import TinkerLife

    VEND, TINKER = 0xBEEF, (2611, 473)

    def peak_stall(pim, blocked=()):
        body = MockBody(player=PlayerView(serial=PLAYER, name="Pim", pos=Position(*pim, 0),
                                          hits=80, hits_max=80, body=0x190))
        body.blocked = set(blocked)
        body.items[BP] = ItemView(serial=BP, graphic=0x0E75, amount=1, pos=Position(),
                                  container=PLAYER, layer=BACKPACK_LAYER, distance=0)
        for s, g, a in ((0x900, sorted(TINKERTOOLS_GRAPHICS)[0], 1),
                        (0x901, TONGS_GRAPHIC, 5)):
            body.items[s] = ItemView(serial=s, graphic=g, amount=a, pos=Position(),
                                     container=BP, layer=0, distance=0)
        body.mobiles[VEND] = MobileView(serial=VEND, name="T", body=0x190, notoriety=1,
                                        hits=50, hits_max=50, distance=0,
                                        pos=Position(*TINKER, 0))
        life = TinkerLife(body=body, persona=Persona(name="Pim"),
                          routes={"vendor_spot": (TINKER,)})
        life.set_leash(pim, 30)
        peak = 0
        for _ in range(120):
            life.tick()
            peak = max(peak, int(life.econ_agent.memory.get("sell_stall", 0) or 0))
        return peak, life.econ_agent.memory.get("sell_stage")

    clear_peak, _ = peak_stall((2600, 474))
    wedged_peak, wedged_stage = peak_stall(
        (2600, 474), blocked={(2605, y) for y in range(460, 490)})
    assert wedged_peak > clear_peak, (
        f"a wedged approach must look different from a walking one: "
        f"clear={clear_peak} wedged={wedged_peak}")
    assert wedged_peak >= BlacksmithMarket.stall_limit - 1, wedged_peak
    assert wedged_stage is None, "a trip that never arrives must not have set a stage"
