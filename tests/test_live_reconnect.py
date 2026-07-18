"""Offline regression tests for the live A3 reconnect gate's pure evidence."""

from __future__ import annotations

from types import SimpleNamespace

from anima2.contract import Observation, PlayerView, Position, WalkTo
from anima2.live_reconnect import _ContinuitySnapshot, _same_walkto
from anima2.skills.base import Goal


def _continuity_fixture():
    resilient = SimpleNamespace(current_pid=202, generation=2)
    body = SimpleNamespace(resilient=resilient)
    goal = Goal(kind="goto", params={"target": Position(2587, 408, 15)})
    agent = SimpleNamespace(
        body=body,
        goal=goal,
        memory={"sentinel": {"nested": [1, 2, 3]}},
        episodes=object(),
        ticks=7,
    )
    snapshot = _ContinuitySnapshot(
        agent_id=id(agent),
        body_wrapper_id=id(body),
        resilient_body_id=id(resilient),
        goal_id=id(goal),
        goal_value=Goal(kind="goto", params={"target": Position(2587, 408, 15)}),
        memory_id=id(agent.memory),
        memory_value={"sentinel": {"nested": [1, 2, 3]}},
        episodes_id=id(agent.episodes),
        ticks=7,
        player_serial=0x1234,
        bridge_pid=101,
        generation=1,
    )
    obs = Observation(
        player=PlayerView(serial=0x1234, name="A3", pos=Position(2552, 420, 15))
    )
    return snapshot, agent, body, obs


def test_continuity_snapshot_requires_same_python_state_but_new_bridge_process():
    snapshot, agent, body, obs = _continuity_fixture()

    assert all(snapshot.flags(agent, body, obs).values())

    agent.memory["sentinel"]["nested"].append(4)
    flags = snapshot.flags(agent, body, obs)
    assert flags["memory_identity_preserved"] is True
    assert flags["memory_snapshot_preserved"] is False


def test_same_walkto_requires_the_original_exact_target():
    target = Position(2587, 408, 15)

    assert _same_walkto(WalkTo(2587, 408), target)
    assert not _same_walkto(WalkTo(2586, 408), target)
    assert not _same_walkto(None, target)
