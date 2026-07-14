"""The slow cognition loop: LLM goal parsing, speech queueing, non-blocking wrap."""

import random
import threading
from pathlib import Path

from anima2.cognition import LLMCognition, LLMWikiReportProducer, NullWikiReportProducer, ThreadedCognition
from anima2.contract import Observation, PlayerView, Position, Say
from anima2.llm import StubLLMClient
from anima2.memory import Episode
from anima2.persona import Persona
from anima2.skills import SpeakPending
from anima2.skills.base import Goal, SkillContext
from anima2.wiki import Wiki

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "wiki"


def _ctx(goal=None, memory=None) -> SkillContext:
    obs = Observation(player=PlayerView(serial=1, pos=Position(3724, 2212, 20), hits=80, hits_max=80))
    return SkillContext(obs=obs, persona=Persona(name="Grimm", title="a miner"),
                        goal=goal, memory=memory if memory is not None else {})


def _ctx_talk(talkativeness: float) -> SkillContext:
    """A fresh ctx (fresh, empty `memory`) whose persona has the given
    `talkativeness` — for the PHASE6.md item 5 talkativeness-gate tests."""
    obs = Observation(player=PlayerView(serial=1, pos=Position(3724, 2212, 20), hits=80, hits_max=80))
    return SkillContext(obs=obs, persona=Persona(name="Grimm", talkativeness=talkativeness),
                        goal=None, memory={})


def test_llm_cognition_parses_goto_and_queues_speech():
    client = StubLLMClient('{"goal": "goto", "x": 3716, "y": 2204, "say": "Off to the mine."}')
    ctx = _ctx()
    goal = LLMCognition(client).reconsider(ctx)
    assert goal is not None and goal.kind == "goto"
    assert (goal.params["target"].x, goal.params["target"].y) == (3716, 2204)
    assert ctx.memory["pending_say"] == "Off to the mine."
    # The persona + situation actually reached the model.
    assert "Grimm" in client.calls[0][0]


def test_llm_cognition_chatter_queues_say_with_job_context():
    # The village chatter path: a job-flavoured prompt, an in-character line, no goto.
    client = StubLLMClient('{"say": "These veins run thin today.", "goal": "idle"}')
    ctx = _ctx(goal=None)
    goal = LLMCognition(client, job="blacksmith").reconsider(ctx)
    assert goal is None  # idle → planner's work/Wander keeps running
    assert ctx.memory["pending_say"] == "These veins run thin today."
    assert "blacksmith" in client.calls[0][0]  # the job reached the system prompt


def test_llm_cognition_speaks_bare_prose():
    # qwen routinely ignores the JSON ask and just emits a line — speak it anyway.
    client = StubLLMClient('"Hope we hit a good vein today!"')
    ctx = _ctx(goal=None)
    goal = LLMCognition(client, job="miner").reconsider(ctx)
    assert goal is None
    assert ctx.memory["pending_say"] == "Hope we hit a good vein today!"


def test_llm_cognition_clamps_a_far_goto_to_a_short_hop():
    # The model asks to walk far across the map; the excursion is clamped so a
    # hallucinated coordinate can't march the worker into the mountains.
    client = StubLLMClient('{"say": "North, to richer veins!", "goal": "goto", "x": 5000, "y": 5000}')
    ctx = _ctx()  # standing at (3724, 2212)
    goal = LLMCognition(client).reconsider(ctx)
    assert goal is not None and goal.kind == "goto"
    t = goal.params["target"]
    here = ctx.obs.player.pos
    assert max(abs(t.x - here.x), abs(t.y - here.y)) == LLMCognition.max_excursion
    # Direction is preserved (both toward +x/+y).
    assert t.x > here.x and t.y > here.y


def test_llm_cognition_idle_clears_goal():
    ctx = _ctx(goal=Goal(kind="goto", params={}))
    goal = LLMCognition(StubLLMClient('{"goal": "idle"}')).reconsider(ctx)
    assert goal is None


def test_llm_cognition_tolerates_garbage():
    ctx = _ctx(goal=Goal(kind="goto", params={}))
    goal = LLMCognition(StubLLMClient("sorry, I cannot help")).reconsider(ctx)
    assert goal is ctx.goal  # unparseable → leaves the current goal untouched


# --- PHASE6.md item 5: the talkativeness gate ---------------------------------
#
# `talkativeness_gate=False` (default) must reproduce every existing test above
# byte-for-byte (a regression pin, below), never a new behavior. With the gate
# ON and a seeded `rng`, the pass/fail boundary is exactly reproducible.

_VALID_REPLY = '{"say": "These veins run thin today.", "goal": "idle"}'


def test_talkativeness_gate_off_is_a_no_op_even_for_a_silent_persona():
    """The load-bearing regression pin: gate OFF (the default) voices every
    valid reply regardless of `talkativeness` — even a `talkativeness=0.0`
    persona and a seeded `rng` present — i.e. no draw ever happens, byte for
    byte the pre-item-5 behavior. An always-on gate would silently mute this
    persona; opt-in is exactly why it doesn't."""
    cog = LLMCognition(StubLLMClient(_VALID_REPLY), rng=random.Random(0))  # gate defaults OFF
    ctx = _ctx_talk(0.0)
    cog.reconsider(ctx)
    assert ctx.memory["pending_say"] == "These veins run thin today."


def test_talkativeness_gate_zero_never_speaks():
    """`talkativeness=0.0` with the gate on: the seeded draw is always `>= 0.0`,
    so `_queue_say` skips every time — never queues `pending_say` across N
    calls with an always-valid reply."""
    cog = LLMCognition(StubLLMClient(_VALID_REPLY), talkativeness_gate=True, rng=random.Random(1))
    for _ in range(40):
        ctx = _ctx_talk(0.0)
        cog.reconsider(ctx)
        assert "pending_say" not in ctx.memory


def test_talkativeness_gate_one_always_speaks():
    """`talkativeness=1.0` with the gate on: `random()` is in `[0.0, 1.0)`, so a
    draw is never `>= 1.0` — always queues."""
    cog = LLMCognition(StubLLMClient(_VALID_REPLY), talkativeness_gate=True, rng=random.Random(2))
    for _ in range(40):
        ctx = _ctx_talk(1.0)
        cog.reconsider(ctx)
        assert ctx.memory["pending_say"] == "These veins run thin today."


def test_talkativeness_gate_intermediate_queues_exactly_the_seed_predicts():
    """An intermediate `talkativeness` with a fixed-seed `rng` queues on the
    EXACT, deterministic subset the seed predicts — asserted against the seed's
    own known draw sequence (`_queue_say` draws once per `reconsider`), not
    re-derived from the implementation."""
    seed, talk, n = 20250714, 0.5, 60
    predictor = random.Random(seed)
    expected = [predictor.random() < talk for _ in range(n)]
    assert any(expected) and not all(expected)  # the seed genuinely splits the calls

    cog = LLMCognition(StubLLMClient(_VALID_REPLY), talkativeness_gate=True, rng=random.Random(seed))
    actual = []
    for _ in range(n):
        ctx = _ctx_talk(talk)
        cog.reconsider(ctx)
        actual.append("pending_say" in ctx.memory)
    assert actual == expected


def test_speak_pending_drains_queue():
    ctx = _ctx(memory={"pending_say": "Heavy ore today."})
    skill = SpeakPending()
    assert skill.can_run(ctx)
    res = skill.step(ctx)
    assert isinstance(res.action, Say) and res.action.text == "Heavy ore today."
    assert not skill.can_run(ctx)  # drained


def test_threaded_cognition_never_blocks():
    gate = threading.Event()
    target_goal = Goal(kind="goto", params={"target": Position(1, 2, 0)})

    class SlowInner:
        def reconsider(self, ctx):
            gate.wait(2.0)  # block until the test releases us
            return target_goal

    cog = ThreadedCognition(SlowInner())
    ctx = _ctx(goal=None)
    # First call kicks off the background thread and returns immediately (current goal).
    assert cog.reconsider(ctx) is None
    gate.set()
    # Poll for the background result to land (no busy-blocking in reconsider).
    got = None
    for _ in range(200):
        got = cog.reconsider(ctx)
        if got is target_goal:
            break
        threading.Event().wait(0.01)
    assert got is target_goal


# --- LLMWikiReportProducer: the wiki-contradiction judge (PHASE4.md item 1) ----


def _skill_episodes() -> list[Episode]:
    return [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]


def test_llm_wiki_report_producer_fills_page_from_search_hit_ignoring_llm_page_field():
    """Safety property 2: the LLM's `page` claim (a different, nonexistent
    page here) must be completely ignored — `ReportDraft.page` always comes
    from the reflection's own wiki search hit, filled in by code."""
    client = StubLLMClient(
        '{"contradiction": true, "page": "totally/different/nonexistent-page", '
        '"claim": "mining gives 3 ore per swing", "observed": "got 1 ore per swing", '
        '"expected": "wiki says 3 ore per swing"}'
    )
    wiki = Wiki(root=FIXTURE_ROOT)
    draft = LLMWikiReportProducer(client).maybe_file_report(_skill_episodes(), Persona(name="Grimm"), wiki)
    assert draft is not None
    assert draft.page == "skills/mining"  # the search hit's own slug, not the LLM's claim
    assert draft.claim == "mining gives 3 ore per swing"
    assert draft.observed == "got 1 ore per swing"
    assert draft.expected == "wiki says 3 ore per swing"
    assert "Grimm" in draft.evidence


def test_llm_wiki_report_producer_contradiction_false_returns_none():
    client = StubLLMClient('{"contradiction": false}')
    wiki = Wiki(root=FIXTURE_ROOT)
    result = LLMWikiReportProducer(client).maybe_file_report(_skill_episodes(), Persona(name="Grimm"), wiki)
    assert result is None


def test_llm_wiki_report_producer_malformed_json_returns_none():
    client = StubLLMClient("sorry, I cannot help with that")
    wiki = Wiki(root=FIXTURE_ROOT)
    result = LLMWikiReportProducer(client).maybe_file_report(_skill_episodes(), Persona(name="Grimm"), wiki)
    assert result is None


def test_llm_wiki_report_producer_missing_fields_returns_none():
    client = StubLLMClient('{"contradiction": true, "claim": "x"}')  # observed/expected missing
    wiki = Wiki(root=FIXTURE_ROOT)
    result = LLMWikiReportProducer(client).maybe_file_report(_skill_episodes(), Persona(name="Grimm"), wiki)
    assert result is None


def test_llm_wiki_report_producer_no_wiki_configured_makes_zero_complete_calls():
    client = StubLLMClient('{"contradiction": true, "claim": "x", "observed": "y", "expected": "z"}')
    result = LLMWikiReportProducer(client).maybe_file_report(_skill_episodes(), Persona(name="Grimm"), None)
    assert result is None
    assert client.calls == []  # cost discipline: never dials the LLM with nothing to judge against


def test_llm_wiki_report_producer_no_search_hit_makes_zero_complete_calls():
    client = StubLLMClient('{"contradiction": true, "claim": "x", "observed": "y", "expected": "z"}')
    wiki = Wiki(root=FIXTURE_ROOT)
    episodes = [Episode(tick=1, kind="skill", summary="zzz-nonexistent-topic-zzz → success", reward=1.0)]
    result = LLMWikiReportProducer(client).maybe_file_report(episodes, Persona(name="Grimm"), wiki)
    assert result is None
    assert client.calls == []


def test_llm_wiki_report_producer_no_episodes_returns_none_zero_calls():
    client = StubLLMClient('{"contradiction": true, "claim": "x", "observed": "y", "expected": "z"}')
    wiki = Wiki(root=FIXTURE_ROOT)
    result = LLMWikiReportProducer(client).maybe_file_report([], Persona(name="Grimm"), wiki)
    assert result is None
    assert client.calls == []


def test_null_wiki_report_producer_always_returns_none():
    wiki = Wiki(root=FIXTURE_ROOT)
    result = NullWikiReportProducer().maybe_file_report(_skill_episodes(), Persona(name="Grimm"), wiki)
    assert result is None
