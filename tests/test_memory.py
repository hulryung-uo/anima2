"""Episodic memory store + its integration into the agent loop and cognition."""

from anima2.agent import Agent
from anima2.cognition import LLMCognition
from anima2.contract import Observation, PlayerView, Position
from anima2.llm import StubLLMClient
from anima2.memory import Episode, EpisodicMemory
from anima2.mock_body import MockBody
from anima2.persona import Persona
from anima2.planner import Planner
from anima2.skills.base import Goal, Skill, SkillContext, SkillResult, Status


def test_memory_records_recent_and_reward():
    m = EpisodicMemory(capacity=3)
    for i in range(5):
        m.record(Episode(tick=i, kind="skill", summary=f"e{i}", reward=1.0))
    assert len(m) == 3  # bounded
    assert [e.tick for e in m.recent(2)] == [3, 4]
    assert m.total_reward() == 3.0  # only the 3 retained


class _RewardOnce(Skill):
    """Emits one rewarded SUCCESS, then runs quietly."""

    name = "reward_once"

    def __init__(self) -> None:
        self.fired = False

    def step(self, ctx: SkillContext) -> SkillResult:
        if not self.fired:
            self.fired = True
            return SkillResult(Status.SUCCESS, None, reward=2.0)
        return SkillResult(Status.RUNNING, None)


def test_agent_logs_skill_outcomes():
    agent = Agent(body=MockBody(), persona=Persona(name="T"), planner=Planner([_RewardOnce()]))
    for _ in range(3):
        agent.tick()
    skills = agent.episodes.by_kind("skill")
    assert len(skills) == 1  # only the rewarded SUCCESS was logged, not the RUNNING ticks
    assert skills[0].reward == 2.0
    assert agent.episodes.total_reward() == 2.0


def test_cognition_sees_recent_episodes():
    client = StubLLMClient('{"goal": "idle"}')
    ctx = SkillContext(
        obs=Observation(player=PlayerView(serial=1, pos=Position(5, 6, 0))),
        persona=Persona(name="Grimm"),
        goal=Goal(kind="goto", params={}),
        episodes=[Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)],
    )
    LLMCognition(client).reconsider(ctx)
    # The episode summary reached the model's situation prompt.
    assert "mine" in client.calls[0][1]
