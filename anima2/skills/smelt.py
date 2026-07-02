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

`MineSmeltDeliver` (Phase 3) adds a third phase on top: once the smelted haul
reaches `deliver_threshold`, walk to a configured smithy drop point, `Drop` the
ingots there (ground, for a blacksmith to `PickUp` — see `craft.py`'s
`Blacksmith._fetch_step`), then walk back and resume mining. This is the miner
side of the first inter-agent economy loop (DESIGN.md §10 Phase 3). It's
opt-in via `ctx.memory["smithy_drop"]` (an (x, y) tuple the village wiring
plumbs in exactly like `harvest_nodes` — see `village.py`); with no drop point
configured, `step()` defers to `MineAndSmelt` unchanged, so every existing
miner (and the offline demo) behaves exactly as before.
"""

from __future__ import annotations

from ..contract import Drop, PickUp, Position, TargetObject, Use, Walk
from ..geometry import chebyshev, direction_toward
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
                return self._payout(ctx, result)
            ctx.memory["smelt_phase"] = "mine"  # ore gone (or gave up) — back to work

        return self._payout(ctx, super().step(ctx))

    def _bank(self, ctx: SkillContext, reward: float) -> None:
        """Stash a reward this tick's caller is about to discard (a bare `None`
        return has nowhere to carry it) so the very next `SkillResult` this skill
        returns — even from a different phase, later in the same `step()` call —
        pays it out instead. Closes the one-tick observation-lag gap where a
        confirmed pack change (ore consumed, ingots dropped) lands on exactly the
        tick a phase's "nothing left to do" scan comes up empty: without this, that
        `None` would silently drop the whole delta on the floor.
        """
        if reward:
            ctx.memory["banked_reward"] = ctx.memory.get("banked_reward", 0.0) + reward

    def _payout(self, ctx: SkillContext, result: SkillResult) -> SkillResult:
        """Fold any reward stashed by `_bank` into `result` before it leaves the skill."""
        banked = ctx.memory.pop("banked_reward", 0.0)
        if not banked:
            return result
        return SkillResult(result.status, result.action, result.reward + banked)

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
                self._bank(ctx, reward)
                return None
            return SkillResult(Status.RUNNING, TargetObject(serial=forge.serial), reward)

        ore = self._pack_ore(ctx)
        if ore is None or self._forge(ctx) is None:
            self._bank(ctx, reward)
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


class MineSmeltDeliver(MineAndSmelt):
    """Mine, smelt, and — once configured with a smithy — haul the ingots there.

    Adds a `deliver`/`return` pair of phases on top of `MineAndSmelt`'s
    `mine`/`smelt`: once the pack holds `deliver_threshold` ingots (checked
    between mining swings, same guard `MineAndSmelt` uses for the smelt
    transition), walk greedily (stepwise, no A* — `geometry.direction_toward`,
    same technique `skills/movement.py::GoTo` uses) to within one tile of
    `ctx.memory["smithy_drop"]` (a ground `Drop` reaches 2 tiles, and stopping
    short avoids needing to step onto whatever occupies that tile — usually
    the blacksmith itself), `Drop` every ingot pile there one at a time, walk
    back to the spot mining started from, then resume. A delivery leg that
    wedges permanently (see `_walk_toward`) gives up rather than retrying
    forever, and the mine-phase trigger backs off until the pack holds more
    ingots than it did at give-up, so a blocked corridor costs one failed trip,
    not an endless commute. **Opt-in**: with no `smithy_drop` in scratch
    memory, `step()` falls straight through to `MineAndSmelt.step()` — today's
    behaviour, byte for byte, so the offline demo and every existing miner
    (and test) is unaffected.

    The drop point is plumbed in by the village wiring exactly like a
    lumberjack's grove or a fisher's water tile (`agent.memory["smithy_drop"]
    = (x, y)`; see `village.py`) rather than a constructor argument, because
    `Profession.work_skill` is a zero-arg `Callable[[], Skill]` — this keeps
    "one work skill per profession" true without complicating that seam.
    """

    name = "mine_smelt_deliver"
    description = "Mine and smelt ore, then haul the ingots to a smithy and drop them for the blacksmith."
    #: Total backpack ingots (summed `amount`) that triggers a delivery run.
    deliver_threshold: int = 10
    #: Consecutive no-progress walking ticks before a delivery/return leg gives
    #: up (mirrors `GoTo.stall_limit` — the greedy mover has no A*).
    stall_limit: int = 6

    def step(self, ctx: SkillContext) -> SkillResult:
        smithy = ctx.memory.get("smithy_drop")
        if smithy is None:
            return super().step(ctx)  # backwards compatible: plain MineAndSmelt

        obs = ctx.obs
        # Remember where the shift started (the ore-bank stand spot) the first
        # time this skill runs, so `return` walks back to exactly the tile
        # `Mine`'s probing/forge reach is calibrated for.
        ctx.memory.setdefault("miner_home", (obs.player.pos.x, obs.player.pos.y))
        phase = ctx.memory.get("smelt_phase", "mine")

        # Only leave for delivery between mining swings, same as the mine→smelt
        # guard above — an open cursor is answered by Mine's own probe logic.
        if phase == "mine" and obs.pending_target is None:
            ingots = self._ingot_count(ctx)
            # `deliver_giveup_ingots` (set by `_walk_toward` when a delivery leg
            # wedges permanently) blocks an immediate re-trigger with the exact
            # same haul still in the pack — that "give up, walk home, immediately
            # walk right back into the same obstruction" cycle is a livelock in
            # all but name. Requiring the pack to grow *past* the failed attempt's
            # count first (more mining/smelting has to happen) is a natural
            # backoff with no tick-counting needed.
            if ingots >= self.deliver_threshold and ingots > ctx.memory.get("deliver_giveup_ingots", -1):
                ctx.memory.pop("deliver_giveup_ingots", None)
                phase = ctx.memory["smelt_phase"] = "deliver"

        if phase == "deliver":
            result = self._deliver_step(ctx, smithy)
            if result is not None:
                return self._payout(ctx, result)
            ctx.memory.pop("deliver_ingots_start", None)
            ctx.memory.pop("deliver_paid", None)
            phase = ctx.memory["smelt_phase"] = "return"

        if phase == "return":
            result = self._return_step(ctx)
            if result is not None:
                return self._payout(ctx, result)
            ctx.memory["smelt_phase"] = "mine"

        return self._payout(ctx, super().step(ctx))

    def _deliver_step(self, ctx: SkillContext, smithy: tuple[int, int]) -> SkillResult | None:
        """One delivery tick, or `None` when the trip is over (resume via `return`).

        A UO ground drop is **two** packets, same as a pickup elsewhere in this
        package (`craft.py::Blacksmith._fetch_step`, `harvest.py`'s tool-equip
        sequence): `PickUp` lifts the pile to the cursor — a bare `Drop` with no
        prior `PickUp` is illegal and the server silently ignores it (confirmed
        live: piles never left the pack until this was two actions) — *then*
        `Drop` places it on the ground. `deliver_held` remembers the lifted
        serial across that tick boundary the same way `bs_fetch_held` does.

        The drop point only needs to be reached to **chebyshev 1** (adjacent),
        not stood on: a ground `Drop` has a 2-tile range, and `smithy` is
        typically the blacksmith's own permanently-occupied stand tile —
        walking onto it would depend on ServUO's shove mechanic (full stamina
        required, -10 stam) for every single delivery. Dropping from next to it
        removes that dependency entirely.

        Reward is earned only for ingots **confirmed net-gone from the pack**
        over the whole delivery phase — not on every observed pack decrease.
        A server-rejected `Drop` bounces the pile straight back into the pack
        (`Item.Bounce`) with no corresponding decrease recorded, so paying on
        every decrease would pay the same ingots again on the retry lift;
        tracking cumulative confirmed loss against `deliver_ingots_start` and
        only paying the amount not yet in `deliver_paid` makes a bounce net
        zero reward instead of a double payment. That confirmation can land on
        the very tick the pile scan below comes up empty (one tick of
        observation lag behind the final `Drop`); `_bank`/`_payout` (see
        `step()`) carry that reward across the `None` return rather than
        dropping it. `step()` clears `deliver_ingots_start`/`deliver_paid`
        whenever the deliver phase ends, so a later delivery run (or a retry
        after a wedged give-up) starts its accounting fresh.
        """
        obs = ctx.obs
        ingots_now = self._ingot_count(ctx)
        start = ctx.memory.get("deliver_ingots_start")
        if start is None:
            start = ctx.memory["deliver_ingots_start"] = ingots_now
        paid = ctx.memory.get("deliver_paid", 0.0)
        confirmed_decrease = max(0, start - ingots_now)
        reward = confirmed_decrease - paid
        if reward > 0:
            ctx.memory["deliver_paid"] = paid + reward
        else:
            reward = 0.0

        here = obs.player.pos
        tx, ty = smithy
        if chebyshev(here, Position(tx, ty, here.z)) > 1:
            return self._walk_toward(ctx, tx, ty, "deliver", reward)

        ctx.memory.pop("deliver_stall", None)
        ctx.memory.pop("deliver_last_pos", None)

        held = ctx.memory.pop("deliver_held", None)
        if held is not None:
            return SkillResult(
                Status.RUNNING, Drop(serial=held, x=tx, y=ty, z=here.z, container=0xFFFFFFFF), reward,
            )

        pile = self._pack_ingot_pile(ctx)
        if pile is None:
            self._bank(ctx, reward)
            return None  # nothing left to drop — the haul is delivered
        ctx.memory["deliver_held"] = pile.serial
        return SkillResult(Status.RUNNING, PickUp(serial=pile.serial, amount=pile.amount), reward)

    def _return_step(self, ctx: SkillContext) -> SkillResult | None:
        """Walk back to `miner_home`, or `None` once there (resume mining)."""
        home = ctx.memory.get("miner_home")
        if home is None:
            return None  # nothing recorded (shouldn't happen) — just resume from here
        here = ctx.obs.player.pos
        hx, hy = home
        if chebyshev(here, Position(hx, hy, here.z)) == 0:
            ctx.memory.pop("return_stall", None)
            ctx.memory.pop("return_last_pos", None)
            return None
        return self._walk_toward(ctx, hx, hy, "return")

    def _walk_toward(self, ctx: SkillContext, tx: int, ty: int, tag: str,
                     reward: float = 0.0) -> SkillResult | None:
        """One greedy step toward `(tx, ty)`, `stall_limit`-bounded like `GoTo`.

        `None` means wedged — give up this leg (the caller advances the phase
        anyway rather than retrying into the same obstruction forever).
        """
        here = ctx.obs.player.pos
        cur = (here.x, here.y)
        stall_key, pos_key = f"{tag}_stall", f"{tag}_last_pos"
        stall = ctx.memory.get(stall_key, 0) + 1 if ctx.memory.get(pos_key) == cur else 0
        ctx.memory[stall_key] = stall
        ctx.memory[pos_key] = cur
        if stall >= self.stall_limit:
            ctx.memory.pop(stall_key, None)
            ctx.memory.pop(pos_key, None)
            if tag == "deliver":
                # The haul is still in the pack — record how much so the
                # mine-phase trigger (see `step()`) doesn't send us right back
                # into the same obstruction on the very next tick.
                ctx.memory["deliver_giveup_ingots"] = self._ingot_count(ctx)
            self._bank(ctx, reward)
            return None
        d = direction_toward(here, Position(tx, ty, here.z))
        return SkillResult(Status.RUNNING, Walk(dir=d, run=False), reward)

    def _pack_ingot_pile(self, ctx: SkillContext):
        bp = self._backpack(ctx)
        if bp is None:
            return None
        return next((i for i in ctx.obs.items if i.graphic in INGOT_GRAPHICS and i.container == bp.serial), None)
