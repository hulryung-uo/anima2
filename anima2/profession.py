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

from .planner import Planner
from .skills import Blacksmith, Chop, Fish, GoTo, Greet, MineSmeltDeliver, Skill, SpeakPending, Wander

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

    def planner(self) -> Planner:
        """Voice a pending line, honour an LLM 'go there' goal, else work, be
        sociable, and wander.

        `GoTo` sits above the work skill so an LLM-set goto goal steers the worker
        off to a nearby tile; it's inert (its `can_run` is false) unless cognition
        sets a goto goal, so offline/heuristic agents behave exactly as before. On
        arrival the goal clears and the worker falls back to its trade.
        """
        skills: list[Skill] = [SpeakPending(), GoTo()]
        if self.work_skill is not None:
            skills.append(self.work_skill())
        skills += [Greet(), Wander()]
        return Planner(skills)


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
        work_skill=Blacksmith,
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
}
