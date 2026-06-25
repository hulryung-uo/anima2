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

    def can_run(self, ctx: SkillContext) -> bool:
        """Whether this skill is applicable right now. Default: always."""
        return True

    @abstractmethod
    def step(self, ctx: SkillContext) -> SkillResult:
        """Produce the next action toward this skill's objective."""
        raise NotImplementedError
