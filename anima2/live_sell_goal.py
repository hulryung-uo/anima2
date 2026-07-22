"""Live B5 gate: closed cognition sells exact staged daggers and nothing else.

The GM fixture is closed before the production Agent exists.  The model client
first returns a schema-invalid request, then the exact ``sell_daggers`` id.
Passing requires the live packet sequence PopupRequest -> PopupSelect(Sell) ->
SellItems, the staged dagger serials disappearing, the quoted gold arriving,
one sealed cognition frame succeeding, and no craft/bank action being emitted.

Usage::

    python -m anima2.live_sell_goal --suffix b5manual
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
from .ipc_body import ResilientIpcBody
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
from .skills.market import GOLD_GRAPHIC, SELL_CLILOC, SellDaggers

_PROFESSION = "blacksmith"
_CAPABILITY = "sell_daggers"


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
            '{"schema":1,"decision":"capability","capability":"sell_daggers",'
            '"action":"sell"}',
            '{"schema":1,"decision":"capability","capability":"sell_daggers"}',
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


def _sell_select(record: _ActionRecord, vendor: int) -> bool:
    action = record.action
    popup = record.observation.popup
    return bool(
        isinstance(action, PopupSelect)
        and action.serial == vendor
        and popup is not None
        and popup.serial == vendor
        and any(
            entry.index == action.index and entry.cliloc == SELL_CLILOC
            for entry in popup.entries
        )
    )


def _run(args: argparse.Namespace) -> tuple[dict[str, bool], str]:
    account = args.account or f"animab5{args.suffix}"
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
            gx, gy, gz = gm.stage(
                serial,
                smith_x,
                smith_y,
                skills={"Blacksmith": 35},
                items=["SmithHammer 999"],
            )
            staged = [ipc.observe() for _ in range(3)][-1]
            removed = all(
                gm.command_on("[Delete", item.serial)
                for graphic in (GOLD_GRAPHIC, DAGGER_GRAPHIC)
                for item in _pack_items(staged, graphic)
            )
            added = all(
                gm.command_on("[AddToPack Dagger", serial) for _ in range(5)
            )
            vendor = gm.stage_npc(
                "Blacksmith", vendor_x, vendor_y, gz, exclude=serial
            )
            print(
                f"GM staged B5 smith; subject=0x{serial:X} pos=({gx},{gy},{gz}) "
                f"daggers=5 vendor={getattr(vendor, 'serial', None)}; closing GM"
            )

        gm_closed = not gm.body.connected
        body = _RecordingBody(ipc)
        for _ in range(4):
            body.observe()
        assert body.last_obs is not None
        baseline = body.last_obs
        baseline_daggers = _pack_items(baseline, DAGGER_GRAPHIC)
        baseline_serials = {item.serial for item in baseline_daggers}
        baseline_tools = [
            item
            for graphic in SMITH_TOOL_GRAPHICS
            for item in _pack_items(baseline, graphic)
        ]
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
                    f"tick={tick:03d} daggers={_pack_amount(body.last_obs, DAGGER_GRAPHIC)} "
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
            if vendor_serial is not None and _sell_select(record, vendor_serial)
        ]
        offers = [
            (i, record)
            for i, record in enumerate(actions)
            if isinstance(record.action, SellItems)
            and record.action.vendor == vendor_serial
        ]
        offered_serials = {
            item_serial
            for _index, record in offers
            for item_serial, _amount in record.action.items
        }
        quoted_gold = sum(
            item.price * amount
            for _index, record in offers
            for item_serial, amount in record.action.items
            for item in (record.observation.shop_sell.items if record.observation.shop_sell else [])
            if item.serial == item_serial and item.graphic == DAGGER_GRAPHIC
        )
        exact_order = bool(
            requests
            and selects
            and offers
            and requests[0] < selects[0] < offers[0][0]
        )
        exact_offer = bool(
            len(offers) == 1
            and offers[0][1].observation.shop_sell is not None
            and offers[0][1].observation.shop_sell.vendor == vendor_serial
            and offers[0][1].action.items
            == [
                (item.serial, item.amount)
                for item in offers[0][1].observation.shop_sell.items
                if item.graphic == DAGGER_GRAPHIC
            ]
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
        transaction_indices = {*requests, *selects, *(i for i, _record in offers)}
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
                and type(owner.skill.inner) is SellDaggers
                for owner in owners
            )
        )

        flags = {
            "schema_v8_ready": ipc.ready.get("schema_version") == 8,
            "gm_fixture_staged": bool(removed and added and vendor is not None),
            "gm_connection_closed_before_agent": gm_closed,
            "live_baseline_exact_daggers_and_zero_gold": bool(
                len(baseline_serials) == 5
                and _pack_amount(baseline, DAGGER_GRAPHIC) == 5
                and _pack_amount(baseline, GOLD_GRAPHIC) == 0
                and baseline_tools
            ),
            "invalid_reply_rejected_without_action": bool(
                invalid_rejected and not negative_actions
            ),
            "closed_prompt_exposes_only_ready_id": bool(
                len(client.calls) == 3
                and all("sell_daggers" in user for _system, user in client.calls[:2])
                and all("bank_gold" not in user for _system, user in client.calls[:2])
                and all("SellDaggers" not in user for _system, user in client.calls[:2])
            ),
            "canonical_goal_enqueued_once": bool(
                target_goal_id is not None and len(target_frames) == 1 and canonical_frame
            ),
            "exact_popup_select_sell_order": exact_order,
            "transaction_actions_once": bool(
                len(requests) == len(selects) == len(offers) == 1
            ),
            "only_staged_daggers_offered": bool(
                exact_offer and offered_serials == baseline_serials
            ),
            "registry_owned_transaction": registry_owned,
            "no_extra_capability_actions": not unexpected_actions,
            "live_sale_delta_matches_quote": bool(
                quoted_gold > 0
                and _pack_amount(final_obs, DAGGER_GRAPHIC) == 0
                and _pack_amount(final_obs, GOLD_GRAPHIC) == quoted_gold
            ),
            "transaction_returned_idle": bool(
                agent.memory.get("mkt_phase", "craft") == "craft"
                and all(
                    key not in agent.memory
                    for key in (
                        "sell_leg",
                        "sell_stage",
                        "sell_vendor",
                        "sell_popup_wait",
                        "sell_ask_wait",
                        "sell_confirm_wait",
                        "sell_return_leg",
                    )
                )
            ),
            "exact_goal_frame_succeeded_once": bool(
                len(terminals) == 1 and terminals[0].outcome is GoalOutcome.SUCCESS
            ),
            "same_goal_not_replayed": len(target_frames) == 1,
        }
        detail = (
            f"serial={serial} vendor={vendor_serial} daggers=5->"
            f"{_pack_amount(final_obs, DAGGER_GRAPHIC)} gold=0->"
            f"{_pack_amount(final_obs, GOLD_GRAPHIC)} quote={quoted_gold} "
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
        print(f"[FLAG] B5 SELL GOAL GATE FAILED: {type(exc).__name__}: {exc}")
        return 1
    return 0 if print_gate_verdict(flags, label="B5 SELL GOAL GATE", detail=detail) else 1


if __name__ == "__main__":
    raise SystemExit(main())
