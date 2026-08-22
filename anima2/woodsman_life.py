"""WoodsmanLife — the autonomous orchestrator that lets a LUMBERJACK live a full loop.

The third life built on `warrior_life.py::WarriorLife`, after the mage, and it is the
first one for an agent that does not fight for a living. Everything structural is
inherited and already live-verified: two `Agent`s over one body, SEPARATE memories that
coordinate only through the world, a caching body so the mode decision costs no extra
pump, and the switch hysteresis. (The inherited work-side agent is still called
`hunt_agent` — for a woodsman read that as "the agent that does the job", which for this
profession is swinging an axe at a tree.)

What differs, again, is the DECISION — and this profession stops for reasons neither of
the first two had:

    a lost or broken AXE (a woodsman with no tool is not a woodsman)
      > sell the boards it is carrying
      > turn its logs into boards
      > bank the surplus
      > chop

Two things about that list are worth stating, because they are what makes this life a
different shape rather than a reskin of the warrior's.

**Its chain is longer.** A warrior turns a fight straight into gold. A woodsman's value
has to be carried: tree -> log -> board -> gold. So the rule prefers the link that moves
material FURTHEST along (sell before process), which is the same ordering lesson the
artisan's chain-priority client had to learn live — an agent that always takes the first
ready step keeps making more of what it already has and never finishes anything.

**Its tool is consumable, and the world runs out.** A blade is lost to a specific event
and a reagent pouch empties predictably, but an axe simply breaks mid-swing, and a grove
thins until swinging at it earns nothing. The first is handled here (`fetch_hatchet` for
one lying on the ground, else `buy_hatchet`); the second is `Harvest`'s windowed
stuck-rate relocation, which `Chop` now fills on lumberjacking's own 500493 and
which the woodsman runners seed with a surveyed grove pool.
"""

from __future__ import annotations

from .capabilities import _valid_spot
from .contract import Observation
from .obsview import on_ground, owns, pack_amount
from .skills.hunt import GOLD_GRAPHIC
from .skills.market import TOOL_BUY_AMOUNT, _bank_reserve
from .skills.woodwork import (
    AXE_GRAPHICS,
    BOARD_GRAPHIC,
    LOG_GRAPHIC,
    BuyHatchet,
    DeliverBoards,
    SellBoards,
)
from .warrior_life import WarriorLife

#: Boards worth a trip to the vendor — the sell capability's own trigger, reused so the
#: rule and the readiness gate can never drift apart.
SELL_BOARDS_AT = SellBoards.sell_threshold
#: Boards worth hauling to a partner — the deliver capability's own trigger.
DELIVER_BOARDS_AT = DeliverBoards.deliver_threshold
#: What replacing a broken axe costs, from the buy capability's own config.
HATCHET_COST = TOOL_BUY_AMOUNT * BuyHatchet.tool_price_estimate
#: Bank the surplus above a working reserve — enough to replace the axe several times
#: over, which is the only thing this profession must never be caught short of.
#:
#: DERIVED from that reason rather than picked. The first value here was a flat 300 (a
#: round number, twelve axes) and it was never reached: a live Yew run peaked at 180
#: gold across six sales, so `bank_gold` sat ready 46 times and was never once chosen.
#: A threshold no run can reach does not express caution, it just removes a capability
#: from the agent's life without saying so.
BANK_RESERVE_AXES = 6
#: The working capital a woodsman KEEPS. One number, read by three places that must
#: agree: this rule (when to want banking), the capability gate (`_bank_reserve`, which
#: is ready at `gold > reserve`), and `BankGold`'s own FSM (which finishes when
#: `final_pack_gold == reserve`). The village wires it into the economy agent's memory
#: under `bank_reserve`, the key those two already share.
#:
#: Leaving it unset — as this first did — means a reserve of ZERO: the woodsman banks
#: every last coin and walks back to the grove unable to replace a broken axe, which is
#: precisely the outcome this constant's own comment claims to prevent. Having a
#: threshold is not the same as keeping a reserve, and conflating them cost nothing here
#: only because no run had reached the threshold yet.
#:
#: The rule below banks ABOVE it — `>`, not `>=`, the same comparison the gate makes —
#: so the rule can never want a deposit the gate would refuse. That clause used to live
#: on a separate `BANK_ABOVE = BANK_RESERVE` alias, and having a second NAME for one
#: number is how the threshold and the reserve came apart in the first place; it belongs
#: on the number itself.
BANK_RESERVE = HATCHET_COST * BANK_RESERVE_AXES


# `_backpack`, `_pack_amount`, `_has_axe` and `_axe_on_ground` now come from `obsview`.
# `owns(obs, AXE_GRAPHICS)` is what `_has_axe` was — an axe HELD OR WORN, the tool `Chop`
# and `ProcessLogs` both require, and the widening that a worn axe cost this very
# profession a whole run to learn. `on_ground(obs, AXE_GRAPHICS)` is `_axe_on_ground`,
# distance clause and all. Both docstrings moved with them.


def decide_mode(obs: Observation, memory: dict) -> tuple[str, str | None]:
    """Pick ``("hunt", None)`` (i.e. go chop) or ``("economy", capability_id)``.

    Priority follows what actually stops a woodsman working: the axe first, then moving
    material along its own chain (sell before process — see the module docstring), then
    banking, else chop. Like the warrior's and mage's rules, every economy branch also
    requires its route AND the means, so a broke or unrouted woodsman keeps chopping
    rather than standing at a shop it cannot use.
    """
    if obs.player.dead:
        return "hunt", None  # RecoverDeath (a work-planner reflex) owns the death window
    if not owns(obs, AXE_GRAPHICS):
        # A dropped axe is free; buying one is not. Prefer the ground.
        if on_ground(obs, AXE_GRAPHICS):
            return "economy", "fetch_hatchet"
        if (pack_amount(obs, GOLD_GRAPHIC) >= HATCHET_COST
                and _valid_spot(memory.get(BuyHatchet.vendor_spot_key))):
            return "economy", "buy_hatchet"
        return "hunt", None  # no tool and no way to get one — keep living, not stalling
    boards = pack_amount(obs, BOARD_GRAPHIC)
    # A partner outranks the shop. Configuring `carpenter_drop` IS the statement that
    # this woodsman supplies somebody — without it nothing changes and it sells as
    # before. Note what this costs in gold, because it is not nothing: the vendor buys
    # boards at 2g each, and EVERY carpentry recipe on this shard sells for less than
    # its own boards (the best, a Club, is 9 boards for 13g against 18g raw). Supplying
    # a carpenter is therefore a decision about having a village, not about income, and
    # it should stay a decision the caller makes explicitly rather than a default.
    if boards >= DELIVER_BOARDS_AT and _valid_spot(memory.get(DeliverBoards.drop_key)):
        return "economy", "deliver_boards"
    # Finish the chain before extending it: a woodsman holding a sellable stack of boards
    # should turn them into gold, not chop more wood it has nowhere to put.
    if (boards >= SELL_BOARDS_AT
            and _valid_spot(memory.get(SellBoards.vendor_spot_key))):
        return "economy", "sell_boards"
    if pack_amount(obs, LOG_GRAPHIC) > 0:
        return "economy", "process_logs"
    # `>` against the SAME `bank_reserve` key the gate and BankGold's FSM read.
    if pack_amount(obs, GOLD_GRAPHIC) > _bank_reserve(memory, BANK_RESERVE) \
            and _valid_spot(memory.get("banker_spot")):
        return "economy", "bank_gold"
    return "hunt", None


class WoodsmanLife(WarriorLife):
    """Autonomous chop <-> haul-and-sell orchestrator for a lumberjack (see module doc).

    Everything structural is `WarriorLife`'s, already live-verified across two other
    professions; only the profession and the mode rule differ.
    """

    decide = staticmethod(decide_mode)
    DEFAULT_BANK_RESERVE = BANK_RESERVE

    def __init__(self, body, persona, profession: str = "lumberjack",
                 routes: dict | None = None, **knobs) -> None:
        # The base constructor writes `bank_reserve` (DEFAULT_BANK_RESERVE unless the
        # caller tunes it) into the econ memory — the one key rule, gate and FSM read.
        super().__init__(body, persona, profession=profession, routes=routes, **knobs)

    @property
    def work_agent(self):
        """The inherited work-side agent, under a name that fits a non-combat life."""
        return self.hunt_agent
