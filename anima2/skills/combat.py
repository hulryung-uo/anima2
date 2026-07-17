"""Combat skill — engage the nearest hostile creature."""

from __future__ import annotations

from ..contract import Attack, MobileView, WarMode
from .base import Skill, SkillContext, SkillResult, Status

# UO notoriety bytes that are valid/likely attack targets:
#   3 gray (attackable), 4 criminal, 5 enemy (orange), 6 murderer (red).
HOSTILE_NOTORIETY = frozenset({3, 4, 5, 6})


def is_hostile(mobile: MobileView) -> bool:
    """Shared target/flee population: attackable and not observably dead."""
    return (
        mobile.notoriety in HOSTILE_NOTORIETY
        and not (mobile.hits_max > 0 and mobile.hits <= 0)
    )


class Combat(Skill):
    """Toggle war mode and attack the nearest hostile within `engage_range`.

    Disabled for pacifist personas. Emits `WarMode(on=True)` once, then `Attack`.
    """

    name = "combat"
    description = "Attack the nearest hostile creature (war mode + attack)."
    engage_range: int = 10

    def can_run(self, ctx: SkillContext) -> bool:
        return ctx.persona.combat_disposition != "pacifist" and self._target(ctx) is not None

    def step(self, ctx: SkillContext) -> SkillResult:
        target = self._target(ctx)
        if target is None:
            ctx.memory["in_war"] = False
            return SkillResult(Status.SUCCESS, None)  # nothing left to fight
        if not ctx.memory.get("in_war"):
            ctx.memory["in_war"] = True
            return SkillResult(Status.RUNNING, WarMode(on=True))
        return SkillResult(Status.RUNNING, Attack(serial=target.serial), reward=0.05)

    def _target(self, ctx: SkillContext) -> MobileView | None:
        # obs.mobiles is sorted by distance, so the first match is the nearest.
        for m in ctx.obs.mobiles:
            if is_hostile(m) and m.distance <= self.engage_range:
                return m
        return None
