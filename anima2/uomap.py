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


# --- land map (UOP) + mining spots ---------------------------------------------------
#
# The land terrain lives in `map{N}LegacyMUL.uop` (a MYP container wrapping the
# classic 196-byte map blocks: 4-byte header + 64 cells of {tileid:u16, z:i8}).
# Reader ported from v1 (`anima/anima/uop.py` + `anima/anima/map.py`), trimmed to
# the one consumer here: finding MINEABLE mountain faces so a miner can be staged
# — and can RELOCATE — onto known rock instead of compass-walking blind (forge11,
# 2026-07-30: chained blind relocations marched 60+ tiles off the mountain face,
# every ring all-invalid, and the economy starved while the machinery "worked").

_MAP_BLOCK_BYTES = 196
_BLOCKS_PER_UOP_CHUNK = 4096


def _uop_hash(s: str) -> int:
    """UOP entry-name hash (Jenkins HashLittle2) — byte-for-byte from v1."""
    length = len(s)
    ebx = edi = esi = (length + 0xDEADBEEF) & 0xFFFFFFFF
    i = 0
    while i + 12 < length:
        edi = (edi + (ord(s[i + 7]) << 24 | ord(s[i + 6]) << 16 | ord(s[i + 5]) << 8 | ord(s[i + 4]))) & 0xFFFFFFFF
        esi = (esi + (ord(s[i + 11]) << 24 | ord(s[i + 10]) << 16 | ord(s[i + 9]) << 8 | ord(s[i + 8]))) & 0xFFFFFFFF
        edx = ((ord(s[i + 3]) << 24 | ord(s[i + 2]) << 16 | ord(s[i + 1]) << 8 | ord(s[i])) - esi) & 0xFFFFFFFF
        edx = (edx + ebx) & 0xFFFFFFFF
        edx ^= ((esi >> 28) | (esi << 4)) & 0xFFFFFFFF
        esi = (esi + edi) & 0xFFFFFFFF
        edi = (edi - edx) & 0xFFFFFFFF
        edi ^= ((edx >> 26) | (edx << 6)) & 0xFFFFFFFF
        edx = (edx + esi) & 0xFFFFFFFF
        esi = (esi - edi) & 0xFFFFFFFF
        esi ^= ((edi >> 24) | (edi << 8)) & 0xFFFFFFFF
        edi = (edi + edx) & 0xFFFFFFFF
        ebx = (edx - esi) & 0xFFFFFFFF
        ebx ^= ((esi >> 16) | (esi << 16)) & 0xFFFFFFFF
        esi = (esi + edi) & 0xFFFFFFFF
        edi = (edi - ebx) & 0xFFFFFFFF
        edi ^= ((ebx >> 13) | (ebx << 19)) & 0xFFFFFFFF
        ebx = (ebx + esi) & 0xFFFFFFFF
        esi = (esi - edi) & 0xFFFFFFFF
        esi ^= ((edi >> 28) | (edi << 4)) & 0xFFFFFFFF
        edi = (edi + ebx) & 0xFFFFFFFF
        i += 12
    remaining = length - i
    if remaining > 0:
        if remaining >= 12: esi = (esi + (ord(s[i + 11]) << 24)) & 0xFFFFFFFF  # noqa: E701
        if remaining >= 11: esi = (esi + (ord(s[i + 10]) << 16)) & 0xFFFFFFFF  # noqa: E701
        if remaining >= 10: esi = (esi + (ord(s[i + 9]) << 8)) & 0xFFFFFFFF  # noqa: E701
        if remaining >= 9: esi = (esi + ord(s[i + 8])) & 0xFFFFFFFF  # noqa: E701
        if remaining >= 8: edi = (edi + (ord(s[i + 7]) << 24)) & 0xFFFFFFFF  # noqa: E701
        if remaining >= 7: edi = (edi + (ord(s[i + 6]) << 16)) & 0xFFFFFFFF  # noqa: E701
        if remaining >= 6: edi = (edi + (ord(s[i + 5]) << 8)) & 0xFFFFFFFF  # noqa: E701
        if remaining >= 5: edi = (edi + ord(s[i + 4])) & 0xFFFFFFFF  # noqa: E701
        if remaining >= 4: ebx = (ebx + (ord(s[i + 3]) << 24)) & 0xFFFFFFFF  # noqa: E701
        if remaining >= 3: ebx = (ebx + (ord(s[i + 2]) << 16)) & 0xFFFFFFFF  # noqa: E701
        if remaining >= 2: ebx = (ebx + (ord(s[i + 1]) << 8)) & 0xFFFFFFFF  # noqa: E701
        if remaining >= 1: ebx = (ebx + ord(s[i])) & 0xFFFFFFFF  # noqa: E701
        esi = ((esi ^ edi) - ((edi >> 18) | (edi << 14))) & 0xFFFFFFFF
        ecx = ((esi ^ ebx) - ((esi >> 21) | (esi << 11))) & 0xFFFFFFFF
        edi = ((edi ^ ecx) - ((ecx >> 7) | (ecx << 25))) & 0xFFFFFFFF
        esi = ((esi ^ edi) - ((edi >> 16) | (edi << 16))) & 0xFFFFFFFF
        edx = ((esi ^ ecx) - ((esi >> 28) | (esi << 4))) & 0xFFFFFFFF
        edi = ((edi ^ edx) - ((edx >> 18) | (edx << 14))) & 0xFFFFFFFF
        eax = ((esi ^ edi) - ((edi >> 8) | (edi << 24))) & 0xFFFFFFFF
        return (edi << 32) | eax
    return (esi << 32) | 0


_uop_cache: dict[int, dict[int, tuple[int, int, int]]] = {}
_uop_bytes: dict[int, bytes] = {}


def _map_chunk(map_index: int, chunk_idx: int) -> bytes | None:
    """Raw bytes of UOP chunk `build/map{N}legacymul/{i:08d}.dat` (uncompressed
    for LegacyMUL maps; zlib handled anyway). Entry table cached per map."""
    import zlib

    if map_index not in _uop_cache:
        data = (UO_DATA / f"map{map_index}LegacyMUL.uop").read_bytes()
        if struct.unpack_from("<I", data, 0)[0] != 0x50594D:
            raise ValueError("not a UOP file")
        entries: dict[int, tuple[int, int, int]] = {}
        next_block = struct.unpack_from("<q", data, 12)[0]
        while next_block != 0 and next_block < len(data):
            pos = next_block
            count = struct.unpack_from("<i", data, pos)[0]
            next_block = struct.unpack_from("<q", data, pos + 4)[0]
            pos += 12
            for _ in range(count):
                if pos + 34 > len(data):
                    break
                offset = struct.unpack_from("<q", data, pos)[0]
                header_len = struct.unpack_from("<i", data, pos + 8)[0]
                compressed = struct.unpack_from("<i", data, pos + 12)[0]
                file_hash = struct.unpack_from("<Q", data, pos + 20)[0]
                compression = struct.unpack_from("<h", data, pos + 32)[0]
                pos += 34
                if offset and compressed:
                    entries[file_hash] = (offset + header_len, compressed, compression)
        _uop_cache[map_index] = entries
        _uop_bytes[map_index] = data
    entry = _uop_cache[map_index].get(
        _uop_hash(f"build/map{map_index}legacymul/{chunk_idx:08d}.dat"))
    if entry is None:
        return None
    off, size, compression = entry
    raw = _uop_bytes[map_index][off:off + size]
    return zlib.decompress(raw) if compression == 1 else raw


def land_cells(map_index: int, x0: int, y0: int, x1: int, y1: int):
    """Yield (x, y, tile_id, z) for every land cell in the box (inclusive).
    Land tile ids are 14-bit — masked with 0x3FFF like ClassicUO's Chunk.cs."""
    for bx in range(x0 // 8, x1 // 8 + 1):
        for by in range(y0 // 8, y1 // 8 + 1):
            block_num = bx * MAP_HEIGHT_BLOCKS + by
            chunk = _map_chunk(map_index, block_num // _BLOCKS_PER_UOP_CHUNK)
            if chunk is None:
                continue
            base = (block_num % _BLOCKS_PER_UOP_CHUNK) * _MAP_BLOCK_BYTES + 4
            for cy in range(8):
                wy = by * 8 + cy
                if not y0 <= wy <= y1:
                    continue
                for cx in range(8):
                    wx = bx * 8 + cx
                    if not x0 <= wx <= x1:
                        continue
                    pos = base + (cy * 8 + cx) * 3
                    tile_id = struct.unpack_from("<H", chunk, pos)[0] & 0x3FFF
                    z = struct.unpack_from("<b", chunk, pos + 2)[0]
                    yield wx, wy, tile_id, z


# Mineable LAND tiles — ServUO `Mining.cs` `m_MountainAndCaveTiles`, land subset
# (entries < 0x4000; the 0x453B..0x454F tail is 0x4000|artId STATIC cave floors,
# out of scope for the land scanner). EXACT membership, not ranges — the source
# array skips ids inside its own runs (e.g. 232-235, 248-251) and `Validate()`
# does exact matching (`RangedTiles` is never set for ore). Pinned against the
# shard source by tests/test_price_tripwire.py-style parsing in test_uomap.py.
MINE_LAND_TILES: frozenset[int] = frozenset({
    *range(220, 232), *range(236, 248), *range(252, 264), *range(268, 280),
    # 286..297 EXCEPT 295 — the source runs "…294, 296, 296, 297" (295 skipped,
    # 296 doubled); the mirror-vs-source test caught the naive range on day one.
    *range(286, 295), 296, 297,
    *range(321, 325), *range(467, 475), *range(476, 488),
    *range(492, 496), *range(543, 580), *range(581, 602), *range(610, 614),
    1010,
    *range(1741, 1758), *range(1771, 1791), *range(1801, 1810),
    *range(1811, 1825), *range(1831, 1855), *range(1861, 1885),
    *range(1981, 2005), *range(2028, 2034), *range(2100, 2106),
})


def _corner_top(cells: dict, x: int, y: int) -> int:
    """ServUO `Map.GetAverageZ`'s TOP for the tile at `(x, y)`.

    The server derives a land tile's surface from its FOUR corners — `(x,y)`, `(x,y+1)`,
    `(x+1,y)`, `(x+1,y+1)` — not from the single map cell, and a LandTarget's line-of-sight
    destination is that top plus one (`Map.cs`). Reading only `cells[(x, y)][1]` would
    understate a slope by exactly the amount that decides whether ore is above the eye,
    which is the whole question `max_rise` exists to answer.

    Missing corners (the caller's box edge) are skipped rather than treated as 0: a zero
    would read as a chasm and make a wall look targetable.
    """
    zs = [cells[p][1] for p in ((x, y), (x, y + 1), (x + 1, y), (x + 1, y + 1)) if p in cells]
    return max(zs) if zs else 0


def play_map(obs=None, *, fallback: int = 1) -> int:
    """The facet a survey must use: the one the body is standing on.

    `Observation.map_index` is 0=Felucca, 1=Trammel. A hardcoded `LUMBER_MAP = 1`
    while the character is on Felucca is follow-up 41 — mine stands happen to
    match across those two files, tree statics need not. `fallback` is only
    for the moment before the first observation exists.
    """
    if obs is not None:
        return int(obs.map_index)
    return int(fallback)


def find_mine_spots(map_index: int, cx: int, cy: int, radius: int = 40,
                    reach: int = 2, spacing: int = 8, min_face: int = 4,
                    max_rise: int = 13,
                    ) -> list[tuple[tuple[int, int], list[tuple[int, int, int, int]]]]:
    """Stand spots for a miner: open-ground tiles beside a mountain face, each
    paired with the mineable LAND tiles within mining `reach` (ServUO MaxRange=2)
    as (x, y, z, 0) nodes — graphic 0 is a LAND target, the fisher's convention.

    `spacing` keeps spots one HarvestBank cell apart (banks are 8x8, so spots
    closer than that share the same 10-34-ore bank and relocating between them
    is a walk to the same empty pool). Nearest-first from (cx, cy), so a
    relocation hop is as short as the terrain allows. Walkability of a stand is
    deliberately NOT proven here — no offline reader can (docs/WOODSMAN.md); the
    relocation walk's own stall-giveup degrades an unlucky spot gracefully.
    """
    # +1 on the high side because `_corner_top` reads `(x+1, y+1)`: without it every
    # stand on the box boundary silently loses corners and reads as flatter than it is.
    cells = {(x, y): (t, z) for x, y, t, z in
             land_cells(map_index, cx - radius, cy - radius,
                        cx + radius + 1, cy + radius + 1)}
    mine = {p for p, (t, _) in cells.items() if t in MINE_LAND_TILES}
    # Stand candidates: NON-mineable land bordering the face (mountain slopes are
    # impassable; the flat ground beside them is where a live miner stands).
    cand: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}
    for (mx, my) in mine:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            s = (mx + dx, my + dy)
            if s in mine or s not in cells or s in cand:
                continue
            nodes = [(nx, ny, cells[(nx, ny)][1], 0)
                     for nx in range(s[0] - reach, s[0] + reach + 1)
                     for ny in range(s[1] - reach, s[1] + reach + 1)
                     if (nx, ny) in mine]
            # DROP ORE THAT IS ABOVE THE MINER'S EYE. A cliff face is mineable land
            # at every height, and a stand at its FOOT is surrounded by ore it can
            # never target: ServUO runs the line-of-sight gate in `Target.Invoke`
            # (`Target.cs`), which is UPSTREAM of `OnTarget` -> `StartHarvesting`,
            # so a blocked tile is refused with 500237 before `CheckResources` ever
            # consults the ore bank. That is why such a stand reports invalid-target
            # replies forever with `nores=0` and `banks=` frozen — it is not an
            # exhausted vein, it is a vein the miner cannot see.
            #
            # Measured on the trade pool (audit §41): FIVE of the twelve stands this
            # function returned had EVERY node 20 or more above the stand, and the
            # three of those the 2026-08-13 run actually visited each produced a
            # 24-sample all-invalid window with zero ore. They were 100% of the dead
            # tail's cost and the survey handed them over as ordinary spots.
            #
            # `max_rise` is not tuned: ServUO puts the eye at `Mobile.Z + 14`
            # (`Map.cs`), so 13 is "at or below eye level". The empirical boundary on
            # this pool is 20, and ANY value from 11 to 19 yields the identical pool —
            # every dead stand loses all its nodes and fails `min_face`, every live one
            # keeps enough. A threshold whose exact value cannot change the answer is
            # the kind worth having.
            sz = _corner_top(cells, *s)
            nodes = [n for n in nodes
                     if _corner_top(cells, n[0], n[1]) - sz <= max_rise]
            # NEAREST-FIRST within the stand, because `nodes[0]` is not just an
            # ordering — it is the tile the miner swings at on arrival. Raster
            # order made it the top-left CORNER of the box, at chebyshev `reach`
            # exactly; and `Harvest._relocate_step` deliberately accepts arrival
            # ONE tile short of a surveyed stand (a wall-side stand is where the
            # greedy mover gets deflected on its last step). Corner + one tile
            # short = chebyshev 3, past ServUO's MaxRange=2, so the first swing of
            # a deflected arrival answered "That is too far away" — from the tile
            # the whole relocation was for. An adjacent node survives the same
            # deflection with a tile to spare.
            nodes.sort(key=lambda t: (max(abs(t[0] - s[0]), abs(t[1] - s[1])), t))
            if len(nodes) >= min_face:
                cand[s] = nodes
    # Keep only stands REACHABLE from the seed without crossing the face: a
    # mountain range has stands on both flanks, and a pool spot on the far side
    # is a wall away — forge12 live: the greedy relocation walker wedged into
    # the face, stall-gave-up, and burned the whole six-spot pool in minutes
    # without arriving anywhere. BFS over non-mineable land (the face as the
    # wall) is a cheap same-flank filter; trees/water/statics stay unmodeled —
    # the walk's own stall-giveup remains the net for those.
    seed = (cx, cy) if (cx, cy) in cells and (cx, cy) not in mine else None
    if seed is None and cand:
        seed = min(cand, key=lambda s: max(abs(s[0] - cx), abs(s[1] - cy)))
    depth: dict[tuple[int, int], int] = {}
    if seed is not None:
        from collections import deque as _deque

        depth[seed] = 0
        frontier = _deque([seed])
        while frontier:
            f = frontier.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (f[0] + dx, f[1] + dy)
                if n in cells and n not in mine and n not in depth:
                    depth[n] = depth[f] + 1
                    frontier.append(n)
        # Unreached = the far flank outright. Reached-but-roundabout (BFS path
        # much longer than the straight hop) = effectively the far flank too:
        # the greedy relocation walker has no A*, so a wrap-around route wedges
        # it into the face (forge12 live: six stall-giveups, whole pool burned).
        # Plain land-tile BFS misses static cliffs, so the bound does the work.
        cand = {s: n for s, n in cand.items()
                if s in depth
                and depth[s] <= 2 * max(abs(s[0] - cx), abs(s[1] - cy)) + 6}
    # Order by ACTUAL walk length, not straight-line: the pool is consumed as a
    # relocation itinerary, and the walker pays path tiles, not chebyshev.
    ordered = sorted(cand.items(),
                     key=lambda kv: depth.get(kv[0],
                                              max(abs(kv[0][0] - cx), abs(kv[0][1] - cy))))
    spots: list[tuple[tuple[int, int], list[tuple[int, int, int, int]]]] = []
    for s, nodes in ordered:
        if all(max(abs(s[0] - t[0]), abs(s[1] - t[1])) >= spacing for t, _ in spots):
            spots.append((s, nodes))
    return spots

#: ServUO allows a land step only when `startZ + StepHeight >= landZ`
#: (`Scripts/Services/Pathing/Movement.cs`, `StepHeight = 2`). Ascent is capped at 2;
#: descent is not. A VENDOR TRIP IS A ROUND TRIP, though, so a one-tile drop of 25 is
#: just an ascent of 25 on the way home — `walkable_run` therefore bounds the step BOTH
#: ways, which is the difference between a reachable shop and a one-way trip off a cliff.
STEP_HEIGHT = 2


def walkable_run(map_index: int, x0: int, y0: int, dx: int, dy: int,
                 max_len: int, *, climb: int = STEP_HEIGHT) -> int:
    """How many steps of `(dx, dy)` from `(x0, y0)` stay round-trip walkable.

    Returns 0 when the very first step is already too steep. Land z only: statics and
    mobiles are the walk's own problem, and this exists to stop a RUNNER placing a
    destination the ground itself forbids.

    Measured need, 2026-08-24 (audit §58): `run_warrior_village` stages its four vendors
    at a flat `+/-12` from the hunting pocket, and the pocket at `(2587, 408)` is a z=15
    plateau ringed by cliffs — the banker sat 54 z above the warrior behind a `+10` step,
    the healer behind a `+13`. Fifteen live days of `banked=0`, no vendor purchase ever,
    and `BACK ALIVE` stuck at 0 because the resurrection healer was on the wrong side of
    the same wall. Nothing in the runner had ever asked whether the ground allowed it.
    """
    cells = {(x, y): z for x, y, _t, z in land_cells(
        map_index,
        min(x0, x0 + dx * max_len), min(y0, y0 + dy * max_len),
        max(x0, x0 + dx * max_len), max(y0, y0 + dy * max_len))}
    prev = cells.get((x0, y0))
    if prev is None:
        return 0
    for step in range(1, max_len + 1):
        z = cells.get((x0 + dx * step, y0 + dy * step))
        if z is None or abs(z - prev) > climb:
            return step - 1
        prev = z
    return max_len
