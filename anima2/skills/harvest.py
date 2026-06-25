"""Harvesting skills — mining (and the pattern for lumberjacking/fishing).

The UO harvest loop is: **use the tool → the server opens a target cursor →
target a resource tile → read the journal for the result.** Every piece this
needs is now in the contract (container contents to find the tool, `pending_target`,
`TargetGround`, journal). Live mining additionally needs a tool in the pack and a
mineable tile in reach — scenario setup (Control plane / manual) — but the skill's
decision logic is exercised directly in tests.
"""

from __future__ import annotations

from ..contract import TargetGround, Use
from ..geometry import DIRECTION_DELTAS
from .base import Skill, SkillContext, SkillResult, Status

# Pickaxe / shovel item graphics (ServUO art ids).
PICKAXE_GRAPHICS = frozenset({0x0E85, 0x0E86, 0x0F39, 0x0F3A})
BACKPACK_LAYER = 0x15

# Journal substrings that classify the outcome of a dig.
_SUCCESS = ("you dig", "you put", "you place", "ingot", " ore")
_NO_RESOURCE = ("no metal", "can't mine", "cannot mine", "no ore", "is too far", "nothing here")


class Mine(Skill):
    """Swing a pickaxe at the tile we're facing and harvest ore.

    Runnable when a pickaxe is visible (in an open container). If a target cursor
    is up, answers it with the tile one step ahead; otherwise uses the tool to
    raise one. Reads the journal to score the dig.
    """

    name = "mine"
    description = "Mine ore from the tile ahead with a pickaxe."

    def can_run(self, ctx: SkillContext) -> bool:
        return self._tool(ctx) is not None or self._backpack(ctx) is not None

    def step(self, ctx: SkillContext) -> SkillResult:
        obs = ctx.obs

        # 1) A target cursor is open → target the tile we're facing.
        if obs.pending_target is not None:
            p = obs.player.pos
            dx, dy = DIRECTION_DELTAS[obs.player.direction & 0x07]
            return SkillResult(Status.RUNNING, TargetGround(x=p.x + dx, y=p.y + dy, z=p.z))

        # 2) React to the result of the previous dig.
        for j in obs.new_journal:
            t = j.text.lower()
            if any(s in t for s in _SUCCESS):
                return SkillResult(Status.RUNNING, None, reward=1.0)  # got ore; keep digging
            if any(s in t for s in _NO_RESOURCE):
                return SkillResult(Status.FAILURE, None)  # barren tile — let the planner move us

        # 3) Need the tool visible first; if the pack is closed, open it.
        tool = self._tool(ctx)
        if tool is None:
            bp = self._backpack(ctx)
            if bp is None:
                return SkillResult(Status.FAILURE, None)
            return SkillResult(Status.RUNNING, Use(serial=bp.serial))  # open pack to reveal tools

        # 4) Swing the pickaxe — opens a target cursor next tick.
        return SkillResult(Status.RUNNING, Use(serial=tool.serial))

    @staticmethod
    def _tool(ctx: SkillContext):
        return next((i for i in ctx.obs.items if i.graphic in PICKAXE_GRAPHICS), None)

    @staticmethod
    def _backpack(ctx: SkillContext):
        return next((i for i in ctx.obs.items if i.layer == BACKPACK_LAYER), None)
