"""`foundry/_filelock.py` offline tests (PHASE5.md item 4's multi-process
ledger-write safety follow-up). The decisive proof is `test_...` below:
several SEPARATE OS PROCESSES (real `subprocess.Popen`, not threads — a
threading test would only prove the GIL, which was never in question, see
`_filelock.py`'s own module docstring) appending to the SAME file
concurrently through `append_line_locked`, with no interleaved/torn lines
in the result.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

from anima2.foundry._filelock import append_line_locked


def test_append_line_locked_writes_one_line(tmp_path):
    path = tmp_path / "ledger.jsonl"
    append_line_locked(path, json.dumps({"a": 1}))
    append_line_locked(path, json.dumps({"a": 2}))

    lines = path.read_text().splitlines()
    assert lines == ['{"a": 1}', '{"a": 2}']


def test_append_line_locked_adds_missing_trailing_newline(tmp_path):
    path = tmp_path / "ledger.jsonl"
    append_line_locked(path, "no newline here")
    assert path.read_text() == "no newline here\n"


def test_append_line_locked_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "dir" / "ledger.jsonl"
    append_line_locked(path, "x")
    assert path.exists()


# --- THE decisive proof: concurrent OS PROCESSES never tear a line ----------

_WORKER_SCRIPT = textwrap.dedent("""
    import json
    import sys
    sys.path.insert(0, {repo_root!r})
    from anima2.foundry._filelock import append_line_locked

    path, worker_id, n_lines = sys.argv[1], sys.argv[2], int(sys.argv[3])
    # A deliberately large payload (padding) makes an unlocked, interleaved
    # write far more likely to tear mid-line if the lock were a no-op —
    # a single small line might "accidentally" survive even with no locking
    # at all (most OSes buffer/flush short writes atomically in practice),
    # which would make this test a false negative for a broken lock.
    padding = "x" * 500
    for i in range(n_lines):
        line = json.dumps({{"worker": worker_id, "i": i, "padding": padding}})
        append_line_locked(path, line)
""")


def test_concurrent_processes_never_interleave_or_tear_a_line(tmp_path):
    path = tmp_path / "concurrent.jsonl"
    script = tmp_path / "worker.py"
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script.write_text(_WORKER_SCRIPT.format(repo_root=repo_root))

    n_workers = 6
    n_lines = 40
    procs = []
    for w in range(n_workers):
        procs.append(subprocess.Popen(
            [sys.executable, str(script), str(path), f"w{w}", str(n_lines)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ))

    outs_errs = [p.communicate(timeout=60) for p in procs]
    for p, (out, err) in zip(procs, outs_errs):
        assert p.returncode == 0, f"worker failed: stdout={out!r} stderr={err!r}"

    raw_lines = path.read_text().splitlines()
    assert len(raw_lines) == n_workers * n_lines  # no line lost, none merged into another

    parsed = []
    per_worker_counts: dict[str, int] = {}
    for line in raw_lines:
        d = json.loads(line)  # raises if any line is torn/interleaved JSON
        parsed.append(d)
        per_worker_counts[d["worker"]] = per_worker_counts.get(d["worker"], 0) + 1

    assert len(parsed) == n_workers * n_lines
    assert per_worker_counts == {f"w{w}": n_lines for w in range(n_workers)}
    # Every worker's own (worker, i) pairs are exactly 0..n_lines-1, no dupes —
    # confirms no line was double-written or a torn fragment silently merged
    # two records into one that happened to still parse as valid JSON.
    for w in range(n_workers):
        seen_i = sorted(d["i"] for d in parsed if d["worker"] == f"w{w}")
        assert seen_i == list(range(n_lines))
