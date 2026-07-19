"""The Skill framework — the deterministic "hands" of the agent.

A skill is a small, composable unit of competence (walk-to, gather, attack-loop,
…). It runs in the **fast loop**: given a `SkillContext`, it returns one `Action`
to take now plus a status. Skills are deliberately dumb and reliable; the LLM
(slow loop) sets the *goal* a skill serves, it does not run inside skills.

This is the seed of the Voyager-style skill library (DESIGN.md §6): skills carry
a name + natural-language `description` for later embedding-indexed retrieval, and
return reward signals for selection learning.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from ..contract import Action, Observation
from ..persona import Persona


class Status(Enum):
    RUNNING = auto()  # not done; call me again next tick
    SUCCESS = auto()  # goal achieved
    FAILURE = auto()  # cannot make progress


@dataclass
class Goal:
    """A high-level objective (set by the planner or the LLM cognition loop)."""

    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillContext:
    obs: Observation
    persona: Persona
    goal: Goal | None = None
    # Per-agent scratch memory skills may read/write across ticks.
    memory: dict[str, Any] = field(default_factory=dict)
    # Recent episodes (read-only view), for cognition context. See memory.Episode.
    episodes: list[Any] = field(default_factory=list)
    # Total episodes ever recorded (EpisodicMemory.total_recorded) — monotonic,
    # unlike len(episodes); reflection cadence compares against this.
    episode_count: int = 0
    # Recent reflection insights (read-only view), for cognition context.
    # See memory.Insight / cognition.ReflectingCognition.
    insights: list[Any] = field(default_factory=list)
    # Stable identity and observation-derived progress for the active goal
    # frame. Kept after every legacy field so positional constructors retain
    # their pre-B1 meaning; new code should pass these by keyword.
    goal_id: int | None = None
    goal_revision: int = 0
    goal_progress: Any | None = None


@dataclass
class SkillResult:
    status: Status
    action: Action | None = None
    reward: float = 0.0


class Skill(ABC):
    """Base class for all skills."""

    #: Unique, stable identifier.
    name: str = "skill"
    #: One-line natural-language description (used later for embedding retrieval).
    description: str = ""
    #: True for skills that exist to serve `ctx.goal` (e.g. GoTo). When such a skill
    #: reaches a terminal state, the agent clears the goal (it's been consumed).
    consumes_goal: bool = False
    #: True when the skill is a deterministic safety transaction that suspends
    #: ordinary goal progress/cognition and must stop any native route first.
    interrupts_goal: bool = False

    def can_run(self, ctx: SkillContext) -> bool:
        """Whether this skill is applicable right now. Default: always."""
        return True

    def diagnose(self, ctx: SkillContext) -> str | None:
        """A short, one-line reason this skill can't run right now, or `None`
        when it can (`can_run(ctx)` is `True`). Feeds item 5's curriculum
        eligibility reasoning without an LLM guessing why a skill is idle
        (PHASE4.md item 3) — mines the *idea* from v1's `../anima/anima/
        skills/base.py` `can_execute`/`diagnose` precondition pattern, not
        its async plumbing.

        Default: a generic fallback whenever `can_run` is `False` — every
        un-overridden skill gets this for free. `Blacksmith`/`Hunt`/
        `MineSmeltDeliver` override with a more specific one-liner drawn from
        their own preconditions (see each class's own `diagnose`).
        """
        if self.can_run(ctx):
            return None
        return f"{self.name}: preconditions not met"

    @abstractmethod
    def step(self, ctx: SkillContext) -> SkillResult:
        """Produce the next action toward this skill's objective."""
        raise NotImplementedError
