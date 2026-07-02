"""The reflection loop: cadence, producers (heuristic + LLM), and consumption.

Covers PHASE2.md B1: `ReflectingCognition` fires from inside `reconsider()` (the
slow loop only), the two `ReflectionProducer`s behind that interface, and that a
recorded `Insight` shows up in `LLMCognition`'s situation prompt on a later call —
first in isolation, then through a real `Agent` run.
"""

from __future__ import annotations

from anima2.agent import Agent
from anima2.cognition import (
    HeuristicCognition,
    HeuristicReflection,
    LLMCognition,
    LLMReflection,
    ReflectingCognition,
    ThreadedCognition,
)
from anima2.contract import Observation, PlayerView, Position
from anima2.llm import StubLLMClient
from anima2.memory import Episode
from anima2.mock_body import MockBody
from anima2.persona import Persona
from anima2.planner import Planner
from anima2.skills.base import Skill, SkillContext, SkillResult, Status


def _ctx(*, episodes=None, episode_count: int = 0, goal=None) -> SkillContext:
    obs = Observation(player=PlayerView(serial=1, pos=Position(3724, 2212, 20), hits=80, hits_max=80))
    return SkillContext(obs=obs, persona=Persona(name="Grimm", title="a miner"),
                        goal=goal, episodes=episodes or [], episode_count=episode_count)


class _CountingReflection:
    """A `ReflectionProducer` stub that records each call, for cadence tests."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def reflect(self, episodes, persona) -> list[str]:
        self.calls.append(len(episodes))
        return [f"insight #{len(self.calls)}"]


# --- cadence -----------------------------------------------------------------


def test_reflecting_cognition_fires_every_n_reconsiders():
    reflection = _CountingReflection()
    cog = ReflectingCognition(HeuristicCognition(), reflection,
                               every_n_reconsiders=3, min_new_episodes=1000)
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]
    ctx = _ctx(episodes=episodes, episode_count=1)

    cog.reconsider(ctx)
    cog.reconsider(ctx)
    assert reflection.calls == []  # only 2 reconsiders so far — not due yet

    cog.reconsider(ctx)
    assert reflection.calls == [1]  # 3rd reconsider crosses the threshold
    assert len(cog.insights) == 1


def test_reflecting_cognition_fires_on_new_episode_threshold():
    reflection = _CountingReflection()
    cog = ReflectingCognition(HeuristicCognition(), reflection,
                               every_n_reconsiders=1000, min_new_episodes=5)

    ctx = _ctx(episodes=[Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)],
               episode_count=4)
    cog.reconsider(ctx)
    assert reflection.calls == []  # 4 new episodes < 5

    ctx2 = _ctx(episodes=[Episode(tick=2, kind="skill", summary="mine → success", reward=1.0)],
                episode_count=5)
    cog.reconsider(ctx2)
    assert reflection.calls == [1]  # 5 new episodes >= 5 → fires


def test_reflecting_cognition_never_fires_on_empty_episodes():
    reflection = _CountingReflection()
    cog = ReflectingCognition(HeuristicCognition(), reflection, every_n_reconsiders=1)
    ctx = _ctx(episodes=[], episode_count=0)
    for _ in range(5):
        cog.reconsider(ctx)
    assert reflection.calls == []  # nothing to reflect on yet


# --- heuristic producer (offline default, no LLM) -----------------------------


def test_heuristic_reflection_aggregates_reward_and_failures():
    episodes = [
        Episode(tick=1, kind="skill", summary="mine → success", reward=1.0),
        Episode(tick=2, kind="skill", summary="mine → success", reward=1.0),
        Episode(tick=3, kind="skill", summary="smelt → failure", reward=0.0),
    ]
    insights = HeuristicReflection().reflect(episodes, Persona(name="Grimm"))
    assert len(insights) == 2
    assert "mine" in insights[0] and "+2.0" in insights[0]
    assert "smelt" in insights[1] and "1 setback" in insights[1]


def test_heuristic_reflection_quiet_stretch_when_nothing_stands_out():
    episodes = [Episode(tick=1, kind="skill", summary="wander → success", reward=0.0)]
    insights = HeuristicReflection().reflect(episodes, Persona(name="Grimm"))
    assert len(insights) == 1
    assert "quiet stretch" in insights[0].lower()


def test_heuristic_reflection_handles_no_episodes():
    assert HeuristicReflection().reflect([], Persona(name="Grimm")) == []


# --- LLM producer (existing stub-client pattern) -------------------------------


def test_llm_reflection_parses_json_array_via_stub_client():
    client = StubLLMClient('["The east vein paid better.", "Bandits circle near dusk."]')
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]
    insights = LLMReflection(client).reflect(episodes, Persona(name="Grimm"))
    assert insights == ["The east vein paid better.", "Bandits circle near dusk."]
    assert "Grimm" in client.calls[0][0]  # the persona reached the system prompt


def test_llm_reflection_falls_back_to_heuristic_on_unparseable_response():
    client = StubLLMClient("sorry, I cannot help with that")
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=2.0)]
    insights = LLMReflection(client).reflect(episodes, Persona(name="Grimm"))
    assert insights and "mine" in insights[0]  # heuristic fallback kicked in


# --- consumption: insights reach the LLMCognition situation prompt ------------


def test_insight_reaches_next_situation_prompt():
    client = StubLLMClient('{"goal": "idle"}')
    reflecting = ReflectingCognition(LLMCognition(client, job="miner"), HeuristicReflection(),
                                      every_n_reconsiders=1, min_new_episodes=1000)
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=3.0)]
    ctx = _ctx(episodes=episodes, episode_count=1)

    reflecting.reconsider(ctx)  # reflects this round, but too late for this prompt
    assert "Lessons learned: (nothing yet)" in client.calls[0][1]

    reflecting.reconsider(ctx)  # next round: the fresh insight is now in the prompt
    assert "mine has paid off" in client.calls[1][1]


def test_reflecting_cognition_composes_with_threaded_cognition():
    """Production wraps `ThreadedCognition(ReflectingCognition(inner))` — reflection
    must survive running on the background thread ThreadedCognition drives."""
    import threading

    reflection = HeuristicReflection()
    reflecting = ReflectingCognition(HeuristicCognition(), reflection, every_n_reconsiders=1)
    threaded = ThreadedCognition(reflecting)
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]
    ctx = _ctx(episodes=episodes, episode_count=1)

    threaded.reconsider(ctx)  # kicks off the background thread
    for _ in range(200):
        if len(reflecting.insights) > 0:
            break
        threading.Event().wait(0.005)
    assert len(reflecting.insights) == 1


# --- agent-loop integration ----------------------------------------------------


class _RewardEachTick(Skill):
    """A trivial work skill: succeeds with reward every tick, so episodes pile up
    fast enough to exercise reflection within a handful of agent ticks."""

    name = "grind"

    def step(self, ctx: SkillContext) -> SkillResult:
        return SkillResult(Status.SUCCESS, None, reward=1.0)


def test_agent_loop_reflection_fires_and_reaches_prompt():
    client = StubLLMClient('{"goal": "idle"}')
    reflecting = ReflectingCognition(LLMCognition(client, job="miner"), HeuristicReflection(),
                                      every_n_reconsiders=3, min_new_episodes=1000)
    agent = Agent(body=MockBody(), persona=Persona(name="Grimm"),
                  planner=Planner([_RewardEachTick()]), cognition=reflecting, cognition_interval=1)

    for _ in range(4):
        agent.tick()

    assert len(reflecting.insights) == 1  # cadence fired once (every 3rd reconsider)
    # The insight from that reflection shows up in the very next situation prompt.
    assert "grind has paid off" in client.calls[-1][1]
