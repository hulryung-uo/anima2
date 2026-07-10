"""`live_common.py` offline tests (Phase 5 item 2's consolidation rider):
`RecordingBody`'s pass-through + caching, the wipe/login-throttle/fresh-suffix
helpers, and `print_gate_verdict`'s flag-tally + bool return. Mirrors
`test_foundry_trajectory.py`'s scripted-fake style for the body wrapper.
"""

from __future__ import annotations

import time

from anima2.live_common import (
    LOGIN_BURST_COOLDOWN_S,
    LOGIN_THROTTLE_S,
    RecordingBody,
    fresh_suffix,
    login_throttle,
    print_gate_verdict,
    wipe_area,
    wipe_bounds,
)


class _FakeInnerBody:
    def __init__(self, observations: list) -> None:
        self._obs = list(observations)
        self.acted: list = []

    @property
    def connected(self) -> bool:
        return True

    def observe(self):
        return self._obs.pop(0)

    def act(self, action) -> None:
        self.acted.append(action)


# --- RecordingBody -----------------------------------------------------------


def test_recording_body_caches_last_obs_and_forwards_act():
    inner = _FakeInnerBody(["obs1", "obs2"])
    body = RecordingBody(inner)

    assert body.last_obs is None
    assert body.observe() == "obs1"
    assert body.last_obs == "obs1"
    assert body.observe() == "obs2"
    assert body.last_obs == "obs2"

    body.act("some-action")
    assert inner.acted == ["some-action"]
    assert body.connected is True


# --- wipe_bounds / wipe_area ---------------------------------------------------


class _FakeGm:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def command_area(self, command, x1, y1, x2, y2, z) -> bool:
        self.calls.append((command, x1, y1, x2, y2, z))
        return True


def test_wipe_bounds_issues_items_then_npcs_over_exact_rectangle():
    gm = _FakeGm()
    wipe_bounds(gm, 10, 20, 30, 40, z=15)
    assert gm.calls == [("[WipeItems", 10, 20, 30, 40, 15), ("[WipeNPCs", 10, 20, 30, 40, 15)]


def test_wipe_bounds_defaults_z_to_20():
    gm = _FakeGm()
    wipe_bounds(gm, 0, 0, 1, 1)
    assert gm.calls[0][-1] == 20


def test_wipe_area_computes_radius_square_around_center():
    gm = _FakeGm()
    wipe_area(gm, 100, 200, 10)
    assert gm.calls == [("[WipeItems", 90, 190, 110, 210, 20), ("[WipeNPCs", 90, 190, 110, 210, 20)]


# --- login_throttle / fresh_suffix ---------------------------------------------


def test_login_throttle_sleeps_the_default_and_a_custom_duration(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    login_throttle()
    login_throttle(LOGIN_BURST_COOLDOWN_S)

    assert slept == [LOGIN_THROTTLE_S, LOGIN_BURST_COOLDOWN_S]


def test_fresh_suffix_is_a_bounded_digit_string():
    suffix = fresh_suffix()
    assert suffix.isdigit()
    assert int(suffix) < 1_000_000


# --- print_gate_verdict ---------------------------------------------------------


def test_print_gate_verdict_all_true_passes_and_prints_every_flag(capsys):
    passed = print_gate_verdict({"a": True, "b": True}, label="GATE")
    out = capsys.readouterr().out

    assert passed is True
    assert "[FLAG] a = True" in out
    assert "[FLAG] b = True" in out
    assert "[FLAG] GATE PASSED" in out


def test_print_gate_verdict_any_false_fails(capsys):
    passed = print_gate_verdict({"a": True, "b": False}, label="GATE")
    out = capsys.readouterr().out

    assert passed is False
    assert "[FLAG] GATE FAILED" in out


def test_print_gate_verdict_includes_detail_suffix_only_when_given(capsys):
    print_gate_verdict({"a": True}, label="GATE", detail="some context")
    with_detail = capsys.readouterr().out
    assert "[FLAG] GATE PASSED: some context" in with_detail

    print_gate_verdict({"a": True}, label="GATE")
    without_detail = capsys.readouterr().out
    assert "[FLAG] GATE PASSED" in without_detail
    assert ":" not in without_detail.strip().splitlines()[-1]
