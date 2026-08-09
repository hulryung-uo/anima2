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
    BuyItems,
    Drop,
    Equip,
    ItemView,
    JournalEntry,
    MobileView,
    Observation,
    PickUp,
    PlayerView,
    PopupEntry,
    PopupMenu,
    PopupRequest,
    PopupSelect,
    Position,
    Say,
    ShopBuy,
    Walk,
)
from .geometry import DIRECTION_DELTAS, chebyshev


@dataclass
class MockVendor:
    """A vendor's shop — as much of one as the buy FSM actually asks for, and no more.

    Audit follow-up 22, which was deferred twice on the argument that "adding a vendor to
    `MockBody` is a production-code change for a test's benefit". That argument got weaker
    every time a live artifact turned out to be unreproducible offline for want of one:
    §8.2's Life-level wedge reproduction was run from a scratch harness instead, and §19's
    bound-1 gate — the only live proof of the give-up ladder there is — has NO offline
    reproduction at all where the bound-3 gate has seven. `MockBody`'s own docstring calls
    it "a test double", so this is test infrastructure that happens to live in the package.

    **`windows` is a LIST OF OPENINGS, not a stock list, and that is the whole design.**
    ServUO shows a vendor's goods in partial subsets: one opening can lack an item the next
    one carries, which is the ~15-of-45-entry pairing bug `OFFER_REOPEN_ATTEMPTS` exists
    for. A single-element list means every opening is identical — the Healer of §19, which
    never has our offer no matter how many times we re-roll. Several elements cycle, so a
    test can prove a re-roll genuinely FINDS an offer the first window lacked. Modelling
    this as a flat stock would make the re-roll path untestable by construction, which is
    exactly the gap this class exists to close.
    """

    serial: int
    windows: list[list] = field(default_factory=list)
    container: int = 0x7000
    #: Windows opened so far — the cycle cursor, and a test's evidence of how many
    #: re-rolls actually reached the server.
    opens: int = 0


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
    #: Open gumps, surfaced in every observation. Sixteen capability gates refuse
    #: while ANY UI surface is open, so "a gump nobody owns" is a whole class of
    #: live wedge (forge15) that was unrepresentable offline until this field.
    gumps: list = field(default_factory=list)
    #: An open vendor BUY window, same reason as `gumps` (forge16's wedge shape).
    shop_buy: object | None = None
    #: An open context menu, surfaced in every observation. Needed at all because the buy
    #: FSM's popup stage is a real two-packet exchange (`PopupRequest` then `PopupSelect`)
    #: and a mock that answered neither could never reach the `window` stage.
    popup: object | None = None
    #: `{serial: MockVendor}` — the shops this world contains. Empty by default, so every
    #: existing fixture behaves byte for byte as before.
    vendors: dict = field(default_factory=dict)
    #: Every action the agent asked for, in order — the mock's only way to prove a
    #: repair was actually ATTEMPTED rather than merely decided.
    actions: list = field(default_factory=list)

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
        return Observation(player=self.player, mobiles=mobiles, items=items,
                           new_journal=new, gumps=list(self.gumps),
                           shop_buy=self.shop_buy, popup=self.popup)

    def act(self, action: Action) -> None:
        self.actions.append(action)
        if type(action).__name__ == "BuyItems" and not getattr(action, "items", None):
            self.shop_buy = None  # an empty buy list is the vendor-window cancel
            return
        if self.vendors and self._vendor_act(action):
            return
        if type(action).__name__ == "GumpResponse" and getattr(action, "button", None) == 0:
            # Button 0 is CLOSE, the same answer the craft FSM sends — model it, so a
            # test can prove the wedge actually clears rather than just that an action
            # was emitted.
            self.gumps = [g for g in self.gumps if g.gump_id != action.gump_id]
            return
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

    # --- the vendor: the three packets a buy trip actually exchanges -----------------

    def _vendor_act(self, action: Action) -> bool:
        """Answer `PopupRequest` / `PopupSelect` / `BuyItems` for a staged vendor.

        Returns True when the action was a vendor exchange this world handled, so `act`
        can stop; False leaves every other action on its existing path. Deliberately
        narrow — a body double owes the FSM the packets it sends and nothing else.
        """
        # Local import to keep this module's "imports nothing from `skills`" property at
        # import time: `BUY_CLILOC` is a ServUO protocol constant that happens to live in
        # `skills/market.py`, and taking it as a parameter would make every caller repeat
        # a number the FSM already knows.
        from .skills.market import BUY_CLILOC

        if isinstance(action, PopupRequest):
            vendor = self.vendors.get(action.serial)
            if vendor is None:
                return False
            self.popup = PopupMenu(serial=vendor.serial,
                                   entries=[PopupEntry(index=0, cliloc=BUY_CLILOC)])
            return True

        if isinstance(action, PopupSelect):
            vendor = self.vendors.get(action.serial)
            if vendor is None:
                return False
            self.popup = None
            # THIS opening's subset, then advance the cursor. A one-element `windows`
            # therefore shows the same entries forever, which is the point.
            entries = (list(vendor.windows[vendor.opens % len(vendor.windows)])
                       if vendor.windows else [])
            vendor.opens += 1
            self.shop_buy = ShopBuy(vendor=vendor.serial, container=vendor.container,
                                    entries=entries)
            return True

        if isinstance(action, BuyItems) and action.items:
            vendor = self.vendors.get(action.vendor)
            if vendor is None or self.shop_buy is None:
                return False
            self._settle_purchase(action)
            self.shop_buy = None       # ServUO closes the window on a completed order
            return True
        return False

    def _settle_purchase(self, action: BuyItems) -> None:
        """Move the goods in and the coin out — the only part of a shop that a brain can
        actually observe. Under-payment is not modelled and neither is stock depletion:
        both are server rules, and a double that enforced them would be asserting a
        contract this project does not own."""
        from .skills.harvest import BACKPACK_LAYER
        from .skills.hunt import GOLD_GRAPHIC

        pack = next((i.serial for i in self.items.values()
                     if i.container == self.player.serial and i.layer == BACKPACK_LAYER),
                    None)
        if pack is None or self.shop_buy is None:
            return
        offers = {e.serial: e for e in self.shop_buy.entries}
        for serial, amount in action.items:
            entry = offers.get(serial)
            if entry is None or amount <= 0:
                continue
            self._held_serial_seq += 1
            self.items[self._held_serial_seq] = ItemView(
                serial=self._held_serial_seq, graphic=entry.graphic, amount=amount,
                pos=Position(), container=pack, layer=0, distance=0)
            owed = entry.price * amount
            for coin in [i for i in self.items.values()
                         if i.graphic == GOLD_GRAPHIC and i.container == pack]:
                if owed <= 0:
                    break
                spend = min(owed, coin.amount)
                coin.amount -= spend
                owed -= spend
            for empty in [s for s, i in self.items.items()
                          if i.graphic == GOLD_GRAPHIC and i.amount <= 0]:
                self.items.pop(empty)

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
