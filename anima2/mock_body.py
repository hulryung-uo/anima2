"""An in-memory fake UO world implementing `Body`.

Lets the brain loop run and be unit-tested with **no Rust core and no server**.
It is intentionally tiny — just enough world physics to exercise perception,
movement, and skills — and doubles as the substrate for curriculum simulation
later. Not a UO simulator; a test double.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contract import (
    Action,
    ItemView,
    JournalEntry,
    MobileView,
    Observation,
    PickUp,
    PlayerView,
    Position,
    Say,
    Walk,
)
from .geometry import DIRECTION_DELTAS, chebyshev


@dataclass
class MockBody:
    """A trivial flat world. Player walks; items can be picked up; speech is logged."""

    player: PlayerView = field(default_factory=lambda: PlayerView(serial=0x1, name="Anima"))
    mobiles: dict[int, MobileView] = field(default_factory=dict)
    items: dict[int, ItemView] = field(default_factory=dict)
    # Tiles the player cannot enter.
    blocked: set[tuple[int, int]] = field(default_factory=set)
    bounds: tuple[int, int, int, int] = (0, 0, 1000, 1000)  # x0, y0, x1, y1
    _journal: list[JournalEntry] = field(default_factory=list)
    _journal_cursor: int = 0
    said: list[str] = field(default_factory=list)

    # --- Body protocol ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        return True

    def observe(self) -> Observation:
        ppos = self.player.pos
        mobiles = sorted(
            (self._with_distance_m(m, ppos) for m in self.mobiles.values()),
            key=lambda m: m.distance,
        )
        items = sorted(
            (self._with_distance_i(i, ppos) for i in self.items.values()),
            key=lambda i: i.distance,
        )
        new = self._journal[self._journal_cursor :]
        self._journal_cursor = len(self._journal)
        return Observation(player=self.player, mobiles=mobiles, items=items, new_journal=new)

    def act(self, action: Action) -> None:
        if isinstance(action, Walk):
            self._walk(action.dir)
        elif isinstance(action, Say):
            self.said.append(action.text)
            self._journal.append(
                JournalEntry(self.player.serial, self.player.name, action.text, 0, 0)
            )
        elif isinstance(action, PickUp):
            self.items.pop(action.serial, None)
        # Other actions are accepted as no-ops in the mock.

    # --- world helpers ---------------------------------------------------------

    def _walk(self, direction: int) -> None:
        self.player.direction = direction & 0x07
        dx, dy = DIRECTION_DELTAS[direction & 0x07]
        nx, ny = self.player.pos.x + dx, self.player.pos.y + dy
        x0, y0, x1, y1 = self.bounds
        if (nx, ny) in self.blocked or not (x0 <= nx <= x1 and y0 <= ny <= y1):
            return  # bumped a wall / edge: a turn, not a move (mirrors a server deny)
        self.player.pos = Position(nx, ny, self.player.pos.z)

    @staticmethod
    def _with_distance_m(m: MobileView, ppos: Position) -> MobileView:
        m.distance = chebyshev(ppos, m.pos)
        return m

    @staticmethod
    def _with_distance_i(i: ItemView, ppos: Position) -> ItemView:
        i.distance = chebyshev(ppos, i.pos)
        return i

    def inject_journal(self, name: str, text: str, serial: int = 0) -> None:
        """Test helper: simulate someone speaking near the player."""
        self._journal.append(JournalEntry(serial, name, text, 0, 0))
