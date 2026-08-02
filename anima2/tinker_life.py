"""TinkerLife — the autonomous orchestrator for the flagship POSITIVE economy.

The fifth life on `warrior_life.py::WarriorLife`, and the first built after the audit
made new professions cheap: the harness carries staging/provenance/telemetry, the
concordance suite checks its rule against the gates offline, and the disagreement
detector is inherited. It exists for an economic reason, not a mechanical one.

Measured against ServUO's own price tables (pinned by `tests/test_price_tripwire.py`),
tongs are the ONE craft on this shard that beats selling its input: an ingot sells raw
for 4g, and one ingot becomes a tongs the tinker vendor pays 7g for — 1.75x, and the
margin is real even on bought iron (5g -> 7g). Every other deployed craft destroys
value (a throne is 19 boards' 38g sold for 24g; a dagger is 3 ingots' 12g sold for
10g), which is why the lumberjack->carpenter pair is economically frozen and THIS pair
— a miner delivering free mined iron to a tinker — is the flagship: craft-side margin
of +7g per ingot, on ground the Phase-3 trade corridor already calibrated.

The rule, in the chain-priority order the artisan taught us (finish before extending):

    a lost tinker tool (no tool, no trade)
      > sell the tongs it is holding
      > collect delivered iron — free material outranks everything money can buy
        (ground drops decay; pack gold does not)
      > bank URGENTLY once the pack holds more than reserve + one restock of spare
        gold — a HEALTHY supply chain never opens the idle gap the patient branch
        below waits for (forge4 live, 2026-07-30: 1500 ticks, 210g carried,
        bank_gold ready and starved the whole day)
      > craft, while standing where the craft gate's own radius allows
      > bank the surplus above a derived working reserve (finish before extending —
        the coverage check proved the other order never banks at all)
      > buy iron only when nothing has been delivered

Every threshold here is the corresponding capability's own config, and the craft
branch reuses the gate's own `craft_spot_within` predicate — the audit's single-source
discipline, applied from birth rather than retrofitted.
"""

from __future__ import annotations

from .capabilities import _valid_spot, craft_spot_within
from .contract import Observation
from .knobs import knob_int
from .obsview import on_ground, owns, pack_amount
from .skills.hunt import GOLD_GRAPHIC
from .skills.market import TOOL_BUY_AMOUNT, _bank_reserve
from .skills.smelt import INGOT_GRAPHICS
from .skills.tinkering import (
    FETCH_IRON_PACK_CAP,
    TONGS_GRAPHIC,
    BuyIron,
    BuyTinkerTool,
    SellTongs,
    TinkerTongs,
)
from .warrior_life import WarriorLife

#: What a replacement tinker tool costs, from the buy capability's own config.
TOOL_COST = TOOL_BUY_AMOUNT * BuyTinkerTool.tool_price_estimate
#: What one iron restock costs.
IRON_BATCH_COST = BuyIron.buy_amount * BuyIron.buy_price_estimate
#: Tongs worth a trip to the vendor — the sell capability's own trigger.
SELL_TONGS_AT = SellTongs.sell_threshold
#: The working capital a tinker KEEPS: one iron restock plus a replacement tool — the
#: two things it cannot trade without, derived exactly like the carpenter's reserve.
BANK_RESERVE = IRON_BATCH_COST + TOOL_COST
#: Surplus above the reserve at which banking turns URGENT — outranking even a ready
#: craft. Derived, not invented: one iron restock (`IRON_BATCH_COST`) is the biggest
#: single purchase any tinker errand makes, so spare gold beyond it is profit no
#: errand can spend. Without this band the patient bank branch only ever fires in a
#: supply GAP, and a healthy miner never opens one.
#:
#: Overridable per instance via `TinkerLife(..., bank_trip_surplus=...)` — a §E
#: "priority band" axis. RULE-ONLY: the only bank gate is `gold > bank_reserve`
#: (`capabilities.py::_bank_ready`), and the urgent band `gold > reserve + surplus` is
#: STRICTLY STRICTER than that for any surplus >= 0, so tuning it up can never make the
#: rule want a deposit the gate refuses. That "for any surplus >= 0" is why the read
#: below goes through `knobs.knob_int` and not `memory.get`: at a NEGATIVE value the
#: band becomes looser than the gate and the rule wants `bank_gold` the gate refuses —
#: the same drift `bank_reserve` already paid for, arriving through a second knob.
BANK_TRIP_SURPLUS = IRON_BATCH_COST

_TOOL_GRAPHICS = frozenset(TinkerTongs.craft_tool_graphics)
_CRAFT_RADIUS = getattr(TinkerTongs, "craft_spot_radius", 0)


# This life was written after the audit and still copy-pasted `_backpack`, `_pack`,
# `_owns_tool` and `_iron_on_ground` from the carpenter — which is the clearest evidence
# there is that the copies were the defect and not the habit. They are `obsview`'s
# `pack_amount`, `owns` and `on_ground` now: held-or-worn is the definition the widened
# `_owned_tool` gate shares, and the ground check is the fetch gate's own distance clause.


def decide_mode(obs: Observation, memory: dict) -> tuple[str, str | None]:
    """Pick ``("hunt", None)`` (idle wait) or ``("economy", capability_id)``.

    Like the carpenter, a tinker has no work skill — every action is a capability, so
    "hunt" here only means "wait for the world to change" (a delivery arriving, mana
    for nobody — a tinker's waits are all supply waits).
    """
    if obs.player.dead:
        return "hunt", None  # RecoverDeath (a work-planner reflex) owns the death window
    gold = pack_amount(obs, GOLD_GRAPHIC)
    if not owns(obs, _TOOL_GRAPHICS):
        if gold >= TOOL_COST and _valid_spot(memory.get(BuyTinkerTool.vendor_spot_key)):
            return "economy", "buy_tinker_tool"
        return "hunt", None  # no tool and no means — wait, never stall at a shop
    tongs = pack_amount(obs, TONGS_GRAPHIC)
    if tongs >= SELL_TONGS_AT and _valid_spot(memory.get(SellTongs.vendor_spot_key)):
        return "economy", "sell_tongs"
    iron = pack_amount(obs, INGOT_GRAPHICS)
    # Free material outranks everything money can buy: delivered iron is the whole
    # margin (7g/ingot delivered vs +2g/ingot bought).
    if on_ground(obs, INGOT_GRAPHICS) and iron < FETCH_IRON_PACK_CAP:
        return "economy", "fetch_iron"
    reserve = _bank_reserve(memory, BANK_RESERVE)
    # Both knobs read through the same clamp (`knobs.py`), so a malformed value moves
    # this rule and the `bank_gold` gate to the same place instead of prying them apart.
    surplus = knob_int(memory, "bank_trip_surplus", BANK_TRIP_SURPLUS)
    # Pockets full -> the bank outranks even a ready craft. The patient branch below
    # only fires when nothing above it wants a turn, and forge4 (2026-07-30) proved
    # live that a HEALTHY supply chain never opens that gap: with the miner
    # delivering continuously, Pim finished a 1500-tick day carrying 210g while
    # bank_gold sat in the ready set throughout. Fetching stays above this (ground
    # drops decay; pack gold does not).
    if gold > reserve + surplus and _valid_spot(memory.get("banker_spot")):
        return "economy", "bank_gold"
    # Craft while the batch is short and the material covers what is left — the craft
    # gate's own arithmetic (`per_item * (batch - made)`), and the gate's own spot
    # radius via its own predicate.
    need = TinkerTongs.craft_material_per_item * (TinkerTongs.craft_batch - tongs)
    if (tongs < TinkerTongs.craft_batch and iron >= need
            and craft_spot_within(obs, memory, _CRAFT_RADIUS)):
        return "economy", "craft_tongs"
    # Bank BEFORE restocking — the concordance suite's coverage check caught the other
    # order as a genuinely dead branch: with buy_iron outranking bank, some branch
    # (craft, or an affordable restock) always preempts the deposit, and pack gold
    # piles up forever. The reserve is one restock plus a tool by construction, so
    # banking down to it never blocks the purchase that follows.
    if gold > reserve and _valid_spot(memory.get("banker_spot")):
        return "economy", "bank_gold"
    if (iron < BuyIron.buy_reorder and gold >= IRON_BATCH_COST
            and _valid_spot(memory.get(BuyIron.vendor_spot_key))):
        return "economy", "buy_iron"
    return "hunt", None


class TinkerLife(WarriorLife):
    """Autonomous craft <-> restock <-> sell orchestrator for a tinker (see module doc).

    Structure is `WarriorLife`'s, live-verified across four other professions; only the
    profession and the mode rule differ.
    """

    decide = staticmethod(decide_mode)
    DEFAULT_BANK_RESERVE = BANK_RESERVE
    #: Per-class default for the `bank_trip_surplus` the constructor writes.
    DEFAULT_BANK_TRIP_SURPLUS = BANK_TRIP_SURPLUS
    #: EXTENDS the base allowlist, never replaces it — the tinker is the only profession
    #: with a knob of its own, and a spec that dropped the inherited three would reject
    #: `bank_reserve` on the one Life whose reserve matters most (it is the flagship
    #: positive-margin loop).
    KNOBS = WarriorLife.KNOBS | {"bank_trip_surplus"}

    def __init__(self, body, persona, profession: str = "tinker",
                 routes: dict | None = None, *,
                 bank_trip_surplus: int | None = None, **knobs) -> None:
        super().__init__(body, persona, profession=profession, routes=routes, **knobs)
        # A MEMORY KEY, not an instance attribute, because `decide_mode` is a
        # staticmethod over `(obs, memory)` — a knob a rule reads has to be somewhere
        # the rule can see, and the econ memory is the very dict the gates read. Written
        # raw; every reader clamps (`knobs.knob_int`), which is what keeps the two sides
        # from disagreeing about a malformed value.
        self.econ_agent.memory["bank_trip_surplus"] = (
            self.DEFAULT_BANK_TRIP_SURPLUS if bank_trip_surplus is None
            else bank_trip_surplus)
