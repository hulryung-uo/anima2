"""`live_evolve_gate.py` offline tests (PHASE6.md item 6).

The gate's live legs need a shard; these pin the one pure helper item 6
added — the `--suffix`-to-path plumbing (the housekeeping nit Phase 5 item 4
recorded). Importing the module is side-effect-free (network only happens
inside `main()`, guarded by `__main__`).
"""

from __future__ import annotations

from pathlib import Path

from anima2.live_evolve_gate import _gate_paths


def test_gate_paths_omitted_suffix_reproduces_the_original_fixed_names():
    """Regression pin against Phase 5 item 4's own gate having used the fixed
    names: an omitted (`None`) or empty suffix yields exactly the original
    `archive_evolve_gate.jsonl` / `archive_random_gate.jsonl` /
    `eval_results.jsonl`, and the canonical `archive.jsonl` is never suffixed."""
    for empty in (None, ""):
        p = _gate_paths(empty, Path("data"))
        assert p["evo"] == Path("data/archive_evolve_gate.jsonl")
        assert p["rand"] == Path("data/archive_random_gate.jsonl")
        assert p["results"] == Path("data/eval_results.jsonl")
        assert p["canon"] == Path("data/archive.jsonl")


def test_gate_paths_distinct_suffixes_give_distinct_files_but_share_the_canonical():
    """Two different `--suffix` values produce distinct evolve/random/results
    files (so a reader inspecting them cold can tell the runs apart) while both
    still mirror into the one shared, deliberately-unsuffixed canonical
    `archive.jsonl`."""
    a = _gate_paths("run1", Path("data"))
    b = _gate_paths("run2", Path("data"))
    assert a["evo"] == Path("data/archive_evolve_gaterun1.jsonl")
    assert b["evo"] == Path("data/archive_evolve_gaterun2.jsonl")
    assert a["results"] == Path("data/eval_resultsrun1.jsonl")
    assert a["evo"] != b["evo"] and a["rand"] != b["rand"] and a["results"] != b["results"]
    assert a["canon"] == b["canon"] == Path("data/archive.jsonl")
