"""Professions — what an agent does for a living, and how the Control plane stages it.

A `Profession` bundles the Control-plane staging (skills to set, tools to give, a
workplace) with the work skill the brain runs. The village runner assigns a
profession (and, for resource jobs, a distinct workplace) to each agent.

All five professions below run live in `village.py`: miner (mine + smelt at a
staged forge), lumberjack (grove-aware chopping via the static-map tree finder),
fisher (casts at a calibrated water tile), blacksmith (gump-driven MAKE-loop
crafting), and townsfolk (no job — wander + greet). The framework is data-driven
so adding a new profession is just a row here plus a matching work `Skill`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .capabilities import (
    CAPABILITIES,
    issue_capability_planner_lease,
    policy_binding_for_context,
)
from .curriculum import (
    curriculum_can_yield,
    milestone_for,
    validate_curriculum_goal,
)
from .planner import Planner
from .skills import BlacksmithMarket, Chop, Fish, GoTo, Greet, Hunt, MineSmeltDeliver, RecoverDeath, Skill, SpeakPending, Survive, Wander
from .skills.base import SkillContext, SkillResult, Status

# anima v1's flood-fill-verified Minoc ore banks (foundry/kernel/gm.py LANE_SPOTS):
# walkable tiles with ~19 mineable tiles in reach, ≥33 apart so workers don't crowd.
MINING_SPOTS: list[tuple[int, int]] = [
    (2567, 493), (2611, 474), (2584, 411), (2551, 420), (2524, 532),
    (2608, 538), (2485, 550), (2698, 538), (2659, 538), (2500, 382),
]

# Vesper-bay fishing spots from the `find-water` tool (anima-net):
# `find-water 2899 676 40` → ((stand_x, stand_y), (water_x, water_y, water_z)).
# The fisher stands on shore and casts at the exact water tile (probing reach-4
# wastes ticks reaching far water, so we target the known tile directly).
FISHING_SPOTS: list[tuple[tuple[int, int], tuple[int, int, int]]] = [
    ((2866, 647), (2865, 646, -5)),
    ((2869, 639), (2868, 638, -5)),
    ((2876, 636), (2873, 633, -5)),
    ((2894, 636), (2898, 632, -5)),
    ((2901, 636), (2902, 635, -5)),
    ((2908, 636), (2912, 632, -5)),
    ((2908, 643), (2912, 639, -5)),
    ((2909, 650), (2913, 646, -5)),
]

# Smithy spots on the FLAT Britain plains — each gets its own forge + anvil. Flat
# ground matters: ServUO's forge/anvil proximity check fails if they settle at a
# different Z than the smith (a steep Minoc slope put forge z=20, anvil z=38).
BLACKSMITH_SPOTS: list[tuple[int, int]] = [
    (1500, 1600), (1508, 1600), (1500, 1608), (1508, 1608),
]

# Phase 3 trade-loop pairing: a miner and a blacksmith co-located so the
# miner's greedy (no-A*) delivery walk (`skills/smelt.py::MineSmeltDeliver`) is
# short and provably open ground the whole way. The Britain smithy spots above
# are ~1000 tiles from the Minoc ridge — far beyond a greedy walk (DESIGN.md
# §10 flags real inter-workplace commutes as a Phase 3 `navigate`/A* item) — so
# this instead puts the smithy right at the ridge's foot.
#
# Calibrated live via `GmControl` (see docs/PHASE3.md item 1 for the method
# and the dead ends it ruled out — notably `MINING_SPOTS[4]`, whose z=0
# pocket looked ideal but turned out to have **no live ore** in reach at all;
# static, offline "flood-fill verified" geometry isn't the same as a live
# ore check). `MINING_SPOTS[1]` (2611, 474) is confirmed-live ore ("You dig
# some iron ore...", Mining 35.0 → 35.1) *and*, unlike most of the ridge
# (e.g. `MINING_SPOTS[0]`, walled in on every side but one), has real open
# room around it: real (collision-checked) `Walk` — not `[Go` teleports,
# which skip collision — reaches 2 tiles due west without a hitch.
# `TRADE_SMITH_SPOT` sits there, with forge/anvil at the blacksmith
# profession's usual (0, -1)/(0, 1) offsets (north/south — see that
# profession's own comment on why: an east/west anvil would sit squarely in
# the miner's western approach and block it), both z=20 — matching the smith
# (no "steep Minoc slope" mismatch). Live-verified end to end (`live_trade.py`):
# the smith crafts its starting stock down to 0 and stalls, the miner mines,
# smelts, walks over, and drops its ingots, the smith picks them up and
# **crafts again** from that delivered metal — the full loop, no GM top-up
# after the initial staging.
TRADE_MINE_SPOT: tuple[int, int] = MINING_SPOTS[1]
TRADE_SMITH_SPOT: tuple[int, int] = (2609, 474)

# Phase 3 item 2 (the trade-smith's own vendor + banker): `TRADE_SMITH_SPOT`
# sits at the closed end of a single-tile-wide corridor with exactly **one**
# real (collision-checked `Walk`, not `[Go`) open exit — due east, through
# `(2610, 474)` to `TRADE_MINE_SPOT` — every other direction from the smith's
# own stand tile is walled rock, confirmed by probing all 8 directions live.
# `(2610, 474)` (the corridor's middle tile) turned out to be a small open
# hub, not just a pass-through: real single-`Walk`-step probes from it found
# N/NE/E/SE/S/W all open (only SW/NW blocked) — room for more than the one
# extra tile the corridor's straight line offers. But `direction_toward`
# (`skills/market.py`'s `_market_walk_toward`) picks *one* straight-line
# direction toward the **final** target and has no fallback when that's
# blocked (no A* — DESIGN.md §10 item 4), so a target off the corridor's own
# east-west line is unreachable in a single leg from the smith's stand tile
# (the very first computed direction, e.g. NE for a target north of the mine
# spot, is exactly the rock this corridor is walled in by). `VENDOR_SPOT`/
# `BANKER_SPOT` are therefore **routes** (`skills/market.py` accepts a
# `[(x, y), ...]` waypoint list, not just a point) through that hub: due east
# to `(2610, 474)`, then due north/south — both confirmed-open single steps,
# landing on distinct tiles so a `[Add Blacksmith`/`[Add Banker` staged there
# doesn't overlap. Both `z=20`, matching the smith/forge/anvil/mine spot (no
# "steep Minoc slope" mismatch — see `TRADE_SMITH_SPOT`'s own comment).
TRADE_HUB: tuple[int, int] = (2610, 474)
VENDOR_SPOT: list[tuple[int, int]] = [TRADE_HUB, (2610, 473)]
BANKER_SPOT: list[tuple[int, int]] = [TRADE_HUB, (2610, 475)]

# Phase 3 item 3 (hunt/loot): a hunting pocket well away from the trade zone
# above (2609-2611, 473-475) — mongbats spawned here must never wander close
# enough to aggro the vendor/banker or cross paths with the trade miner's own
# delivery walk, and (a check the trade-spot calibration didn't need) must not
# already be *populated* — a spot with pre-existing wildlife/townsfolk nearby
# would let the hunter's Combat/Hunt (which attacks *any* qualifying-notoriety
# mobile in range, not specifically mongbats) engage the wrong thing, muddying
# `corpse_of` attribution and the "mongbat" narrative alike. Live-checked via
# `GmControl` before picking: two open-Britain-plains candidates (grid-probed
# the way `BLACKSMITH_SPOTS` was) both turned out to already have nearby
# mobiles within 15-20 tiles (named "innocent"-notoriety NPCs plus unrelated
# grey wildlife — evidently inhabited farmland, not empty ground). The
# Minoc-ridge `MINING_SPOTS` pool is all confirmed-**empty** (mining camps,
# not settlements) — reused that *area*, but not a pool entry verbatim:
# most nooks there are deliberately tight and walled-in (good for a
# stationary miner, bad for a hunter chasing multiple corpses), and reusing
# an exact `MINING_SPOTS` tuple would let `village.py`'s miner pool
# eventually hand the *same* tile to a real miner (nothing excludes it there
# the way `TRADE_MINE_SPOT` is excluded — see that constant's own comment).
# Real, collision-checked `Walk` probing (the Z-map + real-Walk method,
# PHASE3.md item 1) a few tiles past `MINING_SPOTS[2]` (2584, 411) — itself
# only 3/8 directions open one step out — found `(2587, 408)` opens into a
# genuinely large pocket: 2-4 real tiles in **every** one of the 8
# directions before anything blocks, all at a consistent z=15 (no slope),
# and zero mobiles within 20 tiles. ~66 tiles from the trade corridor and
# ~1100 from `BLACKSMITH_SPOTS`; not a `MINING_SPOTS` member, so no pool
# collision risk with a real miner either.
HUNTING_SPOT: tuple[int, int] = (2587, 408)

# Phase 3 item 4 (A* navigate): a start/destination pair on opposite sides of
# a Minoc-ridge spur where a straight greedy walk is completely blocked by
# rock, for `live_navigate.py`'s greedy-vs-WalkTo differential proof.
# `NAV_START` reuses `MINING_SPOTS[3]` (2551, 420), an already-calibrated,
# confirmed-empty mining pocket. Live-probed with real, collision-checked
# `Walk` actions (the greedy technique `skills/movement.py::GoTo` itself
# uses): a plain `direction_toward` walk from `NAV_START` toward the ridge's
# far side **never moves at all** — wedged at the very first attempted step,
# 0 tiles of progress — confirming the spur has no straight-line line of
# sight across it.
#
# `NAV_DEST` deliberately reuses `HUNTING_SPOT` (2587, 408), *not* the
# nearer, tighter `MINING_SPOTS[2]` (2584, 411) the first calibration pass
# tried — live-caught, the hard way, why that matters: `MINING_SPOTS[2]`'s
# own "only 3/8 directions open one step out" isn't just cosmetic. A **GM**
# character could walk `WalkTo` routes through it fine both ways (GM
# movement bypasses normal collision denial — misleadingly reassuring), but
# the **real, collision-respecting navigator character** could arrive there
# (after a lot of wobbling on the final approach) yet then get *permanently*
# wedged trying to leave — stuck on the exact same tile through 200 ticks and
# 10 full fresh-`WalkTo` retries (each with an empty deny-blacklist), never
# moving once. `HUNTING_SPOT`, 3 tiles away, is the already-documented fix
# for exactly this class of problem (its own comment: "genuinely large
# pocket: 2-4 real tiles in every one of the 8 directions before anything
# blocks") — re-tested with the real navigator character and it never
# stalled more than a single tick either way. General lesson (worth
# generalizing beyond this one pair): calibrating a destination via the *GM*
# body alone isn't sufficient proof it's enterable/leaveable by a normal
# character — a tight single-exit alcove can look fine to a GM and still be
# a one-way trap for anyone else.
#
# `NAV_START` <-> `NAV_DEST` are 36 tiles apart (chebyshev), comfortably "a
# few dozen". `Action::WalkTo` (`anima-net`'s A*), issued from either spot,
# arrives both ways (~110-121 ticks at the usual 400ms route cadence) by
# taking a real detour: distance-to-target *climbs* well above the starting
# distance over the first ~50 ticks (looping north around the spur through
# ~(2545-2580, 383-420)) before finally closing — the concrete case that
# ruled out a "distance must monotonically improve" progress signal in
# `GoTo` itself (see that class's own docstring) in favor of plain "did the
# tile position change at all".
NAV_START: tuple[int, int] = MINING_SPOTS[3]  # (2551, 420)
NAV_DEST: tuple[int, int] = HUNTING_SPOT  # (2587, 408) — spacious, not a one-way alcove


class CurriculumGoalComplete(Skill):
    """Observation-truth terminal for one validated curriculum frame."""

    name = "curriculum_complete"
    description = "Finish a curriculum goal once its catalog predicate is observed."
    consumes_goal = True

    def __init__(self, profession: str) -> None:
        self.profession = profession

    def can_run(self, ctx: SkillContext) -> bool:
        goal = ctx.goal
        if goal is None or not validate_curriculum_goal(goal, self.profession):
            return False
        milestone = milestone_for(self.profession, str(goal.params["milestone"]))
        if milestone is None:
            return False
        if not curriculum_can_yield(ctx, self.profession):
            return False
        try:
            return bool(milestone.is_achieved(ctx))
        except Exception:  # noqa: BLE001 — a broken predicate cannot complete work
            return False

    def step(self, ctx: SkillContext) -> SkillResult:
        if not self.can_run(ctx):
            return SkillResult(Status.FAILURE)
        return SkillResult(Status.SUCCESS)


class CurriculumBoundWork(Skill):
    """Expose a profession's existing hands only to its admitted work Goal."""

    def __init__(self, profession: str, inner: Skill) -> None:
        self.profession = profession
        self.inner = inner
        self.name = inner.name
        self.description = inner.description
        self._policy_defaults = {
            name: getattr(inner, name)
            for name in ("ore_threshold", "sell_threshold")
            if hasattr(inner, name)
        }

    def _apply_milestone_policy(self, goal_name: str | None) -> None:
        for name, value in self._policy_defaults.items():
            setattr(self.inner, name, value)
        overrides: dict[tuple[str, str], dict[str, int]] = {
            ("miner", "miner_hold_20_ore"): {"ore_threshold": 20},
            ("blacksmith", "blacksmith_hold_10_daggers"): {"sell_threshold": 10},
        }
        for name, value in overrides.get((self.profession, goal_name or ""), {}).items():
            if name in self._policy_defaults:
                setattr(self.inner, name, value)

    def can_run(self, ctx: SkillContext) -> bool:
        goal = ctx.goal
        admitted = goal is not None and validate_curriculum_goal(goal, self.profession)
        goal_name = str(goal.params["milestone"]) if admitted else None
        self._apply_milestone_policy(goal_name)
        exhausted_fallback = goal is None and bool(ctx.memory.get("curriculum_exhausted"))
        return bool((admitted or exhausted_fallback) and self.inner.can_run(ctx))

    def diagnose(self, ctx: SkillContext) -> str | None:
        goal = ctx.goal
        if goal is None or not validate_curriculum_goal(goal, self.profession):
            return f"{self.name}: no admitted {self.profession} curriculum goal"
        return self.inner.diagnose(ctx)

    def step(self, ctx: SkillContext) -> SkillResult:
        if not self.can_run(ctx):
            return SkillResult(Status.FAILURE)
        return self.inner.step(ctx)


class CurriculumWait(Skill):
    """Hold a calibrated workplace while the next trusted goal is picked."""

    name = "curriculum_wait"
    description = "Wait in place for the next admitted curriculum goal."

    def __init__(self, profession: str) -> None:
        self.profession = profession

    def can_run(self, ctx: SkillContext) -> bool:
        if ctx.goal is None:
            return not bool(ctx.memory.get("curriculum_exhausted"))
        return validate_curriculum_goal(ctx.goal, self.profession)

    def step(self, ctx: SkillContext) -> SkillResult:
        return SkillResult(Status.RUNNING)


class CapabilityGoalComplete(Skill):
    """Finish only the installed capability bound to the active sealed Goal."""

    name = "capability_complete"
    description = "Finish a verified capability at its observation-confirmed yield point."
    consumes_goal = True

    def __init__(self, profession: str) -> None:
        self.profession = profession

    def can_run(self, ctx: SkillContext) -> bool:
        if ctx.goal is None:
            return False
        binding = policy_binding_for_context(ctx, self.profession)
        if binding is None:
            return False
        try:
            return bool(binding.achieved(ctx) and binding.can_yield(ctx))
        except Exception:  # noqa: BLE001 — policy callbacks fail closed
            return False

    def step(self, ctx: SkillContext) -> SkillResult:
        return SkillResult(Status.SUCCESS if self.can_run(ctx) else Status.FAILURE)


class CapabilityBoundSkill(Skill):
    """Lease one preconstructed shipped instance to its exact binding only."""

    def __init__(self, profession: str, inner: Skill) -> None:
        self.profession = profession
        self.inner = inner
        self.name = inner.name
        self.description = inner.description

    def can_run(self, ctx: SkillContext) -> bool:
        if ctx.goal is None or not ctx.goal.sealed:
            return False
        binding = policy_binding_for_context(ctx, self.profession)
        return bool(binding is not None and type(self.inner) is binding.skill_type)

    def diagnose(self, ctx: SkillContext) -> str | None:
        if not self.can_run(ctx):
            return f"{self.name}: no matching installed capability lease"
        return None

    def step(self, ctx: SkillContext) -> SkillResult:
        if not self.can_run(ctx):
            return SkillResult(Status.FAILURE)
        return self.inner.step(ctx)


class CapabilityWait(Skill):
    """Hold position before admission or while a verified capability is blocked."""

    name = "capability_wait"
    description = "Wait in place for an admitted capability or its next safe step."

    def __init__(self, profession: str) -> None:
        self.profession = profession

    def can_run(self, ctx: SkillContext) -> bool:
        return bool(
            ctx.goal is None
            or policy_binding_for_context(ctx, self.profession) is not None
            or ctx.goal.kind == "capability"
        )

    def step(self, ctx: SkillContext) -> SkillResult:
        return SkillResult(Status.RUNNING)


@dataclass
class Profession:
    key: str
    persona_name: str
    #: ServUO skill name -> base value to set when staging.
    skills: dict[str, float] = field(default_factory=dict)
    #: ServUO item types to `[AddToPack`.
    items: list[str] = field(default_factory=list)
    #: True when this job needs a calibrated resource workplace (assigned per-agent).
    needs_workplace: bool = False
    #: Builds the work skill the brain runs.
    work_skill: Callable[[], Skill] | None = None
    #: Set when a calibrated single workplace exists (else assigned from a pool).
    workplace: tuple[int, int] | None = None
    #: World objects to `[Add` near the workplace: (type, dx, dy). E.g. a smith's
    #: forge + anvil. Placed by the Control plane when staging.
    structures: list[tuple[str, int, int]] = field(default_factory=list)
    #: `Persona.combat_disposition` this profession's characters are staged
    #: with (`village.py::_persona_for`). Every existing profession leaves
    #: this at the `Persona` default ("neutral", which `Combat`/`Hunt` both
    #: already treat as "will fight" — only "pacifist" opts out), so this is
    #: purely flavor for the hunter below, not a behavioural change elsewhere.
    combat_disposition: str = "neutral"

    def planner(
        self,
        *,
        curriculum_goals: bool = False,
        capability_goals: bool = False,
    ) -> Planner:
        """Voice a pending line, honour an LLM 'go there' goal, else work, be
        sociable, and wander.

        `GoTo` sits above the work skill so an LLM-set goto goal steers the worker
        off to a nearby tile; it's inert (its `can_run` is false) unless cognition
        sets a goto goal, so offline/heuristic agents behave exactly as before. On
        arrival the goal clears and the worker falls back to its trade.
        """
        if curriculum_goals and capability_goals:
            raise ValueError("curriculum_goals and capability_goals are separate modes")
        skills: list[Skill] = [Survive(), RecoverDeath(), SpeakPending(), GoTo()]
        if self.work_skill is not None and not capability_goals:
            work = self.work_skill()
            if curriculum_goals:
                skills.append(CurriculumGoalComplete(self.key))
                skills.append(CurriculumBoundWork(self.key, work))
                skills.append(CurriculumWait(self.key))
            else:
                skills.append(work)
        if capability_goals:
            bindings = [
                binding
                for (profession, _capability), binding in CAPABILITIES.items()
                if profession == self.key
            ]
            if not bindings:
                raise ValueError(f"profession {self.key!r} has no installed capabilities")
            skills.append(CapabilityGoalComplete(self.key))
            for binding in bindings:
                skills.append(CapabilityBoundSkill(self.key, binding.skill_type()))
            skills.append(CapabilityWait(self.key))
        skills += [Greet(), Wander()]
        planner = Planner(skills)
        if capability_goals:
            planner.capability_profession = self.key
            planner.capability_ids = frozenset(
                binding.capability_id for binding in bindings
            )
            planner.capability_lease = issue_capability_planner_lease(
                self.key, tuple(planner.skills)
            )
        return planner


PROFESSIONS: dict[str, Profession] = {
    "miner": Profession(
        key="miner",
        persona_name="Grimm",
        skills={"Mining": 35},
        items=["Pickaxe", "Pickaxe"],
        needs_workplace=True,  # assigned a distinct MINING_SPOTS entry
        # `MineSmeltDeliver` is a strict superset of `MineAndSmelt` — with no
        # `smithy_drop` staged (the common case) it behaves identically, so this
        # stays the miner's *one* work skill (DESIGN.md §10 Phase 3) rather than
        # branching between two skill classes at wiring time.
        work_skill=MineSmeltDeliver,
        # A forge within reach of the stand spot — `Mine` never walks, so once
        # staged it's in range for the whole shift; no navigation needed.
        structures=[("Forge", 1, 1)],
    ),
    "fisher": Profession(
        key="fisher",
        persona_name="Marina",
        skills={"Fishing": 35},
        items=["FishingPole"],
        needs_workplace=True,  # assigned a distinct FISHING_SPOTS shore
        work_skill=Fish,
    ),
    "blacksmith": Profession(
        key="blacksmith",
        persona_name="Tormund",
        skills={"Blacksmith": 35},
        # A freshly `[AddToPack SmithHammer` tool gets a *random* 25-75 uses
        # (ServUO `BaseTool(int itemID) : this(Utility.RandomMinMax(25, 75),
        # itemID)`) and breaks on depletion — live-observed to silently stall
        # a long-running smith (no error, no journal line; the reshown
        # CraftGump's failure message is baked into its *layout*, not sent
        # separately — see `skills/craft.py`). `SmithHammer <uses>` invokes
        # its `[Constructable] SmithHammer(int uses)` overload instead,
        # exactly like the `IronIngot <amount>` pattern below.
        items=["SmithHammer 999", "IronIngot 300"],
        needs_workplace=True,  # assigned a BLACKSMITH_SPOTS spot
        # `BlacksmithMarket` is a strict superset of `Blacksmith` — with no
        # `vendor_spot`/`banker_spot` staged (the common case) it behaves
        # identically (Phase 3 item 2, DESIGN.md §10), so this stays the
        # blacksmith's *one* work skill rather than branching between two
        # skill classes at wiring time (mirrors `MineSmeltDeliver` becoming
        # the miner's one work skill in item 1).
        work_skill=BlacksmithMarket,
        # Forge/anvil north/south of the stand spot, not east/west: a Phase 3
        # trade pairing has the miner approach (and stand adjacent to drop
        # ingots) from the side, and an anvil is a solid, blocking static —
        # live-observed to seal off a 1-tile-wide corridor entirely once
        # placed on it. North/south keeps the horizontal approach clear
        # (and is just as valid for a solo, unpaired blacksmith).
        structures=[("Forge", 0, -1), ("Anvil", 0, 1)],
    ),
    "townsfolk": Profession(
        key="townsfolk",
        persona_name="Sera",
        work_skill=None,  # no job — just lives in town (wander + greet)
    ),
    # Live via `find_tree_clusters` (uomap.py), which locates real tree statics
    # from the static map so village.py can assign each lumberjack its own grove:
    "lumberjack": Profession(
        key="lumberjack",
        persona_name="Bjorn",
        skills={"Lumberjacking": 35},
        items=["Hatchet"],
        needs_workplace=True,
        work_skill=Chop,
    ),
    # The lumber->carpenter->tinker chain's second link (Bricks 4-5,
    # docs/LUMBER-CARPENTER-TINKER.md): saw boards into furniture, sell it, bank
    # the gold, and self-provision boards + a replacement saw — five capabilities
    # (`skills/carpentry.py`), all thin config subclasses of the generalized
    # craft/market machinery. Capability-driven only (no standalone village
    # work_skill yet, unlike the smith's `BlacksmithMarket`): the planner reads the
    # five carpenter bindings straight from `CAPABILITIES`. Carpentry 80 clears the
    # Throne's 73.6 skill floor; a durable `Saw 999` avoids the mid-run tool break
    # that `SmithHammer 999` guards against on the smith (buy_saw is the fallback if
    # it still breaks). No forge/anvil/structures (a saw crafts anywhere) and no
    # resource-node workplace (the carpenter buys/gets boards, it doesn't harvest).
    "carpenter": Profession(
        key="carpenter",
        persona_name="Sten",
        skills={"Carpentry": 80},
        items=["Saw 999"],
        work_skill=None,
    ),
    # Phase 3 item 3 (hunt/loot, DESIGN.md §10): engages weak creatures
    # (calibrated target: Mongbat — `Scripts/Mobiles/Normal/Mongbat.cs`, 4-6
    # hits, `AddLoot(LootPack.Poor)`) bare-handed. No weapon needed —
    # Wrestling alone reliably kills a Mongbat in one or two swings
    # (live-verified, `live_hunt.py`); Tactics raises the hit chance so
    # engagements don't drag on. No `structures` (unlike mining/blacksmithing,
    # hunting needs no forge/anvil/tree). Bandages + Healing/Anatomy make the
    # universal `Survive` skill live for the combat profession, but there is no
    # starting weapon (bare hands *are* the weapon here). `HUNTING_SPOT` is a single calibrated
    # pocket (see its own comment) — every hunter shares it, matching
    # `TRADE_SMITH_SPOT`'s single-workplace shape rather than the per-agent
    # pools miners/fishers/lumberjacks draw from (a village today only ever
    # stages one hunter at a time in practice; nothing stops staging more,
    # they'd simply share the field).
    "hunter": Profession(
        key="hunter",
        persona_name="Ragnar",
        skills={"Wrestling": 50, "Tactics": 50, "Healing": 60, "Anatomy": 60},
        items=["Bandage 50"],
        needs_workplace=True,
        workplace=HUNTING_SPOT,
        work_skill=Hunt,
        combat_disposition="aggressive",
    ),
    # The lumber->carpenter->tinker chain's third link (Bricks 7-10,
    # docs/LUMBER-CARPENTER-TINKER.md): forge iron into small metal goods (Tongs),
    # sell them to the Tinker NPC, bank the gold, and self-provision iron + a
    # replacement tinker's tool — five capabilities (`skills/tinkering.py`), all
    # thin config subclasses of the generalized craft/market machinery. Capability-
    # driven only (no standalone village work_skill), like the carpenter: the
    # planner reads the five tinker bindings straight from `CAPABILITIES`. Tinkering
    # 80 clears Tongs' 35-skill floor with headroom (and GoldRing's 65 for a later
    # brick); a durable `TinkerTools 999` avoids a mid-run tool break (buy_tinker_tool
    # is the fallback if it still breaks). No forge/anvil/structures (the tinker
    # tool crafts anywhere) and no resource-node workplace (the tinker buys/gets
    # iron, it doesn't harvest — a free-iron supply is a later brick).
    "tinker": Profession(
        key="tinker",
        persona_name="Pim",
        skills={"Tinkering": 80},
        items=["TinkerTools 999"],
        work_skill=None,
    ),
}
