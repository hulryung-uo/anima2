"""Smelting — turn mined ore into ingots at a forge, folded into the miner's job.

Closes the ore→ingot half of the village economy chain (the blacksmith already
consumes IronIngot — see `craft.py`). UO smelting is a **target** interaction
just like harvesting: double-click an ore pile → the server opens a target
cursor → target a forge → `Ore.OnDoubleClick`/`InternalTarget` (ServUO
`Scripts/Items/Resource/Ore.cs`) consumes the pile and adds ingots to the pack
(cliloc 501988), or fails cleanly (too small a pile: 501987; ore type too hard
for the miner's skill: 501986) — never a hard error, so ingots-gained is the
only reward signal we need.

`MineAndSmelt` alternates two phases in one skill so the planner/profession
wiring stays a single `work_skill` swap: mine until the backpack holds
`ore_threshold` total ore — threshold on summed `amount`, not item count, since
a dig only merges into an *existing* pile of the exact same graphic (see
`ORE_GRAPHICS` below), so a haul is usually one to a few piles, not always one —
then smelt it all at a forge staged within reach of the workplace
(`Profession.structures = [("Forge", dx, dy)]`), then resume mining. Ore.cs
smelts the *whole* targeted pile per attempt, so smelting one pile is usually a
single Use→TargetObject exchange; `_pack_ore` walks piles one at a time each
tick until none are left. `Mine` never walks (it probes surrounding tiles from
a fixed stand spot), so a forge placed a couple of tiles from the workplace
stays in reach for the whole shift — no navigation needed.
"""

from __future__ import annotations

from ..contract import TargetObject, Use
from .base import SkillContext, SkillResult, Status
from .harvest import Mine

# ServUO ore piles (Scripts/Items/Resource/Ore.cs BaseOre.RandomSize) — small
# pile, common, large pile, mid pile. Each dig rolls one of these graphics for a
# *new* stack; `Item.WillStack` requires an exact `ItemID` match to merge with an
# existing pile, so a miner can end up carrying several distinct piles at once,
# not just one growing stack.
ORE_GRAPHICS = frozenset({0x19B7, 0x19B8, 0x19B9, 0x19BA})
# The "small pile" graphic — Ore.cs hard-fails smelting it ("not enough
# metal-bearing ore in this pile") whenever its amount is below 2, forever, until
# it accumulates more (which requires *another* small-pile dig to stack onto it).
SMALL_ORE_GRAPHIC = 0x19B7
# ServUO ingot stacks (BaseIngot subclasses use the same 4 art ids as ore does
# for stack-size variants).
INGOT_GRAPHICS = frozenset({0x1BEF, 0x1BF0, 0x1BF1, 0x1BF2})
# A forge: the `Forge` item class (0x0FB1) plus the static forge tile range and
# expansion variants ServUO's `Ore.IsForge`/`CheckAnvilAndForge` both accept.
FORGE_GRAPHICS = frozenset({0x0FB1, 0x2DD8, 0xA531, 0xA535}) | frozenset(range(0x197A, 0x19AA))
# Ore.cs targets the forge within 2 tiles (`InternalTarget(2, ...)`).
FORGE_REACH = 2


class MineAndSmelt(Mine):
    """Mine ore; once the backpack fills up, smelt it at a nearby forge, then resume.

    Rewards mining the same way `Mine` does (skill-base gain) while in the mine
    phase, and rewards ingots gained in the backpack while in the smelt phase.
    """

    name = "mine_and_smelt"
    description = "Mine ore, then smelt the haul into ingots at a nearby forge."
    #: Total backpack ore (summed `amount`, not pile count) that triggers a smelt run.
    ore_threshold: int = 5

    def step(self, ctx: SkillContext) -> SkillResult:
        obs = ctx.obs
        phase = ctx.memory.get("smelt_phase", "mine")

        # Only switch into smelting between swings — an open cursor mid-mine is
        # answered by `Mine`'s own probe logic, not the smelt target logic below.
        if phase == "mine" and obs.pending_target is None:
            if self._pack_ore_amount(ctx) >= self.ore_threshold:
                phase = ctx.memory["smelt_phase"] = "smelt"

        if phase == "smelt":
            result = self._smelt_step(ctx)
            if result is not None:
                return result
            ctx.memory["smelt_phase"] = "mine"  # ore gone (or gave up) — back to work

        return super().step(ctx)

    def _smelt_step(self, ctx: SkillContext) -> SkillResult | None:
        """One smelting tick, or `None` when the smelt run is over (resume mining)."""
        obs = ctx.obs

        ingots_now = self._ingot_count(ctx)
        prev = ctx.memory.get("smelt_ingots")
        reward = ingots_now - prev if prev is not None and ingots_now > prev else 0.0
        ctx.memory["smelt_ingots"] = ingots_now

        # Cursor open (from double-clicking ore) → target the forge.
        if obs.pending_target is not None:
            forge = self._forge(ctx)
            if forge is None:
                # No forge in reach (shouldn't happen — it's staged at the
                # workplace) — bail out and let `Mine`'s own cursor handling
                # answer the stray target with a ground probe.
                return None
            return SkillResult(Status.RUNNING, TargetObject(serial=forge.serial), reward)

        ore = self._pack_ore(ctx)
        if ore is None or self._forge(ctx) is None:
            return None  # nothing left to smelt, or the forge went out of reach

        return SkillResult(Status.RUNNING, Use(serial=ore.serial), reward)

    def _pack_ore(self, ctx: SkillContext):
        bp = self._backpack(ctx)
        if bp is None:
            return None
        return next(
            (i for i in ctx.obs.items
             if i.graphic in ORE_GRAPHICS and i.container == bp.serial
             and not (i.graphic == SMALL_ORE_GRAPHIC and i.amount < 2)),
            None,
        )

    def _pack_ore_amount(self, ctx: SkillContext) -> int:
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(i.amount for i in ctx.obs.items if i.graphic in ORE_GRAPHICS and i.container == bp.serial)

    def _ingot_count(self, ctx: SkillContext) -> int:
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(i.amount for i in ctx.obs.items if i.graphic in INGOT_GRAPHICS and i.container == bp.serial)

    def _forge(self, ctx: SkillContext):
        return next((i for i in ctx.obs.items if i.graphic in FORGE_GRAPHICS and i.distance <= FORGE_REACH), None)
