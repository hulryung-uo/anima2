"""Woodworking — turn harvested logs into boards, the lumberjack's processing step.

The lumber→carpenter→tinker economy's first new mechanic (Brick 1 of
`docs/LUMBER-CARPENTER-TINKER.md`), the parallel to `smelt.py::MineAndSmelt` in
the mining→smithing chain: `Chop` (`harvest.py`) produces Log piles the way
`Mine` produces ore, and `ProcessLogs` converts them to Boards the way smelting
converts ore to ingots.

It is an **inverted** Smelt. Smelting double-clicks the ore pile and targets the
forge; log processing double-clicks the **axe** and targets the **log pile**:
`Use(axe)` → the server opens a target cursor (cliloc 1010018 "Target the pile of
logs") → `TargetObject(log_pile)` → ServUO `Log.Axe()` → `TryCreateBoards` →
`ScissorHelper` converts the whole pile 1:1 into Boards at 0 skill for regular
wood (`Scripts/Items/Resource/Log.cs`). The log must be in the pack, else the
server refuses with cliloc 1062334 ("The log needs to be in your pack..."); since
this only ever targets a pile it already found in the pack, that path can't fire.
Live-verified: 20 logs → 20 boards in one `Use(axe)` + `TargetObject(pile)`.
"""

from __future__ import annotations

from ..contract import TargetObject, Use
from .base import Skill, SkillContext, SkillResult, Status
from .harvest import AXE_GRAPHICS, BACKPACK_LAYER
from .market import BuyToolCapability, SellItemCapability

# ServUO `Scripts/Items/Resource/Log.cs`: `Log() : base(0x1BDD)` — a pack log pile.
LOG_GRAPHIC = 0x1BDD
# ServUO `Scripts/Items/Resource/Board.cs`: `Board() : base(0x1BD7)` — what
# `Log.Axe -> TryCreateBoards` produces, 1:1. A SINGLE art id (the ground flip is
# 0x1BDA), NOT the four stack-size variants ore/ingots use — so `BOARD_GRAPHICS`
# is a one-element set, kept a frozenset for parity with `smelt.INGOT_GRAPHICS`
# so the later sell/market side can scan pack boards the same way it scans ingots.
BOARD_GRAPHIC = 0x1BD7
BOARD_GRAPHICS = frozenset({BOARD_GRAPHIC})


class ProcessLogs(Skill):
    """Convert pack logs into boards with an axe — a standalone, inverted Smelt.

    Mirrors `smelt.py::MineAndSmelt._smelt_step` structurally, inverted: smelting
    USES the ore pile and TARGETS the forge; this USES the axe and TARGETS the
    log pile. One log pile at a time (like Smelt walks ore piles one at a time),
    rewarding on confirmed board arrival (pack board count rose). Kept standalone
    (the equivalent of the smelt phase, not the combined `MineAndSmelt`) — a later
    brick composes chop→process→deliver the way `MineSmeltDeliver` composes
    mine→smelt→deliver.
    """

    name = "process_logs"
    description = "Convert harvested logs into boards with an axe."

    def can_run(self, ctx: SkillContext) -> bool:
        return self._axe(ctx) is not None

    def diagnose(self, ctx: SkillContext) -> str | None:
        """`None` iff `can_run` (an axe is present), plus a richer second
        diagnostic layered on top (mirrors `MineSmeltDeliver.diagnose`): an axe
        with nothing to process is technically runnable but idle."""
        if self._axe(ctx) is None:
            return "no axe to process logs with"
        if self._pack_log(ctx) is None:
            return "no logs in the pack to process"
        return None

    def step(self, ctx: SkillContext) -> SkillResult:
        obs = ctx.obs

        # Reward = boards gained since last tick (the conversion's output),
        # exactly as `_smelt_step` rewards ingots gained. The board count is
        # carried in `process_boards`; the first tick seeds the baseline (no
        # reward), and a later batch's boards are credited even on the same tick
        # the last log pile disappears — the reward rides in this tick's result,
        # so there's no `_smelt_step`-style `_bank`/`_payout` lag to close here.
        boards_now = self._pack_boards(ctx)
        prev = ctx.memory.get("process_boards")
        reward = boards_now - prev if prev is not None and boards_now > prev else 0.0
        ctx.memory["process_boards"] = boards_now

        axe = self._axe(ctx)
        if axe is None:
            # Lost the tool mid-run — can't process without it (fails closed).
            return SkillResult(Status.FAILURE, None, reward)

        # Cursor open (from double-clicking the axe) → target the log pile.
        if obs.pending_target is not None:
            log = self._pack_log(ctx)
            if log is None:
                # A stray/expired cursor with no logs to target — idle and let
                # it clear rather than targeting nothing.
                return SkillResult(Status.RUNNING, None, reward)
            return SkillResult(Status.RUNNING, TargetObject(serial=log.serial), reward)

        # No cursor: use the axe on the next log pile, or idle when none remain.
        log = self._pack_log(ctx)
        if log is None:
            return SkillResult(Status.RUNNING, None, reward)  # nothing to process
        return SkillResult(Status.RUNNING, Use(serial=axe.serial), reward)

    @staticmethod
    def _axe(ctx: SkillContext):
        # Found by graphic, mirroring the harvest/craft tool finders
        # (`Harvest._tool` / `Blacksmith._tool`) — a lumberjack's Hatchet works
        # from the pack or worn; `Use(serial)` double-clicks it either way.
        return next((i for i in ctx.obs.items if i.graphic in AXE_GRAPHICS), None)

    @staticmethod
    def _backpack(ctx: SkillContext):
        # Filter by owner, not just layer (see `Harvest._backpack`'s docstring):
        # another mobile's pack shares the layer and can tie on distance.
        return next(
            (i for i in ctx.obs.items
             if i.layer == BACKPACK_LAYER and i.container == ctx.obs.player.serial),
            None,
        )

    def _pack_log(self, ctx: SkillContext):
        """The next log pile to process (first in observation order), or `None`.
        Mirrors `MineAndSmelt._pack_ore` — one pile at a time."""
        bp = self._backpack(ctx)
        if bp is None:
            return None
        return next(
            (i for i in ctx.obs.items if i.graphic == LOG_GRAPHIC and i.container == bp.serial),
            None,
        )

    def _pack_boards(self, ctx: SkillContext) -> int:
        """Boards currently in the pack (summed `amount`), the confirmed-arrival
        reward signal. Mirrors `MineAndSmelt._ingot_count`."""
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(
            i.amount for i in ctx.obs.items
            if i.graphic in BOARD_GRAPHICS and i.container == bp.serial
        )


# The WeaponSmith's for-sale Hatchet (ServUO `Scripts/VendorInfo/SBWeaponSmith.cs`:
# `GenericBuyInfo(typeof(Hatchet), 25, 20, 0x0F44, 0)`) — a member of
# `harvest.AXE_GRAPHICS`, resolved off the enriched `ShopBuyEntry` by this graphic.
HATCHET_GRAPHIC = 0x0F44


class SellBoards(SellItemCapability):
    """Lumberjack config: sell boards (0x1BD7) to the `vendor_spot` Carpenter NPC
    (SBCarpenter buys Board @2g). Free logs -> boards -> gold is the lumberjack's
    income. Only `sold_graphic`/`sell_threshold` differ from `SellDaggers`; the
    provenance machinery is `SellItemCapability`'s, unchanged.
    """

    name = "sell_boards"
    description = "Sell observed backpack boards to the configured carpenter vendor and return."
    #: Boards sold to the carpenter — a single art id, not a 4-variant stack set.
    sold_graphic = BOARD_GRAPHIC
    #: A full sell batch of boards (free input, so a generous threshold is fine).
    sell_threshold = 20
    # vendor_spot_key = "vendor_spot" (inherited): the lumberjack's `vendor_spot`
    # IS the Carpenter — the sell vendor — while buy_hatchet uses a separate key.


class BuyHatchet(BuyToolCapability):
    """Lumberjack config: buy a replacement Hatchet (0x0F44) from the
    `tool_vendor_spot` WeaponSmith NPC when no axe is in the pack.

    The lumberjack uses TWO vendors (unlike the one-NPC blacksmith): the Carpenter
    for selling (`vendor_spot`) and the WeaponSmith for hatchets (`tool_vendor_spot`).
    Only the tool config differs from `BuyTool`; the buy machinery is
    `BuyToolCapability`'s, unchanged.
    """

    name = "buy_hatchet"
    description = "Buy one replacement hatchet from the configured tool vendor and return."
    #: The trigger is "no AXE_GRAPHICS in the pack" — the SET (any axe counts as a
    #: working tool). Distinct from `offer_graphic`: the WeaponSmith sells EIGHT
    #: distinct axes whose art is in AXE_GRAPHICS, so the offer must be resolved by
    #: the single scalar `offer_graphic` (0x0F44), never the set (which would match
    #: 8 entries and fail `_offer_by_graphic`'s exactly-one guard).
    owned_tool_graphics = AXE_GRAPHICS
    #: The exact for-sale Hatchet art the WeaponSmith stocks — the cheapest axe
    #: (live-confirmed 0x0F44 @ 25g). `_offer_by_graphic(buy, self.offer_graphic)`
    #: matches this single graphic among the vendor's 8 axes.
    offer_graphic = HATCHET_GRAPHIC
    #: This shard's live-confirmed Hatchet price (25g); the readiness affordability
    #: estimate only — the buy reads the live entry price.
    tool_price_estimate = 25
    #: The lumberjack's tool vendor is a SEPARATE WeaponSmith NPC, not the sell
    #: (Carpenter) vendor — so buy_hatchet reads its own memory key.
    vendor_spot_key = "tool_vendor_spot"


class ProcessLogsGoal(ProcessLogs):
    """Capability hands: convert the pack's frozen logs into boards for one goal.

    The produce analog of `craft_daggers` for the lumberjack — goal-scoped. At
    admission it freezes the pack's total log amount (N); it then drives the
    `ProcessLogs` convert gesture (`Use(axe)` -> `TargetObject(log pile)`) pile by
    pile until all N logs have become N boards (1:1), and marks the goal finished.
    Completion (`_process_achieved`, `capabilities.py`) requires exactly N boards
    to have arrived and 0 logs to remain, scoped to the same ``goal_id``.
    """

    name = "process_logs"
    description = "Convert the pack's logs into boards for one verified goal."

    def _begin_goal(self, ctx: SkillContext) -> bool:
        goal_id = ctx.goal_id
        if type(goal_id) is not int:
            return False
        if ctx.memory.get("cap_process_goal_id") == goal_id:
            return True
        if self._backpack(ctx) is None:
            return False
        start_logs = self._pack_logs_amount(ctx)
        if start_logs <= 0:
            return False
        ctx.memory["cap_process_goal_id"] = goal_id
        ctx.memory["cap_process_start_logs"] = start_logs
        ctx.memory["cap_process_start_boards"] = self._pack_boards(ctx)
        ctx.memory["cap_process_needed"] = start_logs
        for key in (
            "cap_process_board_delta",
            "cap_process_logs_remaining",
            "cap_process_finished_goal_id",
        ):
            ctx.memory.pop(key, None)
        return True

    def _observe_evidence(self, ctx: SkillContext) -> None:
        goal_id = ctx.goal_id
        if ctx.memory.get("cap_process_goal_id") != goal_id:
            return
        start_boards = ctx.memory.get("cap_process_start_boards")
        if type(start_boards) is not int:
            return
        ctx.memory["cap_process_board_delta"] = max(0, self._pack_boards(ctx) - start_boards)
        ctx.memory["cap_process_logs_remaining"] = self._pack_logs_amount(ctx)

    def step(self, ctx: SkillContext) -> SkillResult:
        if not self._begin_goal(ctx):
            return SkillResult(Status.RUNNING)
        self._observe_evidence(ctx)
        goal_id = ctx.goal_id
        if ctx.memory.get("cap_process_finished_goal_id") == goal_id:
            return SkillResult(Status.RUNNING)
        # All logs converted (none left) and no cursor open -> the batch is done.
        if self._pack_log(ctx) is None and ctx.obs.pending_target is None:
            ctx.memory["cap_process_finished_goal_id"] = goal_id
            self._observe_evidence(ctx)
            return SkillResult(Status.RUNNING)
        # Otherwise run the convert gesture (Use axe -> target log), goal-scoped.
        # `ProcessLogs.step` returns the confirmed-board reward for this tick.
        return super().step(ctx)

    def _pack_logs_amount(self, ctx: SkillContext) -> int:
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(
            i.amount for i in ctx.obs.items
            if i.graphic == LOG_GRAPHIC and i.container == bp.serial
        )
