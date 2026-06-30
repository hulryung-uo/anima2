"""Static-map reader — find resource tiles (trees, …) from the UO client data.

Parses `staidx{N}.mul` + `statics{N}.mul` (the per-8×8-block static-item index and
data) to locate resource statics by graphic, so the Control plane can stage a
gatherer **adjacent to a real node** (lumberjacking targets specific tree statics,
not open ground — blind probing never finds them).

Map N is 7168×4096 tiles → 896×512 blocks. staidx entry (12 bytes): lookup:i32,
length:i32, extra:i32. statics record (7 bytes): graphic:u16, dx:u8, dy:u8, z:i8,
hue:u16. Both little-endian. World (x,y) = (blockX*8+dx, blockY*8+dy).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

UO_DATA = Path.home() / "dev" / "uo" / "uo-resource"
MAP_HEIGHT_BLOCKS = 512  # map0 & map1 are 4096 tiles tall → 512 blocks

# Lumberjacking tree statics (ServUO Lumberjacking.cs m_TreeTiles, masked to the
# art id with & 0x3FFF — the harvest list stores them as 0x4000 | artId).
TREE_GRAPHICS: frozenset[int] = frozenset(
    {
        *range(0xCCA, 0xCE9),  # 0xCCA..0xCE8 contiguous trunk/foliage block
        *range(0xCF8, 0xD04),  # 0xCF8..0xD03
        *range(0xD41, 0xD54),  # 0xD41..0xD53
        *range(0xD57, 0xD6A),  # 0xD57..0xD69
        *range(0xD6E, 0xD80),  # 0xD6E..0xD7F
        *range(0xD84, 0xD91),  # 0xD84..0xD90
        *range(0xD95, 0xDAC),  # 0xD95..0xDAB (sparse, but harmless extras)
        *range(0x12B5, 0x12C8),  # 0x12B5..0x12C7
    }
)


@dataclass(frozen=True)
class Static:
    x: int
    y: int
    z: int
    graphic: int


def scan_statics(map_index: int, x0: int, y0: int, x1: int, y1: int,
                 graphics: frozenset[int]) -> list[Static]:
    """Return all statics whose graphic is in `graphics` within the tile box."""
    staidx = (UO_DATA / f"staidx{map_index}.mul").read_bytes()
    out: list[Static] = []
    with open(UO_DATA / f"statics{map_index}.mul", "rb") as sf:
        for bx in range(x0 // 8, x1 // 8 + 1):
            for by in range(y0 // 8, y1 // 8 + 1):
                block = bx * MAP_HEIGHT_BLOCKS + by
                off = block * 12
                if off + 12 > len(staidx):
                    continue
                lookup, length, _ = struct.unpack_from("<iii", staidx, off)
                if lookup < 0 or length <= 0:
                    continue
                sf.seek(lookup)
                data = sf.read(length)
                for i in range(0, length - 6, 7):
                    g, dx, dy, z, _hue = struct.unpack_from("<HBBbH", data, i)
                    if g in graphics:
                        x, y = bx * 8 + dx, by * 8 + dy
                        if x0 <= x <= x1 and y0 <= y <= y1:
                            out.append(Static(x, y, z, g))
    return out


def find_trees(map_index: int, cx: int, cy: int, radius: int = 40) -> list[Static]:
    """Tree statics within `radius` tiles of (cx, cy), nearest first."""
    trees = scan_statics(map_index, cx - radius, cy - radius, cx + radius, cy + radius,
                         TREE_GRAPHICS)
    trees.sort(key=lambda s: max(abs(s.x - cx), abs(s.y - cy)))
    return trees


def distinct_trees(map_index: int, cx: int, cy: int, radius: int = 60) -> list[Static]:
    """One tree per tile (a tree stacks several graphics at the same (x, y))."""
    seen: set[tuple[int, int]] = set()
    out: list[Static] = []
    for t in find_trees(map_index, cx, cy, radius):
        if (t.x, t.y) not in seen:
            seen.add((t.x, t.y))
            out.append(t)
    return out


def find_tree_clusters(map_index: int, cx: int, cy: int, radius: int = 60,
                       reach: int = 2) -> list[tuple[tuple[int, int], list[Static]]]:
    """Standing spots with several trees in harvest reach, richest first.

    Returns [((stand_x, stand_y), [trees within `reach`]), …]. A lumberjack stands
    on the spot and chops every tree in its list without moving — and as one
    depletes it moves to the next (trees regrow), giving sustained work. Stand
    spots are a tile *south* of a tree (likely open ground); the spots returned
    don't overlap, so multiple lumberjacks get separate groves.
    """
    trees = distinct_trees(map_index, cx, cy, radius)
    scored: list[tuple[tuple[int, int], list[Static]]] = []
    for t in trees:
        sx, sy = t.x, t.y + 1  # stand just south of a tree
        grove = [u for u in trees if max(abs(u.x - sx), abs(u.y - sy)) <= reach]
        if len(grove) >= 2:
            scored.append(((sx, sy), grove))
    scored.sort(key=lambda s: len(s[1]), reverse=True)

    # Keep spatially-separate spots so different workers don't share a grove.
    chosen: list[tuple[tuple[int, int], list[Static]]] = []
    for spot, grove in scored:
        if all(max(abs(spot[0] - s[0]), abs(spot[1] - s[1])) > 2 * reach + 1 for s, _ in chosen):
            chosen.append((spot, grove))
    return chosen


if __name__ == "__main__":
    import sys

    mi = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    cx = int(sys.argv[2]) if len(sys.argv) > 2 else 2567
    cy = int(sys.argv[3]) if len(sys.argv) > 3 else 493
    trees = find_trees(mi, cx, cy, radius=60)
    print(f"map{mi}: {len(trees)} tree statics within 60 of ({cx},{cy})")
    for t in trees[:12]:
        print(f"  ({t.x},{t.y},{t.z}) graphic={hex(t.graphic)}")
