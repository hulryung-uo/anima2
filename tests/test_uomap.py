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


# --- mining: land reader + spot finder (forge11's terrain-blind lesson) ---------------

def test_mine_land_tiles_mirror_the_shard_source_exactly():
    # Same discipline as tests/test_price_tripwire.py: the constant is a MIRROR of
    # ServUO's Mining.cs m_MountainAndCaveTiles (land subset — static-encoded
    # 0x4000|artId cave-floor entries are out of the land scanner's scope), and
    # this test re-parses the shard source so a shard-side edit fires here.
    import re
    from pathlib import Path

    from anima2.uomap import MINE_LAND_TILES

    src = Path.home() / "dev" / "uo" / "servuo" / "Scripts" / "Services" / "Harvest" / "Mining.cs"
    if not src.exists():
        import pytest
        pytest.skip("shard source not checked out")
    body = re.search(
        r"m_MountainAndCaveTiles = new int\[\]\s*\{(.*?)\};", src.read_text(), re.S
    ).group(1)
    parsed = {int(t, 0) for t in re.findall(r"0x[0-9A-Fa-f]+|\d+", body)}
    land = {t for t in parsed if t < 0x4000}
    assert land == MINE_LAND_TILES


def test_find_mine_spots_rediscovers_the_live_calibrated_trade_stand():
    # The trade corridor's mine stand (2611,474) was hand-calibrated live in
    # Phase 3. The surveyor, working from map data alone, must rank exactly that
    # tile as the best stand in range — and space the rest one 8x8 HarvestBank
    # cell apart (spots closer than 8 share the same 10-34-ore pool, making a
    # relocation between them a walk to the same empty bank).
    import pytest

    from anima2.uomap import UO_DATA, find_mine_spots

    if not (UO_DATA / "map1LegacyMUL.uop").exists():
        pytest.skip("client map data not present")
    spots = find_mine_spots(1, 2611, 474, radius=40)
    assert spots and spots[0][0] == (2611, 474)
    assert len(spots[0][1]) >= 4  # a real face in mining reach
    coords = [s for s, _ in spots]
    for i, a in enumerate(coords):
        for b in coords[i + 1:]:
            assert max(abs(a[0] - b[0]), abs(a[1] - b[1])) >= 8
