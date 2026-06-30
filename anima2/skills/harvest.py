"""Harvesting skills — gather a resource by using a tool on the surrounding tiles.

The UO harvest loop is identical for mining, lumberjacking, and fishing: **use the
tool → the server opens a target cursor → target a resource tile → harvest.** The
success signal is the relevant skill's base rising (0x3A) — unambiguous and
already parsed (dig/chop results are cliloc 0xC1, available but not needed). So
`Harvest` rewards on skill gain and probes the 8 surrounding tiles round-robin to
keep hitting resource nodes.
"""

from __future__ import annotations

from ..contract import TargetGround, Use
from ..geometry import DIRECTION_DELTAS
from .base import Skill, SkillContext, SkillResult, Status

BACKPACK_LAYER = 0x15

# Tool item graphics (ServUO art ids).
PICKAXE_GRAPHICS = frozenset({0x0E85, 0x0E86, 0x0F39, 0x0F3A})  # pickaxe / shovel
AXE_GRAPHICS = frozenset({0x0F43, 0x0F44, 0x0F47, 0x0F49, 0x0F4B, 0x13B0, 0x13FB, 0x1443})

# UO skill ids.
SKILL_LUMBERJACKING = 44
SKILL_MINING = 45


class Harvest(Skill):
    """Base gathering skill: use a tool on probed neighbour tiles, reward on skill gain.

    Subclasses set `tool_graphics`, `skill_id`, `name`, `description`.
    """

    tool_graphics: frozenset[int] = frozenset()
    skill_id: int = -1

    def can_run(self, ctx: SkillContext) -> bool:
        return self._tool(ctx) is not None or self._backpack(ctx) is not None

    def step(self, ctx: SkillContext) -> SkillResult:
        obs = ctx.obs

        # Reward = skill base gained since last tick (the real signal).
        reward = 0.0
        base = self._skill_base(obs)
        prev = ctx.memory.get("harvest_base")
        if base is not None:
            if prev is not None and base > prev + 1e-3:
                reward = base - prev
            ctx.memory["harvest_base"] = base

        # 1) Cursor open → target a probed neighbour tile (offset from our facing).
        if obs.pending_target is not None:
            p = obs.player.pos
            d = (obs.player.direction + ctx.memory.get("harvest_probe", 0)) & 0x07
            dx, dy = DIRECTION_DELTAS[d]
            return SkillResult(Status.RUNNING, TargetGround(x=p.x + dx, y=p.y + dy, z=p.z), reward)

        # 2) Need the tool visible; if the pack is closed, open it to reveal tools.
        tool = self._tool(ctx)
        if tool is None:
            bp = self._backpack(ctx)
            if bp is None:
                return SkillResult(Status.FAILURE, None, reward)
            return SkillResult(Status.RUNNING, Use(serial=bp.serial), reward)

        # 3) Swing — and rotate the probe so the next attempt tries the next tile.
        ctx.memory["harvest_probe"] = (ctx.memory.get("harvest_probe", 0) + 1) & 0x07
        return SkillResult(Status.RUNNING, Use(serial=tool.serial), reward)

    def _skill_base(self, obs) -> float | None:
        return next((s.base for s in obs.skills if s.id == self.skill_id), None)

    def _tool(self, ctx: SkillContext):
        return next((i for i in ctx.obs.items if i.graphic in self.tool_graphics), None)

    @staticmethod
    def _backpack(ctx: SkillContext):
        return next((i for i in ctx.obs.items if i.layer == BACKPACK_LAYER), None)


class Mine(Harvest):
    """Mine ore from surrounding rock with a pickaxe."""

    name = "mine"
    description = "Mine ore from the surrounding rock with a pickaxe."
    tool_graphics = PICKAXE_GRAPHICS
    skill_id = SKILL_MINING


class Chop(Harvest):
    """Chop logs from surrounding trees with an axe (lumberjacking)."""

    name = "chop"
    description = "Chop logs from the surrounding trees with an axe."
    tool_graphics = AXE_GRAPHICS
    skill_id = SKILL_LUMBERJACKING
