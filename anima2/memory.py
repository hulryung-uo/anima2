"""Episodic + reflection memory — what happened, and what the agent made of it.

The fast loop records `Episode`s (skill outcomes with rewards, notable events);
the slow cognition loop reads recent episodes to set goals and reflect, and
aggregate reward is the backbone learning signal (DESIGN.md §6). `ReflectionMemory`
is the Generative-Agents-style layer on top: the slow loop periodically distills a
run of episodes into a handful of short `Insight`s (PHASE2.md B1), which persist
and feed back into later goal/speech decisions (see `cognition.ReflectingCognition`).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


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


class ReflectionMemory:
    """A bounded log of `Insight`s the slow loop has distilled from episodic memory.

    `record()` runs on `cognition.ReflectingCognition`'s background reflection
    thread; `recent()` is read from whatever thread is running the cognition pass
    (itself possibly `ThreadedCognition`'s background thread) — so the two can run
    concurrently. No explicit lock here: `record()` is a single `deque.append`,
    which CPython documents as thread-safe (including the implicit `popleft` a
    bounded `maxlen` deque does on overflow); `recent()`'s `list(deque)` snapshot
    is not *documented* thread-safe but is a single C call with no GIL release
    points on today's GIL builds — worst case it raises cleanly, never returns a
    torn deque. Revisit with a lock if free-threaded CPython becomes a target.
    """

    def __init__(self, capacity: int = 20) -> None:
        self._insights: deque[Insight] = deque(maxlen=capacity)

    def record(self, insight: Insight) -> None:
        self._insights.append(insight)

    def recent(self, n: int = 3) -> list[Insight]:
        if n <= 0:
            return []
        return list(self._insights)[-n:]

    def __len__(self) -> int:
        return len(self._insights)
