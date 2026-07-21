"""Opt-in village wiring for the closed capability cognition mode."""

from __future__ import annotations

import pytest

from anima2 import village
from anima2.capabilities import CapabilityPolicy
from anima2.capability_cognition import CapabilityCognition
from anima2.cognition import ThreadedCognition
from anima2.profession import BANKER_SPOT, PROFESSIONS


class _FakeBody:
    def __init__(self, serial: int) -> None:
        self.ready = {"player": {"serial": serial}}
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_incompatible_curriculum_mode_fails_before_opening_a_body(monkeypatch) -> None:
    opened = False

    def _spawn(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal opened
        opened = True
        raise AssertionError("must fail before transport")

    monkeypatch.setattr(village.ResilientIpcBody, "spawn", _spawn)

    with pytest.raises(ValueError, match="cannot be combined"):
        village.run_village(
            ["miner", "blacksmith"],
            capability_goals=True,
            curriculum=True,
        )

    assert opened is False


def test_invalid_account_prefix_fails_before_transport(monkeypatch) -> None:
    monkeypatch.setattr(
        village.ResilientIpcBody,
        "spawn",
        lambda *args, **kwargs: pytest.fail("must fail before transport"),
    )

    with pytest.raises(ValueError, match="account_prefix"):
        village.run_village(
            ["miner", "blacksmith"],
            capability_goals=True,
            account_prefix="bad prefix",
        )


def test_roster_without_an_installed_capability_fails_before_transport(monkeypatch) -> None:
    monkeypatch.setattr(
        village.ResilientIpcBody,
        "spawn",
        lambda *args, **kwargs: pytest.fail("must fail before transport"),
    )

    with pytest.raises(ValueError, match="no profession"):
        village.run_village(["miner"], capability_goals=True)


def test_solo_blacksmith_fails_without_the_calibrated_banker_pair(monkeypatch) -> None:
    monkeypatch.setattr(
        village.ResilientIpcBody,
        "spawn",
        lambda *args, **kwargs: pytest.fail("must fail before transport"),
    )

    with pytest.raises(ValueError, match=r"miner\+blacksmith"):
        village.run_village(["blacksmith"], capability_goals=True)


def test_flag_reaches_online_runtime_and_every_body_closes(monkeypatch) -> None:
    bodies = iter((_FakeBody(1), _FakeBody(2)))
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        village.ResilientIpcBody,
        "spawn",
        lambda *args, **kwargs: next(bodies),
    )
    monkeypatch.setattr(village.time, "sleep", lambda seconds: None)

    def _capture(online, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["online"] = online
        captured.update(kwargs)

    monkeypatch.setattr(village, "_run_online_village", _capture)

    village.run_village(["miner", "blacksmith"], capability_goals=True)

    assert captured["capability_goals"] is True
    online = captured["online"]
    assert isinstance(online, list)
    assert all(body.closed for body, _profession, _persona in online)


def test_partial_login_failure_does_not_silently_downgrade_to_legacy(monkeypatch) -> None:
    surviving = _FakeBody(2)
    attempts = 0

    def _spawn(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("miner login failed")
        return surviving

    monkeypatch.setattr(village.ResilientIpcBody, "spawn", _spawn)
    monkeypatch.setattr(village.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match=r"lost its miner\+blacksmith pair"):
        village.run_village(["miner", "blacksmith"], capability_goals=True)

    assert surviving.closed is True


def test_total_login_failure_is_an_explicit_capability_error(monkeypatch) -> None:
    monkeypatch.setattr(
        village.ResilientIpcBody,
        "spawn",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    monkeypatch.setattr(village.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="no capability villagers"):
        village.run_village(["miner", "blacksmith"], capability_goals=True)


def _paired_smith_plan() -> dict:
    profession = PROFESSIONS["blacksmith"]
    return {
        "body": _FakeBody(7),
        "prof": profession,
        "persona": village._persona_for(profession, 0),
        "banker_spot": BANKER_SPOT,
    }


def test_capability_staging_explicitly_provisions_first_bank_gold() -> None:
    plan = _paired_smith_plan()

    enabled = village._staging_items(plan, True)
    disabled = village._staging_items(plan, False)

    assert enabled == [*PROFESSIONS["blacksmith"].items, "Gold 100"]
    assert disabled == PROFESSIONS["blacksmith"].items


def test_runtime_builder_uses_exact_closed_components() -> None:
    planner, cognition, policy = village._build_capability_runtime(
        PROFESSIONS["blacksmith"], None
    )

    assert type(cognition) is ThreadedCognition
    assert type(cognition.inner) is CapabilityCognition
    assert type(policy) is CapabilityPolicy
    assert planner.capability_profession == "blacksmith"
    assert planner.capability_ids == frozenset({"bank_gold"})
    assert planner.capability_lease is not None


def test_agent_builder_preserves_policy_and_production_cadence() -> None:
    plan = _paired_smith_plan()
    planner, cognition, policy = village._build_capability_runtime(
        plan["prof"], None
    )

    agent = village._build_villager_agent(
        plan,
        planner,
        cognition,
        policy,
        curriculum_ctrl=None,
        curriculum_goals=False,
    )

    assert agent.planner is planner
    assert agent.cognition is cognition
    assert agent.goal_policy is policy
    assert agent.cognition_interval == 12
    assert agent.goal_validator is None
    assert getattr(agent.goal_progress, "__self__", None) is policy
    assert getattr(agent.goal_admitter, "__self__", None) is policy
