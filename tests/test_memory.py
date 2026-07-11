"""Episodic memory store + its integration into the agent loop and cognition."""

import json

from anima2.agent import Agent
from anima2.cognition import LLMCognition
from anima2.contract import Observation, PlayerView, Position
from anima2.llm import StubLLMClient
from anima2.memory import Episode, EpisodicMemory, Insight, ReflectionMemory, load_insights
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


# --- PHASE6.md item 1: disk-backed ReflectionMemory (persist_path/agent_key/load_insights) ---


def test_reflection_memory_persist_path_none_is_byte_for_byte_noop(monkeypatch):
    """The default (`persist_path=None`, every caller before this item):
    `_persist` must never even be called — not just "no file appears", the
    load-bearing "zero file I/O at all" claim, verified directly rather than
    inferred from a missing file."""
    calls = []
    monkeypatch.setattr(ReflectionMemory, "_persist", lambda self, insight: calls.append(insight))
    mem = ReflectionMemory()
    mem.record(Insight(text="a", episode_ticks=(1, 2), episode_count=2))
    mem.record(Insight(text="b", episode_ticks=(3, 4), episode_count=2))
    assert calls == []
    assert [i.text for i in mem.recent(2)] == ["a", "b"]  # in-memory behavior unchanged
    assert mem.persist_path is None


def test_reflection_memory_record_appends_well_formed_json_line(tmp_path):
    path = tmp_path / "insights.jsonl"
    mem = ReflectionMemory(persist_path=path, agent_key="Grimm0")
    mem.record(Insight(text="The east vein pays better in the morning.",
                        episode_ticks=(5, 9), episode_count=4))

    lines = path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["agent_key"] == "Grimm0"
    assert record["text"] == "The east vein pays better in the morning."
    assert record["episode_ticks"] == [5, 9]
    assert record["episode_count"] == 4
    assert "ts" in record


def test_load_insights_skips_corrupted_trailing_line(tmp_path):
    path = tmp_path / "insights.jsonl"
    good = json.dumps({"ts": "x", "agent_key": "Grimm0", "text": "good insight",
                        "episode_ticks": [1, 2], "episode_count": 2})
    path.write_text(good + "\n" + '{"agent_key": "Grimm0", "text": "trunc')  # hand-corrupted trailing line

    mem = load_insights(path, "Grimm0")
    assert [i.text for i in mem.recent(5)] == ["good insight"]


def test_reflection_memory_two_instances_same_ledger_see_each_others_writes(tmp_path):
    """The load-bearing case: two separate `ReflectionMemory`s (the second
    standing in for a fresh process restart, via `load_insights`) pointed at
    the same `persist_path`/`agent_key` see each other's writes — mirrors
    `skill_library.py`'s own "two instances, same ledger" persistence proof."""
    path = tmp_path / "insights.jsonl"
    mem_a = ReflectionMemory(persist_path=path, agent_key="Grimm0")
    mem_a.record(Insight(text="first insight", episode_ticks=(1, 5), episode_count=5))

    mem_b = load_insights(path, "Grimm0")  # simulates a fresh process reading the file back
    assert [i.text for i in mem_b.recent(5)] == ["first insight"]

    mem_b.record(Insight(text="second insight", episode_ticks=(6, 10), episode_count=5))
    mem_c = load_insights(path, "Grimm0")  # a second restart
    assert [i.text for i in mem_c.recent(5)] == ["first insight", "second insight"]


def test_load_insights_filters_strictly_by_agent_key(tmp_path):
    path = tmp_path / "insights.jsonl"
    mem_grimm = ReflectionMemory(persist_path=path, agent_key="Grimm0")
    mem_marina = ReflectionMemory(persist_path=path, agent_key="Marina0")
    mem_grimm.record(Insight(text="grimm insight 1", episode_ticks=(1, 2), episode_count=2))
    mem_marina.record(Insight(text="marina insight 1", episode_ticks=(1, 2), episode_count=2))
    mem_grimm.record(Insight(text="grimm insight 2", episode_ticks=(3, 4), episode_count=2))

    loaded = load_insights(path, "Grimm0")
    assert [i.text for i in loaded.recent(5)] == ["grimm insight 1", "grimm insight 2"]

    loaded_marina = load_insights(path, "Marina0")
    assert [i.text for i in loaded_marina.recent(5)] == ["marina insight 1"]


def test_load_insights_caps_at_capacity_keeping_most_recent(tmp_path):
    path = tmp_path / "insights.jsonl"
    mem = ReflectionMemory(capacity=100, persist_path=path, agent_key="Grimm0")
    for i in range(5):
        mem.record(Insight(text=f"insight {i}", episode_ticks=(i, i), episode_count=1))

    loaded = load_insights(path, "Grimm0", capacity=3)
    assert [i.text for i in loaded.recent(3)] == ["insight 2", "insight 3", "insight 4"]
    assert len(loaded) == 3


def test_load_insights_missing_file_yields_empty_correctly_wired_memory(tmp_path):
    """The fresh-persona case: a missing `persist_path` file is not an error,
    and the returned `ReflectionMemory` still keeps appending to it."""
    path = tmp_path / "does_not_exist.jsonl"
    mem = load_insights(path, "Grimm0")
    assert mem.recent(5) == []
    assert mem.persist_path == path
    assert mem.agent_key == "Grimm0"

    mem.record(Insight(text="first ever", episode_ticks=(1, 1), episode_count=1))
    assert path.exists()
    assert json.loads(path.read_text().splitlines()[0])["text"] == "first ever"


def test_load_insights_agent_key_is_exact_equality_not_prefix(tmp_path):
    """Adversarial pair from review: personas whose names share a prefix
    ("Grimm" vs "Grimm2") must never see each other's insights — the filter is
    strict equality, not startswith."""
    path = tmp_path / "insights.jsonl"
    ReflectionMemory(persist_path=path, agent_key="Grimm").record(
        Insight(text="mine the east vein", episode_ticks=(1, 5), episode_count=3))
    ReflectionMemory(persist_path=path, agent_key="Grimm2").record(
        Insight(text="fish the west shore", episode_ticks=(2, 6), episode_count=2))

    grimm = load_insights(path, "Grimm")
    grimm2 = load_insights(path, "Grimm2")
    assert [i.text for i in grimm.recent(5)] == ["mine the east vein"]
    assert [i.text for i in grimm2.recent(5)] == ["fish the west shore"]
