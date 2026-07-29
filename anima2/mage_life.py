"""MageLife — the autonomous orchestrator that lets a mage LIVE a full loop.

The mage's counterpart to `warrior_life.py::WarriorLife`, and it deliberately reuses that
orchestrator's whole machinery: two `Agent`s over one body (a hunt-mode agent and an
economy-mode agent), SEPARATE memories that coordinate only through the world, a caching
body so the mode decision costs no extra pump, and the switch hysteresis. Those three
properties were each live-caught the hard way while building the warrior's version, so the
mage inherits the fixes rather than rediscovering them.

What differs is the DECISION, because a mage stops being able to fight for different
reasons than a warrior does. A warrior is stopped by losing its blade or its armor; a mage
is stopped by running out of REAGENTS — without them `CastAttack` is inert and a frail
caster is left swinging its fists. So the mage's rule is:

    reagents (it cannot cast without them)
      > collect a delivered purse (the crafter's earnings are sitting on the ground)
      > bank the surplus
      > hunt

Note the second line: this is where the production pipeline closes into the mage's own
life. A crafter delivers gold to the mage's funding spot, and the mage — with no prompting
from anyone — walks over, picks it up, and turns it into the ability to cast.
"""

from __future__ import annotations

from .capabilities import _valid_spot
from .contract import Observation
from .skills.harvest import BACKPACK_LAYER
from .skills.hunt import GOLD_GRAPHIC
from .skills.craft import PICKUP_RADIUS
from .skills.mage import FETCH_GOLD_PACK_CAP, BuyReagent, SULFUROUS_ASH_GRAPHIC
from .warrior_life import WarriorLife

#: Restock reagents when the pouch falls below the buy capability's own reorder line.
LOW_REAGENTS = BuyReagent.buy_reorder
#: One reagent batch's cost — the affordability floor for a restock.
REAGENT_BATCH_COST = BuyReagent.buy_amount * BuyReagent.buy_price_estimate
#: Collect a delivered purse whenever this much gold (or more) is lying in reach. A
#: crafter's `DeliverGold` drops a real purse, so anything at all on the ground is worth
#: the few steps.
COLLECT_ABOVE = 1
#: The working capital a mage KEEPS — and it is the fetch gate's own pack cap
#: (`FETCH_GOLD_PACK_CAP`, three reagent batches), because the two thresholds are one
#: idea: below it the mage still collects delivered purses, above it the surplus goes
#: to the bank. The first value here was a flat 400 alongside a reserve of ZERO
#: (nothing wrote `bank_reserve`), so the mage would have banked every coin at 400 and
#: kept nothing for ash — the threshold-vs-reserve conflation, mage edition.
BANK_RESERVE = FETCH_GOLD_PACK_CAP
#: Back-compat alias (tests referenced the old name).
BANK_ABOVE = BANK_RESERVE


def _backpack(obs: Observation) -> int | None:
    return next((i.serial for i in obs.items
                 if i.layer == BACKPACK_LAYER and i.container == obs.player.serial), None)


def _pack_amount(obs: Observation, graphic: int) -> int:
    bp = _backpack(obs)
    return sum(i.amount for i in obs.items if i.graphic == graphic and i.container == bp) if bp else 0


def _ground_gold(obs: Observation) -> int:
    """Gold lying in the world WITHIN PICKUP REACH — never our own pack gold.

    The distance test mirrors the fetch gate's own `PICKUP_RADIUS` clause. Without it
    this rule wanted `fetch_gold` for a purse it could merely SEE, which admission must
    refuse — the audit found exactly that drift latent here after the carpenter's
    identical fix was never back-ported (docs/AUDIT-2026-07-29.md), and the concordance
    suite now fails on it if either side moves alone.
    """
    return sum(i.amount for i in obs.items
               if i.graphic == GOLD_GRAPHIC and i.container is None
               and i.distance <= PICKUP_RADIUS)


def decide_mode(obs: Observation, memory: dict) -> tuple[str, str | None]:
    """Pick ``("hunt", None)`` or ``("economy", capability_id)`` for a mage.

    Priority follows what actually stops a mage living: reagents first (an empty pouch
    makes `CastAttack` inert — the caster's equivalent of a warrior losing its blade), then
    collecting a delivered purse (the crafter's earnings, waiting on the ground), then
    banking surplus, else hunt. Like the warrior's rule, every economy branch also requires
    its route AND the means, so a broke or unrouted mage keeps hunting rather than stalling
    at a shop it cannot use.
    """
    if obs.player.dead:
        return "hunt", None  # RecoverDeath (a hunt-planner reflex) owns the death window
    gold = _pack_amount(obs, GOLD_GRAPHIC)
    if (_pack_amount(obs, SULFUROUS_ASH_GRAPHIC) < LOW_REAGENTS
            and gold >= REAGENT_BATCH_COST
            and _valid_spot(memory.get("mage_vendor_spot"))):
        return "economy", "buy_reagent"
    # The pipeline closing into the mage's own life: a crafter's purse is on the ground,
    # so go and pick it up — that gold is what the next reagent batch is bought with.
    # The pack cap is the GATE'S OWN (one shared constant): once the purse already holds
    # a few reagent batches' worth, admission refuses the fetch, so wanting it would be
    # the stall shape this project keeps paying for.
    if _ground_gold(obs) >= COLLECT_ABOVE and gold < FETCH_GOLD_PACK_CAP:
        return "economy", "fetch_gold"
    if gold > memory.get("bank_reserve", BANK_RESERVE) \
            and _valid_spot(memory.get("banker_spot")):
        return "economy", "bank_gold"
    return "hunt", None


class MageLife(WarriorLife):
    """Autonomous hunt <-> resupply orchestrator for a mage (see module docstring).

    Everything structural is `WarriorLife`'s, already live-verified: the two agents with
    separate memories, the caching body, the switch hysteresis, and the Agent-compatible
    surface that lets any agent runner (e.g. `village._run_worker`) drive it unchanged.
    Only the profession and the mode rule differ.
    """

    decide = staticmethod(decide_mode)
    DEFAULT_BANK_RESERVE = BANK_RESERVE

    def __init__(self, body, persona, profession: str = "mage",
                 routes: dict | None = None, **knobs) -> None:
        super().__init__(body, persona, profession=profession, routes=routes, **knobs)
