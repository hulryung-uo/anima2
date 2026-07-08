"""Skill parameter tuning — a discrete-grid UCB1 bandit over an existing skill constant.

PHASE4.md item 4 (DESIGN.md A3): genuinely closes the "bandit/Q-learning later"
seam `planner.py` itself names (`Planner.select`'s own docstring: "rules first;
bandit/Q-learning later") — scoped down to the one place today's one-work-
skill-per-profession architecture actually presents a real tunable, not a
speculative multi-skill selection problem. The tunable: `skills/smelt.py::
MineSmeltDeliver.deliver_threshold` — a real, already-live-tested (`live_trade.py
--deliver-threshold`) `int` **class attribute** (no constructor involved —
`MineSmeltDeliver` defines no `__init__`, nor does anything else in its MRO), so
a bandit can pick a value and set it via plain post-construction attribute
assignment, exactly like the existing CLI flag already does.

Not a code-generation system and not a general parameter-search framework: one
`ParamTuner` picks one value for one `(skill_name, param_name)` pair, once per
agent-session (construction time — see `village.py`'s wiring and this item's
"Key design decisions": session granularity, never mid-session re-tuning), and
the outcome is recorded through item 3's own `SkillLibrary.record_outcome(...,
param=, param_value=)` — no new persistence format. `ParamTuner.
load_from_ledger()` reconstructs a tuner's pull counts/totals purely by
replaying those same ledger lines through `update()`, so a tuner's state
survives a process restart the same way item 3's `SkillLibrary` ledger does.

**UCB1** (Auer, Cesa-Bianchi & Fischer 2002), not epsilon-greedy or Thompson
sampling — the simplest algorithm that (a) tries every candidate at least once
before ever exploiting, and (b) has a closed-form "how much should I still
explore this arm" term with no tuned epsilon schedule needed. `choose()`
returns the highest upper-confidence-bound candidate; `update()` folds one more
observed reward in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .skill_library import SkillLibrary

#: The one tunable this item lands (PHASE4.md item 4's own "First and only
#: tunable this item lands"): `MineSmeltDeliver.deliver_threshold`, candidates
#: spanning "frequent small deliveries" to "rare large ones" around the
#: existing default (`10`, `skills/smelt.py`) and the value `live_trade.py`'s
#: own default CLI flag already used pre-item-4 (`8`).
DELIVER_THRESHOLD_SPEC = "deliver_threshold"
DELIVER_THRESHOLD_CANDIDATES: tuple[float, ...] = (5, 8, 12, 20)


@dataclass(frozen=True)
class ParamSpec:
    """One tunable: which parameter, and the discrete grid of values to try."""

    name: str
    candidates: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.candidates:
            raise ValueError("ParamSpec.candidates must be non-empty")


@dataclass
class _Arm:
    """One candidate value's running `(count, total_reward)` — the dict-of-
    tuples shape PHASE4.md item 4's own Scope names, as a small mutable
    dataclass instead of a literal tuple (mirrors `skill_library.py::_Accum`'s
    identical choice for the same reason: a tuple would need to be replaced
    wholesale on every update, a dataclass just mutates in place)."""

    count: int = 0
    total_reward: float = 0.0

    @property
    def mean(self) -> float:
        return self.total_reward / self.count if self.count else 0.0


class ParamTuner:
    """UCB1 bandit over `spec.candidates` for one `(skill_name, spec.name)` pair.

    `choose()` is called once per agent-session, at construction time — the
    caller holds the returned value fixed for the whole session (never
    mid-session re-tuning; see PHASE4.md item 4's own "Key design decisions")
    and reports back exactly once, via `update()` (in-process) and/or
    `SkillLibrary.record_outcome(..., param=spec.name, param_value=chosen)`
    (persisted — see `load_from_ledger`).
    """

    def __init__(self, skill_name: str, spec: ParamSpec, *, exploration: float = 2.0) -> None:
        self.skill_name = skill_name
        self.spec = spec
        self.exploration = exploration
        self._arms: dict[float, _Arm] = {c: _Arm() for c in spec.candidates}

    @property
    def total_pulls(self) -> int:
        """Pulls summed across `spec.candidates` only — a stray ledger value
        outside today's candidate grid (e.g. replayed from a since-changed
        `ParamSpec`; see `update()`'s own docstring) is tracked by `update()`
        but never counted here, so a stale candidate can't skew `choose()`'s
        exploration term for the *current* grid."""
        return sum(self._arms[c].count for c in self.spec.candidates)

    def choose(self) -> float:
        """UCB1 pick: any never-pulled candidate first (in `spec.candidates`
        order — deterministic, not random, so an offline test can assert the
        exact first-N picks), then the candidate with the highest upper-
        confidence bound once every candidate has at least one pull.
        """
        for c in self.spec.candidates:
            if self._arms[c].count == 0:
                return c
        total = self.total_pulls  # >= len(candidates) >= 1 here — never log(0)
        log_total = math.log(total)

        def ucb(c: float) -> float:
            arm = self._arms[c]
            return arm.mean + self.exploration * math.sqrt(log_total / arm.count)

        return max(self.spec.candidates, key=ucb)

    def update(self, value: float, reward: float) -> None:
        """Record one observed `reward` for having chosen `value` (e.g. a
        session's mean reward — see `session_mean_reward` below). `value`
        need not be a member of `spec.candidates`: a caller replaying
        `load_from_ledger` against a ledger written under a since-changed
        `ParamSpec` degrades to tracking the stale value as its own arm
        (excluded from `choose()`'s candidate set and `total_pulls` — see
        that property) rather than raising — `choose()` itself never
        produces an off-grid value, so this is only reachable via a stale
        ledger, not live play.
        """
        arm = self._arms.setdefault(value, _Arm())
        arm.count += 1
        arm.total_reward += reward

    def pulls(self) -> dict[float, int]:
        """The empirical pull distribution — `{candidate: count}` — restricted
        to `spec.candidates` (a stale off-grid arm from `update()` is tracked
        internally but not surfaced here, matching `total_pulls`). This is the
        live gate's own decisive evidence: a working tuner concentrates pulls
        on whichever candidate scores best; a broken/no-op one stays flat.
        """
        return {c: self._arms[c].count for c in self.spec.candidates}

    def mean_rewards(self) -> dict[float, float]:
        """`{candidate: mean_reward}` for every candidate — `0.0` for one
        never pulled. Diagnostic companion to `pulls()`, not consulted by
        `choose()` itself (which reads `_arms` directly)."""
        return {c: self._arms[c].mean for c in self.spec.candidates}

    @classmethod
    def load_from_ledger(
        cls,
        path: str | Path,
        skill_name: str,
        param_name: str,
        spec: ParamSpec,
        *,
        exploration: float = 2.0,
    ) -> ParamTuner:
        """Reconstruct a `ParamTuner`'s pull counts/totals from item 3's
        `data/skill_ledger.jsonl` — no new persistence format. Replays, in
        file order, every ledger line whose `skill_name`/`param` match this
        tuner's own via `update()`, so the reconstructed tuner is exactly
        what an in-process sequence of the same `update()` calls would
        produce (proven by `test_load_from_ledger_matches_equivalent_update_
        sequence`). Reuses `SkillLibrary`'s own ledger reader
        (`_read_ledger`) rather than re-implementing "skip a corrupted
        line, tolerate a missing file" a second time — the two modules
        already share one ledger file and record shape by design (PHASE4.md's
        own dependency-order note: "item 4 depends on item 3 ... reuses its
        ledger file and record shape"). A missing or empty ledger file
        initializes every candidate at zero pulls, never raises — matches
        `SkillLibrary.stats()`'s own "degrade, never crash" contract for the
        same file.
        """
        tuner = cls(skill_name, spec, exploration=exploration)
        for record in SkillLibrary(ledger_path=path)._read_ledger():  # noqa: SLF001
            if record.get("skill_name") != skill_name or record.get("param") != param_name:
                continue
            value = record.get("param_value")
            if value is None:
                continue
            try:
                reward = float(record.get("reward", 0.0))
            except (TypeError, ValueError):
                reward = 0.0
            tuner.update(value, reward)
        return tuner


def session_mean_reward(episodes: Any) -> float:
    """Mean reward per recorded episode over a session. Takes anything
    exposing `EpisodicMemory`'s `total_reward()`/`total_recorded` (an
    `Agent.episodes`), not the type itself, so this module needs no import
    of `agent.py`/`memory.py` just for a type hint. `0.0` for a session with
    no recorded episodes — never a `ZeroDivisionError`.

    **Not** what a `ParamTuner` should be fed when comparing candidates —
    live-caught the hard way (PHASE4.md item 4's first live-gate attempt):
    a per-episode mean is unstable across candidates whose sessions either
    run different lengths (an early-stopping session ends sooner for a
    "faster" candidate) or simply accrue episodes at different *rates* even
    over an identical fixed tick budget (a higher `deliver_threshold`
    triggers fewer, larger delivery events than a lower one, so the same
    total value gets divided by a smaller episode count) — neither reflects
    "how much value did this candidate actually produce." Callers recording
    a candidate's outcome should use the session's raw `episodes.
    total_reward()` over a **fixed, non-early-stopped** window instead (see
    `live_trade.py::_run_session`'s own docstring and `village.py`'s
    tuning-wiring comment for the concrete live case) — that quantity is
    directly comparable across candidates precisely because the denominator
    (session length) is held constant by construction, not divided out here.
    This function is kept as a general diagnostic/reporting utility, not
    removed — just not the recommended bandit-feeding objective.
    """
    total_recorded = getattr(episodes, "total_recorded", 0)
    if not total_recorded:
        return 0.0
    return episodes.total_reward() / total_recorded
