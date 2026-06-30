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
from .skills import Chop, Greet, Mine, Skill, SpeakPending, Wander

# anima v1's flood-fill-verified Minoc ore banks (foundry/kernel/gm.py LANE_SPOTS):
# walkable tiles with ~19 mineable tiles in reach, ≥33 apart so workers don't crowd.
MINING_SPOTS: list[tuple[int, int]] = [
    (2567, 493), (2611, 474), (2584, 411), (2551, 420), (2524, 532),
    (2608, 538), (2485, 550), (2698, 538), (2659, 538), (2500, 382),
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

    def planner(self) -> Planner:
        """Work first, then be sociable, then wander."""
        skills: list[Skill] = [SpeakPending()]
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
        work_skill=Mine,
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
