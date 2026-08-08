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
ticked — EXCEPT that a live goal frame holds the economy mode until it retires OR
outlives its own deadline (see `tick`: the rule cannot see the goal stack, so it answers
"hunt" on the very tick a transaction completes, and only the economy agent's own ticks
can finish one; a frame past its budget has stopped being a transaction and releases the
hold, so the Life is never pinned by one). The economy agent's cognition is SYNCHRONOUS
on purpose (the async ThreadedCognition races and intermittently never proposes the goal
— a live-caught flake).
"""

from __future__ import annotations

from .agent import Agent
from .capabilities import CapabilityPolicy, _valid_spot
from .capability_cognition import CapabilityCognition
from .contract import Observation
from .knobs import knob_param
from .obsview import owns, pack_amount, pack_has
from .persona import Persona
from .profession import PROFESSIONS
from .skills.market import _bank_reserve
from .skills.hunt import GOLD_GRAPHIC
from .skills.warrior import (
    BANDAGE_GRAPHICS,
    PLATE_ARMOR_LAYERS,
    UPGRADE_RESERVE,
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
#: RE-EXPORTED from `skills/warrior.py`, where the equation "an upgrade must leave a chest
#: plate's worth behind" is now stated ONCE — the `upgrade_weapon` gate reads the same name
#: (audit follow-up 6). It was written here AND in `capabilities.py`, with near-identical
#: comments: numerically locked, but the DECISION was recorded twice.
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
#: Require the economy condition to PERSIST this many ticks before switching. This
#: filters the 1-2 tick transient where a blade is on the cursor mid-equip (gone from
#: `items`, so it momentarily reads "weaponless") — diverting to the economy then would
#: interrupt EquipWeapon and strand the blade. During the grace the hunt agent keeps
#: running, so it wields an OWNED blade first; only a genuine loss survives the grace.
#: Overridable per instance via `WarriorLife(..., econ_grace=...)`, clamped to at least 2
#: — the smallest window that grants ANY hysteresis (see the constructor).
#:
#: ONE EXCEPTION, and it is deliberate: a transaction the orchestrator HELD the economy
#: mode for (see `tick`) pins the streak at this value while it lasts, so the tick it
#: retires commits straight back to the economy if the rule wants it. A completed
#: transaction is not the mid-equip transient this knob filters — it IS the commitment —
#: so it does not re-earn the hysteresis. Tuning this value therefore does not lengthen
#: the gap after a held transaction; it lengthens only the un-held entry.
ECON_GRACE = 6
#: Consecutive ticks the rule may WANT an economy capability that admission never grants
#: (no goal on the stack, capability absent from the ready set) before the orchestrator
#: flags a rule-vs-gate disagreement. High enough that a healthy transaction can never
#: trip it: ready gates deliberately de-assert MID-transaction (a buy in flight holds a
#: goal and shows not-ready), which is why the no-goal guard below is mandatory, not an
#: optimization.
#:
#: Overridable per instance via `WarriorLife(..., disagreement_ticks=...)` — the §E
#: "retry policy" axis, and RULE-ONLY: nothing outside this module reads it (no gate, no
#: skill), so tuning it cannot pull the rule away from a gate. It is read off `self`, not
#: as a module global, precisely so a genome axis can move it; the module constant stays
#: the default, so an untuned Life behaves exactly as before.
DISAGREEMENT_TICKS = 10
#: How many stale-UI closes ONE overdue frame may buy itself before the orchestrator
#: stops extending the hold for it at all (see `tick`'s third bound). `_clear_stale_ui`
#: knows exactly three closable surfaces — a gump, a vendor BUY window, a vendor SELL
#: window — so three is one per surface class: enough to clear everything a finished
#: trip can leave behind, and no more. A fourth close would mean the surface is being
#: RE-OPENED rather than left behind, which is not a stale surface and not this repair's
#: to fix; holding for it would be exactly the unbounded hold the bound exists to
#: prevent. Not a knob: it is a property of `_clear_stale_ui`'s own case list, so it
#: moves only when that list does.
OVERDUE_REPAIRS = 3


# The pack/worn readbacks this rule used to hand-roll — `_backpack`, `_pack_amount`,
# `_has_weapon`, `_has_chest`, `_pack_has_sword` — now come from `obsview`, which is the
# single definition all five Lives share. `owns(..., layer=...)` is exactly what
# `_has_weapon`/`_has_chest` were: a sword worn at the ONE-HANDED layer (any other worn
# plate is not "wearing a chest") OR sitting in the pack, just bought and not yet
# equipped. `_worn_blade_rank` below stays here — it is not a readback anybody else
# duplicated, and it needs this profession's own `SWORD_RANK`.


def _worn_blade_rank(obs: Observation) -> int | None:
    """Rank of the sword currently WIELDED, or `None` if bare-handed."""
    ranks = [
        SWORD_RANK.get(i.graphic, 0)
        for i in obs.items
        if i.graphic in SWORD_GRAPHICS
        and i.layer == WEAPON_LAYER and i.container == obs.player.serial
    ]
    return max(ranks) if ranks else None


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
    gold = pack_amount(obs, GOLD_GRAPHIC)
    if (not owns(obs, SWORD_GRAPHICS, layer=WEAPON_LAYER) and gold >= WEAPON_PRICE
            and _valid_spot(memory.get("weapon_vendor_spot"))):
        return "economy", "buy_weapon"
    if pack_amount(obs, BANDAGE_GRAPHICS) < LOW_BANDAGES and gold >= BANDAGE_BATCH_COST \
            and _valid_spot(memory.get("healer_spot")):
        return "economy", "buy_bandage"
    if (not owns(obs, PLATE_CHEST_GRAPHIC, layer=PLATE_ARMOR_LAYERS[PLATE_CHEST_GRAPHIC])
            and gold >= ARMOR_PRICE and _valid_spot(memory.get("armorer_spot"))):
        return "economy", "buy_armor"
    # Growth, once the necessities are covered: trade a weaker worn blade up to the
    # vendor's best, but only with surplus beyond a re-arm reserve, and only while the
    # pack holds no sword (the arrival proof requires it to start empty of them).
    worn_rank = _worn_blade_rank(obs)
    if (worn_rank is not None and worn_rank < UPGRADE_TARGET_RANK
            and not pack_has(obs, SWORD_GRAPHICS)
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

    #: The ALLOWLIST for the tuning channel: exactly the constructor parameters that are
    #: knobs, i.e. that route through `anima2/knobs.py`'s clamp. A subclass with its own
    #: knob extends it (`TinkerLife.KNOBS`), never replaces it.
    #:
    #: `LifeSpec.knobs` splats into this constructor, and the other parameters it can
    #: reach are NOT knobs — they are identity. `profession` is the worst of them, and
    #: review-caught: a spec built for the carpenter with `knobs={"profession": "mage"}`
    #: kept `spec.profession="carpenter"` for staging, the worker label and the
    #: `ready=` telemetry list while the Life got `_profession_key="mage"` for its goal
    #: policy, its capability cognition and its disagreement detector — with `decide`
    #: still the CARPENTER rule. That is the rule-vs-gate drift class this whole change
    #: set exists to kill, rebuilt out of the channel that was supposed to be the safe
    #: way to tune. It is not a contrived key either: `foundry/archive.py::Genome`'s
    #: first axis is literally named `profession`, and the genome is the searcher this
    #: channel is being built for. `steering` is excluded for the milder version of the
    #: same reason: it is a cognition-tier switch that builds a real LLM client at
    #: construction time, and it is clamped by nothing.
    KNOBS: frozenset[str] = frozenset({"bank_reserve", "econ_grace", "disagreement_ticks",
                                       "wander_leash"})

    def __init__(self, body, persona: Persona, profession: str = "swordsman",
                 routes: dict | None = None, *,
                 bank_reserve: int | None = None,
                 econ_grace: int | None = None,
                 disagreement_ticks: int | None = None,
                 wander_leash: int | None = None,
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
        # The two RULE-ONLY knobs — nothing outside this module reads either, so they
        # ride instance attributes rather than memory keys (`decide` is a staticmethod
        # over `(obs, memory)` and can only see a knob that is a KEY; a knob only
        # `tick()`/a method reads must not be one, or the rule cannot find it). Both go
        # through `knobs.knob_param`, the same clamp `bank_reserve` uses, so a genome
        # axis exploring a negative value gets a floor instead of a Life that switches
        # instantly (`econ_grace`, floor 2) or self-reports a disagreement every single
        # tick and closes a UI surface on each one (`disagreement_ticks`, floor 1).
        #
        # Each floor is the SMALLEST value that still does the job its knob exists for,
        # and NEITHER is `knob_param`'s natural 0. For `econ_grace`, 1 is no better than
        # 0: `_econ_streak` is incremented BEFORE `self._econ_streak < self.econ_grace`
        # is tested, so at 0 and at 1 alike the orchestrator commits to the economy on
        # the FIRST tick the rule asks for it — no hysteresis at all, which is the
        # mid-equip window ECON_GRACE was added for (blade on the cursor, absent from
        # `items`, so the rule reads "weaponless" and wants `buy_weapon`): switching
        # there interrupts EquipWeapon, strands the blade and buys a second sword.
        # Review-caught, because floor 0 shipped here first and EVERY malformed write
        # (`-1`, `2.5`, `True`, `"6"`) landed on it — the clamp this comment already
        # claimed was a floor was a behavioral no-op, and worse than the plain `int`
        # parameter it replaced, which at least raised loudly on a string.
        self.econ_grace = knob_param(econ_grace, ECON_GRACE, floor=2)
        #: Ticks a want-vs-refuse standoff must persist before it is reported+repaired.
        self.disagreement_ticks = knob_param(disagreement_ticks, DISAGREEMENT_TICKS,
                                             floor=1)
        #: §E's "exploration radius", and the cheapest axis on audit follow-up 4's list.
        #: A MEMORY KEY like `bank_reserve` — `Wander` is a skill and reads `ctx.memory`,
        #: so an instance attribute would be invisible to it — written RAW here and
        #: clamped by its one reader (`skills/movement.py::wander_leash`), the same split
        #: `bank_reserve` uses.
        #:
        #: It is the only knob with a PRECEDENCE question, because it is the only one
        #: something else already writes: every runner calls `set_leash(home, derived)`
        #: after construction with a value derived from the world (Sten's is
        #: `min(max(1, shop_reach), PICKUP_RADIUS - 1)`, live-caught after he drifted off
        #: his own supply drop). A tuned knob that a later derived write silently
        #: overwrote would be a channel that reports success and changes nothing — the
        #: exact defect this whole thread has been closing. So the TUNED value wins, and
        #: `_leash_tuned` is how `set_leash` knows the difference between "nobody has set
        #: this yet" and "a caller chose it".
        self._leash_tuned = wander_leash is not None
        #: The staging leash a tuned value overrode, once `set_leash` has been called —
        #: `None` until then. Kept so a banner can report the override rather than only
        #: the winner, because several runners DERIVE their leash from a correctness
        #: constraint (`PICKUP_RADIUS - 1`, so a delivery stays fetchable) rather than
        #: from taste, and a tuned value above it silently stops the chain closing.
        self._leash_derived: int | None = None
        if self._leash_tuned:
            for memory in (self.hunt_agent.memory, self.econ_agent.memory):
                memory["wander_leash"] = wander_leash
        self.mode = "hunt"
        self.target_cap: str | None = None
        #: True while the orchestrator is finishing a transaction the rule stopped
        #: wanting (see `tick`). Telemetry marks it `+hold`; a hold that is progressing
        #: is not a fault. A hold that is NOT progressing sets `frame_overdue` below and
        #: is released, so `+hold` alone never means "stuck".
        self.holding_frame = False
        #: True whenever the frame on the economy stack has outlived its own deadline —
        #: read off the FRAME and the economy agent's clock alone, never gated on the
        #: hold. Gating it on the hold was review-caught: it blinded the report in
        #: exactly the half of the wedge where the rule still happened to want the
        #: capability, and what the rule wants has nothing to do with why the frame
        #: cannot finish. It is both the report AND the hold's third bound (see `tick`).
        self.frame_overdue = False
        #: `{frame id: closes}` — stale-UI closes already spent on an overdue frame, so
        #: the repair that extends the hold is capped per frame (`OVERDUE_REPAIRS`).
        self._overdue_repairs: dict[int, int] = {}
        self._econ_streak = 0
        self._profession_key = profession
        self._disagree_streak = 0
        #: `(capability_id, streak)` once a rule-vs-gate disagreement has persisted for
        #: `self.disagreement_ticks`; `None` while healthy. Runners print this loudly — six
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

        `home` is ALWAYS written; it is not a knob and has no contest.

        **A tuned `wander_leash` outranks the STAGING leash only — the FIRST explicit
        `leash` this Life is given — and every later call wins outright.** The first
        version made the tuned value outrank every call for the Life's lifetime, which
        was review-caught as breaking a real caller: `live_frame_overdue_gate.py`
        teleports the tinker and then re-leashes it to the new spot, with a stated
        invariant ("if `Survive` ever let go, `Wander` would walk the tinker back onto
        its craft spot and the rule would want the economy again on its OWN account,
        silently un-engaging the hold"). Against a tuned Life that re-leash became a
        no-op and the gate could report a bogus pass. Nothing tunes that gate today, so
        this was latent, not live.

        The split is the honest one: a runner's staging call passes a value DERIVED from
        the world (`min(max(1, shop_reach), PICKUP_RADIUS - 1)`) as a default, and a
        tuning channel whose value the next line overwrites is worse than no channel
        because it reports success. A LATER call is a decision somebody made at runtime,
        and a knob set before the run began has no business overriding one.
        """
        for memory in (self.hunt_agent.memory, self.econ_agent.memory):
            memory["wander_home"] = home
            if leash is not None and not self._leash_tuned:
                memory["wander_leash"] = leash
        if leash is not None:
            #: What the staging call ASKED for, kept so a banner can say that a tuned
            #: value overrode it and by how much (see `village`'s staged lines): several
            #: derived leashes are correctness constraints, not preferences.
            if self._leash_derived is None:
                self._leash_derived = leash
            # Consumed: the tuned value has now outranked the staging default once, and
            # anything after this is a runtime decision.
            self._leash_tuned = False

    def set_route(self, key: str, value) -> None:
        """Configure a vendor route on both the decision inputs and the economy agent."""
        self.routes[key] = value
        self.econ_agent.memory[key] = value

    def tick(self):
        # Tick the CURRENT mode's agent (it observes + acts), then decide the NEXT tick's
        # mode from the observation IT just cached — no extra pump (an extra observe
        # around the inner agent's own tick breaks its route/reflex cadence; live-caught).
        # The one-tick lag on the mode decision is immaterial (modes change slowly) and
        # `self.mode` starts at "hunt", so the very first action is a hunt action. Exactly
        # ONE inner agent is ticked per orchestrator tick, and that is load-bearing: two
        # would split `new_journal` (a since-last-observe delta) between them, act twice
        # on one body from two agents that each believe they own movement, and halve every
        # capability deadline's wall-clock life. Which one gets the tick is `self.mode`,
        # so the exit-edge rule below — not a second agent — is how an owed transaction
        # gets finished.
        self.body.begin_tick()  # this tick's first observe pumps for real
        # Which inner agent that ONE tick went to, remembered so the Agent-compatible
        # surface below can forward its `last_skill_name`. `self.mode` is reassigned
        # further down before this tick ends, so a later reader cannot reconstruct it —
        # and the work-liveness alarm in `village._run_worker` needs to know whether the
        # skill that just ran was idle-by-design (`wander` while hunting with no prey,
        # `capability_wait` with nothing admitted) or real work. No Life subclass
        # overrides `tick`, so every one of the five gets this by construction.
        self._ticked_agent = self.econ_agent if self.mode == "economy" else self.hunt_agent
        action = self._ticked_agent.tick()
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
        # FINISH WHAT WE STARTED — the EXIT edge needs its own rule, and it is not the
        # entry hysteresis mirrored. `decide` is a pure function of (obs, memory): it
        # structurally cannot see the goal stack, so it answers "hunt" the instant the
        # world-fact it keyed on flips — and for a sale that instant is the moment the
        # vendor TAKES the item, i.e. MID-transaction, with the walk home still owed.
        # Every economy branch of all five Lives is keyed on state its own transaction
        # changes (the goods leave the pack, the coin moves, the pile is lifted), so
        # this is structural, not the carpenter's.
        #
        # Leaving economy there freezes THREE things at once, because all three only
        # advance inside `Agent.tick`: the capability FSM, the `cap_*_finished` markers
        # it sets (retirement always needs at least one more econ tick after them —
        # every capability step returns RUNNING, `CapabilityGoalComplete` is a planner
        # skill selected on a LATER tick), and `GoalStack.expire_due`, whose deadline is
        # counted in that same agent's ticks. Live 2026-08-03: a `sell_furniture` frame
        # sat mid-`sell_return` at `mkt_phase='sell'` for 280 ticks with the econ agent's
        # counter pinned at 5, while the status line said `admitted=sell_furniture` and
        # nothing was executing it (docs/AUDIT-2026-07-29.md).
        #
        # THE HOLD IS BOUNDED THREE TIMES, and the third bound is the orchestrator's own
        # because the first two are NOT general:
        #  1. the FSM's give-up ladder returns `mkt_phase` to "craft" and sets
        #     `cap_run_finished_goal_id`, which `CapabilityGoalComplete` closes as a
        #     FAILURE. This is the usual exit, but it exists for exactly TWO capability
        #     families: only `SellItemCapability` and `BankGoldCapability` ever write that
        #     marker (`skills/market.py`). Every buy / tool-buy / craft / fetch / process /
        #     deliver frame has no ladder at all.
        #  2. the frame's own deadline, via `GoalStack.expire_due` — reachable only
        #     because the hold keeps the economy agent ticking, since that deadline is
        #     counted in ITS ticks. But `CapabilityPolicy.deadline_can_expire` defers at
        #     an unsafe yield point, and EVERY `*_can_yield` in `capabilities.py` carries
        #     the same unconditional "idle UI" clause. One unowned gump (forge15's wedge)
        #     therefore holds bound 2 open forever, and it holds bound 1 open too, since
        #     the readiness gates share that clause.
        #  3. so: OVERDUE RELEASES THE HOLD. Measured, review-caught, on all five Lives:
        #     with a gump nobody owns plus any non-sell/non-bank frame, bounds 1 and 2 are
        #     both blocked at once and the Life was pinned in economy mode for 3000 ticks
        #     emitting nothing — a total livelock where the pre-hold code merely carried a
        #     zombie frame and kept hunting. A safety interrupt (`WarriorSurvive` sits
        #     above the capability skills in the capability planner too) starves the FSM
        #     the same way with no surface to blame at all. So the release must NOT depend
        #     on the FSM being stepped, which is precisely what those states withhold: it
        #     depends only on the frame's deadline and the economy agent's own clock, both
        #     of which the hold itself keeps advancing. Worst case is now the frame's own
        #     budget in orchestrator ticks, after which the Life is exactly the pre-hold
        #     Life — a stale frame, but alive.
        #
        # The one extension: an overdue frame first gets `_clear_stale_ui` pointed at it.
        # A frame past its FULL budget that still cannot yield has forfeited the "a
        # mid-transaction gump belongs to a live goal" premise that otherwise keeps that
        # repair away from a live frame, and closing the surface is what makes bounds 1
        # and 2 reachable again — so when a close lands, the hold is extended one tick to
        # let the economy agent USE it (the frame then expires on its next tick and the
        # stack comes back clean, instead of the Life hunting on beside a wedged surface).
        # Capped at `OVERDUE_REPAIRS` per frame so the extension cannot itself run away.
        #
        # DEATH IS THE OTHER OVERRIDE, and it covers the whole EPISODE, not just the
        # ghost window: `RecoverDeath` is a WORK-planner reflex that runs on
        # `dead OR death_waiting_resurrection OR death_corpse_pending`, and the corpse
        # RUN happens entirely after `obs.player.dead` goes false. Keying the override on
        # `dead` alone took the body away from the corpse leg the tick after
        # resurrection and deferred gear recovery by up to the frame's whole budget
        # (measured: 177 ticks for a warrior) — the naked death-loop this module exists
        # to end, review-caught. The frame simply waits (nobody ticks it, and telemetry
        # says so with `!frozen`); the hold resumes once the episode closes.
        frame = self.econ_agent.goal_stack.current
        self.frame_overdue = bool(
            frame is not None and frame.deadline_tick is not None
            and self.econ_agent.ticks > frame.deadline_tick)
        repaired = self.frame_overdue and self._repair_overdue_frame(obs, frame)
        holding = (mode != "economy" and frame is not None
                   and not self._death_episode_open(obs)
                   and (not self.frame_overdue or repaired))
        if holding:
            mode = "economy"
            # The transaction IS the commitment `econ_grace` exists to establish, so the
            # entry hysteresis is not re-earned the moment it retires; without this the
            # Life drops into up to `econ_grace` wander ticks after EVERY transaction
            # whose rule-side want expired mid-flight — which is every one of them.
            self._econ_streak = max(self._econ_streak, self.econ_grace)
        self.holding_frame = holding
        # `cap` is deliberately NOT rewritten to the frame's capability: `want=` must
        # stay the RULE's own answer, or fixing the `admitted=` lie re-creates the same
        # ambiguity on the `want=` side that `telemetry_line`'s docstring says cost
        # three runs and one wrong root cause.
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

    def _death_episode_open(self, obs) -> bool:
        """Is a death still being recovered from — ghost window OR corpse run?

        `RecoverDeath.can_run` is `dead OR death_waiting_resurrection OR
        death_corpse_pending`, and the last of those is set only AFTER the resurrection
        lands, so `obs.player.dead` covers barely the first half of an episode. It is
        the HUNT agent's memory that is read, deliberately: the two agents keep separate
        memories by design, `decide` sends every dead tick to the hunt agent, and so the
        hunt agent is the only one that ever owns a death episode. Reading the economy
        agent's copy instead would consult keys nothing ever writes; reading both would
        let one stale key there suppress the hold forever.
        """
        return bool(
            obs.player.dead
            or self.hunt_agent.memory.get("death_waiting_resurrection")
            or self.hunt_agent.memory.get("death_corpse_pending"))

    def _repair_overdue_frame(self, obs, frame) -> bool:
        """Point the stale-UI repair at an overdue frame; True if a surface was closed.

        The licence is the frame's own budget: a capability that has spent ALL of it
        without reaching a yield point is not mid-transaction in any sense a surface can
        belong to, which is the one premise `_detect_disagreement`'s no-goal guard is
        protecting (see `_clear_stale_ui`). The two callers can never collide — that one
        requires an EMPTY goal stack, this one requires a live frame.

        Capped at `OVERDUE_REPAIRS` closes per frame, because a close here also buys the
        hold one more tick, and an uncapped repair-and-extend against a surface that
        keeps re-opening would be the unbounded hold in a new costume.
        """
        spent = self._overdue_repairs.get(frame.id, 0)
        if spent >= OVERDUE_REPAIRS:
            return False
        if not self._clear_stale_ui(obs):
            return False
        # Frame ids are monotonic and a retired frame is never revisited, so the ledger
        # is REPLACED rather than added to: it holds the live frame alone and cannot
        # grow for the lifetime of the process.
        self._overdue_repairs = {frame.id: spent + 1}
        return True

    def _detect_disagreement(self, obs) -> None:
        """Self-report the stall this project kept paying to discover live.

        The shape: the rule WANTS an economy capability, no goal is on the stack, and the
        capability's own readiness gate refuses — for `self.disagreement_ticks` straight. Each
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
        if self._disagree_streak >= self.disagreement_ticks:
            self.rule_gate_disagreement = (self.target_cap, self._disagree_streak)
            self._clear_stale_ui(obs)
        else:
            self.rule_gate_disagreement = None

    def _clear_stale_ui(self, obs) -> bool:
        """Close a gump nobody owns — the detector as a REPAIR, not just a report.

        Returns True when a surface was actually closed (`_repair_overdue_frame` reads
        that; `_detect_disagreement` does not care).

        Sixteen readiness gates share one "idle UI" clause (no gumps, no popup, no
        shop window, no cursor), so a single surface left open refuses EVERY
        capability at once: the ready set goes empty, the rule keeps wanting, and the
        Life stands still with material at its feet. forge15 live, 2026-07-31: Pim
        wanted `fetch_iron` for 156 ticks with 38 ingots on the ground and `ready=[]`.

        TWO callers, and each brings its own proof that no capability owns the surface.
        `_detect_disagreement` brings the original: NO goal on the stack for
        `self.disagreement_ticks` straight, so nothing is mid-transaction. The second is
        `_repair_overdue_frame`, added when the same wedge was measured pinning a Life
        in economy mode indefinitely: a frame that has burned its ENTIRE deadline
        without once reaching a safe yield point has forfeited the "a mid-transaction
        gump belongs to a live goal" premise, and the open surface is the documented
        reason it can neither give up nor expire. A frame INSIDE its budget is still
        never touched. THREE surfaces are closable and all three have been seen live: a gump
        (`GumpResponse` button 0 — the craft FSM's own close), and a vendor BUY or
        SELL window, answered with an EMPTY item list. ServUO's `VendorBuyReply`
        replies to anything that is not flag 0x02 with `EndVendorBuy`, and the
        bridge already encodes an empty list as flag 0x00
        (`anima-core::net::outgoing::build_buy`) — so "buy nothing" IS the close.
        forge16 (2026-07-31) caught that version: 200 disagreement ticks with
        `ui=shopbuy` left behind by a finished `buy_iron` trip. A stale target
        CURSOR is left alone: the body's own target state is not the Life's to
        clear, and cancelling one mid-flight would race the skill that opened it.
        """
        from .contract import BuyItems, GumpResponse, SellItems

        gumps = getattr(obs, "gumps", None)
        shop_buy = getattr(obs, "shop_buy", None)
        shop_sell = getattr(obs, "shop_sell", None)
        if gumps:
            surface = f"gump id={gumps[0].gump_id}"
            action = GumpResponse(gumps[0].serial, gumps[0].gump_id, button=0)
        elif shop_buy is not None:
            surface = "vendor BUY window"
            action = BuyItems(vendor=shop_buy.vendor, items=[])
        elif shop_sell is not None:
            surface = "vendor SELL window"
            action = SellItems(vendor=shop_sell.vendor, items=[])
        else:
            return False
        self._stale_ui_closes = getattr(self, "_stale_ui_closes", 0) + 1
        print(f"  ** {self.persona.name}: closing an unowned {surface} — "
              f"it was refusing every capability **")
        self.body.act(action)
        return True

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
    def last_skill_name(self) -> str | None:
        """The skill the LAST-TICKED inner agent ran — see `_ticked_agent` in `tick`.

        Not `hunt_agent`'s: a carpenter measured 2994 econ ticks against 6 hunt ticks
        over a 3000-tick offline run, so reading the hunt agent here would describe an
        agent that barely ran (its last skill would read `wander` for the whole window,
        which `village._doing_work` treats as idle — the alarm would go permanently
        silent on the Life that works hardest)."""
        agent = getattr(self, "_ticked_agent", None)
        return None if agent is None else agent.last_skill_name

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
