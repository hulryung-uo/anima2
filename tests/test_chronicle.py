"""`chronicle.py` offline tests (PHASE6.md item 2).

The load-bearing proof across this file: `queue_event()` never touches disk
(only `flush()` does), and the two, once split, behave exactly as a
fast-loop-adjacent queue + a single end-of-session batch write should —
mirrors `test_skill_library.py`'s own "two instances, same ledger" and
`test_memory.py`'s own persist-path proofs, applied to the read side here
too (`events_for`/`between`/`recent`).
"""

from __future__ import annotations

import json
import threading

from anima2.chronicle import ChronicleEvent, ChronicleLedger


def _queue_sample(ledger: ChronicleLedger, n: int = 2) -> None:
    for i in range(n):
        ledger.queue_event(tick=i, from_persona="Grimm0", to_persona="Tormund0",
                           kind="delivered_ingots", amount=float(i + 1))


# --- the queue/flush split is real, not cosmetic ----------------------------


def test_queue_event_alone_writes_zero_bytes(tmp_path):
    path = tmp_path / "chronicle.jsonl"
    ledger = ChronicleLedger(ledger_path=path)
    _queue_sample(ledger, 3)
    assert not path.exists()


def test_flush_writes_queued_events_in_order_and_clears_queue(tmp_path):
    path = tmp_path / "chronicle.jsonl"
    ledger = ChronicleLedger(ledger_path=path)
    ledger.queue_event(tick=1, from_persona="Grimm0", to_persona="Tormund0",
                       kind="delivered_ingots", amount=5.0)
    ledger.queue_event(tick=2, from_persona="Tormund0", to_persona="Grimm0",
                       kind="picked_up_ingots", amount=5.0)

    n = ledger.flush()
    assert n == 2
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    first, second = json.loads(lines[0]), json.loads(lines[1])
    assert first["kind"] == "delivered_ingots"
    assert first["tick"] == 1
    assert second["kind"] == "picked_up_ingots"
    assert second["tick"] == 2


def test_second_flush_with_nothing_newly_queued_writes_nothing_more(tmp_path):
    path = tmp_path / "chronicle.jsonl"
    ledger = ChronicleLedger(ledger_path=path)
    _queue_sample(ledger, 2)
    assert ledger.flush() == 2
    before = path.read_text()

    assert ledger.flush() == 0  # nothing queued since — a true no-op
    assert path.read_text() == before  # not a duplicate of the first flush's lines


def test_flush_with_empty_queue_never_touches_disk_at_all(tmp_path):
    path = tmp_path / "nested" / "chronicle.jsonl"
    ledger = ChronicleLedger(ledger_path=path)
    assert ledger.flush() == 0
    assert not path.parent.exists()  # not even the parent dir was created


def test_flush_can_be_pointed_at_an_explicit_path_override(tmp_path):
    default_path = tmp_path / "default.jsonl"
    override_path = tmp_path / "override.jsonl"
    ledger = ChronicleLedger(ledger_path=default_path)
    _queue_sample(ledger, 1)
    ledger.flush(path=override_path)
    assert override_path.exists()
    assert not default_path.exists()


# --- read side: round-trips across flushes and fresh instances -------------


def test_events_for_between_recent_round_trip_across_flushes_and_fresh_instance(tmp_path):
    path = tmp_path / "chronicle.jsonl"
    ledger_a = ChronicleLedger(ledger_path=path)
    ledger_a.queue_event(tick=1, from_persona="Grimm0", to_persona="Tormund0",
                         kind="delivered_ingots", amount=8.0)
    ledger_a.flush()
    ledger_a.queue_event(tick=5, from_persona="Tormund0", to_persona="Grimm0",
                         kind="picked_up_ingots", amount=8.0)
    ledger_a.queue_event(tick=9, from_persona="Tormund0", to_persona=None,
                         kind="sold_to_vendor", amount=42.0)
    ledger_a.flush()

    # A fresh instance pointed at the same file — the "two instances, same
    # ledger" persistence proof, mirroring `test_skill_library.py`'s own.
    ledger_b = ChronicleLedger(ledger_path=path)

    for ledger in (ledger_a, ledger_b):
        grimm_events = ledger.events_for("Grimm0")
        assert {e.kind for e in grimm_events} == {"delivered_ingots", "picked_up_ingots"}

        between = ledger.between("Grimm0", "Tormund0")
        assert {e.kind for e in between} == {"delivered_ingots", "picked_up_ingots"}  # world event excluded
        assert ledger.between("Tormund0", "Grimm0") == between  # order-independent

        assert [e.kind for e in ledger.recent(2)] == ["picked_up_ingots", "sold_to_vendor"]


def test_events_for_since_tick_filters_out_earlier_events(tmp_path):
    path = tmp_path / "chronicle.jsonl"
    ledger = ChronicleLedger(ledger_path=path)
    ledger.queue_event(tick=1, from_persona="Grimm0", to_persona="Tormund0",
                       kind="delivered_ingots", amount=1.0)
    ledger.queue_event(tick=10, from_persona="Grimm0", to_persona="Tormund0",
                       kind="delivered_ingots", amount=2.0)
    ledger.flush()

    assert [e.tick for e in ledger.events_for("Grimm0", since_tick=5)] == [10]
    assert [e.tick for e in ledger.events_for("Grimm0", since_tick=0)] == [1, 10]


def test_between_returns_empty_for_a_pair_with_no_shared_events(tmp_path):
    path = tmp_path / "chronicle.jsonl"
    ledger = ChronicleLedger(ledger_path=path)
    ledger.queue_event(tick=1, from_persona="Grimm0", to_persona="Tormund0",
                       kind="delivered_ingots", amount=1.0)
    ledger.flush()
    assert ledger.between("Grimm0", "Marina0") == []


def test_between_never_returns_a_to_persona_none_world_event(tmp_path):
    path = tmp_path / "chronicle.jsonl"
    ledger = ChronicleLedger(ledger_path=path)
    ledger.queue_event(tick=1, from_persona="Tormund0", to_persona=None,
                       kind="banked_gold", amount=100.0)
    ledger.flush()
    assert ledger.events_for("Tormund0") == [
        ChronicleEvent(ts=ledger.events_for("Tormund0")[0].ts, tick=1, from_persona="Tormund0",
                       to_persona=None, kind="banked_gold", amount=100.0, detail=""),
    ]
    assert ledger.between("Tormund0", "Grimm0") == []


def test_missing_ledger_file_degrades_to_empty_reads(tmp_path):
    path = tmp_path / "does_not_exist.jsonl"
    ledger = ChronicleLedger(ledger_path=path)
    assert ledger.events_for("Grimm0") == []
    assert ledger.between("Grimm0", "Tormund0") == []
    assert ledger.recent(5) == []


def test_hand_corrupted_trailing_line_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "chronicle.jsonl"
    good = json.dumps({"ts": "x", "tick": 1, "from_persona": "Grimm0", "to_persona": "Tormund0",
                       "kind": "delivered_ingots", "amount": 5.0, "detail": ""})
    path.write_text(good + "\n" + '{"tick": 2, "from_persona": "Grimm0", "trunc')

    ledger = ChronicleLedger(ledger_path=path)
    events = ledger.events_for("Grimm0")
    assert len(events) == 1
    assert events[0].tick == 1


def test_recent_n_le_zero_yields_empty(tmp_path):
    path = tmp_path / "chronicle.jsonl"
    ledger = ChronicleLedger(ledger_path=path)
    _queue_sample(ledger, 2)
    ledger.flush()
    assert ledger.recent(0) == []
    assert ledger.recent(-1) == []


# --- concurrency: the load-bearing thread-safety proof ----------------------


def test_many_threads_queue_event_concurrently_then_one_flush_loses_nothing(tmp_path):
    path = tmp_path / "chronicle.jsonl"
    ledger = ChronicleLedger(ledger_path=path)
    n_threads = 12
    n_per_thread = 25

    def worker(idx: int) -> None:
        for i in range(n_per_thread):
            ledger.queue_event(tick=i, from_persona=f"Agent{idx}", to_persona=None,
                               kind="looted_corpse", amount=float(i))

    threads = [threading.Thread(target=worker, args=(idx,)) for idx in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    n = ledger.flush()
    assert n == n_threads * n_per_thread
    lines = path.read_text().splitlines()
    assert len(lines) == n_threads * n_per_thread  # no lost or torn events
    for line in lines:
        json.loads(line)  # raises if any line is torn/malformed
