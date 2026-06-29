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
        d = ctx.memory.get("wander_dir", 2)  # default East
        last = ctx.memory.get("wander_last_pos")
        cur = (ctx.obs.player.pos.x, ctx.obs.player.pos.y)
        # In UO the first walk in a NEW direction only turns you (no move), so a
        # single no-move tick isn't "blocked". Give each direction a real step
        # (turn + move) before rotating — otherwise we'd spin in place forever.
        stuck = ctx.memory.get("wander_stuck", 0) + 1 if last == cur else 0
        if stuck >= 2:
            d = (d + 1) % 8
            stuck = 0
        ctx.memory["wander_dir"] = d
        ctx.memory["wander_stuck"] = stuck
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

    #: Consecutive no-progress ticks before we declare ourselves wedged. The
    #: first step in a new direction is a UO *turn* (no move), so this must be >1.
    stall_limit: int = 4

    def step(self, ctx: SkillContext) -> SkillResult:
        assert ctx.goal is not None
        target: Position = ctx.goal.params["target"]
        here = ctx.obs.player.pos
        if chebyshev(here, target) == 0:
            ctx.memory.pop("goto_stall", None)
            return SkillResult(Status.SUCCESS, None, reward=1.0)

        cur = (here.x, here.y)
        stall = ctx.memory.get("goto_stall", 0) + 1 if ctx.memory.get("goto_last_pos") == cur else 0
        ctx.memory["goto_stall"] = stall
        ctx.memory["goto_last_pos"] = cur

        # Sustained no progress in open-greedy mode → wedged (a wall we can't route
        # around); fail so a higher layer re-plans. The greedy mover has no A*.
        if stall >= self.stall_limit:
            ctx.memory.pop("goto_stall", None)
            return SkillResult(Status.FAILURE, None)

        return SkillResult(Status.RUNNING, Walk(dir=direction_toward(here, target), run=False))
