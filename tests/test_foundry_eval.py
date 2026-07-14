"""`foundry/eval.py` offline tests (PHASE5.md item 2's "Offline tests
(planned)" list): `EvalConfig`/`EvalResult` round-trip through
`eval_results.jsonl`, `run_eval_multi`'s averaging math on stubbed per-seed
results (a zero-fitness "frozen" seed pulling the mean down but never being
dropped), and the kernel-integrity guard refusing a tampered-tree fixture via
a `subprocess`-stubbed `git diff`/`git status`. `run_eval` itself needs a live
shard (exercised by `live_eval_gate.py`, not here) — every test below stubs
either `subprocess.run` or `eval.run_eval` so nothing touches the network.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from anima2.foundry import eval as eval_mod
from anima2.foundry.eval import (
    SCENARIOS,
    EvalConfig,
    EvalResult,
    KernelTamperedError,
    assert_kernel_clean,
    read_eval_results,
    run_eval_multi,
    write_eval_result,
)
from anima2.foundry.fitness import FitnessBreakdown
from anima2.skills import Mine


# --- scenario registry ---------------------------------------------------


def test_scenarios_registry_has_mining_and_mining_50():
    assert set(SCENARIOS) >= {"mining", "mining_50"}
    assert SCENARIOS["mining"].skills == {"Mining": 35}
    assert SCENARIOS["mining_50"].skills == {"Mining": 50}
    assert SCENARIOS["mining"].work_skill is Mine
    assert SCENARIOS["mining_50"].work_skill is Mine
    assert SCENARIOS["mining"].items == ("Pickaxe", "Pickaxe")
    assert SCENARIOS["mining"].nodes is None  # unchanged by PHASE6.md item 4's new field
    assert SCENARIOS["mining_50"].nodes is None


def test_scenarios_registry_has_fishing():
    """PHASE6.md item 4: a second scenario-supported profession — staged from
    `profession.py::FISHING_SPOTS[0]`'s calibrated shore/water pair."""
    from anima2.profession import FISHING_SPOTS
    from anima2.skills import Fish

    stand, water = FISHING_SPOTS[0]
    assert "fishing" in SCENARIOS
    fishing = SCENARIOS["fishing"]
    assert fishing.spot == stand
    assert fishing.skills == {"Fishing": 35}
    assert fishing.items == ("FishingPole",)
    assert fishing.skill_names == ("Fishing",)
    assert fishing.work_skill is Fish
    # A 4-tuple cluster of exactly the one calibrated water tile, graphic 0
    # (a land-target cast) — matches `village.py`'s own fisher wiring shape.
    assert fishing.nodes == (water + (0,),)


def test_eval_config_account_name():
    assert EvalConfig(scenario_id="mining", account_prefix="foo", seed=3).account_name() == "foo3"


# --- EvalConfig / EvalResult round-trip -----------------------------------


def _result(*, scenario_id: str = "mining", seed: int = 0, total: float = 13.6) -> EvalResult:
    cfg = EvalConfig(
        scenario_id=scenario_id, ticks=250, seed=seed, spot=(2567, 493),
        skill_overrides={"Mining": 40.0}, item_overrides=("Pickaxe",),
    )
    fb = FitnessBreakdown(total=total, skill_term=10.0, worth_term=3.0, produce_term=0.6)
    return EvalResult(
        scenario_id=scenario_id, config=cfg, fitness=fb, duration_h=1.0,
        skill_gain_total=10.0, gold_delta=200, alive_fraction=1.0,
    )


def test_eval_result_speech_sent_round_trips_and_defaults_zero(tmp_path):
    """PHASE6.md item 5's cognition-eval gate reads raw `speech_sent` off the
    persisted result (the decisive magnitude signal for the dose-response leg,
    not the coarse `sociability_bin`). It must survive the jsonl round-trip,
    and a legacy line written before this field existed must read back as 0."""
    path = tmp_path / "eval_results.jsonl"
    cfg = EvalConfig(scenario_id="mining", ticks=200)
    r = EvalResult(
        scenario_id="mining", config=cfg, fitness=FitnessBreakdown(total=5.0),
        duration_h=1.0, skill_gain_total=0.0, gold_delta=0, alive_fraction=1.0,
        speech_sent=14,
    )
    write_eval_result(r, path)
    assert read_eval_results(path)[0].speech_sent == 14
    # a pre-item-5 ledger line (no "speech_sent" key) defaults to 0 on read
    assert EvalResult.from_json({k: v for k, v in r.to_json().items() if k != "speech_sent"}).speech_sent == 0


def test_eval_result_round_trips_through_jsonl(tmp_path):
    path = tmp_path / "eval_results.jsonl"
    r1 = _result(seed=0, total=13.6)
    r2 = _result(seed=1, total=20.0)
    write_eval_result(r1, path)
    write_eval_result(r2, path)

    back = read_eval_results(path)

    assert len(back) == 2
    assert back[0].scenario_id == "mining"
    assert back[0].config == r1.config
    assert back[0].config.spot == (2567, 493)  # tuple, not a list, after round-trip
    assert back[0].config.item_overrides == ("Pickaxe",)
    assert back[0].fitness.total == pytest.approx(13.6)
    assert back[0].fitness.skill_term == pytest.approx(10.0)
    assert back[0].duration_h == pytest.approx(1.0)
    assert back[0].gold_delta == 200
    assert back[1].fitness.total == pytest.approx(20.0)


def test_eval_config_nodes_round_trips_through_jsonl(tmp_path):
    """PHASE6.md item 4's fishing bank-drain fix: `EvalConfig.nodes` (the
    per-seed water-tile override) survives the `to_json`/`from_json` ledger
    round-trip as a tuple-of-tuples, not a list-of-lists — mirroring the
    `spot`/`item_overrides` conversion `from_json` already does."""
    path = tmp_path / "eval_results.jsonl"
    cfg = EvalConfig(scenario_id="fishing", ticks=250, nodes=((2868, 638, -5, 0),))
    fb = FitnessBreakdown(total=5.0)
    r = EvalResult(
        scenario_id="fishing", config=cfg, fitness=fb, duration_h=1.0,
        skill_gain_total=0.0, gold_delta=0, alive_fraction=1.0,
    )
    write_eval_result(r, path)

    back = read_eval_results(path)

    assert len(back) == 1
    assert back[0].config.nodes == ((2868, 638, -5, 0),)  # tuple-of-tuples, not list-of-lists
    assert back[0].config == cfg  # full config equality survives the round-trip


def test_read_eval_results_missing_file_returns_empty():
    assert read_eval_results("no/such/path/eval_results.jsonl") == []


def test_read_eval_results_skips_corrupt_and_blank_lines(tmp_path):
    path = tmp_path / "eval_results.jsonl"
    write_eval_result(_result(seed=0), path)
    with path.open("a") as f:
        f.write("{not valid json\n")
        f.write("\n")
        f.write('{"scenario_id": "mining"}\n')  # valid JSON, missing required keys

    back = read_eval_results(path)

    assert len(back) == 1


# --- run_eval_multi: averaging math on stubbed per-seed results ----------


def _stub_run_eval(fitness_by_seed: dict[int, float]):
    def fake(cfg, *, kernel_repo_root="."):
        fb = FitnessBreakdown(total=fitness_by_seed[cfg.seed])
        return EvalResult(
            scenario_id=cfg.scenario_id, config=cfg, fitness=fb, duration_h=1.0,
            skill_gain_total=0.0, gold_delta=0, alive_fraction=1.0,
        )
    return fake


def test_run_eval_multi_averages_per_seed_fitness(monkeypatch):
    monkeypatch.setattr(eval_mod, "run_eval", _stub_run_eval({0: 10.0, 1: 20.0, 2: 30.0}))
    cfg = EvalConfig(scenario_id="mining", seed=0)

    multi = run_eval_multi(cfg, seeds=3, kernel_repo_root=None)

    assert multi.per_seed_fitness == [10.0, 20.0, 30.0]
    assert multi.mean_fitness == pytest.approx(20.0)
    assert multi.stdev_fitness > 0.0


def test_run_eval_multi_zero_fitness_frozen_seed_pulls_mean_down_not_to_zero(monkeypatch):
    """The known Harvest/Mine intermittent freeze gates a frozen seed to ~0
    (fitness.py's own viability gate) — run_eval_multi must average that
    seed IN (pulling the mean down), never drop it or let it zero the whole
    mean out (PHASE5.md item 2's own "average repeats, never trust one
    sample" requirement)."""
    monkeypatch.setattr(eval_mod, "run_eval", _stub_run_eval({0: 10.0, 1: 20.0, 2: 0.0}))
    cfg = EvalConfig(scenario_id="mining", seed=0)

    multi = run_eval_multi(cfg, seeds=3, kernel_repo_root=None)

    assert multi.per_seed_fitness == [10.0, 20.0, 0.0]
    assert multi.mean_fitness == pytest.approx(10.0)  # pulled down from 15.0 (dropping the 0) to 10.0
    assert multi.mean_fitness != 0.0  # never zeroed out entirely
    assert len(multi.results) == 3  # the frozen seed's own result is kept, not discarded


def test_run_eval_multi_assigns_fresh_seed_per_repeat_and_cycles_spot_pool(monkeypatch):
    captured: list[EvalConfig] = []

    def fake(cfg, *, kernel_repo_root="."):
        captured.append(cfg)
        return EvalResult(
            scenario_id=cfg.scenario_id, config=cfg, fitness=FitnessBreakdown(total=1.0),
            duration_h=1.0, skill_gain_total=0.0, gold_delta=0, alive_fraction=1.0,
        )

    monkeypatch.setattr(eval_mod, "run_eval", fake)
    cfg = EvalConfig(scenario_id="mining", seed=10)
    pool = [(1, 1), (2, 2)]

    run_eval_multi(cfg, seeds=3, spot_pool=pool, kernel_repo_root=None)

    assert [c.seed for c in captured] == [10, 11, 12]
    assert [c.spot for c in captured] == [(1, 1), (2, 2), (1, 1)]  # cycles, no repeats within len(pool)


def test_run_eval_multi_no_spot_pool_keeps_configured_spot(monkeypatch):
    captured: list[EvalConfig] = []

    def fake(cfg, *, kernel_repo_root="."):
        captured.append(cfg)
        return EvalResult(
            scenario_id=cfg.scenario_id, config=cfg, fitness=FitnessBreakdown(total=1.0),
            duration_h=1.0, skill_gain_total=0.0, gold_delta=0, alive_fraction=1.0,
        )

    monkeypatch.setattr(eval_mod, "run_eval", fake)
    cfg = EvalConfig(scenario_id="mining", seed=0, spot=(5, 5))

    run_eval_multi(cfg, seeds=2, kernel_repo_root=None)

    assert [c.spot for c in captured] == [(5, 5), (5, 5)]


def test_run_eval_multi_cycles_nodes_pool_in_lockstep_with_spot_pool(monkeypatch):
    """PHASE6.md item 4's fishing bank-drain fix: `nodes_pool` rotates the
    water node per seed, index-aligned with `spot_pool`'s shore stand — a
    matched `(stand, water)` pair must move together so no two seeds re-fish
    one draining 8x8 `HarvestBank`."""
    captured: list[EvalConfig] = []

    def fake(cfg, *, kernel_repo_root="."):
        captured.append(cfg)
        return EvalResult(
            scenario_id=cfg.scenario_id, config=cfg, fitness=FitnessBreakdown(total=1.0),
            duration_h=1.0, skill_gain_total=0.0, gold_delta=0, alive_fraction=1.0,
        )

    monkeypatch.setattr(eval_mod, "run_eval", fake)
    cfg = EvalConfig(scenario_id="fishing", seed=0)
    stands = [(1, 1), (2, 2), (3, 3)]
    nodes = [((10, 10, -5, 0),), ((20, 20, -5, 0),), ((30, 30, -5, 0),)]

    run_eval_multi(cfg, seeds=3, spot_pool=stands, nodes_pool=nodes, kernel_repo_root=None)

    # stand[i] and node[i] land on the SAME seed — the lockstep guarantee.
    assert [c.spot for c in captured] == [(1, 1), (2, 2), (3, 3)]
    assert [c.nodes for c in captured] == [
        ((10, 10, -5, 0),), ((20, 20, -5, 0),), ((30, 30, -5, 0),)
    ]


def test_run_eval_multi_no_nodes_pool_keeps_configured_nodes(monkeypatch):
    """`nodes_pool=None` (every mining caller) leaves each seed at `cfg.nodes`
    — a byte-for-byte no-op, mirroring `spot_pool`'s own default."""
    captured: list[EvalConfig] = []

    def fake(cfg, *, kernel_repo_root="."):
        captured.append(cfg)
        return EvalResult(
            scenario_id=cfg.scenario_id, config=cfg, fitness=FitnessBreakdown(total=1.0),
            duration_h=1.0, skill_gain_total=0.0, gold_delta=0, alive_fraction=1.0,
        )

    monkeypatch.setattr(eval_mod, "run_eval", fake)
    cfg = EvalConfig(scenario_id="mining", seed=0)

    run_eval_multi(cfg, seeds=2, kernel_repo_root=None)

    assert [c.nodes for c in captured] == [None, None]


def test_run_eval_multi_writes_each_seed_to_results_path(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_mod, "run_eval", _stub_run_eval({0: 5.0, 1: 7.0}))
    path = tmp_path / "results.jsonl"
    cfg = EvalConfig(scenario_id="mining", seed=0)

    run_eval_multi(cfg, seeds=2, kernel_repo_root=None, results_path=path)

    back = read_eval_results(path)
    assert len(back) == 2
    assert {r.fitness.total for r in back} == {5.0, 7.0}


# --- kernel-integrity guard: subprocess-stubbed git -----------------------


class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_assert_kernel_clean_passes_on_clean_tree(monkeypatch):
    def fake_run(argv, **kwargs):
        return _FakeCompleted(0, stdout="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert_kernel_clean(".")  # does not raise


def test_assert_kernel_clean_raises_on_tracked_edit(monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[:2] == ["git", "diff"]:
            return _FakeCompleted(1)  # git diff --quiet: 1 == differs
        return _FakeCompleted(0, stdout="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(KernelTamperedError, match="uncommitted changes"):
        assert_kernel_clean(".")


def test_assert_kernel_clean_raises_on_untracked_file(monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[:2] == ["git", "diff"]:
            return _FakeCompleted(0)
        return _FakeCompleted(0, stdout="?? anima2/foundry/sneaky.py\n")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(KernelTamperedError, match="untracked"):
        assert_kernel_clean(".")


def test_assert_kernel_clean_raises_when_git_unavailable(monkeypatch):
    def fake_run(argv, **kwargs):
        raise OSError("git: command not found")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(KernelTamperedError, match="could not check kernel integrity"):
        assert_kernel_clean(".")


def test_assert_kernel_clean_raises_on_unexpected_diff_exit_code(monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[:2] == ["git", "diff"]:
            return _FakeCompleted(128, stderr="fatal: not a git repository")
        return _FakeCompleted(0, stdout="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(KernelTamperedError, match="failed unexpectedly"):
        assert_kernel_clean(".")


def test_run_eval_refuses_before_touching_network_when_kernel_tampered(monkeypatch):
    """`run_eval` must call the guard BEFORE any staging/network work — proven
    by raising from the guard and asserting nothing about IpcBody was ever
    touched (no monkeypatch on it needed at all: if run_eval reached that far
    without one, this test would hang or explode against a real bridge)."""
    def fake_assert_clean(repo_root, kernel_path="anima2/foundry"):
        raise KernelTamperedError("dirty (test fixture)")
    monkeypatch.setattr(eval_mod, "assert_kernel_clean", fake_assert_clean)

    cfg = EvalConfig(scenario_id="mining")
    with pytest.raises(KernelTamperedError):
        eval_mod.run_eval(cfg)


def test_run_eval_skips_kernel_guard_when_repo_root_is_none(monkeypatch):
    called = []
    monkeypatch.setattr(eval_mod, "assert_kernel_clean", lambda *a, **k: called.append((a, k)))
    monkeypatch.setattr(time, "sleep", lambda s: None)  # skip run_eval's own real login throttle

    class _NoNetwork(Exception):
        pass

    def fake_spawn(*a, **k):
        raise _NoNetwork("must not be reached in this test either way")

    monkeypatch.setattr(eval_mod.IpcBody, "spawn", staticmethod(fake_spawn))

    cfg = EvalConfig(scenario_id="mining")
    with pytest.raises(_NoNetwork):
        eval_mod.run_eval(cfg, kernel_repo_root=None)

    assert called == []  # the guard itself was never invoked


# --- PHASE6.md item 4: Scenario.nodes -> agent.memory["harvest_nodes"] -----
#
# `run_eval` itself needs a live shard to actually exercise `Fish` (that's
# `live_eval_gate.py`'s job — see PHASE6.md item 4's own live gate); these
# stub out `IpcBody`/`GmControl` entirely (no network) to prove the
# staging/memory-seeding PLUMBING alone: a fixture `run_eval` call, with
# `ticks=0` so the fast loop never actually runs, seeds (or doesn't seed)
# `agent.memory["harvest_nodes"]` exactly per `Scenario.nodes`.


class _StubIpc:
    """Minimal context-manager `IpcBody.spawn(...)` stand-in — no real bridge
    subprocess, no network."""

    def __init__(self, serial: int = 111) -> None:
        self.ready = {"player": {"serial": serial}}
        self._serial = serial

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def observe(self):
        from anima2.contract import Observation, PlayerView, Position
        return Observation(player=PlayerView(serial=self._serial, pos=Position(0, 0, 0)))

    def act(self, action) -> None:
        pass


class _StubGm:
    """Minimal context-manager `GmControl.spawn(...)` stand-in — same purpose
    as `_StubIpc`."""

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def hide(self) -> None:
        pass

    def stage(self, *a, **k) -> None:
        pass

    def get_property_value(self, *a, **k):
        return 0.0


def _run_eval_with_stub_plumbing(monkeypatch, cfg: EvalConfig):
    """Runs `eval_mod.run_eval(cfg, kernel_repo_root=None)` against
    `_StubIpc`/`_StubGm` and returns the `Agent` it constructed (via a spy
    wrapper around `eval_mod.Agent`, since `run_eval` doesn't return it
    itself) — proves `run_eval`'s own staging/memory-seeding wiring without a
    live shard."""
    monkeypatch.setattr(time, "sleep", lambda s: None)  # skip the real login throttle
    monkeypatch.setattr(eval_mod, "wipe_area", lambda *a, **k: None)
    monkeypatch.setattr(eval_mod.IpcBody, "spawn", staticmethod(lambda *a, **k: _StubIpc()))
    monkeypatch.setattr(eval_mod.GmControl, "spawn", staticmethod(lambda *a, **k: _StubGm()))

    captured = {}
    real_agent_cls = eval_mod.Agent

    def _spy_agent(*a, **k):
        agent = real_agent_cls(*a, **k)
        captured["agent"] = agent
        return agent

    monkeypatch.setattr(eval_mod, "Agent", _spy_agent)
    eval_mod.run_eval(cfg, kernel_repo_root=None)
    return captured["agent"]


def test_run_eval_seeds_harvest_nodes_from_scenario_nodes_when_set(monkeypatch):
    """`SCENARIOS["fishing"].nodes` round-trips into
    `agent.memory["harvest_nodes"]` exactly (a list, matching `village.py`'s
    own `agent.memory["harvest_nodes"] = p["nodes"]` shape) — `Fish` has no
    exact node to probe toward without this."""
    agent = _run_eval_with_stub_plumbing(monkeypatch, EvalConfig(scenario_id="fishing", ticks=0))
    assert agent.memory["harvest_nodes"] == list(SCENARIOS["fishing"].nodes)


def test_run_eval_does_not_seed_harvest_nodes_when_scenario_nodes_is_none(monkeypatch):
    """Negative control — `Scenario.nodes=None` (every mining entry, the
    pre-item-4 default) never touches `agent.memory` at all, not even an
    empty-list key."""
    agent = _run_eval_with_stub_plumbing(monkeypatch, EvalConfig(scenario_id="mining", ticks=0))
    assert "harvest_nodes" not in agent.memory


def test_run_eval_prefers_cfg_nodes_over_scenario_nodes_when_set(monkeypatch):
    """PHASE6.md item 4's fishing bank-drain fix: `EvalConfig.nodes`, when
    set, OVERRIDES the scenario's own `nodes` in `agent.memory[
    "harvest_nodes"]` — the per-seed water-tile rotation the fix needs.
    Deliberately a DISTINCT node from `SCENARIOS["fishing"].nodes` to prove
    the override path, not the fallback one."""
    override = ((2868, 638, -5, 0),)
    assert override != SCENARIOS["fishing"].nodes  # genuinely different from the scenario default
    agent = _run_eval_with_stub_plumbing(
        monkeypatch, EvalConfig(scenario_id="fishing", ticks=0, nodes=override)
    )
    assert agent.memory["harvest_nodes"] == list(override)


# --- PHASE6.md item 5: cognition-aware eval (the off-switch + the stub path) --
#
# The load-bearing regression pin below proves the off-switch really gates on
# `cognition_provider`, NEVER on `cognition_tier`/`sociability` (both non-`None`
# yet ignored). The stub-provider tests drive a FULLY OFFLINE `StubLLMClient`
# (no network) end-to-end through `run_eval`'s own tick loop, proving the
# cognition wiring produces real speech for a sociable persona and none for a
# silent one — short of the live gate (`live_cognition_eval_gate.py`).


def test_run_eval_cognition_provider_none_is_bare_agent_even_with_tier_and_sociability(monkeypatch):
    """THE load-bearing pin (PHASE6.md item 5): `cognition_provider=None` (the
    default) builds the pre-item-5 bare agent — exactly `[work_skill]`,
    `NullCognition`, the `Persona` default `talkativeness` — EVEN WHEN
    `cognition_tier`/`sociability` are non-`None`. Proves the off-switch gates
    on `cognition_provider` alone, so a genome-driven eval (whose
    `cognition_tier` is a required, never-`None` field) stays inert unless the
    RUN-level `EvolutionConfig.cognition_provider` is set."""
    from anima2.agent import NullCognition

    agent = _run_eval_with_stub_plumbing(
        monkeypatch,
        EvalConfig(scenario_id="mining", ticks=0, cognition_tier="standard", sociability=0.9),
    )
    assert [type(s) for s in agent.planner.skills] == [Mine]
    assert isinstance(agent.cognition, NullCognition)
    assert agent.persona.talkativeness == 0.3  # sociability=0.9 was ignored — the off-switch is on provider


def test_run_eval_cognition_provider_stub_builds_cognition_aware_agent(monkeypatch):
    """A concrete `cognition_provider` builds the cognition-aware agent: the
    full `Profession.planner()`-shaped worker planner (so `SpeakPending` can
    voice queued lines) driven by a `ThreadedCognition(LLMCognition(...,
    talkativeness_gate=True))`, its persona staged at `sociability`."""
    from anima2.cognition import LLMCognition, ThreadedCognition
    from anima2.skills import Fish, GoTo, Greet, SpeakPending, Wander

    agent = _run_eval_with_stub_plumbing(
        monkeypatch,
        EvalConfig(scenario_id="fishing", ticks=0, cognition_provider="stub",
                   cognition_tier="cheap", sociability=0.75),
    )
    assert [type(s) for s in agent.planner.skills] == [SpeakPending, GoTo, Fish, Greet, Wander]
    assert isinstance(agent.cognition, ThreadedCognition)
    assert isinstance(agent.cognition.inner, LLMCognition)
    assert agent.cognition.inner.talkativeness_gate is True
    assert agent.persona.talkativeness == 0.75


class _SlowStubIpc(_StubIpc):
    """`_StubIpc` whose `observe()` yields for ~2ms so `run_eval`'s tick loop
    takes real (if small) wall-clock time. `run_eval` builds the
    cognition-aware agent with a `ThreadedCognition` that reconsiders on a
    BACKGROUND thread (the fast loop never blocks on cognition, by design), so
    an instant-stub tick loop would race to the end before any async reconsider
    could queue speech. A couple of ms per tick gives the (microsecond-fast,
    fully offline `StubLLMClient`) reconsider ample time to land — enough for
    `SpeakPending` to voice it, so `speech_sent` reflects real speech."""

    def observe(self):
        time.sleep(0.002)
        return super().observe()


def _run_eval_stub_capture_summary(monkeypatch, tmp_path, cfg: EvalConfig):
    """Drive `run_eval`'s real tick loop against `_SlowStubIpc`/`_StubGm` (no
    network) with a fully offline `cognition_provider="stub"` cognition, and
    return the `TrajectorySummary` it scored — captured via a spy around
    `eval_mod.compute_fitness` (the one place the finished summary is in
    scope). Routes the tiered clients' usage log at a `tmp_path` so the suite
    never touches the real `data/llm_usage.jsonl`, and no-ops `run_eval`'s own
    login throttle (`eval_mod.login_throttle`) rather than `time.sleep`, so the
    per-tick yields above still elapse."""
    from anima2 import llm as llm_mod

    monkeypatch.setattr(llm_mod, "_DEFAULT_USAGE_LOG", tmp_path / "usage.jsonl")
    monkeypatch.setattr(eval_mod, "login_throttle", lambda *a, **k: None)  # skip the 4s/15s throttles
    monkeypatch.setattr(eval_mod, "wipe_area", lambda *a, **k: None)
    monkeypatch.setattr(eval_mod.IpcBody, "spawn", staticmethod(lambda *a, **k: _SlowStubIpc()))
    monkeypatch.setattr(eval_mod.GmControl, "spawn", staticmethod(lambda *a, **k: _StubGm()))

    captured: dict = {}
    real_fitness = eval_mod.compute_fitness

    def _spy_fitness(summary):
        captured["summary"] = summary
        return real_fitness(summary)

    monkeypatch.setattr(eval_mod, "compute_fitness", _spy_fitness)
    eval_mod.run_eval(cfg, kernel_repo_root=None)
    return captured["summary"]


def test_run_eval_stub_cognition_speaks_when_sociable(monkeypatch, tmp_path):
    """Fully offline, no network: `cognition_provider="stub"`,
    `sociability=1.0` — the stub always replies, the talkativeness gate always
    passes, so `SpeakPending` voices it and `TrajectorySummary.speech_sent` is
    nonzero (the wiring end-to-end, short of the live gate)."""
    summary = _run_eval_stub_capture_summary(
        monkeypatch, tmp_path,
        EvalConfig(scenario_id="mining", ticks=160, cognition_provider="stub",
                   cognition_tier="cheap", sociability=1.0),
    )
    assert summary.speech_sent > 0


def test_run_eval_stub_cognition_silent_when_asocial(monkeypatch, tmp_path):
    """The deterministic negative control: same fully-offline stub wiring but
    `sociability=0.0` — every talkativeness-gate draw is `>= 0.0`, so nothing
    is ever queued, `SpeakPending` never voices, and `speech_sent` is EXACTLY
    zero (not merely small)."""
    summary = _run_eval_stub_capture_summary(
        monkeypatch, tmp_path,
        EvalConfig(scenario_id="mining", ticks=160, cognition_provider="stub",
                   cognition_tier="cheap", sociability=0.0),
    )
    assert summary.speech_sent == 0
