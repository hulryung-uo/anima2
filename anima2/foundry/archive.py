"""Genome archive + MAP-Elites grid (PHASE5.md item 3, DESIGN.md §6.6) —
kernel-owned. Ports v1 `../anima/foundry/kernel/archive.py`'s promotion rule
and grid mechanics; the `Genome` shape and the persistence layer are adapted
to anima2's config-only genome space (see class docstrings below for every
adaptation, stated plainly — the promotion rule itself, the integrity-
critical part, is ported **verbatim**).

Stores every evaluated genome and maintains the QD grid: a map from behavior
cell (`descriptor.py::Descriptor.cell`) -> the single highest-*reliability*
genome with that behavior. The promotion rule (who enters/replaces a cell) is
integrity-critical and lives here in the kernel — same as v1, where the
*selection policy* (which parent to mutate next, item 4's concern) is kept
out of this module entirely.

This module keeps to kernel-only imports (stdlib + `anima2.foundry.*`).
Note the direction of the guard: `tests/test_foundry_import_guard.py`
mechanically asserts the LEARNING code (`anima2/skills/`, `curriculum.py`,
`skill_tuning.py`, `cognition.py`, `skill_library.py`) never imports
`anima2.foundry` — the ruler is unreachable from the measured code; the
kernel's own import hygiene is convention, enforced by review.
"""

from __future__ import annotations

import json
import statistics
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ._filelock import append_line_locked

# Promotion is decided on a reliability-discounted score (a lower-confidence
# bound), NOT the raw multi-seed mean — ported VERBATIM from v1
# `../anima/foundry/kernel/archive.py:28-44`. With run-to-run variance,
# comparing point means lets a cell fill with whichever genome got the
# luckiest run — the "optimizer's curse" (v1's own provenance comment,
# adapted: observed 2026-06-12 in v1 Foundry, `g_00070` recorded 91.9 from a
# lucky seed triple [49, 50, 17], held-out mean 39 — its lucky run displaced
# a steadier elite that would have held up). R = mean - LAMBDA*pstdev
# penalizes BOTH a low mean and high spread, so a steady genome beats a
# volatile one of equal (or even higher) raw mean. Porting the raw-fitness
# rule instead would silently reintroduce that exact failure.
PROMOTION_LAMBDA = 1.0


def reliability_score(per_seed: list[float], point: float) -> float:
    """Lower-confidence bound used for promotion. Falls back to the point
    estimate when per-seed data is unavailable (single-seed or legacy).
    Ported VERBATIM from v1 `archive.py`."""
    vals = [float(v) for v in per_seed] if per_seed else []
    if len(vals) < 2:
        return point
    return statistics.fmean(vals) - PROMOTION_LAMBDA * statistics.pstdev(vals)


def cell_to_str(cell: tuple) -> str:
    return "|".join(str(p) for p in cell)


@dataclass
class Genome:
    """An anima2 agent CONFIGURATION — never code (PHASE5.md item 3's own
    "Key design decisions": "Genome = config, not code").

    **Adapted from v1's `Genome` shape (stated plainly).** v1's genome
    carried an opaque `config: dict` (a persona/params blob) plus `code_ref`
    (a git SHA of a *mutated worktree* — in v1, a genome variant literally
    WAS a code diff). anima2's genomes never touch code at all: evolution
    (item 4) only ever nudges four hand-defined axes — `profession.py`'s
    profession keys, `persona.py::Persona.talkativeness` (the "persona
    sociability" axis PHASE5.md names), `skill_tuning.py`'s
    `DELIVER_THRESHOLD_CANDIDATES` grid, and `llm.py`'s cognition tier keys
    (`_TIER_MODEL`) — so this port replaces the opaque dict + code_ref with
    four NAMED, TYPED fields spanning exactly that space. This makes "never
    code" a schema-level fact (you cannot put a code diff in a
    `deliver_threshold: float`), not just a documented convention. `id` /
    `parent` / `eval` / `hypothesis` / `ts` and the `fitness` /
    `reliability` / `cell` properties are ported over unchanged (same names,
    same semantics, all still reading from `eval` — the caller, e.g. item
    2's eval harness, populates `eval` with `fitness` / `cell` / `descriptor`
    / `per_seed_fitness` / `seed`, exactly like v1).
    """

    id: str
    profession: str = "miner"
    sociability: float = 0.3         # persona.py Persona.talkativeness axis
    deliver_threshold: float = 8.0   # skill_tuning.py DELIVER_THRESHOLD_CANDIDATES grid
    cognition_tier: str = "cheap"    # llm.py _TIER_MODEL key ("cheap"/"standard"/"heavy")
    parent: str | None = None
    eval: dict[str, Any] = field(default_factory=dict)
    hypothesis: str = ""
    ts: float = 0.0

    @property
    def fitness(self) -> float:
        return float(self.eval.get("fitness", 0.0))

    @property
    def reliability(self) -> float:
        """Promotion score: reliability-discounted, not the raw mean."""
        return reliability_score(self.eval.get("per_seed_fitness", []), self.fitness)

    @property
    def cell(self) -> tuple:
        return tuple(self.eval.get("cell", ()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Genome":
        return cls(
            id=d["id"],
            profession=d.get("profession", "miner"),
            sociability=d.get("sociability", 0.3),
            deliver_threshold=d.get("deliver_threshold", 8.0),
            cognition_tier=d.get("cognition_tier", "cheap"),
            parent=d.get("parent"),
            eval=d.get("eval", {}),
            hypothesis=d.get("hypothesis", ""),
            ts=d.get("ts", 0.0),
        )


@dataclass
class InsertResult:
    """Ported verbatim from v1 `archive.py::InsertResult`."""

    status: str          # "filled" (new cell) | "improved" | "rejected"
    cell: tuple
    fitness: float
    prev_fitness: float | None = None

    @property
    def entered_grid(self) -> bool:
        return self.status in ("filled", "improved")


#: `data/archive.jsonl` relative to the process's cwd — mirrors this
#: project's existing cross-process ledger convention exactly
#: (`skill_library.py`'s `_DEFAULT_LEDGER`, `llm.py`'s `_DEFAULT_USAGE_LOG`):
#: gitignored (`data/`), created lazily. Tests must always pass an explicit
#: `path=` (a `tmp_path`) so the suite never touches the real file.
_DEFAULT_ARCHIVE_PATH = Path("data") / "archive.jsonl"

#: Guards concurrent appends to a single archive file across threads/processes
#: within this interpreter — mirrors `skill_library.py::_ledger_lock` /
#: `llm.py::_usage_log_lock`.
_archive_lock = threading.Lock()


class Archive:
    """Cross-process-readable genome store + MAP-Elites grid.

    **Persistence: a single append-only `data/archive.jsonl`, not v1's
    directory-of-files (adaptation, stated plainly and justified).** v1
    splits state across a `genomes/g_NNNNN.json` file per genome (full
    lineage) plus a separate `grid.json` index (cell -> current elite id).
    PHASE5.md item 3 asks for one kernel-owned `data/archive.jsonl`, so this
    port collapses both into a single append-only log instead of choosing
    "rewrite the whole file on every add":

    - **Append, not rewrite, chosen because:** (a) it mirrors the house
      convention this codebase already uses for every other cross-process
      ledger — `skill_library.py::SkillLibrary`'s `data/skill_ledger.jsonl`
      and `llm.py`'s `data/llm_usage.jsonl` are both append-only,
      corrupt-line-tolerant, lazily rebuilt from disk on read — rather than
      inventing a second persistence convention for this one file; and (b)
      an append is a single `write()` of one already-serialized line, so a
      crash mid-write can corrupt at most that last (trailing, partial)
      line, caught by the corrupt-line-tolerant reader below — a
      rewrite-in-place risks losing the WHOLE archive if interrupted
      mid-write, a strictly worse failure mode for a growing evolution run.
    - **Full lineage is kept**, exactly like v1's `genomes_dir`: `add()`
      appends one line per genome regardless of grid outcome (`rejected`
      genomes are never deleted — only the grid's elite pointer moves).
    - **Corrupt-line-tolerant read** matches `skill_library.py::
      SkillLibrary._read_ledger`'s "skip a malformed line, never fatal"
      discipline exactly: a partial trailing line from an interrupted write
      is silently skipped, not fatal.
    - **The grid is not persisted separately** (unlike v1's `grid.json`) —
      it is deterministically *rebuilt* by replaying every genome line
      through the identical promotion rule `add()` uses, in file order, on
      construction (`_load()`). This is what makes the round-trip real: a
      fresh `Archive(path)` in a second process reconstructs the exact same
      `elites()`/`summary()` a live process would report, purely from the
      log, without a second index file that could drift out of sync with it.
    """

    def __init__(self, path: str | Path = _DEFAULT_ARCHIVE_PATH) -> None:
        self.path = Path(path)
        self._genomes: dict[str, Genome] = {}   # id -> Genome, in first-seen order
        self.grid: dict[str, str] = {}        # cell_str -> current elite genome id
        self._load()

    # ---- ids ---------------------------------------------------------------
    def next_id(self) -> str:
        n = len(self._genomes) + 1
        return f"g_{n:05d}"

    # ---- persistence ---------------------------------------------------------
    def _load(self) -> None:
        """Replay every parseable line of `self.path`, in file order, through
        the same promotion rule `add()` uses — reconstructs `self._genomes`
        (full lineage) and `self.grid` (elites) identically to how they'd
        look had this process made every `add()` call itself. A missing file
        starts empty (no genomes yet — not an error, matches
        `SkillLibrary._read_ledger`'s "missing file -> no lines")."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or "id" not in record:
                continue
            try:
                g = Genome.from_dict(record)
                self._insert_no_persist(g)
            except (KeyError, TypeError, ValueError, statistics.StatisticsError):
                # A well-formed JSON line can still carry wrong-typed fields
                # (e.g. a non-list per_seed) that only blow up inside the
                # promotion-rule property access — same degrade-never-crash
                # bar as the JSON parse above.
                continue

    def _append(self, g: Genome) -> None:
        """Append one genome line. A broken/unwritable archive degrades
        silently (never raises) — matches `skill_library.py::
        SkillLibrary.record_outcome`'s "never break the caller over a
        logging failure" discipline; the in-memory archive stays correct
        even if the disk write fails.

        Guarded by BOTH `_archive_lock` (same-process thread safety — kept,
        cheap, harmless) AND `append_line_locked`'s `fcntl.flock` (PHASE5.md
        item 4's cross-process ledger-write safety follow-up — see
        `_filelock.py`'s own module docstring for why the threading lock
        alone was never enough for a future multi-process run)."""
        try:
            with _archive_lock:
                append_line_locked(self.path, json.dumps(g.to_dict()))
        except OSError:
            return

    # ---- the promotion rule (integrity-critical, ported verbatim) -----------
    def add(self, g: Genome) -> InsertResult:
        """Persist genome (append) and insert into the grid iff it is the
        new elite.

        Rule ported VERBATIM from v1 `archive.py::Archive.add`: enter the
        cell if it is empty (`filled`) or this genome's RELIABILITY score
        (mean - lambda*pstdev across seeds) strictly exceeds the current
        occupant's (`improved`). Promoting on the reliability bound, not the
        raw mean, stops a single lucky run from crowning a volatile genome
        over a steadier one. The genome is always persisted regardless of
        grid outcome (full lineage is kept; the grid only tracks elites).
        """
        result = self._insert_no_persist(g)
        self._append(g)
        return result

    def _insert_no_persist(self, g: Genome) -> InsertResult:
        self._genomes[g.id] = g
        cell_str = cell_to_str(g.cell)
        cur_id = self.grid.get(cell_str)

        if cur_id is None:
            self.grid[cell_str] = g.id
            return InsertResult("filled", g.cell, g.fitness, None)

        cur = self._genomes.get(cur_id)
        prev_fit = cur.fitness if cur else None
        prev_rel = cur.reliability if cur else None
        if prev_rel is None or g.reliability > prev_rel:
            self.grid[cell_str] = g.id
            return InsertResult("improved", g.cell, g.fitness, prev_fit)

        return InsertResult("rejected", g.cell, g.fitness, prev_fit)

    # ---- queries (read-only; selection policy is item 4's concern) ---------
    def get(self, gid: str) -> Genome | None:
        return self._genomes.get(gid)

    def all_genomes(self) -> list[Genome]:
        return list(self._genomes.values())

    def get_elite(self, cell: tuple) -> Genome | None:
        gid = self.grid.get(cell_to_str(cell))
        return self._genomes.get(gid) if gid else None

    def elites(self) -> list[Genome]:
        out = []
        for gid in self.grid.values():
            g = self._genomes.get(gid)
            if g:
                out.append(g)
        return out

    def filled_cells(self) -> int:
        return len(self.grid)

    def qd_score(self) -> float:
        """Sum of elite fitnesses — the standard QD progress metric (telemetry)."""
        return sum(g.fitness for g in self.elites())

    def best(self) -> Genome | None:
        """Raw-fitness argmax — display/telemetry only (`summary()`). For any
        reliability-based decision use `best_by_reliability`: picking a champion
        by raw mean and then reading its reliability re-imports the optimizer's
        curse the reliability discount exists to prevent (a lucky high-variance
        genome can out-mean a steady one while carrying a worse discounted
        score)."""
        es = self.elites()
        return max(es, key=lambda g: g.fitness) if es else None

    def best_by_reliability(self) -> Genome | None:
        """Reliability argmax over the elites — the selector for comparative
        verdicts (the live evolve gate's margin, the offline convergence test)."""
        es = self.elites()
        return max(es, key=lambda g: g.reliability) if es else None

    def summary(self) -> dict[str, Any]:
        es = self.elites()
        return {
            "total_genomes": len(self._genomes),
            "filled_cells": self.filled_cells(),
            "qd_score": round(self.qd_score(), 3),
            "best_fitness": round(self.best().fitness, 3) if es else 0.0,
            "cells": {c: gid for c, gid in sorted(self.grid.items())},
        }
