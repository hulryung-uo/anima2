"""The profession framework wires staging data + a work-skill planner."""

from anima2.contract import ItemView, Observation, PlayerView, Position
from anima2.persona import Persona
from anima2.profession import HUNTING_SPOT, MINING_SPOTS, PROFESSIONS
from anima2.skills import Blacksmith, BlacksmithMarket, GoTo, Hunt, Mine, MineAndSmelt, MineSmeltDeliver
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


def test_blacksmith_profession_is_calibrated():
    smith = PROFESSIONS["blacksmith"]
    # `BlacksmithMarket` subclasses `Blacksmith` and is a strict superset when
    # unconfigured (Phase 3 item 2 — see its docstring), so it's the
    # blacksmith's *one* work skill rather than switching between two classes.
    assert smith.needs_workplace and smith.work_skill is BlacksmithMarket
    assert issubclass(BlacksmithMarket, Blacksmith)
    assert smith.skills == {"Blacksmith": 35}
    assert ("Forge", 0, -1) in smith.structures and ("Anvil", 0, 1) in smith.structures


def test_blacksmith_planner_runs_the_craft_skill():
    planner = PROFESSIONS["blacksmith"].planner()
    assert any(isinstance(s, Blacksmith) for s in planner.skills)  # BlacksmithMarket counts (subclass)


def test_hunter_profession_is_calibrated():
    hunter = PROFESSIONS["hunter"]
    assert hunter.needs_workplace and hunter.work_skill is Hunt
    assert hunter.workplace == HUNTING_SPOT
    # A single shared, calibrated field (like TRADE_SMITH_SPOT), not a
    # per-agent pool like MINING_SPOTS/FISHING_SPOTS.
    assert HUNTING_SPOT not in MINING_SPOTS
    assert hunter.skills.get("Wrestling", 0) > 0  # bare-handed — no weapon staged
    assert "Pickaxe" not in hunter.items and hunter.items == []
    assert hunter.combat_disposition != "pacifist"


def test_hunter_planner_runs_the_hunt_skill():
    planner = PROFESSIONS["hunter"].planner()
    assert any(isinstance(s, Hunt) for s in planner.skills)


def test_hunter_persona_carries_combat_disposition():
    from anima2.village import _persona_for

    persona = _persona_for(PROFESSIONS["hunter"], 0)
    assert persona.combat_disposition == "aggressive"
    # Default rosters (no hunter opted in) are untouched: an existing
    # profession's persona still gets the unchanged "neutral" default.
    miner_persona = _persona_for(PROFESSIONS["miner"], 0)
    assert miner_persona.combat_disposition == "neutral"
