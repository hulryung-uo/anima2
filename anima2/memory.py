"""Episodic memory — a rolling log of what happened, for reflection and context.

The fast loop records `Episode`s (skill outcomes with rewards, notable events);
the slow cognition loop reads recent episodes to set goals and reflect, and
aggregate reward is the backbone learning signal (DESIGN.md §6). This is the
offline-first core of workstream B; semantic memory (the wiki) and richer
reflection build on top.
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

    def record(self, ep: Episode) -> None:
        self._eps.append(ep)

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
