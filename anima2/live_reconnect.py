"""Live A3 gate: survive an abrupt ``anima-agent`` bridge process crash.

The gate deliberately kills only the subject's bridge child while a real
``GoTo`` route is in flight.  A stable :class:`ResilientIpcBody` must reconnect
the same character without rebuilding the Python ``Agent``.  Before the Agent
is allowed to tick again, the gate independently proves that its goal, memory,
episodic-memory object, and tick counter survived unchanged.  The live action
proof then requires ``GoTo`` to re-issue the same ``WalkTo`` target (the Rust
route died with the old bridge), resume movement, and arrive.

The GM control connection is used only for deterministic staging and is closed
*before* failure injection.  No GM connection is opened during recovery.

Requires a running local ServUO shard and a built ``anima-agent`` bridge.

Usage::

    python -m anima2.live_reconnect --suffix a3manual
"""

from __future__ import annotations

import argparse
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .agent import Agent
from .contract import Action, Observation, Position, WalkTo
from .control import GmControl
from .geometry import chebyshev
from .ipc_body import RestartPolicy, ResilientIpcBody
from .live_common import (
    GM_RELOGIN_COOLDOWN_S,
    RecordingBody,
    fresh_suffix,
    login_throttle,
    print_gate_verdict,
    wipe_bounds,
)
from .persona import Persona
from .planner import Planner
from .profession import NAV_DEST, NAV_START
from .skills.base import Goal
from .skills.movement import GoTo

ARRIVAL_RADIUS = 2


class _ActionRecordingBody(RecordingBody):
    """Cache observations and record the bridge generation for every action."""

    def __init__(self, inner: ResilientIpcBody) -> None:
        super().__init__(inner)
        self.actions: list[tuple[int, Action]] = []

    @property
    def resilient(self) -> ResilientIpcBody:
        return self._inner

    def act(self, action: Action) -> None:
        self.actions.append((self.resilient.generation, action))
        super().act(action)


@dataclass(frozen=True)
class _ContinuitySnapshot:
    """Python-side state that a bridge-only restart must not replace or mutate."""

    agent_id: int
    body_wrapper_id: int
    resilient_body_id: int
    goal_id: int
    goal_value: Goal
    memory_id: int
    memory_value: dict[str, Any]
    episodes_id: int
    ticks: int
    player_serial: int
    bridge_pid: int
    generation: int

    @classmethod
    def capture(
        cls,
        agent: Agent,
        body: _ActionRecordingBody,
        player_serial: int,
    ) -> _ContinuitySnapshot:
        assert agent.goal is not None
        return cls(
            agent_id=id(agent),
            body_wrapper_id=id(body),
            resilient_body_id=id(body.resilient),
            goal_id=id(agent.goal),
            goal_value=deepcopy(agent.goal),
            memory_id=id(agent.memory),
            memory_value=deepcopy(agent.memory),
            episodes_id=id(agent.episodes),
            ticks=agent.ticks,
            player_serial=player_serial,
            bridge_pid=body.resilient.current_pid,
            generation=body.resilient.generation,
        )

    def flags(
        self,
        agent: Agent,
        body: _ActionRecordingBody,
        observation: Observation,
    ) -> dict[str, bool]:
        return {
            "new_bridge_pid": body.resilient.current_pid != self.bridge_pid,
            "generation_advanced_once": body.resilient.generation == self.generation + 1,
            "same_character_serial": observation.player.serial == self.player_serial,
            "same_agent": id(agent) == self.agent_id,
            "same_body_wrapper": id(body) == self.body_wrapper_id and agent.body is body,
            "same_resilient_body": id(body.resilient) == self.resilient_body_id,
            "goal_identity_and_value_preserved": (
                agent.goal is not None
                and id(agent.goal) == self.goal_id
                and agent.goal == self.goal_value
            ),
            "memory_identity_preserved": id(agent.memory) == self.memory_id,
            "memory_snapshot_preserved": agent.memory == self.memory_value,
            "episodes_identity_preserved": id(agent.episodes) == self.episodes_id,
            "ticks_unchanged_during_reconnect": agent.ticks == self.ticks,
        }


def _same_walkto(action: Action | None, target: Position) -> bool:
    return isinstance(action, WalkTo) and (action.x, action.y) == (target.x, target.y)


def _run(args: argparse.Namespace) -> tuple[dict[str, bool], str]:
    account = args.account or f"animaa3{args.suffix}"
    password = args.password or account
    policy = RestartPolicy(
        max_attempts=args.max_restart_attempts,
        initial_backoff_s=args.initial_backoff,
        max_backoff_s=args.max_backoff,
        max_outage_s=args.reconnect_deadline,
        immediate_first=True,
    )

    with ResilientIpcBody.spawn(
        args.host,
        args.port,
        account,
        password,
        bridge=args.bridge,
        pump_ms=args.pump_ms,
        response_timeout_s=args.response_timeout,
        policy=policy,
    ) as resilient:
        serial = resilient.expected_serial
        print(
            f"subject: account={account} serial={serial} "
            f"pid={resilient.current_pid} generation={resilient.generation}"
        )

        # The only control-plane phase.  Closing this context before the Agent
        # starts also makes it impossible for the recovery proof to depend on a
        # GM fixing the character after the bridge crash.
        # The GM account is shared across every live gate and ServUO can retain
        # its previous session longer than an ordinary fresh-account login.
        # Use the project's live-calibrated GM-specific cooldown, not the
        # shorter fresh-subject throttle.
        login_throttle(GM_RELOGIN_COOLDOWN_S)
        sx, sy = NAV_START
        dx, dy = NAV_DEST
        with GmControl.spawn(args.host, args.port, bridge=args.bridge) as gm:
            gm.hide()
            wipe_bounds(
                gm,
                min(sx, dx) - 5,
                min(sy, dy) - 5,
                max(sx, dx) + 5,
                max(sy, dy) + 5,
                20,
            )
            staged_x, staged_y, staged_z = gm.stage(serial, sx, sy)
            print(f"GM staged subject at ({staged_x},{staged_y},{staged_z}); closing GM")

        body = _ActionRecordingBody(resilient)
        for _ in range(3):
            body.observe()
        assert body.last_obs is not None
        start = body.last_obs.player.pos
        target = Position(dx, dy, staged_z)
        goal = Goal(kind="goto", params={"target": target})
        goto = GoTo()
        agent = Agent(
            body=body,
            persona=Persona(name="A3 Reconnect Probe"),
            planner=Planner([goto]),
            goal=goal,
        )
        agent.memory["a3_reconnect_sentinel"] = {
            "account": account,
            "token": f"continuity-{args.suffix}",
            "nested": [1, 2, 3],
        }

        first_walkto: WalkTo | None = None
        moved_before_kill = False
        print("\n--- establish an in-flight GoTo route ---")
        for tick in range(args.pre_kill_ticks):
            action = agent.tick()
            assert body.last_obs is not None
            pos = body.last_obs.player.pos
            if _same_walkto(action, target) and first_walkto is None:
                assert isinstance(action, WalkTo)
                first_walkto = action
            moved_before_kill = (pos.x, pos.y) != (start.x, start.y)
            distance = chebyshev(pos, target)
            if tick % args.snapshot_every == 0 or moved_before_kill:
                print(
                    f"    pre tick={tick:3d} agent_ticks={agent.ticks:3d} "
                    f"pos=({pos.x},{pos.y}) dist={distance:2d} action={type(action).__name__ if action else '-'}"
                )
            if first_walkto is not None and moved_before_kill and distance > args.arrival_radius:
                break

        if first_walkto is None or not moved_before_kill:
            raise RuntimeError(
                "precondition failed: WalkTo did not establish real movement before the kill budget"
            )
        assert body.last_obs is not None
        if chebyshev(body.last_obs.player.pos, target) <= args.arrival_radius:
            raise RuntimeError("precondition failed: subject arrived before failure injection")

        snapshot = _ContinuitySnapshot.capture(agent, body, serial)
        action_count_before_reconnect = len(body.actions)
        print(
            "\n--- abrupt child-only failure injection at a completed tick boundary ---\n"
            f"    killing bridge pid={snapshot.bridge_pid}; agent_ticks={snapshot.ticks}"
        )
        resilient.abort_current_bridge()
        child_was_killed_and_reaped = not resilient.bridge_connected

        reconnect_started = time.monotonic()
        reconnect_obs = body.observe()  # deliberately outside Agent.tick()
        reconnect_elapsed = time.monotonic() - reconnect_started
        action_count_after_reconnect = len(body.actions)
        reconnect_generation = resilient.generation
        reconnect_pos = reconnect_obs.player.pos
        continuity = snapshot.flags(agent, body, reconnect_obs)
        print(
            f"    reconnected pid={resilient.current_pid} generation={reconnect_generation} "
            f"attempts={resilient.restart_attempts} elapsed={reconnect_elapsed:.3f}s "
            f"pos=({reconnect_pos.x},{reconnect_pos.y})"
        )

        print("\n--- resume the same Agent and the same GoTo ---")
        reissue_tick: int | None = None
        movement_resumed = False
        arrived = False
        ticks_monotonic = True
        generation_stable_after_reconnect = True
        for resumed_tick in range(1, args.resume_ticks + 1):
            action = agent.tick()
            assert body.last_obs is not None
            pos = body.last_obs.player.pos
            ticks_monotonic = ticks_monotonic and agent.ticks == snapshot.ticks + resumed_tick
            generation_stable_after_reconnect = (
                generation_stable_after_reconnect
                and resilient.generation == reconnect_generation
            )
            if (
                reissue_tick is None
                and _same_walkto(action, target)
                and resilient.generation == reconnect_generation
            ):
                reissue_tick = resumed_tick
                print(f"    same-target WalkTo re-issued at resumed tick {resumed_tick}")
            if reissue_tick is not None and (pos.x, pos.y) != (reconnect_pos.x, reconnect_pos.y):
                movement_resumed = True
            distance = chebyshev(pos, target)
            arrived = distance <= args.arrival_radius
            if (
                resumed_tick % args.snapshot_every == 0
                or action is not None
                or arrived
            ):
                print(
                    f"    post tick={resumed_tick:3d} agent_ticks={agent.ticks:3d} "
                    f"pos=({pos.x},{pos.y}) dist={distance:2d} "
                    f"action={type(action).__name__ if action else '-'}"
                )
            if arrived:
                break

        # One changed-position observation immediately after reconnect is
        # possible if the server accepted the old bridge's final route step.
        # GoTo then needs one reset tick plus its six stationary ticks.  The
        # small +3 margin remains tightly coupled to the skill's own bound.
        reissue_bound = goto.walkto_stall_limit + 3
        flags = {
            "pre_kill_walkto_sent": first_walkto is not None,
            "pre_kill_movement_observed": moved_before_kill,
            "old_bridge_sigkilled_and_reaped": child_was_killed_and_reaped,
            **continuity,
            "reconnect_within_deadline": reconnect_elapsed <= args.reconnect_deadline,
            "restart_attempts_bounded": (
                1 <= resilient.restart_attempts <= args.max_restart_attempts
            ),
            "no_action_during_reconnect": (
                action_count_after_reconnect == action_count_before_reconnect
            ),
            "same_target_walkto_reissued": (
                reissue_tick is not None
                and reissue_tick <= reissue_bound
                and first_walkto == WalkTo(x=target.x, y=target.y)
            ),
            "ticks_monotonic_after_resume": ticks_monotonic,
            "movement_resumed_after_reissue": movement_resumed,
            "arrived_after_reconnect": arrived,
            "replacement_generation_remained_stable": generation_stable_after_reconnect,
        }
        final = body.last_obs.player.pos
        detail = (
            f"old_pid={snapshot.bridge_pid} new_pid={resilient.current_pid} "
            f"serial={serial} reconnect={reconnect_elapsed:.3f}s "
            f"reissue_tick={reissue_tick} final=({final.x},{final.y})"
        )
        return flags, detail


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2594)
    parser.add_argument("--bridge", default=None)
    parser.add_argument("--suffix", default=fresh_suffix())
    parser.add_argument("--account", default=None, help="subject account; default is a fresh suffix account")
    parser.add_argument("--password", default=None, help="default: same as the subject account")
    parser.add_argument("--pump-ms", type=int, default=400)
    parser.add_argument("--response-timeout", type=float, default=8.0)
    parser.add_argument("--max-restart-attempts", type=int, default=6)
    parser.add_argument("--initial-backoff", type=float, default=0.25)
    parser.add_argument("--max-backoff", type=float, default=4.0)
    parser.add_argument("--reconnect-deadline", type=float, default=30.0)
    parser.add_argument("--pre-kill-ticks", type=int, default=80)
    parser.add_argument("--resume-ticks", type=int, default=300)
    parser.add_argument("--arrival-radius", type=int, default=ARRIVAL_RADIUS)
    parser.add_argument("--snapshot-every", type=int, default=10)
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        flags, detail = _run(args)
    except Exception as exc:
        print(f"[FLAG] A3 RECONNECT GATE FAILED: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc
    if not print_gate_verdict(flags, label="A3 RECONNECT GATE", detail=detail):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
