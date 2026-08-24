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
    SellItems,
    ShopBuy,
    ShopSell,
    ShopSellItem,
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
    #: `{graphic: price}` — what this vendor BUYS from us, and what it pays. The sell side
    #: of the shop, and follow-up 34.
    #:
    #: A flat map where `windows` is a list of openings, and the asymmetry is the
    #: protocol's rather than a shortcut. A BUY window is the vendor's own stock, which
    #: ServUO shows in partial subsets — the whole reason `windows` cycles. A SELL window
    #: (0x9E `SellList`) is a list of OUR PACK ITEMS the vendor recognises, rebuilt from
    #: the pack each time it opens, so there is no per-opening subset to model: what varies
    #: between openings is what we are carrying, and the pack already says that. Modelling
    #: it as openings would invent a failure mode the server does not have.
    #:
    #: Empty by default, so every fixture written before this behaves byte for byte as
    #: before — including the popup, which only grows a SELL entry when this is non-empty.
    buys: dict = field(default_factory=dict)


@dataclass
class MockBanker:
    """A banker — the popup entry and the box it opens, which is all `BankGold` asks for.

    Audit follow-up 22's other half. `MockVendor` closed the buy/sell side; the bank trip
    stayed live-only, and §63 is what that costs: the walk home and the arrival test had
    drifted apart into two copies of one constant, a **successful deposit was being
    recorded as a give-up for six frames a day**, and the only way to see any of it was to
    read a shard tape. Seventeen live warrior days and not one of them could be replayed.

    The box is not a stock list: `BankGold` proves a deposit by watching gold appear
    INSIDE a container worn at `BANKBOX_LAYER` (`_bankbox_gold`), so the box has to be a
    real item other items can be dropped into — which `MockBody._drop` already does by
    setting `container`. What this adds is only the two packets that make it VISIBLE:
    ServUO does not show a bank box until a banker has been asked to open one.
    """

    serial: int
    #: The box item's serial. It appears in `items` (worn at `BANKBOX_LAYER`) only after
    #: a `PopupSelect` on the Bank entry, exactly as `BankBox.Open()` behaves.
    box_serial: int
    #: Set by the mock when the box has been opened. A trip that never gets the popup
    #: right therefore finds no box, which is the failure this double exists to be able
    #: to reproduce.
    opened: bool = False


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
    #: Walkable rectangle, x0/y0/x1/y1. The default spans UO's own map rather than a
    #: convenient 1000x1000 box, because the small box was a SILENT trap: every production
    #: coordinate in this project is real UO ground (the trade mine is ~(2611,474), the Yew
    #: woods ~(2520,450)), so a fixture built at a real spot had every `Walk` refused as an
    #: edge bump and simply never moved. It cost a wrong conclusion — audit §31.2 reported
    #: three hypotheses "eliminated" by a reproduction that could not walk at all, when
    #: walking was the thing under investigation. A double whose failure mode is silent
    #: immobility teaches the wrong thing twice: once when it passes, once when it is
    #: believed.
    bounds: tuple[int, int, int, int] = (0, 0, 7168, 4096)  # x0, y0, x1, y1
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
    #: An open vendor SELL window (0x9E `SellList`). Follow-up 34: without it the sell
    #: FSM's `list` stage could never be reached offline, so the only proof this project
    #: had that a sale can COMPLETE was a live one — and §30.2 spent a whole day on a sell
    #: loop that never completed, with no offline control to compare against.
    shop_sell: object | None = None
    #: An open context menu, surfaced in every observation. Needed at all because the buy
    #: FSM's popup stage is a real two-packet exchange (`PopupRequest` then `PopupSelect`)
    #: and a mock that answered neither could never reach the `window` stage.
    popup: object | None = None
    #: `{serial: MockVendor}` — the shops this world contains. Empty by default, so every
    #: existing fixture behaves byte for byte as before.
    vendors: dict = field(default_factory=dict)
    #: `{serial: MockBanker}` — the bankers this world contains. Empty by default, so
    #: every existing fixture behaves byte for byte as before.
    bankers: dict = field(default_factory=dict)
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
                           shop_buy=self.shop_buy, shop_sell=self.shop_sell,
                           popup=self.popup)

    def act(self, action: Action) -> None:
        self.actions.append(action)
        if type(action).__name__ == "BuyItems" and not getattr(action, "items", None):
            self.shop_buy = None  # an empty buy list is the vendor-window cancel
            return
        if type(action).__name__ == "SellItems" and not getattr(action, "items", None):
            # The sell side's own cancel — `_sell_step` sends exactly this when it finds a
            # SELL window already open at the popup stage, and it is the exit-edge close
            # follow-up 16 added. Modelled, or a trip that cancels looks like one that hung.
            self.shop_sell = None
            return
        if (self.vendors or self.bankers) and self._vendor_act(action):
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
        from .skills.market import BUY_CLILOC, SELL_CLILOC

        from .skills.market import BANK_CLILOC, BANKBOX_LAYER

        if isinstance(action, PopupRequest) and action.serial in self.bankers:
            self.popup = PopupMenu(serial=action.serial,
                                   entries=[PopupEntry(index=0, cliloc=BANK_CLILOC)])
            return True

        if isinstance(action, PopupSelect) and action.serial in self.bankers:
            self.popup = None
            banker = self.bankers[action.serial]
            # ONLY the Bank entry opens a box. A menu index the banker never offered
            # leaves the box shut, which is what makes "the popup stage went wrong"
            # reproducible instead of merely suspected.
            if action.index == 0:
                banker.opened = True
                self.items[banker.box_serial] = ItemView(
                    serial=banker.box_serial, graphic=0x0E7C, amount=1, pos=Position(),
                    container=self.player.serial, layer=BANKBOX_LAYER, distance=0)
            return True

        if isinstance(action, PopupRequest):
            vendor = self.vendors.get(action.serial)
            if vendor is None:
                return False
            self.popup = PopupMenu(serial=vendor.serial,
                                   entries=self._popup_entries(vendor))
            return True

        if isinstance(action, PopupSelect):
            vendor = self.vendors.get(action.serial)
            if vendor is None:
                return False
            self.popup = None
            # WHICH entry was clicked. The buy and sell FSMs both send a `PopupSelect`
            # and they differ only in the index they resolved from the menu, so a mock
            # that ignored the index would open a BUY window for a sell trip — and the
            # sell FSM would then sit in `list` until `ASK_RETRY` and give up, which is
            # indistinguishable from the live wedge this double exists to rule out.
            chosen = next((e.cliloc for e in self._popup_entries(vendor)
                           if e.index == action.index), BUY_CLILOC)
            if chosen == SELL_CLILOC:
                self.shop_sell = self._sell_window(vendor)
                return True
            # THIS opening's subset, then advance the cursor. A one-element `windows`
            # therefore shows the same entries forever, which is the point.
            entries = (list(vendor.windows[vendor.opens % len(vendor.windows)])
                       if vendor.windows else [])
            vendor.opens += 1
            self.shop_buy = ShopBuy(vendor=vendor.serial, container=vendor.container,
                                    entries=entries)
            return True

        if isinstance(action, SellItems) and action.items:
            vendor = self.vendors.get(action.vendor)
            if vendor is None or self.shop_sell is None:
                return False
            self._settle_sale(vendor, action)
            self.shop_sell = None      # ServUO closes the window on a completed sale
            return True

        if isinstance(action, BuyItems) and action.items:
            vendor = self.vendors.get(action.vendor)
            if vendor is None or self.shop_buy is None:
                return False
            self._settle_purchase(action)
            self.shop_buy = None       # ServUO closes the window on a completed order
            return True
        return False

    def _popup_entries(self, vendor) -> list:
        """The vendor's context menu. BUY always; SELL only when the vendor buys anything.

        Conditional, so every fixture written before the sell side existed sees the exact
        menu it saw then. It also mirrors ServUO: `VendorSellEntry` is added by
        `BaseVendor.AddCustomContextEntries` only for a vendor with an active buy list, so
        a vendor that buys nothing genuinely has no Sell entry to click.
        """
        from .skills.market import BUY_CLILOC, SELL_CLILOC

        entries = [PopupEntry(index=0, cliloc=BUY_CLILOC)]
        if vendor.buys:
            entries.append(PopupEntry(index=1, cliloc=SELL_CLILOC))
        return entries

    def _sell_window(self, vendor) -> ShopSell:
        """A 0x9E `SellList`: OUR pack items this vendor recognises, priced.

        Built from the pack at open time, not from stored state — that is what the server
        does, and it is why a sale that empties the pack yields an EMPTY window on the next
        opening rather than a stale one. `_sell_step` reads that empty list as "the vendor
        does not recognise the item" and gives up, which is a real live shape.
        """
        from .skills.harvest import BACKPACK_LAYER

        pack = next((i.serial for i in self.items.values()
                     if i.container == self.player.serial and i.layer == BACKPACK_LAYER),
                    None)
        items = [
            ShopSellItem(serial=i.serial, graphic=i.graphic, hue=0, amount=i.amount,
                         price=vendor.buys[i.graphic])
            for i in self.items.values()
            if pack is not None and i.container == pack and i.graphic in vendor.buys
        ]
        return ShopSell(vendor=vendor.serial, items=items)

    def _settle_sale(self, vendor, action: SellItems) -> None:
        """Take the goods, pay the coin — the mirror of `_settle_purchase`.

        Only items the vendor actually BUYS are taken, and only up to the amount present:
        a request naming a serial the vendor does not want, or more than the stack holds,
        moves nothing. The server owns those rules and a double that invented its own
        would be asserting a contract this project does not control — the same line
        `_settle_purchase` draws around under-payment and stock depletion.
        """
        from .skills.harvest import BACKPACK_LAYER
        from .skills.hunt import GOLD_GRAPHIC

        pack = next((i.serial for i in self.items.values()
                     if i.container == self.player.serial and i.layer == BACKPACK_LAYER),
                    None)
        if pack is None:
            return
        paid = 0
        for serial, amount in action.items:
            item = self.items.get(serial)
            if (item is None or item.container != pack or amount <= 0
                    or item.graphic not in vendor.buys):
                continue
            taken = min(amount, item.amount)
            item.amount -= taken
            paid += vendor.buys[item.graphic] * taken
            if item.amount <= 0:
                self.items.pop(serial, None)
        if not paid:
            return
        coin = next((i for i in self.items.values()
                     if i.graphic == GOLD_GRAPHIC and i.container == pack), None)
        if coin is not None:
            coin.amount += paid
        else:
            self._held_serial_seq += 1
            self.items[self._held_serial_seq] = ItemView(
                serial=self._held_serial_seq, graphic=GOLD_GRAPHIC, amount=paid,
                pos=Position(), container=pack, layer=0, distance=0)

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
            # SPLIT THE STACK THE WAY THE SERVER DOES, which is the opposite of what this
            # used to do while claiming to mirror it. ServUO's `Mobile.LiftItemDupe`
            # (`Server/Mobile.cs`) gives the NEW item `oldAmount - amount` and leaves it
            # in the parent container, then sets `oldItem.Amount = amount` and lifts the
            # ORIGINAL onto the cursor. So the serial the caller asked for is the serial
            # that MOVES; the remainder is the one that gets a fresh id.
            #
            # The direction is load-bearing, not cosmetic. `BankGold`'s achievement proof
            # requires `cap_bank_start_piles_cleared` — every manifest serial GONE from
            # the pack — and `cap_bank_start_piles_removed` to equal the surplus. With
            # the split backwards, a deposit that fully succeeded (gold in the box,
            # `pack_delta` and `bank_delta` both right) still failed both clauses, so the
            # first offline bank trip this project has ever been able to run reported a
            # FAILURE the shard does not produce.
            self._held_serial_seq += 1
            self.items[self._held_serial_seq] = ItemView(
                serial=self._held_serial_seq, graphic=item.graphic,
                amount=item.amount - amount, pos=item.pos, container=item.container,
                layer=item.layer, distance=item.distance)
            item.amount = amount
            self.held = self.items.pop(action.serial)
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
