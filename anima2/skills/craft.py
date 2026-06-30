"""Crafting skills — make items from a craft gump (blacksmithing first).

Unlike gathering, crafting drives a multi-step **gump** (the ServUO CraftGump):
double-click the tool → a menu opens → pick a category → pick an item → it crafts
and re-shows the menu. Button ids come from ServUO's formula `1 + type + index*7`
(verified in anima v1's `craft_blacksmith.py`). The gump is exposed to the brain
via `Observation.gumps`; we answer with `GumpResponse`.
"""

from __future__ import annotations

from ..contract import GumpResponse, Use
from .base import Skill, SkillContext, SkillResult, Status

# Blacksmithing tool: a smith's hammer (0x13E3) opens the craft gump even away
# from a forge/anvil (SE+); tongs (0x0FBB/0x0FBC) also work as a craft tool.
SMITH_TOOL_GRAPHICS = frozenset({0x13E3, 0x0FBB, 0x0FBC})
SKILL_BLACKSMITHING = 7


def _button(btn_type: int, index: int) -> int:
    """ServUO CraftGump button id (CraftGump.cs GetButtonID)."""
    return 1 + btn_type + index * 7


# Bladed weapons (group 3), Dagger (item 4): cheap (3 ingots), craftable at ~0 skill.
CATEGORY_BTN = _button(0, 3)  # 22 — select the "bladed" category
DAGGER_BTN = _button(1, 4)  # 30 — make a dagger
MAKE_LAST_BTN = _button(6, 2)  # 21 — re-make the last item (the craft loop)

# How long (ticks) to wait for the craft gump to re-appear before re-opening it
# with the tool — the craft delay is ~2s.
_REOPEN_AFTER = 12


class Blacksmith(Skill):
    """Forge daggers from iron ingots at a forge/anvil, looping with MAKE LAST.

    Needs tongs in the pack, iron ingots, and to stand by a forge + anvil (staged
    by the Control plane). Rewards on Blacksmithing skill gain.
    """

    name = "blacksmith"
    description = "Forge items from iron ingots at a forge and anvil."

    def can_run(self, ctx: SkillContext) -> bool:
        return self._tool(ctx) is not None

    def step(self, ctx: SkillContext) -> SkillResult:
        obs = ctx.obs

        # Reward = Blacksmithing base gained since last tick (each craft trains it).
        reward = 0.0
        base = next((s.base for s in obs.skills if s.id == SKILL_BLACKSMITHING), None)
        prev = ctx.memory.get("bs_base")
        if base is not None:
            if prev is not None and base > prev + 1e-3:
                reward = base - prev
            ctx.memory["bs_base"] = base

        gump = obs.gumps[0] if obs.gumps else None
        state = ctx.memory.get("bs_state", "open")

        # A craft gump is open → press the next button in the sequence.
        if gump is not None:
            ctx.memory["bs_wait"] = 0
            gs, gid = gump.serial, gump.gump_id
            if state in ("open", "category"):
                ctx.memory["bs_state"] = "item"
                return SkillResult(Status.RUNNING, GumpResponse(gs, gid, button=CATEGORY_BTN), reward)
            if state == "item":
                ctx.memory["bs_state"] = "loop"
                return SkillResult(Status.RUNNING, GumpResponse(gs, gid, button=DAGGER_BTN), reward)
            return SkillResult(Status.RUNNING, GumpResponse(gs, gid, button=MAKE_LAST_BTN), reward)

        # No gump open.
        tool = self._tool(ctx)
        if tool is None:
            return SkillResult(Status.FAILURE, None, reward)
        if state == "open":
            ctx.memory["bs_state"] = "category"  # the gump that opens → press category
            return SkillResult(Status.RUNNING, Use(serial=tool.serial), reward)

        # Mid-sequence: the server is crafting (gump briefly gone) — wait for it to
        # re-appear; only re-open with the tool if it's been gone too long.
        wait = ctx.memory.get("bs_wait", 0) + 1
        ctx.memory["bs_wait"] = wait
        if wait < _REOPEN_AFTER:
            return SkillResult(Status.RUNNING, None, reward)
        ctx.memory["bs_wait"] = 0
        ctx.memory["bs_state"] = "open"
        return SkillResult(Status.RUNNING, Use(serial=tool.serial), reward)

    @staticmethod
    def _tool(ctx: SkillContext):
        return next((i for i in ctx.obs.items if i.graphic in SMITH_TOOL_GRAPHICS), None)
