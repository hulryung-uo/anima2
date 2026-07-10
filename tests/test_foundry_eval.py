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
