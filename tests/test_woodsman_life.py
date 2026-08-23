"""WoodsmanLife.decide_mode — a lumberjack's own chop <-> haul-and-sell switch.

The third life on the live-verified `WarriorLife` machinery, and the first for an agent
that does not fight for a living. A woodsman stops working for reasons neither of the
other two had: its TOOL breaks mid-swing, and its value has to be carried along a longer
chain (tree -> log -> board -> gold) before any of it is gold at all.
"""

from anima2.contract import ItemView, Observation, PlayerView, Position, TargetCursor
from anima2.persona import Persona
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.hunt import GOLD_GRAPHIC
from anima2.skills.woodwork import BOARD_GRAPHIC, LOG_GRAPHIC
from anima2.woodsman_life import (
    BANK_RESERVE,
    HATCHET_COST,
    SELL_BOARDS_AT,
    WoodsmanLife,
    decide_mode,
)

PLAYER = 1
BP = 0x50
HATCHET = 0x0F43
ROUTES = {"vendor_spot": ((10, 10),), "tool_vendor_spot": ((10, 10),),
          "banker_spot": ((10, 10),)}


def _item(serial, graphic, amount=1, *, container=BP, layer=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=0)


def _backpack():
    return _item(BP, 0x0E75, container=PLAYER, layer=BACKPACK_LAYER)


def _axe():
    return _item(0x900, HATCHET)


def _obs(items, *, dead=False, pending_target=None):
    return Observation(player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0), dead=dead),
                       items=list(items), pending_target=pending_target)


def test_a_tooled_woodsman_with_nothing_to_carry_chops():
    assert decide_mode(_obs([_backpack(), _axe()]), dict(ROUTES)) == ("hunt", None)


def test_a_broken_axe_outranks_everything_else():
    # A woodsman with no tool is not a woodsman — this is its equivalent of the
    # warrior losing its blade, and it wins even with a full pack and a full purse.
    obs = _obs([_backpack(), _item(0x901, BOARD_GRAPHIC, SELL_BOARDS_AT),
                _item(0x902, GOLD_GRAPHIC, 1000)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "buy_hatchet")


def test_an_axe_on_the_ground_is_preferred_to_buying_one():
    # A dropped axe is free; a bought one is 25 gold. Also the shape a tinker's
    # `deliver_hatchet` produces — the woodsman picks up what was left for it.
    obs = _obs([_backpack(), _item(0x903, HATCHET, container=None),
                _item(0x902, GOLD_GRAPHIC, 1000)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "fetch_hatchet")


def test_a_broke_or_unrouted_woodsman_keeps_working_instead_of_stalling():
    poor = _obs([_backpack(), _item(0x902, GOLD_GRAPHIC, HATCHET_COST - 1)])
    assert decide_mode(poor, dict(ROUTES)) == ("hunt", None)
    unrouted = _obs([_backpack(), _item(0x902, GOLD_GRAPHIC, 1000)])
    assert decide_mode(unrouted, {}) == ("hunt", None)


def test_selling_outranks_processing_so_the_chain_actually_finishes():
    # The ordering lesson the artisan's chain had to learn live: an agent that always
    # takes the first ready step keeps making more of what it already has. Holding both
    # logs AND a sellable stack of boards, the boards go to market first.
    obs = _obs([_backpack(), _axe(),
                _item(0x901, BOARD_GRAPHIC, SELL_BOARDS_AT),
                _item(0x904, LOG_GRAPHIC, 10)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "sell_boards")


def test_logs_are_processed_once_there_is_nothing_worth_selling():
    obs = _obs([_backpack(), _axe(),
                _item(0x901, BOARD_GRAPHIC, SELL_BOARDS_AT - 1),
                _item(0x904, LOG_GRAPHIC, 10)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "process_logs")


def test_boards_below_the_threshold_are_carried_not_hauled():
    obs = _obs([_backpack(), _axe(), _item(0x901, BOARD_GRAPHIC, SELL_BOARDS_AT - 1)])
    assert decide_mode(obs, dict(ROUTES)) == ("hunt", None)


def test_surplus_gold_banks_but_only_when_nothing_is_needed():
    rich = _obs([_backpack(), _axe(), _item(0x902, GOLD_GRAPHIC, BANK_RESERVE + 1)])
    assert decide_mode(rich, dict(ROUTES)) == ("economy", "bank_gold")
    # ...and an unfinished chain still wins over banking.
    busy = _obs([_backpack(), _axe(), _item(0x902, GOLD_GRAPHIC, BANK_RESERVE + 1),
                 _item(0x904, LOG_GRAPHIC, 4)])
    assert decide_mode(busy, dict(ROUTES)) == ("economy", "process_logs")


def test_a_dead_woodsman_yields_to_recover_death():
    obs = _obs([_backpack()], dead=True)
    assert decide_mode(obs, dict(ROUTES)) == ("hunt", None)


def test_a_worn_axe_counts_as_owned():
    # Chop works from an axe held or worn, not only one loose in the pack.
    obs = _obs([_backpack(), _item(0x905, HATCHET, container=PLAYER, layer=1)])
    assert decide_mode(obs, dict(ROUTES)) == ("hunt", None)


class _MockBody:
    connected = True
    ready = {"player": {"serial": PLAYER}}

    def __init__(self, seq):
        self._it = iter(seq)
        self._last = None

    def observe(self):
        try:
            self._last = next(self._it)
        except StopIteration:
            pass
        return self._last

    def act(self, action):
        pass


def test_woodsman_life_reuses_the_orchestrator_with_its_own_rule():
    from anima2.warrior_life import WarriorLife

    assert issubclass(WoodsmanLife, WarriorLife)
    life = WoodsmanLife(body=_MockBody([_obs([_backpack(), _axe()])]),
                        persona=Persona(name="Bjorn"), routes=dict(ROUTES))
    assert set(life.econ_agent.planner.capability_ids) == {
        "process_logs", "sell_boards", "bank_gold", "fetch_hatchet", "buy_hatchet",
        "deliver_boards",
    }
    # The work side is the lumberjack's own trade.
    assert "Chop" in [type(s).__name__ for s in life.hunt_agent.planner.skills]
    # Separate memories (the warrior's live-caught requirement) still hold.
    assert life.hunt_agent.memory is not life.econ_agent.memory
    # ...and the work agent is reachable under a name that fits a non-combat life.
    assert life.work_agent is life.hunt_agent


def test_the_switch_keeps_the_inherited_hysteresis():
    from anima2.warrior_life import ECON_GRACE

    needy = _obs([_backpack(), _axe(), _item(0x901, BOARD_GRAPHIC, SELL_BOARDS_AT)])
    life = WoodsmanLife(body=_MockBody([needy] * (ECON_GRACE + 2)),
                        persona=Persona(name="Bjorn"), routes=dict(ROUTES))
    for _ in range(ECON_GRACE - 1):
        life.tick()
        assert life.mode == "hunt"
    life.tick()
    assert life.mode == "economy" and life.target_cap == "sell_boards"


def test_a_chop_leftover_cursor_does_not_block_process_logs_admission():
    # woodsman-20260818-1926: Chop left pending_target, 20 logs, axe yes,
    # want=process_logs admitted=None ready=[] for the rest of a 600-tick day.
    # The skill already answers that cursor; admission was the odd one out.
    from anima2.warrior_life import ECON_GRACE

    cursor = TargetCursor(target_type=1, cursor_id=7, cursor_flag=0)
    obs = _obs([_backpack(), _axe(), _item(0x904, LOG_GRAPHIC, 20)],
               pending_target=cursor)
    life = WoodsmanLife(body=_MockBody([obs] * (ECON_GRACE + 8)),
                        persona=Persona(name="Bjorn"), routes=dict(ROUTES))
    admitted = None
    for _ in range(ECON_GRACE + 6):
        life.tick()
        cur = life.econ_agent.goal_stack.current
        if cur is not None:
            admitted = cur.goal.params.get("capability")
            break
    assert life.mode == "economy" and life.target_cap == "process_logs"
    assert admitted == "process_logs"


# --- the reserve is one number, shared ----------------------------------------------
#
# Having a THRESHOLD is not the same as keeping a RESERVE, and the two were conflated:
# `BANK_ABOVE` said "keep enough for several axes" while nothing told `BankGold` to keep
# anything, so its own completion rule (`final_pack_gold == reserve`, reserve defaulting
# to 0) would have banked every coin and walked the woodsman back to the grove unable to
# replace a broken axe — exactly what the constant claims to prevent.

def test_the_rule_never_wants_a_deposit_the_gate_would_refuse():
    # The gate is ready at `gold > reserve`; a rule firing at `>=` would ask for a goal
    # admission must refuse, which is the stall shape this project keeps paying for.
    from anima2.woodsman_life import BANK_RESERVE

    at_reserve = _obs([_backpack(), _axe(), _item(0x902, GOLD_GRAPHIC, BANK_RESERVE)])
    assert decide_mode(at_reserve, dict(ROUTES)) == ("hunt", None)
    above = _obs([_backpack(), _axe(), _item(0x902, GOLD_GRAPHIC, BANK_RESERVE + 1)])
    assert decide_mode(above, dict(ROUTES)) == ("economy", "bank_gold")


def test_the_skill_is_told_the_same_reserve_the_rule_assumes():
    from anima2.woodsman_life import BANK_RESERVE

    life = WoodsmanLife(body=_MockBody([_obs([_backpack(), _axe()])]),
                        persona=Persona(name="Bjorn"), routes=dict(ROUTES))
    # `bank_reserve` is the key BankGold's FSM and the capability gate already share.
    assert life.econ_agent.memory["bank_reserve"] == BANK_RESERVE
    assert BANK_RESERVE > 0, "a zero reserve banks the axe money too"


def test_the_reserve_covers_replacing_the_axe_several_times():
    # The reserve exists for one reason: this profession must never be caught unable to
    # replace its tool. Pin it to that, not to a round number.
    from anima2.woodsman_life import BANK_RESERVE, BANK_RESERVE_AXES

    assert BANK_RESERVE == HATCHET_COST * BANK_RESERVE_AXES
    assert BANK_RESERVE >= HATCHET_COST * 2


# --- supplying a partner ------------------------------------------------------------
#
# Configuring `carpenter_drop` IS the statement that this woodsman supplies somebody.
# It costs gold to do so, measurably: the vendor buys boards at 2g each, and every
# carpentry recipe on this shard sells for less than its own boards (the best, a Club,
# is 9 boards for 13g against 18g raw). So it must stay an explicit choice, never a
# default — an unpartnered woodsman has to behave exactly as it did before.

def test_a_configured_drop_makes_the_partner_outrank_the_shop():
    from anima2.woodsman_life import DELIVER_BOARDS_AT

    routes = {**ROUTES, "carpenter_drop": ((12, 12),)}
    obs = _obs([_backpack(), _axe(),
                _item(0x901, BOARD_GRAPHIC, max(DELIVER_BOARDS_AT, SELL_BOARDS_AT))])
    assert decide_mode(obs, routes) == ("economy", "deliver_boards")


def test_without_a_drop_nothing_changes():
    # The no-op guarantee for every woodsman that has no partner.
    obs = _obs([_backpack(), _axe(), _item(0x901, BOARD_GRAPHIC, SELL_BOARDS_AT)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "sell_boards")


def test_a_short_stack_is_not_hauled_to_the_partner():
    from anima2.woodsman_life import DELIVER_BOARDS_AT

    routes = {**ROUTES, "carpenter_drop": ((12, 12),)}
    obs = _obs([_backpack(), _axe(), _item(0x901, BOARD_GRAPHIC, DELIVER_BOARDS_AT - 1)])
    assert decide_mode(obs, routes) != ("economy", "deliver_boards")


def test_a_missing_axe_still_outranks_supplying_anyone():
    from anima2.woodsman_life import DELIVER_BOARDS_AT

    routes = {**ROUTES, "carpenter_drop": ((12, 12),)}
    obs = _obs([_backpack(), _item(0x901, BOARD_GRAPHIC, DELIVER_BOARDS_AT),
                _item(0x902, GOLD_GRAPHIC, 1000)])
    assert decide_mode(obs, routes) == ("economy", "buy_hatchet")


# --- the leftover quantum -----------------------------------------------------------
#
# `SELL_BOARDS_AT` used to be 20 with the reason "a full sell batch of boards (free
# input, so a generous threshold is fine)" — a round number, the same shape as the
# `BANK_ABOVE = 300` mistake this file already records. It is off-grid by exactly one
# quantum: `LOGS_PER_HARVEST` (ServUO `ConsumedPerHarvest`) is 10, boards are 1:1 with
# logs, and the LAST harvest action of every 20..45-log bank yields exactly 10 — so a
# grove going dry always hands over a 10-stack, and a threshold of 20 strands it.
#
# Measured: `.logs/woodsman-20260818-1941` and `-20260822-1225` both ended
# `logs=0 boards=10` and never sold, holding that stack for 51/67 and 47/69 status
# samples respectively.

def test_the_last_chop_of_a_dry_grove_is_still_worth_a_trip():
    from anima2.skills.woodwork import LOGS_PER_HARVEST

    obs = _obs([_backpack(), _axe(), _item(0x901, BOARD_GRAPHIC, LOGS_PER_HARVEST)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "sell_boards")


def test_a_leftover_quantum_goes_to_the_shop_even_when_a_partner_is_configured():
    # The interaction the threshold change buys, pinned rather than left implicit:
    # `deliver_boards` outranks selling but needs 19, so a 10-stack that a partnered
    # woodsman used to CARRY into the next delivery is now sold to the vendor instead.
    # Worked example on a 60-log bank (chops of 20, 20, 10, 10): 60 boards delivered
    # before, 40 delivered + 20 sold after.
    from anima2.skills.woodwork import LOGS_PER_HARVEST

    routes = {**ROUTES, "carpenter_drop": ((12, 12),)}
    obs = _obs([_backpack(), _axe(), _item(0x901, BOARD_GRAPHIC, LOGS_PER_HARVEST)])
    assert decide_mode(obs, routes) == ("economy", "sell_boards")


def test_admission_moves_with_the_threshold_rather_than_with_a_literal():
    """Rule and gate read ONE number — driven at the edge, not restated as `20`.

    The gate is `capabilities._make_sell_ready(..., SellBoards.sell_threshold, ...)`,
    built at import from the same attribute `SELL_BOARDS_AT` aliases. Asserting the
    literal would pass a rule-side-only threshold change straight through (the exact
    probe `test_life_gate_concordance` documents), so this drives
    `ready_capability_ids` at T-1 and T and asks both sides the same question.
    """
    from anima2.capabilities import ready_capability_ids
    from anima2.skills.base import SkillContext

    def ask(boards):
        obs = _obs([_backpack(), _axe(), _item(0x901, BOARD_GRAPHIC, boards)])
        ctx = SkillContext(obs=obs, persona=Persona(name="Bjorn"), memory=dict(ROUTES))
        return decide_mode(obs, dict(ROUTES)), ready_capability_ids("lumberjack", ctx)

    (below_mode, below_ready) = ask(SELL_BOARDS_AT - 1)
    assert below_mode != ("economy", "sell_boards")
    assert "sell_boards" not in below_ready

    (at_mode, at_ready) = ask(SELL_BOARDS_AT)
    assert at_mode == ("economy", "sell_boards")
    assert "sell_boards" in at_ready
