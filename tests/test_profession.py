"""The profession framework wires staging data + a work-skill planner."""

from anima2.profession import MINING_SPOTS, PROFESSIONS
from anima2.skills import Mine


def test_miner_profession_is_calibrated():
    miner = PROFESSIONS["miner"]
    assert miner.needs_workplace and miner.work_skill is Mine
    assert miner.skills == {"Mining": 35}
    assert "Pickaxe" in miner.items


def test_miner_planner_runs_the_mine_skill():
    planner = PROFESSIONS["miner"].planner()
    assert any(isinstance(s, Mine) for s in planner.skills)


def test_townsfolk_has_no_work_skill():
    town = PROFESSIONS["townsfolk"]
    assert town.work_skill is None
    # Its planner still exists (wander + greet) but contains no Mine.
    assert not any(isinstance(s, Mine) for s in town.planner().skills)


def test_enough_distinct_mining_spots_for_a_crew():
    # Workers are placed on distinct ore banks; ensure the pool isn't trivially small.
    assert len(set(MINING_SPOTS)) >= 8
