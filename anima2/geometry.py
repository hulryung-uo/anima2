"""UO direction/geometry helpers (matches anima-core's direction convention)."""

from __future__ import annotations

from .contract import Position

# UO directions 0..7 → (dx, dy). Mirrors anima-core types.rs / net::movement.
#   0 N, 1 NE(Right), 2 E, 3 SE(Down), 4 S, 5 SW(Left), 6 W, 7 NW(Up)
DIRECTION_DELTAS: list[tuple[int, int]] = [
    (0, -1),  # 0 North
    (1, -1),  # 1 Right / NE
    (1, 0),  # 2 East
    (1, 1),  # 3 Down / SE
    (0, 1),  # 4 South
    (-1, 1),  # 5 Left / SW
    (-1, 0),  # 6 West
    (-1, -1),  # 7 Up / NW
]


def chebyshev(a: Position, b: Position) -> int:
    """Chebyshev (king-move) distance — matches anima-core's `distance`."""
    return max(abs(a.x - b.x), abs(a.y - b.y))


def direction_toward(frm: Position, to: Position) -> int:
    """The UO direction (0..7) that best steps from `frm` toward `to`."""
    dx = (to.x > frm.x) - (to.x < frm.x)  # sign: -1, 0, or 1
    dy = (to.y > frm.y) - (to.y < frm.y)
    if (dx, dy) == (0, 0):
        return 0
    return DIRECTION_DELTAS.index((dx, dy))
