"""Static-map reader: tree graphic set + (when data present) a real forest scan."""

import pytest

from anima2 import uomap


def test_tree_graphics_cover_known_ids():
    # Spot-check ids from ServUO's lumberjacking tile list (masked to art ids).
    for g in (0xCCA, 0xCE0, 0xD41, 0x12B5):
        assert g in uomap.TREE_GRAPHICS
    assert len(uomap.TREE_GRAPHICS) > 100


@pytest.mark.skipif(
    not (uomap.UO_DATA / "statics1.mul").exists(), reason="UO static map not present"
)
def test_finds_trees_near_minoc():
    # The Minoc mountains (map 1) are wooded — the scanner should find tree statics.
    trees = uomap.find_trees(1, 2567, 493, radius=60)
    assert len(trees) > 20
    # Results are nearest-first and within the box.
    assert all(abs(t.x - 2567) <= 60 and abs(t.y - 493) <= 60 for t in trees)
    assert all(t.graphic in uomap.TREE_GRAPHICS for t in trees)
