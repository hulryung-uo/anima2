"""Live B1 gate: durable nested goals, deadlines, and cognition isolation.

The subject is staged on the calibrated open ``HUNTING_SPOT`` while a GM
connection exists.  That connection is closed before the real
``Agent(Planner([GoTo(), Wander()]))`` is created or ticked.  The gate then
proves, from live observations and emitted actions, that:

* a moving base ``goto`` can be interrupted only after its native route is
  explicitly stopped;
* a real child ``goto`` completes and resumes the exact suspended parent frame
  with its accumulated progress intact;
* a bounded child expires exactly once, stops its route, and resumes that same
  parent again; and
* synchronous cognition is called throughout but neither ``None`` nor an
  adversarial replacement ``goto`` can overwrite active/suspended work.

Requires a running local ServUO shard and the ``anima-agent`` bridge.

Usage::

    python -m anima2.live_goal_stack --suffix b1manual
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from .agent import Agent
from .contract import Action, Observation, Position, WalkTo
from .control import GmControl
from .goals import GoalFrame, GoalOutcome, GoalSource
from .ipc_body import IpcBody, ResilientIpcBody, SUPPORTED_SCHEMA_VERSION
from .live_common import (
    GM_RELOGIN_COOLDOWN_S,
    fresh_suffix,
    login_throttle,
    print_gate_verdict,
    wipe_area,
)
from .persona import Persona
from .planner import Planner
from .profession import HUNTING_SPOT, NAV_START
from .skills import GoTo, Wander
from .skills.base import Goal, SkillContext


@dataclass(frozen=True)
class _ActionRecord:
    action: Action
    pos: Position


class _RecordingBody:
    """Cache the exact observation against which every action was emitted."""

    def __init__(self, inner: IpcBody | ResilientIpcBody) -> None:
        self.inner = inner
        self.last_obs: Observation | None = None
        self.observations: list[Observation] = []
        self.actions: list[_ActionRecord] = []

    @property
    def connected(self) -> bool:
        return self.inner.connected

    def observe(self) -> Observation:
        self.last_obs = self.inner.observe()
        self.observations.append(self.last_obs)
        return self.last_obs

    def act(self, action: Action) -> None:
        assert self.last_obs is not None
        self.actions.append(_ActionRecord(action=action, pos=self.last_obs.player.pos))
        self.inner.act(action)


class _AdversarialCognition:
    """Alternate a clear proposal and an unrelated replacement proposal."""

    def __init__(self, replacement: Goal) -> None:
        self.replacement = replacement
        self.calls = 0
        self.none_calls = 0
        self.replacement_calls = 0

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        self.calls += 1
        if self.calls % 2:
            self.none_calls += 1
            return None
        self.replacement_calls += 1
        return self.replacement


def _xy(pos: Position) -> tuple[int, int]:
    return pos.x, pos.y


def _walks_to(action: Action | None, target: Position) -> bool:
    return isinstance(action, WalkTo) and (action.x, action.y) == (target.x, target.y)


def _is_route_stop(record: _ActionRecord | None) -> bool:
    return bool(
        record is not None
        and isinstance(record.action, WalkTo)
        and (record.action.x, record.action.y) == _xy(record.pos)
    )


def _terminal_matches(
    frame: GoalFrame,
    history: tuple[GoalFrame, ...],
    outcome: GoalOutcome,
) -> bool:
    matches = [entry for entry in history if entry.id == frame.id]
    return len(matches) == 1 and matches[0] is frame and frame.outcome is outcome


def _trace_tick(label: str, tick: int, agent: Agent, body: _RecordingBody) -> None:
    assert body.last_obs is not None
    frame = agent.goal_stack.current
    progress = frame.progress if frame is not None else None
    print(
        f"[{label}] tick={tick:03d} agent_tick={agent.ticks:03d} "
        f"pos={_xy(body.last_obs.player.pos)} "
        f"goal_id={frame.id if frame else None} "
        f"progress={progress.value if progress else None} "
        f"evidence={progress.evidence_count if progress else None}"
    )


def _run(args: argparse.Namespace) -> tuple[dict[str, bool], str]:
    account = args.account or f"animab1{args.suffix}"
    password = args.password or account
    hunting_x, hunting_y = HUNTING_SPOT

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
        body = _RecordingBody(ipc)
        login_throttle(GM_RELOGIN_COOLDOWN_S)

        with GmControl.spawn(args.host, args.port, bridge=args.bridge) as gm:
            gm.hide()
            wipe_area(gm, hunting_x, hunting_y, radius=8, z=15)
            staged = gm.stage(serial, hunting_x, hunting_y)
            print(f"GM staged goal-stack subject; subject=0x{serial:X} staged={staged}; closing GM")

        # Trust boundary: every Agent construction/tick and all goal mutation
        # happens below this point, after the sole control-plane connection is
        # closed.  Warm observations only let the teleport settle.
        gm_closed_before_agent = not gm.body.connected
        for _ in range(3):
            body.observe()
        assert body.last_obs is not None
        start = body.last_obs.player.pos

        # The 36-tile HUNTING_SPOT -> NAV_START course is the same live-
        # calibrated A* differential used by live_navigate.  It leaves enough
        # time to interrupt after real movement.  The child stays in the known
        # open HUNTING_SPOT pocket; the deadline child reuses the long target so
        # a two-tick budget cannot vacuously arrive before expiry.
        base_target = Position(*NAV_START, start.z)
        child_target = Position(start.x + 3, start.y, start.z)
        deadline_target = base_target
        foreign_goal = Goal(
            kind="goto",
            params={"target": Position(start.x + 99, start.y + 99, start.z)},
        )
        cognition = _AdversarialCognition(foreign_goal)
        base_goal = Goal(kind="goto", params={"target": base_target})
        agent = Agent(
            body=body,
            persona=Persona(name="B1 Goal Stack Probe"),
            planner=Planner([GoTo(), Wander()]),
            cognition=cognition,
            cognition_interval=1,
            goal=base_goal,
        )
        base_frame = agent.goal_stack.current
        assert base_frame is not None

        foreign_never_owned_stack = True
        base_walk_issued = False
        base_moved_before_interrupt = False
        base_progress_observed = False
        base_start_ticks = 0
        for tick in range(args.base_start_ticks):
            action = agent.tick()
            base_start_ticks = tick + 1
            assert body.last_obs is not None
            foreign_never_owned_stack = foreign_never_owned_stack and all(
                frame.goal is not foreign_goal for frame in agent.goal_stack.frames
            )
            base_walk_issued = base_walk_issued or _walks_to(action, base_target)
            base_moved_before_interrupt = base_moved_before_interrupt or (
                _xy(body.last_obs.player.pos) != _xy(start)
            )
            base_progress_observed = base_progress_observed or (
                base_frame.progress.evidence_count > 0 and base_frame.progress.value > 0.0
            )
            if tick % args.snapshot_every == 0:
                _trace_tick("base-start", tick, agent, body)
            if (
                agent.goal_stack.current is base_frame
                and base_walk_issued
                and base_moved_before_interrupt
                and base_progress_observed
                and _xy(body.last_obs.player.pos) != _xy(base_target)
            ):
                break

        base_live_before_child = agent.goal_stack.current is base_frame
        base_progress_before_child = base_frame.progress
        child_goal = Goal(kind="goto", params={"target": child_target})
        child_action_offset = len(body.actions)
        child_frame = agent.interrupt_goal(child_goal, source=GoalSource.SYSTEM)

        child_walk_issued = False
        child_moved = False
        child_issue_pos: tuple[int, int] | None = None
        parent_frozen_during_child = True
        child_ticks = 0
        for tick in range(args.child_ticks):
            action = agent.tick()
            child_ticks = tick + 1
            assert body.last_obs is not None
            foreign_never_owned_stack = foreign_never_owned_stack and all(
                frame.goal is not foreign_goal for frame in agent.goal_stack.frames
            )
            if _walks_to(action, child_target):
                child_walk_issued = True
                child_issue_pos = _xy(body.last_obs.player.pos)
            if child_issue_pos is not None and _xy(body.last_obs.player.pos) != child_issue_pos:
                child_moved = True
            parent_frozen_during_child = parent_frozen_during_child and (
                base_frame.progress is base_progress_before_child
            )
            if tick % args.snapshot_every == 0:
                _trace_tick("child", tick, agent, body)
            if agent.goal_stack.current is base_frame:
                break

        child_records = body.actions[child_action_offset:]
        child_stop_first = bool(child_records) and _is_route_stop(child_records[0])
        child_completed_once = _terminal_matches(
            child_frame, agent.goal_stack.history, GoalOutcome.SUCCESS
        )
        exact_parent_after_child = agent.goal_stack.current is base_frame
        exact_parent_progress_after_child = base_frame.progress is base_progress_before_child

        # A conservative transition may spend one tick stopping the child's
        # now-arrived native route.  The parent must then reissue its own exact
        # target within a small bounded window.
        parent_reissued_after_child = False
        parent_resume_ticks = 0
        for tick in range(args.parent_resume_ticks):
            parent_resume_action = agent.tick()
            parent_resume_ticks = tick + 1
            foreign_never_owned_stack = foreign_never_owned_stack and all(
                frame.goal is not foreign_goal for frame in agent.goal_stack.frames
            )
            parent_reissued_after_child = parent_reissued_after_child or _walks_to(
                parent_resume_action, base_target
            )
            if parent_reissued_after_child:
                break
        parent_progress_continuous = (
            agent.goal_stack.current is base_frame
            and base_frame.progress.initial_distance == base_progress_before_child.initial_distance
            and base_frame.progress.evidence_count >= base_progress_before_child.evidence_count
            and base_frame.progress.value >= base_progress_before_child.value
        )

        base_progress_before_deadline = base_frame.progress
        deadline_goal = Goal(kind="goto", params={"target": deadline_target})
        deadline_action_offset = len(body.actions)
        deadline_frame = agent.interrupt_goal(
            deadline_goal,
            source=GoalSource.SYSTEM,
            deadline_ticks=args.child_deadline_ticks,
        )
        deadline_walk_issued = False
        parent_frozen_during_deadline = True
        deadline_ticks = 0
        for tick in range(args.deadline_phase_ticks):
            action = agent.tick()
            deadline_ticks = tick + 1
            foreign_never_owned_stack = foreign_never_owned_stack and all(
                frame.goal is not foreign_goal for frame in agent.goal_stack.frames
            )
            deadline_walk_issued = deadline_walk_issued or _walks_to(action, deadline_target)
            parent_frozen_during_deadline = parent_frozen_during_deadline and (
                base_frame.progress is base_progress_before_deadline
            )
            if tick % args.snapshot_every == 0:
                _trace_tick("deadline-child", tick, agent, body)
            if deadline_frame.terminal:
                break

        deadline_records = body.actions[deadline_action_offset:]
        deadline_stop_first = bool(deadline_records) and _is_route_stop(deadline_records[0])
        deadline_expired_once = _terminal_matches(
            deadline_frame, agent.goal_stack.history, GoalOutcome.EXPIRED
        )
        expiry_record = deadline_records[-1] if deadline_expired_once and deadline_records else None
        expired_route_stopped = _is_route_stop(expiry_record)
        exact_parent_after_expiry = agent.goal_stack.current is base_frame
        exact_parent_progress_after_expiry = base_frame.progress is base_progress_before_deadline

        assert body.last_obs is not None
        finish_start_pos = _xy(body.last_obs.player.pos)
        base_reissued_after_expiry = False
        base_moved_after_expiry = False
        base_finish_ticks = 0
        for tick in range(args.base_finish_ticks):
            action = agent.tick()
            base_finish_ticks = tick + 1
            assert body.last_obs is not None
            foreign_never_owned_stack = foreign_never_owned_stack and all(
                frame.goal is not foreign_goal for frame in agent.goal_stack.frames
            )
            base_reissued_after_expiry = base_reissued_after_expiry or _walks_to(
                action, base_target
            )
            base_moved_after_expiry = base_moved_after_expiry or (
                _xy(body.last_obs.player.pos) != finish_start_pos
            )
            if tick % args.snapshot_every == 0 or base_frame.terminal:
                _trace_tick("base-finish", tick, agent, body)
            if base_frame.terminal:
                break

        history = agent.goal_stack.history
        base_completed_once = _terminal_matches(base_frame, history, GoalOutcome.SUCCESS)
        foreign_never_owned_stack = foreign_never_owned_stack and all(
            frame.goal is not foreign_goal for frame in history
        )
        overwrite_rejections = int(agent.memory.get("cognition_overwrite_rejections", 0))

        if not base_completed_once:
            print(
                "[TRACE] actions="
                + repr(
                    [
                        (
                            type(record.action).__name__,
                            _xy(record.pos),
                            getattr(record.action, "x", None),
                            getattr(record.action, "y", None),
                        )
                        for record in body.actions
                    ]
                )
            )
            print(f"[TRACE] memory={agent.memory}")

        flags = {
            "schema_ready": ipc.ready.get("schema_version") == SUPPORTED_SCHEMA_VERSION,
            "gm_connection_closed_before_agent": gm_closed_before_agent,
            "base_exact_walkto_issued": base_walk_issued,
            "base_real_movement_and_progress_observed": (
                base_moved_before_interrupt and base_progress_observed
            ),
            "base_live_when_interrupted": base_live_before_child,
            "explicit_child_stops_old_route_first": child_stop_first,
            "explicit_child_exact_walkto_and_real_movement": (child_walk_issued and child_moved),
            "explicit_child_succeeds_exactly_once": child_completed_once,
            "parent_frame_and_progress_frozen_while_child_active": (
                parent_frozen_during_child
                and exact_parent_after_child
                and exact_parent_progress_after_child
            ),
            "same_parent_resumes_with_continuous_progress": (
                parent_reissued_after_child and parent_progress_continuous
            ),
            "deadline_child_stops_parent_route_first": deadline_stop_first,
            "deadline_child_became_real_active_route": deadline_walk_issued,
            "deadline_child_expires_exactly_once": deadline_expired_once,
            "expired_child_route_stopped": expired_route_stopped,
            "same_parent_frame_and_progress_resume_after_expiry": (
                parent_frozen_during_deadline
                and exact_parent_after_expiry
                and exact_parent_progress_after_expiry
            ),
            "base_reissued_and_moved_after_expiry": (
                base_reissued_after_expiry and base_moved_after_expiry
            ),
            "base_completes_exactly_once": base_completed_once,
            "sync_cognition_exercised_none_and_replacement": (
                cognition.calls > 0 and cognition.none_calls > 0 and cognition.replacement_calls > 0
            ),
            "cognition_cannot_overwrite_live_stack": (
                foreign_never_owned_stack and overwrite_rejections >= cognition.replacement_calls
            ),
            "no_gm_connection_during_goal_work": gm_closed_before_agent,
        }
        detail = (
            f"serial={serial} start={_xy(start)} base={_xy(base_target)} "
            f"child={_xy(child_target)} deadline={_xy(deadline_target)} "
            f"ticks={base_start_ticks}+{child_ticks}+{parent_resume_ticks}+"
            f"{deadline_ticks}+{base_finish_ticks} "
            f"cognition={cognition.calls} rejects={overwrite_rejections} "
            f"history={[(frame.id, frame.outcome.value if frame.outcome else None) for frame in history]}"
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
    parser.add_argument("--base-start-ticks", type=int, default=30)
    parser.add_argument("--child-ticks", type=int, default=60)
    parser.add_argument("--parent-resume-ticks", type=int, default=8)
    parser.add_argument("--child-deadline-ticks", type=int, default=2)
    parser.add_argument("--deadline-phase-ticks", type=int, default=12)
    parser.add_argument("--base-finish-ticks", type=int, default=250)
    parser.add_argument("--snapshot-every", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        flags, detail = _run(args)
    except Exception as exc:
        print(f"[FLAG] B1 GOAL STACK GATE FAILED: {type(exc).__name__}: {exc}")
        return 1
    return 0 if print_gate_verdict(flags, label="B1 GOAL STACK GATE", detail=detail) else 1


if __name__ == "__main__":
    raise SystemExit(main())
