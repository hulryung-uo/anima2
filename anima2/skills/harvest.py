"""Harvesting skills — gather a resource by using a tool on the surrounding tiles.

The UO harvest loop is identical for mining, lumberjacking, and fishing: **use the
tool → the server opens a target cursor → target a resource tile → harvest.** The
success signal is the relevant skill's base rising (0x3A) — unambiguous and
already parsed (dig/chop results are cliloc 0xC1, available but not needed). So
`Harvest` rewards on skill gain and probes the 8 surrounding tiles round-robin to
keep hitting resource nodes.
"""

from __future__ import annotations

from ..contract import Equip, PickUp, TargetGround, Use
from .base import Skill, SkillContext, SkillResult, Status

BACKPACK_LAYER = 0x15

# Tiles within harvesting reach (Chebyshev ≤ 2), nearest ring first. We probe
# these round-robin to find the resource node (rock/tree) near where we stand —
# reach-2 because a gatherer is often staged a tile or two off the exact node.
PROBE_OFFSETS = [
    (dx, dy)
    for r in (1, 2)
    for dx in range(-r, r + 1)
    for dy in range(-r, r + 1)
    if max(abs(dx), abs(dy)) == r
]

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
    #: Some tools must be worn to work (lumberjacking: "the axe must be equipped").
    requires_equipped: bool = False
    #: Worn layer for the tool (2 = two-handed, e.g. an axe).
    equip_layer: int = 2

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

        # 1) Cursor open → target the resource. If the Control plane gave us an
        #    exact node (x, y, z, graphic) — required for statics like trees —
        #    target it; otherwise probe the surrounding tiles (works for ore).
        if obs.pending_target is not None:
            node = ctx.memory.get("harvest_node")
            if node is not None:
                x, y, z, graphic = node
                return SkillResult(Status.RUNNING, TargetGround(x=x, y=y, z=z, graphic=graphic), reward)
            p = obs.player.pos
            dx, dy = PROBE_OFFSETS[ctx.memory.get("harvest_probe", 0) % len(PROBE_OFFSETS)]
            return SkillResult(Status.RUNNING, TargetGround(x=p.x + dx, y=p.y + dy, z=p.z), reward)

        tool = self._tool(ctx)
        if tool is not None:
            ctx.memory["harvest_tool"] = tool.serial  # remember it (vanishes on cursor)

        # 2) Equip the tool if it must be worn (lumberjacking). A UO equip is two
        #    steps — pick up to the cursor, then wear — and the item disappears
        #    from `items` while held, so drive it off the remembered serial.
        if self.requires_equipped and not (tool is not None and tool.layer == self.equip_layer):
            tser = ctx.memory.get("harvest_tool")
            if tser is None:  # never seen the tool → open the pack to reveal it
                bp = self._backpack(ctx)
                if bp is None:
                    return SkillResult(Status.FAILURE, None, reward)
                return SkillResult(Status.RUNNING, Use(serial=bp.serial), reward)
            step = ctx.memory.get("harvest_equip_step", 0)
            ctx.memory["harvest_equip_step"] = (step + 1) % 2
            if step == 0:
                return SkillResult(Status.RUNNING, PickUp(serial=tser, amount=1), reward)
            return SkillResult(Status.RUNNING, Equip(serial=tser, layer=self.equip_layer), reward)

        # 3) Tool not visible (and not mid-equip) → open the pack to reveal it.
        if tool is None:
            bp = self._backpack(ctx)
            if bp is None:
                return SkillResult(Status.FAILURE, None, reward)
            return SkillResult(Status.RUNNING, Use(serial=bp.serial), reward)

        # 4) Swing — and advance the probe so the next attempt tries the next tile.
        ctx.memory["harvest_probe"] = ctx.memory.get("harvest_probe", 0) + 1
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
    """Chop logs from a tree with an axe (lumberjacking).

    The axe must be **equipped** (worn, two-handed) and the target must be the
    exact tree static — so a lumberjack needs `memory['harvest_node']` = (x, y, z,
    graphic) set by the Control plane (found via `anima2.uomap.find_trees`).
    """

    name = "chop"
    description = "Chop logs from a tree with an axe."
    tool_graphics = AXE_GRAPHICS
    skill_id = SKILL_LUMBERJACKING
    requires_equipped = True
    equip_layer = 2  # two-handed
