"""Professions — what an agent does for a living, and how the Control plane stages it.

A `Profession` bundles the Control-plane staging (skills to set, tools to give, a
workplace) with the work skill the brain runs. The village runner assigns a
profession (and, for resource jobs, a distinct workplace) to each agent.

Currently only **mining** is fully calibrated (a verified resource + workplace).
Lumberjacking/fishing/smithing are defined but need a calibrated spot (a real tree/
water tile from the static map) or gump support (crafting) — see PHASE2.md. The
framework is data-driven so adding them is just a row here once that lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .planner import Planner
from .skills import Blacksmith, Chop, Fish, GoTo, Greet, MineAndSmelt, Skill, SpeakPending, Wander

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
        work_skill=MineAndSmelt,
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
        items=["SmithHammer", "IronIngot 300"],
        needs_workplace=True,  # assigned a BLACKSMITH_SPOTS spot
        work_skill=Blacksmith,
        structures=[("Forge", -1, 0), ("Anvil", 1, 0)],  # a smithy at the workplace
    ),
    "townsfolk": Profession(
        key="townsfolk",
        persona_name="Sera",
        work_skill=None,  # no job — just lives in town (wander + greet)
    ),
    # Defined but not yet live (need a calibrated tree tile from the static map):
    "lumberjack": Profession(
        key="lumberjack",
        persona_name="Bjorn",
        skills={"Lumberjacking": 35},
        items=["Hatchet"],
        needs_workplace=True,
        work_skill=Chop,
    ),
}
