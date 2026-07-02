"""The profession framework wires staging data + a work-skill planner."""

from anima2.contract import ItemView, Observation, PlayerView, Position
from anima2.persona import Persona
from anima2.profession import MINING_SPOTS, PROFESSIONS
from anima2.skills import GoTo, Mine, MineAndSmelt, MineSmeltDeliver
from anima2.skills.base import Goal, SkillContext


def _ctx(goal=None) -> SkillContext:
    # A pickaxe in hand so the Mine work skill is actually runnable (can_run true).
    pick = ItemView(serial=2, graphic=0x0E86, amount=1, pos=Position(2567, 493, 20),
                    container=None, layer=0, distance=0)
    obs = Observation(player=PlayerView(serial=1, pos=Position(2567, 493, 20), hits=1, hits_max=1),
                      items=[pick])
    return SkillContext(obs=obs, persona=Persona(name="Grimm"), goal=goal)


def test_miner_profession_is_calibrated():
    miner = PROFESSIONS["miner"]
    # `MineSmeltDeliver` subclasses `MineAndSmelt` and is a strict superset when
    # unconfigured (Phase 3 — see its docstring), so it's the miner's *one* work
    # skill rather than switching between two classes at wiring time.
    assert miner.needs_workplace and miner.work_skill is MineSmeltDeliver
    assert issubclass(MineSmeltDeliver, MineAndSmelt)
    assert miner.skills == {"Mining": 35}
    assert "Pickaxe" in miner.items
    assert ("Forge", 1, 1) in miner.structures  # forge staged within reach — Mine never walks


def test_miner_planner_runs_the_mine_skill():
    planner = PROFESSIONS["miner"].planner()
    assert any(isinstance(s, MineAndSmelt) for s in planner.skills)  # MineSmeltDeliver counts (subclass)


def test_townsfolk_has_no_work_skill():
    town = PROFESSIONS["townsfolk"]
    assert town.work_skill is None
    # Its planner still exists (wander + greet) but contains no Mine.
    assert not any(isinstance(s, Mine) for s in town.planner().skills)


def test_miner_planner_goto_is_inert_without_a_goal_but_preempts_work_with_one():
    planner = PROFESSIONS["miner"].planner()
    assert any(isinstance(s, GoTo) for s in planner.skills)  # workers can be steered
    # No goto goal → GoTo is skipped and the Mine skill is selected (business as usual).
    assert isinstance(planner.select(_ctx(goal=None)), Mine)
    # With a goto goal, GoTo preempts the work skill so the LLM can move the worker.
    goal = Goal(kind="goto", params={"target": Position(2570, 496, 20)})
    assert isinstance(planner.select(_ctx(goal=goal)), GoTo)


def test_enough_distinct_mining_spots_for_a_crew():
    # Workers are placed on distinct ore banks; ensure the pool isn't trivially small.
    assert len(set(MINING_SPOTS)) >= 8
