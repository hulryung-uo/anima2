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
    Drop,
    Equip,
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

    # NOTE on sharing: pass the SAME `items` dict (and `blocked` set) to two MockBody
    # instances and they inhabit one world — a ground drop by one is visible to the
    # other, which is what the two-Life simulation exercises.

    player: PlayerView = field(default_factory=lambda: PlayerView(serial=0x1, name="Anima"))
    mobiles: dict[int, MobileView] = field(default_factory=dict)
    items: dict[int, ItemView] = field(default_factory=dict)
    # Tiles the player cannot enter.
    blocked: set[tuple[int, int]] = field(default_factory=set)
    bounds: tuple[int, int, int, int] = (0, 0, 1000, 1000)  # x0, y0, x1, y1
    _journal: list[JournalEntry] = field(default_factory=list)
    _journal_cursor: int = 0
    #: The lift cursor: at most ONE item in hand between PickUp and Drop, like the
    #: real client. `None` when empty.
    held: ItemView | None = None
    #: Serial source for split-off stacks (high range, clear of test fixtures).
    _held_serial_seq: int = 0x7F000000
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
            self._pick_up(action)
        elif isinstance(action, Drop):
            self._drop(action)
        elif isinstance(action, Equip):
            self._equip(action)
        # Other actions are accepted as no-ops in the mock.

    # --- the two-packet UO move: PickUp lifts to the cursor, Drop places -------------
    #
    # This used to DELETE on PickUp and ignore Drop entirely, which made the exact
    # mechanism both live supply chains run on (deliver: Drop to the ground; fetch:
    # PickUp then Drop into the pack) unrepresentable offline — the audit's proposal 7.
    # The mock now conserves items: lift, split stacks, place on ground or into a
    # container, never create or destroy.

    def _pick_up(self, action: PickUp) -> None:
        item = self.items.get(action.serial)
        if item is None or self.held is not None:
            return  # nothing there, or the cursor is already full — a server deny
        amount = getattr(action, "amount", None) or item.amount
        if amount < item.amount:
            # Split the stack: the remainder stays where it was, the lifted part gets
            # a fresh serial on the cursor (mirrors the server's split behaviour).
            item.amount -= amount
            self._held_serial_seq += 1
            self.held = ItemView(serial=self._held_serial_seq, graphic=item.graphic,
                                 amount=amount, pos=Position(), container=None,
                                 layer=0, distance=0)
        else:
            self.held = self.items.pop(action.serial)

    def _equip(self, action: Equip) -> None:
        """Wear the held item at a layer — the second packet of the equip two-step.
        Without this the first Life ticked over the mock wedged instantly: Harvest
        lifted its axe (PickUp) and the no-op Equip left it on the cursor forever,
        reading as permanently tool-less — offline, the exact mid-equip blip the
        warrior's hysteresis exists for live."""
        held = self.held
        if held is None or held.serial != action.serial:
            return
        held.container = self.player.serial
        held.layer = getattr(action, "layer", 1) or 1
        held.pos = Position()
        self.items[held.serial] = held
        self.held = None

    def _drop(self, action: Drop) -> None:
        held = self.held
        if held is None or held.serial != action.serial:
            # The contract drops BY SERIAL; with a split, the lifted half carries the
            # new serial, but real callers reuse the ORIGINAL serial across the
            # two-packet boundary — accept it for the common whole-stack case.
            if held is None:
                return
        container = getattr(action, "container", 0xFFFFFFFF)
        if container in (0xFFFFFFFF, None, 0):
            held.container = None
            held.pos = Position(action.x, action.y, action.z)
        else:
            held.container = container
            held.pos = Position()
        self.items[held.serial] = held
        self.held = None

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
