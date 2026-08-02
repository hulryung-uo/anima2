"""CarpenterLife — the autonomous orchestrator that lets a CARPENTER live a full loop.

The fourth life on `warrior_life.py::WarriorLife`, after the mage and the woodsman, and
the one that finally has no work of its own to fall back on.

The other three all have something they can always do for free. A warrior can swing at
whatever is in front of it; a mage can cast while its pouch holds ash; a woodsman can
walk up to a tree and chop. Each of those is a plain work skill, running outside the
capability system, so "work mode" always means something.

A carpenter has **no work skill at all** (`PROFESSIONS["carpenter"].work_skill is None`).
Every single thing it does — crafting, selling, restocking, replacing its saw, banking —
is a goal-scoped capability. So for this profession there is no separate work mode to
return to; the mode rule below answers with a capability essentially always, and the
inherited work-side agent only ever covers the death window. That is not a limitation
being worked around, it is what this profession IS, and it makes the life a real test of
whether the capability layer can carry an agent on its own.

What stops a carpenter is different again, and it is the first life whose blocker is
somebody ELSE:

    a lost saw (no tool, no trade)
      > sell the furniture it is holding
      > get boards — off the ground first, bought only if it must
      > bank the surplus
      > craft

The boards line is the interesting one. A woodsman makes its own material out of the
world; a carpenter cannot. It either buys boards, or it picks up boards a lumberjack
delivered to its drop point — which is why `fetch_boards` is preferred over `buy_boards`
even when it can afford them. Free material that a neighbour hauled over is the whole
point of having a village rather than a set of hermits.
"""

from __future__ import annotations

from .capabilities import _valid_spot, craft_spot_within
from .contract import Observation
from .obsview import on_ground, owns, pack_amount
from .skills.carpentry import (
    BuyBoards,
    BuySaw,
    CarpenterCraft,
    FetchBoards,
    SellFurniture,
)
from .skills.hunt import GOLD_GRAPHIC
from .skills.market import TOOL_BUY_AMOUNT, _bank_reserve
from .warrior_life import WarriorLife

#: Boards consumed per piece of furniture — the craft capability's own recipe.
BOARDS_PER_ITEM = CarpenterCraft.craft_material_per_item
#: What one restock of boards costs, from the buy capability's own config.
BOARD_BATCH_COST = BuyBoards.buy_amount * BuyBoards.buy_price_estimate
#: What replacing a lost saw costs.
SAW_COST = TOOL_BUY_AMOUNT * BuySaw.tool_price_estimate
#: Furniture worth a trip to the vendor — the sell capability's own trigger.
SELL_FURNITURE_AT = SellFurniture.sell_threshold
#: The working capital a carpenter KEEPS: one restock of material plus a replacement
#: saw. Derived from the two things it cannot trade without, the way the woodsman's
#: reserve is derived from its axe — a round number here would be a number that agrees
#: with nothing. Written into the economy agent's memory as `bank_reserve`, the key the
#: `bank_gold` gate and `BankGold`'s own FSM already share, so the rule that decides to
#: bank and the skill that performs it keep the same amount. The rule below banks ABOVE
#: it with `>`, not `>=`, matching the gate's own comparison exactly.
BANK_RESERVE = BOARD_BATCH_COST + SAW_COST
#: One craft's worth of output — the craft gate's own `0 <= made < batch` clause, read
#: off the same skill class the gate reads it from.
CRAFT_BATCH = CarpenterCraft.craft_batch

_SAW_GRAPHICS = frozenset(BuySaw.owned_tool_graphics)
_BOARD_GRAPHICS = frozenset(FetchBoards.fetched_graphics)
_FURNITURE_GRAPHIC = SellFurniture.sold_graphic
#: The craft gate's own drift tolerance at the stand — read off the skill class the gate
#: reads it from, exactly as `tinker_life` does, so the two can never be tuned apart.
_CRAFT_RADIUS = getattr(CarpenterCraft, "craft_spot_radius", 0)

# `_backpack`, `_pack`, `_owns` and `_on_ground` now come from `obsview` as `pack_amount`,
# `owns` and `on_ground`. `_on_ground`'s docstring — the nine-tile stall that made the
# distance clause non-negotiable — moved with it, and is the reason the other three Lives'
# ground checks now read from the same definition instead of remembering to copy it.


def decide_mode(obs: Observation, memory: dict) -> tuple[str, str | None]:
    """Pick ``("hunt", None)`` or ``("economy", capability_id)`` for a carpenter.

    Nearly always the latter: this profession has no work skill, so the "hunt" answer
    here means only "leave this tick to the work planner's reflexes" — in practice, the
    death window, plus the two waits below (no tool and no means, no material and no
    means) and the craft the gate would refuse. Priority is the saw, then finishing the
    chain (sell), then material (free before bought), then banking, then crafting —
    each branch guarded by the same precondition the matching gate applies.
    """
    if obs.player.dead:
        return "hunt", None  # RecoverDeath (a work-planner reflex) owns the death window
    if not owns(obs, _SAW_GRAPHICS):
        if on_ground(obs, _SAW_GRAPHICS):
            return "economy", "fetch_saw"
        if (pack_amount(obs, GOLD_GRAPHIC) >= SAW_COST
                and _valid_spot(memory.get(BuySaw.vendor_spot_key))):
            return "economy", "buy_saw"
        return "hunt", None  # no tool and no way to get one — do not stall at a shop
    # Finish the chain before extending it: turn finished furniture into gold first.
    if (pack_amount(obs, _FURNITURE_GRAPHIC) >= SELL_FURNITURE_AT
            and _valid_spot(memory.get(SellFurniture.vendor_spot_key))):
        return "economy", "sell_furniture"
    if pack_amount(obs, _BOARD_GRAPHICS) < BOARDS_PER_ITEM:
        # A neighbour's delivery is free; a vendor's boards are not. Ground first.
        if on_ground(obs, _BOARD_GRAPHICS):
            return "economy", "fetch_boards"
        if (pack_amount(obs, GOLD_GRAPHIC) >= BOARD_BATCH_COST
                and _valid_spot(memory.get(BuyBoards.vendor_spot_key))):
            return "economy", "buy_boards"
        return "hunt", None  # no material and no means — wait rather than stall
    if pack_amount(obs, GOLD_GRAPHIC) > _bank_reserve(memory, BANK_RESERVE) \
            and _valid_spot(memory.get("banker_spot")):
        return "economy", "bank_gold"
    # Mirror the craft gate's own two remaining clauses (`capabilities._make_craft_ready`:
    # `_craft_at_spot` and `0 <= made < batch`) via the gate's OWN predicate, the way
    # `tinker_life` has since birth. This branch was the terminal fallthrough — the ONE
    # place the carpenter broke the invariant its two sibling branches above already keep
    # — and a differential probe over 30,000 randomized carpenter states found it: the
    # only forward-concordance violation across all five Lives (150,000 states), 308 of
    # them, every one failing on the spot radius, the batch clause, or both.
    #
    # What it cost is not a missed craft, it is a PERMANENT one: the rule wants
    # `craft_carpentry` against `ready=[]` forever, nothing else in the chain fires
    # (sell needs a vendor route, fetch/buy are skipped with boards in the pack), and once
    # `disagreement_ticks` elapses `warrior_life._clear_stale_ui` fires EVERY TICK on a
    # perfectly healthy agent — 16 unowned-vendor-window closes in 30 ticks, measured.
    # Both halves are live-shaped: `run_supply_pair` stages with `strict=False`, so an
    # unset `vendor_spot` is a tolerated, documented outcome, and a carpenter that then
    # crafts its one throne can never clear the batch; and `FetchBoards` walks up to
    # `PICKUP_RADIUS` (6) — twice this radius — to a delivered pile with no walk-home leg.
    # The material clause is already exact here: this branch is reached only with
    # boards >= BOARDS_PER_ITEM, which with made == 0 IS the gate's per_item * (batch - made).
    if (pack_amount(obs, _FURNITURE_GRAPHIC) < CRAFT_BATCH
            and craft_spot_within(obs, memory, _CRAFT_RADIUS)):
        return "economy", "craft_carpentry"
    return "hunt", None  # nothing admissible — wait for the world, never stall


class CarpenterLife(WarriorLife):
    """Autonomous craft <-> restock <-> sell orchestrator for a carpenter.

    Structure is `WarriorLife`'s, live-verified across three other professions; only the
    profession and the mode rule differ. See the module docstring for why this one is
    almost entirely economy.
    """

    decide = staticmethod(decide_mode)
    DEFAULT_BANK_RESERVE = BANK_RESERVE

    def __init__(self, body, persona, profession: str = "carpenter",
                 routes: dict | None = None, **knobs) -> None:
        # The base constructor writes `bank_reserve` (DEFAULT_BANK_RESERVE unless the
        # caller tunes it) into the econ memory — the one key rule, gate and FSM read.
        super().__init__(body, persona, profession=profession, routes=routes, **knobs)

    @property
    def work_agent(self):
        """The inherited work-side agent. For a carpenter it holds only reflexes."""
        return self.hunt_agent
