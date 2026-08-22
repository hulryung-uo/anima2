"""The EXIT edge of the mode switch: an orchestrator must finish what it started.

`WarriorLife.decide` is a pure function of `(obs, memory)` and structurally cannot see
the goal stack, so it answers "hunt" the instant the world-fact it keyed on flips — and
for a transaction that instant is MID-transaction, the moment the vendor takes the goods
or the coin moves. Ticking the hunt agent from there freezes the economy agent, and with
it the capability FSM, the `cap_*_finished` markers, and `GoalStack.expire_due` (all
three only advance inside `Agent.tick`, and the frame's deadline is counted in that
agent's own ticks). Live 2026-08-03: a carpenter's `sell_furniture` frame sat
mid-`sell_return` for 280 ticks with `econ_agent.ticks` pinned at 5, while the status
line read `admitted=sell_furniture` and nothing was executing it.

The defect is STRUCTURAL — it lives in `WarriorLife.tick`, which all five Lives share,
and every economy branch of every one of them is keyed on state its own transaction
changes. So the first test here is parametrized over all five, not written five times.

Everything below is real production code over the real `anima2.mock_body.MockBody`; the
only thing simulated is the shard's side of a transaction (the vendor taking an item and
paying), because MockBody has no vendor NPC.
"""

import re

import pytest

from anima2.carpenter_life import BOARD_BATCH_COST, BOARDS_PER_ITEM, CarpenterLife
from anima2.contract import GumpView, ItemView, PlayerView, Position
from anima2.life_runner import frame_retirements, telemetry_line
from anima2.mage_life import MageLife
from anima2.mock_body import MockBody
from anima2.persona import Persona
from anima2.skills.carpentry import BuySaw, FetchBoards, SellFurniture
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.hunt import GOLD_GRAPHIC
from anima2.skills.tinkering import TINKERTOOLS_GRAPHICS
from anima2.skills.woodwork import BuyHatchet
from anima2.skills.warrior import BANDAGE_GRAPHIC, SWORD_GRAPHICS, WEAPON_LAYER
from anima2.tinker_life import TinkerLife
from anima2.village import agent_walk_readout
from anima2.warrior_life import BANK_RESERVE, WarriorLife
from anima2.woodsman_life import WoodsmanLife

PLAYER, BP = 0x1, 0x50
SAW = sorted(BuySaw.owned_tool_graphics)[0]
BOARD = sorted(FetchBoards.fetched_graphics)[0]
THRONE = SellFurniture.sold_graphic
GOLD = 0x902


def _item(serial, graphic, amount=1, *, container=BP, layer=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=0)


def _body(*items):
    body = MockBody(player=PlayerView(serial=PLAYER, name="T", pos=Position(5, 5, 0),
                                      hits=80, hits_max=80, body=0x190))
    body.items[BP] = _item(BP, 0x0E75, container=PLAYER, layer=BACKPACK_LAYER)
    for it in items:
        body.items[it.serial] = it
    return body


def _run_to_admission(life, limit=60):
    """Tick until the economy agent owns a capability frame. Returns it."""
    for _ in range(limit):
        life.tick()
        if life.econ_agent.goal_stack.current is not None:
            return life.econ_agent.goal_stack.current
    return None


def _history(life):
    return [(f.goal.params.get("capability"), f.outcome.value)
            for f in life.econ_agent.goal_stack.history]


# --- the structural property, over all five Lives ------------------------------------

#: One self-falsifying transaction per Life, chosen to be the SAME one everywhere:
#: `bank_gold`, whose branch is keyed on `gold > bank_reserve` in all five rules and goes
#: false the moment the coin moves into the bank. Each entry is the extra pack stock that
#: profession needs before its rule will even consider banking (a tool, and for the
#: carpenter enough boards to get past its material branch) — the professions differ in
#: what stops them, not in the shape of the defect.
_BANKERS = [
    ("swordsman", WarriorLife, ()),
    ("mage", MageLife, ()),
    ("lumberjack", WoodsmanLife, (0x910, sorted(BuyHatchet.owned_tool_graphics)[0], 1)),
    ("carpenter", CarpenterLife, (0x910, SAW, 1)),
    ("tinker", TinkerLife, (0x910, sorted(TINKERTOOLS_GRAPHICS)[0], 1)),
]


@pytest.mark.parametrize("profession,cls,tool", _BANKERS,
                         ids=[p for p, _c, _t in _BANKERS])
def test_every_life_finishes_a_transaction_its_own_rule_stopped_wanting(
        profession, cls, tool):
    """The defect is in the shared `tick()`, so the fix is checked on all five Lives.

    Before the exit-edge hold: the coin moves, the rule flips to "hunt" on that very
    tick, the economy agent is never ticked again, and the frame sits admitted forever
    with `econ_agent.ticks` frozen. After: the mode is HELD until the frame retires.
    """
    stock = [_item(GOLD, GOLD_GRAPHIC, 5000)]
    if tool:
        stock.append(_item(*tool))
    if profession == "carpenter":
        # Past the carpenter's material branch, which would otherwise answer before
        # banking; and NO `craft_spot`, so once the gold is gone nothing is admissible.
        stock.append(_item(0x911, BOARD, BOARDS_PER_ITEM))
    body = _body(*stock)
    life = cls(body=body, persona=Persona(name="T"),
               routes={"banker_spot": ((10, 10),)})
    life.set_leash((5, 5), 3)

    frame = _run_to_admission(life)
    assert frame is not None and frame.goal.params.get("capability") == "bank_gold"
    assert life.mode == "economy"

    # THE SELF-FALSIFICATION, exactly as the shard performs it: the coin leaves the
    # pack. `decide` now answers ("hunt", None) for every one of the five rules.
    body.items.pop(GOLD)
    econ_at_falsification = life.econ_agent.ticks
    modes_while_live = set()
    retired_after = None
    for tick in range(1, 401):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            retired_after = tick
            break
        modes_while_live.add(life.mode)

    assert modes_while_live == {"economy"}, (
        f"{profession}: the mode left economy with a live frame on the stack — that is "
        f"the freeze, because only the economy agent's own ticks can retire it")
    assert retired_after is not None, (
        f"{profession}: the frame never retired in 400 orchestrator ticks "
        f"(econ ticks {econ_at_falsification} -> {life.econ_agent.ticks})")
    assert life.econ_agent.ticks > econ_at_falsification, (
        f"{profession}: the frame retired without the economy agent ever being ticked")
    assert _history(life) == [("bank_gold", "failure")], (
        f"{profession}: the frame must close with a TERMINAL outcome the gates can "
        f"read, not simply vanish — got {_history(life)}")
    # And the hold ENDS with the frame: a Life that keeps holding would report a
    # disagreement on a perfectly healthy agent and start closing UI surfaces.
    assert life.holding_frame is False and life.mode == "hunt"


# --- the exact live shape: the carpenter's sale --------------------------------------

def _sale_life():
    """The live t=19 carpenter: a saw, a leftover board, one finished throne, 69 gold."""
    body = _body(_item(0x900, SAW), _item(0x901, BOARD, 1), _item(0x903, THRONE, 1),
                 _item(GOLD, GOLD_GRAPHIC, 69))
    life = CarpenterLife(body=body, persona=Persona(name="Sten"),
                         routes={"vendor_spot": ((10, 10),), "banker_spot": ((10, 10),),
                                 "craft_spot": (5, 5)})
    for memory in (life.memory, life.econ_agent.memory):
        memory["craft_spot"] = (5, 5)
    life.set_leash((5, 5), 3)
    return body, life


def _reach_the_sale(body, life, limit=40):
    """Tick until `sell_furniture` is admitted and walking, then let the vendor take the
    throne and pay 24g — the shard's own side of the transaction, which MockBody cannot
    perform (no vendor NPC, no `shop_sell` window). This is the instant that falsifies
    the rule's `pack_amount(obs, THRONE) >= 1` clause, mid-transaction, with the
    `sell_return` leg still owed."""
    for _ in range(limit):
        life.tick()
        if life.econ_agent.memory.get("cap_sell_goal_id") is not None \
                and life.econ_agent.ticks >= 4:
            body.items.pop(0x903, None)
            body.items[GOLD].amount = 93
            return True
    return False


def test_the_sale_that_froze_live_retires_instead():
    body, life = _sale_life()
    assert _reach_the_sale(body, life), "the sell frame was never admitted"
    # The frozen state the live run reached: mid-`sell_return`, both finished markers
    # unset, so retirement still needs at least one more economy tick.
    assert life.econ_agent.memory.get("mkt_phase") == "sell"
    assert life.econ_agent.memory.get("cap_sell_finished_goal_id") is None
    assert life.econ_agent.memory.get("cap_run_finished_goal_id") is None

    retired_after = None
    for tick in range(1, 401):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            retired_after = tick
            break
    assert retired_after is not None and retired_after < 60, (
        f"the sell frame outlived the sale by {retired_after} ticks")
    assert _history(life) == [("sell_furniture", "failure")]
    # The FSM walked its own give-up ladder home rather than being abandoned in place:
    # `mkt_phase` back to "craft" is the clause TWELVE readiness gates read, so this is
    # what lets the carpenter's next transaction start at all.
    assert life.econ_agent.memory.get("mkt_phase") == "craft"


def test_a_transaction_does_not_re_earn_the_entry_hysteresis_when_it_ends():
    """`econ_grace` exists to filter a TRANSIENT — a blade on the cursor reading as
    "weaponless" for a tick or two. A completed transaction is not a transient, it IS the
    commitment, so the streak is pinned while the hold lasts. Unpinned, the Life pays
    `econ_grace` wander ticks after every transaction whose want expired mid-flight —
    which the rules make that of essentially every transaction.

    Injected at the FSM's own run-finished marker, i.e. exactly one economy tick before
    `CapabilityGoalComplete` closes the frame: that is the only window where the pin can
    show, and it is the window a real Life lands in after every sale.
    """
    body, life = _sale_life()
    assert _reach_the_sale(body, life)
    armed = False
    for _ in range(60):
        life.tick()
        if not armed and life.econ_agent.memory.get("cap_run_finished_goal_id"):
            armed = True
            body.items[GOLD].amount = 5000     # the rule wants `buy_boards` again
            continue
        if armed and life.econ_agent.goal_stack.current is None:
            break
    assert armed and life.target_cap == "buy_boards"
    assert life.mode == "economy", (
        "the tick a held frame retires with the rule already wanting economy again must "
        "commit straight back, not spend econ_grace ticks wandering")
    assert life.holding_frame is False, "this is a genuine want, not a hold"


def test_the_rule_keeps_its_own_voice_while_the_orchestrator_holds():
    """`want=` must stay the RULE's answer. Fixing the `admitted=` lie by rewriting
    `target_cap` to the frame's capability would re-create the same ambiguity on the
    `want=` side — the one `telemetry_line`'s docstring says cost three runs and one
    wrong root cause."""
    body, life = _sale_life()
    assert _reach_the_sale(body, life)
    life.tick()
    assert life.mode == "economy" and life.holding_frame is True
    assert life.target_cap is None, "the hold must not forge a want the rule never had"


# --- death, the one unconditional override -------------------------------------------

def test_death_overrides_the_hold_for_the_whole_episode_including_the_corpse_run():
    """`RecoverDeath` is a WORK-planner reflex and owns the death EPISODE, not just the
    ghost window: its `can_run` is `dead OR death_waiting_resurrection OR
    death_corpse_pending`, and the corpse-reclaim leg runs entirely with
    `obs.player.dead` already False. Keying the override on `dead` alone was
    review-caught taking the body back the tick after resurrection and deferring gear
    recovery by up to the frame's whole budget (177 ticks for a warrior) — which is the
    naked death-loop this module was written to end. The frame simply waits."""
    body, life = _sale_life()
    assert _reach_the_sale(body, life)
    life.tick()
    assert life.mode == "economy" and life.holding_frame is True

    body.player.dead = True
    modes = set()
    for _ in range(12):
        life.tick()
        modes.add(life.mode)
    assert modes == {"hunt"}, "the hold survived a death and stranded the corpse run"
    assert life.holding_frame is False
    assert life.econ_agent.goal_stack.current is not None, "the frame must WAIT, not die"
    # The hunt agent has the hands, and is running the death FSM with them.
    assert life.hunt_agent.memory.get("death_waiting_resurrection")

    body.player.dead = False           # RESURRECTED — and the corpse is still out there
    reached_corpse_leg = None
    for tick in range(1, 41):
        life.tick()
        if life.hunt_agent.memory.get("death_corpse_pending"):
            reached_corpse_leg = tick
            break
    assert reached_corpse_leg is not None, "the corpse leg was never reached"
    assert life.mode == "hunt" and life.holding_frame is False, (
        "the hold took the body back from RecoverDeath mid-corpse-run")
    assert life.econ_agent.goal_stack.current is not None

    # Let `RecoverDeath` run its own course — its route attempts run out against a
    # corpse MockBody never spawned — and only THEN does the hold get the body back.
    # The episode is long (a real corpse run is), which is exactly why deferring it by
    # a frame's whole budget was worth a finding.
    econ_during_episode = life.econ_agent.ticks
    closed_at = retired_after = None
    for tick in range(1, 901):
        life.tick()
        memory = life.hunt_agent.memory
        if closed_at is None and not (memory.get("death_waiting_resurrection")
                                      or memory.get("death_corpse_pending")):
            closed_at = tick
            assert life.econ_agent.ticks == econ_during_episode, (
                "the economy agent was ticked during the corpse run")
        if life.econ_agent.goal_stack.current is None:
            retired_after = tick
            break
    assert closed_at is not None, (
        "the episode never closed, so this proves nothing about the hold resuming")
    assert retired_after is not None and retired_after > closed_at, (
        "the hold never resumed after the episode closed")
    assert _history(life) == [("sell_furniture", "failure")]


# --- the hold is BOUNDED, twice ------------------------------------------------------

def test_a_held_frame_that_gives_up_closes_on_the_fsms_own_ladder():
    """Bound 1, and the usual one: the capability FSM's own timeouts return `mkt_phase`
    to "craft" and set `cap_run_finished_goal_id`, which `CapabilityGoalComplete` turns
    into a FAILURE close on the next economy tick. Being ticked is what reaches it."""
    body, life = _sale_life()
    assert _reach_the_sale(body, life)
    for _ in range(400):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            break
    assert life.econ_agent.ticks < 180, (
        "the FSM's give-up ladder should close this frame long before its deadline")
    assert _history(life) == [("sell_furniture", "failure")]


def test_a_finished_unachieved_craft_closes_on_the_ladder_not_the_deadline():
    """Follow-up 15, live on forge-20260818-0003.

    `craft_tongs#4` reached `cap_craft_stage=finished` with 3 tongs of a batch of 5,
    pack iron 0, and 22 ingots on the ground. `want=fetch_iron` and `ready=['fetch_iron']`
    the whole way. The frame had no give-up marker, so bound 2 spent the remaining
    ~270 ticks and only then did fetch land in 8. Craft now writes the same
    `cap_run_finished_goal_id` sell/bank/buy already did, so this world is bound 1.

    Bound 2 still exists, but not on a finished craft and not on a starved
    FSM (that is bound 3 / overdue). The remaining vehicle is a fetch that
    finishes empty — `_expiring_fetch_life` below.
    """
    body, life, frame = _expiring_craft_life()
    budget = frame.deadline_tick - frame.created_tick
    for _ in range(budget * 3):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            break
    assert _history(life) == [("craft_tongs", "failure")], (
        "a terminal-but-unachieved craft must close on the ladder, not sit to 300")
    assert life.econ_agent.ticks < budget, (
        f"bound 1 must beat the {budget}-tick deadline; got {life.econ_agent.ticks}")
    # The delivery was already there. After the ladder, fetch must actually run.
    from anima2.skills.smelt import INGOT_GRAPHICS
    iron = sorted(INGOT_GRAPHICS)[0]
    body.items[0x200] = ItemView(
        serial=0x200, graphic=iron, amount=22,
        pos=Position(6, 5, 0), container=None, layer=0, distance=1)
    pack_iron = 0
    for _ in range(80):
        life.tick()
        pack_iron = sum(i.amount for i in body.items.values()
                        if i.graphic == iron and i.container == BP)
        if pack_iron > 0:
            break
    assert pack_iron > 0, "fetch_iron never ran — the 2026-08-18 dead window is back"


def _wedged_buy_life():
    """A carpenter with a live `buy_boards` frame, an unowned gump, and no coin.

    This is the world that made bounds 1 and 2 fail TOGETHER, and it is built out of
    nothing but the two halves this file already used separately: the self-falsification
    of `test_every_life_finishes_a_transaction_its_own_rule_stopped_wanting`
    (`body.items.pop(GOLD)`) and the wedge of
    `test_the_hold_does_not_delay_the_disagreement_detector` (a `GumpView` nobody owns).

    Why both bounds die at once: every `*_can_yield` in `capabilities.py` carries an
    unconditional "idle UI" clause, so one open gump makes `deadline_can_expire` False
    forever (bound 2) and makes every readiness gate refuse (bound 1).

    **The third leg used to be "a BUY frame has no give-up ladder at all", and audit
    follow-up 19 made that false** by giving both buy families the neutral
    `cap_run_finished_goal_id` the sell and bank wrappers had carried since forge1. This
    world now closes its frame through BOUND 1, at age 17 instead of at the 180-tick
    deadline — which is the improvement, measured live at 540 dead ticks (audit §17.4).

    So this fixture proves bound 1 now, and the bound-2 tests that used to stand on the
    missing marker moved to `_expiring_craft_life` below. Keeping them on a buy frame
    would have meant preserving the defect to keep testing around it.
    """
    body = _body(_item(0x900, SAW), _item(GOLD, GOLD_GRAPHIC, BOARD_BATCH_COST + 5))
    life = CarpenterLife(body=body, persona=Persona(name="Sten"),
                         routes={"vendor_spot": ((10, 10),), "banker_spot": ((10, 10),)})
    life.set_leash((5, 5), 3)
    frame = _run_to_admission(life)
    assert frame is not None and frame.goal.params.get("capability") == "buy_boards"
    body.items.pop(GOLD)                                   # the rule wants nothing now
    body.gumps = [GumpView(serial=0xABCD, gump_id=0x1234)]  # and nothing can yield
    return body, life, frame


def _expiring_craft_life(*, surface: bool = False):
    """A tinker craft frame whose material vanishes mid-batch.

    Follow-up 15 gave craft the same `cap_run_finished_goal_id` marker the buy
    families got in follow-up 19: once the FSM reaches `finished` without a
    full batch, bound 1 closes the frame. This fixture is therefore the craft
    GIVE-UP vehicle. Bound 2/3 for craft need the FSM starved as well
    (`_starved_craft_life`), matching the live overdue gate.

    `surface` injects the unowned gump the overdue repair is supposed to close.
    """
    from anima2.skills.tinkering import INGOT_GRAPHICS, TinkerTongs

    body = _body(_item(0x900, sorted(TinkerTongs.craft_tool_graphics)[0]),
                 _item(0x901, sorted(INGOT_GRAPHICS)[0],
                       TinkerTongs.craft_material_per_item * TinkerTongs.craft_batch * 4))
    life = TinkerLife(body=body, persona=Persona(name="Pim"), routes={})
    for memory in (life.memory, life.econ_agent.memory):
        memory["craft_spot"] = (5, 5)
    life.set_leash((5, 5), 3)
    frame = _run_to_admission(life)
    assert frame is not None and frame.goal.params.get("capability") == "craft_tongs"
    for serial in [s for s, i in body.items.items() if i.graphic in INGOT_GRAPHICS]:
        body.items.pop(serial)                             # the craft can never finish
    if surface:
        body.gumps = [GumpView(serial=0xABCD, gump_id=0x1234)]
    return body, life, frame


def _starved_craft_life(*, surface: bool = False):
    """Bound-2/3 vehicle: Survive starves the craft FSM so it never reaches `finished`.

    Follow-up 15 closed the no-gump `_expiring_craft_life` through bound 1. An
    overdue craft still needs the live gate's starve — `Survive` is skills[0]
    of the capability planner, so a wounded tinker's economy agent ticks while
    its FSM does not, and no run-finished marker is written.
    """
    body, life, frame = _expiring_craft_life(surface=surface)
    body.items[0x950] = _item(0x950, BANDAGE_GRAPHIC, 40)
    body.player.hits = 20
    return body, life, frame


def test_an_overdue_frame_is_reached_by_a_plain_world_and_never_pins_the_life():
    """The hold's THIRD bound, and the reason it has to exist.

    No deadline is touched here: the frame reaches its own budget on the economy clock
    the hold itself keeps advancing. Without this bound the measured outcome was a total
    livelock — mode pinned to "economy" for 3000 ticks on four of the five Lives, the
    work agent never ticked again, and the Life emitting nothing at all — which is
    strictly worse than the zombie frame the hold was written to fix.
    """
    body, life, frame = _starved_craft_life(surface=True)
    budget = frame.deadline_tick - frame.created_tick
    overdue_at = released_at = None
    for tick in range(1, budget * 3):
        life.tick()
        if overdue_at is None and life.frame_overdue:
            overdue_at = tick
        if released_at is None and overdue_at is not None and life.mode == "hunt":
            released_at = tick
            break
    assert overdue_at is not None, (
        "the frame never went overdue, so this world no longer proves the bound")
    assert released_at is not None, "the hold pinned the Life to economy mode"
    assert released_at <= budget + 4, (
        f"the hold outlived the frame's own {budget}-tick budget by "
        f"{released_at - budget} orchestrator ticks")
    # And the Life really does get its hands back: the work agent runs again.
    work_before = life.hunt_agent.ticks
    for _ in range(50):
        life.tick()
    assert life.hunt_agent.ticks >= work_before + 40


def test_an_overdue_frame_gets_the_stale_ui_repair_pointed_at_it_and_then_retires():
    """`frame_overdue` is not only a report: the surface blocking the yield gets closed.

    `_clear_stale_ui`'s no-goal precondition exists because "a mid-transaction gump
    belongs to a live goal". A frame that has burned its ENTIRE budget without once
    reaching a safe yield point has forfeited that premise — it is exactly the case the
    repair should be allowed to touch — so the wedge's SURFACE resolves: the
    gump closes. After follow-up 15 a starved craft is still `started and not
    terminal`, so `_craft_can_yield` stays false and the frame remains — the
    live overdue gate's documented worst case, "a stale frame, but alive".
    """
    body, life, frame = _starved_craft_life(surface=True)
    budget = frame.deadline_tick - frame.created_tick
    for _ in range(budget * 3):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            break
    assert not body.gumps, "the surface that blocked every yield was never closed"
    assert getattr(life, "_stale_ui_closes", 0) == 1, "and closed exactly once"
    # Follow-up 15 + starve: the FSM never reaches `finished`, so closing the
    # gump does not make `_craft_can_yield` true (`started and not terminal`).
    # That is the live gate's documented worst case — a stale frame, but alive.
    assert life.econ_agent.goal_stack.current is not None
    assert life.frame_overdue is True
    assert _history(life) == []


def _starved_fsm_life():
    """A warrior that will admit `bank_gold` and then be wounded into a starved FSM.

    `WarriorSurvive` latches until 75% HP and needs bandages to bandage with; a blade is
    worn so the rule reaches its banking branch rather than answering `buy_weapon`.
    """
    body = _body(_item(GOLD, GOLD_GRAPHIC, BANK_RESERVE + 5000),
                 _item(0x950, BANDAGE_GRAPHIC, 40),
                 _item(0x960, sorted(SWORD_GRAPHICS)[0], container=PLAYER,
                       layer=WEAPON_LAYER))
    life = WarriorLife(body=body, persona=Persona(name="W"),
                       routes={"banker_spot": ((10, 10),)})
    life.set_leash((5, 5), 3)
    return body, life


def test_the_overdue_release_does_not_depend_on_there_being_a_surface_to_close():
    """The other way both bounds die: a safety interrupt owns the economy agent's hands.

    `Survive`/`WarriorSurvive` sits ABOVE the capability skills in the CAPABILITY
    planner too, so a wounded character's economy agent is ticked every tick while its
    capability FSM is never stepped — `mkt_phase` never returns to "craft", no marker is
    ever written, `can_yield` stays false. There is no surface to repair here, so the
    release must not depend on one. Measured before the bound: 4000 orchestrator ticks,
    zero of them in hunt mode, with a 120-tick budget.
    """
    body, life = _starved_fsm_life()
    frame = _run_to_admission(life)
    assert frame is not None and frame.goal.params.get("capability") == "bank_gold"
    budget = frame.deadline_tick - frame.created_tick

    body.items.pop(GOLD)          # the coin moves: the rule stops wanting the economy
    body.player.hits = 30         # and the warrior is wounded, so Survive takes the hands
    hunt_ticks = 0
    for _ in range(budget * 4):
        life.tick()
        hunt_ticks += life.mode == "hunt"
    assert life.econ_agent.memory.get("mkt_phase") == "bank", (
        "this world no longer starves the capability FSM, so it proves nothing")
    assert not body.gumps and getattr(life, "_stale_ui_closes", 0) == 0
    assert hunt_ticks >= budget * 3 - 4, (
        f"the hold survived a starved FSM with nothing to repair "
        f"({hunt_ticks} hunt ticks in {budget * 4})")
    assert life.frame_overdue is True, "and the stale frame it left behind stays LOUD"


def test_the_overdue_report_is_not_gated_on_the_hold():
    """Same starved FSM, one pack readback different: the coin stays, so the rule still
    WANTS the capability and the mode is economy on the rule's own account — no hold.
    Gating the report on the hold was review-caught making that half completely silent,
    and what the rule happens to want has nothing to do with why the frame is stuck."""
    body, life = _starved_fsm_life()
    frame = _run_to_admission(life)
    assert frame is not None
    budget = frame.deadline_tick - frame.created_tick

    body.player.hits = 30         # wounded only; the coin is still in the pack
    for _ in range(budget * 2):
        life.tick()
    assert life.mode == "economy" and life.holding_frame is False, (
        "the rule itself wants the economy here — there is nothing to hold")
    assert life.frame_overdue is True, (
        "a frame this far past its deadline must be reported whether or not the "
        "orchestrator happens to be holding for it")


# --- the masking cost: the detector gets its states back -----------------------------

def _wedged_world(body, life):
    """The identical end-state both A/B arms enter: a gump nobody owns (one open surface
    makes EVERY readiness gate refuse — the forge15 wedge) plus enough gold that the
    rule wants `buy_boards`. The detector is supposed to fire and repair."""
    body.gumps = [GumpView(serial=0xABCD, gump_id=0x1234)]
    body.items[GOLD].amount = BOARD_BATCH_COST + 86
    for tick in range(1, 121):
        life.tick()
        if life.rule_gate_disagreement is not None:
            return tick
    return None


def test_the_hold_does_not_delay_the_disagreement_detector():
    """The measured cost of the freeze, and the reason it is not merely cosmetic:
    `_apply_cognition` refuses every new capability proposal while a frame is live, so a
    stale frame delayed the stall alarm for a REAL, later problem by 15 ticks. Both arms
    end in an identical world; only the history differs."""
    body_a, life_a = _sale_life()
    assert _reach_the_sale(body_a, life_a)
    for _ in range(40):                       # let the sale finish under the hold
        life_a.tick()
    assert life_a.econ_agent.goal_stack.current is None
    after_a_transaction = _wedged_world(body_a, life_a)

    body_b, life_b = _sale_life()             # control: no transaction ever ran
    body_b.items.pop(0x903)
    for _ in range(40):
        life_b.tick()
    control = _wedged_world(body_b, life_b)

    assert control is not None and after_a_transaction == control, (
        f"a preceding transaction still delays the stall alarm "
        f"({after_a_transaction} vs {control} ticks)")
    assert not body_a.gumps and getattr(life_a, "_stale_ui_closes", 0) == 1


# --- telemetry: `admitted=` can no longer claim work nobody is doing -----------------

def test_telemetry_marks_a_live_frame_that_nobody_is_ticking():
    body, life = _sale_life()
    assert _reach_the_sale(body, life)
    life.tick()
    held = telemetry_line(life, "carpenter", body.observe())
    # An EMPTY gate list, pinned so it cannot be satisfied by `ready=[]` being a PREFIX of
    # `ready=['sell_furniture']`. Twice now that has been written by naming whichever field
    # happened to follow — `endswith("ready=[]")` first, then `"ready=[] retired="` when
    # `retired=` was added — and a follow-up-32 draft that briefly put `trip=` between them
    # broke it a third time. That draft did not ship (the field went to the worker line
    # instead, `village._run_worker`), so nothing here is load-bearing for it; the
    # assertion is fixed anyway, because the intent was never ADJACENCY. `[]` followed by
    # a field boundary, whatever the next field turns out to be called.
    assert re.search(r"ready=\[\](?=\s|$)", held), held
    assert "admitted=sell_furniture@" in held
    assert "+hold" in held and "!frozen" not in held, held

    body.player.dead = True
    life.tick()
    life.tick()
    frozen = telemetry_line(life, "carpenter", body.observe())
    assert "!frozen" in frozen, (
        f"a frame on the stack while the hunt agent has the hands must SAY so: {frozen}")
    assert "want=None" in frozen  # and `want` still speaks only for the rule


def test_the_frame_age_telemetry_counts_the_clock_the_deadline_is_counted_in():
    """`@age/budget` is in ECON-AGENT ticks — the clock `deadline_tick` uses, and the one
    that STOPS when the frame stops being ticked. That is what makes a frozen frame
    visible on a status line printed every few seconds: its age stops moving."""
    body, life = _sale_life()
    assert _reach_the_sale(body, life)
    life.tick()
    frame = life.econ_agent.goal_stack.current
    budget = frame.deadline_tick - frame.created_tick
    age = life.econ_agent.ticks - frame.created_tick
    assert f"admitted=sell_furniture@{age}/{budget}" in telemetry_line(
        life, "carpenter", body.observe())

    body.player.dead = True
    for _ in range(6):
        life.tick()
    frozen_age = life.econ_agent.ticks - frame.created_tick
    line = telemetry_line(life, "carpenter", body.observe())
    assert f"@{frozen_age}/{budget}!frozen" in line
    for _ in range(6):                        # six more ticks, and the age does not move
        life.tick()
    assert telemetry_line(life, "carpenter", body.observe()) == line


def test_a_throttled_life_can_still_reach_the_runner_with_its_self_reports():
    """`_run_worker` prints both self-reports off the object it drives, and for the
    throttled carpenter and the throttled mage that object is `_ThrottledAgent`, which
    has no `__getattr__`. Without an explicit passthrough the two Lives that run their
    economy agent nearly every tick are the two whose alarms can never be heard."""
    from anima2.village import _ThrottledAgent

    body, life = _sale_life()
    assert _reach_the_sale(body, life)
    throttled = _ThrottledAgent(life, every=3)
    throttled.yield_pause_s = 0
    life.rule_gate_disagreement = ("sell_furniture", 11)
    life.frame_overdue = True
    assert throttled.rule_gate_disagreement == ("sell_furniture", 11)
    assert throttled.frame_overdue is True
    # And a plain Agent underneath — which has neither attribute — still reads clean.
    plain = _ThrottledAgent(life.hunt_agent, every=3)
    assert plain.rule_gate_disagreement is None and plain.frame_overdue is False


def test_telemetry_names_an_overdue_frame_instead_of_leaving_it_to_arithmetic():
    """`@age/budget` alone leaves "this frame is past its budget" as a comparison between
    two numbers on the line, and review-caught nobody makes that comparison. `!overdue`
    rides ALONGSIDE the other two markers, so the released stale frame reads
    `!frozen!overdue` — nobody is ticking it, and it is past its budget."""
    body, life, frame = _starved_craft_life(surface=True)
    budget = frame.deadline_tick - frame.created_tick
    line = telemetry_line(life, "tinker", body.observe())
    assert "!overdue" not in line, f"a frame inside its budget is not overdue: {line}"

    for _ in range(budget + 2):
        life.tick()
        if life.frame_overdue:
            break
    assert life.frame_overdue is True
    line = telemetry_line(life, "tinker", body.observe())
    assert "admitted=craft_tongs@" in line and "!overdue" in line, line


def test_the_runner_prints_the_overdue_report_and_throttles_it(capsys):
    """The report is the change's only outward signal for a frame nothing can close, and
    mutation testing found it was the one production hunk NO test killed. It is also
    throttled: unthrottled it measured 3,881 identical lines in one 4,000-tick run, which
    buries the two sibling alarms printed from the same loop."""
    import threading

    from anima2.village import _run_worker

    class _Body:
        connected = True

        def observe(self):
            return MockBody(player=PlayerView(serial=PLAYER, name="T", pos=Position(),
                                              hits=10, hits_max=10, body=0x190)).observe()

    class _Episodes:
        total_recorded = 0

        def total_reward(self):
            return 0.0

        def recent(self, n):
            return []

    class _OverdueAgent:
        body = _Body()
        persona = Persona(name="Sten")
        episodes = _Episodes()
        memory: dict = {}
        frame_overdue = True

        def tick(self):
            return None

    _run_worker(_OverdueAgent(), 85, 0, {}, threading.Lock(), "carpenter")
    out = capsys.readouterr().out
    assert "FRAME OVERDUE" in out, "a frame nothing can close must say so"
    assert 1 < out.count("FRAME OVERDUE") < 10, (
        f"it must keep saying it, but not once per tick: {out.count('FRAME OVERDUE')} "
        f"lines in 85 ticks")


def test_telemetry_says_nothing_extra_when_no_frame_is_live():
    """`admitted=None` is the shape every existing reader knows; the decorations ride on
    the frame, so a Life with an empty stack prints exactly what it printed before."""
    body, life = _sale_life()
    life.tick()
    line = telemetry_line(life, "carpenter", body.observe())
    assert "admitted=None" in line and "@" not in line
    assert "!frozen" not in line and "+hold" not in line


# --- bound 1, made observable: WHY a frame retired, not just that it did -------------
#
# The three decorations above all describe the frame that is HERE. A frame that has
# already gone is simply ABSENT, which is why bound 1 (the FSM's give-up ladder) could
# not be told from an ordinary successful sale on any 2026-08-03 log — and why the
# standing count is "bounds 2 and 3 live-proven, bound 1 not". The reason field is a
# projection of `frame.outcome`, which `GoalStack._archive` has always stamped.
# `tests/test_capabilities.py` holds the achievement-vs-give-up arms on a real Agent;
# these are the end-to-end ones, through a real Life and a real FSM.


def test_a_real_give_up_ladder_is_reported_as_bound_1():
    """The live t=19 carpenter's sale, which `test_the_sale_that_froze_live_retires_instead`
    already proves closes on the ladder rather than by achievement. The status line said
    only that the frame was gone; now it says which branch closed it."""
    from anima2.life_runner import frame_retirements, retirement_tally

    body, life = _sale_life()
    assert _reach_the_sale(body, life)
    for _ in range(80):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            break
    assert _history(life) == [("sell_furniture", "failure")]
    rows = frame_retirements(life)
    assert len(rows) == 1
    fid, cap, age, budget, why = rows[0]
    assert (cap, budget, why) == ("sell_furniture", 180, "giveup")
    assert fid == 1 and 0 < age < budget, rows
    assert retirement_tally(life) == "retired=1:1g"
    assert "retired=1:1g" in telemetry_line(life, "carpenter", body.observe())


def _expiring_fetch_life():
    """A fetch_iron frame that finishes empty: the pile vanishes after admission.

    After follow-up 15 remainder, fetch writes `cap_run_finished_goal_id` at
    finish — the production close is bound 1 (giveup). Bound 2's reporter
    still needs a finished-but-ladderless frame, so the helper strips the
    marker once after finish; see
    `test_a_deadline_retirement_is_reported_as_bound_2_not_bound_1`.
    The pile sits past `PICKUP_REACH` so admission walks rather than lifts;
    popping it then cannot accidentally achieve the fetch.
    """
    from anima2.skills.craft import PICKUP_REACH
    from anima2.skills.smelt import INGOT_GRAPHICS

    iron = sorted(INGOT_GRAPHICS)[0]
    body = _body(_item(0x900, sorted(TINKERTOOLS_GRAPHICS)[0]))
    pile = ItemView(
        serial=0x200, graphic=iron, amount=22,
        pos=Position(5 + PICKUP_REACH + 2, 5, 0), container=None, layer=0,
        distance=PICKUP_REACH + 2)
    body.items[pile.serial] = pile
    life = TinkerLife(body=body, persona=Persona(name="Pim"), routes={})
    # Pin the wander: a wide leash walks onto the pile during econ_grace and
    # the admission tick lifts it, so there is nothing left to vanish.
    life.set_leash((5, 5), 0)
    frame = _run_to_admission(life)
    assert frame is not None and frame.goal.params.get("capability") == "fetch_iron"
    body.items.pop(pile.serial)
    return body, life, frame


def test_a_finished_empty_fetch_closes_on_the_ladder_not_the_deadline():
    """Follow-up 15 remainder: fetch's run-finished marker is bound 1."""
    from anima2.life_runner import frame_retirements, retirement_tally

    body, life, frame = _expiring_fetch_life()
    budget = frame.deadline_tick - frame.created_tick
    for _ in range(budget):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            break
    rows = frame_retirements(life)
    assert len(rows) == 1 and rows[0][1] == "fetch_iron"
    assert rows[0][4] == "giveup", rows
    assert rows[0][2] < budget, f"ladder must beat the deadline: {rows}"
    assert retirement_tally(life) == "retired=1:1g"


def test_a_deadline_retirement_is_reported_as_bound_2_not_bound_1():
    """The control that makes the give-up label mean something.

    Every production finish path now writes the ladder (FU15 remainder).
    Bound 2 still exists for frames that can yield without it; the reporter
    keeps that shape by stripping the marker after fetch finishes empty.
    """
    from anima2.life_runner import frame_retirements, retirement_tally

    body, life, frame = _expiring_fetch_life()
    budget = frame.deadline_tick - frame.created_tick
    stripped = False
    for _ in range(budget * 3):
        if (not stripped
                and life.econ_agent.memory.get("cap_fetch_finished_goal_id")
                == frame.id):
            life.econ_agent.memory.pop("cap_run_finished_goal_id", None)
            stripped = True
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            break
    assert stripped, "fetch never finished empty — cannot strip the ladder"
    rows = frame_retirements(life)
    assert len(rows) == 1 and rows[0][1] == "fetch_iron"
    assert rows[0][4] == "expired", rows
    assert rows[0][2] >= budget, f"bound 2 closes AT the deadline, not before: {rows}"
    assert retirement_tally(life) == "retired=1:1x"


def test_the_reason_does_not_depend_on_when_it_is_read():
    """The correction that reshaped this feature, kept as a test.

    An earlier version consulted `cap_run_finished_goal_id` to confirm the ladder. That
    key is a SINGLE memory slot every later transaction overwrites, so it confirms only
    the newest frame to have walked one. Measured on this exact harness: 117 of 117
    FAILURE closes are give-ups when classified at retirement time, and 116 of the 117
    flipped to "no ladder ran" when the same history was re-read at the end — the error
    direction that ERASES bound-1 evidence. The per-tick alarm drains as retirements
    happen; the ~4s status line re-derives from scratch. They must agree — and where
    they CANNOT, past the bounded history's cap, the tally must say so instead of
    printing a saturated count as a total."""
    from anima2.life_runner import frame_retirements, retirement_tally

    body = _body(_item(0x900, SAW), _item(0x901, BOARD, BOARDS_PER_ITEM * 4),
                 _item(GOLD, GOLD_GRAPHIC, 5000))
    life = CarpenterLife(body=body, persona=Persona(name="Sten"),
                         routes={"vendor_spot": ((10, 10),), "banker_spot": ((10, 10),),
                                 "craft_spot": (5, 5)})
    for memory in (life.memory, life.econ_agent.memory):
        memory["craft_spot"] = (5, 5)
    life.set_leash((5, 5), 3)

    cursor, as_they_happen = 0, []
    for _ in range(2000):
        life.tick()
        for row in frame_retirements(life, after_id=cursor):
            cursor = row[0]
            as_they_happen.append(row)
    assert len(as_they_happen) > 100, f"the run must actually retire frames: {len(as_they_happen)}"
    assert all(r[4] == "giveup" for r in as_they_happen), (
        f"{ {r[4] for r in as_they_happen} }")
    assert list(frame_retirements(life)) == as_they_happen, (
        "re-reading the same history later must give the same answer")
    assert retirement_tally(life) == f"retired={len(as_they_happen)}:{len(as_they_happen)}g"

    # AND PAST THE HISTORY CAP, because that is where agreement STOPS being free. This
    # harness retires 117 frames by 2000 ticks and fills the 128-frame history at econ
    # tick 2182 — a review measured 176 retirements at 3000 ticks and 234 at 4000, so
    # "128 is far more than any run has produced" was simply false. From the cap on, the
    # edge reader keeps every frame and the level reader can only see the newest 128:
    # the two no longer agree on the total, and the tally has to SAY so rather than
    # printing a saturated 128 as if it were one.
    for _ in range(2000):
        life.tick()
        for row in frame_retirements(life, after_id=cursor):
            cursor = row[0]
            as_they_happen.append(row)
    history = life.econ_agent.goal_stack.history
    assert len(history) == life.econ_agent.goal_stack.history_limit, (
        f"the cap must actually bind for this to test anything: {len(history)}")
    still_visible = list(frame_retirements(life))
    assert len(as_they_happen) > len(still_visible), (
        f"history must have overflowed: {len(as_they_happen)} vs {len(still_visible)}")
    # What survives is a SUFFIX of what the edge reader banked — same frames, same
    # reasons, in the same order; only the oldest are gone.
    assert as_they_happen[-len(still_visible):] == still_visible
    tally = retirement_tally(life)
    assert tally.startswith(f"retired>={len(still_visible)}:"), tally
    assert str(len(as_they_happen)) not in tally, (
        f"a saturated tally must not be readable as a lifetime total: {tally}")


def test_the_runner_reports_every_retirement_exactly_once(capsys):
    """Printed from `_run_worker`, which runs EVERY tick — the runners' own loops sample
    every ~4s and cannot see an edge. Deliberately unthrottled, unlike FRAME OVERDUE
    beside it: a retirement is one event per transaction, not a state that persists.
    The cursor is the last reported frame ID, so a retirement that lands between two
    samples is still reported, and none is reported twice."""
    import threading

    from anima2.village import _run_worker

    body, life = _sale_life()
    assert _reach_the_sale(body, life)
    _run_worker(life, 120, 0, {}, threading.Lock(), "carpenter")
    out = capsys.readouterr().out
    assert out.count("FRAME RETIRED") == 1, out
    assert "FRAME RETIRED sell_furniture#1" in out, out
    assert "-> giveup (bound 1: the FSM's give-up ladder)" in out, out


def test_a_throttled_life_can_still_reach_the_runner_with_its_retirements():
    """`_ThrottledAgent` has no `__getattr__`, and the throttled carpenter and throttled
    mage are the two Lives that run their economy agent nearly every tick — so they are
    the two that retire the most frames and the two the report would ship DEAD for. Same
    hole as `rule_gate_disagreement`/`frame_overdue`, one commit earlier."""
    from anima2.life_runner import frame_retirements
    from anima2.village import _ThrottledAgent

    body, life = _sale_life()
    assert _reach_the_sale(body, life)
    for _ in range(80):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            break
    throttled = _ThrottledAgent(life, every=3)
    throttled.yield_pause_s = 0
    assert throttled.econ_agent is life.econ_agent
    assert frame_retirements(throttled) == frame_retirements(life) != ()
    # A plain Agent underneath has no economy agent. `frame_retirements` then falls back
    # to the object it was handed — which is this proxy, and the proxy deliberately
    # exposes no `goal_stack`, so the read fails closed to "nothing to report" rather
    # than raising. That is the right answer HERE for a second reason: a hunt agent's
    # planner is not capability-driven, so it owns no capability frames to report. The
    # fallback exists for the UNWRAPPED plain capability Agent `run_village
    # --capability-goals` builds (`tests/test_capabilities.py::
    # test_a_plain_capability_agent_reports_its_own_retirements`); no production site
    # wraps one of those in a `_ThrottledAgent`.
    assert _ThrottledAgent(life.hunt_agent, every=3).econ_agent is None
    assert frame_retirements(_ThrottledAgent(life.hunt_agent, every=3)) == ()
    assert not hasattr(_ThrottledAgent(life.hunt_agent, every=3), "goal_stack")
    # The unwrapped hunt agent DOES expose one, and it is empty of capability frames —
    # so the fallback is safe on it too, by content and not only by the proxy's shape.
    assert frame_retirements(life.hunt_agent) == ()


def test_the_tally_rides_the_status_line_because_the_alarm_scrolls_away():
    """The alarm is an edge and scrolls between status blocks; the tally is the level
    signal an operator joining late, or grepping the log afterwards, still sees."""
    from anima2.life_runner import retirement_tally

    body, life = _sale_life()
    assert retirement_tally(life) == "retired=0"
    assert "retired=0" in telemetry_line(life, "carpenter", body.observe())
    assert _reach_the_sale(body, life)
    for _ in range(80):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            break
    assert "retired=1:1g" in telemetry_line(life, "carpenter", body.observe())


def test_a_given_up_buy_frame_retires_through_bound_1_instead_of_its_deadline():
    """Audit follow-up 19, and the reason it stopped being a guess.

    `market.py` has written the neutral `cap_run_finished_goal_id` on the SELL (:1681)
    and BANK (:2072) give-up paths since forge1/forge13, and on NEITHER buy path. So a
    buy trip that gave up and walked home left a frame nothing could close except
    `expire_due` — bound 2 doing bound 1's job at ~40x the cost.

    MEASURED live before the fix, which is why the deferral ("a behaviour change nobody
    has measured") no longer applies: a 1200-tick forge run retired THREE `buy_iron`
    frames at exactly `180/180`, with 55 samples showing the frame admitted, the gate
    ready, `mkt_phase=craft` and no hold/frozen/overdue marker — 540 dead economy ticks,
    45% of the run, on the one positive-margin chain this project has (audit §17.4).

    This is the same `_wedged_buy_life` world that used to prove bound 2. It proves
    bound 1 now, and the age is the whole point."""
    from anima2.life_runner import frame_retirements

    body, life, frame = _wedged_buy_life()
    budget = frame.deadline_tick - frame.created_tick
    for _ in range(budget * 3):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            break
    rows = frame_retirements(life)
    assert len(rows) == 1 and rows[0][1] == "buy_boards", rows
    assert rows[0][4] == "giveup", f"the give-up ladder must close it, not the clock: {rows}"
    assert rows[0][2] < budget // 2, (
        f"it must close EARLY — the whole point is not paying the {budget}-tick "
        f"deadline for a decision the FSM already made: {rows}")


def test_the_walk_readout_renders_the_age_8_giveup_signature():
    """Follow-up 32, against the day it was written for — and the first OFFLINE
    reproduction of follow-up 29's signature.

    §30.2: 203 `sell_tongs` frames in one live day, **every one retired at age 8**,
    `sell_stage` never written once, 0 gold banked. §31 relocated the failure into the
    walk, and §31.4 named the remaining hypothesis it could not test: *"a blocked approach
    the mock cannot model (a real NPC occupying a tile, or terrain)"*. `MockBody.blocked` is
    that, and the trip dies before the vendor is ever addressed — so reproducing it needs no
    vendor at all, which is why this was reachable while follow-up 22's `MockVendor` work
    was not.

    Driving a real `CarpenterLife` into a walled approach reproduces the live arithmetic
    exactly: age 8, budget 180, `giveup`, over and over. What the OLD status line could say
    about that is `admitted=sell_furniture@5/180` — admitted, ready, unfrozen, well inside
    budget, i.e. healthy. What it says now is that he is five tiles from a vendor he must be
    two from, has not moved, and is five ticks into a six-tick give-up.

    This does NOT establish that the live day's cause was a blocked tile — it establishes
    that a blocked approach PRODUCES that signature, and that the line now renders it. The
    live attribution still needs a forge day (follow-up 30).
    """
    body, life = _sale_life()
    # A wall between the carpenter at (5,5) and the vendor at (10,10): every step that
    # would close the gap bumps. `_walk` treats a blocked tile as a turn, not a move.
    body.blocked.update({(x, 6) for x in range(20)} | {(6, y) for y in range(20)})

    # Read through the SAME helper `village._run_worker` uses, econ-agent resolution and
    # all — not by reaching into memory directly, which would prove the readout works on
    # state no production caller assembles that way.
    walking = []
    for _ in range(60):
        life.tick()
        line = agent_walk_readout(life, body.observe().player.pos)
        if line.startswith("trip=sell "):
            walking.append(line)

    # The wedge, on the line: the distance never closes and the give-up counter climbs.
    assert walking, "the sell trip never started"
    assert all("to=(10,10) d=5>2" in ln for ln in walking), walking[:3]
    assert [ln for ln in walking if "stall=5/6" in ln], walking
    assert (5, 5) == (body.player.pos.x, body.player.pos.y), "the wall did not hold"

    # ...and the frame retirements are the live day's arithmetic, verbatim.
    rets = frame_retirements(life)
    assert len(rets) >= 3, rets
    assert {(cap, age, budget, why) for _id, cap, age, budget, why in rets} == {
        ("sell_furniture", 8, 180, "giveup")}, rets
