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

from .capabilities import _valid_spot
from .contract import Observation
from .skills.carpentry import (
    BuyBoards,
    BuySaw,
    CarpenterCraft,
    FetchBoards,
    SellFurniture,
)
from .skills.harvest import BACKPACK_LAYER
from .skills.hunt import GOLD_GRAPHIC
from .skills.market import TOOL_BUY_AMOUNT
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
#: bank and the skill that performs it keep the same amount.
BANK_RESERVE = BOARD_BATCH_COST + SAW_COST
#: Bank above that reserve. `>` not `>=`, matching the gate's own comparison exactly.
BANK_ABOVE = BANK_RESERVE

_SAW_GRAPHICS = frozenset(BuySaw.owned_tool_graphics)
_BOARD_GRAPHICS = frozenset(FetchBoards.fetched_graphics)
_FURNITURE_GRAPHIC = SellFurniture.sold_graphic


def _backpack(obs: Observation) -> int | None:
    return next((i.serial for i in obs.items
                 if i.layer == BACKPACK_LAYER and i.container == obs.player.serial), None)


def _pack(obs: Observation, graphics) -> int:
    bp = _backpack(obs)
    if bp is None:
        return 0
    if isinstance(graphics, int):
        graphics = {graphics}
    return sum(i.amount for i in obs.items if i.graphic in graphics and i.container == bp)


def _owns(obs: Observation, graphics) -> bool:
    """Held or worn — the same definition the skills and the readiness gate now share
    (see `capabilities._owned_tool`, widened after a worn axe cost a woodsman a run)."""
    bp = _backpack(obs)
    return any(i.graphic in graphics and i.container in (bp, obs.player.serial)
               for i in obs.items)


def _on_ground(obs: Observation, graphics) -> bool:
    return any(i.graphic in graphics and i.container is None for i in obs.items)


def decide_mode(obs: Observation, memory: dict) -> tuple[str, str | None]:
    """Pick ``("hunt", None)`` or ``("economy", capability_id)`` for a carpenter.

    Nearly always the latter: this profession has no work skill, so the "hunt" answer
    here means only "leave this tick to the work planner's reflexes" — in practice, the
    death window. Priority is the saw, then finishing the chain (sell), then material
    (free before bought), then banking, then crafting.
    """
    if obs.player.dead:
        return "hunt", None  # RecoverDeath (a work-planner reflex) owns the death window
    if not _owns(obs, _SAW_GRAPHICS):
        if _on_ground(obs, _SAW_GRAPHICS):
            return "economy", "fetch_saw"
        if (_pack(obs, GOLD_GRAPHIC) >= SAW_COST
                and _valid_spot(memory.get(BuySaw.vendor_spot_key))):
            return "economy", "buy_saw"
        return "hunt", None  # no tool and no way to get one — do not stall at a shop
    # Finish the chain before extending it: turn finished furniture into gold first.
    if (_pack(obs, _FURNITURE_GRAPHIC) >= SELL_FURNITURE_AT
            and _valid_spot(memory.get(SellFurniture.vendor_spot_key))):
        return "economy", "sell_furniture"
    if _pack(obs, _BOARD_GRAPHICS) < BOARDS_PER_ITEM:
        # A neighbour's delivery is free; a vendor's boards are not. Ground first.
        if _on_ground(obs, _BOARD_GRAPHICS):
            return "economy", "fetch_boards"
        if (_pack(obs, GOLD_GRAPHIC) >= BOARD_BATCH_COST
                and _valid_spot(memory.get(BuyBoards.vendor_spot_key))):
            return "economy", "buy_boards"
        return "hunt", None  # no material and no means — wait rather than stall
    if _pack(obs, GOLD_GRAPHIC) > BANK_ABOVE and _valid_spot(memory.get("banker_spot")):
        return "economy", "bank_gold"
    return "economy", "craft_carpentry"


class CarpenterLife(WarriorLife):
    """Autonomous craft <-> restock <-> sell orchestrator for a carpenter.

    Structure is `WarriorLife`'s, live-verified across three other professions; only the
    profession and the mode rule differ. See the module docstring for why this one is
    almost entirely economy.
    """

    decide = staticmethod(decide_mode)

    def __init__(self, body, persona, profession: str = "carpenter",
                 routes: dict | None = None) -> None:
        super().__init__(body, persona, profession=profession, routes=routes)
        # Tell the SKILL what the rule assumes: keep this much, bank the surplus.
        self.econ_agent.memory["bank_reserve"] = BANK_RESERVE

    @property
    def work_agent(self):
        """The inherited work-side agent. For a carpenter it holds only reflexes."""
        return self.hunt_agent
