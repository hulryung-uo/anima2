"""`live_evolve_gate.py` offline tests (PHASE6.md item 6, extended by
PHASE7.md item 1).

The gate's live legs need a shard; these pin the pure helpers — item 6's
`--suffix`-to-path plumbing, and item 1's `_fish_window`/
`_prove_fish_spot_fairness` fishing-pool rotation arithmetic (the fishing
counterpart to `_spot_window`/`_prove_spot_fairness`). Importing the module is
side-effect-free (network only happens inside `main()`, guarded by `__main__`).
"""

from __future__ import annotations

from pathlib import Path

from anima2.live_evolve_gate import (
    FISH_POOL,
    _fish_window,
    _gate_paths,
    _prove_fish_spot_fairness,
    _prove_spot_fairness,
    _spot_window,
)


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


# =============================================================================
# PHASE7.md item 1: _fish_window / _prove_fish_spot_fairness (the fishing
# counterpart to _spot_window / _prove_spot_fairness).
# =============================================================================


def test_fish_window_returns_matched_index_aligned_stand_and_node_pairs():
    """`_fish_window` returns `(stand_window, nodes_window)` of equal length,
    each `stand_window[i]` the shore stand of a `FISH_POOL` entry and each
    `nodes_window[i]` that SAME entry's water as a full `EvalConfig.nodes`
    value — a tuple containing one `(water_x, water_y, water_z, 0)` land-target
    node (graphic 0). The stand and its node are the SAME pool entry (matched),
    the invariant the whole fix rests on."""
    stands, nodes = _fish_window(0, 2)
    assert len(stands) == len(nodes) == 2
    for i in range(2):
        exp_stand, exp_water = FISH_POOL[i]
        assert stands[i] == exp_stand
        # nodes[i] is a tuple-of-one-node; the node is (wx, wy, wz, 0).
        assert nodes[i] == (exp_water + (0,),)
        assert nodes[i][0][3] == 0  # land-target graphic


def test_fish_window_wraps_around_the_pool():
    """Starting near the end of the 4-spot pool wraps back to the front — the
    same non-degenerate wraparound as `_spot_window`."""
    stands, nodes = _fish_window(3, 2)
    assert stands[0] == FISH_POOL[3][0]
    assert stands[1] == FISH_POOL[0][0]  # wrapped
    assert nodes[0] == (FISH_POOL[3][1] + (0,),)
    assert nodes[1] == (FISH_POOL[0][1] + (0,),)


def test_fish_window_advancing_by_one_is_non_degenerate_like_spot_window():
    """Advancing the cursor by ONE (not by `width`) makes consecutive fisher
    windows overlap-and-slide across the pool rather than partitioning it —
    the identical property `_spot_window`'s own docstring proves for mining
    (coprime stride vs. pool size for width==2)."""
    w0, _ = _fish_window(0, 2)
    w1, _ = _fish_window(1, 2)
    # window 0 = {stand0, stand1}, window 1 = {stand1, stand2} — they SHARE
    # stand1 and DIFFER (a degenerate by-`width` stride would give disjoint
    # {0,1},{2,3}). The stand tuples mirror `_spot_window`'s `(x, y)` shape.
    assert set(w0) & set(w1)        # overlap exists
    assert set(w0) != set(w1)       # but they are not identical
    assert all(isinstance(s, tuple) and len(s) == 2 for s in _spot_window(0, 2) + w0)


def test_prove_fish_spot_fairness_touches_every_stand_evenly_both_searches():
    """Mirrors `_prove_spot_fairness`'s own assertion shape: over
    `args.genomes`-many rounds, both the evo and rand fisher subsequences visit
    every `FISH_POOL` stand a roughly-even number of times (no stand starved,
    at most two distinct counts) — the fairness the independent `fish_cursor`
    must preserve for the fishing pool on its own terms."""
    fair = _prove_fish_spot_fairness(8, 2)
    evo = fair["evo_fish_spot_counts"]
    rand = fair["rand_fish_spot_counts"]
    assert set(evo) == {stand for stand, _w in FISH_POOL}  # keyed by stand
    for counts in (evo, rand):
        assert all(c > 0 for c in counts.values())         # no stand starved
        assert len(set(counts.values())) <= 2              # roughly even


def test_prove_fish_spot_fairness_mirrors_mining_proof_arithmetic():
    """The fishing fairness proof is the SAME arithmetic as the mining one:
    with identical `(n_rounds, width)` and equal 4-spot pools, the per-position
    visit COUNTS match exactly (the fishing keys are stands, the mining keys are
    mining coords, but the multiset of counts is identical) — proving
    `_fish_window`/`_prove_fish_spot_fairness` didn't drift from the mining
    template."""
    fish = _prove_fish_spot_fairness(6, 2)
    mine = _prove_spot_fairness(6, 2)
    assert sorted(fish["evo_fish_spot_counts"].values()) == sorted(mine["evo_spot_counts"].values())
    assert sorted(fish["rand_fish_spot_counts"].values()) == sorted(mine["rand_spot_counts"].values())
