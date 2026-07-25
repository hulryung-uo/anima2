"""WarriorLife — the autonomous orchestrator that lets a swordsman LIVE a full loop.

A living-endurance test showed a warrior that dies drops its gear on its corpse,
resurrects far away, and — unable to reclaim the guarded corpse — death-loops naked.
The `buy_weapon`/`buy_bandage`/`bank_gold` capabilities give it the *means* to re-arm,
but the warrior hunts in the WORK-SKILL planner while those live in a separate
capability planner, and nothing decides *when* to switch. This orchestrator is that
missing piece: it runs the hunt loop, and when the warrior has lost its blade or run
low on bandages (or has surplus gold to bank), it switches to the economy leg to
re-arm/restock/bank, then resumes hunting — turning a death into a recoverable setback.

Design: two `Agent`s over ONE body — a hunt-mode agent (`profession.planner()`) and an
economy-mode agent (`planner(capability_goals=True)` + `CapabilityPolicy` + a
`CapabilityCognition` whose choice this orchestrator drives). They keep SEPARATE
memories and coordinate only through the WORLD (each observes the shared body): the
hunt agent wields the blade the economy agent bought because it SEES it in the pack,
not via a shared dict. (Sharing one memory was live-caught corrupting the economy
agent's buy FSM with the hunt agent's leftover skill state.) The economy agent is given
the vendor `routes` it needs to shop. Each tick, `decide_mode` (a pure, testable
function over the observation + routes) picks the mode, and the matching agent is
ticked. The economy agent's cognition is SYNCHRONOUS on purpose (the async
ThreadedCognition races and intermittently never proposes the goal — a live-caught flake).
"""

from __future__ import annotations

from .agent import Agent
from .capabilities import CapabilityPolicy, _valid_spot
from .capability_cognition import CapabilityCognition
from .contract import Observation
from .persona import Persona
from .profession import PROFESSIONS
from .skills.harvest import BACKPACK_LAYER
from .skills.hunt import GOLD_GRAPHIC
from .skills.warrior import (
    BANDAGE_GRAPHIC,
    SWORD_GRAPHICS,
    WEAPON_LAYER,
    BuyBandage,
    BuyWeapon,
)

#: Buy a fresh blade when weaponless and this much pack gold is on hand.
WEAPON_PRICE = BuyWeapon.tool_price_estimate
#: Restock bandages when the stack falls below the buy capability's reorder line.
LOW_BANDAGES = BuyBandage.buy_reorder
#: One bandage batch's cost (the affordability floor for a restock).
BANDAGE_BATCH_COST = BuyBandage.buy_amount * BuyBandage.buy_price_estimate
#: Bank looted gold once the pack holds more than this, keeping a working reserve
#: (enough to re-arm a blade + a bandage batch) so banking never strands the warrior.
BANK_ABOVE = 400
#: Require the economy condition to PERSIST this many ticks before switching. This
#: filters the 1-2 tick transient where a blade is on the cursor mid-equip (gone from
#: `items`, so it momentarily reads "weaponless") — diverting to the economy then would
#: interrupt EquipWeapon and strand the blade. During the grace the hunt agent keeps
#: running, so it wields an OWNED blade first; only a genuine loss survives the grace.
ECON_GRACE = 6


def _backpack(obs: Observation) -> int | None:
    return next((i.serial for i in obs.items
                if i.layer == BACKPACK_LAYER and i.container == obs.player.serial), None)


def _pack_amount(obs: Observation, graphic: int) -> int:
    bp = _backpack(obs)
    return sum(i.amount for i in obs.items if i.graphic == graphic and i.container == bp) if bp else 0


def _has_weapon(obs: Observation) -> bool:
    """A sword worn at the one-handed layer OR sitting in the pack (just bought)."""
    bp = _backpack(obs)
    player = obs.player.serial
    return any(
        i.graphic in SWORD_GRAPHICS
        and ((i.layer == WEAPON_LAYER and i.container == player) or (bp is not None and i.container == bp))
        for i in obs.items
    )


def decide_mode(obs: Observation, memory: dict) -> tuple[str, str | None]:
    """Pick ``("hunt", None)`` or ``("economy", capability_id)`` from the live state.

    Priority — re-arm a lost blade first (you cannot hunt without one), then restock
    bandages, then bank surplus, else hunt. Each economy branch also requires its
    vendor route to be configured AND the gold to afford it, so a penniless or
    unrouted warrior just keeps hunting rather than stalling at a shop it can't use.
    """
    if obs.player.dead:
        return "hunt", None  # RecoverDeath (a hunt-planner reflex) owns the death window
    gold = _pack_amount(obs, GOLD_GRAPHIC)
    if not _has_weapon(obs) and gold >= WEAPON_PRICE and _valid_spot(memory.get("weapon_vendor_spot")):
        return "economy", "buy_weapon"
    if _pack_amount(obs, BANDAGE_GRAPHIC) < LOW_BANDAGES and gold >= BANDAGE_BATCH_COST \
            and _valid_spot(memory.get("healer_spot")):
        return "economy", "buy_bandage"
    if gold >= BANK_ABOVE and _valid_spot(memory.get("banker_spot")):
        return "economy", "bank_gold"
    return "hunt", None


class _LifeClient:
    """The economy agent's cognition client — returns whichever capability the
    orchestrator currently wants (set each tick by `WarriorLife`)."""

    def __init__(self, life: "WarriorLife") -> None:
        self._life = life

    def complete(self, system: str, user: str) -> str:
        cap = self._life.target_cap
        if cap is None:
            return '{"schema":1,"decision":"idle"}'
        return '{"schema":1,"decision":"capability","capability":"%s"}' % cap


class WarriorLife:
    """Autonomous hunt <-> re-arm orchestrator for a swordsman (see module docstring)."""

    def __init__(self, body, persona: Persona, profession: str = "swordsman",
                 routes: dict | None = None) -> None:
        prof = PROFESSIONS[profession]
        self.body = body
        #: Vendor routes the economy leg needs (weapon_vendor_spot / healer_spot /
        #: banker_spot). `decide_mode` reads them; the economy agent's memory carries
        #: them so its buy/bank FSMs can navigate.
        self.routes: dict = dict(routes) if routes else {}
        self.hunt_agent = Agent(body=body, persona=persona, planner=prof.planner())
        self.econ_agent = Agent(
            body=body, persona=persona,
            planner=prof.planner(capability_goals=True),
            cognition=CapabilityCognition(_LifeClient(self), profession),
            cognition_interval=1, profession=profession,
            goal_policy=CapabilityPolicy(profession),
        )
        # SEPARATE memories (no shared dict). The economy agent gets the vendor routes.
        self.econ_agent.memory.update(self.routes)
        self.mode = "hunt"
        self.target_cap: str | None = None
        self._econ_streak = 0

    def set_route(self, key: str, value) -> None:
        """Configure a vendor route on both the decision inputs and the economy agent."""
        self.routes[key] = value
        self.econ_agent.memory[key] = value

    def tick(self) -> None:
        obs = self.body.observe()
        mode, cap = decide_mode(obs, self.routes)
        # Hysteresis: only commit to the economy after the condition PERSISTS for
        # ECON_GRACE ticks, so a transient mid-equip "weaponless" blip doesn't yank the
        # warrior off wielding a blade it already owns (which would strand it on the
        # cursor). Genuine loss/low-supply persists and switches.
        if mode == "economy":
            self._econ_streak += 1
            if self._econ_streak < ECON_GRACE:
                mode, cap = "hunt", None
        else:
            self._econ_streak = 0
        self.mode, self.target_cap = mode, cap
        (self.econ_agent if mode == "economy" else self.hunt_agent).tick()
