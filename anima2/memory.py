"""Episodic + reflection memory — what happened, and what the agent made of it.

The fast loop records `Episode`s (skill outcomes with rewards, notable events);
the slow cognition loop reads recent episodes to set goals and reflect, and
aggregate reward is the backbone learning signal (DESIGN.md §6). `ReflectionMemory`
is the Generative-Agents-style layer on top: the slow loop periodically distills a
run of episodes into a handful of short `Insight`s (PHASE2.md B1), which persist
and feed back into later goal/speech decisions (see `cognition.ReflectingCognition`).

PHASE6.md item 1 ("persistent lives") adds an optional disk-backed layer to
`ReflectionMemory` itself: an `Insight` an agent distills today should still be
available the next time that same character logs in, even in a brand new
process — unlike raw `EpisodicMemory`, which deliberately stays session-only
(see PHASE6.md item 1's "Key design decisions" for why insights, not episodes,
are the persistence unit). `persist_path=None` (every existing caller, unchanged)
keeps `ReflectionMemory` exactly as it always was; `load_insights()` is the
"load at construction, append incrementally" entry point a caller like
`village.py` uses to resume a persona's distilled memory across a restart —
the same idiom `skill_library.py::SkillLibrary`/`curriculum.py::
CurriculumController` already established for their own ledgers.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ._textindex import _terms, score_terms, weighted_terms


@dataclass
class Episode:
    """One remembered event."""

    tick: int
    kind: str  # "skill", "journal", "goal", "speech", ...
    summary: str
    reward: float = 0.0
    pos: tuple[int, int] | None = None

    def __str__(self) -> str:
        r = f" ({self.reward:+.1f})" if self.reward else ""
        return f"[t{self.tick}] {self.summary}{r}"


class EpisodicMemory:
    """A bounded, append-only event log with simple retrieval."""

    def __init__(self, capacity: int = 500) -> None:
        self._eps: deque[Episode] = deque(maxlen=capacity)
        #: Monotonic count of every `record()` call ever made — unlike `len()`,
        #: this survives the deque's bounded truncation, so it's what cadence
        #: logic (e.g. "M new episodes since the last reflection") should compare.
        self.total_recorded = 0

    def record(self, ep: Episode) -> None:
        self._eps.append(ep)
        self.total_recorded += 1

    def recent(self, n: int = 10) -> list[Episode]:
        if n <= 0:
            return []
        return list(self._eps)[-n:]

    def by_kind(self, kind: str) -> list[Episode]:
        return [e for e in self._eps if e.kind == kind]

    def total_reward(self) -> float:
        return sum(e.reward for e in self._eps)

    def __len__(self) -> int:
        return len(self._eps)


@dataclass
class Insight:
    """A short reflective takeaway distilled from a run of episodes.

    Records which episodes it covered (by tick range + count) so an insight stays
    traceable back to the memory that produced it.
    """

    text: str
    episode_ticks: tuple[int, int]  # (first, last) tick among the episodes covered
    episode_count: int

    def __str__(self) -> str:
        return self.text


#: Guards concurrent appends to a single `persist_path` file across threads —
#: mirrors `curriculum.py::_milestones_log_lock`/`skill_library.py::_ledger_lock`
#: exactly (several agents' `ReflectingCognition`s could reflect around the same
#: wall-clock moment in a multi-agent `village.py` roster, same justification
#: those two ledgers already give). One module-level lock for every
#: `ReflectionMemory` instance, not one per instance — matches both precedents.
_insights_log_lock = threading.Lock()


class ReflectionMemory:
    """A bounded log of `Insight`s the slow loop has distilled from episodic memory.

    `record()` runs on `cognition.ReflectingCognition`'s background reflection
    thread; `recent()` is read from whatever thread is running the cognition pass
    (itself possibly `ThreadedCognition`'s background thread) — so the two can run
    concurrently. No explicit lock around the in-memory deque: `record()`'s
    `deque.append` is a single call, which CPython documents as thread-safe
    (including the implicit `popleft` a bounded `maxlen` deque does on overflow);
    `recent()`'s `list(deque)` snapshot is not *documented* thread-safe but is a
    single C call with no GIL release points on today's GIL builds — worst case
    it raises cleanly, never returns a torn deque. Revisit with a lock if
    free-threaded CPython becomes a target.

    `persist_path`/`agent_key` (both optional, PHASE6.md item 1) add a disk-backed
    write: when `persist_path` is set, `record()` additionally appends one JSON
    line (`{ts, agent_key, text, episode_ticks, episode_count}`) to that file,
    guarded by `_insights_log_lock` (the disk write, unlike the deque append
    above, genuinely needs a lock — several threads' `open(..., "a")`/`write()`
    calls could otherwise interleave). `persist_path=None` (the default) makes
    this a byte-for-byte no-op — every existing caller (today: `cognition.
    ReflectingCognition.__init__`'s own default instance) is unaffected. See
    `load_insights()` below for the read side.
    """

    def __init__(self, capacity: int = 20, *, persist_path: str | Path | None = None,
                 agent_key: str | None = None) -> None:
        self._insights: deque[Insight] = deque(maxlen=capacity)
        self.persist_path = Path(persist_path) if persist_path is not None else None
        self.agent_key = agent_key

    def record(self, insight: Insight) -> None:
        self._insights.append(insight)
        if self.persist_path is not None:
            self._persist(insight)

    def _persist(self, insight: Insight) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent_key": self.agent_key,
            "text": insight.text,
            "episode_ticks": list(insight.episode_ticks),
            "episode_count": insight.episode_count,
        }
        try:
            with _insights_log_lock:
                self.persist_path.parent.mkdir(parents=True, exist_ok=True)
                with self.persist_path.open("a") as f:
                    f.write(json.dumps(record) + "\n")
        except OSError:
            pass  # degrade silently — matches skill_library.py's own logging discipline

    def recent(self, n: int = 3) -> list[Insight]:
        if n <= 0:
            return []
        return list(self._insights)[-n:]

    def relevant(self, query: str, k: int = 3) -> list[Insight]:
        """Return the most topically relevant insights, with recency as a tie-break.

        When the shared keyword index finds no overlap at all, preserve the
        existing recency behavior exactly. This keeps relevance retrieval a
        safe opt-in over the established ``recent()`` baseline.
        """
        if k <= 0:
            return []
        query_terms = _terms(query)
        insights = list(self._insights)
        scored = [
            (score_terms(query_terms, weighted_terms((insight.text, 1))), index, insight)
            for index, insight in enumerate(insights)
        ]
        if not any(score for score, _, _ in scored):
            return self.recent(k)
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [insight for score, _, insight in scored if score > 0][:k]

    def __len__(self) -> int:
        return len(self._insights)


def load_insights(path: str | Path, agent_key: str, capacity: int = 20) -> ReflectionMemory:
    """Read every parseable, `agent_key`-matching line of `path` and seed a
    `ReflectionMemory` already wired (`persist_path=path, agent_key=agent_key`)
    to keep appending to the same file — the "load at construction, write
    incrementally" idiom `skill_library.py::SkillLibrary`/`curriculum.py::
    CurriculumController` both already establish for their own ledgers, applied
    here for the first time to `ReflectionMemory`.

    A missing `path` yields an empty (but correctly wired) `ReflectionMemory` —
    the fresh-persona case, not an error. A corrupted/partial trailing line, or
    any line that isn't a well-formed insight record, is skipped rather than
    fatal — the same `skill_library.py::_read_ledger`/`curriculum.py::
    _load_achieved` "degrade, never crash" discipline. Rows are appended in the
    file's own (oldest-first) order, so the bounded deque's own `maxlen`
    truncation naturally keeps only the most recent `capacity` — matching
    `ReflectionMemory`'s own recency contract.
    """
    mem = ReflectionMemory(capacity=capacity, persist_path=path, agent_key=agent_key)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return mem
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # corrupted/partial trailing line — skip, never fatal
        if not isinstance(record, dict) or record.get("agent_key") != agent_key:
            continue
        text_val, ticks, count = record.get("text"), record.get("episode_ticks"), record.get("episode_count")
        if not isinstance(text_val, str) or not (isinstance(ticks, list) and len(ticks) == 2):
            continue
        try:
            episode_ticks = (int(ticks[0]), int(ticks[1]))
            episode_count = int(count)
        except (TypeError, ValueError):
            continue
        mem._insights.append(Insight(text=text_val, episode_ticks=episode_ticks, episode_count=episode_count))
    return mem
