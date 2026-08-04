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

import pytest

from anima2.carpenter_life import BOARD_BATCH_COST, BOARDS_PER_ITEM, CarpenterLife
from anima2.contract import GumpView, ItemView, PlayerView, Position
from anima2.life_runner import telemetry_line
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


def test_a_held_frame_is_bounded_by_its_own_deadline():
    """Bound 2, the backstop: a frame that never gives up is EXPIRED by
    `GoalStack.expire_due` at `default_deadline_ticks` — reachable at all only because
    the mode is held, since `expire_due` runs solely inside `Agent.tick` and is compared
    against that agent's own counter. Frozen, the deadline is unreachable by
    construction: 180 econ ticks never arrive when the econ agent stops at 5."""
    body = _body(_item(0x900, SAW), _item(GOLD, GOLD_GRAPHIC, BOARD_BATCH_COST + 5))
    life = CarpenterLife(body=body, persona=Persona(name="Sten"),
                         routes={"vendor_spot": ((10, 10),), "banker_spot": ((10, 10),)})
    life.set_leash((5, 5), 3)
    frame = _run_to_admission(life)
    assert frame is not None and frame.goal.params.get("capability") == "buy_boards"
    budget = frame.deadline_tick - frame.created_tick

    body.items.pop(GOLD)  # the coin moves: the rule wants nothing at all now
    for _ in range(budget * 3):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            break
    assert _history(life) == [("buy_boards", "expired")], (
        "the deadline is the hold's backstop and it must actually be reached")
    assert life.econ_agent.ticks <= budget + 2, (
        f"the frame outlived its {budget}-tick budget in the clock that budget is "
        f"counted in ({life.econ_agent.ticks} economy ticks)")


def _wedged_buy_life():
    """A carpenter with a live `buy_boards` frame, an unowned gump, and no coin.

    This is the world that made bounds 1 and 2 fail TOGETHER, and it is built out of
    nothing but the two halves this file already used separately: the self-falsification
    of `test_every_life_finishes_a_transaction_its_own_rule_stopped_wanting`
    (`body.items.pop(GOLD)`) and the wedge of
    `test_the_hold_does_not_delay_the_disagreement_detector` (a `GumpView` nobody owns).

    Why both bounds die at once: every `*_can_yield` in `capabilities.py` carries an
    unconditional "idle UI" clause, so one open gump makes `deadline_can_expire` False
    forever (bound 2) and makes every readiness gate refuse (bound 1). And a BUY frame
    has no give-up ladder at all — only `SellItemCapability` and `BankGoldCapability`
    ever write `cap_run_finished_goal_id`, the marker `CapabilityGoalComplete` needs to
    close an unachieved run.
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


def test_an_overdue_frame_is_reached_by_a_plain_world_and_never_pins_the_life():
    """The hold's THIRD bound, and the reason it has to exist.

    No deadline is touched here: the frame reaches its own budget on the economy clock
    the hold itself keeps advancing. Without this bound the measured outcome was a total
    livelock — mode pinned to "economy" for 3000 ticks on four of the five Lives, the
    work agent never ticked again, and the Life emitting nothing at all — which is
    strictly worse than the zombie frame the hold was written to fix.
    """
    body, life, frame = _wedged_buy_life()
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
    repair should be allowed to touch — so the wedge RESOLVES instead of being reported
    forever: the gump closes, `deadline_can_expire` comes back, and the frame expires on
    the economy agent's very next tick, leaving a clean stack.
    """
    body, life, frame = _wedged_buy_life()
    budget = frame.deadline_tick - frame.created_tick
    for _ in range(budget * 3):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            break
    assert not body.gumps, "the surface that blocked every yield was never closed"
    assert getattr(life, "_stale_ui_closes", 0) == 1, "and closed exactly once"
    assert _history(life) == [("buy_boards", "expired")], (
        "with the surface gone the frame must actually retire, not sit stale forever")
    assert life.frame_overdue is False and life.holding_frame is False


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
    # `ready=[] ` with the following field's name attached, so this still pins an EMPTY
    # gate list and cannot be satisfied by `ready=[]...` being a prefix of a longer one.
    # (It used to be `endswith("ready=[]")`; `retired=` now rides after it — see
    # `life_runner.retirement_reason` for why the line grew a retirement tally.)
    assert "admitted=sell_furniture@" in held and "ready=[] retired=" in held
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
    body, life, frame = _wedged_buy_life()
    budget = frame.deadline_tick - frame.created_tick
    line = telemetry_line(life, "carpenter", body.observe())
    assert "!overdue" not in line, f"a frame inside its budget is not overdue: {line}"

    for _ in range(budget + 2):
        life.tick()
        if life.frame_overdue:
            break
    assert life.frame_overdue is True
    line = telemetry_line(life, "carpenter", body.observe())
    assert "admitted=buy_boards@" in line and "!overdue" in line, line


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


def test_a_deadline_retirement_is_reported_as_bound_2_not_bound_1():
    """The control that makes the give-up label mean something: the same reporter over
    `_wedged_buy_life`, where a BUY frame has no give-up ladder at all and only
    `expire_due` can close it."""
    from anima2.life_runner import frame_retirements, retirement_tally

    body, life, frame = _wedged_buy_life()
    budget = frame.deadline_tick - frame.created_tick
    for _ in range(budget * 3):
        life.tick()
        if life.econ_agent.goal_stack.current is None:
            break
    rows = frame_retirements(life)
    assert len(rows) == 1 and rows[0][1] == "buy_boards"
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
