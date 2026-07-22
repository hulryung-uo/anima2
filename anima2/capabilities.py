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
from .skills.craft import DAGGER_GRAPHIC, MIN_INGOTS, SMITH_TOOL_GRAPHICS, CraftDaggers
from .skills.harvest import AXE_GRAPHICS
from .skills.market import (
    BUY_AMOUNT,
    TOOL_BUY_AMOUNT,
    BankGold,
    BuyIngots,
    BuyTool,
    SellDaggers,
    _bank_reserve,
)
from .skills.smelt import INGOT_GRAPHICS
from .skills.woodwork import (
    LOG_GRAPHIC,
    BuyHatchet,
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


def _pack_ingots(ctx: SkillContext) -> int:
    backpack = _backpack_serial(ctx)
    if backpack is None:
        return 0
    return sum(
        item.amount
        for item in ctx.obs.items
        if item.graphic in INGOT_GRAPHICS and item.container == backpack
    )


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


def _craft_ready(ctx: SkillContext) -> bool:
    daggers = _pack_daggers(ctx)
    obs = ctx.obs
    return bool(
        _craft_at_spot(ctx)
        and _backpack_serial(ctx) is not None
        and _owned_smith_tool(ctx) is not None
        and 0 <= daggers < 5
        and _pack_ingots(ctx) >= MIN_INGOTS * (5 - daggers)
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


def _craft_achieved(ctx: SkillContext) -> bool:
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
        and item.graphic == DAGGER_GRAPHIC
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
        and start_count + needed == 5
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
        and all(type(cost) is int and cost in {0, MIN_INGOTS} for cost in failure_costs)
        and failed_ingots == sum(failure_costs)
        and ingots_used == MIN_INGOTS * needed + failed_ingots
        and start_ingots - _pack_ingots(ctx) == ingots_used
        and _pack_daggers(ctx) == 5
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


_CRAFT_DAGGERS = CapabilityBinding(
    capability_id="craft_daggers",
    profession="blacksmith",
    skill_type=CraftDaggers,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_craft_ready,
    achieved=_craft_achieved,
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

# Reorder trigger: below one full sale batch's worth of ingots. A five-dagger
# `craft_daggers` batch needs ``MIN_INGOTS * 5`` == 15 ingots (each dagger costs
# ``MIN_INGOTS`` == 3 — see `_craft_ready`), so at or above 15 the smith can
# still craft a whole batch from stock and nothing needs buying; below it the
# next batch would starve mid-run, which is exactly when a replenishment trip is
# worthwhile. ``BUY_AMOUNT`` (also 15) refills from empty back over this line.
_BUY_REORDER_INGOTS = MIN_INGOTS * 5
# This ServUO shard's iron-ingot unit price (``Scripts/VendorInfo/
# SBBlacksmith.cs``: ``GenericBuyInfo(typeof(IronIngot), 5, 16, 0x1BF2, 0)``),
# used ONLY as the readiness affordability estimate. The actual purchase reads
# the live price from the matching ``ShopBuyEntry`` and never hardcodes it (see
# `skills/market.py::BlacksmithMarket._iron_offer`). The order is also clamped to
# the vendor's live stock, so it never costs more than this estimate implies; if
# the live price is higher than the estimate, a buy simply fails server-side for
# want of gold and the goal times out — an optimistic estimate never overspends.
_IRON_UNIT_PRICE = 5

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


def _buy_ready(ctx: SkillContext) -> bool:
    return bool(
        _valid_spot(ctx.memory.get("vendor_spot"))
        and _backpack_serial(ctx) is not None
        and _pack_ingots(ctx) < _BUY_REORDER_INGOTS
        and _pack_gold(ctx) >= BUY_AMOUNT * _IRON_UNIT_PRICE
        and ctx.memory.get("bs_state", "open") not in {"fetch", "fetch_return"}
        and _buy_can_yield(ctx)
    )


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


_BUY_INGOTS = CapabilityBinding(
    capability_id="buy_ingots",
    profession="blacksmith",
    skill_type=BuyIngots,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_buy_ready,
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
# tongs, `BuyHatchet.tool_price_estimate == 27` for the lumberjack's hatchet).

# Suggested working-capital reserve for `bank_gold`'s opt-in `bank_reserve`: keep
# back enough pack gold to afford ONE iron replenishment batch AND ONE tool
# replacement (15*5 + 1*13 == 88), so a solo capability loop that banks its
# surplus can still fund the buy_ingots/buy_smith_tool fallbacks that bridge a
# supply gap instead of banking itself broke and stalling. Nothing sets
# `bank_reserve` by default (it stays 0 — byte-identical to B7); the loop/gate
# opts in by writing `memory["bank_reserve"] = WORKING_CAPITAL_RESERVE`.
WORKING_CAPITAL_RESERVE = (
    BUY_AMOUNT * _IRON_UNIT_PRICE + TOOL_BUY_AMOUNT * BuyTool.tool_price_estimate
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
        (_BUY_HATCHET.profession, _BUY_HATCHET.capability_id): _BUY_HATCHET,
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
