"""Forum posting: in-character composition + post wiring (offline)."""

import json
from types import SimpleNamespace

from anima2.chronicle import ChronicleEvent
from anima2.forum import ForumClient, compose_post, post_day
from anima2.memory import Episode, EpisodicMemory
from anima2.persona import Persona


def test_compose_post_summarizes_a_working_day():
    p = Persona(name="Grimm", title="a miner", interests="mining ore, smelting")
    eps = [Episode(tick=i, kind="skill", summary="mine", reward=0.1) for i in range(5)]
    title, content = compose_post(p, eps, job="miner")
    assert "Grimm" in title and "miner" in title
    assert content and "0.5" in content  # the day's output


def test_compose_post_handles_a_quiet_day():
    title, content = compose_post(Persona(name="Sera", title="a wanderer"), [], job="townsfolk")
    assert "Sera" not in title or content  # produces something
    assert "quiet" in content.lower()


class _FakeClient:
    configured = True

    def __init__(self) -> None:
        self.posted: list[tuple] = []

    def post(self, board, title, content):
        self.posted.append((board, title, content))
        return {"id": 1}


def test_post_day_composes_from_episodes_and_posts(tmp_path):
    eps = EpisodicMemory()
    eps.record(Episode(tick=1, kind="skill", summary="forge dagger", reward=0.6))
    agent = SimpleNamespace(persona=Persona(name="Tormund", title="a blacksmith"), episodes=eps)
    client = _FakeClient()
    res = post_day(agent, job="blacksmith", board="tavern", client=client,
                    forum_log_path=tmp_path / "forum_log.jsonl")
    assert res == {"id": 1}
    assert len(client.posted) == 1 and client.posted[0][0] == "tavern"


def test_post_day_noops_without_api_key():
    eps = EpisodicMemory()
    agent = SimpleNamespace(persona=Persona(name="X"), episodes=eps)
    assert post_day(agent, client=ForumClient(api_key="")) is None


def test_llm_post_uses_model_prose():
    from anima2.forum import compose_post_llm
    from anima2.llm import StubLLMClient

    llm = StubLLMClient('{"title": "Iron in the Bones", "body": "Swung the pickaxe all day."}')
    title, body = compose_post_llm(llm, Persona(name="Grimm", title="a miner"), [], job="miner")
    assert title == "Iron in the Bones" and "pickaxe" in body


def test_llm_post_falls_back_when_character_breaks():
    from anima2.forum import compose_post_llm
    from anima2.llm import StubLLMClient

    # A refusal / AI-disclosure must fall back to the heuristic, not be posted.
    llm = StubLLMClient("I am an AI language model and cannot role-play.")
    title, body = compose_post_llm(llm, Persona(name="Grimm", title="a miner"), [], job="miner")
    assert "ai language model" not in body.lower()
    assert "Grimm" in title


# =============================================================================
# PHASE6.md item 3 — the forum as continuing chronicle: `yesterday`/
# `chronicle_events`, both optional and additive, plus the `data/
# forum_log.jsonl` local mirror.
# =============================================================================


def test_compose_post_yesterday_none_chronicle_none_is_byte_for_byte_unchanged():
    """Regression pin against a captured GOLDEN output: with both new kwargs at
    their None defaults, compose_post must produce exactly the pre-item-3
    output. (An earlier draft compared omitted-kwargs vs explicit-None calls —
    tautological in Python, review-caught: those are the same bound call.)"""
    p = Persona(name="Sera", title="a wanderer")
    eps = [Episode(tick=1, kind="skill", summary="x", reward=0.5)]
    title, body = compose_post(p, eps, job="townsfolk", yesterday=None, chronicle_events=None)
    assert title == "Sera's day of townsfolk"
    assert body == (
        "Spent the day watching the town go by — 1 good turns and 0.5 to show "
        "for it. Tomorrow, more of the same. Britannia rewards the patient."
        "\n\n— Sera, a wanderer"
    )


def test_compose_post_llm_yesterday_none_chronicle_none_is_byte_for_byte_unchanged():
    from anima2.forum import compose_post_llm
    from anima2.llm import StubLLMClient

    reply = '{"title": "Iron in the Bones", "body": "Swung the pickaxe all day."}'
    p = Persona(name="Grimm", title="a miner")
    # Golden pin (review-caught: comparing omitted-kwargs vs explicit-None is
    # the same bound call — tautological): with both kwargs at None the model
    # reply passes through untouched, no grounding/yesterday lines spliced in.
    title, body = compose_post_llm(StubLLMClient(reply), p, [], job="miner",
                                   yesterday=None, chronicle_events=None)
    assert title == "Iron in the Bones"
    assert body == "Swung the pickaxe all day."


def test_compose_post_grounding_line_matches_the_spec_example():
    # PHASE6.md item 3's own example: "You delivered ingots to Tormund3 twice
    # today." — two delivered_ingots events, same counterpart, this session.
    p = Persona(name="Grimm0", title="a miner")
    events = [
        ChronicleEvent(ts="", tick=10, from_persona="Grimm0", to_persona="Tormund3",
                        kind="delivered_ingots", amount=10.0),
        ChronicleEvent(ts="", tick=20, from_persona="Grimm0", to_persona="Tormund3",
                        kind="delivered_ingots", amount=8.0),
    ]
    _, body = compose_post(p, [], job="miner", chronicle_events=events)
    assert "You delivered ingots to Tormund3 twice today." in body


def test_compose_post_grounding_line_ignores_events_that_arent_this_personas_own():
    # `to_persona == "Grimm0"` here (the blacksmith's own picked_up_ingots
    # event, counterparty Grimm0) — must NOT be narrated as Grimm0's own
    # action; only from_persona == the posting persona counts (see the
    # function's own docstring: "what I did", never "what happened to me").
    p = Persona(name="Grimm0", title="a miner")
    events = [ChronicleEvent(ts="", tick=1, from_persona="Tormund3", to_persona="Grimm0",
                             kind="picked_up_ingots", amount=5.0)]
    _, body = compose_post(p, [], job="miner", chronicle_events=events)
    assert "Tormund3" not in body


def test_compose_post_yesterday_appended_when_set():
    p = Persona(name="Sera", title="a wanderer")
    _, body = compose_post(p, [], job="townsfolk", yesterday="The tavern was quiet last night.")
    assert "Yesterday I noted: The tavern was quiet last night." in body


def test_compose_post_llm_yesterday_reaches_the_prompt_before_the_call():
    from anima2.forum import compose_post_llm
    from anima2.llm import StubLLMClient

    llm = StubLLMClient('{"title": "A New Day", "body": "Back to it."}')
    p = Persona(name="Grimm", title="a miner")
    compose_post_llm(llm, p, [], job="miner", yesterday="The east vein pays better in the morning.")
    assert len(llm.calls) == 1
    _system, user = llm.calls[0]
    assert "The east vein pays better in the morning." in user


def test_compose_post_llm_chronicle_events_grounding_names_the_counterpart_in_prompt():
    from anima2.forum import compose_post_llm
    from anima2.llm import StubLLMClient

    llm = StubLLMClient('{"title": "A New Day", "body": "Back to it."}')
    p = Persona(name="Grimm0", title="a miner")
    events = [
        ChronicleEvent(ts="", tick=10, from_persona="Grimm0", to_persona="Tormund3",
                        kind="delivered_ingots", amount=10.0),
        ChronicleEvent(ts="", tick=20, from_persona="Grimm0", to_persona="Tormund3",
                        kind="delivered_ingots", amount=8.0),
    ]
    compose_post_llm(llm, p, [], job="miner", chronicle_events=events)
    _system, user = llm.calls[0]
    assert "delivered ingots to Tormund3" in user
    assert "twice" in user


def test_compose_post_llm_fallback_still_threads_grounding_and_yesterday_through():
    """The load-bearing property from PHASE6.md item 3's own offline-test
    spec: a stub that ignores the prompt entirely (a broken-character reply,
    forcing the `compose_post` fallback) still gets a post whose grounding
    line and "yesterday" text are present — the property holds even off the
    LLM path."""
    from anima2.forum import compose_post_llm
    from anima2.llm import StubLLMClient

    llm = StubLLMClient("I am an AI language model and cannot role-play.")
    p = Persona(name="Grimm0", title="a miner")
    events = [ChronicleEvent(ts="", tick=1, from_persona="Grimm0", to_persona="Tormund3",
                             kind="delivered_ingots", amount=5.0)]
    _title, body = compose_post_llm(
        llm, p, [], job="miner", yesterday="The east vein pays better in the morning.",
        chronicle_events=events,
    )
    assert "delivered ingots to Tormund3" in body
    assert "The east vein pays better in the morning." in body


class _FailingClient:
    configured = True

    def post(self, board, title, content):
        raise RuntimeError("forum is down")


def test_post_day_writes_forum_log_mirror(tmp_path):
    eps = EpisodicMemory()
    eps.record(Episode(tick=1, kind="skill", summary="forge dagger", reward=0.6))
    agent = SimpleNamespace(persona=Persona(name="Tormund", title="a blacksmith"), episodes=eps)
    log_path = tmp_path / "forum_log.jsonl"
    res = post_day(agent, job="blacksmith", board="tavern", client=_FakeClient(), forum_log_path=log_path)
    assert res == {"id": 1}
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["persona"] == "Tormund"
    assert record["job"] == "blacksmith"
    assert record["remote_ok"] is True
    assert record["title"] and record["content"]


def test_post_day_logs_remote_ok_false_on_a_failed_remote_post(tmp_path):
    eps = EpisodicMemory()
    agent = SimpleNamespace(persona=Persona(name="X"), episodes=eps)
    log_path = tmp_path / "forum_log.jsonl"
    # The existing `except Exception: return None` path — unchanged, still
    # returns None on a remote hiccup — but the ATTEMPT is still logged.
    res = post_day(agent, client=_FailingClient(), forum_log_path=log_path)
    assert res is None
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["remote_ok"] is False


def test_post_day_never_logs_when_forum_not_configured(tmp_path):
    log_path = tmp_path / "forum_log.jsonl"
    eps = EpisodicMemory()
    agent = SimpleNamespace(persona=Persona(name="X"), episodes=eps)
    assert post_day(agent, client=ForumClient(api_key=""), forum_log_path=log_path) is None
    assert not log_path.exists()  # never even attempted — no log entry


def test_post_day_forum_log_two_instances_same_file_see_each_others_writes(tmp_path):
    # The same "two instances/a fresh read, one ledger" persistence proof
    # items 1-2 both already establish.
    log_path = tmp_path / "forum_log.jsonl"
    eps = EpisodicMemory()
    agent_a = SimpleNamespace(persona=Persona(name="A"), episodes=eps)
    agent_b = SimpleNamespace(persona=Persona(name="B"), episodes=eps)
    post_day(agent_a, client=_FakeClient(), forum_log_path=log_path)
    post_day(agent_b, client=_FakeClient(), forum_log_path=log_path)
    lines = log_path.read_text().splitlines()
    assert len(lines) == 2
    personas = {json.loads(line)["persona"] for line in lines}
    assert personas == {"A", "B"}
