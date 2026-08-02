"""CarpenterLife.decide_mode — a carpenter's own craft <-> restock <-> sell switch.

The fourth life on the live-verified `WarriorLife` machinery, and the first for a
profession with NO work skill: every action a carpenter takes is a goal-scoped
capability, so there is no free fallback to return to. It is also the first whose
blocker is somebody else — it cannot make its own material.
"""

from anima2.carpenter_life import (
    BANK_RESERVE,
    BOARD_BATCH_COST,
    BOARDS_PER_ITEM,
    SAW_COST,
    SELL_FURNITURE_AT,
    CarpenterLife,
    decide_mode,
)
from anima2.contract import ItemView, Observation, PlayerView, Position
from anima2.persona import Persona
from anima2.skills.carpentry import BuySaw, FetchBoards, SellFurniture
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.hunt import GOLD_GRAPHIC

PLAYER = 1
BP = 0x50
SAW = sorted(BuySaw.owned_tool_graphics)[0]
BOARD = sorted(FetchBoards.fetched_graphics)[0]
FURNITURE = SellFurniture.sold_graphic
#: The REAL wiring, not a reduced one: both production construction sites write
#: `craft_spot` into the economy agent's memory before the first tick
#: (`village.run_supply_pair` and `run_carpenter_life`'s `Staged.econ_memory`), and the
#: craft gate reads it. `_obs` stands the player at (5, 5), so this is "on the stand" —
#: without the key the fixture was testing a configuration that never runs, and the rule
#: wanted a craft the gate refuses. `tests/test_life_gate_concordance.py` and
#: `tests/test_tinkering.py` already carried the key for exactly this reason.
ROUTES = {"vendor_spot": ((10, 10),), "banker_spot": ((10, 10),),
          "craft_spot": (5, 5)}


def _item(serial, graphic, amount=1, *, container=BP, layer=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=0)


def _backpack():
    return _item(BP, 0x0E75, container=PLAYER, layer=BACKPACK_LAYER)


def _saw():
    return _item(0x900, SAW)


def _obs(items, *, dead=False):
    return Observation(player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0), dead=dead),
                       items=list(items))


def test_a_stocked_carpenter_crafts():
    obs = _obs([_backpack(), _saw(), _item(0x901, BOARD, BOARDS_PER_ITEM)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "craft_carpentry")


def test_crafting_is_an_economy_answer_because_there_is_no_work_skill():
    # The structural point: unlike the other three lives, "work" here IS a capability.
    from anima2.profession import PROFESSIONS

    assert PROFESSIONS["carpenter"].work_skill is None
    obs = _obs([_backpack(), _saw(), _item(0x901, BOARD, BOARDS_PER_ITEM)])
    mode, cap = decide_mode(obs, dict(ROUTES))
    assert mode == "economy" and cap is not None


def test_a_lost_saw_outranks_everything():
    obs = _obs([_backpack(), _item(0x901, BOARD, 100), _item(0x902, GOLD_GRAPHIC, 9999),
                _item(0x903, FURNITURE, 3)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "buy_saw")


def test_a_saw_on_the_ground_beats_buying_one():
    obs = _obs([_backpack(), _item(0x904, SAW, container=None),
                _item(0x902, GOLD_GRAPHIC, 9999)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "fetch_saw")


def test_selling_finished_furniture_outranks_restocking():
    # Finish the chain before extending it — the ordering lesson from the artisan.
    obs = _obs([_backpack(), _saw(), _item(0x903, FURNITURE, SELL_FURNITURE_AT),
                _item(0x902, GOLD_GRAPHIC, 9999)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "sell_furniture")


def test_delivered_boards_are_preferred_to_bought_ones():
    # A carpenter cannot make its own material. Boards a lumberjack hauled over are
    # free; the vendor's are not — so the ground wins even with money in hand.
    obs = _obs([_backpack(), _saw(), _item(0x905, BOARD, 40, container=None),
                _item(0x902, GOLD_GRAPHIC, 9999)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "fetch_boards")


def test_boards_are_bought_when_none_have_been_delivered():
    obs = _obs([_backpack(), _saw(), _item(0x902, GOLD_GRAPHIC, BOARD_BATCH_COST)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "buy_boards")


def test_a_short_stack_of_boards_still_counts_as_no_material():
    # One board short of a recipe is not enough to craft anything.
    obs = _obs([_backpack(), _saw(), _item(0x901, BOARD, BOARDS_PER_ITEM - 1),
                _item(0x902, GOLD_GRAPHIC, BOARD_BATCH_COST)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "buy_boards")


def test_a_broke_carpenter_with_no_material_waits_rather_than_stalling():
    # Its supplier may yet turn up. What it must NOT do is stand at a shop it cannot
    # pay for — the same rule the other three lives keep.
    obs = _obs([_backpack(), _saw(), _item(0x902, GOLD_GRAPHIC, BOARD_BATCH_COST - 1)])
    assert decide_mode(obs, dict(ROUTES)) == ("hunt", None)
    unrouted = _obs([_backpack(), _saw(), _item(0x902, GOLD_GRAPHIC, 9999)])
    assert decide_mode(unrouted, {}) == ("hunt", None)


def test_surplus_gold_banks_only_above_the_reserve():
    stocked = [_backpack(), _saw(), _item(0x901, BOARD, BOARDS_PER_ITEM)]
    at_reserve = _obs([*stocked, _item(0x902, GOLD_GRAPHIC, BANK_RESERVE)])
    assert decide_mode(at_reserve, dict(ROUTES)) == ("economy", "craft_carpentry")
    above = _obs([*stocked, _item(0x902, GOLD_GRAPHIC, BANK_RESERVE + 1)])
    assert decide_mode(above, dict(ROUTES)) == ("economy", "bank_gold")


def test_the_reserve_covers_the_two_things_it_cannot_trade_without():
    assert BANK_RESERVE == BOARD_BATCH_COST + SAW_COST
    assert BANK_RESERVE > SAW_COST


def test_a_dead_carpenter_yields_to_recover_death():
    assert decide_mode(_obs([_backpack(), _saw()], dead=True), dict(ROUTES)) == ("hunt", None)


def test_a_worn_saw_counts_as_owned():
    obs = _obs([_backpack(), _item(0x906, SAW, container=PLAYER, layer=1),
                _item(0x901, BOARD, BOARDS_PER_ITEM)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "craft_carpentry")


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


def test_carpenter_life_reuses_the_orchestrator_and_shares_its_reserve():
    from anima2.warrior_life import WarriorLife

    assert issubclass(CarpenterLife, WarriorLife)
    life = CarpenterLife(body=_MockBody([_obs([_backpack(), _saw()])]),
                         persona=Persona(name="Sten"), routes=dict(ROUTES))
    assert set(life.econ_agent.planner.capability_ids) == {
        "craft_carpentry", "sell_furniture", "bank_gold", "fetch_boards", "buy_boards",
        "fetch_saw", "buy_saw",
    }
    # The rule, the gate and BankGold's FSM must all keep the same amount back.
    assert life.econ_agent.memory["bank_reserve"] == BANK_RESERVE
    assert life.hunt_agent.memory is not life.econ_agent.memory


def test_wanting_a_pickup_it_cannot_reach_is_refused():
    # The fetch gate requires a ground item within PICKUP_RADIUS. A rule that ignored
    # distance would want `fetch_boards` for boards it can merely SEE — a goal the gate
    # must refuse, which is the stall shape this project keeps paying for. Live-caught:
    # a carpenter nine tiles off its drop was never once admitted a fetch in 1200 ticks.
    from anima2.skills.craft import PICKUP_RADIUS

    def _far_boards(distance):
        return ItemView(serial=0x905, graphic=BOARD, amount=40, pos=Position(),
                        container=None, layer=0, distance=distance)

    near = _obs([_backpack(), _saw(), _far_boards(PICKUP_RADIUS)])
    assert decide_mode(near, dict(ROUTES)) == ("economy", "fetch_boards")
    far = _obs([_backpack(), _saw(), _far_boards(PICKUP_RADIUS + 1),
                _item(0x902, GOLD_GRAPHIC, BOARD_BATCH_COST)])
    assert decide_mode(far, dict(ROUTES)) == ("economy", "buy_boards")


# --- the craft branch: the one place this rule used to break its own invariant -------
#
# Found by a differential probe over 30,000 randomized carpenter states — the ONLY
# forward-concordance violation across all five Lives (150,000 states), 308 of them. The
# terminal `return "economy", "craft_carpentry"` was unguarded while the gate
# (`capabilities._make_craft_ready`) applies TWO more clauses, so the rule could want a
# craft admission refuses. That is not a missed craft, it is a PERMANENT want-but-never-
# ready: with boards already in the pack nothing below fires, and once
# `disagreement_ticks` elapses `warrior_life._clear_stale_ui` closes a UI surface EVERY
# tick on a perfectly healthy agent (16 unowned-vendor-window closes in 30 ticks,
# measured). Both tests below fail without the guard.


def _stocked():
    return [_backpack(), _saw(), _item(0x901, BOARD, BOARDS_PER_ITEM)]


def _gate_refuses(obs, memory) -> bool:
    from anima2.capabilities import ready_capability_ids
    from anima2.skills.base import SkillContext

    ctx = SkillContext(obs=obs, persona=Persona(name="T"), memory=dict(memory))
    return "craft_carpentry" not in ready_capability_ids("carpenter", ctx)


def test_a_carpenter_off_its_stand_waits_instead_of_wanting_a_refused_craft():
    # The live-shaped half: `FetchBoards` walks up to PICKUP_RADIUS (6) to a delivered
    # pile — TWICE the craft radius — and has no walk-home leg, so a carpenter can end a
    # fetch standing outside the gate's own drift tolerance with a full pack of boards.
    from anima2.carpenter_life import _CRAFT_RADIUS

    obs = _obs(_stocked())  # the player stands at (5, 5)
    on_stand = {**ROUTES, "craft_spot": (5, 5)}
    assert decide_mode(obs, on_stand) == ("economy", "craft_carpentry")
    edge = {**ROUTES, "craft_spot": (5 + _CRAFT_RADIUS, 5 + _CRAFT_RADIUS)}
    assert decide_mode(obs, edge) == ("economy", "craft_carpentry")
    wandered = {**ROUTES, "craft_spot": (5 + _CRAFT_RADIUS + 1, 5)}
    assert _gate_refuses(obs, wandered)
    assert decide_mode(obs, wandered) == ("hunt", None)
    # ...and the same for a spot that was never wired, or wired malformed.
    for spot in ({}, {"craft_spot": None}, {"craft_spot": (5, "5")}):
        memory = {k: v for k, v in ROUTES.items() if k != "craft_spot"} | spot
        assert _gate_refuses(obs, memory), spot
        assert decide_mode(obs, memory) == ("hunt", None), spot


def test_a_throne_it_cannot_sell_does_not_become_a_forever_craft():
    # The gate allows one throne per goal (`CarpenterCraft.craft_batch == 1`) and refuses
    # while one is already in the pack. `sell_furniture` is the only way to clear it and
    # it needs a vendor route — and `run_supply_pair` stages with `strict=False`, so an
    # UNSET vendor_spot is a tolerated, documented live outcome (a Banker resolving to
    # the Weaponsmith's serial leaves the key unset and the run proceeds). Sten still
    # has his staged saw and still gets boards from Bjorn, so without this clause he
    # crafts exactly one throne and then wants a craft the gate refuses forever.
    from anima2.carpenter_life import CRAFT_BATCH

    unrouted = {k: v for k, v in ROUTES.items() if k != "vendor_spot"}
    held = _obs([*_stocked(), _item(0x903, FURNITURE, CRAFT_BATCH)])
    assert _gate_refuses(held, unrouted)
    assert decide_mode(held, unrouted) == ("hunt", None)
    # With the route wired it is a SALE, not a wait — the clause blocks the craft, not
    # the chain.
    assert decide_mode(held, dict(ROUTES)) == ("economy", "sell_furniture")
