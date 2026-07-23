"""Closed, immutable execution capabilities for autonomous Goal proposals.

This registry is intentionally separate from ``skill_library.REGISTRY``.  The
skill library is descriptive/retrieval metadata; this module is an authority
boundary.  A Goal can name only an opaque, hand-written capability id.  Trusted
code binds that id to one shipped leaf skill plus its readiness, completion,
progress, yield, source, profession, and deadline policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Mapping

from .goals import GoalAdmission, GoalSource
from .skills import Skill
from .skills.base import Goal, SkillContext
from .skills.carpentry import (
    BuyBoards,
    BuySaw,
    CarpenterCraft,
    FetchBoards,
    FetchSaw,
    SellFurniture,
)
from .skills.tinkering import (
    BuyIron,
    BuyTinkerTool,
    DeliverHatchet,
    DeliverSaw,
    SellTongs,
    TinkerHatchet,
    TinkerSaw,
    TinkerTongs,
)
from .skills.craft import DAGGER_GRAPHIC, PICKUP_RADIUS, SMITH_TOOL_GRAPHICS, CraftDaggers
from .skills.harvest import AXE_GRAPHICS
from .skills.market import (
    TOOL_BUY_AMOUNT,
    BankGold,
    BuyIngots,
    BuyTool,
    SellDaggers,
    _bank_reserve,
)
from .skills.smelt import INGOT_GRAPHICS
from .skills.woodwork import (
    BOARD_GRAPHIC,
    LOG_GRAPHIC,
    BuyHatchet,
    DeliverBoards,
    FetchHatchet,
    ProcessLogsGoal,
    SellBoards,
)

_BACKPACK_LAYER = 0x15
_BANKBOX_LAYER = 0x1D
_GOLD_GRAPHIC = 0x0EED
_GOAL_KEYS = frozenset({"schema", "profession", "capability"})
_CAPABILITY_AUTHORITY = object()
_PLANNER_AUTHORITY = object()


@dataclass(frozen=True)
class CapabilityBinding:
    """One auditable profession capability; never constructed from model text."""

    capability_id: str
    profession: str
    skill_type: type[Skill]
    allowed_sources: frozenset[GoalSource]
    ready: Callable[[SkillContext], bool]
    achieved: Callable[[SkillContext], bool]
    progress: Callable[[SkillContext], float]
    can_yield: Callable[[SkillContext], bool]
    default_deadline_ticks: int


@dataclass(frozen=True)
class ResolvedCapability:
    """A canonical sealed Goal and the sole trusted binding that may serve it."""

    goal: Goal
    binding: CapabilityBinding


@dataclass(frozen=True)
class CapabilityPlannerLease:
    """Proof that the capability planner came from the profession factory."""

    profession: str
    capability_ids: frozenset[str]
    installed_skills: tuple[Skill, ...] = field(repr=False, compare=False)
    _authority: object = field(repr=False, compare=False)


def _backpack_serial(ctx: SkillContext) -> int | None:
    item = next(
        (
            item
            for item in ctx.obs.items
            if item.layer == _BACKPACK_LAYER and item.container == ctx.obs.player.serial
        ),
        None,
    )
    return item.serial if item is not None else None


def _bankbox_serial(ctx: SkillContext) -> int | None:
    item = next(
        (
            item
            for item in ctx.obs.items
            if item.layer == _BANKBOX_LAYER and item.container == ctx.obs.player.serial
        ),
        None,
    )
    return item.serial if item is not None else None


def _container_gold(ctx: SkillContext, container: int | None) -> int:
    if container is None:
        return 0
    return sum(
        item.amount
        for item in ctx.obs.items
        if item.graphic == _GOLD_GRAPHIC and item.container == container
    )


def _pack_gold(ctx: SkillContext) -> int:
    return _container_gold(ctx, _backpack_serial(ctx))


def _bank_gold(ctx: SkillContext) -> int:
    return _container_gold(ctx, _bankbox_serial(ctx))


def _pack_graphic(ctx: SkillContext, graphic: int) -> int:
    """Pack amount of one item art — the generalized count the sell readiness
    gate uses (daggers for the blacksmith, boards for the lumberjack)."""
    backpack = _backpack_serial(ctx)
    if backpack is None:
        return 0
    return sum(
        item.amount
        for item in ctx.obs.items
        if item.graphic == graphic and item.container == backpack
    )


def _pack_daggers(ctx: SkillContext) -> int:
    return _pack_graphic(ctx, DAGGER_GRAPHIC)


def _pack_graphics(ctx: SkillContext, graphics: frozenset[int]) -> int:
    """Pack amount summed over a SET of item arts — the generalized material
    count a craft capability uses (blacksmith: the 4 ingot pile-size variants;
    carpenter: boards)."""
    backpack = _backpack_serial(ctx)
    if backpack is None:
        return 0
    return sum(
        item.amount
        for item in ctx.obs.items
        if item.graphic in graphics and item.container == backpack
    )


def _pack_ingots(ctx: SkillContext) -> int:
    return _pack_graphics(ctx, INGOT_GRAPHICS)


def _owned_tool(ctx: SkillContext, graphics: frozenset[int]):
    """The first owned (backpack) tool whose art is in `graphics`, or `None` —
    the generalized "do I have a working tool?" check. The buy_tool trigger is
    this returning `None`; buy_tool's arrival proof is it becoming non-`None`."""
    backpack = _backpack_serial(ctx)
    if backpack is None:
        return None
    return next(
        (
            item
            for item in ctx.obs.items
            if item.graphic in graphics and item.container == backpack
        ),
        None,
    )


def _owned_smith_tool(ctx: SkillContext):
    return _owned_tool(ctx, SMITH_TOOL_GRAPHICS)


def _valid_spot(value: object) -> bool:
    if not isinstance(value, (tuple, list)) or not value:
        return False
    points = value if isinstance(value[0], (tuple, list)) else (value,)
    return all(
        isinstance(point, (tuple, list))
        and len(point) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in point)
        for point in points
    )


_BANK_TRANSACTION_KEYS = (
    "bank_leg",
    "bank_stage",
    "bank_banker",
    "bank_find_wait",
    "bank_popup_wait",
    "bank_popup_total",
    "bank_settle",
    "bank_deposit_attempts",
    "bank_return_leg",
    "cap_bank_recovery_drop_sent",
    "cap_bank_reopen_started",
)


def _bank_ui_clear(ctx: SkillContext) -> bool:
    obs = ctx.obs
    return bool(
        obs.pending_target is None
        and not obs.gumps
        and obs.popup is None
        and obs.shop_buy is None
        and obs.shop_sell is None
    )


def _bank_idle(ctx: SkillContext) -> bool:
    return bool(
        ctx.memory.get("mkt_phase", "craft") == "craft"
        and ctx.memory.get("bank_held") is None
        and ctx.memory.get("cap_bank_release_pending") is None
        and all(ctx.memory.get(key) is None for key in _BANK_TRANSACTION_KEYS)
        and _bank_ui_clear(ctx)
    )


def _bank_ready(ctx: SkillContext) -> bool:
    return bool(
        _valid_spot(ctx.memory.get("banker_spot"))
        and _backpack_serial(ctx) is not None
        # Bank only when there is a SURPLUS above the optional working-capital
        # reserve (default 0 == the whole pile, byte-identical to B7). The reserve
        # keeps enough pack gold to buy iron/tools in a supply gap, so a solo
        # capability loop doesn't bank itself broke and stall — see
        # `WORKING_CAPITAL_RESERVE` and `skills/market.py::BankGold`.
        and _pack_gold(ctx) > _bank_reserve(ctx.memory)
        and ctx.memory.get("bs_state", "open") not in {"fetch", "fetch_return"}
        and _bank_idle(ctx)
    )


def _bank_can_yield(ctx: SkillContext) -> bool:
    goal_id = ctx.goal_id
    started = type(goal_id) is int and ctx.memory.get("cap_bank_goal_id") == goal_id
    terminal = bool(
        started and ctx.memory.get("cap_bank_finished_goal_id") == goal_id
    )
    return bool(
        type(goal_id) is int
        and (not started or terminal)
        and _bank_idle(ctx)
    )


def _bank_achieved(ctx: SkillContext) -> bool:
    goal_id = ctx.goal_id
    # The retained working-capital reserve (default 0 == the whole pile banked,
    # byte-identical to B7). `expected`/the deltas are all about the SURPLUS that
    # moved, not the reserve left behind.
    reserve = _bank_reserve(ctx.memory)
    expected = ctx.memory.get("cap_bank_expected_gold")
    start_pack = ctx.memory.get("cap_bank_start_pack_gold")
    start_bank = ctx.memory.get("cap_bank_start_bank_gold")
    box_serial = ctx.memory.get("cap_bank_box_serial")
    piles = ctx.memory.get("cap_bank_start_piles")
    lifted = ctx.memory.get("cap_bank_lifted_items")
    dropped = ctx.memory.get("cap_bank_dropped_items")
    manifest_valid = bool(
        isinstance(piles, tuple)
        and piles
        and all(
            isinstance(entry, tuple)
            and len(entry) == 2
            and all(type(value) is int and value > 0 for value in entry)
            for entry in piles
        )
        and len({serial for serial, _amount in piles}) == len(piles)
    )
    lifted_valid = bool(
        isinstance(lifted, tuple)
        and all(
            isinstance(entry, tuple)
            and len(entry) == 2
            and all(type(value) is int and value > 0 for value in entry)
            for entry in lifted
        )
        and len(set(lifted)) == len(lifted)
    )
    dropped_valid = bool(
        isinstance(dropped, tuple)
        and all(
            isinstance(entry, tuple)
            and len(entry) == 3
            and all(type(value) is int and value > 0 for value in entry)
            for entry in dropped
        )
        and len(set(dropped)) == len(dropped)
    )
    return bool(
        type(goal_id) is int
        and ctx.memory.get("cap_bank_goal_id") == goal_id
        and ctx.memory.get("cap_bank_baseline_goal_id") == goal_id
        and ctx.memory.get("cap_bank_sent_goal_id") == goal_id
        and ctx.memory.get("cap_bank_finished_goal_id") == goal_id
        and ctx.memory.get("cap_bank_returned_goal_id") == goal_id
        and type(expected) is int
        and expected > 0
        and start_pack == expected
        and type(start_bank) is int
        and start_bank >= 0
        and type(box_serial) is int
        and box_serial > 0
        and manifest_valid
        and sum(amount for _serial, amount in piles) == expected
        and lifted_valid
        and set(lifted) == set(piles)
        and dropped_valid
        and set(dropped)
        == {(serial, amount, box_serial) for serial, amount in piles}
        and ctx.memory.get("cap_bank_pack_delta") == expected
        and ctx.memory.get("cap_bank_bank_delta") == expected
        and ctx.memory.get("cap_bank_confirmed") == expected
        and ctx.memory.get("cap_bank_final_pack_gold") == reserve
        and ctx.memory.get("cap_bank_start_piles_removed") == expected
        and ctx.memory.get("cap_bank_start_piles_cleared") is True
        and _bank_can_yield(ctx)
    )


def _bank_progress(ctx: SkillContext) -> float:
    goal_id = ctx.goal_id
    expected = ctx.memory.get("cap_bank_expected_gold")
    if (
        type(goal_id) is not int
        or ctx.memory.get("cap_bank_goal_id") != goal_id
        or type(expected) is not int
        or expected <= 0
    ):
        return 0.0
    pack_delta = ctx.memory.get("cap_bank_pack_delta", 0)
    bank_delta = ctx.memory.get("cap_bank_confirmed", 0)
    if type(pack_delta) is not int or type(bank_delta) is not int:
        return 0.0
    return max(0.0, min(1.0, min(pack_delta, bank_delta) / expected))


_SELL_TRANSACTION_KEYS = (
    "sell_leg",
    "sell_stage",
    "sell_vendor",
    "sell_find_wait",
    "sell_popup_wait",
    "sell_popup_total",
    "sell_ask_wait",
    "sell_confirm_wait",
    "sell_return_leg",
)


def _sell_can_yield(ctx: SkillContext) -> bool:
    obs = ctx.obs
    ui_clear = bool(
        obs.popup is None and obs.shop_buy is None and obs.shop_sell is None
    )
    finished = bool(
        type(ctx.goal_id) is int
        and ctx.memory.get("cap_sell_finished_goal_id") == ctx.goal_id
    )
    return bool(
        ctx.memory.get("mkt_phase", "craft") == "craft"
        and all(ctx.memory.get(key) is None for key in _SELL_TRANSACTION_KEYS)
        and ctx.memory.get("bank_held") is None
        and ctx.memory.get("cap_bank_release_pending") is None
        and obs.pending_target is None
        and not obs.gumps
        and (ui_clear or finished)
    )


def _make_sell_ready(
    sold_graphic: int, threshold: int, vendor_spot_key: str
) -> Callable[[SkillContext], bool]:
    """Build a sell readiness gate for one configured item/threshold/vendor key.
    The blacksmith uses `(DAGGER_GRAPHIC, 5, "vendor_spot")` — byte-identical to
    the old `_sell_ready`; the lumberjack uses `(BOARD_GRAPHIC, 20, "vendor_spot")`
    (its `vendor_spot` is the Carpenter)."""

    def ready(ctx: SkillContext) -> bool:
        return bool(
            _valid_spot(ctx.memory.get(vendor_spot_key))
            and _backpack_serial(ctx) is not None
            and _pack_graphic(ctx, sold_graphic) >= threshold
            and ctx.memory.get("bs_state", "open") not in {"fetch", "fetch_return"}
            and _sell_can_yield(ctx)
        )

    return ready


def _sell_achieved(ctx: SkillContext) -> bool:
    goal_id = ctx.goal_id
    sent = ctx.memory.get("cap_sell_sent_daggers")
    expected_gold = ctx.memory.get("cap_sell_expected_gold")
    dagger_delta = ctx.memory.get("cap_sell_dagger_delta")
    gold_delta = ctx.memory.get("cap_sell_gold_delta")
    offered = ctx.memory.get("cap_sell_offered_items")
    offered_valid = bool(
        isinstance(offered, tuple)
        and offered
        and all(
            isinstance(entry, tuple)
            and len(entry) == 3
            and all(type(value) is int and value > 0 for value in entry)
            for entry in offered
        )
    )
    return bool(
        type(goal_id) is int
        and ctx.memory.get("cap_sell_goal_id") == goal_id
        and ctx.memory.get("cap_sell_sent_goal_id") == goal_id
        and ctx.memory.get("cap_sell_finished_goal_id") == goal_id
        and ctx.memory.get("cap_sell_returned_goal_id") == goal_id
        and type(sent) is int
        and sent > 0
        and type(expected_gold) is int
        and expected_gold > 0
        and offered_valid
        and sum(amount for _serial, amount, _price in offered) == sent
        and sum(amount * price for _serial, amount, price in offered)
        == expected_gold
        and ctx.memory.get("cap_sell_offered_cleared") is True
        and ctx.memory.get("cap_sell_offered_removed") == sent
        and type(dagger_delta) is int
        and dagger_delta >= sent
        and type(gold_delta) is int
        and gold_delta >= expected_gold
        and ctx.obs.popup is None
        and ctx.obs.shop_buy is None
        and ctx.obs.shop_sell is None
        and _sell_can_yield(ctx)
    )


def _sell_progress(ctx: SkillContext) -> float:
    if ctx.memory.get("cap_sell_goal_id") != ctx.goal_id:
        return 0.0
    sent = ctx.memory.get("cap_sell_sent_daggers")
    expected_gold = ctx.memory.get("cap_sell_expected_gold")
    if type(sent) is not int or sent <= 0 or type(expected_gold) is not int or expected_gold <= 0:
        return 0.0
    dagger_delta = ctx.memory.get("cap_sell_offered_removed", 0)
    gold_delta = ctx.memory.get("cap_sell_gold_delta", 0)
    if type(dagger_delta) is not int or type(gold_delta) is not int:
        return 0.0
    return max(
        0.0,
        min(1.0, min(dagger_delta / sent, gold_delta / expected_gold)),
    )


_SELL_DAGGERS = CapabilityBinding(
    capability_id="sell_daggers",
    profession="blacksmith",
    skill_type=SellDaggers,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_sell_ready(
        SellDaggers.sold_graphic, SellDaggers.sell_threshold, SellDaggers.vendor_spot_key
    ),
    achieved=_sell_achieved,
    progress=_sell_progress,
    can_yield=_sell_can_yield,
    default_deadline_ticks=180,
)


_BANK_GOLD = CapabilityBinding(
    capability_id="bank_gold",
    profession="blacksmith",
    skill_type=BankGold,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_bank_ready,
    achieved=_bank_achieved,
    progress=_bank_progress,
    can_yield=_bank_can_yield,
    default_deadline_ticks=120,
)


def _craft_at_spot(ctx: SkillContext) -> bool:
    spot = ctx.memory.get("craft_spot")
    return bool(
        isinstance(spot, (tuple, list))
        and len(spot) == 2
        and all(type(value) is int for value in spot)
        and (ctx.obs.player.pos.x, ctx.obs.player.pos.y) == tuple(spot)
    )


def _make_craft_ready(
    output_graphic: int,
    material_graphics: frozenset[int],
    per_item: int,
    tool_graphics: frozenset[int],
    batch: int,
) -> Callable[[SkillContext], bool]:
    """Build a craft readiness gate for one recipe. Blacksmith:
    `(DAGGER_GRAPHIC, INGOT_GRAPHICS, MIN_INGOTS, SMITH_TOOL_GRAPHICS, 5)` —
    byte-identical to the old `_craft_ready`."""

    def ready(ctx: SkillContext) -> bool:
        made = _pack_graphic(ctx, output_graphic)
        obs = ctx.obs
        return bool(
            _craft_at_spot(ctx)
            and _backpack_serial(ctx) is not None
            and _owned_tool(ctx, tool_graphics) is not None
            and 0 <= made < batch
            and _pack_graphics(ctx, material_graphics) >= per_item * (batch - made)
            and ctx.memory.get("mkt_phase", "craft") == "craft"
            and ctx.memory.get("bs_state", "open") not in {"fetch", "fetch_return"}
            and ctx.memory.get("bs_fetch_held") is None
            and ctx.memory.get("bank_held") is None
            and ctx.memory.get("cap_bank_release_pending") is None
            and all(ctx.memory.get(key) is None for key in _SELL_TRANSACTION_KEYS)
            and obs.pending_target is None
            and not obs.gumps
            and obs.popup is None
            and obs.shop_buy is None
            and obs.shop_sell is None
        )

    return ready


def _craft_can_yield(ctx: SkillContext) -> bool:
    obs = ctx.obs
    goal_id = ctx.goal_id
    started = type(goal_id) is int and ctx.memory.get("cap_craft_goal_id") == goal_id
    terminal = bool(
        started
        and ctx.memory.get("cap_craft_finished_goal_id") == goal_id
        and ctx.memory.get("cap_craft_stage") == "finished"
    )
    return bool(
        type(goal_id) is int
        and (not started or terminal)
        and ctx.memory.get("cap_craft_attempt_daggers") is None
        and ctx.memory.get("cap_craft_attempt_ingots") is None
        and ctx.memory.get("cap_craft_attempt_gump_serial") is None
        and ctx.memory.get("cap_craft_attempt_wait") is None
        and ctx.memory.get("bs_fetch_held") is None
        and obs.pending_target is None
        and not obs.gumps
        and obs.popup is None
        and obs.shop_buy is None
        and obs.shop_sell is None
    )


def _make_craft_achieved(
    output_graphic: int,
    material_graphics: frozenset[int],
    per_item: int,
    batch: int,
) -> Callable[[SkillContext], bool]:
    """Build a craft completion proof for one recipe. Blacksmith:
    `(DAGGER_GRAPHIC, INGOT_GRAPHICS, MIN_INGOTS, 5)` — byte-identical."""

    def achieved(ctx: SkillContext) -> bool:
        return _craft_achieved_impl(ctx, output_graphic, material_graphics, per_item, batch)

    return achieved


def _craft_achieved_impl(
    ctx: SkillContext,
    output_graphic: int,
    material_graphics: frozenset[int],
    per_item: int,
    batch: int,
) -> bool:
    goal_id = ctx.goal_id
    needed = ctx.memory.get("cap_craft_needed")
    confirmed = ctx.memory.get("cap_craft_confirmed")
    produced = ctx.memory.get("cap_craft_produced")
    ingots_used = ctx.memory.get("cap_craft_ingots_used")
    failed_attempts = ctx.memory.get("cap_craft_failed_attempts")
    failed_ingots = ctx.memory.get("cap_craft_failed_ingots")
    failure_costs = ctx.memory.get("cap_craft_failure_costs")
    start_ingots = ctx.memory.get("cap_craft_start_ingots")
    start_daggers = ctx.memory.get("cap_craft_start_daggers")
    produced_valid = bool(
        isinstance(produced, tuple)
        and produced
        and all(
            isinstance(entry, tuple)
            and len(entry) == 2
            and type(entry[0]) is int
            and entry[0] > 0
            and type(entry[1]) is int
            and entry[1] > 0
            for entry in produced
        )
    )
    start_valid = bool(
        isinstance(start_daggers, tuple)
        and all(
            isinstance(entry, tuple)
            and len(entry) == 2
            and type(entry[0]) is int
            and entry[0] > 0
            and type(entry[1]) is int
            and entry[1] == 1
            for entry in start_daggers
        )
        and len({serial for serial, _amount in start_daggers}) == len(start_daggers)
    )
    start_count = (
        sum(amount for _serial, amount in start_daggers) if start_valid else -1
    )
    produced_serials = (
        {serial for serial, _amount in produced} if produced_valid else set()
    )
    start_serials = (
        {serial for serial, _amount in start_daggers} if start_valid else set()
    )
    backpack = _backpack_serial(ctx)
    observed_daggers = {
        item.serial: item.amount
        for item in ctx.obs.items
        if backpack is not None
        and item.graphic == output_graphic
        and item.container == backpack
    }
    expected_daggers = (
        dict(start_daggers) | dict(produced)
        if start_valid and produced_valid
        else {}
    )
    close_proven = bool(
        ctx.memory.get("cap_craft_close_sent") is True
        or (
            ctx.memory.get("cap_craft_close_reopen_sent") is True
            and ctx.memory.get("cap_craft_close_absent_wait", 0) >= 12
            and ctx.memory.get("cap_craft_close_reopen_wait", 0) >= 12
        )
    )
    return bool(
        type(goal_id) is int
        and ctx.memory.get("cap_craft_goal_id") == goal_id
        and ctx.memory.get("cap_craft_dagger_button_goal_id") == goal_id
        and ctx.memory.get("cap_craft_returned_goal_id") == goal_id
        and ctx.memory.get("cap_craft_abort_goal_id") != goal_id
        and type(needed) is int
        and needed > 0
        and start_valid
        and start_count + needed == batch
        and type(confirmed) is int
        and confirmed == needed
        and produced_valid
        and len(produced_serials) == len(produced)
        and not (start_serials & produced_serials)
        and sum(amount for _serial, amount in produced) == needed
        and all(amount == 1 for _serial, amount in produced)
        and observed_daggers == expected_daggers
        and close_proven
        and type(start_ingots) is int
        and type(ingots_used) is int
        and type(failed_attempts) is int
        and failed_attempts >= 0
        and type(failed_ingots) is int
        and isinstance(failure_costs, tuple)
        and len(failure_costs) == failed_attempts
        and all(type(cost) is int and cost in {0, per_item} for cost in failure_costs)
        and failed_ingots == sum(failure_costs)
        and ingots_used == per_item * needed + failed_ingots
        and start_ingots - _pack_graphics(ctx, material_graphics) == ingots_used
        and _pack_graphic(ctx, output_graphic) == batch
        and _craft_can_yield(ctx)
    )


def _craft_progress(ctx: SkillContext) -> float:
    if ctx.memory.get("cap_craft_goal_id") != ctx.goal_id:
        return 0.0
    needed = ctx.memory.get("cap_craft_needed")
    confirmed = ctx.memory.get("cap_craft_confirmed")
    if type(needed) is not int or needed <= 0 or type(confirmed) is not int:
        return 0.0
    return max(0.0, min(1.0, confirmed / needed))


def _craft_ready_for(skill_cls: type) -> Callable[[SkillContext], bool]:
    """A craft readiness gate built from a craft skill class's own `craft_*`
    config — the single source of truth (skill and leaf-func can't disagree)."""
    return _make_craft_ready(
        skill_cls.craft_output_graphic,
        skill_cls.craft_material_graphics,
        skill_cls.craft_material_per_item,
        skill_cls.craft_tool_graphics,
        skill_cls.craft_batch,
    )


def _craft_achieved_for(skill_cls: type) -> Callable[[SkillContext], bool]:
    return _make_craft_achieved(
        skill_cls.craft_output_graphic,
        skill_cls.craft_material_graphics,
        skill_cls.craft_material_per_item,
        skill_cls.craft_batch,
    )


_CRAFT_DAGGERS = CapabilityBinding(
    capability_id="craft_daggers",
    profession="blacksmith",
    skill_type=CraftDaggers,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_craft_ready_for(CraftDaggers),
    achieved=_craft_achieved_for(CraftDaggers),
    progress=_craft_progress,
    can_yield=_craft_can_yield,
    default_deadline_ticks=300,
)


# --- buy_ingots (B8) — the self-provisioning keystone -------------------------
#
# The exact mirror of ``sell_daggers`` inverted: gold LEAVES the pack, iron
# ingots ARRIVE. Replenishing finite crafting metal with earned gold closes the
# supply loop so craft->sell->bank runs indefinitely without a GM re-gifting
# ingots.

# The reorder trigger (buy when pack material is below one craft batch's worth)
# and the affordability price estimate now live on each material-buy skill class
# as `buy_reorder`/`buy_price_estimate` — the single config source the leaf-func
# factory reads (`BuyIngots.buy_reorder == 15`, `BuyIngots.buy_price_estimate == 5`
# for iron; the carpenter's `BuyBoards` sets its own). The actual purchase reads
# the live price from the matching `ShopBuyEntry` and clamps to live stock, so an
# optimistic estimate never overspends.

_BUY_TRANSACTION_KEYS = (
    "buy_leg",
    "buy_stage",
    "buy_vendor",
    "buy_find_wait",
    "buy_popup_wait",
    "buy_popup_total",
    "buy_ask_wait",
    "buy_confirm_wait",
    "buy_return_leg",
)


def _buy_can_yield(ctx: SkillContext) -> bool:
    obs = ctx.obs
    ui_clear = bool(
        obs.popup is None and obs.shop_buy is None and obs.shop_sell is None
    )
    finished = bool(
        type(ctx.goal_id) is int
        and ctx.memory.get("cap_buy_finished_goal_id") == ctx.goal_id
    )
    return bool(
        ctx.memory.get("mkt_phase", "craft") == "craft"
        and all(ctx.memory.get(key) is None for key in _BUY_TRANSACTION_KEYS)
        and ctx.memory.get("bank_held") is None
        and ctx.memory.get("cap_bank_release_pending") is None
        and obs.pending_target is None
        and not obs.gumps
        and (ui_clear or finished)
    )


def _make_buy_ready(
    material_graphics: frozenset[int],
    reorder: int,
    amount: int,
    price_estimate: int,
    vendor_spot_key: str,
) -> Callable[[SkillContext], bool]:
    """Build a material-buy readiness gate. Blacksmith:
    `(INGOT_GRAPHICS, 15, BUY_AMOUNT, 5, "vendor_spot")` — byte-identical to the
    old `_buy_ready`; carpenter: `(BOARD_GRAPHICS, ..., ..., 3, "vendor_spot")`."""

    def ready(ctx: SkillContext) -> bool:
        return bool(
            _valid_spot(ctx.memory.get(vendor_spot_key))
            and _backpack_serial(ctx) is not None
            and _pack_graphics(ctx, material_graphics) < reorder
            and _pack_gold(ctx) >= amount * price_estimate
            and ctx.memory.get("bs_state", "open") not in {"fetch", "fetch_return"}
            and _buy_can_yield(ctx)
        )

    return ready


def _buy_achieved(ctx: SkillContext) -> bool:
    goal_id = ctx.goal_id
    bought = ctx.memory.get("cap_buy_bought_ingots")
    expected_cost = ctx.memory.get("cap_buy_expected_cost")
    ingot_delta = ctx.memory.get("cap_buy_ingot_delta")
    gold_delta = ctx.memory.get("cap_buy_gold_delta")
    offer = ctx.memory.get("cap_buy_offer")
    offer_valid = bool(
        isinstance(offer, tuple)
        and len(offer) == 3
        and all(type(value) is int and value > 0 for value in offer)
    )
    return bool(
        type(goal_id) is int
        and ctx.memory.get("cap_buy_goal_id") == goal_id
        and ctx.memory.get("cap_buy_sent_goal_id") == goal_id
        and ctx.memory.get("cap_buy_finished_goal_id") == goal_id
        and ctx.memory.get("cap_buy_returned_goal_id") == goal_id
        and type(bought) is int
        and bought > 0
        and type(expected_cost) is int
        and expected_cost > 0
        and offer_valid
        # offer == (iron_serial, amount, unit_price): the exact observed vendor
        # offer must account for the amount actually bought at the quoted price
        # (`bought` is the batch clamped to the vendor's live stock, so it can be
        # below BUY_AMOUNT — the proof binds to what was ordered, not a constant).
        and offer[1] == bought
        and offer[1] * offer[2] == expected_cost
        # At least the bought amount of iron arrived in the pack, and exactly
        # the quoted cost — never a coin more — left it. A short arrival (fewer
        # ingots than ordered) or a mismatched spend fails these checks.
        and type(ingot_delta) is int
        and ingot_delta >= bought
        and type(gold_delta) is int
        and gold_delta == expected_cost
        and ctx.obs.popup is None
        and ctx.obs.shop_buy is None
        and ctx.obs.shop_sell is None
        and _buy_can_yield(ctx)
    )


def _buy_progress(ctx: SkillContext) -> float:
    if ctx.memory.get("cap_buy_goal_id") != ctx.goal_id:
        return 0.0
    bought = ctx.memory.get("cap_buy_bought_ingots")
    expected_cost = ctx.memory.get("cap_buy_expected_cost")
    if (
        type(bought) is not int
        or bought <= 0
        or type(expected_cost) is not int
        or expected_cost <= 0
    ):
        return 0.0
    ingot_delta = ctx.memory.get("cap_buy_ingot_delta", 0)
    gold_delta = ctx.memory.get("cap_buy_gold_delta", 0)
    if type(ingot_delta) is not int or type(gold_delta) is not int:
        return 0.0
    return max(
        0.0,
        min(1.0, min(ingot_delta / bought, gold_delta / expected_cost)),
    )


def _buy_ready_for(skill_cls: type) -> Callable[[SkillContext], bool]:
    """A material-buy readiness gate built from the skill class's own config."""
    return _make_buy_ready(
        skill_cls.buy_material_graphics,
        skill_cls.buy_reorder,
        skill_cls.buy_amount,
        skill_cls.buy_price_estimate,
        skill_cls.vendor_spot_key,
    )


_BUY_INGOTS = CapabilityBinding(
    capability_id="buy_ingots",
    profession="blacksmith",
    skill_type=BuyIngots,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_buy_ready_for(BuyIngots),
    achieved=_buy_achieved,
    progress=_buy_progress,
    can_yield=_buy_can_yield,
    default_deadline_ticks=180,
)


# --- buy_smith_tool (B8) — the tool-replacement half of the acquisition set ----
#
# The near-exact mirror of ``buy_ingots`` for a NON-stacking tool bought one at a
# time. It fires exactly when the craft loop would otherwise stall for good: the
# smith's hammer/tongs has broken and there is no working tool to craft with (so
# ``craft_daggers`` is already unready), closing the last finite-supply GM
# dependency by reacquiring a tool with earned gold.

# The per-tool price estimate (used ONLY for the readiness affordability gate;
# the actual purchase reads the live `ShopBuyEntry` price) now lives on each
# buy_tool skill class as `tool_price_estimate` — the single config source the
# leaf-func factory reads (`BuyTool.tool_price_estimate == 13` for the smith's
# tongs, `BuyHatchet.tool_price_estimate == 25` for the lumberjack's hatchet).

# Suggested working-capital reserve for `bank_gold`'s opt-in `bank_reserve`: keep
# back enough pack gold to afford ONE iron replenishment batch AND ONE tool
# replacement (15*5 + 1*13 == 88), so a solo capability loop that banks its
# surplus can still fund the buy_ingots/buy_smith_tool fallbacks that bridge a
# supply gap instead of banking itself broke and stalling. Nothing sets
# `bank_reserve` by default (it stays 0 — byte-identical to B7); the loop/gate
# opts in by writing `memory["bank_reserve"] = WORKING_CAPITAL_RESERVE`. The iron
# batch cost reads `BuyIngots`' own config (`buy_amount * buy_price_estimate`) so
# it stays in lockstep with the buy_ingots gate that spends it.
WORKING_CAPITAL_RESERVE = (
    BuyIngots.buy_amount * BuyIngots.buy_price_estimate
    + TOOL_BUY_AMOUNT * BuyTool.tool_price_estimate
)

_TOOLBUY_TRANSACTION_KEYS = (
    "toolbuy_leg",
    "toolbuy_stage",
    "toolbuy_vendor",
    "toolbuy_find_wait",
    "toolbuy_popup_wait",
    "toolbuy_popup_total",
    "toolbuy_ask_wait",
    "toolbuy_confirm_wait",
    "toolbuy_return_leg",
)


def _toolbuy_can_yield(ctx: SkillContext) -> bool:
    obs = ctx.obs
    ui_clear = bool(
        obs.popup is None and obs.shop_buy is None and obs.shop_sell is None
    )
    finished = bool(
        type(ctx.goal_id) is int
        and ctx.memory.get("cap_toolbuy_finished_goal_id") == ctx.goal_id
    )
    return bool(
        ctx.memory.get("mkt_phase", "craft") == "craft"
        and all(ctx.memory.get(key) is None for key in _TOOLBUY_TRANSACTION_KEYS)
        and ctx.memory.get("bank_held") is None
        and ctx.memory.get("cap_bank_release_pending") is None
        and obs.pending_target is None
        and not obs.gumps
        and (ui_clear or finished)
    )


def _make_toolbuy_ready(
    owned_graphics: frozenset[int], price_estimate: int, vendor_spot_key: str
) -> Callable[[SkillContext], bool]:
    """Build a buy_tool readiness gate for one profession's tool. Blacksmith:
    `(SMITH_TOOL_GRAPHICS, 13, "vendor_spot")` — byte-identical to the old
    `_toolbuy_ready`; lumberjack: `(AXE_GRAPHICS, 27, "tool_vendor_spot")`."""

    def ready(ctx: SkillContext) -> bool:
        return bool(
            _valid_spot(ctx.memory.get(vendor_spot_key))
            and _backpack_serial(ctx) is not None
            # The trigger: no working tool. When this holds the produce capability
            # is already unready, so a tool buy fires exactly when the loop stalls.
            and _owned_tool(ctx, owned_graphics) is None
            and _pack_gold(ctx) >= TOOL_BUY_AMOUNT * price_estimate
            and ctx.memory.get("bs_state", "open") not in {"fetch", "fetch_return"}
            and _toolbuy_can_yield(ctx)
        )

    return ready


def _make_toolbuy_achieved(
    owned_graphics: frozenset[int],
) -> Callable[[SkillContext], bool]:
    """Build a buy_tool completion proof for one profession's tool graphics."""

    def achieved(ctx: SkillContext) -> bool:
        goal_id = ctx.goal_id
        bought = ctx.memory.get("cap_toolbuy_bought_tools")
        expected_cost = ctx.memory.get("cap_toolbuy_expected_cost")
        tool_delta = ctx.memory.get("cap_toolbuy_tool_delta")
        gold_delta = ctx.memory.get("cap_toolbuy_gold_delta")
        start_tools = ctx.memory.get("cap_toolbuy_start_tools")
        offer = ctx.memory.get("cap_toolbuy_offer")
        offer_valid = bool(
            isinstance(offer, tuple)
            and len(offer) == 3
            and all(type(value) is int and value > 0 for value in offer)
        )
        return bool(
            type(goal_id) is int
            and ctx.memory.get("cap_toolbuy_goal_id") == goal_id
            and ctx.memory.get("cap_toolbuy_sent_goal_id") == goal_id
            and ctx.memory.get("cap_toolbuy_finished_goal_id") == goal_id
            and ctx.memory.get("cap_toolbuy_returned_goal_id") == goal_id
            and type(bought) is int
            and bought > 0
            and type(expected_cost) is int
            and expected_cost > 0
            and offer_valid
            # offer == (tool_serial, amount, unit_price): the exact observed vendor
            # offer must account for the one tool bought at the quoted price.
            and offer[1] == bought
            and offer[1] * offer[2] == expected_cost
            # A tool ARRIVED where there was none: started toolless (0), and at
            # least the bought count arrived; and exactly the quoted cost — never a
            # coin more — left the pack. Tools don't stack, so arrival is a count
            # delta, corroborated by a tool being present in the pack right now.
            and start_tools == 0
            and type(tool_delta) is int
            and tool_delta >= bought
            and type(gold_delta) is int
            and gold_delta == expected_cost
            and _owned_tool(ctx, owned_graphics) is not None
            and ctx.obs.popup is None
            and ctx.obs.shop_buy is None
            and ctx.obs.shop_sell is None
            and _toolbuy_can_yield(ctx)
        )

    return achieved


def _toolbuy_progress(ctx: SkillContext) -> float:
    if ctx.memory.get("cap_toolbuy_goal_id") != ctx.goal_id:
        return 0.0
    bought = ctx.memory.get("cap_toolbuy_bought_tools")
    expected_cost = ctx.memory.get("cap_toolbuy_expected_cost")
    if (
        type(bought) is not int
        or bought <= 0
        or type(expected_cost) is not int
        or expected_cost <= 0
    ):
        return 0.0
    tool_delta = ctx.memory.get("cap_toolbuy_tool_delta", 0)
    gold_delta = ctx.memory.get("cap_toolbuy_gold_delta", 0)
    if type(tool_delta) is not int or type(gold_delta) is not int:
        return 0.0
    return max(
        0.0,
        min(1.0, min(tool_delta / bought, gold_delta / expected_cost)),
    )


_BUY_SMITH_TOOL = CapabilityBinding(
    capability_id="buy_smith_tool",
    profession="blacksmith",
    skill_type=BuyTool,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_toolbuy_ready(
        BuyTool.owned_tool_graphics, BuyTool.tool_price_estimate, BuyTool.vendor_spot_key
    ),
    achieved=_make_toolbuy_achieved(BuyTool.owned_tool_graphics),
    progress=_toolbuy_progress,
    can_yield=_toolbuy_can_yield,
    default_deadline_ticks=180,
)


# --- lumberjack capabilities (Brick 2) — generalize the market machinery to a new
# profession + items via the config-attr/factory shape above. `bank_gold` reuses
# the blacksmith leaf funcs verbatim (gold is profession-agnostic); `sell_boards`
# and `buy_hatchet` reuse the sell/buy_tool machinery with the lumberjack skill
# classes' own config; `process_logs` is the produce analog of `craft_daggers`.


def _process_can_yield(ctx: SkillContext) -> bool:
    """Safe to yield/cancel a process_logs goal only between conversion gestures
    (no open target cursor), and only before it starts or once it has finished."""
    goal_id = ctx.goal_id
    started = type(goal_id) is int and ctx.memory.get("cap_process_goal_id") == goal_id
    finished = bool(
        started and ctx.memory.get("cap_process_finished_goal_id") == goal_id
    )
    return bool(
        type(goal_id) is int
        and (not started or finished)
        and ctx.obs.pending_target is None
        and not ctx.obs.gumps
    )


def _process_ready(ctx: SkillContext) -> bool:
    obs = ctx.obs
    return bool(
        _backpack_serial(ctx) is not None
        # Logs to convert AND an axe to do it with — the produce trigger. When
        # this holds and there's no working tool, buy_hatchet fires instead.
        and _pack_graphic(ctx, LOG_GRAPHIC) > 0
        and _owned_tool(ctx, AXE_GRAPHICS) is not None
        # An idle UI, not mid a market trip — mirrors `_craft_ready` (this is a
        # produce step, checked before a goal is admitted, so it must not require
        # a goal_id the way the goal-scoped `_process_can_yield` does).
        and ctx.memory.get("mkt_phase", "craft") == "craft"
        and obs.pending_target is None
        and not obs.gumps
        and obs.popup is None
        and obs.shop_buy is None
        and obs.shop_sell is None
    )


def _process_achieved(ctx: SkillContext) -> bool:
    goal_id = ctx.goal_id
    needed = ctx.memory.get("cap_process_needed")
    board_delta = ctx.memory.get("cap_process_board_delta")
    logs_remaining = ctx.memory.get("cap_process_logs_remaining")
    return bool(
        type(goal_id) is int
        and ctx.memory.get("cap_process_goal_id") == goal_id
        and ctx.memory.get("cap_process_finished_goal_id") == goal_id
        and type(needed) is int
        and needed > 0
        # The frozen N logs became exactly N boards (1:1), and none of those logs
        # remain — the whole admitted batch converted, goal-scoped.
        and type(board_delta) is int
        and board_delta == needed
        and type(logs_remaining) is int
        and logs_remaining == 0
        and ctx.obs.pending_target is None
        and _process_can_yield(ctx)
    )


def _process_progress(ctx: SkillContext) -> float:
    if ctx.memory.get("cap_process_goal_id") != ctx.goal_id:
        return 0.0
    needed = ctx.memory.get("cap_process_needed")
    board_delta = ctx.memory.get("cap_process_board_delta")
    if type(needed) is not int or needed <= 0 or type(board_delta) is not int:
        return 0.0
    return max(0.0, min(1.0, board_delta / needed))


_PROCESS_LOGS = CapabilityBinding(
    capability_id="process_logs",
    profession="lumberjack",
    skill_type=ProcessLogsGoal,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_process_ready,
    achieved=_process_achieved,
    progress=_process_progress,
    can_yield=_process_can_yield,
    default_deadline_ticks=180,
)

_SELL_BOARDS = CapabilityBinding(
    capability_id="sell_boards",
    profession="lumberjack",
    skill_type=SellBoards,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_sell_ready(
        SellBoards.sold_graphic, SellBoards.sell_threshold, SellBoards.vendor_spot_key
    ),
    achieved=_sell_achieved,
    progress=_sell_progress,
    can_yield=_sell_can_yield,
    default_deadline_ticks=180,
)

_LUMBER_BANK_GOLD = CapabilityBinding(
    capability_id="bank_gold",
    profession="lumberjack",
    skill_type=BankGold,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_bank_ready,
    achieved=_bank_achieved,
    progress=_bank_progress,
    can_yield=_bank_can_yield,
    default_deadline_ticks=120,
)

_BUY_HATCHET = CapabilityBinding(
    capability_id="buy_hatchet",
    profession="lumberjack",
    skill_type=BuyHatchet,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_toolbuy_ready(
        BuyHatchet.owned_tool_graphics, BuyHatchet.tool_price_estimate, BuyHatchet.vendor_spot_key
    ),
    achieved=_make_toolbuy_achieved(BuyHatchet.owned_tool_graphics),
    progress=_toolbuy_progress,
    can_yield=_toolbuy_can_yield,
    default_deadline_ticks=180,
)


# --- deliver_boards (Brick 6) — the lumberjack half of the board-delivery trade
# pair. Haul the pack's boards to the `carpenter_drop` point and drop them on the
# ground for the carpenter's `fetch_boards` to pick up (the board-typed,
# goal-scoped analog of the miner's `smelt.py::MineSmeltDeliver` deliver leg).


def _deliver_can_yield(ctx: SkillContext) -> bool:
    """Safe to yield/cancel a deliver_boards goal only between drop/walk gestures
    (no open cursor/gump), and only before it starts or once it has finished —
    mirrors `_process_can_yield`."""
    goal_id = ctx.goal_id
    started = type(goal_id) is int and ctx.memory.get("cap_deliver_goal_id") == goal_id
    finished = bool(
        started and ctx.memory.get("cap_deliver_finished_goal_id") == goal_id
    )
    return bool(
        type(goal_id) is int
        and (not started or finished)
        and ctx.obs.pending_target is None
        and not ctx.obs.gumps
    )


def _deliver_ready(ctx: SkillContext) -> bool:
    obs = ctx.obs
    return bool(
        _backpack_serial(ctx) is not None
        # Enough boards to carry AND a drop point to carry them to — the deliver
        # trigger. A full throne's worth (19) so a delivery is a craftable batch.
        and _pack_graphic(ctx, BOARD_GRAPHIC) >= DeliverBoards.deliver_threshold
        and _valid_spot(ctx.memory.get("carpenter_drop"))
        # An idle UI, not mid a market trip — mirrors `_process_ready` (checked
        # before a goal is admitted, so it cannot require a goal_id the way the
        # goal-scoped `_deliver_can_yield` does).
        and ctx.memory.get("mkt_phase", "craft") == "craft"
        and obs.pending_target is None
        and not obs.gumps
        and obs.popup is None
        and obs.shop_buy is None
        and obs.shop_sell is None
    )


def _deliver_achieved(ctx: SkillContext) -> bool:
    goal_id = ctx.goal_id
    needed = ctx.memory.get("cap_deliver_needed")
    delivered = ctx.memory.get("cap_deliver_delivered")
    boards_remaining = ctx.memory.get("cap_deliver_boards_remaining")
    return bool(
        type(goal_id) is int
        and ctx.memory.get("cap_deliver_goal_id") == goal_id
        and ctx.memory.get("cap_deliver_finished_goal_id") == goal_id
        and type(needed) is int
        and needed > 0
        # The frozen N boards all left the pack (delivered == N, 0 remain) — the
        # whole admitted haul dropped, goal-scoped.
        and type(delivered) is int
        and delivered == needed
        and type(boards_remaining) is int
        and boards_remaining == 0
        and _pack_graphic(ctx, BOARD_GRAPHIC) == 0
        and _deliver_can_yield(ctx)
    )


def _deliver_progress(ctx: SkillContext) -> float:
    if ctx.memory.get("cap_deliver_goal_id") != ctx.goal_id:
        return 0.0
    needed = ctx.memory.get("cap_deliver_needed")
    delivered = ctx.memory.get("cap_deliver_delivered")
    if type(needed) is not int or needed <= 0 or type(delivered) is not int:
        return 0.0
    return max(0.0, min(1.0, delivered / needed))


_DELIVER_BOARDS = CapabilityBinding(
    capability_id="deliver_boards",
    profession="lumberjack",
    skill_type=DeliverBoards,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_deliver_ready,
    achieved=_deliver_achieved,
    progress=_deliver_progress,
    can_yield=_deliver_can_yield,
    default_deadline_ticks=180,
)

# --- carpenter capabilities (Bricks 4-5) — the same config-attr/factory shape as
# the lumberjack. `craft_carpentry` is the craft analog of `craft_daggers` (a
# Throne from boards, via the no-material-submenu carpentry gump); `sell_furniture`
# /`buy_boards`/`buy_saw` reuse the sell/material-buy/tool-buy machinery with the
# carpenter skill classes' own config; `bank_gold` reuses the profession-agnostic
# gold leaf funcs verbatim. All four vendor legs use the ONE `vendor_spot`
# Carpenter NPC (SBCarpenter buys furniture + boards and sells boards + saws).

_CRAFT_CARPENTRY = CapabilityBinding(
    capability_id="craft_carpentry",
    profession="carpenter",
    skill_type=CarpenterCraft,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_craft_ready_for(CarpenterCraft),
    achieved=_craft_achieved_for(CarpenterCraft),
    progress=_craft_progress,
    can_yield=_craft_can_yield,
    default_deadline_ticks=300,
)

_SELL_FURNITURE = CapabilityBinding(
    capability_id="sell_furniture",
    profession="carpenter",
    skill_type=SellFurniture,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_sell_ready(
        SellFurniture.sold_graphic, SellFurniture.sell_threshold, SellFurniture.vendor_spot_key
    ),
    achieved=_sell_achieved,
    progress=_sell_progress,
    can_yield=_sell_can_yield,
    default_deadline_ticks=180,
)

_CARPENTER_BANK_GOLD = CapabilityBinding(
    capability_id="bank_gold",
    profession="carpenter",
    skill_type=BankGold,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_bank_ready,
    achieved=_bank_achieved,
    progress=_bank_progress,
    can_yield=_bank_can_yield,
    default_deadline_ticks=120,
)

_BUY_BOARDS = CapabilityBinding(
    capability_id="buy_boards",
    profession="carpenter",
    skill_type=BuyBoards,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_buy_ready_for(BuyBoards),
    achieved=_buy_achieved,
    progress=_buy_progress,
    can_yield=_buy_can_yield,
    default_deadline_ticks=180,
)

_BUY_SAW = CapabilityBinding(
    capability_id="buy_saw",
    profession="carpenter",
    skill_type=BuySaw,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_toolbuy_ready(
        BuySaw.owned_tool_graphics, BuySaw.tool_price_estimate, BuySaw.vendor_spot_key
    ),
    achieved=_make_toolbuy_achieved(BuySaw.owned_tool_graphics),
    progress=_toolbuy_progress,
    can_yield=_toolbuy_can_yield,
    default_deadline_ticks=180,
)


# --- fetch_boards (Brick 6) — the carpenter half of the board-delivery trade
# pair. Pick up ground boards the lumberjack's `deliver_boards` dropped nearby
# into the pack, to feed `craft_carpentry` (the board-typed, goal-scoped analog
# of the blacksmith's `craft.py::Blacksmith._fetch_step` dropped-ingot pickup).

# The fetch gate: only when the pack can't already craft a throne (below one
# throne's boards). Reads `CarpenterCraft`'s own per-item board cost (19) so it
# stays in lockstep with the craft gate that consumes it.
_FETCH_BOARDS_THRESHOLD = CarpenterCraft.craft_material_per_item


def _nearby_ground_boards(ctx: SkillContext):
    """The nearest board pile ON THE GROUND within `PICKUP_RADIUS` (a world item,
    `container is None` — never our own pack boards), or `None`. Mirrors the
    board-typed `FetchBoards._nearby_ground_boards`."""
    return next(
        (
            item
            for item in ctx.obs.items
            if item.graphic == BOARD_GRAPHIC
            and item.container is None
            and item.distance <= PICKUP_RADIUS
        ),
        None,
    )


def _fetch_can_yield(ctx: SkillContext) -> bool:
    """Safe to yield/cancel a fetch_boards goal only between pickup/walk gestures
    (no open cursor/gump), and only before it starts or once it has finished —
    mirrors `_deliver_can_yield`."""
    goal_id = ctx.goal_id
    started = type(goal_id) is int and ctx.memory.get("cap_fetch_goal_id") == goal_id
    finished = bool(
        started and ctx.memory.get("cap_fetch_finished_goal_id") == goal_id
    )
    return bool(
        type(goal_id) is int
        and (not started or finished)
        and ctx.obs.pending_target is None
        and not ctx.obs.gumps
    )


def _fetch_ready(ctx: SkillContext) -> bool:
    obs = ctx.obs
    return bool(
        _backpack_serial(ctx) is not None
        # A board pile is on the ground nearby to fetch AND the pack can't already
        # craft a throne (below one throne's boards) — so it only fetches when it
        # genuinely needs boards, never on top of a craftable stock.
        and _nearby_ground_boards(ctx) is not None
        and _pack_graphic(ctx, BOARD_GRAPHIC) < _FETCH_BOARDS_THRESHOLD
        and ctx.memory.get("mkt_phase", "craft") == "craft"
        and obs.pending_target is None
        and not obs.gumps
        and obs.popup is None
        and obs.shop_buy is None
        and obs.shop_sell is None
    )


def _fetch_achieved(ctx: SkillContext) -> bool:
    goal_id = ctx.goal_id
    fetched = ctx.memory.get("cap_fetch_fetched")
    ground_remaining = ctx.memory.get("cap_fetch_ground_remaining")
    return bool(
        type(goal_id) is int
        and ctx.memory.get("cap_fetch_goal_id") == goal_id
        and ctx.memory.get("cap_fetch_finished_goal_id") == goal_id
        # Boards rose from the frozen baseline (fetched > 0) and no ground pile
        # remains nearby — goal-scoped.
        and type(fetched) is int
        and fetched > 0
        and type(ground_remaining) is int
        and ground_remaining == 0
        and _nearby_ground_boards(ctx) is None
        and _fetch_can_yield(ctx)
    )


def _fetch_progress(ctx: SkillContext) -> float:
    if ctx.memory.get("cap_fetch_goal_id") != ctx.goal_id:
        return 0.0
    fetched = ctx.memory.get("cap_fetch_fetched")
    ground_remaining = ctx.memory.get("cap_fetch_ground_remaining")
    if type(fetched) is not int or type(ground_remaining) is not int:
        return 0.0
    total = fetched + ground_remaining
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, fetched / total))


_FETCH_BOARDS = CapabilityBinding(
    capability_id="fetch_boards",
    profession="carpenter",
    skill_type=FetchBoards,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_fetch_ready,
    achieved=_fetch_achieved,
    progress=_fetch_progress,
    can_yield=_fetch_can_yield,
    default_deadline_ticks=180,
)

# --- tinker capabilities (Bricks 7-10) — the same config-attr/factory shape as
# the carpenter. `craft_tongs` is the craft analog of `craft_daggers` (Tongs from
# iron, via the no-material-submenu tinkering gump); `sell_tongs`/`buy_iron`/
# `buy_tinker_tool` reuse the sell/material-buy/tool-buy machinery with the tinker
# skill classes' own config; `bank_gold` reuses the profession-agnostic gold leaf
# funcs verbatim. All four vendor legs use the ONE `vendor_spot` Tinker NPC
# (SBTinker buys tongs and sells iron + tinker's tools).

_CRAFT_TONGS = CapabilityBinding(
    capability_id="craft_tongs",
    profession="tinker",
    skill_type=TinkerTongs,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_craft_ready_for(TinkerTongs),
    achieved=_craft_achieved_for(TinkerTongs),
    progress=_craft_progress,
    can_yield=_craft_can_yield,
    default_deadline_ticks=300,
)

_SELL_TONGS = CapabilityBinding(
    capability_id="sell_tongs",
    profession="tinker",
    skill_type=SellTongs,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_sell_ready(
        SellTongs.sold_graphic, SellTongs.sell_threshold, SellTongs.vendor_spot_key
    ),
    achieved=_sell_achieved,
    progress=_sell_progress,
    can_yield=_sell_can_yield,
    default_deadline_ticks=180,
)

_TINKER_BANK_GOLD = CapabilityBinding(
    capability_id="bank_gold",
    profession="tinker",
    skill_type=BankGold,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_bank_ready,
    achieved=_bank_achieved,
    progress=_bank_progress,
    can_yield=_bank_can_yield,
    default_deadline_ticks=120,
)

_BUY_IRON = CapabilityBinding(
    capability_id="buy_iron",
    profession="tinker",
    skill_type=BuyIron,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_buy_ready_for(BuyIron),
    achieved=_buy_achieved,
    progress=_buy_progress,
    can_yield=_buy_can_yield,
    default_deadline_ticks=180,
)

_BUY_TINKER_TOOL = CapabilityBinding(
    capability_id="buy_tinker_tool",
    profession="tinker",
    skill_type=BuyTinkerTool,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_toolbuy_ready(
        BuyTinkerTool.owned_tool_graphics, BuyTinkerTool.tool_price_estimate,
        BuyTinkerTool.vendor_spot_key,
    ),
    achieved=_make_toolbuy_achieved(BuyTinkerTool.owned_tool_graphics),
    progress=_toolbuy_progress,
    can_yield=_toolbuy_can_yield,
    default_deadline_ticks=180,
)

# --- Brick 10: the closed-village tool-supply link ---------------------------
# The tinker forges the village's wooden-working tools (a Saw for the carpenter,
# a Hatchet for the lumberjack) and DELIVERS one spare of each to its
# counterpart's drop slot, maintaining exactly ONE spare there. A counterpart
# whose tool breaks FETCHES the delivered spare instead of BUYING one from a
# vendor — closing the village (no vendor tool purchases). So the deliver/craft
# side carries a NO-OVERSUPPLY gate (never forge or deliver a second spare while
# one already sits at the drop slot) and the fetch side fires only when the
# worker's OWN tool has broken (no working tool in the pack). All four leaf funcs
# reuse the Brick-6 board deliver/fetch machinery (cap_deliver_*/cap_fetch_*
# memory keys, goal-scoped), generalized over each skill's own graphics set.


def _ground_graphics_near(
    ctx: SkillContext, graphics: frozenset[int], point: object
) -> bool:
    """True iff a world item (`container is None`) of `graphics` sits within
    `PICKUP_RADIUS` of the (x, y) `point` — the no-oversupply gate's "a spare is
    already at the drop slot" check. Uses each item's own position (not its
    distance-from-player), since the drop slot is a fixed tile, not the tinker."""
    if not (
        isinstance(point, (tuple, list))
        and len(point) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in point)
    ):
        return False
    px, py = point
    return any(
        item.graphic in graphics
        and item.container is None
        and max(abs(item.pos.x - px), abs(item.pos.y - py)) <= PICKUP_RADIUS
        for item in ctx.obs.items
    )


def _nearby_ground_graphics(ctx: SkillContext, graphics: frozenset[int]):
    """The nearest world item (`container is None`) of `graphics` within
    `PICKUP_RADIUS` of the player, or `None` — the generalized fetch trigger
    (`items` is distance-sorted, so the first match is nearest). Mirrors
    `_nearby_ground_boards`, tool-graphics-set-typed."""
    return next(
        (
            item
            for item in ctx.obs.items
            if item.graphic in graphics
            and item.container is None
            and item.distance <= PICKUP_RADIUS
        ),
        None,
    )


def _make_deliver_ready(skill_cls: type) -> Callable[[SkillContext], bool]:
    """A deliver readiness gate from a `DeliverBoards` subclass's own config:
    enough `delivered_graphics` to carry, a valid `drop_key` point, and an idle UI
    — AND a NO-OVERSUPPLY gate: NOT ready if a matching spare is already on the
    ground at the drop slot (so the tinker keeps exactly ONE spare per slot).

    Only the two TOOL delivers use this; `deliver_boards` keeps the unchanged
    `_deliver_ready` (boards get consumed, so no oversupply gate is needed there),
    staying byte-identical to Brick 6."""
    graphics = frozenset(skill_cls.delivered_graphics)
    threshold = skill_cls.deliver_threshold
    drop_key = skill_cls.drop_key

    def ready(ctx: SkillContext) -> bool:
        obs = ctx.obs
        drop = ctx.memory.get(drop_key)
        return bool(
            _backpack_serial(ctx) is not None
            and _pack_graphics(ctx, graphics) >= threshold
            and _valid_spot(drop)
            and not _ground_graphics_near(ctx, graphics, drop)
            and ctx.memory.get("mkt_phase", "craft") == "craft"
            and obs.pending_target is None
            and not obs.gumps
            and obs.popup is None
            and obs.shop_buy is None
            and obs.shop_sell is None
        )

    return ready


def _make_deliver_achieved(skill_cls: type) -> Callable[[SkillContext], bool]:
    """A deliver completion proof from a `DeliverBoards` subclass's config — the
    pack count summed over `delivered_graphics` must reach 0 (the whole haul
    dropped), goal-scoped. Mirrors `_deliver_achieved`, generalized off the tool
    graphics rather than the single `BOARD_GRAPHIC`."""
    graphics = frozenset(skill_cls.delivered_graphics)

    def achieved(ctx: SkillContext) -> bool:
        goal_id = ctx.goal_id
        needed = ctx.memory.get("cap_deliver_needed")
        delivered = ctx.memory.get("cap_deliver_delivered")
        remaining = ctx.memory.get("cap_deliver_boards_remaining")
        return bool(
            type(goal_id) is int
            and ctx.memory.get("cap_deliver_goal_id") == goal_id
            and ctx.memory.get("cap_deliver_finished_goal_id") == goal_id
            and type(needed) is int
            and needed > 0
            and type(delivered) is int
            and delivered == needed
            and type(remaining) is int
            and remaining == 0
            and _pack_graphics(ctx, graphics) == 0
            and _deliver_can_yield(ctx)
        )

    return achieved


def _make_tool_craft_ready(
    craft_cls: type, tool_graphics: frozenset[int], drop_key: str
) -> Callable[[SkillContext], bool]:
    """A tool-craft readiness gate: the normal `_craft_ready_for(craft_cls)` AND a
    VALID drop point AND that drop slot is empty (no `tool_graphics` on the ground
    at `memory[drop_key]`) AND the pack holds no such tool. So the tinker forges a
    spare only for a real counterpart (a wired drop key — fail-closed, matching
    `_make_deliver_ready` so a forged tool is never stranded undeliverable) and
    only when both the slot AND the pack are empty of that tool — never a second on
    top of a delivered spare, never while it still carries an undelivered one.
    A standalone tinker (no drop key) never forges tools -> falls through to its
    tongs income loop, so the tool crafts can sit at higher priority safely."""
    base_ready = _craft_ready_for(craft_cls)
    graphics = frozenset(tool_graphics)

    def ready(ctx: SkillContext) -> bool:
        drop = ctx.memory.get(drop_key)
        return bool(
            base_ready(ctx)
            and _valid_spot(drop)
            and not _ground_graphics_near(ctx, graphics, drop)
            and _pack_graphics(ctx, graphics) == 0
        )

    return ready


def _make_tool_fetch_ready(
    tool_graphics: frozenset[int],
) -> Callable[[SkillContext], bool]:
    """A fetch-tool readiness gate: a backpack, a matching tool on the ground
    nearby, the worker's OWN tool broken (no working `tool_graphics` in the pack —
    the trigger that the tool broke), and an idle UI. Mirrors `_fetch_ready`,
    tool-typed."""
    graphics = frozenset(tool_graphics)

    def ready(ctx: SkillContext) -> bool:
        obs = ctx.obs
        return bool(
            _backpack_serial(ctx) is not None
            and _nearby_ground_graphics(ctx, graphics) is not None
            and _owned_tool(ctx, graphics) is None
            and ctx.memory.get("mkt_phase", "craft") == "craft"
            and obs.pending_target is None
            and not obs.gumps
            and obs.popup is None
            and obs.shop_buy is None
            and obs.shop_sell is None
        )

    return ready


def _make_tool_fetch_achieved(
    tool_graphics: frozenset[int],
) -> Callable[[SkillContext], bool]:
    """A fetch-tool completion proof: the pack tool count rose (fetched > 0) and no
    ground tool remains nearby, goal-scoped. Mirrors `_fetch_achieved`, tool-typed
    (a working tool now in the pack retires the broken-tool trigger)."""
    graphics = frozenset(tool_graphics)

    def achieved(ctx: SkillContext) -> bool:
        goal_id = ctx.goal_id
        fetched = ctx.memory.get("cap_fetch_fetched")
        ground_remaining = ctx.memory.get("cap_fetch_ground_remaining")
        return bool(
            type(goal_id) is int
            and ctx.memory.get("cap_fetch_goal_id") == goal_id
            and ctx.memory.get("cap_fetch_finished_goal_id") == goal_id
            and type(fetched) is int
            and fetched > 0
            and type(ground_remaining) is int
            and ground_remaining == 0
            and _nearby_ground_graphics(ctx, graphics) is None
            and _fetch_can_yield(ctx)
        )

    return achieved


# lumberjack: FETCH the tinker's delivered Hatchet instead of BUYING one.
_FETCH_HATCHET = CapabilityBinding(
    capability_id="fetch_hatchet",
    profession="lumberjack",
    skill_type=FetchHatchet,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_tool_fetch_ready(FetchHatchet.fetched_graphics),
    achieved=_make_tool_fetch_achieved(FetchHatchet.fetched_graphics),
    progress=_fetch_progress,
    can_yield=_fetch_can_yield,
    default_deadline_ticks=180,
)

# carpenter: FETCH the tinker's delivered Saw instead of BUYING one.
_FETCH_SAW = CapabilityBinding(
    capability_id="fetch_saw",
    profession="carpenter",
    skill_type=FetchSaw,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_tool_fetch_ready(FetchSaw.fetched_graphics),
    achieved=_make_tool_fetch_achieved(FetchSaw.fetched_graphics),
    progress=_fetch_progress,
    can_yield=_fetch_can_yield,
    default_deadline_ticks=180,
)

# tinker: forge + deliver each village tool, one spare per counterpart's slot.
_CRAFT_SAW = CapabilityBinding(
    capability_id="craft_saw",
    profession="tinker",
    skill_type=TinkerSaw,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_tool_craft_ready(
        TinkerSaw, DeliverSaw.delivered_graphics, DeliverSaw.drop_key
    ),
    achieved=_craft_achieved_for(TinkerSaw),
    progress=_craft_progress,
    can_yield=_craft_can_yield,
    default_deadline_ticks=300,
)

_CRAFT_HATCHET = CapabilityBinding(
    capability_id="craft_hatchet",
    profession="tinker",
    skill_type=TinkerHatchet,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_tool_craft_ready(
        TinkerHatchet, DeliverHatchet.delivered_graphics, DeliverHatchet.drop_key
    ),
    achieved=_craft_achieved_for(TinkerHatchet),
    progress=_craft_progress,
    can_yield=_craft_can_yield,
    default_deadline_ticks=300,
)

_DELIVER_SAW = CapabilityBinding(
    capability_id="deliver_saw",
    profession="tinker",
    skill_type=DeliverSaw,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_deliver_ready(DeliverSaw),
    achieved=_make_deliver_achieved(DeliverSaw),
    progress=_deliver_progress,
    can_yield=_deliver_can_yield,
    default_deadline_ticks=180,
)

_DELIVER_HATCHET = CapabilityBinding(
    capability_id="deliver_hatchet",
    profession="tinker",
    skill_type=DeliverHatchet,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_make_deliver_ready(DeliverHatchet),
    achieved=_make_deliver_achieved(DeliverHatchet),
    progress=_deliver_progress,
    can_yield=_deliver_can_yield,
    default_deadline_ticks=180,
)

CAPABILITIES: Mapping[tuple[str, str], CapabilityBinding] = MappingProxyType(
    {
        (_SELL_DAGGERS.profession, _SELL_DAGGERS.capability_id): _SELL_DAGGERS,
        (_BANK_GOLD.profession, _BANK_GOLD.capability_id): _BANK_GOLD,
        (_CRAFT_DAGGERS.profession, _CRAFT_DAGGERS.capability_id): _CRAFT_DAGGERS,
        (_BUY_INGOTS.profession, _BUY_INGOTS.capability_id): _BUY_INGOTS,
        (_BUY_SMITH_TOOL.profession, _BUY_SMITH_TOOL.capability_id): _BUY_SMITH_TOOL,
        (_PROCESS_LOGS.profession, _PROCESS_LOGS.capability_id): _PROCESS_LOGS,
        (_SELL_BOARDS.profession, _SELL_BOARDS.capability_id): _SELL_BOARDS,
        (_LUMBER_BANK_GOLD.profession, _LUMBER_BANK_GOLD.capability_id): _LUMBER_BANK_GOLD,
        # fetch_hatchet BEFORE buy_hatchet: prefer the tinker's FREE delivered
        # hatchet over BUYING one from the WeaponSmith (closing the village —
        # no vendor tool purchases, Brick 10).
        (_FETCH_HATCHET.profession, _FETCH_HATCHET.capability_id): _FETCH_HATCHET,
        (_BUY_HATCHET.profession, _BUY_HATCHET.capability_id): _BUY_HATCHET,
        (_DELIVER_BOARDS.profession, _DELIVER_BOARDS.capability_id): _DELIVER_BOARDS,
        (_CRAFT_CARPENTRY.profession, _CRAFT_CARPENTRY.capability_id): _CRAFT_CARPENTRY,
        (_SELL_FURNITURE.profession, _SELL_FURNITURE.capability_id): _SELL_FURNITURE,
        (_CARPENTER_BANK_GOLD.profession, _CARPENTER_BANK_GOLD.capability_id): _CARPENTER_BANK_GOLD,
        # fetch_boards BEFORE buy_boards: prefer the lumberjack's FREE delivered
        # boards over BUYING boards (which loses money — furniture is value-negative
        # at NPC prices, docs/LUMBER-CARPENTER-TINKER.md's economics finding).
        (_FETCH_BOARDS.profession, _FETCH_BOARDS.capability_id): _FETCH_BOARDS,
        (_BUY_BOARDS.profession, _BUY_BOARDS.capability_id): _BUY_BOARDS,
        # fetch_saw BEFORE buy_saw: prefer the tinker's FREE delivered saw over
        # BUYING one from the Carpenter vendor (Brick 10).
        (_FETCH_SAW.profession, _FETCH_SAW.capability_id): _FETCH_SAW,
        (_BUY_SAW.profession, _BUY_SAW.capability_id): _BUY_SAW,
        # Brick 10: the tinker forges + delivers the village's wooden-working tools
        # BEFORE its tongs income loop. Each tool craft is DEMAND-gated (ready only
        # when that counterpart's drop key is wired AND its drop slot is empty), so
        # a spare is forged only when actually needed; when both slots are full (or
        # the tinker is standalone, no drop keys) these fall through to the Tongs
        # income loop. Interleaved craft->deliver so the pack holds <=1 forged tool.
        (_CRAFT_SAW.profession, _CRAFT_SAW.capability_id): _CRAFT_SAW,
        (_DELIVER_SAW.profession, _DELIVER_SAW.capability_id): _DELIVER_SAW,
        (_CRAFT_HATCHET.profession, _CRAFT_HATCHET.capability_id): _CRAFT_HATCHET,
        (_DELIVER_HATCHET.profession, _DELIVER_HATCHET.capability_id): _DELIVER_HATCHET,
        (_CRAFT_TONGS.profession, _CRAFT_TONGS.capability_id): _CRAFT_TONGS,
        (_SELL_TONGS.profession, _SELL_TONGS.capability_id): _SELL_TONGS,
        (_TINKER_BANK_GOLD.profession, _TINKER_BANK_GOLD.capability_id): _TINKER_BANK_GOLD,
        (_BUY_IRON.profession, _BUY_IRON.capability_id): _BUY_IRON,
        (_BUY_TINKER_TOOL.profession, _BUY_TINKER_TOOL.capability_id): _BUY_TINKER_TOOL,
    }
)


def _valid_capability_skill_manifest(
    profession: str,
    skills: tuple[Skill, ...],
) -> bool:
    """Match the exact shipped factory order, types, bindings, and defaults."""

    # Local imports avoid a module cycle: Profession calls the issuer only
    # after this module and its own class definitions have finished loading.
    from .profession import CapabilityBoundSkill, CapabilityGoalComplete, CapabilityWait
    from .skills import GoTo, Greet, RecoverDeath, SpeakPending, Survive, Wander

    bindings = tuple(
        binding
        for (bound_profession, _capability), binding in CAPABILITIES.items()
        if bound_profession == profession
    )
    expected_length = 8 + len(bindings)
    if len(skills) != expected_length:
        return False
    prefix = skills[:4]
    goal_complete = skills[4]
    bound_skills = skills[5 : 5 + len(bindings)]
    wait, greet, wander = skills[-3:]
    if tuple(type(skill) for skill in prefix) != (
        Survive,
        RecoverDeath,
        SpeakPending,
        GoTo,
    ):
        return False
    if (
        vars(prefix[0]) != {}
        or vars(prefix[1]) != {"resurrection_target": None}
        or vars(prefix[2]) != {}
        or vars(prefix[3]) != {}
        or type(goal_complete) is not CapabilityGoalComplete
        or vars(goal_complete) != {"profession": profession}
        or type(wait) is not CapabilityWait
        or vars(wait) != {"profession": profession}
        or type(greet) is not Greet
        or vars(greet) != {}
        or type(wander) is not Wander
        or vars(wander) != {}
    ):
        return False
    for wrapper, binding in zip(bound_skills, bindings, strict=True):
        if type(wrapper) is not CapabilityBoundSkill:
            return False
        inner = getattr(wrapper, "inner", None)
        if type(inner) is not binding.skill_type or vars(inner) != {}:
            return False
        if vars(wrapper) != {
            "profession": profession,
            "inner": inner,
            "name": inner.name,
            "description": inner.description,
        }:
            return False
    return True


def issue_capability_planner_lease(
    profession: str,
    skills: tuple[Skill, ...],
) -> CapabilityPlannerLease:
    """Bind a lease to one exact, validated factory skill manifest."""

    capability_ids = frozenset(
        capability
        for bound_profession, capability in CAPABILITIES
        if bound_profession == profession
    )
    if not capability_ids:
        raise ValueError(f"profession {profession!r} has no installed capabilities")
    if not _valid_capability_skill_manifest(profession, skills):
        raise ValueError("capability planner does not match the shipped factory manifest")
    return CapabilityPlannerLease(
        profession,
        capability_ids,
        skills,
        _PLANNER_AUTHORITY,
    )


def valid_capability_planner_lease(
    value: object,
    skills: tuple[Skill, ...],
) -> bool:
    """Validate planner provenance without trusting mutable marker attributes."""

    return bool(
        type(value) is CapabilityPlannerLease
        and value._authority is _PLANNER_AUTHORITY
        and value.capability_ids
        and len(value.installed_skills) == len(skills)
        and all(
            installed is current
            for installed, current in zip(value.installed_skills, skills, strict=True)
        )
        and _valid_capability_skill_manifest(value.profession, skills)
        and value.capability_ids
        == frozenset(
            capability
            for bound_profession, capability in CAPABILITIES
            if bound_profession == value.profession
        )
    )


def ready_capability_ids(
    profession: str,
    ctx: SkillContext,
    source: GoalSource = GoalSource.COGNITION,
) -> tuple[str, ...]:
    """Return advisory, registry-ordered choices ready in a cognition snapshot.

    This helper grants no execution authority. The Agent later resolves the
    returned opaque id against its live context, creates a separate canonical
    Goal, and installs the deadline. A stale or mutated snapshot therefore
    cannot make an unready operation executable.
    """

    if type(profession) is not str or not isinstance(ctx, SkillContext):
        return ()
    if not isinstance(source, GoalSource):
        return ()
    ready: list[str] = []
    for (bound_profession, capability_id), binding in CAPABILITIES.items():
        if bound_profession != profession or source not in binding.allowed_sources:
            continue
        try:
            if binding.ready(ctx) and not binding.achieved(ctx):
                ready.append(capability_id)
        except Exception:  # noqa: BLE001 — proposal discovery fails closed
            continue
    return tuple(ready)


def capability_goal(profession: str, capability: str) -> Goal:
    """Construct an unsealed request; admission returns a separate sealed copy."""

    return Goal(
        kind="capability",
        params={"schema": 1, "profession": profession, "capability": capability},
    )


def binding_for_goal(
    goal: Goal,
    profession: str,
    source: GoalSource,
) -> CapabilityBinding | None:
    """Resolve structure and authority only, with exact keys and types."""

    binding = _structural_binding_for_goal(goal, profession)
    if (
        binding is None
        or not isinstance(source, GoalSource)
        or source not in binding.allowed_sources
    ):
        return None
    return binding


def installed_binding_for_goal(
    goal: Goal,
    profession: str,
) -> CapabilityBinding | None:
    """Resolve an already-admitted frame against installed profession hands."""

    if not isinstance(goal, Goal) or not goal.sealed_by(_CAPABILITY_AUTHORITY):
        return None
    return _structural_binding_for_goal(goal, profession)


def execution_goal_copy(goal: Goal, profession: str) -> Goal | None:
    """Return a fresh authority-sealed copy for deterministic SkillContext use."""

    binding = installed_binding_for_goal(goal, profession)
    if binding is None:
        return None
    return capability_goal(binding.profession, binding.capability_id).seal(
        _CAPABILITY_AUTHORITY
    )


def policy_binding_for_context(
    ctx: SkillContext,
    profession: str,
) -> CapabilityBinding | None:
    """Require both a canonical Goal and the exact Agent-installed policy."""

    policy = ctx.goal_policy
    if type(policy) is not CapabilityPolicy or policy.profession != profession:
        return None
    if ctx.goal is None:
        return None
    binding = installed_binding_for_goal(ctx.goal, profession)
    if binding is None or binding.capability_id not in policy.capability_ids:
        return None
    return binding


def _structural_binding_for_goal(
    goal: Goal,
    profession: str,
) -> CapabilityBinding | None:
    if not isinstance(goal, Goal) or goal.kind != "capability":
        return None
    params = goal.params
    if not isinstance(params, Mapping) or set(params) != _GOAL_KEYS:
        return None
    schema = params.get("schema")
    goal_profession = params.get("profession")
    capability_id = params.get("capability")
    if type(schema) is not int or schema != 1:
        return None
    if type(goal_profession) is not str or goal_profession != profession:
        return None
    if type(capability_id) is not str or len(capability_id) > 80:
        return None
    binding = CAPABILITIES.get((profession, capability_id))
    if binding is None or binding.profession != profession:
        return None
    return binding


def resolve_capability(
    goal: Goal,
    profession: str,
    source: GoalSource,
    ctx: SkillContext,
) -> ResolvedCapability | None:
    """Admit a ready request and detach it from producer-owned mutable state."""

    binding = binding_for_goal(goal, profession, source)
    if binding is None:
        return None
    try:
        ready = bool(binding.ready(ctx))
    except Exception:  # noqa: BLE001 — authority callbacks fail closed
        ready = False
    if not ready:
        return None
    canonical = capability_goal(binding.profession, binding.capability_id).seal(
        _CAPABILITY_AUTHORITY
    )
    return ResolvedCapability(goal=canonical, binding=binding)


@dataclass(frozen=True)
class CapabilityPolicy:
    """Agent/planner policy view over the immutable registry for one profession."""

    profession: str
    capability_ids: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        capability_ids = frozenset(
            capability
            for (bound_profession, capability) in CAPABILITIES
            if bound_profession == self.profession
        )
        if not capability_ids:
            raise ValueError(f"profession {self.profession!r} has no installed capabilities")
        object.__setattr__(self, "capability_ids", capability_ids)

    def admit_goal(
        self,
        goal: Goal,
        ctx: SkillContext,
        source: GoalSource,
    ) -> GoalAdmission | None:
        resolved = resolve_capability(goal, self.profession, source, ctx)
        if resolved is None:
            return None
        return GoalAdmission(
            goal=resolved.goal,
            deadline_ticks=resolved.binding.default_deadline_ticks,
        )

    def binding(self, goal: Goal, source: GoalSource = GoalSource.COGNITION) -> CapabilityBinding | None:
        return binding_for_goal(goal, self.profession, source)

    def goal_progress(self, goal: Goal, ctx: SkillContext) -> float | None:
        binding = installed_binding_for_goal(goal, self.profession)
        if binding is None:
            return None
        try:
            return float(binding.progress(ctx))
        except Exception:  # noqa: BLE001 — telemetry callbacks fail closed
            return None

    def deadline_can_expire(self, goal: Goal, ctx: SkillContext) -> bool:
        """Expire only outside a transaction and never race observed success."""

        binding = installed_binding_for_goal(goal, self.profession)
        if binding is None:
            return False
        try:
            if binding.achieved(ctx):
                return False  # let the terminal skill archive SUCCESS this tick
            return bool(binding.can_yield(ctx))
        except Exception:  # noqa: BLE001 — deadline safety fails closed
            return False

    def can_preempt(self, goal: Goal, ctx: SkillContext) -> bool:
        """Allow direct Goal APIs only when no capability transaction owns hands."""

        binding = installed_binding_for_goal(goal, self.profession)
        if binding is None:
            return False
        try:
            return bool(binding.can_yield(ctx))
        except Exception:  # noqa: BLE001 — pre-emption safety fails closed
            return False
