"""Wiki semantic memory: indexing/ranking, excerpt bounding, graceful degradation,
query derivation, cognition-prompt wiring, and caching (PHASE2.md B1).

Uses a small fixture wiki tree under `tests/fixtures/wiki/` (mirrors the real
`../uowiki/src/content/docs` shape: frontmatter + markdown, a `ja/` locale
decoy, a `templates/` build-preset decoy) instead of the real, possibly-absent
`../uowiki` checkout — keeps these tests offline and deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anima2.cognition import LLMCognition, LLMReflection, _top_skill_name, _wiki_query
from anima2.contract import Observation, PlayerView, Position
from anima2.llm import StubLLMClient
from anima2.memory import Episode
from anima2.persona import Persona
from anima2.skills.base import SkillContext
from anima2.wiki import Wiki, _DEFAULT_ROOT, _stem

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "wiki"


def _ctx(*, episodes=None) -> SkillContext:
    obs = Observation(player=PlayerView(serial=1, pos=Position(3724, 2212, 20), hits=80, hits_max=80))
    return SkillContext(obs=obs, persona=Persona(name="Grimm", title="a miner"), episodes=episodes or [])


# --- indexing + ranking --------------------------------------------------------


def test_search_surfaces_mining_page_first():
    wiki = Wiki(root=FIXTURE_ROOT)
    hits = wiki.search("mining ore", k=3)
    assert hits and hits[0].slug == "skills/mining"
    assert hits[0].title == "Mining"


def test_search_ranks_by_relevance_not_just_presence():
    wiki = Wiki(root=FIXTURE_ROOT)
    hits = wiki.search("fishing", k=3)
    assert hits and hits[0].slug == "skills/fishing"


def test_search_returns_empty_for_blank_or_stopword_only_query():
    wiki = Wiki(root=FIXTURE_ROOT)
    assert wiki.search("") == []
    assert wiki.search("   ") == []
    assert wiki.search("the and of") == []  # only stopwords


def test_search_k_bounds_result_count():
    wiki = Wiki(root=FIXTURE_ROOT)
    # "skill" matches 2 fixture pages (mechanics/skill-gain and skills/mining) —
    # a real bound check, not one an empty/no-match result would also satisfy.
    assert [h.slug for h in wiki.search("skill", k=5)] == ["mechanics/skill-gain", "skills/mining"]
    hits = wiki.search("skill", k=1)
    assert len(hits) == 1
    assert hits[0].slug == "mechanics/skill-gain"


def test_locale_decoy_excluded_from_index():
    wiki = Wiki(root=FIXTURE_ROOT)
    pages, _ = wiki._ensure_index()
    slugs = [p.slug for p in pages]
    assert not any(s.startswith("ja/") for s in slugs)
    # The ja/ decoy is a near-duplicate of skills/mining.md (same title/ore-heavy
    # body) — if it leaked into the index, "mining ore" would return 2 hits
    # instead of 1 real page (or the ja copy could even outrank/duplicate it).
    hits = wiki.search("mining ore", k=5)
    assert [h.slug for h in hits].count("skills/mining") == 1
    assert not any(h.slug.startswith("ja/") for h in hits)


def test_templates_decoy_excluded_from_index():
    wiki = Wiki(root=FIXTURE_ROOT)
    pages, _ = wiki._ensure_index()
    assert not any(p.slug.startswith("templates/") for p in pages)
    # The decoy is stuffed with "mining"/"ore" specifically to try to outrank
    # the real page if the exclusion filter were broken.
    hits = wiki.search("mining ore", k=5)
    assert not any(h.slug.startswith("templates/") for h in hits)


def test_index_covers_exactly_the_non_excluded_fixture_pages():
    wiki = Wiki(root=FIXTURE_ROOT)
    pages, _ = wiki._ensure_index()
    assert {p.slug for p in pages} == {"skills/mining", "skills/fishing", "mechanics/skill-gain"}


# --- stemmer: doubled-consonant regression --------------------------------------


def test_stem_collapses_doubled_consonant_left_by_ing_strip():
    # Regression: "chopping" used to stem to "chopp" (the trailing "ing" strip
    # leaves a doubled "p" undone), which didn't match "chop" ("chop" itself is
    # too short for any suffix to strip) — so a lumberjack's derived wiki query
    # ("chop lumberjack") could only score skills/lumberjacking.md's *title*,
    # never the "chopping" repeated throughout its body.
    assert _stem("chopping") == _stem("chop") == "chop"
    assert _stem("digging") == _stem("dig") == "dig"
    # l/s/z-final doubles must NOT be undoubled (classic stemmer exception) —
    # a regression here would mangle ordinary wiki prose ("press", "pass",
    # "miss", "buzz" already are the stem, not "pres"/"pas"/"mis"/"buz").
    assert _stem("pressing") == "press"
    assert _stem("missing") == "miss"
    assert _stem("buzzing") == "buzz"
    # Must still not collide with "Minoc" (the mining town) — see _stem's
    # docstring for the bug this originally guarded against.
    assert _stem("miner") == _stem("mining") == "min"
    assert _stem("minoc") == "minoc"


@pytest.mark.skipif(not _DEFAULT_ROOT.is_dir(), reason="../uowiki checkout not present")
def test_real_corpus_chop_lumberjack_surfaces_lumberjacking_page():
    # End-to-end version of the _stem regression above, against the real
    # ~2.4k-page corpus (the fixture tree has no lumberjacking page to exercise
    # this with): a miner's `_wiki_query`-shaped query surfaces the skill page.
    wiki = Wiki(root=_DEFAULT_ROOT)
    hits = wiki.search("chop lumberjack", k=3)
    assert hits and hits[0].slug == "skills/lumberjacking"


# --- excerpt: bounding + stripping ----------------------------------------------


def test_excerpt_prefers_description_and_strips_markdown_noise():
    wiki = Wiki(root=FIXTURE_ROOT)
    page = wiki.search("mining ore", k=1)[0]
    excerpt = wiki.excerpt(page)
    assert excerpt == (
        "Digging ore and stone from mountains — ore banks, MaxRange, and smelting at a forge."
    )
    assert "---" not in excerpt  # frontmatter never leaks in
    assert "<img" not in excerpt  # no raw HTML
    assert "#" not in excerpt  # no heading markers


def test_excerpt_falls_back_to_first_body_paragraph_when_no_description():
    from anima2.wiki import WikiPage

    page = WikiPage(
        slug="x", title="X", description="",
        body="![banner](/img/x.png)\n\n**Bold** intro text with a [link](/y/) and `code`.\n\nMore.",
    )
    excerpt = Wiki(root=FIXTURE_ROOT).excerpt(page)
    assert excerpt == "Bold intro text with a link and code."


def test_excerpt_clamps_to_limit():
    from anima2.wiki import WikiPage

    page = WikiPage(slug="x", title="X", description="word " * 200, body="")
    excerpt = Wiki(root=FIXTURE_ROOT).excerpt(page, limit=50)
    assert len(excerpt) <= 51  # 50 chars + the trailing ellipsis char
    assert excerpt.endswith("…")


# --- missing root: graceful, never raises ---------------------------------------


def test_missing_root_degrades_to_empty_results():
    wiki = Wiki(root="/nonexistent/definitely-not-a-real-path-xyz")
    assert wiki.available is False
    assert wiki.search("mining ore") == []  # never raises
    assert wiki.files_read == 0


def test_default_root_construction_never_raises():
    # Constructing with the default root must not touch the filesystem (lazy
    # indexing) or raise, even if `../uowiki` isn't checked out on this box.
    Wiki()


# --- query derivation from a SkillContext ---------------------------------------


def test_top_skill_name_picks_the_most_rewarded_recent_episode():
    episodes = [
        Episode(tick=1, kind="skill", summary="chop → success", reward=0.2),
        Episode(tick=2, kind="skill", summary="mine → success", reward=1.0),
        Episode(tick=3, kind="journal", summary="heard something", reward=5.0),  # not a skill episode
    ]
    assert _top_skill_name(episodes) == "mine"


def test_top_skill_name_none_when_no_rewarded_skill_episodes():
    episodes = [Episode(tick=1, kind="skill", summary="mine → failure", reward=0.0)]
    assert _top_skill_name(episodes) is None


def test_wiki_query_combines_skill_and_job():
    ctx = _ctx(episodes=[Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)])
    assert _wiki_query(ctx, "miner") == "mine miner"


def test_wiki_query_falls_back_to_job_alone_with_no_episodes():
    ctx = _ctx(episodes=[])
    assert _wiki_query(ctx, "miner") == "miner"


def test_wiki_query_none_without_episode_or_job():
    ctx = _ctx(episodes=[])
    assert _wiki_query(ctx, "") is None


# --- prompt inclusion through LLMCognition (StubLLMClient pattern) --------------


def test_llm_cognition_splices_wiki_line_into_situation_prompt():
    client = StubLLMClient('{"goal": "idle"}')
    wiki = Wiki(root=FIXTURE_ROOT)
    ctx = _ctx(episodes=[Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)])

    LLMCognition(client, job="miner", wiki=wiki).reconsider(ctx)

    prompt = client.calls[0][1]
    assert "Wiki — Mining:" in prompt
    assert "ore banks" in prompt  # the mining fixture's description made it in
    assert prompt.rstrip().endswith("stroll somewhere close?")  # wiki line didn't
    # push out / reorder the JSON-ask tail of the prompt.


def test_llm_cognition_without_wiki_never_adds_a_wiki_line():
    client = StubLLMClient('{"goal": "idle"}')
    ctx = _ctx(episodes=[Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)])
    LLMCognition(client, job="miner").reconsider(ctx)  # no wiki=
    assert "Wiki —" not in client.calls[0][1]


def test_llm_cognition_wiki_line_absent_when_no_hit():
    client = StubLLMClient('{"goal": "idle"}')
    wiki = Wiki(root=FIXTURE_ROOT)
    ctx = _ctx(episodes=[])
    LLMCognition(client, job="zzz-nonexistent-topic-zzz", wiki=wiki).reconsider(ctx)
    assert "Wiki —" not in client.calls[0][1]


def test_llm_reflection_splices_wiki_line_into_situation_prompt():
    client = StubLLMClient('["A quiet day."]')
    wiki = Wiki(root=FIXTURE_ROOT)
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]

    LLMReflection(client, wiki=wiki).reflect(episodes, Persona(name="Grimm"))

    prompt = client.calls[0][1]
    assert "Wiki — Mining:" in prompt


# --- caching: identical query costs one search/read, not one per call ----------


def test_wiki_search_result_is_memoized_per_query():
    wiki = Wiki(root=FIXTURE_ROOT)
    first = wiki.search("mining ore", k=3)
    reads_after_first = wiki.files_read
    second = wiki.search("mining ore", k=3)
    assert second is first  # same cached list object, not recomputed
    assert wiki.files_read == reads_after_first  # no re-read of any file


def test_wiki_index_builds_exactly_once_across_many_distinct_queries(monkeypatch):
    wiki = Wiki(root=FIXTURE_ROOT)
    real_read_text = Path.read_text
    calls = []

    def counting_read_text(self, *a, **kw):
        calls.append(self)
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    wiki.search("mining ore")
    n_after_first = len(calls)
    assert n_after_first > 0  # the index build actually read the fixture files

    wiki.search("fishing")  # a different query — must not trigger a re-index
    wiki.search("skill gain")
    wiki.search("mining ore")  # repeat of the first — cached, no re-read either

    assert len(calls) == n_after_first  # no file was ever read a second time


def test_llm_cognition_wiki_cache_avoids_a_second_search_for_the_same_query():
    class _CountingWiki:
        def __init__(self, inner: Wiki) -> None:
            self.inner = inner
            self.search_calls = 0

        def search(self, query, k=3):
            self.search_calls += 1
            return self.inner.search(query, k)

        def excerpt(self, page, limit=280):
            return self.inner.excerpt(page, limit)

    counting = _CountingWiki(Wiki(root=FIXTURE_ROOT))
    cog = LLMCognition(StubLLMClient('{"goal": "idle"}'), job="miner", wiki=counting)
    episodes = [Episode(tick=1, kind="skill", summary="mine → success", reward=1.0)]

    cog.reconsider(_ctx(episodes=episodes))
    cog.reconsider(_ctx(episodes=episodes))  # identical derived query ("mine miner")

    assert counting.search_calls == 1
