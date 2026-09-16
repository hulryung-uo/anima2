import pytest

from anima2.foundry.life_eval import LifeResult
from anima2.foundry.life_search import search
from anima2.life_config import LifeProfile


def test_search_constructs_real_lives_and_reports_flat_axes():
    result = search(LifeProfile("swordsman", {}),
                    {"bank_reserve": (129, 400), "econ_grace": (2, 6)}, budget=5)
    assert len(result.trials) == 5
    assert all(trial.result.achieved == 1 for trial in result.trials)
    assert result.best.knobs["bank_reserve"] == 129
    assert result.axis_evidence["bank_reserve"]["effect"] == "varying"
    assert result.axis_evidence["econ_grace"]["effect"] == "flat_in_observed_pairs"
    assert result.axis_evidence["econ_grace"]["complete_contexts"] == 2
    for trial in result.trials:
        assert trial.result.banked + trial.result.pack_gold == 2000


def test_budget_seed_and_best_profile_are_reproducible():
    calls = []

    def evaluate(profile):
        calls.append(profile)
        return LifeResult(1000 - profile.knobs.get("bank_reserve", 300), 0, 1, 1, 20)

    axes = {"bank_reserve": tuple(range(100)), "econ_grace": (2, 4, 8)}
    first = search(LifeProfile("tinker", {}), axes, budget=7, seed=42, evaluator=evaluate)
    assert len(calls) == 7
    second = search(LifeProfile("tinker", {}), axes, budget=7, seed=42, evaluator=evaluate)
    assert first.as_dict() == second.as_dict()
    assert len({tuple(sorted(p.knobs.items())) for p in calls[:7]}) == 7


def test_inert_or_broken_trials_do_not_export_a_winner():
    result = search(LifeProfile("swordsman", {}), {"bank_reserve": (10, 20)}, budget=1,
                    evaluator=lambda profile: LifeResult(100, 0, 0, 0, 10))
    assert result.best is None
    assert result.axis_evidence["bank_reserve"]["effect"] == "unmeasured"


@pytest.mark.parametrize("axes,budget", [
    ({"profession": (1,)}, 2), ({"bank_reserve": (1, True)}, 2),
    ({"bank_reserve": ()}, 2), ({"bank_reserve": (-1,)}, 2),
    ({"bank_reserve": (1,)}, 0),
])
def test_invalid_searches_do_not_evaluate(axes, budget):
    with pytest.raises(ValueError):
        search(LifeProfile("swordsman", {}), axes, budget=budget,
               evaluator=lambda p: pytest.fail("invalid search evaluated"))


def test_the_builtin_scenario_does_not_claim_to_measure_other_professions():
    with pytest.raises(ValueError, match="only swordsman"):
        search(LifeProfile("mage", {}), {"bank_reserve": (129, 400)})
