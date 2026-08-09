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
