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
from .skills.market import _bank_reserve
from .skills.harvest import BACKPACK_LAYER
from .skills.hunt import GOLD_GRAPHIC
from .skills.warrior import (
    BANDAGE_GRAPHIC,
    PLATE_ARMOR_LAYERS,
    PLATE_CHEST_GRAPHIC,
    SWORD_GRAPHICS,
    SWORD_RANK,
    WEAPON_LAYER,
    BuyArmor,
    BuyBandage,
    BuyWeapon,
    UpgradeWeapon,
)

#: Buy a fresh blade when weaponless and this much pack gold is on hand.
WEAPON_PRICE = BuyWeapon.tool_price_estimate
#: Restock bandages when the stack falls below the buy capability's reorder line.
LOW_BANDAGES = BuyBandage.buy_reorder
#: One bandage batch's cost (the affordability floor for a restock).
BANDAGE_BATCH_COST = BuyBandage.buy_amount * BuyBandage.buy_price_estimate
#: A replacement plate chest — the biggest slice of armor rating, and the piece a death
#: most needs replaced when the corpse can't be reclaimed.
ARMOR_PRICE = BuyArmor.tool_price_estimate
#: Gold an optional blade upgrade must leave behind (a chest plate's worth), so growth
#: never spends the coin a life-critical re-arm would need.
UPGRADE_RESERVE = ARMOR_PRICE
#: The rank of the blade the vendor offers — a worn sword below this can be traded up.
UPGRADE_TARGET_RANK = SWORD_RANK.get(UpgradeWeapon.offer_graphic, 0)
#: Bank looted gold once the pack holds more than this, keeping a working reserve
#: (enough to re-arm a blade + a bandage batch) so banking never strands the warrior.
#: The working capital a warrior KEEPS: one full re-arm kit - a blade, a bandage
#: batch, and a chest plate, the three purchases the rule itself makes. DERIVED from
#: the same capability configs the buy gates read, not picked: the first value here
#: was a flat 400 alongside a reserve of ZERO (nothing ever wrote `bank_reserve`),
#: which meant the warrior banked every coin at 400 and walked back to the hunt
#: unable to re-arm - the exact outcome the constant claimed to prevent (the
#: woodsman's threshold-vs-reserve conflation, warrior edition; see
#: docs/AUDIT-2026-07-29.md). Overridable per instance via
#: `WarriorLife(..., bank_reserve=...)` - the constructor writes it to the econ
#: memory's `bank_reserve`, the ONE key the rule below, the `bank_gold` gate, and
#: `BankGold`'s own FSM all read.
BANK_RESERVE = WEAPON_PRICE + BANDAGE_BATCH_COST + ARMOR_PRICE
#: Back-compat alias (tests and docs referenced the old name).
BANK_ABOVE = BANK_RESERVE
#: Require the economy condition to PERSIST this many ticks before switching. This
#: filters the 1-2 tick transient where a blade is on the cursor mid-equip (gone from
#: `items`, so it momentarily reads "weaponless") — diverting to the economy then would
#: interrupt EquipWeapon and strand the blade. During the grace the hunt agent keeps
#: running, so it wields an OWNED blade first; only a genuine loss survives the grace.
ECON_GRACE = 6
#: Consecutive ticks the rule may WANT an economy capability that admission never grants
#: (no goal on the stack, capability absent from the ready set) before the orchestrator
#: flags a rule-vs-gate disagreement. High enough that a healthy transaction can never
#: trip it: ready gates deliberately de-assert MID-transaction (a buy in flight holds a
#: goal and shows not-ready), which is why the no-goal guard below is mandatory, not an
#: optimization.
DISAGREEMENT_TICKS = 10


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


def _has_chest(obs: Observation) -> bool:
    """A plate chest worn at its body layer OR sitting in the pack (just bought)."""
    bp = _backpack(obs)
    player = obs.player.serial
    return any(
        i.graphic == PLATE_CHEST_GRAPHIC
        and (
            (i.container == player and i.layer == PLATE_ARMOR_LAYERS[PLATE_CHEST_GRAPHIC])
            or (bp is not None and i.container == bp)
        )
        for i in obs.items
    )


def _worn_blade_rank(obs: Observation) -> int | None:
    """Rank of the sword currently WIELDED, or `None` if bare-handed."""
    ranks = [
        SWORD_RANK.get(i.graphic, 0)
        for i in obs.items
        if i.graphic in SWORD_GRAPHICS
        and i.layer == WEAPON_LAYER and i.container == obs.player.serial
    ]
    return max(ranks) if ranks else None


def _pack_has_sword(obs: Observation) -> bool:
    bp = _backpack(obs)
    return bp is not None and any(
        i.graphic in SWORD_GRAPHICS and i.container == bp for i in obs.items
    )


def decide_mode(obs: Observation, memory: dict) -> tuple[str, str | None]:
    """Pick ``("hunt", None)`` or ``("economy", capability_id)`` from the live state.

    Priority reflects what actually stops a warrior living: a lost blade first (you cannot
    hunt without one), then bandages (you cannot survive a fight without them), then a lost
    chest plate (you can fight, but unarmored against rich prey is fatal — a living test
    proved it), then banking surplus, else hunt. Each economy branch also requires its
    vendor route to be configured AND the gold to afford it, so a penniless or unrouted
    warrior just keeps hunting rather than stalling at a shop it can't use.
    """
    if obs.player.dead:
        return "hunt", None  # RecoverDeath (a hunt-planner reflex) owns the death window
    gold = _pack_amount(obs, GOLD_GRAPHIC)
    if not _has_weapon(obs) and gold >= WEAPON_PRICE and _valid_spot(memory.get("weapon_vendor_spot")):
        return "economy", "buy_weapon"
    if _pack_amount(obs, BANDAGE_GRAPHIC) < LOW_BANDAGES and gold >= BANDAGE_BATCH_COST \
            and _valid_spot(memory.get("healer_spot")):
        return "economy", "buy_bandage"
    if not _has_chest(obs) and gold >= ARMOR_PRICE and _valid_spot(memory.get("armorer_spot")):
        return "economy", "buy_armor"
    # Growth, once the necessities are covered: trade a weaker worn blade up to the
    # vendor's best, but only with surplus beyond a re-arm reserve, and only while the
    # pack holds no sword (the arrival proof requires it to start empty of them).
    worn_rank = _worn_blade_rank(obs)
    if (worn_rank is not None and worn_rank < UPGRADE_TARGET_RANK
            and not _pack_has_sword(obs)
            and gold >= WEAPON_PRICE + UPGRADE_RESERVE
            and _valid_spot(memory.get("weapon_vendor_spot"))):
        return "economy", "upgrade_weapon"
    # `>` against the SAME `bank_reserve` key the gate and BankGold's FSM read - one
    # number decides when to bank, what admission allows, and how much the skill
    # leaves behind. The fallback default only matters to bare-dict unit tests;
    # every Life writes the key at construction.
    if gold > _bank_reserve(memory, BANK_RESERVE) \
            and _valid_spot(memory.get("banker_spot")):
        return "economy", "bank_gold"
    return "hunt", None


class _LifeClient:
    """The economy agent's cognition client.

    Scripted by default: it returns whichever capability the orchestrator's rule wants
    (set each tick by `WarriorLife`), as fake LLM JSON — which is what the audit called
    out as "no cognition steers any Life". With `steering="llm"` it becomes the first
    REAL steering slice (audit #8): whenever the rule finds TWO OR MORE branches
    simultaneously admissible (`decide_candidates`), a real LLM picks among exactly
    those; a single candidate stays scripted (no call — the fast loop is not the LLM's
    place), and an answer outside the candidate list falls back to the rule's own
    first choice. The model can never mint an option the rule did not admit — the
    closed-vocabulary discipline (B4), applied to the orchestrator.

    Every LLM consult is appended to `life.steering_log` as
    `(candidates, chosen, used_llm)` so a live gate can audit the choices afterwards.
    """

    def __init__(self, life: "WarriorLife", llm=None) -> None:
        self._life = life
        self._llm = llm

    def _pick(self) -> str | None:
        cands = list(self._life.candidates)
        if not cands:
            return None
        if self._llm is None or len(cands) < 2:
            return cands[0]
        try:
            reply = self._llm.complete(
                "You steer a UO character's economy. Answer with EXACTLY one word: "
                "one item from the allowed list. No punctuation, no explanation.",
                "The character can do any ONE of these right now, all currently "
                f"possible and safe: {', '.join(cands)}. Which single one should it "
                "do first?",
            )
            token = str(reply).strip().strip('."\'` ').lower()
            chosen = next((c for c in cands if c == token or c in token), None)
        except Exception:  # noqa: BLE001 — a steering consult must never break the life
            chosen = None
        used = chosen is not None
        if chosen is None:
            chosen = cands[0]  # closed vocabulary: an invalid answer changes nothing
        self._life.steering_log.append((tuple(cands), chosen, used))
        return chosen

    def complete(self, system: str, user: str) -> str:
        cap = self._pick()
        if cap is None:
            return '{"schema":1,"decision":"idle"}'
        return '{"schema":1,"decision":"capability","capability":"%s"}' % cap


class _CachingBody:
    """Wraps a body, remembering the LAST observation so the orchestrator can read the
    current world state to decide the mode WITHOUT issuing its own extra `observe()`. An
    extra pump inserted between an agent's own observe and its next action breaks the
    inner agent's non-blocking route / reflex cadence (live-caught: the warrior stopped
    equipping and never engaged prey). The inner agent's own observe populates
    `last_obs`, so the decision is free."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.last_obs: Observation | None = None
        #: True once this tick's real observation has been taken, so a SECOND observe in
        #: the same tick is served from cache instead of costing another pump.
        self._fresh = False

    @property
    def connected(self) -> bool:
        return self.inner.connected

    @property
    def ready(self):
        return self.inner.ready

    def observe(self) -> Observation:
        """One real pump per tick. An agent runner typically observes twice per tick (the
        agent's own observe inside `tick()`, then the runner's for status/telemetry); on a
        single-threaded shard shared by several live agents those pumps are the scarce
        resource, so the second observe of a tick returns THIS tick's cached world state
        rather than paying for another. `WarriorLife.tick()` opens each tick by clearing
        the flag, so every tick still starts from a genuinely fresh observation."""
        if self._fresh and self.last_obs is not None:
            return self.last_obs
        self.last_obs = self.inner.observe()
        self._fresh = True
        return self.last_obs

    def begin_tick(self) -> None:
        self._fresh = False

    def act(self, action) -> None:
        self.inner.act(action)


class WarriorLife:
    """Autonomous hunt <-> re-arm orchestrator for a swordsman (see module docstring)."""

    #: The mode decision. A subclass points this at its own profession's rule; the
    #: rest of the orchestrator (two agents, separate memories, the hysteresis, the
    #: caching body, the Agent-compatible surface) is profession-agnostic.
    decide = staticmethod(decide_mode)
    #: The rule's full admissible set (a list, priority order). Subclasses with a real
    #: multi-branch rule (the mage) point this at their own `decide_candidates`; the
    #: default derives a one-element set from `decide`, so steering is a no-op for
    #: professions that have not opted in.
    decide_all = None

    #: Per-class default for the `bank_reserve` the constructor writes; subclasses
    #: override with their own derived reserve.
    DEFAULT_BANK_RESERVE = BANK_RESERVE

    def __init__(self, body, persona: Persona, profession: str = "swordsman",
                 routes: dict | None = None, *,
                 bank_reserve: int | None = None,
                 econ_grace: int = ECON_GRACE,
                 steering: str = "scripted") -> None:
        prof = PROFESSIONS[profession]
        #: Steering evidence: every LLM consult as (candidates, chosen, used_llm).
        self.steering_log: list[tuple[tuple[str, ...], str, bool]] = []
        #: The rule's full admissible set this tick (see `decide_candidates`).
        self.candidates: list[str] = []
        llm = None
        if steering == "llm":
            # A real slow-loop brain, degrading HONESTLY: if no provider builds, the
            # life stays scripted and says so, rather than dying or pretending.
            try:
                from .llm import build_tiered_clients, with_role
                llm = with_role(build_tiered_clients()["cheap"], "steering")
                print(f"  steering: LLM ({type(llm).__name__}) picks when 2+ branches are admissible")
            except Exception as e:  # noqa: BLE001
                print(f"  steering: requested llm but none built ({e}); staying scripted")
        self._steering_llm = llm
        # Wrap the body so the inner agents' own observes cache the world state; the
        # orchestrator then decides the mode off that cache with no extra pump.
        self.body = _CachingBody(body)
        #: Vendor routes the economy leg needs (weapon_vendor_spot / healer_spot /
        #: banker_spot). `decide_mode` reads them; the economy agent's memory carries
        #: them so its buy/bank FSMs can navigate.
        self.routes: dict = dict(routes) if routes else {}
        self.hunt_agent = Agent(body=self.body, persona=persona, planner=prof.planner())
        self.econ_agent = Agent(
            body=self.body, persona=persona,
            planner=prof.planner(capability_goals=True),
            cognition=CapabilityCognition(_LifeClient(self, llm), profession),
            cognition_interval=1, profession=profession,
            goal_policy=CapabilityPolicy(profession),
        )
        # SEPARATE memories (no shared dict). The economy agent gets the vendor routes.
        self.econ_agent.memory.update(self.routes)
        # The tuning knobs (audit proposal 5), exposed for genome axes / bandit tuning
        # / slow-loop steering. `bank_reserve` goes into the econ memory - the ONE key
        # the rule, the bank gate, and BankGold's FSM all read, AND every reader goes
        # through `market._bank_reserve`'s clamp — one key alone was not enough: a
        # malformed value (negative, float) read raw by the rule but clamped by the
        # gate recreated the drift class through this very knob (review-caught).
        self.econ_agent.memory["bank_reserve"] = (
            self.DEFAULT_BANK_RESERVE if bank_reserve is None else bank_reserve)
        self.econ_grace = econ_grace
        self.mode = "hunt"
        self.target_cap: str | None = None
        self._econ_streak = 0
        self._profession_key = profession
        self._disagree_streak = 0
        #: `(capability_id, streak)` once a rule-vs-gate disagreement has persisted for
        #: `DISAGREEMENT_TICKS`; `None` while healthy. Runners print this loudly — six
        #: live failures were exactly this state with no outward signature.
        self.rule_gate_disagreement: tuple[str, int] | None = None

    def set_leash(self, home: tuple[int, int], leash: int | None = None) -> None:
        """Bound idle wandering on BOTH agents.

        Each agent owns its own memory (deliberately — they coordinate through the
        world, not a shared dict), and BOTH planners end in `Wander`, which reads the
        memory of whichever agent is ticking. Setting a leash on one of them therefore
        leashes the agent only while it happens to be in that mode.

        Live-caught with the carpenter, whose profession has no work skill and so runs
        the ECONOMY agent nearly every tick: its leash was written to the hunt agent's
        memory, the economy agent wandered free, and it drifted three tiles off its
        supply drop onto ground it could not walk back from. Its `fetch_boards` goal
        stayed correctly admitted and ready the whole time, walking into a wall.
        """
        for memory in (self.hunt_agent.memory, self.econ_agent.memory):
            memory["wander_home"] = home
            if leash is not None:
                memory["wander_leash"] = leash

    def set_route(self, key: str, value) -> None:
        """Configure a vendor route on both the decision inputs and the economy agent."""
        self.routes[key] = value
        self.econ_agent.memory[key] = value

    def tick(self):
        # Tick the CURRENT mode's agent (it observes + acts), then decide the NEXT tick's
        # mode from the observation IT just cached — no extra pump (an extra observe
        # around the inner agent's own tick breaks its route/reflex cadence; live-caught).
        # The one-tick lag on the mode decision is immaterial (modes change slowly) and
        # `self.mode` starts at "hunt", so the very first action is a hunt action.
        self.body.begin_tick()  # this tick's first observe pumps for real
        action = (self.econ_agent if self.mode == "economy" else self.hunt_agent).tick()
        obs = self.body.last_obs
        if obs is None:
            return action
        # The rule reads the ECON AGENT'S memory - the very dict the gates read - so a
        # knob written at construction (bank_reserve) or a route added later is seen
        # by both sides by construction, never by synchronization.
        mode, cap = self.decide(obs, self.econ_agent.memory)
        # Hysteresis: only commit to the economy after the condition PERSISTS for
        # ECON_GRACE ticks, so a transient mid-equip "weaponless" blip doesn't yank the
        # warrior off wielding a blade it already owns (which would strand it on the
        # cursor). Genuine loss/low-supply persists and switches.
        if mode == "economy":
            self._econ_streak += 1
            if self._econ_streak < self.econ_grace:
                mode, cap = "hunt", None
        else:
            self._econ_streak = 0
        self.mode, self.target_cap = mode, cap
        # The admissible SET, for the steering client: the rule's own candidates when
        # the profession exposes them, else exactly the one capability `decide` chose.
        if self.mode == "economy":
            if type(self).decide_all is not None:
                self.candidates = list(type(self).decide_all(obs, self.econ_agent.memory))
            else:
                self.candidates = [cap] if cap else []
        else:
            self.candidates = []
        self._detect_disagreement(obs)
        return action

    def _detect_disagreement(self, obs) -> None:
        """Self-report the stall this project kept paying to discover live.

        The shape: the rule WANTS an economy capability, no goal is on the stack, and the
        capability's own readiness gate refuses — for `DISAGREEMENT_TICKS` straight. Each
        of the six documented live failures sat in exactly this state, outwardly
        indistinguishable from an agent at work, until a bespoke instrumentation round
        named it. The no-goal guard is mandatory: gates de-assert mid-transaction by
        design, so a naive want-but-not-ready check false-fires on every healthy buy.

        Pure reads over this tick's cached observation — no extra pump.
        """
        from .capabilities import ready_capability_ids
        from .skills.base import SkillContext

        disagreeing = False
        if self.mode == "economy" and self.target_cap is not None \
                and self.econ_agent.goal_stack.current is None:
            try:
                ctx = SkillContext(obs=obs, persona=self.econ_agent.persona,
                                   memory=self.econ_agent.memory)
                disagreeing = self.target_cap not in ready_capability_ids(
                    self._profession_key, ctx)
            except Exception:  # noqa: BLE001 — a detector must never break the life
                disagreeing = False
        self._disagree_streak = self._disagree_streak + 1 if disagreeing else 0
        if self._disagree_streak >= DISAGREEMENT_TICKS:
            self.rule_gate_disagreement = (self.target_cap, self._disagree_streak)
            self._clear_stale_ui(obs)
        else:
            self.rule_gate_disagreement = None

    def _clear_stale_ui(self, obs) -> None:
        """Close a gump nobody owns — the detector as a REPAIR, not just a report.

        Sixteen readiness gates share one "idle UI" clause (no gumps, no popup, no
        shop window, no cursor), so a single surface left open refuses EVERY
        capability at once: the ready set goes empty, the rule keeps wanting, and the
        Life stands still with material at its feet. forge15 live, 2026-07-31: Pim
        wanted `fetch_iron` for 156 ticks with 38 ingots on the ground and `ready=[]`.

        Safe by the detector's own precondition: firing requires NO goal on the stack
        for `DISAGREEMENT_TICKS` straight, so no capability owns the surface being
        closed — a mid-transaction gump belongs to a live goal and is never touched
        here. Only gumps are closable through the contract (`GumpResponse` button 0,
        the craft FSM's own close); a stale shop window has no close action and is
        left to report itself through the same loud line.
        """
        gumps = getattr(obs, "gumps", None)
        if not gumps:
            return
        from .contract import GumpResponse

        gump = gumps[0]
        self._stale_ui_closes = getattr(self, "_stale_ui_closes", 0) + 1
        print(f"  ** {self.persona.name}: closing an unowned gump "
              f"(id={gump.gump_id}) — it was refusing every capability **")
        self.body.act(GumpResponse(gump.serial, gump.gump_id, button=0))

    # --- Agent-compatible surface, so any agent runner (e.g. village._run_worker)
    # drives a WarriorLife unchanged. The HUNT agent is the primary: it does the
    # living, so its persona/episodes/memory are what status + chronicle read. ---
    @property
    def persona(self) -> Persona:
        return self.hunt_agent.persona

    @property
    def episodes(self):
        return self.hunt_agent.episodes

    @property
    def memory(self) -> dict:
        return self.hunt_agent.memory

    @property
    def ticks(self) -> int:
        return self.hunt_agent.ticks + self.econ_agent.ticks

    @property
    def kills(self) -> int:
        """Corpses this warrior has looted (~its kills) — `Hunt` records each in
        `hunt_looted`. Lets a village driver drive a kills-based prey respawn."""
        return len(self.hunt_agent.memory.get("hunt_looted", ()))
