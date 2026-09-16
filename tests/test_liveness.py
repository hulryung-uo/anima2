from anima2.contract import Position
from anima2.liveness import ApproachWatch
from anima2.skills.market import WalkDestination

import pytest


def test_moving_sideways_is_not_progress_and_reports_are_throttled():
    watch = ApproachWatch(threshold=10)
    target = WalkDestination("sell", 10, 10, 2)
    reports = []
    for tick in range(35):
        result = watch.observe(target, Position(tick % 2, 0, 0), tick,
                               attempted=True, output=0)
        if result:
            reports.append((tick, result))
    assert [tick for tick, _ in reports] == [10, 20, 30]
    assert reports[0][1] == (10, 10, 10, 10)


def test_revisiting_an_old_best_does_not_reset_a_failed_approach():
    watch = ApproachWatch(threshold=10)
    target = WalkDestination("sell", 10, 0, 2)
    reports = []
    for tick in range(20):
        # One real step of progress, then infinite backwards/forwards movement.
        pos = Position(1 if tick % 2 else 0, 0, 0)
        if watch.observe(target, pos, tick, attempted=True, output=0):
            reports.append(tick)
    assert reports == [11]


def test_trip_retries_do_not_erase_the_destination_history():
    watch = ApproachWatch(threshold=12)
    target = WalkDestination("sell", 10, 0, 2)
    reports = []
    for tick in range(25):
        destination = None if tick % 4 == 3 else target
        if watch.observe(destination, Position(), tick, attempted=destination is not None,
                         output=0):
            reports.append(tick)
    assert reports == [12, 24]


def test_actual_progress_arrival_and_output_reset_the_watch():
    watch = ApproachWatch(threshold=10)
    target = WalkDestination("sell", 100, 0, 2)
    for tick in range(80):
        assert watch.observe(target, Position(tick, 0, 0), tick,
                             attempted=True, output=0) is None
    for tick in range(80, 100):
        assert watch.observe(target, Position(80, 0, 0), tick,
                             attempted=True, output=tick // 8) is None
    watch.observe(target, Position(98, 0, 0), 100, attempted=True, output=12)
    assert not watch._routes


def test_rare_walks_and_old_destinations_are_not_a_wedge():
    watch = ApproachWatch(threshold=10, capacity=3)
    target = WalkDestination("sell", 10, 0, 2)
    for tick in range(50):
        assert watch.observe(target, Position(), tick, attempted=tick < 2, output=0) is None
    for tick in range(50, 60):
        watch.observe(WalkDestination("sell", tick, 0, 2), Position(), tick,
                      attempted=True, output=0)
    assert len(watch._routes) == 3
    assert watch.observe(target, Position(), 100, attempted=True, output=0) is None


def test_worker_reports_a_moving_market_wedge(capsys):
    import threading

    from anima2.agent import Agent
    from anima2.contract import Walk
    from anima2.mock_body import MockBody
    from anima2.persona import Persona
    from anima2.planner import Planner
    from anima2.skills.base import Skill, SkillResult, Status
    from anima2.village import _run_worker

    class Oscillate(Skill):
        def step(self, ctx):
            direction = 2 if ctx.obs.player.pos.x == 50 else 6
            return SkillResult(Status.RUNNING, Walk(dir=direction))

    body = MockBody()
    body.player.pos = Position(50, 50, 0)
    agent = Agent(body, Persona(name="Blocked"), Planner([Oscillate()]))
    agent.memory.update(mkt_phase="sell", vendor_spot=(60, 60))
    _run_worker(agent, 500, 0, {}, threading.Lock(), "carpenter")
    output = capsys.readouterr().out
    assert output.count("WEDGED WALK") == 2
    assert "no closer to (60,60)" in output
    assert "position never changed" not in output


@pytest.mark.parametrize("succeeds", [True, False])
def test_stationary_economy_success_is_progress_but_failed_frames_are_not(capsys, succeeds):
    """Reproduce the live tinker: hunt reward stays frozen while economy frames close.

    Drive the actual worker and goal histories separately, as on a two-agent Life.
    Repeated failed transactions must still warn; merely recording episodes is no proof.
    """
    import threading

    from anima2.agent import Agent
    from anima2.goals import GoalOutcome
    from anima2.mock_body import MockBody
    from anima2.persona import Persona
    from anima2.planner import Planner
    from anima2.skills.base import Goal
    from anima2.skills.movement import Wander
    from anima2.village import _run_worker

    class StationaryLife:
        def __init__(self):
            self.body = MockBody()
            self.persona = Persona(name="Productive" if succeeds else "Failing")
            self.hunt_agent = Agent(self.body, self.persona, Planner([Wander()]))
            self.econ_agent = Agent(self.body, self.persona, Planner([Wander()]))
            self.memory = self.hunt_agent.memory
            self.episodes = self.hunt_agent.episodes
            self.ticks = 0

        def tick(self):
            self.ticks += 1
            if self.ticks % 20 == 0:
                stack = self.econ_agent.goal_stack
                stack.push(Goal("capability", {"capability": "sell_tongs"}), tick=self.ticks)
                outcome = GoalOutcome.SUCCESS if succeeds else GoalOutcome.FAILURE
                stack.finish(outcome, tick=self.ticks)
            return None

    life = StationaryLife()
    _run_worker(life, 500, 0, {}, threading.Lock(), "tinker")
    output = capsys.readouterr().out
    assert life.episodes.total_reward() == 0
    assert output.count("FRAME RETIRED") == 25
    assert ("NO PROGRESS" not in output) == succeeds
    assert "WEDGED WALK" not in output
