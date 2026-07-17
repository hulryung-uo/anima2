"""Skill library v0 — registry, keyword retrieval, persisted outcome ledger.

PHASE4.md item 3 (DESIGN.md §6): a registry-plus-ledger, not a code-generation
system. Every `SkillEntry` wraps an existing, hand-written `Skill` subclass —
nothing here authors new code, sidestepping the scout's top-flagged Voyager
risk rather than solving it (PHASE4.md item 3's own "Key design decisions").
Adds two things composability alone doesn't give the codebase yet:

- **Natural-language retrieval** (`retrieve()`) over each skill's `name` +
  `description`, reusing `_textindex.py`'s weighted keyword scoring — the
  exact scoring `wiki.py::Wiki.search()` already validated against the real
  ~2.4k-page wiki corpus (item 1) — not a reimplementation, not embeddings.
- **A persisted, cross-restart, cross-process-readable outcome ledger**
  (`record_outcome()`/`stats()`) — the first thing in anima2 to survive a
  process restart. `data/skill_ledger.jsonl`, gitignored, created lazily
  (mirrors `llm.py`'s `data/llm_usage.jsonl` convention).

**Measurement-independence caveat, stated plainly (not glossed over):** the
ledger's `reward` field is the agent's own computed `SkillResult.reward` (the
same value already recorded into `EpisodicMemory` today) — **not** an
independently GM-verified channel. Weaker than DESIGN.md A6's "agents can't
lie" standard (v1 Foundry's wire-level, packet-parsed fitness — anima2 has no
equivalent of that yet). `live_hunt.py --skill-library`'s advisory GM
gold-readback corroboration (`GmControl.get_property`) is the cheap, optional
check this caveat motivates — logged, never a hard pass/fail gate.

Built without importing `cognition.py`/`planner.py` — mirrors v1
`../anima/anima/planner/modes.py`'s deliberately dependency-free style (a
flat list of data, "loads anywhere").
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._textindex import _terms, score_terms, weighted_terms
from .skills import (
    Blacksmith,
    BlacksmithMarket,
    Chop,
    Combat,
    Fish,
    GoTo,
    Greet,
    Harvest,
    Hunt,
    Mine,
    MineAndSmelt,
    MineSmeltDeliver,
    Skill,
    SpeakPending,
    Survive,
    Wander,
)

#: `data/skill_ledger.jsonl` relative to the process's cwd — mirrors `llm.py`'s
#: `_DEFAULT_USAGE_LOG` convention exactly (created lazily, gitignored). Tests
#: must always pass an explicit `ledger_path=` (a `tmp_path`) so the suite
#: never touches the real file.
_DEFAULT_LEDGER = Path("data") / "skill_ledger.jsonl"

#: Guards concurrent appends to a single ledger file across threads — mirrors
#: `llm.py::_usage_log_lock` (`village.py` runs each agent's tick loop on its
#: own thread; several could record an outcome around the same wall-clock
#: moment).
_ledger_lock = threading.Lock()

#: Retrieval field weights — name (~a title) dominates, mirrors `wiki.py`'s own
#: title >> description weighting (30 vs 8) at the same order of magnitude,
#: scaled down since a skill name is a couple of words, not a page title
#: competing against a full page body.
_WEIGHT_NAME = 12
_WEIGHT_DESCRIPTION = 4


@dataclass(frozen=True)
class SkillEntry:
    """One registered skill: enough to rank it (`name`/`description`) and
    instantiate it (`skill_cls`, zero-arg — every `Skill` subclass in this
    codebase today defines no `__init__`, matching `Profession.work_skill`'s
    own `Callable[[], Skill]` shape)."""

    name: str
    description: str
    skill_cls: type[Skill]
    tags: tuple[str, ...] = ()


def _entry(skill_cls: type[Skill], *, tags: tuple[str, ...] = ()) -> SkillEntry:
    return SkillEntry(name=skill_cls.name, description=skill_cls.description, skill_cls=skill_cls, tags=tags)


#: The static registry — covers every skill currently exported from
#: `skills/__init__.py` (verified: `Blacksmith`, `BlacksmithMarket`, `Chop`,
#: `Combat`, `Fish`, `GoTo`, `Greet`, `Harvest`, `Hunt`, `Mine`,
#: `MineAndSmelt`, `MineSmeltDeliver`, `SpeakPending`, `Survive`, `Wander` —
#: 15 concrete `Skill` subclasses; `Skill`/`SkillContext`/`SkillResult`/
#: `Status`/`Goal` are also exported but aren't skills, so the registry doesn't
#: cover them).
#: `tests/test_skill_library.py::test_registry_covers_every_exported_skill`
#: fails loudly if `__all__` ever grows a `Skill` subclass this list doesn't
#: know about — the two can't silently drift.
REGISTRY: list[SkillEntry] = [
    _entry(Blacksmith, tags=("blacksmith", "craft")),
    _entry(BlacksmithMarket, tags=("blacksmith", "craft", "sell", "bank")),
    _entry(Chop, tags=("lumberjack", "gather")),
    _entry(Combat, tags=("combat",)),
    _entry(Fish, tags=("fisher", "gather")),
    _entry(GoTo, tags=("movement",)),
    _entry(Greet, tags=("social",)),
    _entry(Harvest, tags=("gather",)),
    _entry(Hunt, tags=("hunter", "combat", "loot")),
    _entry(Mine, tags=("miner", "gather")),
    _entry(MineAndSmelt, tags=("miner", "gather", "craft")),
    _entry(MineSmeltDeliver, tags=("miner", "gather", "craft", "trade")),
    _entry(SpeakPending, tags=("social",)),
    _entry(Survive, tags=("survival", "healing", "movement")),
    _entry(Wander, tags=("movement",)),
]


@dataclass(frozen=True)
class SkillStats:
    """`stats()`'s return shape — see the module docstring for the
    measurement-independence caveat `mean_reward` inherits from
    `SkillResult.reward`."""

    count: int = 0
    mean_reward: float = 0.0
    success_rate: float = 0.0


@dataclass
class _Accum:
    """Mutable running totals behind one `(skill_name, profession)` key —
    `SkillStats` is the frozen snapshot handed to callers; this is what
    `_ensure_stats`/`record_outcome` actually mutate."""

    count: int = 0
    total_reward: float = 0.0
    successes: int = 0

    def snapshot(self) -> SkillStats:
        if self.count == 0:
            return SkillStats()
        return SkillStats(
            count=self.count,
            mean_reward=self.total_reward / self.count,
            success_rate=self.successes / self.count,
        )


def _status_name(status: Any) -> str:
    """`Status.SUCCESS` -> `"SUCCESS"`; tolerant of a bare string too (a
    caller reconstructing a record from a hand-written fixture line, or a
    future caller with no `Status` enum member handy)."""
    return getattr(status, "name", str(status))


class SkillLibrary:
    """The registry (`REGISTRY`, static) plus a per-instance, file-backed
    outcome ledger (`record_outcome`/`stats`).

    Persistence is the load-bearing claim of this whole item: two separate
    `SkillLibrary` instances pointed at the same `ledger_path` see each
    other's writes — instance A's `record_outcome` call is durable on disk
    immediately (not just in A's own memory), so a *freshly started* instance
    B (or, live, a second Python process — see `live_hunt.py
    --skill-library`'s cross-process readback gate) reading `stats()` for the
    first time sees everything A ever wrote. Mirrors `wiki.py::Wiki`'s lazy,
    build-once-then-cache index pattern for the *read* side (`_ensure_stats`
    only ever scans the ledger file from scratch once per instance), while
    `record_outcome` keeps an already-warm cache accurate for its *own*
    subsequent writes by updating it incrementally in memory instead of
    rescanning the whole file on every call (would be O(ledger size) per
    outcome recorded over a long session otherwise).
    """

    def __init__(self, ledger_path: str | Path | None = None) -> None:
        self.ledger_path = Path(ledger_path) if ledger_path is not None else _DEFAULT_LEDGER
        self._stats: dict[tuple[str, str], _Accum] | None = None

    @property
    def entries(self) -> list[SkillEntry]:
        return list(REGISTRY)

    # -- retrieval --------------------------------------------------------------

    def retrieve(self, query: str, k: int = 3) -> list[SkillEntry]:
        """Top-`k` `SkillEntry` ranked by weighted, stemmed keyword overlap of
        `query` against each entry's `name` + `description` — the identical
        `_textindex.py` scoring `wiki.py::Wiki.search()` uses (title >>
        description, an all-terms bonus), just over a different corpus.
        `[]` for a blank/stopword-only query or no match — never raises,
        matching `Wiki.search()`'s own contract.
        """
        q = " ".join(query.split()).lower() if query else ""
        if not q:
            return []
        query_terms = _terms(q)
        if not query_terms:
            return []
        scored: list[tuple[int, str, SkillEntry]] = []
        for entry in REGISTRY:
            counts = weighted_terms((entry.name, _WEIGHT_NAME), (entry.description, _WEIGHT_DESCRIPTION))
            score = score_terms(query_terms, counts)
            if not score:
                continue
            scored.append((-score, entry.name, entry))  # stable, deterministic order
        scored.sort(key=lambda s: (s[0], s[1]))
        return [e for _, _, e in scored[:k]]

    # -- outcome ledger -----------------------------------------------------------

    def record_outcome(
        self,
        skill_name: str,
        profession: str,
        reward: float,
        status: Any,
        *,
        param: str | None = None,
        param_value: float | None = None,
    ) -> None:
        """Append one JSON line to `ledger_path` — `{ts, skill_name,
        profession, reward, status, param, param_value}` (the last two `None`
        unless a caller like item 4's `ParamTuner` is recording a tuned run).
        A broken/unwritable ledger degrades silently (never raises) — the
        same "never break the caller over a logging failure" discipline
        `llm.py::_UsageLoggingClient._log` already established.
        """
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "skill_name": skill_name,
            "profession": profession,
            "reward": reward,
            "status": _status_name(status),
            "param": param,
            "param_value": param_value,
        }
        try:
            with _ledger_lock:
                self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
                with self.ledger_path.open("a") as f:
                    f.write(json.dumps(record) + "\n")
        except OSError:
            return
        # Keep an already-warm cache accurate for this instance's own writes
        # (see class docstring) — a no-op if `stats()` hasn't been called yet
        # on this instance (the next call lazily rebuilds from disk anyway,
        # picking up this write along with everything else).
        if self._stats is not None:
            self._accumulate(self._stats, record)

    def stats(self, skill_name: str, profession: str) -> SkillStats:
        """`count`/`mean_reward`/`success_rate` for `(skill_name,
        profession)`, lazily built from the ledger file on first read (mirrors
        `Wiki._ensure_index`'s lazy-build-once pattern), then kept warm in
        memory (see class docstring). All-zero `SkillStats()` for a key never
        recorded, or a missing/empty ledger file — never raises.
        """
        stats = self._ensure_stats()
        accum = stats.get((skill_name, profession))
        return accum.snapshot() if accum is not None else SkillStats()

    def _ensure_stats(self) -> dict[tuple[str, str], _Accum]:
        if self._stats is None:
            stats: dict[tuple[str, str], _Accum] = {}
            for record in self._read_ledger():
                self._accumulate(stats, record)
            self._stats = stats
        return self._stats

    def _read_ledger(self) -> list[dict[str, Any]]:
        """Every parseable line of `ledger_path`, in order. A missing file
        yields no lines (not an error — no outcomes recorded yet); a
        corrupted/partial trailing line (or any malformed line) is skipped,
        never fatal — matches `wiki.py`'s "degrade, never crash" discipline
        for a broken frontmatter block.
        """
        try:
            text = self.ledger_path.read_text(encoding="utf-8")
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    @staticmethod
    def _accumulate(stats: dict[tuple[str, str], _Accum], record: dict[str, Any]) -> None:
        skill_name = record.get("skill_name")
        profession = record.get("profession")
        if not isinstance(skill_name, str) or not isinstance(profession, str):
            return  # malformed record — skip rather than crash the whole read
        try:
            reward = float(record.get("reward", 0.0))
        except (TypeError, ValueError):
            reward = 0.0
        accum = stats.setdefault((skill_name, profession), _Accum())
        accum.count += 1
        accum.total_reward += reward
        if record.get("status") == "SUCCESS":
            accum.successes += 1
