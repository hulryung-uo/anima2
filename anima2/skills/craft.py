"""Crafting skills — make items from a craft gump (blacksmithing first).

Unlike gathering, crafting drives a multi-step **gump** (the ServUO CraftGump):
double-click the tool → a menu opens → pick a category → pick an item → it crafts
and re-shows the menu. Button ids come from ServUO's formula `1 + type + index*7`
(verified in anima v1's `craft_blacksmith.py`). The gump is exposed to the brain
via `Observation.gumps`; we answer with `GumpResponse`.

`Blacksmith` also closes the blacksmith side of the inter-agent economy loop
(DESIGN.md §10 Phase 3 — the miner side is `skills/smelt.py::MineSmeltDeliver`):
when the pack runs short of ingots (a dagger costs 3 — ServUO
`Scripts/Services/Craft/DefBlacksmithy.cs` `AddCraft(typeof(Dagger), ...,
typeof(IronIngot), ..., 3, 1044037)`, cliloc 1044037 = "You do not have
sufficient metal to make that.") and no gump is open, it walks to a dropped
ingot pile within a short radius and `PickUp`s it into the pack (a UO pickup
is a two-step lift-then-place, same shape as `Harvest`'s tool-equip sequence:
`PickUp` to the cursor, then `Drop` into the backpack) before resuming the MAKE
loop. Gated on "no gump open" so it never answers a button the gump is waiting
on with a walk/pickup instead.
"""

from __future__ import annotations

from ..contract import Drop, GumpResponse, PickUp, Position, Use, Walk
from ..geometry import chebyshev, direction_toward
from .base import Skill, SkillContext, SkillResult, Status
from .harvest import BACKPACK_LAYER
from .smelt import INGOT_GRAPHICS

# Blacksmithing tool: a smith's hammer (0x13E3) opens the craft gump even away
# from a forge/anvil (SE+); tongs (0x0FBB/0x0FBC) also work as a craft tool.
SMITH_TOOL_GRAPHICS = frozenset({0x13E3, 0x0FBB, 0x0FBC})
SKILL_BLACKSMITHING = 7
# Cliloc 1044037 — "You do not have sufficient metal to make that." The server's
# own confirmation of starvation; `Blacksmith` doesn't need to parse this to
# *detect* starvation (the pack ingot count already does, deterministically),
# but it's the message a human would see, kept here for readers matching up
# live journal output against this logic (`live_trade.py` watches for it).
NOT_ENOUGH_METAL_CLILOC = 1044037
# Cliloc 1044267 — "You must be near an anvil and a forge to smith items."
# ServUO's `DefBlacksmithy.CanCraft` (`Scripts/Services/Craft/DefBlacksmithy.cs`)
# returns this when `CheckAnvilAndForge` (2-tile range) fails; `CraftGump`'s
# "Create item"/"Make last" button handlers (`Core/CraftGump.cs::CraftItem`)
# check it *synchronously* and re-`SendGump` the same failure, baked into the
# layout the same way `NOT_ENOUGH_METAL_CLILOC` is — confirmed by reading both
# files in the local ServUO checkout, not assumed. Unlike starvation, no amount
# of metal in the pack fixes this — only walking back into range does — so
# it's handled as its own unconditional path in `step()`, not folded into
# `starved`.
PROXIMITY_CLILOC = 1044267
# A dagger — the item this skill crafts — costs 3 ingots; below that, the next
# MAKE click can only fail.
MIN_INGOTS = 3
# How far (tiles) a dropped ingot pile has to be to be worth walking for.
PICKUP_RADIUS = 6
# How close to actually pick up (matches `smelt.py`'s FORGE_REACH order of
# magnitude — a UO pickup needs to be adjacent-ish, not exact).
PICKUP_REACH = 2


def _button(btn_type: int, index: int) -> int:
    """ServUO CraftGump button id (CraftGump.cs GetButtonID)."""
    return 1 + btn_type + index * 7


# Bladed weapons (group 3), Dagger: cheap (3 ingots — ServUO DefBlacksmithy.cs
# `AddCraft(typeof(Dagger), ..., typeof(IronIngot), ..., 3, 1044037)`), craftable
# at ~0 skill. Dagger's index *within* the Bladed group is server-config-
# dependent (DefBlacksmithy.cs's `AddCraft` order shifts with which `Core.XXX`
# expansion flags are active) — **live-verified against this ServUO** by
# decoding the actual CraftGump layout (`{xmfhtmlgumpcolor ... 1023921 ...}` —
# cliloc 1023921 resolves to "dagger") rather than assumed: it's index 2 here,
# *not* 4 (index 4 is a Kryss — cliloc 1025121 — a far more expensive item that
# a thin ingot stock can't sustain; this was a latent bug: it happened to work
# in earlier verification because *any* successful craft still delivers the
# Blacksmithing skill-gain reward, so crafting the wrong item was invisible
# until a bounded ingot supply was actually run dry against it, in Phase 3).
CATEGORY_BTN = _button(0, 3)  # 22 — select the "bladed" category
DAGGER_BTN = _button(1, 2)  # 16 — make a dagger
MAKE_LAST_BTN = _button(6, 2)  # 21 — re-make the last item (the craft loop)
# The Dagger's own item graphic (ServUO Scripts/VendorInfo/SBBlacksmith.cs:
# `GenericBuyInfo(typeof(Dagger), 21, 20, 0xF52, 0)`) — what `skills/market.py`'s
# sell phase matches pack/SellList items against to find crafted daggers, as
# opposed to the tools/ingots also sitting in the pack (a smith must never sell
# its own hammer or its remaining metal, even though SBBlacksmith buys those too).
DAGGER_GRAPHIC = 0x0F52

# How long (ticks) to wait for the craft gump to re-appear before re-opening it
# with the tool — the craft delay is ~2s.
_REOPEN_AFTER = 12


class Blacksmith(Skill):
    """Forge daggers from iron ingots at a forge/anvil, looping with MAKE LAST.

    Needs tongs in the pack, iron ingots, and to stand by a forge + anvil (staged
    by the Control plane). Rewards on Blacksmithing skill gain. When the pack
    runs short of metal, fetches a nearby dropped ingot pile (see the module
    docstring) before resuming the MAKE loop — the blacksmith side of the
    inter-agent trade loop; a solo blacksmith with no miner ever dropping
    anything nearby, or genuinely out of metal with nothing left to fetch,
    doesn't spin forever either — `step()`'s dead-gump watchdog notices a
    reshown craft gump isn't producing any outcome and backs off to the
    ordinary re-open-with-the-tool cadence instead of hammering the same
    dead buttons every tick.
    """

    name = "blacksmith"
    description = "Forge items from iron ingots at a forge and anvil."
    #: Consecutive no-progress walking ticks before giving up on one pile
    #: (mirrors `GoTo.stall_limit` / `MineSmeltDeliver.stall_limit`).
    stall_limit: int = 6
    #: Consecutive gump-answering ticks with zero observable outcome (pack
    #: ingots, pack daggers, and Blacksmithing base all unchanged) before a
    #: reshown gump is treated as dead — see `step()`'s watchdog for why this
    #: exists on top of `stuck_gump`/`proximity_stuck`'s cliloc checks.
    dead_gump_presses: int = 6

    def can_run(self, ctx: SkillContext) -> bool:
        return self._tool(ctx) is not None

    def step(self, ctx: SkillContext) -> SkillResult:
        obs = ctx.obs

        # Remember the forge/anvil stand tile the first time this skill runs,
        # so a fetch that pulls the smith away from it (see `_fetch_step`) has
        # somewhere to walk back to before resuming the MAKE loop.
        ctx.memory.setdefault("bs_stand", (obs.player.pos.x, obs.player.pos.y))

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

        # Cliloc-independent dead-gump watchdog. `stuck_gump`/`proximity_stuck`
        # below only recognize a reshow by a *specific* baked-in cliloc — a
        # third variant slips past both (live-caught: a truly-starved MAKE
        # LAST press with nothing on the ground to fetch reshows a gump whose
        # layout contains neither `NOT_ENOUGH_METAL_CLILOC` nor
        # `PROXIMITY_CLILOC`), and the existing state machine has no other way
        # to notice: `stuck_gump`'s own fetch-and-fail resets `bs_state` to
        # "open" every tick with nothing to show for it, so it advances
        # open→item→loop→[fails, reshows]→open→item→... forever — a fresh
        # gump serial every single tick, never actually giving up. This tracks
        # the *outcome* that actually matters (pack ingots, pack daggers,
        # Blacksmithing base) instead of any particular gump's text: once
        # `dead_gump_presses` consecutive ticks pass with a gump open and none
        # of the three having moved, the gump is treated as dead regardless of
        # its layout — `gump` is nulled out here so `stuck_gump`,
        # `proximity_stuck`, and the button-press block below all run exactly
        # as if none were open, falling through to the ordinary
        # re-open-with-the-tool path (paced by `_REOPEN_AFTER`, not every
        # tick) instead of hammering the same dead buttons.
        progress = (self._pack_ingots(ctx), self._pack_daggers(ctx), round(base, 1) if base is not None else None)
        if gump is not None:
            if ctx.memory.get("bs_progress") == progress:
                dead = ctx.memory.get("bs_dead_presses", 0) + 1
                ctx.memory["bs_dead_presses"] = dead
                if dead >= self.dead_gump_presses:
                    gump = None
                    ctx.memory["bs_state"] = state = "open"
                    ctx.memory["bs_dead_presses"] = 0
            else:
                ctx.memory["bs_dead_presses"] = 0
        ctx.memory["bs_progress"] = progress

        # A truly-out-of-metal MAKE LAST press fails *synchronously* (no craft
        # to animate) and CraftGump.cs's `CraftItem` just re-`SendGump`s the
        # same category/item list with the failure message baked into its
        # layout (`CraftGump.CanCraft` → `num > 0` → `SendGump(..., num)`) —
        # live-observed: `gump` is non-`None` on *every* tick once starved, not
        # just between attempts. Pressing that button again can never succeed,
        # so treat this one specific gump the same as "no gump open" below —
        # otherwise the smith could never notice a delivered ground pile once
        # truly out of metal (`gump is None` would never come true again).
        stuck_gump = gump is not None and str(NOT_ENOUGH_METAL_CLILOC) in gump.layout
        # The *other* synchronous-failure reshow (see `PROXIMITY_CLILOC`): a
        # fetch (or anything else) left the smith out of forge/anvil range.
        # Pressing buttons on this gump can never succeed no matter the pack's
        # ingot count, so — unlike the metal-fetch path below — this check
        # runs unconditionally, before `starved` is even computed, and keeps
        # driving (`state == "fetch_return"`) until back in range. If the walk
        # itself wedges, `_fetch_return_step` gives up and we try to craft
        # again from wherever we are, which (still out of range) reproduces
        # the same reshow next tick and retries — a self-healing loop rather
        # than a silent freeze, since transient blockers (another agent, a
        # dropped pile) tend to clear on their own.
        proximity_stuck = gump is not None and str(PROXIMITY_CLILOC) in gump.layout
        if state == "fetch_return" or proximity_stuck:
            result = self._fetch_return_step(ctx, reward)
            if result is not None:
                ctx.memory["bs_state"] = "fetch_return"
                return result
            ctx.memory["bs_state"] = state = "open"  # back in range — resume crafting

        # Starved of metal — go fetch a dropped ingot pile instead of hammering
        # an empty MAKE loop. The pack ingot count is the primary, deterministic
        # signal; the server's own "not enough metal" cliloc (which a failed
        # MAKE click provokes, in the journal *or*, per above, baked into the
        # reshown gump) is an extra corroborating trigger in case the count
        # check somehow missed it. Gated on no *answerable* gump being open:
        # never answer a button the gump is waiting on with a walk/pickup
        # instead — this can only kick in between craft attempts (or on the
        # terminal stuck gump above). `state == "fetch"` keeps an in-flight
        # pickup (walk → PickUp → Drop-into-pack is multi-tick) running to
        # completion even if a pickup mid-trip already pushed us back over
        # `MIN_INGOTS`.
        starved = self._pack_ingots(ctx) < MIN_INGOTS or any(
            j.cliloc == NOT_ENOUGH_METAL_CLILOC for j in obs.new_journal
        )
        if (gump is None or stuck_gump) and (state == "fetch" or starved):
            result = self._fetch_step(ctx, reward)
            if result is not None:
                return result
            ctx.memory["bs_state"] = state = "open"  # nothing left to fetch — resume crafting

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

    def _fetch_step(self, ctx: SkillContext, reward: float) -> SkillResult | None:
        """One fetch tick, or `None` when there's nothing (more) to fetch and
        the smith is back at its forge/anvil stand tile.

        A UO pickup is two actions — `PickUp` lifts the pile to the cursor,
        then `Drop` places it in a container — mirroring `Harvest`'s tool-equip
        sequence (`PickUp` then `Equip`); `bs_fetch_held` remembers the lifted
        serial across that tick boundary the same way `harvest_tool` does there.

        A pile can be up to `PICKUP_RADIUS` tiles out, well past
        `CheckAnvilAndForge`'s 2-tile crafting range, so once nothing more is
        left to fetch this walks back to `bs_stand` (see `_fetch_return_step`)
        before handing back to `step()`'s MAKE loop — otherwise every craft
        attempt fails a proximity check that isn't the "not enough metal" gump
        `stuck_gump` recognizes, and the smith is stuck pressing a dead button.
        """
        bp = self._backpack(ctx)
        if bp is None:
            return None
        # Mark ourselves mid-fetch so `step()`'s `state == "fetch"` check keeps
        # driving this trip to completion even on a tick where `starved` has
        # already flipped false (e.g. a stack merge nudges the count up while a
        # lifted item is still waiting on its Drop-into-pack) — matches the
        # class docstring's "never leave a picked-up item stuck in hand".
        ctx.memory["bs_state"] = "fetch"

        held = ctx.memory.pop("bs_fetch_held", None)
        if held is not None:
            return SkillResult(Status.RUNNING, Drop(serial=held, container=bp.serial), reward)

        pile = self._nearby_ground_ingots(ctx)
        if pile is not None:
            if pile.distance <= PICKUP_REACH:
                ctx.memory.pop("bs_fetch_stall", None)
                ctx.memory.pop("bs_fetch_last_pos", None)
                ctx.memory["bs_fetch_held"] = pile.serial
                return SkillResult(Status.RUNNING, PickUp(serial=pile.serial, amount=pile.amount), reward)

            here = ctx.obs.player.pos
            cur = (here.x, here.y)
            stall = ctx.memory.get("bs_fetch_stall", 0) + 1 if ctx.memory.get("bs_fetch_last_pos") == cur else 0
            ctx.memory["bs_fetch_stall"] = stall
            ctx.memory["bs_fetch_last_pos"] = cur
            if stall < self.stall_limit:
                return SkillResult(Status.RUNNING, Walk(dir=direction_toward(here, pile.pos), run=False), reward)
            # wedged — give up this pile and fall through to walking home
            ctx.memory.pop("bs_fetch_stall", None)
            ctx.memory.pop("bs_fetch_last_pos", None)

        return self._fetch_return_step(ctx, reward)

    def _fetch_return_step(self, ctx: SkillContext, reward: float) -> SkillResult | None:
        """Walk back to the forge/anvil stand tile after a fetch, or `None`
        once there (or if no stand tile was ever recorded) — resume the MAKE
        loop. Mirrors `MineSmeltDeliver._return_step`.
        """
        stand = ctx.memory.get("bs_stand")
        if stand is None:
            return None
        here = ctx.obs.player.pos
        sx, sy = stand
        if chebyshev(here, Position(sx, sy, here.z)) == 0:
            ctx.memory.pop("bs_return_stall", None)
            ctx.memory.pop("bs_return_last_pos", None)
            return None

        cur = (here.x, here.y)
        stall = ctx.memory.get("bs_return_stall", 0) + 1 if ctx.memory.get("bs_return_last_pos") == cur else 0
        ctx.memory["bs_return_stall"] = stall
        ctx.memory["bs_return_last_pos"] = cur
        if stall >= self.stall_limit:
            ctx.memory.pop("bs_return_stall", None)
            ctx.memory.pop("bs_return_last_pos", None)
            return None  # wedged — resume crafting from wherever we are; better than looping forever
        return SkillResult(
            Status.RUNNING, Walk(dir=direction_toward(here, Position(sx, sy, here.z)), run=False), reward,
        )

    def _pack_ingots(self, ctx: SkillContext) -> int:
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(i.amount for i in ctx.obs.items if i.graphic in INGOT_GRAPHICS and i.container == bp.serial)

    def _pack_daggers(self, ctx: SkillContext) -> int:
        """Crafted daggers currently held — one leg of `step()`'s dead-gump
        watchdog's progress signature (a successful craft moves this, even on
        a tick where the ingot count alone might look ambiguous mid-stack-
        merge). Mirrors `BlacksmithMarket._pack_daggers` (market.py); kept
        separate rather than shared since this class predates that one and
        has no dependency on it.
        """
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(i.amount for i in ctx.obs.items if i.graphic == DAGGER_GRAPHIC and i.container == bp.serial)

    @staticmethod
    def _nearby_ground_ingots(ctx: SkillContext):
        # `container is None` (not merely "graphic matches") is essential — our
        # *own* pack ingots also carry `graphic in INGOT_GRAPHICS` and, being a
        # contained item, report the placeholder (0,0,0) position, which can
        # read as "distance 0, right here" (the exact footgun `Harvest.
        # _backpack`'s docstring calls out for another mobile's backpack).
        # `items` is sorted by distance (Observation's own invariant), so the
        # first match is the nearest.
        return next(
            (i for i in ctx.obs.items
             if i.graphic in INGOT_GRAPHICS and i.container is None and i.distance <= PICKUP_RADIUS),
            None,
        )

    @staticmethod
    def _backpack(ctx: SkillContext):
        # Mirrors `Harvest._backpack` (filter by owner, not just layer — see
        # its docstring for why layer alone can tie on distance with someone
        # else's pack). Duplicated rather than shared because `Blacksmith`
        # isn't a `Harvest` subclass (crafting isn't gathering).
        return next((i for i in ctx.obs.items
                    if i.layer == BACKPACK_LAYER and i.container == ctx.obs.player.serial), None)

    @staticmethod
    def _tool(ctx: SkillContext):
        return next((i for i in ctx.obs.items if i.graphic in SMITH_TOOL_GRAPHICS), None)
