"""Shared, dependency-free weighted keyword text index.

Extracted from `wiki.py` (Phase 4 item 1, PHASE4.md) so `skill_library.py`
(item 3, `SkillLibrary.retrieve()`) can reuse the identical tokenization,
light stemming, and title-dominant weighted scoring over a different corpus
(skill `name` + `description`) without any `wiki.py`-specific import
(frontmatter, headings, excerpting — all wiki-local concepts — stay in
`wiki.py`). `wiki.py` imports `_stem`/`_terms`/`_WORD_RE`/`_STOPWORDS` from
here instead of defining its own copy; existing `wiki.py` behavior and its
tests (which import `_stem` from `anima2.wiki`) are unchanged — `wiki.py`
re-exports these names via its own `from ._textindex import ...`.
"""

from __future__ import annotations

import re
from collections import Counter

#: Suffixes stripped (longest first) by `_stem` before its trailing e/y pass.
_SUFFIXES = ("ing", "ers", "er", "ed", "es", "s")

_WORD_RE = re.compile(r"[a-z0-9]+")

#: Small stopword list — just enough to keep near-universal function words from
#: adding equal-ish noise to every item's score (not a linguistic stemmer).
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "if",
    "in", "into", "is", "it", "its", "not", "of", "on", "or", "per", "that",
    "the", "this", "to", "up", "was", "were", "with", "you", "your",
})

#: Bonus added (per query term) when an item contains every query term, on top
#: of the summed per-term weighted counts — nudges a whole-query match above a
#: partial match built from one very-repeated term.
ALL_TERMS_BONUS = 5


def _stem(tok: str) -> str:
    """Small suffix-stripping stem — not a real stemmer, just enough to collapse
    a name/title and the prose word forms actually used for it into the same
    bucket: "mine"/"miner"/"mining"/"miners" -> "min", "fish"/"fisher"/
    "fishing" -> "fish", "blacksmith"/"blacksmithy" -> "blacksmith",
    "lumberjack"/"lumberjacking" -> "lumberjack", "smelt"/"smelting"/
    "smelter" -> "smelt". Strips one inflectional suffix (longest match
    first, so "miners" loses "ers" not just "s"), then collapses a trailing
    doubled consonant left behind by that strip (e.g. "chopping" -> "chopp"
    -> "chop", matching the un-suffixed "chop" a query would use — the
    classic stemmer undoubling rule, skipped for l/s/z so "press"/"pass"-
    shaped words don't get chopped down to "pres"/"pas"), then strips a
    trailing silent e or y, each only when the token is long enough that
    little of substance is left (avoids mangling short words like "ore" or
    "ash"). Deliberately not a blind prefix-truncation stemmer: an earlier
    version of this collapsed "miner"/"mining" *and* "Minoc" (the mining
    town) to the same 3-letter prefix, which then let a page about the town
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


def weighted_terms(*fields: tuple[str, int]) -> Counter[str]:
    """Sum weighted, stemmed term counts across `(text, weight)` field pairs —
    e.g. `weighted_terms((title, 30), (description, 8), (body, 1))`. The
    shared scoring primitive behind `wiki.py::Wiki._weighted_terms` (title >>
    description > headings > body) and `skill_library.py::SkillLibrary`'s
    name+description index (item 3) — each caller picks its own field/weight
    shape, this just does the summing."""
    counts: Counter[str] = Counter()
    for text, weight in fields:
        for tok in _terms(text):
            counts[tok] += weight
    return counts


def score_terms(
    query_terms: list[str], counts: Counter[str], *, all_terms_bonus: int = ALL_TERMS_BONUS
) -> int:
    """Weighted term-overlap score of one indexed item (`counts`, from
    `weighted_terms`) against `query_terms` (already stemmed, e.g. via
    `_terms(query)`), plus `all_terms_bonus` per query term when the item
    contains every query term rather than just some. `0` means no overlap at
    all — callers should treat that as "exclude", not merely "rank low"."""
    total = sum(counts.get(t, 0) for t in query_terms)
    if total and all(counts.get(t, 0) > 0 for t in query_terms):
        total += all_terms_bonus * len(query_terms)
    return total
