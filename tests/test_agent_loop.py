"""The fast loop must make a persona perceive → decide → act against a Body."""

from anima2.agent import Agent, NullCognition
from anima2.contract import Position
from anima2.mock_body import MockBody
from anima2.persona import Persona
from anima2.planner import Planner
from anima2.skills import GoTo, Wander
from anima2.skills.base import Goal


def make_agent(body: MockBody, cognition=None, goal=None) -> Agent:
    return Agent(
        body=body,
        persona=Persona(name="Test"),
        planner=Planner([GoTo(), Wander()]),
        cognition=cognition,
        goal=goal,
        cognition_interval=1,
    )


def test_wander_moves_the_player():
    body = MockBody()
    body.player.pos = Position(100, 100, 0)
    agent = make_agent(body)  # no goal → Wander fallback
    start = (body.player.pos.x, body.player.pos.y)
    for _ in range(5):
        agent.tick()
    assert (body.player.pos.x, body.player.pos.y) != start


def test_goto_reaches_target_in_open_terrain():
    body = MockBody()
    body.player.pos = Position(100, 100, 0)
    target = Position(105, 103, 0)
    agent = make_agent(body, goal=Goal(kind="goto", params={"target": target}))
    # Keep the goto goal alive (NullCognition passes the current goal through).
    agent.cognition = NullCognition()
    for _ in range(20):
        agent.tick()
        if (body.player.pos.x, body.player.pos.y) == (target.x, target.y):
            break
    assert (body.player.pos.x, body.player.pos.y) == (target.x, target.y)


def test_wander_turns_when_blocked():
    body = MockBody()
    body.player.pos = Position(10, 10, 0)
    # Wall everything east so the first East step is denied → wander must turn.
    body.blocked = {(11, 10), (11, 9), (11, 11)}
    agent = make_agent(body)
    moved = False
    for _ in range(12):
        agent.tick()
        if (body.player.pos.x, body.player.pos.y) != (10, 10):
            moved = True
            break
    assert moved  # eventually found an open direction


def test_wander_retries_direction_before_rotating():
    """A direction change in UO costs a turn (no move), so Wander must give each
    direction a real step (2 ticks) before rotating — else it spins in place."""
    from anima2.contract import Observation, PlayerView, Position
    from anima2.skills.base import SkillContext

    skill = Wander()
    mem: dict = {}

    def at(pos):  # stay at the same tile → "didn't move"
        obs = Observation(player=PlayerView(serial=1, pos=Position(*pos)))
        return SkillContext(obs=obs, persona=Persona(name="T"), memory=mem)

    a = skill.step(at((10, 10)))  # first tick: pick dir
    b = skill.step(at((10, 10)))  # still here (turn) — same dir, don't rotate yet
    assert a.action.dir == b.action.dir
    c = skill.step(at((10, 10)))  # still stuck after a real step attempt → rotate
    assert c.action.dir != b.action.dir


def test_say_is_recorded():
    body = MockBody()
    from anima2.contract import Say

    body.act(Say(text="hail"))
    assert body.said == ["hail"]
    # And it shows up in the next observation's journal.
    obs = body.observe()
    assert any(j.text == "hail" for j in obs.new_journal)
