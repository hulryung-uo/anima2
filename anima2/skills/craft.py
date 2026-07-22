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
CRAFT_FAILURE_CLILOC = 1044043
CRAFT_FAILURE_NO_LOSS_CLILOC = 1044157
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
RESOURCE_MENU_BTN = _button(6, 0)  # 7 — open material selection
IRON_RESOURCE_BTN = _button(5, 0)  # 6 — reset the remembered resource to iron
CRAFT_TITLE_CLILOC = 1044002
DAGGER_NAME_CLILOC = 1023921
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

    # --- craft config (generalize the CraftGump MAKE loop to a new profession +
    # recipe without forking — carpenter/tinker reuse these). The defaults are the
    # blacksmith's, so every blacksmith path resolves exactly what it did before
    # (byte-identical); a carpenter/tinker craft skill overrides them.
    #: The tool that opens this recipe's craft gump (blacksmith: hammer/tongs).
    craft_tool_graphics: frozenset[int] = SMITH_TOOL_GRAPHICS
    #: The gump category button (`_button(0, category_index)`) and item button
    #: (`_button(1, item_index)`) for the crafted item; make-last is 21 across all
    #: ServUO craft systems (`_button(6, 2)`).
    craft_category_btn: int = CATEGORY_BTN
    craft_item_btn: int = DAGGER_BTN
    craft_make_last_btn: int = MAKE_LAST_BTN
    #: Resource-select stages: the blacksmith resets the remembered metal to iron
    #: (RESOURCE_MENU_BTN then IRON_RESOURCE_BTN). `None` on both SKIPS them
    #: (carpentry has no material submenu — open -> category directly).
    craft_resource_menu_btn: int | None = RESOURCE_MENU_BTN
    craft_material_resource_btn: int | None = IRON_RESOURCE_BTN
    #: The material this recipe consumes (blacksmith: iron ingots), how many per
    #: item, and the crafted item's own pack graphic.
    craft_material_graphics: frozenset[int] = INGOT_GRAPHICS
    craft_material_per_item: int = MIN_INGOTS
    craft_output_graphic: int = DAGGER_GRAPHIC
    #: The item-page safety cliloc (the item name shown on the gump). `None`
    #: relies on `_has_reply(gump, craft_item_btn)` alone.
    craft_item_name_cliloc: int | None = DAGGER_NAME_CLILOC
    #: Fill-to-N batch target (blacksmith: 5 daggers = one sale batch; a big item
    #: like the carpenter's Throne uses 1).
    craft_batch: int = 5
    #: The craft gump's title cliloc — ServUO gives each craft SYSTEM its own
    #: `GumpTitleNumber` (blacksmithy 1044002, carpentry 1044004, tinkering
    #: 1044007), so `_craft_gump` must key on THIS recipe's title, not a shared
    #: constant, or a sibling profession's gump opens invisibly and the FSM
    #: stalls at "category" forever waiting for a gump it never recognizes.
    craft_title_cliloc: int = CRAFT_TITLE_CLILOC

    def can_run(self, ctx: SkillContext) -> bool:
        return self._tool(ctx) is not None

    def diagnose(self, ctx: SkillContext) -> str | None:
        """`None` iff `can_run`; else a one-liner explaining *why* — richer
        than the ABC default's generic fallback. A missing tool is the only
        thing `can_run` itself gates on (see above — narrowing it further to
        also cover ingot starvation would stop the planner from ever
        selecting `Blacksmith` while starved, breaking the live-verified
        starvation-fetch loop in `step()`/`_fetch_step`, which needs to run
        *precisely* in that state to go find a dropped pile). So this adds a
        second, richer diagnostic *on top of* `can_run` rather than folding
        it in: even when `can_run` is `True` (tool present), a starved smith
        with nothing in reach to fetch is effectively stuck — worth
        surfacing to item 5's eligibility reasoning even though the skill
        remains technically runnable.
        """
        if self._tool(ctx) is None:
            return "no smithing tool (hammer/tongs) in pack"
        if self._pack_ingots(ctx) < self.craft_material_per_item and self._nearby_ground_ingots(ctx) is None:
            return "starved of ingots, no pile in range"
        return None

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
        starved = self._pack_ingots(ctx) < self.craft_material_per_item or any(
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
                return SkillResult(Status.RUNNING, GumpResponse(gs, gid, button=self.craft_category_btn), reward)
            if state == "item":
                ctx.memory["bs_state"] = "loop"
                return SkillResult(Status.RUNNING, GumpResponse(gs, gid, button=self.craft_item_btn), reward)
            return SkillResult(Status.RUNNING, GumpResponse(gs, gid, button=self.craft_make_last_btn), reward)

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
        """Pack amount of this recipe's material (`self.craft_material_graphics`;
        blacksmith: iron ingots). Byte-identical for the smith."""
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(i.amount for i in ctx.obs.items if i.graphic in self.craft_material_graphics and i.container == bp.serial)

    def _pack_daggers(self, ctx: SkillContext) -> int:
        """Crafted output items currently held (`self.craft_output_graphic`;
        blacksmith: daggers) — one leg of `step()`'s dead-gump watchdog's
        progress signature (a successful craft moves this, even on a tick where
        the material count alone might look ambiguous mid-stack-merge)."""
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(i.amount for i in ctx.obs.items if i.graphic == self.craft_output_graphic and i.container == bp.serial)

    def _nearby_ground_ingots(self, ctx: SkillContext):
        # `container is None` (not merely "graphic matches") is essential — our
        # *own* pack material also carries these graphics and, being a
        # contained item, reports the placeholder (0,0,0) position, which can
        # read as "distance 0, right here" (the exact footgun `Harvest.
        # _backpack`'s docstring calls out for another mobile's backpack).
        # `items` is sorted by distance (Observation's own invariant), so the
        # first match is the nearest. Uses `self.craft_material_graphics`.
        return next(
            (i for i in ctx.obs.items
             if i.graphic in self.craft_material_graphics and i.container is None and i.distance <= PICKUP_RADIUS),
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

    def _tool(self, ctx: SkillContext):
        return next((i for i in ctx.obs.items if i.graphic in self.craft_tool_graphics), None)


class CraftItemCapability(Blacksmith):
    """Capability hands that make one observed craft batch and do nothing else —
    the generalized base for `CraftDaggers` (blacksmith) and `CarpenterCraft`
    (carpenter). Subclasses only set the `craft_*` config attrs (inherited from
    `Blacksmith`, smith defaults) plus `name`/`description`; the whole gump FSM
    below reads those, so nothing forks per recipe.

    The ordinary work skill can fetch ground material and walk back to its forge.
    This leaf deliberately disables both powers: admission requires owned pack
    material, an owned pack tool, and the exact configured craft stand. The only
    emitted actions are that tool's ``Use`` and responses to the craft gump
    opened for this goal (plus button 0 to close it at the terminal yield). (The
    ``cap_craft_*``/``*_daggers`` memory keys keep their legacy names.)
    """

    max_goal_steps = 240
    max_attempts = 20

    def _tool(self, ctx: SkillContext):
        backpack = self._backpack(ctx)
        if backpack is None:
            return None
        return next(
            (
                item
                for item in ctx.obs.items
                if item.graphic in self.craft_tool_graphics
                and item.container == backpack.serial
            ),
            None,
        )

    def _fetch_step(self, ctx: SkillContext, reward: float) -> SkillResult | None:
        return None

    def _fetch_return_step(
        self, ctx: SkillContext, reward: float
    ) -> SkillResult | None:
        return None

    def _begin_goal(self, ctx: SkillContext) -> bool:
        goal_id = ctx.goal_id
        if type(goal_id) is not int:
            return False
        if ctx.memory.get("cap_craft_goal_id") == goal_id:
            return True
        backpack = self._backpack(ctx)
        if backpack is None:
            return False
        ctx.memory["cap_craft_goal_id"] = goal_id
        ctx.memory["cap_craft_start_ingots"] = self._pack_ingots(ctx)
        ctx.memory["cap_craft_start_daggers"] = tuple(
            sorted(
                (item.serial, item.amount)
                for item in ctx.obs.items
                if item.graphic == self.craft_output_graphic
                and item.container == backpack.serial
            )
        )
        ctx.memory["cap_craft_start_pos"] = (
            ctx.obs.player.pos.x,
            ctx.obs.player.pos.y,
        )
        start_count = sum(amount for _serial, amount in ctx.memory["cap_craft_start_daggers"])
        ctx.memory["cap_craft_needed"] = max(0, self.craft_batch - start_count)
        ctx.memory["cap_craft_confirmed"] = 0
        ctx.memory["cap_craft_produced"] = ()
        ctx.memory["cap_craft_failed_attempts"] = 0
        ctx.memory["cap_craft_failed_ingots"] = 0
        ctx.memory["cap_craft_failure_costs"] = ()
        ctx.memory["cap_craft_stage"] = "open"
        ctx.memory["cap_craft_attempts"] = 0
        ctx.memory["cap_craft_steps"] = 0
        for key in (
            "cap_craft_tool_serial",
            "cap_craft_gump_id",
            "cap_craft_dagger_button_goal_id",
            "cap_craft_attempt_daggers",
            "cap_craft_attempt_ingots",
            "cap_craft_attempt_gump_serial",
            "cap_craft_attempt_wait",
            "cap_craft_ingots_used",
            "cap_craft_abort_goal_id",
            "cap_craft_close_sent",
            "cap_craft_close_reopen_sent",
            "cap_craft_close_absent_wait",
            "cap_craft_close_reopen_wait",
            "cap_craft_finished_goal_id",
            "cap_craft_returned_goal_id",
        ):
            ctx.memory.pop(key, None)
        return True

    def _observe_evidence(self, ctx: SkillContext) -> None:
        if (
            ctx.memory.get("cap_craft_dagger_button_goal_id") != ctx.goal_id
            or ctx.memory.get("cap_craft_stage") != "pending"
        ):
            return
        backpack = self._backpack(ctx)
        attempt = ctx.memory.get("cap_craft_attempt_daggers")
        attempt_ingots = ctx.memory.get("cap_craft_attempt_ingots")
        attempt_gump_serial = ctx.memory.get("cap_craft_attempt_gump_serial")
        if (
            backpack is None
            or not isinstance(attempt, tuple)
            or type(attempt_ingots) is not int
            or type(attempt_gump_serial) is not int
        ):
            return
        before = {
            serial: amount
            for entry in attempt
            if isinstance(entry, tuple) and len(entry) == 2
            for serial, amount in (entry,)
            if type(serial) is int and type(amount) is int and amount >= 0
        }
        current = {
            item.serial: item.amount
            for item in ctx.obs.items
            if item.graphic == self.craft_output_graphic
            and item.container == backpack.serial
            and item.amount > 0
        }
        produced = tuple(
            sorted(
                (serial, amount - before.get(serial, 0))
                for serial, amount in current.items()
                if amount > before.get(serial, 0)
            )
        )
        created = sum(amount for _serial, amount in produced)
        ingots_used = max(0, attempt_ingots - self._pack_ingots(ctx))
        known_gump_id = ctx.memory.get("cap_craft_gump_id")
        craft_gump = next(
            (
                gump
                for gump in ctx.obs.gumps
                if str(self.craft_title_cliloc) in gump.layout
                and gump.gump_id == known_gump_id
                and gump.serial != attempt_gump_serial
            ),
            None,
        )
        lost_failure = bool(
            craft_gump is not None
            and str(CRAFT_FAILURE_CLILOC) in craft_gump.layout
            and self._has_reply(craft_gump, self.craft_make_last_btn)
        )
        no_loss_failure = bool(
            craft_gump is not None
            and str(CRAFT_FAILURE_NO_LOSS_CLILOC) in craft_gump.layout
            and self._has_reply(craft_gump, self.craft_make_last_btn)
        )
        fresh_result_gump = craft_gump is not None
        if (
            created == 1
            and ingots_used == self.craft_material_per_item
            and fresh_result_gump
            and not lost_failure
            and not no_loss_failure
        ):
            confirmed = ctx.memory.get("cap_craft_confirmed", 0) + 1
            ctx.memory["cap_craft_confirmed"] = confirmed
            prior = ctx.memory.get("cap_craft_produced", ())
            ctx.memory["cap_craft_produced"] = tuple(prior) + produced
            start_ingots = ctx.memory.get("cap_craft_start_ingots", attempt_ingots)
            ctx.memory["cap_craft_ingots_used"] = max(
                0, start_ingots - self._pack_ingots(ctx)
            )
            ctx.memory.pop("cap_craft_attempt_daggers", None)
            ctx.memory.pop("cap_craft_attempt_ingots", None)
            ctx.memory.pop("cap_craft_attempt_gump_serial", None)
            ctx.memory.pop("cap_craft_attempt_wait", None)
            needed = ctx.memory.get("cap_craft_needed", 0)
            ctx.memory["cap_craft_stage"] = (
                "close" if confirmed >= needed else "make_last"
            )
            return

        confirmed_failure = bool(
            created == 0
            and fresh_result_gump
            and (
                (lost_failure and ingots_used in {0, self.craft_material_per_item})
                or (no_loss_failure and ingots_used == 0)
            )
        )
        if confirmed_failure:
            ctx.memory["cap_craft_failed_attempts"] = (
                ctx.memory.get("cap_craft_failed_attempts", 0) + 1
            )
            ctx.memory["cap_craft_failed_ingots"] = (
                ctx.memory.get("cap_craft_failed_ingots", 0) + ingots_used
            )
            costs = ctx.memory.get("cap_craft_failure_costs", ())
            ctx.memory["cap_craft_failure_costs"] = tuple(costs) + (ingots_used,)
            start_ingots = ctx.memory.get("cap_craft_start_ingots", attempt_ingots)
            ctx.memory["cap_craft_ingots_used"] = max(
                0, start_ingots - self._pack_ingots(ctx)
            )
            ctx.memory.pop("cap_craft_attempt_daggers", None)
            ctx.memory.pop("cap_craft_attempt_ingots", None)
            ctx.memory.pop("cap_craft_attempt_gump_serial", None)
            ctx.memory.pop("cap_craft_attempt_wait", None)
            ctx.memory["cap_craft_stage"] = "make_last"
            return

        if (
            craft_gump is not None
            and fresh_result_gump
            and (
                (lost_failure or no_loss_failure)
                or created > 1
                or ingots_used > self.craft_material_per_item
            )
        ):
            # A malformed mixed delta cannot be attributed to one dagger
            # attempt.  Stop issuing craft replies and drain the owned gump.
            ctx.memory["cap_craft_abort_goal_id"] = ctx.goal_id
            ctx.memory["cap_craft_stage"] = "close"
            return

        wait = ctx.memory.get("cap_craft_attempt_wait", 0) + 1
        ctx.memory["cap_craft_attempt_wait"] = wait
        # A stale pre-response gump is not proof of failure.  Keep the one
        # attempt pending until an exact success delta, a server failure
        # cliloc, or the bounded goal-step limit settles it.

    def _craft_gump(self, ctx: SkillContext):
        return next(
            (
                gump
                for gump in ctx.obs.gumps
                if str(self.craft_title_cliloc) in gump.layout
            ),
            None,
        )

    @staticmethod
    def _has_reply(gump, button: int) -> bool:
        """Require the structured reply actually present on this gump page."""

        return any(
            isinstance(element, dict)
            and element.get("type") == "button"
            and type(element.get("reply_id")) is int
            and element.get("reply_id") == button
            and type(element.get("pageflag")) is int
            and element.get("pageflag") == 1
            for element in gump.elements
        )

    def _snapshot_attempt(self, ctx: SkillContext, gump) -> None:
        backpack = self._backpack(ctx)
        ctx.memory["cap_craft_attempt_daggers"] = tuple(
            sorted(
                (item.serial, item.amount)
                for item in ctx.obs.items
                if backpack is not None
                and item.graphic == self.craft_output_graphic
                and item.container == backpack.serial
            )
        )
        ctx.memory["cap_craft_attempt_ingots"] = self._pack_ingots(ctx)
        ctx.memory["cap_craft_attempt_gump_serial"] = gump.serial
        ctx.memory["cap_craft_attempt_wait"] = 0
        ctx.memory["cap_craft_attempts"] = ctx.memory.get("cap_craft_attempts", 0) + 1

    def _close_or_finish(self, ctx: SkillContext) -> SkillResult:
        # Once cleanup owns the leaf, no in-flight craft result may keep the
        # capability lease non-yieldable forever.  Success/failure evidence
        # has already been settled before normal close; abort paths deliberately
        # discard their unattributable pending snapshot here.
        ctx.memory.pop("cap_craft_attempt_daggers", None)
        ctx.memory.pop("cap_craft_attempt_ingots", None)
        ctx.memory.pop("cap_craft_attempt_gump_serial", None)
        ctx.memory.pop("cap_craft_attempt_wait", None)
        gump = self._craft_gump(ctx)
        stage = ctx.memory.get("cap_craft_stage")
        if gump is not None:
            known = ctx.memory.get("cap_craft_gump_id")
            if known is not None and known != gump.gump_id:
                return SkillResult(Status.RUNNING)
            ctx.memory["cap_craft_gump_id"] = gump.gump_id
            ctx.memory["cap_craft_close_sent"] = True
            ctx.memory["cap_craft_stage"] = "close_wait"
            return SkillResult(
                Status.RUNNING,
                GumpResponse(gump.serial, gump.gump_id, button=0),
            )
        if stage == "close" and not ctx.memory.get("cap_craft_close_reopen_sent"):
            wait = ctx.memory.get("cap_craft_close_absent_wait", 0) + 1
            ctx.memory["cap_craft_close_absent_wait"] = wait
            if wait < _REOPEN_AFTER:
                return SkillResult(Status.RUNNING)
            tool = self._tool(ctx)
            if tool is not None:
                ctx.memory["cap_craft_close_reopen_sent"] = True
                ctx.memory["cap_craft_close_reopen_wait"] = 0
                return SkillResult(Status.RUNNING, Use(serial=tool.serial))
            ctx.memory["cap_craft_stage"] = "close_wait"
            return SkillResult(Status.RUNNING)
        if stage == "close" and ctx.memory.get("cap_craft_close_reopen_sent"):
            wait = ctx.memory.get("cap_craft_close_reopen_wait", 0) + 1
            ctx.memory["cap_craft_close_reopen_wait"] = wait
            if wait >= _REOPEN_AFTER:
                # A tool response has had a full ordinary reopen window to
                # appear.  Move to a second no-gump observation before
                # declaring the UI settled so one delayed packet cannot both
                # finish the leaf and admit SUCCESS in the same tick.
                ctx.memory["cap_craft_stage"] = "close_wait"
            return SkillResult(Status.RUNNING)
        if stage != "close_wait":
            return SkillResult(Status.RUNNING)
        ctx.memory["cap_craft_finished_goal_id"] = ctx.goal_id
        ctx.memory["cap_craft_stage"] = "finished"
        start_pos = ctx.memory.get("cap_craft_start_pos")
        if start_pos == (ctx.obs.player.pos.x, ctx.obs.player.pos.y):
            ctx.memory["cap_craft_returned_goal_id"] = ctx.goal_id
        ctx.memory["bs_state"] = "open"
        ctx.memory.pop("bs_wait", None)
        return SkillResult(Status.RUNNING)

    def step(self, ctx: SkillContext) -> SkillResult:
        if not self._begin_goal(ctx):
            return SkillResult(Status.RUNNING)
        self._observe_evidence(ctx)
        goal_id = ctx.goal_id
        if ctx.memory.get("cap_craft_finished_goal_id") == goal_id:
            return SkillResult(Status.RUNNING)
        steps = ctx.memory.get("cap_craft_steps", 0) + 1
        ctx.memory["cap_craft_steps"] = steps
        stage = ctx.memory.get("cap_craft_stage")
        abort = ctx.memory.get("cap_craft_abort_goal_id") == goal_id
        limit_reached = bool(
            stage not in {"close", "close_wait", "finished"}
            and (
                steps > self.max_goal_steps
                or (
                    stage != "pending"
                    and ctx.memory.get("cap_craft_attempts", 0) >= self.max_attempts
                )
            )
        )
        if (
            abort
            or stage in {"close", "close_wait"}
            or limit_reached
        ):
            if limit_reached:
                ctx.memory["cap_craft_abort_goal_id"] = goal_id
            if (abort or limit_reached) and stage not in {"close", "close_wait"}:
                ctx.memory["cap_craft_stage"] = "close"
            return self._close_or_finish(ctx)

        if any(
            str(cliloc) in gump.layout
            for gump in ctx.obs.gumps
            for cliloc in (NOT_ENOUGH_METAL_CLILOC, PROXIMITY_CLILOC)
        ):
            ctx.memory["cap_craft_abort_goal_id"] = goal_id
            ctx.memory["cap_craft_stage"] = "close"
            return self._close_or_finish(ctx)

        if stage == "open":
            tool = self._tool(ctx)
            if tool is None:
                ctx.memory["cap_craft_abort_goal_id"] = goal_id
                ctx.memory["cap_craft_stage"] = "close"
                return SkillResult(Status.RUNNING)
            ctx.memory["cap_craft_tool_serial"] = tool.serial
            # Skip the resource-select stages entirely when this recipe has no
            # material submenu (carpentry) — go open -> category directly.
            ctx.memory["cap_craft_stage"] = (
                "resource_menu" if self.craft_resource_menu_btn is not None else "category"
            )
            return SkillResult(Status.RUNNING, Use(serial=tool.serial))

        gump = self._craft_gump(ctx)
        if gump is None:
            return SkillResult(Status.RUNNING)
        known = ctx.memory.get("cap_craft_gump_id")
        if known is not None and known != gump.gump_id:
            ctx.memory["cap_craft_abort_goal_id"] = goal_id
            ctx.memory["cap_craft_stage"] = "close"
            return SkillResult(Status.RUNNING)
        ctx.memory["cap_craft_gump_id"] = gump.gump_id

        if stage == "resource_menu":
            ctx.memory["cap_craft_stage"] = "iron"
            button = self.craft_resource_menu_btn
        elif stage == "iron":
            ctx.memory["cap_craft_stage"] = "category"
            button = self.craft_material_resource_btn
        elif stage == "category":
            ctx.memory["cap_craft_stage"] = "item"
            button = self.craft_category_btn
        elif stage == "item":
            name_ok = (
                self.craft_item_name_cliloc is None
                or str(self.craft_item_name_cliloc) in gump.layout
            )
            if not name_ok or not self._has_reply(gump, self.craft_item_btn):
                return SkillResult(Status.RUNNING)
            self._snapshot_attempt(ctx, gump)
            ctx.memory["cap_craft_dagger_button_goal_id"] = goal_id
            ctx.memory["cap_craft_stage"] = "pending"
            button = self.craft_item_btn
        elif stage == "make_last":
            if self._pack_ingots(ctx) < self.craft_material_per_item:
                ctx.memory["cap_craft_abort_goal_id"] = goal_id
                ctx.memory["cap_craft_stage"] = "close"
                return self._close_or_finish(ctx)
            self._snapshot_attempt(ctx, gump)
            ctx.memory["cap_craft_dagger_button_goal_id"] = goal_id
            ctx.memory["cap_craft_stage"] = "pending"
            button = self.craft_make_last_btn
        else:
            return SkillResult(Status.RUNNING)
        if button not in {self.craft_item_btn, self.craft_make_last_btn} and not self._has_reply(gump, button):
            # The prior response may still be the currently observed gump.
            # Never advance the FSM until the exact next-page reply exists.
            ctx.memory["cap_craft_stage"] = stage
            return SkillResult(Status.RUNNING)
        if button == self.craft_make_last_btn and not self._has_reply(gump, button):
            ctx.memory.pop("cap_craft_attempt_daggers", None)
            ctx.memory.pop("cap_craft_attempt_ingots", None)
            ctx.memory.pop("cap_craft_attempt_gump_serial", None)
            ctx.memory.pop("cap_craft_attempt_wait", None)
            ctx.memory["cap_craft_attempts"] = max(
                0, ctx.memory.get("cap_craft_attempts", 1) - 1
            )
            ctx.memory["cap_craft_stage"] = stage
            return SkillResult(Status.RUNNING)
        return SkillResult(
            Status.RUNNING,
            GumpResponse(gump.serial, gump.gump_id, button=button),
        )


class CraftDaggers(CraftItemCapability):
    """Blacksmith config: craft a five-item batch of daggers from iron ingots.
    Inherits every `craft_*` default (tool=hammer/tongs, category 22, item 16,
    material=iron, 3/item, output=dagger, batch 5, iron resource submenu), so it
    is byte-identical to the B4 skill.
    """

    name = "craft_daggers"
    description = "Craft enough observation-confirmed daggers to fill one five-item sale batch."
