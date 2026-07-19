"""Bounded, resumable high-level goal lifecycle.

The fast-loop skill contract continues to use :class:`skills.base.Goal`
directly.  This module adds lifecycle metadata around that exact object; it
never copies or rewrites a goal's ``kind`` or ``params``.

Only the top frame may be active.  Pushing a child suspends its parent, and
finishing or expiring the child resumes that parent.  Terminal frames leave the
live stack and enter a bounded history, making completion observable without
allowing an old frame to become runnable again.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import math

from .contract import Observation, Position
from .skills.base import Goal


class GoalState(Enum):
    """A frame's non-terminal or terminal lifecycle state."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    TERMINAL = "terminal"


class GoalSource(Enum):
    """The closed set of authorities that may introduce a goal."""

    USER = "user"
    COGNITION = "cognition"
    SYSTEM = "system"
    SKILL = "skill"


class GoalOutcome(Enum):
    """Why a frame left the live stack."""

    SUCCESS = "success"
    FAILURE = "failure"
    EXPIRED = "expired"
    REPLACED = "replaced"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class GoalProgress:
    """Immutable, observation-derived progress owned by one goal frame."""

    started_tick: int
    last_active_tick: int
    active_ticks: int = 0
    attempts: int = 0
    note: str | None = None
    value: float = 0.0
    evidence_count: int = 0
    initial_distance: int | None = None
    best_distance: int | None = None
    last_position: tuple[int, int, int] | None = None

    def observe_active(self, tick: int) -> GoalProgress:
        """Return an active-time update; observing one tick twice is idempotent."""

        _require_tick(tick)
        if tick < self.last_active_tick:
            raise ValueError("goal progress tick cannot move backwards")
        if tick == self.last_active_tick:
            return self
        return self._copy(active_ticks=self.active_ticks + 1, last_active_tick=tick)

    def record_attempt(self, tick: int, *, note: str | None = None) -> GoalProgress:
        """Return a snapshot with one additional bounded-work attempt."""

        active = self.observe_active(tick)
        return active._copy(
            attempts=active.attempts + 1,
            note=active.note if note is None else note,
        )

    def observe(
        self,
        goal: Goal,
        observation: Observation,
        tick: int,
    ) -> tuple[GoalProgress, bool]:
        """Return ``(snapshot, evidence_changed)`` from one world observation.

        A ``goto`` snapshot normalizes its best observed distance against the
        first distance. Standing still adds no evidence, and a detour can never
        decrease ``value``.
        """

        active = self.observe_active(tick)
        pos = observation.player.pos
        current = (pos.x, pos.y, pos.z)
        target = goal.params.get("target") if goal.kind == "goto" else None

        if active.last_position is None:
            initial_distance = active.initial_distance
            best_distance = active.best_distance
            value = active.value
            if isinstance(target, Position):
                distance = max(abs(pos.x - target.x), abs(pos.y - target.y))
                initial_distance = distance
                best_distance = distance
                value = 1.0 if distance == 0 else 0.0
            return (
                active._copy(
                    value=value,
                    initial_distance=initial_distance,
                    best_distance=best_distance,
                    last_position=current,
                ),
                True,
            )

        if current == active.last_position:
            return active, False

        value = active.value
        initial_distance = active.initial_distance
        best_distance = active.best_distance
        if isinstance(target, Position):
            distance = max(abs(pos.x - target.x), abs(pos.y - target.y))
            best_distance = distance if best_distance is None else min(best_distance, distance)
            if initial_distance is None:
                initial_distance = distance
                best_distance = distance
            candidate = (
                1.0
                if initial_distance == 0
                else 1.0 - min(initial_distance, best_distance) / initial_distance
            )
            value = max(value, min(1.0, max(0.0, candidate)))
        return (
            active._copy(
                value=value,
                evidence_count=active.evidence_count + 1,
                initial_distance=initial_distance,
                best_distance=best_distance,
                last_position=current,
            ),
            True,
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, GoalProgress):
            return NotImplemented
        return self.value < other.value

    def _copy(self, **changes: object) -> GoalProgress:
        values = dict(vars(self))
        values.update(changes)
        return GoalProgress(**values)  # type: ignore[arg-type]


@dataclass
class GoalFrame:
    """A goal plus stable identity and lifecycle metadata.

    ``goal`` is the caller's original object.  It is deliberately not cloned so
    existing identity-based live gates and callers keep working.
    """

    id: int
    goal: Goal
    source: GoalSource
    state: GoalState
    created_tick: int
    deadline_tick: int | None = None
    progress: GoalProgress = field(default_factory=lambda: GoalProgress(0, 0))
    outcome: GoalOutcome | None = None
    finished_tick: int | None = None

    @property
    def terminal(self) -> bool:
        return self.state is GoalState.TERMINAL


class GoalStack:
    """A bounded LIFO goal stack with fail-closed top-only transitions."""

    def __init__(self, *, max_depth: int = 16, history_limit: int = 128) -> None:
        if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth <= 0:
            raise ValueError("max_depth must be a positive integer")
        if (
            not isinstance(history_limit, int)
            or isinstance(history_limit, bool)
            or history_limit < 0
        ):
            raise ValueError("history_limit must be a non-negative integer")
        self.max_depth = max_depth
        self.history_limit = history_limit
        self._frames: list[GoalFrame] = []
        self._history: list[GoalFrame] = []
        self._revision = 0
        self._next_id = 1

    @property
    def revision(self) -> int:
        """Monotonic mutation revision for stale cognition CAS checks."""

        return self._revision

    @property
    def current(self) -> GoalFrame | None:
        return self._frames[-1] if self._frames else None

    @property
    def current_frame(self) -> GoalFrame | None:
        """Compatibility name used by Agent and goal-stack observers."""

        return self.current

    @property
    def top(self) -> GoalFrame | None:
        """Alias for :attr:`current`, matching ordinary stack terminology."""

        return self.current

    @property
    def current_goal(self) -> Goal | None:
        frame = self.current
        return frame.goal if frame is not None else None

    @property
    def frames(self) -> tuple[GoalFrame, ...]:
        """Bottom-to-top immutable view of live frames."""

        return tuple(self._frames)

    @property
    def history(self) -> tuple[GoalFrame, ...]:
        """Oldest-to-newest immutable view of retained terminal frames."""

        return tuple(self._history)

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def depth(self) -> int:
        return len(self._frames)

    def push(
        self,
        goal: Goal,
        *,
        source: GoalSource = GoalSource.USER,
        tick: int,
        deadline_tick: int | None = None,
    ) -> GoalFrame:
        """Suspend the current frame and activate ``goal`` on top."""

        self._validate_new(goal, source, tick, deadline_tick)
        if len(self._frames) >= self.max_depth:
            raise OverflowError(f"goal stack depth limit reached ({self.max_depth})")
        if self._frames:
            self._frames[-1].state = GoalState.SUSPENDED
        frame = self._new_frame(goal, source, tick, deadline_tick)
        self._frames.append(frame)
        self._changed()
        self._assert_invariants()
        return frame

    def replace(
        self,
        goal: Goal,
        *,
        source: GoalSource = GoalSource.USER,
        tick: int,
        deadline_tick: int | None = None,
        expected_id: int | None = None,
    ) -> GoalFrame:
        """Terminally replace only the current frame.

        A suspended parent, if present, remains suspended beneath the new frame.
        On an empty stack this is equivalent to :meth:`push`.
        """

        self._validate_new(goal, source, tick, deadline_tick)
        if not self._frames:
            if expected_id is not None:
                raise LookupError("cannot match expected_id on an empty goal stack")
            return self.push(
                goal,
                source=source,
                tick=tick,
                deadline_tick=deadline_tick,
            )
        current = self._require_current(expected_id)
        if tick < current.created_tick:
            raise ValueError("goal replacement tick cannot precede current goal creation")
        old = self._frames.pop()
        self._archive(old, GoalOutcome.REPLACED, tick)
        frame = self._new_frame(goal, source, tick, deadline_tick)
        self._frames.append(frame)
        self._changed()
        self._assert_invariants()
        return frame

    def finish(
        self,
        outcome: GoalOutcome,
        *,
        tick: int,
        expected_id: int | None = None,
    ) -> GoalFrame:
        """Finish and archive the current frame, then resume its parent."""

        _require_tick(tick)
        if not isinstance(outcome, GoalOutcome):
            raise TypeError("outcome must be a GoalOutcome")
        frame = self._require_current(expected_id)
        if tick < frame.created_tick:
            raise ValueError("goal terminal tick cannot precede creation")
        self._frames.pop()
        self._archive(frame, outcome, tick)
        if self._frames:
            self._frames[-1].state = GoalState.ACTIVE
        self._changed()
        self._assert_invariants()
        return frame

    def expire_due(self, tick: int) -> tuple[GoalFrame, ...]:
        """Expire every due frame, including suspended parents.

        Deadlines are absolute fast-loop ticks, not active-time budgets.  A
        safety interrupt therefore cannot accidentally keep an obsolete parent
        alive forever.  Surviving frames keep their order and the newest one is
        made active.
        """

        _require_tick(tick)
        expired = [
            frame
            for frame in reversed(self._frames)
            if frame.deadline_tick is not None and tick >= frame.deadline_tick
        ]
        if expired:
            expired_ids = {frame.id for frame in expired}
            self._frames = [frame for frame in self._frames if frame.id not in expired_ids]
            for frame in expired:
                self._archive(frame, GoalOutcome.EXPIRED, tick)
            for frame in self._frames:
                frame.state = GoalState.SUSPENDED
            if self._frames:
                self._frames[-1].state = GoalState.ACTIVE
            self._changed()
            self._assert_invariants()
        return tuple(expired)

    def observe_active(self, tick: int) -> None:
        """Advance the current frame's progress without changing stack revision."""

        frame = self.current
        if frame is not None:
            frame.progress = frame.progress.observe_active(tick)

    def invalidate_proposals(self) -> None:
        """Advance the CAS revision for an out-of-stack safety transition."""

        self._changed()

    def observe(self, observation: Observation, tick: int) -> bool:
        """Update top-frame progress and revision from observed world evidence."""

        if not isinstance(observation, Observation):
            raise TypeError("observation must be a contract.Observation")
        _require_tick(tick)
        frame = self.current
        if frame is None:
            return False
        progress, changed = frame.progress.observe(frame.goal, observation, tick)
        frame.progress = progress
        if changed:
            self._changed()
        return changed

    def set_progress(
        self,
        value: float,
        *,
        tick: int,
        note: str | None = "policy",
    ) -> bool:
        """Merge trusted policy progress into the active frame monotonically."""

        _require_tick(tick)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError("goal progress value must be numeric")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValueError("goal progress value must be finite")
        normalized = max(0.0, min(1.0, normalized))
        frame = self.current
        if frame is None or normalized <= frame.progress.value:
            return False
        active = frame.progress.observe_active(tick)
        frame.progress = replace(
            active,
            value=normalized,
            note=note,
            evidence_count=active.evidence_count + 1,
        )
        self._changed()
        return True

    def _new_frame(
        self,
        goal: Goal,
        source: GoalSource,
        tick: int,
        deadline_tick: int | None,
    ) -> GoalFrame:
        frame = GoalFrame(
            id=self._next_id,
            goal=goal,
            source=source,
            state=GoalState.ACTIVE,
            created_tick=tick,
            deadline_tick=deadline_tick,
            progress=GoalProgress(started_tick=tick, last_active_tick=tick),
        )
        self._next_id += 1
        return frame

    def _archive(self, frame: GoalFrame, outcome: GoalOutcome, tick: int) -> None:
        if tick < frame.created_tick:
            raise ValueError("goal terminal tick cannot precede creation")
        frame.state = GoalState.TERMINAL
        frame.outcome = outcome
        frame.finished_tick = tick
        if self.history_limit == 0:
            return
        self._history.append(frame)
        overflow = len(self._history) - self.history_limit
        if overflow > 0:
            del self._history[:overflow]

    def _validate_new(
        self,
        goal: Goal,
        source: GoalSource,
        tick: int,
        deadline_tick: int | None,
    ) -> None:
        if not isinstance(goal, Goal):
            raise TypeError("goal must be a skills.base.Goal")
        if not isinstance(source, GoalSource):
            raise TypeError("source must be a GoalSource")
        _require_tick(tick)
        if deadline_tick is not None:
            _require_tick(deadline_tick, name="deadline_tick")
            if deadline_tick < tick:
                raise ValueError("deadline_tick cannot precede creation tick")

    def _require_current(self, expected_id: int | None) -> GoalFrame:
        if not self._frames:
            raise LookupError("goal stack is empty")
        frame = self._frames[-1]
        if expected_id is not None and frame.id != expected_id:
            raise LookupError(
                f"current goal id {frame.id} does not match expected id {expected_id}"
            )
        return frame

    def _changed(self) -> None:
        self._revision += 1

    def _assert_invariants(self) -> None:
        if len(self._frames) > self.max_depth:
            raise AssertionError("goal stack exceeded its configured depth")
        for index, frame in enumerate(self._frames):
            expected = GoalState.ACTIVE if index == len(self._frames) - 1 else GoalState.SUSPENDED
            if (
                frame.state is not expected
                or frame.outcome is not None
                or frame.finished_tick is not None
            ):
                raise AssertionError("live goal frame lifecycle invariant violated")
        if any(not frame.terminal or frame.outcome is None for frame in self._history):
            raise AssertionError("goal history contains a non-terminal frame")


def _require_tick(value: int, *, name: str = "tick") -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
