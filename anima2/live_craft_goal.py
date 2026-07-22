"""Live B6 gate: closed cognition crafts one exact five-dagger batch.

The GM fixture is removed before the production Agent starts.  A strict model
reply is rejected first; the next reply may name only ``craft_daggers``.
Passing requires exactly 15 owned iron ingots to become five new dagger
serials through the observed ServUO craft-gump replies, followed by an
explicit UI close and one successful sealed capability frame.

Usage::

    python -m anima2.live_craft_goal --suffix b6manual
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from .agent import Agent
from .capabilities import CapabilityPolicy, capability_goal, installed_binding_for_goal
from .capability_cognition import CapabilityCognition
from .cognition import ThreadedCognition
from .contract import Action, GumpResponse, Observation, Use
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
from .profession import PROFESSIONS, TRADE_SMITH_SPOT, CapabilityBoundSkill
from .skills.base import Goal, Skill, SkillContext
from .skills.craft import (
    CATEGORY_BTN,
    CRAFT_TITLE_CLILOC,
    DAGGER_BTN,
    DAGGER_GRAPHIC,
    DAGGER_NAME_CLILOC,
    IRON_RESOURCE_BTN,
    MAKE_LAST_BTN,
    RESOURCE_MENU_BTN,
    SMITH_TOOL_GRAPHICS,
    CraftDaggers,
)
from .skills.harvest import BACKPACK_LAYER
from .skills.market import GOLD_GRAPHIC
from .skills.smelt import INGOT_GRAPHICS

_PROFESSION = "blacksmith"
_CAPABILITY = "craft_daggers"
_BUTTONS = [
    RESOURCE_MENU_BTN,
    IRON_RESOURCE_BTN,
    CATEGORY_BTN,
    DAGGER_BTN,
    MAKE_LAST_BTN,
    MAKE_LAST_BTN,
    MAKE_LAST_BTN,
    MAKE_LAST_BTN,
    0,
]


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
            '{"schema":1,"decision":"capability","capability":"craft_daggers",'
            '"action":"craft"}',
            '{"schema":1,"decision":"capability","capability":"craft_daggers"}',
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


def _pack_items(obs: Observation, graphics: frozenset[int]):
    backpack = _backpack(obs)
    if backpack is None:
        return []
    return [
        item
        for item in obs.items
        if item.graphic in graphics and item.container == backpack
    ]


def _amount(obs: Observation, graphics: frozenset[int]) -> int:
    return sum(item.amount for item in _pack_items(obs, graphics))


def _craft_gump(record: _ActionRecord):
    action = record.action
    if not isinstance(action, GumpResponse):
        return None
    return next(
        (
            gump
            for gump in record.observation.gumps
            if gump.serial == action.serial
            and gump.gump_id == action.gump_id
            and str(CRAFT_TITLE_CLILOC) in gump.layout
        ),
        None,
    )


def _has_reply(gump, button: int) -> bool:
    if button == 0:
        return True
    return any(
        element.get("type") == "button"
        and type(element.get("reply_id")) is int
        and element.get("reply_id") == button
        and type(element.get("pageflag")) is int
        and element.get("pageflag") == 1
        for element in gump.elements
    )


def _run(args: argparse.Namespace) -> tuple[dict[str, bool], str]:
    account = args.account or f"animab6{args.suffix}"
    password = args.password or account
    smith_x, smith_y = TRADE_SMITH_SPOT

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
                skills={"Blacksmith": 50},
                items=[],
            )
            staged = [ipc.observe() for _ in range(3)][-1]
            cleanup_graphics = (
                SMITH_TOOL_GRAPHICS | INGOT_GRAPHICS | {DAGGER_GRAPHIC, GOLD_GRAPHIC}
            )
            removed = all(
                gm.command_on("[Delete", item.serial)
                for item in _pack_items(staged, cleanup_graphics)
            )
            added = bool(
                gm.command_on("[AddToPack SmithHammer 999", serial)
                and gm.command_on("[AddToPack IronIngot 15", serial)
            )
            forge = gm.command_at("[Add Forge", gx, gy - 1, gz)
            anvil = gm.command_at("[Add Anvil", gx, gy + 1, gz)
            print(
                f"GM staged B6 smith; subject=0x{serial:X} pos=({gx},{gy},{gz}) "
                "iron=15 dagger=0; closing GM"
            )

        gm_closed = not gm.body.connected
        body = _RecordingBody(ipc)
        for _ in range(5):
            body.observe()
        assert body.last_obs is not None
        baseline = body.last_obs
        baseline_tools = _pack_items(baseline, SMITH_TOOL_GRAPHICS)
        baseline_daggers = _pack_items(baseline, frozenset({DAGGER_GRAPHIC}))
        start_pos = (baseline.player.pos.x, baseline.player.pos.y)

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
        agent.memory["craft_spot"] = start_pos

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
                    f"tick={tick:03d} iron={_amount(body.last_obs, INGOT_GRAPHICS)} "
                    f"daggers={_amount(body.last_obs, frozenset({DAGGER_GRAPHIC}))} "
                    f"stage={agent.memory.get('cap_craft_stage')!r}"
                )

        assert body.last_obs is not None
        final_obs = body.last_obs
        offset = target_offset if target_offset is not None else len(body.actions)
        actions = body.actions[offset:]
        uses = [record for record in actions if isinstance(record.action, Use)]
        responses = [record for record in actions if isinstance(record.action, GumpResponse)]
        buttons = [record.action.button for record in responses]
        paired_gumps = [_craft_gump(record) for record in responses]
        craft_records = [
            record
            for record in responses
            if record.action.button in {DAGGER_BTN, MAKE_LAST_BTN}
        ]
        inventory_trajectory = [
            (
                _amount(record.observation, INGOT_GRAPHICS),
                _amount(record.observation, frozenset({DAGGER_GRAPHIC})),
            )
            for record in craft_records
        ]
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
            and target_frame.deadline_tick - target_frame.created_tick == 300
        )
        owners = []
        for local_index in range(len(actions)):
            global_index = offset + local_index
            matches = [
                selection
                for selection in tracer.selections
                if selection.action_offset == global_index
            ]
            if matches:
                owners.append(matches[-1])
        registry_owned = bool(
            len(owners) == len(actions)
            and all(
                owner.goal_id == target_goal_id
                and isinstance(owner.skill, CapabilityBoundSkill)
                and type(owner.skill.inner) is CraftDaggers
                for owner in owners
            )
        )
        final_daggers = _pack_items(final_obs, frozenset({DAGGER_GRAPHIC}))
        final_serials = {item.serial for item in final_daggers}
        exact_action_types = bool(
            len(actions) == 10
            and len(uses) == 1
            and uses[0].action.serial == baseline_tools[0].serial
            and len(responses) == 9
            and buttons == _BUTTONS
        )
        exact_gump_provenance = bool(
            len(paired_gumps) == len(responses)
            and all(gump is not None for gump in paired_gumps)
            and all(
                _has_reply(gump, record.action.button)
                for record, gump in zip(responses, paired_gumps, strict=True)
                if gump is not None
            )
            and str(1044022) in paired_gumps[1].layout
            and str(DAGGER_NAME_CLILOC) in paired_gumps[3].layout
        )
        memory_provenance = bool(
            agent.memory.get("cap_craft_goal_id") == target_goal_id
            and agent.memory.get("cap_craft_dagger_button_goal_id") == target_goal_id
            and agent.memory.get("cap_craft_finished_goal_id") == target_goal_id
            and agent.memory.get("cap_craft_returned_goal_id") == target_goal_id
            and agent.memory.get("cap_craft_needed") == 5
            and agent.memory.get("cap_craft_confirmed") == 5
            and sum(
                amount for _serial, amount in agent.memory.get("cap_craft_produced", ())
            ) == 5
            and agent.memory.get("cap_craft_ingots_used") == 15
            and agent.memory.get("cap_craft_failed_attempts") == 0
            and agent.memory.get("cap_craft_failed_ingots") == 0
            and agent.memory.get("cap_craft_failure_costs") == ()
            and agent.memory.get("cap_craft_stage") == "finished"
        )

        flags = {
            "schema_ready": ipc.ready.get("schema_version") == SUPPORTED_SCHEMA_VERSION,
            "gm_fixture_staged": bool(removed and added and forge and anvil),
            "gm_connection_closed_before_agent": gm_closed,
            "live_baseline_exact_owned_resources": bool(
                start_pos == (gx, gy)
                and len(baseline_tools) == 1
                and _amount(baseline, INGOT_GRAPHICS) == 15
                and not baseline_daggers
                and _amount(baseline, frozenset({GOLD_GRAPHIC})) == 0
                and not baseline.gumps
            ),
            "invalid_reply_rejected_without_action": bool(
                invalid_rejected and not negative_actions
            ),
            "closed_prompt_exposes_only_ready_id": bool(
                len(client.calls) >= 2
                and all("craft_daggers" in user for _system, user in client.calls[:2])
                and all("bank_gold" not in user for _system, user in client.calls[:2])
                and all("sell_daggers" not in user for _system, user in client.calls[:2])
                and all("CraftDaggers" not in user for _system, user in client.calls[:2])
            ),
            "canonical_goal_enqueued_once": bool(
                target_goal_id is not None and len(target_frames) == 1 and canonical_frame
            ),
            "exact_owned_tool_and_gump_actions": exact_action_types,
            "every_response_matches_live_reply": exact_gump_provenance,
            "exact_inventory_trajectory": inventory_trajectory
            == [(15, 0), (12, 1), (9, 2), (6, 3), (3, 4)],
            "registry_owned_all_actions": registry_owned,
            "exact_new_dagger_serials_and_iron_delta": bool(
                len(final_serials) == 5
                and all(item.amount == 1 for item in final_daggers)
                and final_serials.isdisjoint({item.serial for item in baseline_daggers})
                and _amount(final_obs, INGOT_GRAPHICS) == 0
            ),
            "terminal_ui_and_position_safe": bool(
                (final_obs.player.pos.x, final_obs.player.pos.y) == start_pos
                and not final_obs.gumps
                and final_obs.pending_target is None
                and final_obs.popup is None
                and final_obs.shop_buy is None
                and final_obs.shop_sell is None
            ),
            "goal_scoped_memory_provenance": memory_provenance,
            "exact_goal_frame_succeeded_once": bool(
                len(terminals) == 1 and terminals[0].outcome is GoalOutcome.SUCCESS
            ),
            "same_goal_not_replayed": len(target_frames) == 1,
        }
        detail = (
            f"serial={serial} iron=15->{_amount(final_obs, INGOT_GRAPHICS)} "
            f"daggers=0->{_amount(final_obs, frozenset({DAGGER_GRAPHIC}))} "
            f"goal_id={target_goal_id} ticks={agent.ticks} actions={len(actions)}"
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
    parser.add_argument("--ticks", type=int, default=180)
    parser.add_argument("--settle-ticks", type=int, default=5)
    parser.add_argument("--snapshot-every", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        flags, detail = _run(args)
    except Exception as exc:
        print(f"[FLAG] B6 CRAFT GOAL GATE FAILED: {type(exc).__name__}: {exc}")
        return 1
    return 0 if print_gate_verdict(flags, label="B6 CRAFT GOAL GATE", detail=detail) else 1


if __name__ == "__main__":
    raise SystemExit(main())
