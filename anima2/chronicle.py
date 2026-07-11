"""The village chronicle — an inter-agent relationship ledger.

PHASE6.md item 2 (DESIGN.md §10): mines "who helped whom" from the trade/
hunt/market interactions the economy loop already runs live, rather than
inventing new instrumentation inside the skills themselves. `skills/smelt.py
::MineSmeltDeliver`, `skills/craft.py::Blacksmith`, `skills/market.py::
BlacksmithMarket`, and `skills/hunt.py::Hunt` already gate every reward on a
**confirmed** pack delta and already expose the exact phase transitions a
relationship event needs (`ctx.memory["smelt_phase"]`/`["bs_state"]`/
`["mkt_phase"]`/`["hunt_looted"]` — the same keys `curriculum.py::
_mid_transaction`/`_memory_list_len_threshold` already read by name). This
module adds no new skill logic and no contract surface — it's a
`village.py`-level observer over state the economy loop already, provably,
computes correctly; the detector functions that read those keys live in
`village.py` itself (see `_chronicle_events_this_tick` and its four
per-detector helpers there), not here.

**Two operations, deliberately split** (mirrors the tick-thread/main-thread
split `village.py`'s own `deliver_threshold` tuner outcome recording already
establishes — see `village.py`'s module docstring and `run_village`'s own
comments at its `for t in threads: t.join()` call site for the full
precedent trail): `queue_event()` is `threading.Lock`-guarded, O(1), and
**only ever appends to an in-memory list** — no file I/O at all, safe to call
from any of `village.py`'s per-agent tick-driving worker threads.
`flush(path=None)` writes every currently-queued event to
`data/chronicle.jsonl` as one batch of JSON lines and clears the queue — the
one place this module touches disk, called exactly once, from `village.py`'s
**main** thread, strictly after every worker thread has already joined. The
accepted tradeoff, stated plainly rather than hidden: a mid-run crash loses
only that session's queued-but-unflushed events (never a torn/corrupt line,
since nothing is written until the batch flush) — the same tradeoff
`village.py`'s existing tuner-outcome recording already carries.

The read side (`events_for`/`between`/`recent`) reads straight from disk on
every call — no caching layer (the ledger is small, and callers read it
after a session ends, not on a hot path) — corrupt-line-tolerant, mirroring
`skill_library.py::SkillLibrary._read_ledger`'s "degrade, never crash"
discipline exactly.

Raw event tallies, not a decaying trust/affinity score (see PHASE6.md item
2's "Key design decisions" for why): an un-groundable continuous
"relationship strength" number would need a calibrated formula this project
has no ground truth to tune against. `to_persona=None` events (a hunter's
confirmed loot, a blacksmith's confirmed sale/deposit) are kept, not
dropped — real, provenance-safe economic life events, just not a
strictly agent-to-agent edge; `between(a, b)` simply never returns them.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: `data/chronicle.jsonl` relative to the process's cwd — mirrors
#: `skill_library.py`'s `_DEFAULT_LEDGER`/`curriculum.py`'s
#: `_DEFAULT_MILESTONES_LOG` convention exactly (created lazily, gitignored).
#: Tests must always pass an explicit `ledger_path=` (a `tmp_path`) so the
#: suite never touches the real file.
_DEFAULT_CHRONICLE_LEDGER = Path("data") / "chronicle.jsonl"

#: Guards the actual disk write inside `flush()` — mirrors `skill_library.py
#: ::_ledger_lock`/`curriculum.py::_milestones_log_lock`/`memory.py::
#: _insights_log_lock` exactly (module-level, not per-instance: two
#: `ChronicleLedger` instances pointed at the same `ledger_path` — e.g. this
#: module's own "two instances, same ledger" offline test — must not
#: interleave writes). Distinct from each instance's own `_queue_lock` below,
#: which guards the in-memory queue several concurrent *worker threads* on
#: one shared instance contend on — a different lock for a different,
#: narrower critical section, the same "one lock per concern" shape
#: `ReflectionMemory`'s deque-append-vs-disk-write split already uses.
_chronicle_log_lock = threading.Lock()


@dataclass(frozen=True)
class ChronicleEvent:
    """One recorded village-economy event. `to_persona` is `None` for an
    agent-to-world event (a hunter's confirmed loot, a blacksmith's confirmed
    sale to an NPC vendor or deposit at an NPC banker) — vendors/bankers are
    staged NPCs, not `Persona`-bearing agents, so there's no second party to
    name. `kind` is one of `"delivered_ingots"`, `"picked_up_ingots"`,
    `"sold_to_vendor"`, `"banked_gold"`, `"looted_corpse"` today (see
    `village.py`'s detector functions) — a plain string, not an enum, so a
    future kind never needs a schema migration here.
    """

    ts: str
    tick: int
    from_persona: str
    to_persona: str | None
    kind: str
    amount: float
    detail: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_from_record(record: dict[str, Any]) -> ChronicleEvent | None:
    """`None` for a malformed/partial record — the caller skips it (`_read_
    ledger`'s "degrade, never crash" discipline), never raises."""
    try:
        to_persona = record.get("to_persona")
        return ChronicleEvent(
            ts=str(record["ts"]),
            tick=int(record["tick"]),
            from_persona=str(record["from_persona"]),
            to_persona=None if to_persona is None else str(to_persona),
            kind=str(record["kind"]),
            amount=float(record["amount"]),
            detail=str(record.get("detail", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


class ChronicleLedger:
    """The in-memory queue (`queue_event`) plus the file-backed ledger
    (`flush`, and the read side: `events_for`/`between`/`recent`). See the
    module docstring for why `queue_event`/`flush` are split rather than
    appending immediately the way `skill_library.py::SkillLibrary.
    record_outcome` does.
    """

    def __init__(self, ledger_path: str | Path | None = None) -> None:
        self.ledger_path = Path(ledger_path) if ledger_path is not None else _DEFAULT_CHRONICLE_LEDGER
        self._queue: list[ChronicleEvent] = []
        #: Guards the in-memory queue only — several of `village.py`'s
        #: per-agent worker threads can call `queue_event` concurrently
        #: against this one shared instance. See the module-level
        #: `_chronicle_log_lock` docstring for how this differs from that one.
        self._queue_lock = threading.Lock()

    # -- write side: queue in the tick thread, flush once from the main thread --

    def queue_event(
        self, *, tick: int, from_persona: str, to_persona: str | None, kind: str,
        amount: float, detail: str = "",
    ) -> None:
        """`threading.Lock`-guarded, O(1), in-memory-only — safe to call from
        any worker thread on the fast-loop-adjacent tick-driving path. **No
        file I/O at all** — see the module docstring and this module's own
        offline test proving a `queue_event`-then-no-`flush()` sequence
        writes zero bytes to disk.
        """
        event = ChronicleEvent(
            ts=_now_iso(), tick=tick, from_persona=from_persona, to_persona=to_persona,
            kind=kind, amount=amount, detail=detail,
        )
        with self._queue_lock:
            self._queue.append(event)

    def flush(self, path: str | Path | None = None) -> int:
        """Write every currently-queued event to `path` (default:
        `self.ledger_path`) as one batch of JSON lines, and clear the queue.
        Returns the number of events written (`0` for an empty queue — and,
        deliberately, no file/parent-directory is even touched in that case,
        so a second `flush()` call with nothing newly queued since the first
        writes nothing more, not a duplicate of the first flush's lines).

        A write failure (unwritable ledger path) degrades silently — the
        queue is still cleared (matching every other ledger's "never break
        the caller over a logging failure" discipline in this codebase) —
        rather than raising or leaving the queue in a half-flushed state.
        """
        target = Path(path) if path is not None else self.ledger_path
        with self._queue_lock:
            events, self._queue = self._queue, []
        if not events:
            return 0
        lines = "".join(json.dumps(asdict(e)) + "\n" for e in events)
        try:
            with _chronicle_log_lock:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a") as f:
                    f.write(lines)
        except OSError:
            return 0
        return len(events)

    # -- read side: disk-backed, corrupt-line-tolerant, no caching -------------

    def _read_ledger(self) -> list[ChronicleEvent]:
        try:
            text = self.ledger_path.read_text(encoding="utf-8")
        except OSError:
            return []
        events: list[ChronicleEvent] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # corrupted/partial trailing line — skip, never fatal
            if not isinstance(record, dict):
                continue
            event = _event_from_record(record)
            if event is not None:
                events.append(event)
        return events

    def events_for(self, persona: str, since_tick: int = 0) -> list[ChronicleEvent]:
        """Every event `persona` is a party to (either side), at or after
        `since_tick`, in ledger (file) order."""
        return [
            e for e in self._read_ledger()
            if e.tick >= since_tick and (e.from_persona == persona or e.to_persona == persona)
        ]

    def between(self, persona_a: str, persona_b: str) -> list[ChronicleEvent]:
        """Every strictly agent-to-agent event shared by `persona_a`/
        `persona_b`, in either direction — order-independent
        (`between(a, b)` and `between(b, a)` return the same event set).
        `to_persona=None` (world) events never match here, and a pair with
        no shared events returns `[]`, not an error.
        """
        pair = {persona_a, persona_b}
        return [
            e for e in self._read_ledger()
            if e.to_persona is not None and {e.from_persona, e.to_persona} == pair
        ]

    def recent(self, n: int = 10) -> list[ChronicleEvent]:
        """The `n` most recent events overall, oldest-first within that
        window — for prompt-grounding (item 3). `n <= 0` yields `[]`,
        mirroring `memory.py::EpisodicMemory.recent`/`ReflectionMemory.
        recent`'s own contract."""
        if n <= 0:
            return []
        return self._read_ledger()[-n:]
