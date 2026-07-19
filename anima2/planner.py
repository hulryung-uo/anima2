"""Planner — picks which skill runs this tick toward the current goal.

Phase 1 is a simple **priority rule**: walk an ordered skill list and run the
first one whose `can_run(ctx)` holds. This is the deliberate first cut from
DESIGN.md A3 ("rules first; bandit/Q-learning later"): the seam where skill
*selection* learning will later slot in, without changing callers.
"""

from __future__ import annotations

from contextvars import ContextVar

from .skills.base import Skill, SkillContext


_APPLICABILITY: ContextVar[dict[int, bool] | None] = ContextVar(
    "planner_applicability", default=None
)


class Planner:
    """Ordered-priority skill selector.

    `skills` is highest-priority first. The last skill should be a always-runnable
    fallback (e.g. `Wander`) so the agent is never idle.
    """

    def __init__(self, skills: list[Skill]) -> None:
        if not skills:
            raise ValueError("Planner needs at least one skill")
        self.skills = skills

    def select(self, ctx: SkillContext) -> Skill:
        return self._select(ctx, _APPLICABILITY.get() or {})

    def preselect_interrupt(self, ctx: SkillContext) -> tuple[Skill | None, dict[int, bool]]:
        """Find deterministic safety work and cache its applicability checks."""

        checked: dict[int, bool] = {}
        for skill in self.skills:
            if not skill.interrupts_goal:
                continue
            runnable = skill.can_run(ctx)
            checked[id(skill)] = runnable
            if runnable:
                return skill, checked
        return None, checked

    def select_cached(self, ctx: SkillContext, applicability: dict[int, bool]) -> Skill:
        """Select with prechecked safety results without closing the override seam."""

        token = _APPLICABILITY.set(applicability)
        try:
            # Always enter through the public override seam. A custom planner
            # that delegates to super().select(ctx) inherits this tick's cache;
            # context-local storage also keeps shared planners thread-safe.
            return self.select(ctx)
        finally:
            _APPLICABILITY.reset(token)

    def _select(self, ctx: SkillContext, applicability: dict[int, bool]) -> Skill:
        for skill in self.skills:
            runnable = applicability.get(id(skill))
            if runnable is None:
                runnable = skill.can_run(ctx)
            if runnable:
                return skill
        return self.skills[-1]  # fallback
