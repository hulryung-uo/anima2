"""IpcBody must speak the NDJSON protocol correctly (tested via a fake bridge)."""

import subprocess
import sys
import threading
from copy import deepcopy
from pathlib import Path

import pytest
import anima2.ipc_body as ipc_module

from anima2.agent import Agent
from anima2.contract import Observation, PlayerView, Position, Say, Walk
from anima2.ipc_body import (
    IpcBody,
    IpcOwnershipError,
    IpcProtocolError,
    IpcRecoveryExhausted,
    IpcRemoteError,
    IpcTransportError,
    RestartPolicy,
    ResilientIpcBody,
    SUPPORTED_SCHEMA_VERSION,
)
from anima2.persona import Persona
from anima2.planner import Planner
from anima2.skills import Wander
from anima2.skills.base import Goal, Skill, SkillContext, SkillResult, Status

FAKE = Path(__file__).parent / "fake_agent.py"


def spawn_fake() -> IpcBody:
    proc = subprocess.Popen(
        [sys.executable, str(FAKE)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    return IpcBody(proc)


def test_ready_and_observe():
    with spawn_fake() as body:
        assert body.ready["event"] == "ready"
        obs = body.observe()
        assert obs.player.name == "Fake"
        assert obs.player.pos.x == 100


def test_ready_accepts_matching_schema_version():
    with spawn_fake() as body:
        assert body.ready["schema_version"] == SUPPORTED_SCHEMA_VERSION


def test_ready_rejects_incompatible_schema_version():
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            'print("{\\"event\\":\\"ready\\",\\"schema_version\\":999}", flush=True)',
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    with pytest.raises(IpcProtocolError, match="unsupported bridge schema 999"):
        IpcBody(proc)


def test_ready_rejects_missing_schema_version():
    proc = subprocess.Popen(
        [sys.executable, "-c", 'print("{\\"event\\":\\"ready\\"}", flush=True)'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    with pytest.raises(IpcProtocolError, match="unsupported bridge schema None"):
        IpcBody(proc)


def test_spawn_ready_timeout_kills_and_reaps_child(tmp_path, monkeypatch):
    bridge = tmp_path / "silent-bridge"
    bridge.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    bridge.chmod(0o755)
    real_popen = subprocess.Popen
    children = []

    def recording_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        children.append(proc)
        return proc

    monkeypatch.setattr(ipc_module.subprocess, "Popen", recording_popen)
    with pytest.raises(IpcTransportError, match="timed out"):
        IpcBody.spawn(bridge=bridge, response_timeout_s=0.05)

    assert len(children) == 1
    assert children[0].poll() is not None


def test_partial_ready_line_cannot_block_past_timeout(tmp_path):
    bridge = tmp_path / "partial-line-bridge"
    bridge.write_text("#!/bin/sh\nprintf '{'\nsleep 60\n", encoding="utf-8")
    bridge.chmod(0o755)

    with pytest.raises(IpcTransportError, match="timed out"):
        IpcBody.spawn(bridge=bridge, response_timeout_s=0.05)


def test_act_walk_moves_player():
    with spawn_fake() as body:
        body.act(Walk(dir=2))  # East: +1 x
        obs = body.observe()
        assert obs.player.pos.x == 101


def test_agent_drives_ipc_body():
    """The real Agent loop should run unchanged against the IPC body."""
    with spawn_fake() as body:
        agent = Agent(body=body, persona=Persona(name="Fake"), planner=Planner([Wander()]))
        for _ in range(3):
            agent.tick()
        obs = body.observe()
        # Wander emitted Walk actions, so the fake player moved from the origin.
        assert (obs.player.pos.x, obs.player.pos.y) != (100, 100)


def _fake_ipc() -> IpcBody:
    proc = subprocess.Popen(
        [sys.executable, str(FAKE)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    return IpcBody(proc)


class _StubIpc:
    def __init__(
        self,
        *,
        serial=1,
        observe_error=None,
        act_error=None,
        apply_before_act_error=False,
    ):
        self.ready = {"player": {"serial": serial}}
        self.serial = serial
        self.observe_error = observe_error
        self.act_error = act_error
        self.apply_before_act_error = apply_before_act_error
        self.actions = []
        self.closed = False
        self.aborted = False
        self.deadline = None

    @property
    def connected(self):
        return not self.closed and not self.aborted

    def observe(self):
        if self.observe_error is not None:
            raise self.observe_error
        return Observation(
            player=PlayerView(
                serial=self.serial,
                name="Stub",
                pos=Position(100, 100, 0),
                hits=80,
                hits_max=80,
            )
        )

    def act(self, action):
        if self.apply_before_act_error:
            self.actions.append(action)
        if self.act_error is not None:
            raise self.act_error
        if not self.apply_before_act_error:
            self.actions.append(action)

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True

    def set_deadline(self, deadline):
        self.deadline = deadline


class _TwoStepGoal(Skill):
    name = "two_step_reconnect_goal"
    consumes_goal = True

    def can_run(self, ctx: SkillContext) -> bool:
        return ctx.goal is not None and ctx.goal.kind == "reconnect_probe"

    def step(self, ctx: SkillContext) -> SkillResult:
        phase = int(ctx.memory.get("reconnect_probe_phase", 0))
        ctx.memory["reconnect_probe_phase"] = phase + 1
        if phase == 0:
            return SkillResult(Status.RUNNING, Say("before restart"))
        return SkillResult(Status.SUCCESS, Say("after restart"))


def test_restart_policy_is_exponential_and_capped():
    policy = RestartPolicy(
        max_attempts=6,
        initial_backoff_s=0.25,
        max_backoff_s=1.0,
        immediate_first=False,
    )
    assert [policy.delay(i) for i in range(1, 7)] == [0.25, 0.5, 1.0, 1.0, 1.0, 1.0]


def test_observe_replaces_dead_subprocess_and_returns_fresh_observation():
    first = _fake_ipc()
    old_pid = first._proc.pid
    delays = []
    body = ResilientIpcBody(
        first,
        _fake_ipc,
        policy=RestartPolicy(max_attempts=2, initial_backoff_s=0, max_backoff_s=0),
        sleeper=delays.append,
    )
    first.abort()

    obs = body.observe()
    try:
        assert obs.player.serial == 1
        assert body.restart_count == 1
        assert body.generation == 2
        assert body.current_pid != old_pid
        assert delays == [0]
    finally:
        body.close()


def test_agent_identity_goal_memory_episodes_and_ticks_survive_bridge_restart():
    first = _fake_ipc()
    body = ResilientIpcBody(
        first,
        _fake_ipc,
        policy=RestartPolicy(max_attempts=2, initial_backoff_s=0, max_backoff_s=0),
        sleeper=lambda _: None,
    )
    goal = Goal(kind="reconnect_probe", params={"token": "same-goal"})
    agent = Agent(
        body=body,
        persona=Persona(name="Continuity"),
        planner=Planner([_TwoStepGoal()]),
        goal=goal,
    )
    memory_identity = id(agent.memory)
    agent.memory["sentinel"] = {"nested": [1, 2, 3]}
    first_action = agent.tick()
    snapshot = deepcopy(agent.memory)
    episodes_identity = id(agent.episodes)
    ticks_before = agent.ticks
    first.abort()

    second_action = agent.tick()
    try:
        assert isinstance(first_action, Say) and first_action.text == "before restart"
        assert isinstance(second_action, Say) and second_action.text == "after restart"
        assert id(agent.memory) == memory_identity
        assert agent.memory["sentinel"] == snapshot["sentinel"]
        assert id(agent.episodes) == episodes_identity
        assert agent.ticks == ticks_before + 1
        assert body.restart_count == 1
        assert agent.goal is None
    finally:
        body.close()


def test_uncertain_action_is_not_replayed_on_replacement_bridge():
    old = _StubIpc(
        act_error=IpcTransportError("ack lost"),
        apply_before_act_error=True,
    )
    replacement = _StubIpc()
    body = ResilientIpcBody(
        old,
        lambda: replacement,
        policy=RestartPolicy(max_attempts=1, initial_backoff_s=0, max_backoff_s=0),
        sleeper=lambda _: None,
    )
    action = Say("at most once")

    body.act(action)

    assert old.actions == [action]
    assert replacement.actions == []
    assert body.uncertain_actions == 1
    assert body.restart_count == 1


def test_action_known_unsent_is_replayed_once_after_recovery():
    old = _StubIpc(
        act_error=IpcTransportError("dead before write", request_sent=False),
    )
    replacement = _StubIpc()
    body = ResilientIpcBody(
        old,
        lambda: replacement,
        policy=RestartPolicy(max_attempts=1, initial_backoff_s=0, max_backoff_s=0),
        sleeper=lambda _: None,
    )
    action = Say("safe replay")

    body.act(action)

    assert old.actions == []
    assert replacement.actions == [action]
    assert body.uncertain_actions == 0


def test_protocol_error_is_not_hidden_by_restart():
    old = _StubIpc(observe_error=IpcProtocolError("bad action"))
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return _StubIpc()

    body = ResilientIpcBody(old, factory, sleeper=lambda _: None)
    with pytest.raises(IpcProtocolError, match="bad action"):
        body.observe()
    assert factory_calls == 0


def test_remote_error_is_not_hidden_by_restart():
    old = _StubIpc(observe_error=IpcRemoteError("request rejected"))
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return _StubIpc()

    body = ResilientIpcBody(old, factory, sleeper=lambda _: None)
    with pytest.raises(IpcRemoteError, match="request rejected"):
        body.observe()
    assert factory_calls == 0


def test_replacement_player_serial_mismatch_is_terminal_and_fail_closed():
    old = _StubIpc(serial=1, observe_error=IpcTransportError("old died"))
    wrong = _StubIpc(serial=2)
    body = ResilientIpcBody(
        old,
        lambda: wrong,
        policy=RestartPolicy(max_attempts=3, initial_backoff_s=0, max_backoff_s=0),
        sleeper=lambda _: None,
    )

    with pytest.raises(IpcProtocolError, match="serial 2, expected 1"):
        body.observe()

    assert wrong.closed
    assert not body.connected
    assert body.restart_count == 0


def test_recovery_exhaustion_is_bounded_closes_every_child_and_turns_disconnected():
    old = _StubIpc(observe_error=IpcTransportError("old died"))
    candidates = [
        _StubIpc(observe_error=IpcTransportError("retry one")),
        _StubIpc(observe_error=IpcTransportError("retry two")),
        _StubIpc(observe_error=IpcTransportError("retry three")),
    ]
    delays = []
    body = ResilientIpcBody(
        old,
        lambda: candidates.pop(0),
        policy=RestartPolicy(
            max_attempts=3,
            initial_backoff_s=1,
            max_backoff_s=2,
            immediate_first=False,
        ),
        sleeper=delays.append,
    )

    with pytest.raises(IpcRecoveryExhausted, match="exhausted after 3 attempts"):
        body.observe()

    assert delays == [1, 2, 2]
    assert not body.connected
    assert old.closed and old.aborted
    assert not candidates


def test_outage_deadline_stops_before_next_backoff_would_exceed_budget():
    old = _StubIpc(observe_error=IpcTransportError("old died"))
    now = [0.0]
    delays = []

    def sleep(seconds):
        delays.append(seconds)
        now[0] += seconds

    body = ResilientIpcBody(
        old,
        lambda: _StubIpc(observe_error=IpcTransportError("still down")),
        policy=RestartPolicy(
            max_attempts=10,
            initial_backoff_s=2,
            max_backoff_s=4,
            max_outage_s=5,
            immediate_first=False,
        ),
        sleeper=sleep,
        monotonic=lambda: now[0],
    )

    with pytest.raises(IpcRecoveryExhausted, match="after 1 attempts/5s budget"):
        body.observe()
    assert delays == [2]
    assert body.restart_attempts == 1


def test_candidate_finishing_after_absolute_deadline_is_rejected_and_closed():
    old = _StubIpc(observe_error=IpcTransportError("old died"))
    candidate = _StubIpc()
    now = [0.0]

    def slow_factory():
        now[0] = 6.0
        return candidate

    body = ResilientIpcBody(
        old,
        slow_factory,
        policy=RestartPolicy(
            max_attempts=2,
            initial_backoff_s=0,
            max_backoff_s=0,
            max_outage_s=5,
        ),
        sleeper=lambda _: None,
        monotonic=lambda: now[0],
    )

    with pytest.raises(IpcRecoveryExhausted, match="after 1 attempts/5s budget"):
        body.observe()
    assert candidate.aborted and candidate.closed
    assert not body.connected


def test_rapid_crash_loop_does_not_reset_restart_budget_on_each_ready():
    old = _StubIpc(observe_error=IpcTransportError("initial crash"))
    replacements = [_StubIpc(), _StubIpc()]
    body = ResilientIpcBody(
        old,
        lambda: replacements.pop(0),
        policy=RestartPolicy(
            max_attempts=2,
            initial_backoff_s=0,
            max_backoff_s=0,
            stable_after_s=10,
        ),
        sleeper=lambda _: None,
    )

    body.observe()
    body._inner.observe_error = IpcTransportError("crash after ready")
    body.observe()
    body._inner.observe_error = IpcTransportError("still crash-looping")

    with pytest.raises(IpcRecoveryExhausted, match="after 0 attempts"):
        body.observe()
    assert body.restart_count == 2
    assert body.restart_attempts == 2


def test_stable_session_resets_restart_budget():
    now = [0.0]
    old = _StubIpc(observe_error=IpcTransportError("initial crash"))
    first_replacement = _StubIpc()
    second_replacement = _StubIpc()
    replacements = [first_replacement, second_replacement]
    body = ResilientIpcBody(
        old,
        lambda: replacements.pop(0),
        policy=RestartPolicy(
            max_attempts=1,
            initial_backoff_s=0,
            max_backoff_s=0,
            stable_after_s=5,
        ),
        sleeper=lambda _: None,
        monotonic=lambda: now[0],
    )

    body.observe()
    now[0] = 5.0
    body.observe()
    first_replacement.observe_error = IpcTransportError("later independent outage")
    obs = body.observe()

    assert obs.player.serial == 1
    assert body.restart_count == 2
    assert body.restart_attempts == 2


def test_successful_replacement_does_not_keep_the_outage_deadline():
    now = [0.0]
    old = _StubIpc(observe_error=IpcTransportError("old died"))
    replacement = _StubIpc()
    body = ResilientIpcBody(
        old,
        lambda: replacement,
        policy=RestartPolicy(max_attempts=1, initial_backoff_s=0, max_backoff_s=0),
        sleeper=lambda _: None,
        monotonic=lambda: now[0],
    )

    body.observe()
    assert replacement.deadline is None
    now[0] = 31.0
    assert body.observe().player.serial == 1
    assert body.restart_count == 1


def test_close_is_idempotent_and_terminal():
    inner = _StubIpc()
    body = ResilientIpcBody(inner, lambda: _StubIpc())
    body.close()
    body.close()
    assert not body.connected
    with pytest.raises(IpcTransportError, match="closed"):
        body.observe()


def test_unexpected_factory_error_fails_closed_and_releases_lifecycle():
    old = _StubIpc(observe_error=IpcTransportError("old died"))
    body = ResilientIpcBody(
        old,
        lambda: (_ for _ in ()).throw(RuntimeError("factory bug")),
        sleeper=lambda _: None,
    )

    with pytest.raises(RuntimeError, match="factory bug"):
        body.observe()
    assert old.aborted and old.closed
    assert not body.connected


def test_close_serializes_with_recovery_and_is_terminal_on_return():
    old = _StubIpc(observe_error=IpcTransportError("old died"))
    replacement = _StubIpc()
    factory_entered = threading.Event()
    release_factory = threading.Event()

    def factory():
        factory_entered.set()
        assert release_factory.wait(timeout=2)
        return replacement

    body = ResilientIpcBody(
        old,
        factory,
        policy=RestartPolicy(max_attempts=1, initial_backoff_s=0, max_backoff_s=0),
        sleeper=lambda _: None,
    )
    recovery_errors = []

    def recover():
        try:
            body.observe()
        except IpcRecoveryExhausted as exc:
            recovery_errors.append(exc)

    recovery = threading.Thread(target=recover)
    recovery.start()
    assert factory_entered.wait(timeout=2)
    closer = threading.Thread(target=body.close)
    closer.start()
    release_factory.set()
    recovery.join(timeout=2)
    closer.join(timeout=2)

    assert not recovery.is_alive() and not closer.is_alive()
    assert len(recovery_errors) == 1
    assert replacement.closed
    assert not body.connected


def test_reentrant_close_during_backoff_prevents_replacement_spawn():
    old = _StubIpc(observe_error=IpcTransportError("old died"))
    factory_calls = 0
    body = None

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return _StubIpc()

    def sleeper(_delay):
        assert body is not None
        body.close()

    body = ResilientIpcBody(
        old,
        factory,
        policy=RestartPolicy(
            max_attempts=2,
            initial_backoff_s=0,
            max_backoff_s=0,
        ),
        sleeper=sleeper,
    )

    with pytest.raises(IpcRecoveryExhausted):
        body.observe()
    assert factory_calls == 0
    assert not body.connected


def test_resilient_spawn_holds_single_owner_lease_until_close(monkeypatch, tmp_path):
    monkeypatch.setattr(IpcBody, "spawn", staticmethod(lambda *args, **kwargs: _StubIpc()))
    username = f"lease-{tmp_path.name}"
    first = ResilientIpcBody.spawn("127.0.0.1", 2594, username, "secret")
    try:
        with pytest.raises(IpcOwnershipError, match="another supervisor owns"):
            ResilientIpcBody.spawn("127.0.0.1", 2594, username, "secret")
    finally:
        first.close()

    replacement = ResilientIpcBody.spawn("127.0.0.1", 2594, username, "secret")
    replacement.close()


def test_resilient_spawn_disposes_initial_body_when_identity_is_invalid(
    monkeypatch, tmp_path
):
    invalid = _StubIpc(serial=True)
    monkeypatch.setattr(IpcBody, "spawn", staticmethod(lambda *args, **kwargs: invalid))

    with pytest.raises(IpcProtocolError, match="invalid player serial"):
        ResilientIpcBody.spawn(
            "127.0.0.1", 2594, f"invalid-{tmp_path.name}", "secret"
        )
    assert invalid.aborted and invalid.closed
