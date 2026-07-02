"""Wiki — read-only semantic memory over the companion `../uowiki` docs tree.

Phase 2 close-out (PHASE2.md B1, DESIGN.md §6/§7): the agent is meant to consult
the wiki "before betting on a mechanic." anima2 is a standalone Python process —
no MCP client, no access to the deployed site — so this module reads the wiki's
local markdown files directly and offers a small keyword `search()` plus a
bounded `excerpt()`, for splicing at most one compact "Wiki — <title>: <excerpt>"
line into the slow-loop cognition prompt (`cognition.py::LLMCognition`/
`LLMReflection`). Read-only: filing discrepancy reports back to the wiki is
Phase 4's fuller loop (DESIGN.md §10) — explicitly out of scope here.

Design notes:
- **Dependency-free keyword scoring, no embeddings.** A small weighted term
  index (title >> description > headings > body — title dominates on purpose,
  see `_WEIGHT_TITLE`), built once, in memory. Query and index terms are
  lightly stemmed (`_stem`, a small suffix-stripper) so a skill name / job
  title like "mine"/"miner" still finds a wiki page titled "Mining" without a
  real stemmer or a hand-maintained synonym table — see `_stem`'s docstring.
- **Locale dirs excluded.** `ja/`/`ko/` under the docs root are full translated
  duplicates of the English tree; indexing them would double-count every page
  and could surface a non-English excerpt into an English prompt.
- **`templates/`/`essays/` excluded too.** These are curated character-build
  presets and personal essays/narrative pieces, not verified game-mechanics
  reference — the kind of content this index exists to serve ("before betting
  on a mechanic"). Leaving them out keeps search focused on the "textbook"
  sections (skills, mechanics, items, crafting, professions, bestiary, world,
  magic, playing, guides, reference, shard).
- **Lazy, one-time index.** Building the index touches the filesystem, so it
  never runs in `__init__` (which could run on any thread, including the fast
  loop's, if a caller got that wrong) — only on first `search()`/`excerpt()`
  call. In production that first call is always made from a slow-loop thread
  (`ThreadedCognition`'s worker or `ReflectingCognition`'s reflection thread —
  see `cognition.py`), never the fast tick. Indexing the real ~2.4k-page tree
  measures well under a second (see `docs/PHASE2.md` B1 for the number).
- **A missing root degrades to empty results, never raises.** Offline dev boxes
  and CI don't have `../uowiki` checked out next to anima2; `search()` then just
  returns `[]` and the cognition wiki line is silently omitted.
"""

from __future__ import annotations

import os
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

#: Default root resolves `../uowiki/src/content/docs` relative to this repo
#: (`anima2/anima2/wiki.py` → `anima2/` (repo root) → its sibling `uowiki/`);
#: override with the `ANIMA2_WIKI_ROOT` env var or the `root=` constructor arg
#: (tests point this at a small fixture tree under `tests/fixtures/wiki`).
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "uowiki" / "src" / "content" / "docs"

#: Top-level directories under the docs root left out of the index — see the
#: module docstring ("Locale dirs excluded" / "templates/essays excluded too").
_EXCLUDED_TOP_DIRS = frozenset({"ja", "ko", "templates", "essays"})

#: Title dominates deliberately (and heavily): on the real ~2.4k-page corpus, a
#: long page that mentions a topic often in passing (e.g. `world/minoc.md`,
#: which is full of incidental "mining"/"miners" flavor text because Minoc is
#: the mining town) would otherwise out-score the page that's actually *about*
#: it (`skills/mining.md`) purely on repetition. A big title weight fixes that
#: without needing full TF-IDF/length normalization — measured against the
#: real corpus (see `docs/PHASE2.md` B1) for the query a miner's cognition
#: actually issues ("mine miner"): `skills/mining` outranks `world/minoc`.
_WEIGHT_TITLE = 30
_WEIGHT_DESCRIPTION = 8
_WEIGHT_HEADING = 3
_WEIGHT_BODY = 1
#: Bonus added when a page contains every query term (on top of the summed
#: per-term weighted counts) — nudges a page matching the whole query above one
#: that only matches part of it via a single very-repeated term.
_ALL_TERMS_BONUS = 5

#: Suffixes stripped (longest first) by `_stem` before its trailing e/y pass.
_SUFFIXES = ("ing", "ers", "er", "ed", "es", "s")

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
_WORD_RE = re.compile(r"[a-z0-9]+")

#: Small stopword list — just enough to keep near-universal function words from
#: adding equal-ish noise to every page's score (not a linguistic stemmer).
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "if",
    "in", "into", "is", "it", "its", "not", "of", "on", "or", "per", "that",
    "the", "this", "to", "up", "was", "were", "with", "you", "your",
})


@dataclass(frozen=True)
class WikiPage:
    """One indexed wiki page — enough to rank it and render a bounded excerpt."""

    slug: str  # path relative to the docs root, no extension, posix separators
    title: str
    description: str
    body: str  # markdown body with frontmatter stripped, otherwise raw
    status: str = ""


def _stem(tok: str) -> str:
    """Small suffix-stripping stem — not a real stemmer, just enough to collapse
    a skill name / job title and the wiki-prose word forms actually used for it
    into the same bucket: "mine"/"miner"/"mining"/"miners" -> "min",
    "fish"/"fisher"/"fishing" -> "fish", "blacksmith"/"blacksmithy" ->
    "blacksmith", "lumberjack"/"lumberjacking" -> "lumberjack", "smelt"/
    "smelting"/"smelter" -> "smelt". Strips one inflectional suffix (longest
    match first, so "miners" loses "ers" not just "s"), then collapses a
    trailing doubled consonant left behind by that strip (e.g. "chopping" ->
    "chopp" -> "chop", matching the un-suffixed "chop" a query would use —
    the classic stemmer undoubling rule, skipped for l/s/z so "press"/"pass"-
    shaped words don't get chopped down to "pres"/"pas"), then strips a
    trailing silent e or y, each only when the token is long enough that
    little of substance is left (avoids mangling short words like "ore" or
    "ash"). Deliberately not a blind prefix-truncation stemmer: an earlier
    version of this collapsed "miner"/"mining" *and* "Minoc" (the mining town)
    to the same 3-letter prefix, which then let a page about the town
    out-rank the page about the skill for a "miner" query — this
    suffix-aware version keeps "minoc" intact (it never matches any suffix
    in `_SUFFIXES`, so it never enters the doubled-consonant path either).
    """
    stripped = False
    for suf in _SUFFIXES:
        if len(tok) > len(suf) + 2 and tok.endswith(suf):
            tok = tok[: -len(suf)]
            stripped = True
            break
    if stripped and len(tok) > 3 and tok[-1] == tok[-2] and tok[-1] not in "aeioulsz":
        tok = tok[:-1]
    if tok.endswith("e") and len(tok) > 3:
        tok = tok[:-1]
    elif tok.endswith("y") and len(tok) > 4:
        tok = tok[:-1]
    return tok


def _terms(text: str) -> list[str]:
    """Lowercased, stopword-filtered, stemmed word tokens from `text`."""
    return [_stem(t) for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS]


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """`(frontmatter dict, body)` — tolerant of missing/unparseable frontmatter
    (never raises: a page with a broken YAML block just indexes with no metadata
    rather than taking the whole index down)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1))
    except Exception:  # noqa: BLE001 — PyYAML raises more than just YAMLError
        # (e.g. ValueError on some malformed scalars); any of it means "not
        # parseable," never a reason to blow up the caller.
        meta = None
    return (meta if isinstance(meta, dict) else {}), text[m.end():]


def _strip_markdown_noise(text: str) -> str:
    """Strip raw HTML/images/links/heading-markers/emphasis, collapse whitespace
    to a single line. Used to render a prompt-safe `excerpt()`."""
    text = re.sub(r"<[^>]+>", " ", text)  # raw HTML, e.g. <img src="..." />
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [text](url) -> text
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)  # heading markers
    text = re.sub(r"[`*_>|]+", "", text)  # emphasis/quote/code/table noise (hugs its word, no gap)
    return " ".join(text.split())


#: MDX pages can open with `import {...} from '...'`/`export ...` statement
#: lines above the prose. Used by `_excerpt_from`'s body-paragraph fallback so
#: those lines (raw braces/quotes/paths) can never end up spliced into a
#: prompt line.
_MDX_STATEMENT_RE = re.compile(r"^\s*(?:import|export)\s")


def _excerpt_from(description: str, body: str, limit: int) -> str:
    """Prefer the frontmatter `description` (already a short hand-written
    summary on every wiki page); fall back to the first body paragraph that
    still has content once markdown-stripped (skips e.g. an image-only lead
    paragraph, or an MDX import/export block). Always clamped to `limit`
    characters."""
    clean = _strip_markdown_noise(description) if description.strip() else ""
    if not clean:
        for para in body.split("\n\n"):
            lines = [ln for ln in para.split("\n") if not _MDX_STATEMENT_RE.match(ln)]
            candidate = _strip_markdown_noise("\n".join(lines))
            if candidate:
                clean = candidate
                break
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "…"


class Wiki:
    """Read-only semantic memory over a local wiki docs tree.

    Indexes lazily on first use (never in `__init__` — see module docstring),
    then memoizes both the index and every `search()` result, so one shared
    `Wiki` instance (e.g. across a whole village of agents) costs one
    filesystem walk total, however many queries hit it (DESIGN.md §7: "cache
    aggressively ... wiki excerpts").
    """

    def __init__(self, root: str | Path | None = None) -> None:
        if root is None:
            root = os.environ.get("ANIMA2_WIKI_ROOT") or _DEFAULT_ROOT
        self.root = Path(root).expanduser()
        self._lock = threading.Lock()
        #: The built index, published as a single `(pages, terms)` tuple — see
        #: `_ensure_index` for why it's one attribute and not two.
        self._index: tuple[list[WikiPage], list[Counter[str]]] | None = None
        self._search_cache: dict[tuple[str, int], list[WikiPage]] = {}
        #: Bumped once per file actually read from disk — lets tests prove the
        #: index (and thus every file read) happens exactly once, no matter how
        #: many `search()` calls follow (see `test_wiki.py`'s cache tests).
        self.files_read = 0

    @property
    def available(self) -> bool:
        """Whether the configured root exists (diagnostic only — `search()`
        itself never needs this; it already degrades to `[]`)."""
        return self.root.is_dir()

    def search(self, query: str, k: int = 3) -> list[WikiPage]:
        """Top-`k` pages ranked by weighted, stemmed keyword overlap with
        `query`. Returns `[]` if the root is missing, `query` is blank/only
        stopwords, or nothing matches — never raises. Memoized per exact
        `(query, k)`: an identical call later returns the cached ranking
        without rescanning the index."""
        q = " ".join(query.split()).lower() if query else ""
        if not q:
            return []
        cache_key = (q, k)
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            return cached
        ranked = self._rank(q)[:k]
        self._search_cache[cache_key] = ranked
        return ranked

    def excerpt(self, page: WikiPage, limit: int = 280) -> str:
        """A bounded, markdown-stripped snippet for `page` — a few hundred
        characters at most, safe to splice into an LLM prompt line."""
        return _excerpt_from(page.description, page.body, limit)

    # -- indexing -----------------------------------------------------------

    def _ensure_index(self) -> tuple[list[WikiPage], list[Counter[str]]]:
        # Publish-order constraint: the fast path below reads only `_index`,
        # never `_pages`/`_terms` separately, and `_index` is always assigned
        # once, as a fully-built tuple. That's deliberate — if `pages` and
        # `terms` were published via two separate attribute writes, a
        # concurrent unlocked reader could observe the first write but not the
        # second (a torn read) and hand `_rank` a `None` where it expects a
        # list, which `zip` would then raise on — breaking `search()`'s
        # "never raises" contract. Building into a local and assigning it to
        # `_index` in one statement makes that torn read impossible.
        index = self._index
        if index is None:
            with self._lock:  # double-checked: only one thread ever builds it
                index = self._index
                if index is None:
                    index = self._build_index()
                    self._index = index
        return index

    def _build_index(self) -> tuple[list[WikiPage], list[Counter[str]]]:
        pages: list[WikiPage] = []
        terms: list[Counter[str]] = []
        if not self.root.is_dir():  # offline/CI: no ../uowiki checked out
            return pages, terms
        paths = sorted(self.root.rglob("*.md")) + sorted(self.root.rglob("*.mdx"))
        for path in paths:
            rel = path.relative_to(self.root)
            if rel.parts and rel.parts[0] in _EXCLUDED_TOP_DIRS:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue  # unreadable file: skip it, never take the index down
            self.files_read += 1
            # Broad on purpose: a malformed page must skip just that page, never
            # leave `_index` unset (which would make every subsequent `search()`
            # re-read the whole tree and re-raise the same error) or propagate
            # out of `_build_index`/`_ensure_index` and break `search()`'s
            # "never raises" contract.
            try:
                meta, body = _split_frontmatter(text)
                title = str(meta.get("title") or path.stem)
                description = str(meta.get("description") or "")
                status = str(meta.get("status") or "")
                slug = rel.with_suffix("").as_posix()
                page = WikiPage(slug=slug, title=title, description=description,
                                 body=body, status=status)
                page_terms = self._weighted_terms(title, description, body)
            except Exception:  # noqa: BLE001
                continue
            pages.append(page)
            terms.append(page_terms)
        return pages, terms

    @staticmethod
    def _weighted_terms(title: str, description: str, body: str) -> Counter[str]:
        counts: Counter[str] = Counter()
        for tok in _terms(title):
            counts[tok] += _WEIGHT_TITLE
        for tok in _terms(description):
            counts[tok] += _WEIGHT_DESCRIPTION
        for heading in _HEADING_RE.findall(body):
            for tok in _terms(heading):
                counts[tok] += _WEIGHT_HEADING
        for tok in _terms(body):
            counts[tok] += _WEIGHT_BODY
        return counts

    def _rank(self, query: str) -> list[WikiPage]:
        pages, terms = self._ensure_index()
        query_terms = _terms(query)
        if not query_terms:
            return []
        scored: list[tuple[int, str, WikiPage]] = []
        for page, counts in zip(pages, terms):
            score = sum(counts.get(t, 0) for t in query_terms)
            if not score:
                continue
            if all(counts.get(t, 0) > 0 for t in query_terms):
                score += _ALL_TERMS_BONUS * len(query_terms)
            scored.append((-score, page.slug, page))  # stable, deterministic order
        scored.sort(key=lambda s: (s[0], s[1]))
        return [p for _, _, p in scored]
