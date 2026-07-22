"""Live B8 gate: closed cognition buys one replacement smith tool and nothing else.

The near-exact mirror of ``live_buy_goal.py`` for a NON-stacking tool. The smith
starts with **no working tool** (its hammer/tongs has broken) but plenty of iron,
0 daggers, a known 100 gold, and a vendor route but no banker route — so sell (no
daggers), bank (no banker), craft (no tool), and buy_ingots (already has 15+
ingots) are all unready, and replacing the tool is the one thing left to do.

The GM fixture is closed before the production Agent exists.  The model client
first returns a schema-invalid request, then the exact ``buy_smith_tool`` id.
Passing requires the live sequence PopupRequest -> PopupSelect(Buy) -> BuyItems,
only the vendor's tongs offer being bought (never its iron/armour/weapons/shield),
the quoted gold leaving the pack while exactly one smith tool ARRIVES (a 0->1
count delta, since tools don't stack), one sealed cognition frame succeeding, and
no craft/sell/bank action being emitted.

Usage::

    python -m anima2.live_toolbuy_goal --suffix b8toolmanual
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from .agent import Agent
from .capabilities import CapabilityPolicy, capability_goal, installed_binding_for_goal
from .capability_cognition import CapabilityCognition
from .cognition import ThreadedCognition
from .contract import (
    Action,
    BuyItems,
    Observation,
    PopupRequest,
    PopupSelect,
    SellItems,
)
from .control import GmControl
from .goals import GoalOutcome, GoalSource
from .ipc_body import ResilientIpcBody, SUPPORTED_SCHEMA_VERSION
from .live_common import (
    GM_RELOGIN_COOLDOWN_S,
    fresh_suffix,
    login_throttle,
    print_gate_verdict,
    wipe_area,
)
from .persona import Persona
from .profession import (
    PROFESSIONS,
    TRADE_SMITH_SPOT,
    VENDOR_SPOT,
    CapabilityBoundSkill,
)
from .skills.base import Goal, Skill, SkillContext
from .skills.craft import DAGGER_GRAPHIC, SMITH_TOOL_GRAPHICS
from .skills.harvest import BACKPACK_LAYER
from .skills.market import (
    BUY_CLILOC,
    GOLD_GRAPHIC,
    SMITH_TONGS_GRAPHIC,
    TOOL_BUY_AMOUNT,
    BuyTool,
)
from .skills.smelt import INGOT_GRAPHICS

_PROFESSION = "blacksmith"
_CAPABILITY = "buy_smith_tool"
# Provenance-clean starting gold: above the 1 x 13 == 13 gold a tongs costs on
# this shard, so the buy is affordable and the exact post-buy balance
# (100 - quoted_cost) is a decisive delta check.
_STARTING_GOLD = 100
# Enough iron that buy_ingots is NOT ready (its reorder point is 15), so tool
# replacement is the only acquisition the cognition can choose.
_STAGED_IRON = 20


@dataclass(frozen=True)
class _ActionRecord:
    action: Action
    observation: Observation


class _RecordingBody:
    def __init__(self, inner: ResilientIpcBody) -> None:
        self.inner = inner
        self.last_obs: Observation | None = None
        self.actions: list[_ActionRecord] = []

    @property
    def connected(self) -> bool:
        return self.inner.connected

    def observe(self) -> Observation:
        self.last_obs = self.inner.observe()
        return self.last_obs

    def act(self, action: Action) -> None:
        assert self.last_obs is not None
        self.actions.append(_ActionRecord(action, self.last_obs))
        self.inner.act(action)


@dataclass(frozen=True)
class _SelectionRecord:
    skill: Skill
    goal: Goal | None
    goal_id: int | None
    action_offset: int


class _SelectionTracer:
    def __init__(self, body: _RecordingBody) -> None:
        self.body = body
        self.selections: list[_SelectionRecord] = []

    def __call__(self, skill: Skill, ctx: SkillContext) -> None:
        self.selections.append(
            _SelectionRecord(skill, ctx.goal, ctx.goal_id, len(self.body.actions))
        )


class _SequencedClient:
    def __init__(self) -> None:
        self.responses = [
            '{"schema":1,"decision":"capability","capability":"buy_smith_tool",'
            '"action":"buy"}',
            '{"schema":1,"decision":"capability","capability":"buy_smith_tool"}',
            '{"schema":1,"decision":"idle"}',
        ]
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def _backpack(obs: Observation) -> int | None:
    item = next(
        (
            item
            for item in obs.items
            if item.layer == BACKPACK_LAYER and item.container == obs.player.serial
        ),
        None,
    )
    return item.serial if item is not None else None


def _pack_items(obs: Observation, graphic: int):
    backpack = _backpack(obs)
    if backpack is None:
        return []
    return [
        item
        for item in obs.items
        if item.graphic == graphic and item.container == backpack
    ]


def _pack_amount(obs: Observation, graphic: int) -> int:
    return sum(item.amount for item in _pack_items(obs, graphic))


def _pack_iron(obs: Observation) -> int:
    backpack = _backpack(obs)
    if backpack is None:
        return 0
    return sum(
        item.amount
        for item in obs.items
        if item.graphic in INGOT_GRAPHICS and item.container == backpack
    )


def _pack_tools(obs: Observation) -> int:
    """COUNT of smith tools in the pack — each tool is a distinct, non-stacking
    item, so a bought tool is a 0->1 count delta, not an amount sum.
    """
    backpack = _backpack(obs)
    if backpack is None:
        return 0
    return sum(
        1
        for item in obs.items
        if item.graphic in SMITH_TOOL_GRAPHICS and item.container == backpack
    )


def _buy_select(record: _ActionRecord, vendor: int) -> bool:
    action = record.action
    popup = record.observation.popup
    return bool(
        isinstance(action, PopupSelect)
        and action.serial == vendor
        and popup is not None
        and popup.serial == vendor
        and any(
            entry.index == action.index and entry.cliloc == BUY_CLILOC
            for entry in popup.entries
        )
    )


def _run(args: argparse.Namespace) -> tuple[dict[str, bool], str]:
    account = args.account or f"animab8t{args.suffix}"
    password = args.password or account
    smith_x, smith_y = TRADE_SMITH_SPOT
    vendor_x, vendor_y = VENDOR_SPOT[-1]

    with ResilientIpcBody.spawn(
        args.host,
        args.port,
        account,
        password,
        bridge=args.bridge,
        pump_ms=args.pump_ms,
        response_timeout_s=args.response_timeout,
    ) as ipc:
        serial = ipc.ready["player"]["serial"]
        login_throttle(GM_RELOGIN_COOLDOWN_S)
        with GmControl.spawn(args.host, args.port, bridge=args.bridge) as gm:
            gm.hide()
            wipe_area(gm, smith_x, smith_y, radius=10, z=20)
            # Deliberately stage NO smith tool (omit `items`) — a broken/absent
            # tool is precisely the buy_smith_tool trigger.
            gx, gy, gz = gm.stage(
                serial,
                smith_x,
                smith_y,
                skills={"Blacksmith": 35},
            )
            staged = [ipc.observe() for _ in range(3)][-1]
            # Provenance-clean pack: a fresh ServUO character spawns with ~1000
            # starter gold and a starter dagger (plus harmless clutter), so delete
            # all pack gold and daggers FIRST — exactly like `live_buy_goal.py` —
            # THEN add the iron (so buy_ingots stays unready, ingots >= 15) and the
            # known gold balance. Deliberately do NOT delete smith tools: a fresh
            # char has none, and `live_baseline_no_tool_known_gold` (tools == 0)
            # must fail LOUD on an unexpected tool rather than silently mask it.
            # (Iron is never deleted — it is only added, after the deletions.)
            removed = all(
                gm.command_on("[Delete", item.serial)
                for graphic in (GOLD_GRAPHIC, DAGGER_GRAPHIC)
                for item in _pack_items(staged, graphic)
            )
            added = bool(
                gm.command_on(f"[AddToPack IronIngot {_STAGED_IRON}", serial)
                and gm.command_on(f"[AddToPack Gold {_STARTING_GOLD}", serial)
            )
            vendor = gm.stage_npc(
                "Blacksmith", vendor_x, vendor_y, gz, exclude=serial
            )
            print(
                f"GM staged B8 tool-buy smith; subject=0x{serial:X} pos=({gx},{gy},{gz}) "
                f"gold={_STARTING_GOLD} iron={_STAGED_IRON} tools=0 daggers=0 "
                f"vendor={getattr(vendor, 'serial', None)}; closing GM"
            )

        gm_closed = not gm.body.connected
        body = _RecordingBody(ipc)
        for _ in range(4):
            body.observe()
        assert body.last_obs is not None
        baseline = body.last_obs
        vendor_serial = vendor.serial if vendor is not None else None

        valid_goal = capability_goal(_PROFESSION, _CAPABILITY)
        client = _SequencedClient()
        cognition = ThreadedCognition(CapabilityCognition(client, _PROFESSION))
        planner = PROFESSIONS[_PROFESSION].planner(capability_goals=True)
        tracer = _SelectionTracer(body)
        planner.selection_observer = tracer
        agent = Agent(
            body=body,
            persona=Persona(name="Tormund"),
            planner=planner,
            cognition=cognition,
            cognition_interval=1,
            profession=_PROFESSION,
            goal_policy=CapabilityPolicy(_PROFESSION),
        )
        # A vendor route but deliberately no banker_spot or craft_spot, so bank,
        # craft, sell, and buy_ingots (already has 15+ ingots) are all unready —
        # only buy_smith_tool can be selected.
        agent.memory["vendor_spot"] = VENDOR_SPOT

        negative_offset = len(body.actions)
        agent.tick()
        if not cognition.wait_idle(timeout=5.0):
            raise RuntimeError("invalid capability reply did not finish")
        agent.tick()
        negative_actions = body.actions[negative_offset:]
        invalid_rejected = agent.goal_stack.current is None
        if not cognition.wait_idle(timeout=5.0):
            raise RuntimeError("valid capability reply did not finish")

        target_goal_id: int | None = None
        target_offset: int | None = None
        completed_at: int | None = None
        for tick in range(args.ticks):
            agent.tick()
            for selection in tracer.selections:
                if selection.goal == valid_goal and selection.goal_id is not None:
                    target_goal_id = selection.goal_id
                    target_offset = selection.action_offset
                    break
            terminal = [
                frame
                for frame in agent.goal_stack.history
                if frame.id == target_goal_id and frame.outcome is GoalOutcome.SUCCESS
            ]
            if terminal:
                completed_at = tick if completed_at is None else completed_at
                if tick >= completed_at + args.settle_ticks:
                    break
            if tick % args.snapshot_every == 0:
                assert body.last_obs is not None
                print(
                    f"tick={tick:03d} tools={_pack_tools(body.last_obs)} "
                    f"gold={_pack_amount(body.last_obs, GOLD_GRAPHIC)} "
                    f"phase={agent.memory.get('mkt_phase', 'craft')!r}"
                )

        assert body.last_obs is not None
        final_obs = body.last_obs
        actions = body.actions[target_offset if target_offset is not None else len(body.actions) :]
        requests = [
            i
            for i, record in enumerate(actions)
            if isinstance(record.action, PopupRequest)
            and record.action.serial == vendor_serial
        ]
        selects = [
            i
            for i, record in enumerate(actions)
            if vendor_serial is not None and _buy_select(record, vendor_serial)
        ]
        buys = [
            (i, record)
            for i, record in enumerate(actions)
            if isinstance(record.action, BuyItems)
            and record.action.vendor == vendor_serial
        ]
        bought = sum(
            amount
            for _index, record in buys
            for _item_serial, amount in record.action.items
        )
        quoted_cost = sum(
            entry.price * amount
            for _index, record in buys
            for item_serial, amount in record.action.items
            for entry in (record.observation.shop_buy.entries if record.observation.shop_buy else [])
            if entry.serial == item_serial and entry.graphic == SMITH_TONGS_GRAPHIC
        )
        exact_order = bool(
            requests
            and selects
            and buys
            and requests[0] < selects[0] < buys[0][0]
        )
        buy_record = buys[0][1] if len(buys) == 1 else None
        buy_shop = buy_record.observation.shop_buy if buy_record is not None else None
        tongs_entry = (
            next(
                (entry for entry in buy_shop.entries if entry.graphic == SMITH_TONGS_GRAPHIC),
                None,
            )
            if buy_shop is not None
            else None
        )
        only_tongs_bought = bool(
            buy_record is not None
            and buy_shop is not None
            and buy_shop.vendor == vendor_serial
            and tongs_entry is not None
            and buy_record.action.items
            == [(tongs_entry.serial, min(TOOL_BUY_AMOUNT, tongs_entry.amount))]
        )
        target_frames = [
            frame
            for frame in (*agent.goal_stack.frames, *agent.goal_stack.history)
            if frame.goal == valid_goal
        ]
        terminals = [
            frame
            for frame in agent.goal_stack.history
            if target_goal_id is not None and frame.id == target_goal_id
        ]
        target_frame = target_frames[0] if len(target_frames) == 1 else None
        canonical_frame = bool(
            target_frame is not None
            and target_frame.goal is not valid_goal
            and target_frame.goal.sealed
            and installed_binding_for_goal(target_frame.goal, _PROFESSION) is not None
            and target_frame.source is GoalSource.COGNITION
            and target_frame.deadline_tick is not None
            and target_frame.deadline_tick - target_frame.created_tick == 180
        )
        transaction_indices = {*requests, *selects, *(i for i, _record in buys)}
        all_transaction_indices = {
            index
            for index, record in enumerate(actions)
            if isinstance(
                record.action,
                (PopupRequest, PopupSelect, SellItems, BuyItems),
            )
        }
        unexpected_actions = [
            record.action
            for index, record in enumerate(actions)
            if index not in transaction_indices
        ]
        owners = []
        for index in sorted(transaction_indices):
            global_index = (target_offset or 0) + index
            matches = [
                selection
                for selection in tracer.selections
                if selection.action_offset == global_index
            ]
            if matches:
                owners.append(matches[-1])
        registry_owned = bool(
            owners
            and len(owners) == len(transaction_indices)
            and all_transaction_indices == transaction_indices
            and all(
                owner.goal_id == target_goal_id
                and isinstance(owner.skill, CapabilityBoundSkill)
                and type(owner.skill.inner) is BuyTool
                for owner in owners
            )
        )

        flags = {
            "schema_ready": ipc.ready.get("schema_version") == SUPPORTED_SCHEMA_VERSION,
            "gm_fixture_staged": bool(removed and added and vendor is not None),
            "gm_connection_closed_before_agent": gm_closed,
            "live_baseline_no_tool_known_gold": bool(
                _pack_tools(baseline) == 0
                and _pack_amount(baseline, DAGGER_GRAPHIC) == 0
                and _pack_iron(baseline) == _STAGED_IRON
                and _pack_amount(baseline, GOLD_GRAPHIC) == _STARTING_GOLD
            ),
            "invalid_reply_rejected_without_action": bool(
                invalid_rejected and not negative_actions
            ),
            "closed_prompt_exposes_only_ready_id": bool(
                len(client.calls) == 3
                and all("buy_smith_tool" in user for _system, user in client.calls[:2])
                and all("bank_gold" not in user for _system, user in client.calls[:2])
                and all("sell_daggers" not in user for _system, user in client.calls[:2])
                and all("buy_ingots" not in user for _system, user in client.calls[:2])
                and all("BuyTool" not in user for _system, user in client.calls[:2])
            ),
            "canonical_goal_enqueued_once": bool(
                target_goal_id is not None and len(target_frames) == 1 and canonical_frame
            ),
            "exact_popup_select_buy_order": exact_order,
            "transaction_actions_once": bool(
                len(requests) == len(selects) == len(buys) == 1
            ),
            "only_tongs_bought": only_tongs_bought,
            "registry_owned_transaction": registry_owned,
            "no_extra_capability_actions": not unexpected_actions,
            "live_toolbuy_delta_matches_quote": bool(
                quoted_cost > 0
                and bought > 0
                and _pack_tools(final_obs) == bought
                and _pack_amount(final_obs, GOLD_GRAPHIC) == _STARTING_GOLD - quoted_cost
            ),
            "transaction_returned_idle": bool(
                agent.memory.get("mkt_phase", "craft") == "craft"
                and all(
                    key not in agent.memory
                    for key in (
                        "toolbuy_leg",
                        "toolbuy_stage",
                        "toolbuy_vendor",
                        "toolbuy_popup_wait",
                        "toolbuy_ask_wait",
                        "toolbuy_confirm_wait",
                        "toolbuy_return_leg",
                    )
                )
            ),
            "exact_goal_frame_succeeded_once": bool(
                len(terminals) == 1 and terminals[0].outcome is GoalOutcome.SUCCESS
            ),
            "same_goal_not_replayed": len(target_frames) == 1,
        }
        detail = (
            f"serial={serial} vendor={vendor_serial} tools=0->"
            f"{_pack_tools(final_obs)} gold={_STARTING_GOLD}->"
            f"{_pack_amount(final_obs, GOLD_GRAPHIC)} quote={quoted_cost} bought={bought} "
            f"goal_id={target_goal_id} ticks={agent.ticks} cognition={len(client.calls)}"
        )
        return flags, detail


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2594)
    parser.add_argument("--bridge", default=None)
    parser.add_argument("--suffix", default=fresh_suffix())
    parser.add_argument("--account", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--pump-ms", type=int, default=400)
    parser.add_argument("--response-timeout", type=float, default=8.0)
    parser.add_argument("--ticks", type=int, default=120)
    parser.add_argument("--settle-ticks", type=int, default=5)
    parser.add_argument("--snapshot-every", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        flags, detail = _run(args)
    except Exception as exc:
        print(f"[FLAG] B8 TOOL BUY GOAL GATE FAILED: {type(exc).__name__}: {exc}")
        return 1
    return 0 if print_gate_verdict(flags, label="B8 TOOL BUY GOAL GATE", detail=detail) else 1


if __name__ == "__main__":
    raise SystemExit(main())
