"""WoodsmanLife.decide_mode — a lumberjack's own chop <-> haul-and-sell switch.

The third life on the live-verified `WarriorLife` machinery, and the first for an agent
that does not fight for a living. A woodsman stops working for reasons neither of the
other two had: its TOOL breaks mid-swing, and its value has to be carried along a longer
chain (tree -> log -> board -> gold) before any of it is gold at all.
"""

from anima2.contract import ItemView, Observation, PlayerView, Position
from anima2.persona import Persona
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.hunt import GOLD_GRAPHIC
from anima2.skills.woodwork import BOARD_GRAPHIC, LOG_GRAPHIC
from anima2.woodsman_life import (
    BANK_ABOVE,
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


def _obs(items, *, dead=False):
    return Observation(player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0), dead=dead),
                       items=list(items))


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
    rich = _obs([_backpack(), _axe(), _item(0x902, GOLD_GRAPHIC, BANK_ABOVE + 1)])
    assert decide_mode(rich, dict(ROUTES)) == ("economy", "bank_gold")
    # ...and an unfinished chain still wins over banking.
    busy = _obs([_backpack(), _axe(), _item(0x902, GOLD_GRAPHIC, BANK_ABOVE + 1),
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
