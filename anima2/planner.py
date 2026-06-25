"""Planner — picks which skill runs this tick toward the current goal.

Phase 1 is a simple **priority rule**: walk an ordered skill list and run the
first one whose `can_run(ctx)` holds. This is the deliberate first cut from
DESIGN.md A3 ("rules first; bandit/Q-learning later"): the seam where skill
*selection* learning will later slot in, without changing callers.
"""

from __future__ import annotations

from .skills.base import Skill, SkillContext


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
        for skill in self.skills:
            if skill.can_run(ctx):
                return skill
        return self.skills[-1]  # fallback
