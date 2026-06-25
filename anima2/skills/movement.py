"""Movement skills: wander aimlessly, or walk toward a target tile."""

from __future__ import annotations

from ..contract import Position, Walk
from ..geometry import chebyshev, direction_toward
from .base import Skill, SkillContext, SkillResult, Status


class Wander(Skill):
    """Step in a steady direction, turning when blocked. The default 'be alive' skill."""

    name = "wander"
    description = "Walk around aimlessly, changing direction when movement stalls."

    def step(self, ctx: SkillContext) -> SkillResult:
        # Rotate through directions; persist the current one in scratch memory.
        d = ctx.memory.get("wander_dir", 2)  # default East
        last = ctx.memory.get("wander_last_pos")
        cur = (ctx.obs.player.pos.x, ctx.obs.player.pos.y)
        if last == cur:  # didn't move last tick → we're blocked, turn
            d = (d + 1) % 8
        ctx.memory["wander_dir"] = d
        ctx.memory["wander_last_pos"] = cur
        return SkillResult(Status.RUNNING, Walk(dir=d, run=False))


class GoTo(Skill):
    """Greedily walk toward ``goal.params['target']`` (a Position). SUCCESS on arrival.

    A first-cut mover using only the contract's `Walk` (no A*). The real body
    (anima-core) has A* navigation; a later skill will delegate to it for routing
    around obstacles. This greedy version is enough for open terrain + the mock.
    """

    name = "goto"
    description = "Walk step-by-step toward a target tile until adjacent/arrived."

    def can_run(self, ctx: SkillContext) -> bool:
        return ctx.goal is not None and ctx.goal.kind == "goto"

    def step(self, ctx: SkillContext) -> SkillResult:
        assert ctx.goal is not None
        target: Position = ctx.goal.params["target"]
        here = ctx.obs.player.pos
        if chebyshev(here, target) == 0:
            return SkillResult(Status.SUCCESS, None, reward=1.0)

        d = direction_toward(here, target)
        # If a real step (already facing) didn't move us, we're wedged → fail so a
        # higher layer can re-plan (the greedy mover can't route around walls).
        last = ctx.memory.get("goto_last_pos")
        cur = (here.x, here.y)
        if last == cur and ctx.memory.get("goto_last_dir") == d:
            return SkillResult(Status.FAILURE, None)
        ctx.memory["goto_last_pos"] = cur
        ctx.memory["goto_last_dir"] = d
        return SkillResult(Status.RUNNING, Walk(dir=d, run=False))
