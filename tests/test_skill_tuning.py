"""Skill parameter tuning: a discrete-grid UCB1 bandit (PHASE4.md item 4).
Offline, seeded RNG where randomness is involved — same style as
`test_skill_library.py`'s own hand-built ledger fixtures.
"""

from __future__ import annotations

import json
import random

from anima2.memory import Episode, EpisodicMemory
from anima2.skill_tuning import ParamSpec, ParamTuner, session_mean_reward

import pytest

SPEC = ParamSpec("deliver_threshold", (5, 8, 12, 20))

# --- ParamSpec ---------------------------------------------------------------------


def test_paramspec_rejects_empty_candidates():
    with pytest.raises(ValueError):
        ParamSpec("deliver_threshold", ())


# --- choose() / update() ------------------------------------------------------------


def test_choose_tries_every_candidate_once_before_exploiting():
    """Deterministic, not random: the first `len(candidates)` picks are
    exactly `spec.candidates` in order — UCB1's mandatory initial sweep."""
    tuner = ParamTuner("mine_smelt_deliver", SPEC)
    picks = []
    for _ in SPEC.candidates:
        c = tuner.choose()
        picks.append(c)
        tuner.update(c, 1.0)
    assert picks == list(SPEC.candidates)


def test_choose_after_initial_sweep_prefers_higher_observed_reward():
    tuner = ParamTuner("mine_smelt_deliver", SPEC)
    for c in SPEC.candidates:
        tuner.update(c, 1.0)
    # 8 now clearly outperforms everything else — even with the exploration
    # bonus (all arms tied at count=1 after the sweep, same log(total)/count
    # term), its higher mean must win the very next pick.
    tuner.update(8, 100.0)
    assert tuner.choose() == 8


def test_bandit_converges_to_the_better_candidate():
    """The classic bandit-convergence test: candidate 8 always pays more (a
    fixed gap well above the per-pull Gaussian noise), so after many
    `choose()`/`update()` cycles the tuner's pull distribution concentrates
    on it — not a uniform/flat split across all four candidates."""
    rng = random.Random(20260708)
    base_reward = {5: 1.0, 8: 3.0, 12: 1.2, 20: 1.1}
    tuner = ParamTuner("mine_smelt_deliver", SPEC)
    for _ in range(300):
        value = tuner.choose()
        reward = base_reward[value] + rng.gauss(0.0, 0.3)
        tuner.update(value, reward)

    pulls = tuner.pulls()
    assert max(pulls, key=pulls.get) == 8
    assert pulls[8] > sum(pulls.values()) * 0.5, f"expected 8 to dominate, got {pulls}"


def test_total_pulls_sums_across_candidates():
    tuner = ParamTuner("mine_smelt_deliver", SPEC)
    assert tuner.total_pulls == 0
    tuner.update(5, 1.0)
    tuner.update(8, 2.0)
    tuner.update(8, 2.0)
    assert tuner.total_pulls == 3


def test_update_on_off_grid_value_is_tracked_but_excluded_from_pulls_and_total():
    """A value outside `spec.candidates` (e.g. replayed from a stale ledger —
    see `load_from_ledger`'s own docstring) is recorded by `update()` without
    raising, but never counted toward `total_pulls`/`pulls()` — it can't skew
    `choose()`'s exploration term for today's actual candidate grid."""
    tuner = ParamTuner("mine_smelt_deliver", SPEC)
    tuner.update(99, 5.0)  # not in SPEC.candidates
    assert tuner.total_pulls == 0
    assert tuner.pulls() == {5: 0, 8: 0, 12: 0, 20: 0}


def test_mean_rewards_reports_zero_for_never_pulled_candidates():
    tuner = ParamTuner("mine_smelt_deliver", SPEC)
    tuner.update(5, 4.0)
    tuner.update(5, 2.0)
    means = tuner.mean_rewards()
    assert means[5] == 3.0
    assert means[8] == means[12] == means[20] == 0.0


# --- load_from_ledger ----------------------------------------------------------------


def _write_ledger(path, records):
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_load_from_ledger_matches_equivalent_update_sequence(tmp_path):
    ledger = tmp_path / "skill_ledger.jsonl"
    _write_ledger(ledger, [
        {"skill_name": "mine_smelt_deliver", "profession": "miner", "reward": 4.0,
         "status": "SUCCESS", "param": "deliver_threshold", "param_value": 5},
        {"skill_name": "mine_smelt_deliver", "profession": "miner", "reward": 6.0,
         "status": "SUCCESS", "param": "deliver_threshold", "param_value": 8},
        {"skill_name": "mine_smelt_deliver", "profession": "miner", "reward": 2.0,
         "status": "SUCCESS", "param": "deliver_threshold", "param_value": 5},
        # a plain item-3 per-tick record (param/param_value unset) — must be ignored
        {"skill_name": "mine_smelt_deliver", "profession": "miner", "reward": 1.0,
         "status": "RUNNING", "param": None, "param_value": None},
        # a different skill entirely, even with a matching param name — must be ignored
        {"skill_name": "hunt", "profession": "hunter", "reward": 9.0,
         "status": "SUCCESS", "param": "deliver_threshold", "param_value": 5},
        # a different param on the same skill — must be ignored
        {"skill_name": "mine_smelt_deliver", "profession": "miner", "reward": 9.0,
         "status": "SUCCESS", "param": "ore_threshold", "param_value": 5},
    ])

    loaded = ParamTuner.load_from_ledger(ledger, "mine_smelt_deliver", "deliver_threshold", SPEC)

    direct = ParamTuner("mine_smelt_deliver", SPEC)
    direct.update(5, 4.0)
    direct.update(8, 6.0)
    direct.update(5, 2.0)

    assert loaded.pulls() == direct.pulls()
    assert loaded.mean_rewards() == direct.mean_rewards()
    assert loaded.total_pulls == direct.total_pulls == 3


def test_load_from_ledger_missing_file_initializes_zero_pulls(tmp_path):
    tuner = ParamTuner.load_from_ledger(
        tmp_path / "does" / "not" / "exist.jsonl", "mine_smelt_deliver", "deliver_threshold", SPEC,
    )
    assert tuner.pulls() == {5: 0, 8: 0, 12: 0, 20: 0}
    assert tuner.total_pulls == 0


def test_load_from_ledger_empty_file_initializes_zero_pulls(tmp_path):
    ledger = tmp_path / "skill_ledger.jsonl"
    ledger.write_text("")
    tuner = ParamTuner.load_from_ledger(ledger, "mine_smelt_deliver", "deliver_threshold", SPEC)
    assert tuner.pulls() == {5: 0, 8: 0, 12: 0, 20: 0}


def test_load_from_ledger_corrupted_trailing_line_is_skipped_not_fatal(tmp_path):
    ledger = tmp_path / "skill_ledger.jsonl"
    _write_ledger(ledger, [
        {"skill_name": "mine_smelt_deliver", "profession": "miner", "reward": 4.0,
         "status": "SUCCESS", "param": "deliver_threshold", "param_value": 5},
    ])
    with ledger.open("a") as f:
        f.write("{not valid json\n")
    tuner = ParamTuner.load_from_ledger(ledger, "mine_smelt_deliver", "deliver_threshold", SPEC)
    assert tuner.pulls()[5] == 1


# --- session_mean_reward -------------------------------------------------------------


def test_session_mean_reward_with_no_episodes_is_zero():
    assert session_mean_reward(EpisodicMemory()) == 0.0


def test_session_mean_reward_averages_recorded_episodes():
    mem = EpisodicMemory()
    mem.record(Episode(tick=1, kind="skill", summary="mine -> success", reward=4.0))
    mem.record(Episode(tick=2, kind="skill", summary="mine -> success", reward=2.0))
    assert session_mean_reward(mem) == 3.0
