"""The reflection loop: cadence, producers (heuristic + LLM), and consumption.

Covers PHASE2.md B1: `ReflectingCognition` fires from inside `reconsider()` (the
slow loop only), the two `ReflectionProducer`s behind that interface, and that a
recorded `Insight` shows up in `LLMCognition`'s situation prompt on a later call —
first in isolation, then through a real `Agent` run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from anima2.agent import Agent
from anima2.cognition import (
    HeuristicCognition,
    HeuristicReflection,
    LLMCognition,
    LLMReflection,
    LLMWikiReportProducer,
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
from anima2.wiki import Wiki

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "wiki"


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
    assert cog.wait_idle(timeout=2.0)  # deterministic join: let that pass finish
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
    assert cog.wait_idle(timeout=2.0)  # deterministic join: let that pass finish
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


# --- LLM insight screening (mirrors LLMCognition._queue_say / forum._broke_character) --


def test_llm_reflection_drops_character_breaking_insights():
    # Insights persist and are replayed into every later situation prompt, so the
    # same "never break character" defense as in-game speech applies here too.
    client = StubLLMClient(
        '["I am an AI language model and cannot reflect on this.", '
        '"The east vein paid better than the west one."]'
    )
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]
    insights = LLMReflection(client).reflect(episodes, Persona(name="Grimm"))
    assert insights == ["The east vein paid better than the west one."]


def test_llm_reflection_falls_back_when_every_insight_breaks_character():
    client = StubLLMClient('["As an AI, I cannot form personal takeaways."]')
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=2.0)]
    insights = LLMReflection(client).reflect(episodes, Persona(name="Grimm"))
    assert insights and "mine" in insights[0]  # nothing usable survived → heuristic fallback


def test_llm_reflection_clamps_long_insights():
    long_line = "x" * 300
    client = StubLLMClient(f'["{long_line}"]')
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]
    insights = LLMReflection(client).reflect(episodes, Persona(name="Grimm"))
    assert insights == [long_line[:200]]
    assert len(insights[0]) == 200


def test_llm_reflection_collapses_whitespace_in_insights():
    client = StubLLMClient('["Thin  veins\\n\\ttoday,   watch  the  weather."]')
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]
    insights = LLMReflection(client).reflect(episodes, Persona(name="Grimm"))
    assert insights == ["Thin veins today, watch the weather."]


# --- consumption: insights reach the LLMCognition situation prompt ------------


def test_insight_reaches_next_situation_prompt():
    client = StubLLMClient('{"goal": "idle"}')
    reflecting = ReflectingCognition(LLMCognition(client, job="miner"), HeuristicReflection(),
                                      every_n_reconsiders=1, min_new_episodes=1000)
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=3.0)]
    ctx = _ctx(episodes=episodes, episode_count=1)

    reflecting.reconsider(ctx)  # kicks off reflection on its own thread — too late for this prompt
    assert "Lessons learned: (nothing yet)" in client.calls[0][1]
    assert reflecting.wait_idle(timeout=2.0)  # deterministic join: let that pass finish

    reflecting.reconsider(ctx)  # next round: the fresh insight is now in the prompt
    assert "mine has paid off" in client.calls[1][1]


def test_reflecting_cognition_composes_with_threaded_cognition():
    """Production wraps `ThreadedCognition(ReflectingCognition(inner))` — reflection
    must survive running on the background thread ThreadedCognition drives. Both
    layers expose `wait_idle()` for a deterministic join instead of a sleep/poll
    loop (reflection itself also runs off its own thread — see `_reflect_bg`)."""
    reflection = HeuristicReflection()
    reflecting = ReflectingCognition(HeuristicCognition(), reflection, every_n_reconsiders=1)
    threaded = ThreadedCognition(reflecting)
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]
    ctx = _ctx(episodes=episodes, episode_count=1)

    threaded.reconsider(ctx)  # kicks off ThreadedCognition's background thread
    assert threaded.wait_idle(timeout=2.0)  # `reconsider` landed — by now the due
    # reflection (if any) has already been kicked off on its own thread too.
    assert reflecting.wait_idle(timeout=2.0)  # ...now wait for that reflection pass itself.
    assert len(reflecting.insights) == 1


def test_reflecting_cognition_returns_goal_before_reflection_completes():
    """Reflection must not sit on the goal-delivery path: `reconsider()` returns as
    soon as `inner.reconsider()` is done, even while a due reflection pass is still
    running on its own thread (mirrors `test_threaded_cognition_never_blocks`'s
    gate pattern for the analogous `ThreadedCognition` guarantee)."""
    import threading

    gate = threading.Event()

    class SlowReflection:
        def reflect(self, episodes, persona):
            gate.wait(2.0)  # block until the test releases us
            return ["done reflecting"]

    reflecting = ReflectingCognition(HeuristicCognition(), SlowReflection(), every_n_reconsiders=1)
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]
    ctx = _ctx(episodes=episodes, episode_count=1)  # no goal set

    goal = reflecting.reconsider(ctx)  # reflection is due but must not block on SlowReflection

    assert goal is None  # HeuristicCognition just passed ctx.goal (None) through
    # Structurally proves reconsider() didn't wait for SlowReflection: the gate is
    # still closed, so if reflection had run inline this would still be empty only
    # by chance — instead it's a hard guarantee, since SlowReflection can't have
    # returned yet.
    assert len(reflecting.insights) == 0

    gate.set()
    assert reflecting.wait_idle(timeout=2.0)
    assert len(reflecting.insights) == 1
    assert reflecting.insights.recent(1)[0].text == "done reflecting"


def test_reflecting_cognition_skips_reflection_when_one_already_in_flight():
    """The non-overlap guard: a due reflection round while one is still in flight is
    skipped (not queued/stacked) — cadence counters stay put so the round isn't
    lost, it's simply retried once the in-flight pass finishes."""
    import threading

    gate = threading.Event()
    started = threading.Event()  # set once `reflect()` has actually begun — the
    # deterministic join `assert started.wait(...)` needs to prove pass #1 is
    # genuinely in flight before round 2 fires, without a sleep/poll loop.
    calls = []

    class SlowReflection:
        def reflect(self, episodes, persona):
            calls.append(len(episodes))
            started.set()
            gate.wait(2.0)  # block until the test releases us
            return [f"pass #{len(calls)}"]

    reflecting = ReflectingCognition(HeuristicCognition(), SlowReflection(),
                                      every_n_reconsiders=1, min_new_episodes=1000)
    ep1 = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]
    ctx1 = _ctx(episodes=ep1, episode_count=1)

    reflecting.reconsider(ctx1)  # starts pass #1, blocked on `gate`
    assert started.wait(2.0)
    assert len(calls) == 1

    # A second episode lands while pass #1 is still in flight — due again (a fresh
    # episode + every_n_reconsiders=1), but the guard must skip it rather than run
    # `reflect()` concurrently.
    ep2 = ep1 + [Episode(tick=2, kind="skill", summary="mine → success", reward=1.0)]
    ctx2 = _ctx(episodes=ep2, episode_count=2)
    reflecting.reconsider(ctx2)
    assert len(calls) == 1  # no second (concurrent) `reflect()` call

    gate.set()
    assert reflecting.wait_idle(timeout=2.0)
    assert len(reflecting.insights) == 1  # only pass #1 ever completed

    # Cadence wasn't consumed by the skipped round — the next due round still
    # fires, and it's the one that picks up the episode that arrived while pass #1
    # was busy (nothing was silently dropped).
    gate.clear()
    started.clear()
    reflecting.reconsider(ctx2)
    assert started.wait(2.0)
    assert len(calls) == 2
    gate.set()
    assert reflecting.wait_idle(timeout=2.0)
    assert len(reflecting.insights) == 2


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

    for _ in range(3):
        agent.tick()
    # Tick 3's reconsider kicked reflection off on its own thread (see
    # `ReflectingCognition._reflect_bg`) — join deterministically before asserting.
    assert reflecting.wait_idle(timeout=2.0)
    assert len(reflecting.insights) == 1  # cadence fired once (every 3rd reconsider)

    agent.tick()  # the next reconsider call — the fresh insight is now in its prompt
    assert "grind has paid off" in client.calls[-1][1]


def test_agent_loop_episode_window_reaches_reflection_beyond_eight():
    """Regression: `Agent`'s per-tick `SkillContext.episodes` slice must not
    silently cap the window `ReflectingCognition.episode_window` asks for — it used
    to be hardcoded at `recent(8)` regardless of `episode_window`. The real `Agent`
    path must deliver more than 8 episodes to the reflection producer once that
    many have accumulated (proves `Agent.episodes_window` actually reaches it)."""
    reflection = _CountingReflection()
    reflecting = ReflectingCognition(HeuristicCognition(), reflection,
                                      every_n_reconsiders=1000, min_new_episodes=12,
                                      episode_window=20)
    agent = Agent(body=MockBody(), persona=Persona(name="Grimm"),
                  planner=Planner([_RewardEachTick()]), cognition=reflecting, cognition_interval=1)

    for _ in range(13):
        agent.tick()
    assert reflecting.wait_idle(timeout=2.0)

    assert reflection.calls == [12]  # all 12 accumulated episodes reached the
    # producer in one window — not capped at the old hardcoded 8.


# --- wiki write loop wiring (PHASE4.md item 1) ---------------------------------
#
# `ReflectingCognition(..., wiki_reporter=...)`: right after a reflection pass
# succeeds, an optional `WikiReportProducer` may propose a `ReportDraft`, which
# `_reflect_bg` hands to `Wiki.file_report()`. `wiki_reporter=None` (the
# default, exercised everywhere above this section) must stay a byte-for-byte
# no-op — these tests cover the opt-in path specifically.


def _git_wiki(tmp_path: Path, *, cooldown_s: float = 3600.0) -> Wiki:
    """A `Wiki` reading the shared fixture pages (a real `skills/mining` page)
    but writing/committing into a fresh, disposable git repo under `tmp_path`
    — the two independent `root`/`repo_root` knobs PHASE4.md item 1 adds."""
    repo = tmp_path / "wikirepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return Wiki(root=FIXTURE_ROOT, repo_root=repo, report_cooldown_s=cooldown_s)


def _open_reports(wiki: Wiki) -> list[Path]:
    open_dir = wiki.repo_root / "reports" / "open"
    return sorted(open_dir.glob("*.md")) if open_dir.exists() else []


def test_reflecting_cognition_wiki_reporter_none_is_byte_for_byte_noop(tmp_path, monkeypatch):
    """`wiki_reporter=None` (the default) must never touch `Wiki.file_report`
    at all, and reflection itself must proceed exactly as before."""
    calls = []
    monkeypatch.setattr(Wiki, "file_report", lambda self, *a, **kw: calls.append((a, kw)) or None)
    wiki = _git_wiki(tmp_path)
    reflection = LLMReflection(StubLLMClient('["A quiet day."]'), wiki=wiki)
    reflecting = ReflectingCognition(HeuristicCognition(), reflection, every_n_reconsiders=1)
    # wiki_reporter defaults to None — not passed at all.
    ep = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]

    reflecting.reconsider(_ctx(episodes=ep, episode_count=1))
    assert reflecting.wait_idle(timeout=2.0)

    assert calls == []  # file_report never called
    assert len(reflecting.insights) == 1  # reflection itself still ran normally
    assert _open_reports(wiki) == []


def test_reflecting_cognition_files_report_via_wiki_reporter_after_reflection(tmp_path):
    """The opt-in path, end to end: a wiki-grounded reflection + an
    LLM judge that says "yes, contradiction" produces exactly one committed
    report, whose `page` matches the reflection's own wiki search hit."""
    wiki = _git_wiki(tmp_path)
    reflection = LLMReflection(StubLLMClient('["A quiet day."]'), wiki=wiki)
    judge = StubLLMClient(
        '{"contradiction": true, "claim": "mining gives 3 ore per swing", '
        '"observed": "got 1 ore per swing", "expected": "wiki says 3 ore per swing"}'
    )
    reflecting = ReflectingCognition(
        HeuristicCognition(), reflection, every_n_reconsiders=1,
        wiki_reporter=LLMWikiReportProducer(judge),
    )
    ep = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]

    reflecting.reconsider(_ctx(episodes=ep, episode_count=1))
    assert reflecting.wait_idle(timeout=2.0)

    files = _open_reports(wiki)
    assert len(files) == 1
    text = files[0].read_text()
    assert "- page: skills/mining" in text
    assert "- observed: got 1 ore per swing" in text
    assert "- expected-per-wiki: wiki says 3 ore per swing" in text


def test_reflecting_cognition_negative_control_multi_tick_zero_reports_when_llm_says_no(tmp_path):
    """Negative control (PHASE4.md item 1): a judge that always answers
    `contradiction: false` across a MULTI-TICK reflection loop (not just one
    call) must file zero reports — proves a producer that only "passes" when
    never actually exercised by the failing input can't sneak through."""
    wiki = _git_wiki(tmp_path)
    reflection = LLMReflection(StubLLMClient('["Nothing unusual."]'), wiki=wiki)
    judge = StubLLMClient('{"contradiction": false}')
    reflecting = ReflectingCognition(
        HeuristicCognition(), reflection, every_n_reconsiders=1, min_new_episodes=1,
        wiki_reporter=LLMWikiReportProducer(judge),
    )

    for i in range(1, 6):
        ep = [Episode(tick=t, kind="skill", summary="mine → success", reward=1.0) for t in range(1, i + 1)]
        reflecting.reconsider(_ctx(episodes=ep, episode_count=i))
        assert reflecting.wait_idle(timeout=2.0)

    assert _open_reports(wiki) == []


def test_reflecting_cognition_no_wiki_configured_makes_zero_wiki_judge_calls(tmp_path):
    """The no-wiki-configured case: a reflection producer with `wiki=None`
    means `_reflect_bg` hands `None` through to the reporter, which — per
    `LLMWikiReportProducer`'s own contract — must make zero `LLMClient.complete`
    calls (cost discipline, same idiom as item 2's tiering tests)."""
    reflection = LLMReflection(StubLLMClient('["A quiet day."]'))  # no wiki=
    judge = StubLLMClient('{"contradiction": true, "claim": "x", "observed": "y", "expected": "z"}')
    reflecting = ReflectingCognition(
        HeuristicCognition(), reflection, every_n_reconsiders=1,
        wiki_reporter=LLMWikiReportProducer(judge),
    )
    ep = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]

    reflecting.reconsider(_ctx(episodes=ep, episode_count=1))
    assert reflecting.wait_idle(timeout=2.0)

    assert judge.calls == []
