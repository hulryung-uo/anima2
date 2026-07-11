"""Advisory, cross-process file-lock helper for ledger appends (PHASE5.md
item 4's own forced follow-up — Phase 4 item 3's open note: "multi-process
ledger-write safety... since parallel evals write the archive/results
concurrently").

**Same-process appends were already safe, and this module adds nothing for
that case.** `archive.py::Archive._append`/`eval.py::write_eval_result` each
already wrap their write in a `threading.Lock` — combined with CPython's GIL
(no two threads can interleave the bytes of a single `write()` syscall) that
was already enough to keep one process's own appends from tearing each
other's lines. What neither a `threading.Lock` nor the GIL can do is
coordinate across **separate OS processes** — e.g. two independently
launched `python -m anima2...` invocations, or the concrete motivating case
this item names: a future `MAX_CONCURRENT_EVALS > 1` (a second GM account)
running two `evolve.py` loops that both append to the same
`data/archive.jsonl`/`data/eval_results.jsonl`. This item's own live gate is
still SEQUENTIAL (one GM account — see `evolve.py`'s module docstring), so
nothing in this repo actually exercises concurrent-process writes today;
this is a real, cheap guard against a near-future need, proven by its own
offline subprocess test (`tests/test_foundry_filelock.py`) rather than left
unverified.

`fcntl.flock` (POSIX advisory locking, held only for the duration of one
line's write) is the standard, dependency-free way to serialize cross-process
appends: every writer takes an exclusive lock on the file before writing its
line and releases it right after, so two processes' lines can never
interleave into one corrupted line. **POSIX only** — this project's dev and
shard hosts are macOS/Linux (DESIGN.md's own stack; no Windows target), so no
`msvcrt` fallback is implemented; a missing `fcntl` would fail loudly at
import time rather than silently degrading to "no lock."
"""

from __future__ import annotations

import fcntl
from pathlib import Path


def append_line_locked(path: str | Path, line: str) -> None:
    """Append one line to `path` (a trailing newline is added if missing),
    holding an OS-level exclusive advisory lock (`fcntl.flock`) around the
    write+flush. Creates parent directories as needed. Raises `OSError` on
    failure — callers that want the existing "never break the caller over a
    logging failure" ledger discipline (`archive.py`/`eval.py`) catch it
    themselves, same as before this module existed.
    """
    if not line.endswith("\n"):
        line += "\n"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
