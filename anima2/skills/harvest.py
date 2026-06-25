"""Harvesting skills — mining (and the pattern for lumberjacking/fishing).

The UO harvest loop: **use the tool → the server opens a target cursor → target a
resource tile → harvest.** Every piece is in the contract (container contents to
find the tool, `pending_target`, `TargetGround`, skills).

Note on the success signal: ServUO reports dig results as *localized* messages
(cliloc, packet 0xC1) which the journal doesn't carry yet. We don't need them —
the unambiguous, already-parsed signal is the **Mining skill base rising** (0x3A),
which is exactly v1's fitness backbone. So `Mine` rewards on skill gain and probes
the 8 surrounding tiles round-robin to keep finding rock. (Parsing 0xC1 for richer
feedback is a TODO.)

Live-verified against ServUO: staged at the Minoc ridge (Control plane), `Mine`
gained Mining 35.0 → 35.2 by digging the east/west rock tiles.
"""

from __future__ import annotations

from ..contract import TargetGround, Use
from ..geometry import DIRECTION_DELTAS
from .base import Skill, SkillContext, SkillResult, Status

# Pickaxe / shovel item graphics (ServUO art ids).
PICKAXE_GRAPHICS = frozenset({0x0E85, 0x0E86, 0x0F39, 0x0F3A})
BACKPACK_LAYER = 0x15
MINING_SKILL_ID = 45


class Mine(Skill):
    """Swing a pickaxe at surrounding tiles and harvest ore.

    Runnable when a pickaxe (or the backpack to find one) is visible. If a target
    cursor is open, answers it with a probed neighbour tile; otherwise swings the
    tool. Rewards whenever the Mining skill base rises, and round-robins the probe
    direction each swing so it keeps hitting mineable rock.
    """

    name = "mine"
    description = "Mine ore from the surrounding rock with a pickaxe."

    def can_run(self, ctx: SkillContext) -> bool:
        return self._tool(ctx) is not None or self._backpack(ctx) is not None

    def step(self, ctx: SkillContext) -> SkillResult:
        obs = ctx.obs

        # Reward = Mining skill base gained since last tick (the real signal).
        reward = 0.0
        base = self._mining_base(obs)
        prev = ctx.memory.get("mine_base")
        if base is not None:
            if prev is not None and base > prev + 1e-3:
                reward = base - prev
            ctx.memory["mine_base"] = base

        # 1) Cursor open → target a probed neighbour tile (offset from our facing).
        if obs.pending_target is not None:
            p = obs.player.pos
            d = (obs.player.direction + ctx.memory.get("mine_probe", 0)) & 0x07
            dx, dy = DIRECTION_DELTAS[d]
            return SkillResult(Status.RUNNING, TargetGround(x=p.x + dx, y=p.y + dy, z=p.z), reward)

        # 2) Need the tool visible; if the pack is closed, open it to reveal tools.
        tool = self._tool(ctx)
        if tool is None:
            bp = self._backpack(ctx)
            if bp is None:
                return SkillResult(Status.FAILURE, None, reward)
            return SkillResult(Status.RUNNING, Use(serial=bp.serial), reward)

        # 3) Swing — and rotate the probe so the next dig tries the next tile.
        ctx.memory["mine_probe"] = (ctx.memory.get("mine_probe", 0) + 1) & 0x07
        return SkillResult(Status.RUNNING, Use(serial=tool.serial), reward)

    @staticmethod
    def _mining_base(obs) -> float | None:
        return next((s.base for s in obs.skills if s.id == MINING_SKILL_ID), None)

    @staticmethod
    def _tool(ctx: SkillContext):
        return next((i for i in ctx.obs.items if i.graphic in PICKAXE_GRAPHICS), None)

    @staticmethod
    def _backpack(ctx: SkillContext):
        return next((i for i in ctx.obs.items if i.layer == BACKPACK_LAYER), None)
