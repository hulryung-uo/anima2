"""`live_eval_gate.py` offline tests (PHASE6.md item 4).

The gate's live legs need a shard; these pin the one thing that DOESN'T — the
`FISH_STANDS`/`FISH_NODES` rotation constants the fishing bank-drain fix
introduced. They regression-pin the exact shape bug the first live gate run
hit: `FISH_NODES[i]` must be a FULL `EvalConfig.nodes` value (a tuple
*containing* one `(x, y, z, graphic)` water node), NOT a bare 4-tuple — a bare
4-tuple flattens under `run_eval`'s `list(nodes)` to `[x, y, z, graphic]` and
makes `skills/harvest.py` try to unpack an int. Importing the module is
side-effect-free (network only happens inside `main()`, guarded by `__main__`).
"""

from __future__ import annotations

from anima2.foundry.eval import SCENARIOS
from anima2.live_eval_gate import FISH_NODES, FISH_STANDS
from anima2.profession import FISHING_SPOTS


def test_fish_rotation_pairs_are_matched_and_skip_the_drained_index_0():
    """`FISH_STANDS[i]`/`FISH_NODES[i]` are index-aligned pairs drawn from
    `FISHING_SPOTS[1..3]` — three DISTINCT spots (so no two with-pole seeds
    share one bank), and index 0 (the one the first gate run drained) is
    deliberately skipped."""
    assert len(FISH_STANDS) == len(FISH_NODES) == 3
    assert len(set(FISH_STANDS)) == 3  # genuinely distinct, no accidental repeat
    for i in range(3):
        stand, (wx, wy, wz) = FISHING_SPOTS[1 + i]  # index 0 skipped -> 1..3
        assert FISH_STANDS[i] == stand
        assert FISH_NODES[i] == ((wx, wy, wz, 0),)


def test_fish_nodes_have_scenario_node_shape_not_a_bare_4_tuple():
    """The exact regression: each `FISH_NODES[i]` iterates to 4-TUPLES (water
    nodes), never to bare ints — same shape as `SCENARIOS["fishing"].nodes`,
    so `run_eval`'s `list(nodes)` yields `[(x, y, z, g)]` (what `harvest.py`
    can unpack), not `[x, y, z, g]` (four ints it can't)."""
    default_shape = SCENARIOS["fishing"].nodes  # ((wx, wy, wz, 0),)
    for nodes in FISH_NODES:
        assert type(nodes) is type(default_shape)
        assert len(nodes) == 1
        for node in nodes:
            assert isinstance(node, tuple) and len(node) == 4
            assert all(isinstance(coord, int) for coord in node)
        # The thing that actually broke live: list(nodes) must be a list of
        # 4-tuples, not a flattened list of ints.
        assert list(nodes) == [nodes[0]]
        assert isinstance(list(nodes)[0], tuple)
