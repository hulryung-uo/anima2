"""The fast loop must make a persona perceive → decide → act against a Body."""

import json

from anima2.agent import Agent, NullCognition
from anima2.contract import Position
from anima2.mock_body import MockBody
from anima2.persona import Persona
from anima2.planner import Planner
from anima2.skill_library import SkillLibrary
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
    """Under `MockBody` (no route driver) `GoTo` spends its bounded WalkTo
    probe budget first (issue, stall, several bounded retries — see
    `GoTo.walkto_max_retries`'s own live-calibration comment) before falling
    back to greedy stepping, hence the generous tick budget below."""
    body = MockBody()
    body.player.pos = Position(100, 100, 0)
    target = Position(105, 103, 0)
    agent = make_agent(body, goal=Goal(kind="goto", params={"target": target}))
    # Keep the goto goal alive (NullCognition passes the current goal through).
    agent.cognition = NullCognition()
    for _ in range(40):
        agent.tick()
        if (body.player.pos.x, body.player.pos.y) == (target.x, target.y):
            break
    assert (body.player.pos.x, body.player.pos.y) == (target.x, target.y)
    assert agent.memory["goto_mode"] == "greedy"  # confirms it did fall back


def test_speaking_does_not_drop_an_active_goto_goal():
    """A high-priority SpeakPending returns SUCCESS every time it voices a line;
    that must NOT clear an active goto goal (only the goal-serving GoTo may)."""
    from anima2.skills import SpeakPending

    body = MockBody()
    body.player.pos = Position(100, 100, 0)
    goal = Goal(kind="goto", params={"target": Position(105, 100, 0)})
    agent = Agent(body=body, persona=Persona(name="T"),
                  planner=Planner([SpeakPending(), GoTo(), Wander()]),
                  cognition=NullCognition(), goal=goal, cognition_interval=1)
    agent.memory["pending_say"] = "Off to richer veins!"
    agent.tick()  # SpeakPending wins this tick and returns SUCCESS
    assert body.said == ["Off to richer veins!"]
    assert agent.goal is goal  # the goto survives — GoTo can still move next tick


def test_wedged_goto_clears_the_goal_and_resumes():
    """GoTo boxed in on all sides fails; the agent drops the goal (no wall-retry loop).

    Under `MockBody` (no route driver — `WalkTo` is a silent no-op) `GoTo`
    first spends its bounded WalkTo-probe budget (issue, stall, several
    bounded retries — see `GoTo.walkto_max_retries`'s own live-calibration
    comment) before falling back to greedy stepping, which then needs its
    own `stall_limit` wedge ticks — more ticks than the pre-A* pure-greedy
    version needed, hence the larger budget below.
    """
    body = MockBody()
    body.player.pos = Position(50, 50, 0)
    body.blocked = {(50 + dx, 50 + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)} - {(50, 50)}
    goal = Goal(kind="goto", params={"target": Position(60, 50, 0)})
    agent = Agent(body=body, persona=Persona(name="T"),
                  planner=Planner([GoTo(), Wander()]),
                  cognition=NullCognition(), goal=goal, cognition_interval=1)
    for _ in range(30):
        agent.tick()
        if agent.goal is None:
            break
    assert agent.goal is None  # gave up the unreachable target
    assert agent.memory["goto_mode"] == "greedy"  # confirms it did fall back, not just time out


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


# --- skill_library=None optional collaborator (PHASE4.md item 3) ---------------


def _goto_scenario(**agent_kwargs) -> tuple[list[tuple[int, int]], list[str], float]:
    body = MockBody()
    body.player.pos = Position(100, 100, 0)
    target = Position(105, 103, 0)
    agent = Agent(body=body, persona=Persona(name="Test"), planner=Planner([GoTo(), Wander()]),
                 cognition=NullCognition(), goal=Goal(kind="goto", params={"target": target}),
                 cognition_interval=1, **agent_kwargs)
    positions = []
    for _ in range(40):
        agent.tick()
        positions.append((body.player.pos.x, body.player.pos.y))
    return positions, [e.summary for e in agent.episodes.recent(1000)], agent.episodes.total_reward()


def test_agent_skill_library_none_is_byte_for_byte_noop():
    """`Agent(skill_library=None)` — the default — must behave identically to
    every existing call site that doesn't pass `skill_library` at all: same
    trajectory, same episodic record. Proves the new guarded ledger call in
    `tick()` never fires unless a real `SkillLibrary` is wired in."""
    omitted = _goto_scenario()
    explicit_none = _goto_scenario(skill_library=None)
    assert omitted == explicit_none


def test_agent_skill_library_negative_control_idle_writes_zero_ledger_lines(tmp_path):
    """A `Wander`-only agent (always `Status.RUNNING`, `reward=0.0` — the
    exact case the episodic-recording filter already excludes) must write
    **zero** ledger lines across many ticks, proving the hook doesn't
    over-record — not just that the happy path writes correctly."""
    ledger = tmp_path / "skill_ledger.jsonl"
    lib = SkillLibrary(ledger_path=ledger)
    body = MockBody()
    body.player.pos = Position(100, 100, 0)
    agent = Agent(body=body, persona=Persona(name="Idle"), planner=Planner([Wander()]),
                 cognition=NullCognition(), cognition_interval=1,
                 skill_library=lib, profession="townsfolk")
    for _ in range(50):
        agent.tick()
    assert agent.episodes.total_recorded == 0  # sanity: the filter really excluded every tick
    assert not ledger.exists()


def test_agent_skill_library_records_rewarded_outcomes_matching_episodic_memory(tmp_path):
    """The positive-path counterpart: a rewarded/terminal skill result is
    ledgered exactly once per episodic record — same filter, same count."""
    ledger = tmp_path / "skill_ledger.jsonl"
    lib = SkillLibrary(ledger_path=ledger)
    body = MockBody()
    body.player.pos = Position(100, 100, 0)
    target = Position(105, 103, 0)  # matches test_goto_reaches_target_in_open_terrain's own budget
    agent = Agent(body=body, persona=Persona(name="T"), planner=Planner([GoTo(), Wander()]),
                 cognition=NullCognition(), goal=Goal(kind="goto", params={"target": target}),
                 cognition_interval=1, skill_library=lib, profession="townsfolk")
    # No early break: GoTo only returns Status.SUCCESS (the terminal, rewarded
    # result this test is after) on the tick *after* the position already
    # matches the target (see GoTo.step's own `here == target` check) — one
    # extra tick beyond "arrived" is needed for that to actually fire.
    for _ in range(60):
        agent.tick()
    assert agent.episodes.total_recorded > 0
    lines = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert len(lines) == agent.episodes.total_recorded
    assert all(line["profession"] == "townsfolk" for line in lines)
    assert lib.stats("goto", "townsfolk").count == agent.episodes.total_recorded
