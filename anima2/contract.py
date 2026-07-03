"""The Observation/Action contract — the stable seam between the body and the brain.

A faithful Python mirror of ``anima-core``'s ``src/agent.rs`` (the Rust producer).
Keep these in lockstep: a future IPC bridge serializes anima-core's `Observation`
to JSON and parses the brain's `Action` from JSON using exactly these shapes.

JSON shape (chosen for the IPC bridge):
- Observation: ``{"player": {...}, "mobiles": [...], "items": [...], "new_journal": [...]}``
- Action: externally tagged by a ``"type"`` field, e.g. ``{"type": "Walk", "dir": 0, "run": false}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Position:
    x: int = 0
    y: int = 0
    z: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Position:
        return cls(x=d.get("x", 0), y=d.get("y", 0), z=d.get("z", 0))


@dataclass
class PlayerView:
    serial: int = 0
    name: str = ""
    pos: Position = field(default_factory=Position)
    direction: int = 0
    hits: int = 0
    hits_max: int = 0
    mana: int = 0
    mana_max: int = 0
    stam: int = 0
    stam_max: int = 0
    strength: int = 0
    dexterity: int = 0
    intelligence: int = 0
    gold: int = 0
    weight: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlayerView:
        d = dict(d)
        d["pos"] = Position.from_dict(d.get("pos", {}))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class MobileView:
    serial: int
    name: str
    pos: Position
    body: int
    notoriety: int
    hits: int
    hits_max: int
    distance: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MobileView:
        return cls(
            serial=d["serial"],
            name=d.get("name", ""),
            pos=Position.from_dict(d.get("pos", {})),
            body=d.get("body", 0),
            notoriety=d.get("notoriety", 0),
            hits=d.get("hits", 0),
            hits_max=d.get("hits_max", 0),
            distance=d.get("distance", 0),
        )


@dataclass
class ItemView:
    serial: int
    graphic: int
    amount: int
    pos: Position
    container: int | None
    layer: int  # worn layer (0 if not equipped); 0x15 == backpack
    distance: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ItemView:
        return cls(
            serial=d["serial"],
            graphic=d.get("graphic", 0),
            amount=d.get("amount", 0),
            pos=Position.from_dict(d.get("pos", {})),
            container=d.get("container"),
            layer=d.get("layer", 0),
            distance=d.get("distance", 0),
        )


@dataclass
class JournalEntry:
    serial: int
    name: str
    text: str
    msg_type: int
    hue: int
    # Cliloc id for localized messages (0xC1/0xCC); 0 for plain speech. When set,
    # `text` holds the raw tab-separated args — resolve via anima2.cliloc.
    cliloc: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JournalEntry:
        return cls(
            serial=d.get("serial", 0),
            name=d.get("name", ""),
            text=d.get("text", ""),
            msg_type=d.get("msg_type", 0),
            hue=d.get("hue", 0),
            cliloc=d.get("cliloc", 0),
        )


@dataclass
class SkillView:
    """One skill's standing, in human units (50.0 == half of GM). Mirrors anima-core."""

    id: int
    value: float  # base + transient bonuses
    base: float  # trainable value — skill *gain* registers here
    cap: float
    lock: int  # 0 up, 1 down, 2 locked

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SkillView:
        return cls(
            id=d.get("id", 0),
            value=d.get("value", 0.0),
            base=d.get("base", 0.0),
            cap=d.get("cap", 0.0),
            lock=d.get("lock", 0),
        )


@dataclass
class TargetCursor:
    """An outstanding target the server is waiting on (mirrors anima-core)."""

    target_type: int  # 0 = object/serial, 1 = ground/location
    cursor_id: int
    cursor_flag: int  # 0 neutral, 1 harmful, 2 helpful

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TargetCursor:
        return cls(
            target_type=d.get("target_type", 0),
            cursor_id=d.get("cursor_id", 0),
            cursor_flag=d.get("cursor_flag", 0),
        )


@dataclass
class GumpView:
    """An open server gump/dialog (e.g. a craft menu). Answer with GumpResponse."""

    serial: int
    gump_id: int
    layout: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GumpView:
        return cls(serial=d.get("serial", 0), gump_id=d.get("gump_id", 0), layout=d.get("layout", ""))


@dataclass
class ShopBuyEntry:
    """One line of a vendor's BUY window: `(price, name)` in packet order —
    anima-core matches these to the vendor's for-sale container items by index
    (mirrors `anima_core::world::ShopBuy.entries`; see `json.rs::shop_buy_json`).
    """

    price: int
    name: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ShopBuyEntry:
        return cls(price=d.get("price", 0), name=d.get("name", ""))


@dataclass
class ShopBuy:
    """A vendor's BUY window (0x74 OpenBuyWindow). Answer with `BuyItems`."""

    vendor: int
    container: int
    entries: list[ShopBuyEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ShopBuy:
        return cls(
            vendor=d.get("vendor", 0),
            container=d.get("container", 0),
            entries=[ShopBuyEntry.from_dict(e) for e in d.get("entries", [])],
        )


@dataclass
class ShopSellItem:
    """One line of a vendor's SELL window (0x9E SellList): an item in our pack
    the vendor will buy, and the price it pays for it.
    """

    serial: int
    graphic: int
    hue: int
    amount: int
    price: int
    name: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ShopSellItem:
        return cls(
            serial=d.get("serial", 0),
            graphic=d.get("graphic", 0),
            hue=d.get("hue", 0),
            amount=d.get("amount", 0),
            price=d.get("price", 0),
            name=d.get("name", ""),
        )


@dataclass
class ShopSell:
    """A vendor's SELL window (0x9E SellList). Answer with `SellItems`."""

    vendor: int
    items: list[ShopSellItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ShopSell:
        return cls(vendor=d.get("vendor", 0), items=[ShopSellItem.from_dict(i) for i in d.get("items", [])])


@dataclass
class PopupEntry:
    """One line of a right-click context menu (0xBF/0x14): `index` is echoed
    back verbatim in `PopupSelect` to choose it; `cliloc` is the label's
    localized-string id (resolve via `anima2.cliloc`) — e.g. ServUO's
    `VendorSellEntry`/`OpenBankEntry` constants are `6104`/`6105`, so a skill
    can pick an entry by cliloc without ever resolving the text. What actually
    arrives here depends on which popup-cliloc layout the server negotiates,
    though: on a shard that negotiates the *legacy* layout, `anima-core`'s
    `parse_popup` reconstructs the full id as the constant **+3,000,000**
    (e.g. `3006104`/`3006105`), not the bare constant — live-verified against
    a real ServUO shard, see `anima2/skills/market.py`'s `SELL_CLILOC`/
    `BANK_CLILOC` for a worked example of a skill picking entries this way.
    """

    index: int
    cliloc: int
    flags: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PopupEntry:
        return cls(index=d.get("index", 0), cliloc=d.get("cliloc", 0), flags=d.get("flags", 0))


@dataclass
class PopupMenu:
    """An open right-click context menu (0xBF/0x14) for `serial`. Answer with
    `PopupSelect`.
    """

    serial: int
    entries: list[PopupEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PopupMenu:
        return cls(serial=d.get("serial", 0), entries=[PopupEntry.from_dict(e) for e in d.get("entries", [])])


@dataclass
class CorpseLink:
    """One corpse→killed-mobile link (0xAF DisplayDeath). Mirrors anima-core's
    `Observation.corpse_of`: `(corpse_serial, killed_mobile_serial)`, letting a
    brain confirm "this is the corpse of what I killed" before looting (see
    `anima2/skills/hunt.py::Hunt`).
    """

    corpse: int
    killed: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CorpseLink:
        return cls(corpse=d.get("corpse", 0), killed=d.get("killed", 0))


@dataclass
class CorpseEquipEntry:
    """One worn-item entry from a corpse's equipment layout (0x89 CorpseEquip):
    `(layer, item_serial)`. `Hunt` deliberately never reads these in its MVP —
    see that module's docstring — this mirror exists for completeness/future use.
    """

    layer: int
    serial: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CorpseEquipEntry:
        return cls(layer=d.get("layer", 0), serial=d.get("serial", 0))


@dataclass
class CorpseEquip:
    """A corpse's worn-item layout (0x89 CorpseEquip) — what the creature had
    *equipped* at death, distinct from its container contents (`Observation.items`
    with `container == corpse_serial`, the ordinary loot `Hunt` picks up).
    """

    corpse: int
    entries: list[CorpseEquipEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CorpseEquip:
        return cls(
            corpse=d.get("corpse", 0),
            entries=[CorpseEquipEntry.from_dict(e) for e in d.get("entries", [])],
        )


@dataclass
class Observation:
    """A perception snapshot. ``mobiles`` and ``items`` are sorted by distance."""

    player: PlayerView = field(default_factory=PlayerView)
    mobiles: list[MobileView] = field(default_factory=list)
    items: list[ItemView] = field(default_factory=list)
    new_journal: list[JournalEntry] = field(default_factory=list)
    # Set when the server wants us to pick a target (answer with TargetObject/Ground).
    pending_target: TargetCursor | None = None
    skills: list[SkillView] = field(default_factory=list)
    gumps: list[GumpView] = field(default_factory=list)
    # A vendor's BUY/SELL window, when one is open (0x74/0x9E) — `None` when
    # neither is open, exactly like `pending_target`. Mirrors anima-net's
    # `shop_buy`/`shop_sell` observation keys (`json.rs::observation_to_json`).
    shop_buy: ShopBuy | None = None
    shop_sell: ShopSell | None = None
    # An open right-click context menu (0xBF/0x14) — `None` when none is open.
    popup: PopupMenu | None = None
    # Corpse→killed-mobile links (0xAF DisplayDeath) and each corpse's worn-item
    # layout (0x89 CorpseEquip) — unlike `pending_target`/`shop_buy`/`popup`
    # these are *lists*, not a single "currently open" slot (several corpses can
    # be tracked at once), so an absent/empty key is just `[]`, not `None`.
    # Mirrors anima-net's `corpse_of`/`corpse_equip` observation keys
    # (`json.rs::observation_to_json`). See `anima2/skills/hunt.py::Hunt`.
    corpse_of: list[CorpseLink] = field(default_factory=list)
    corpse_equip: list[CorpseEquip] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Observation:
        pt = d.get("pending_target")
        sb = d.get("shop_buy")
        ss = d.get("shop_sell")
        pu = d.get("popup")
        return cls(
            player=PlayerView.from_dict(d.get("player", {})),
            mobiles=[MobileView.from_dict(m) for m in d.get("mobiles", [])],
            items=[ItemView.from_dict(i) for i in d.get("items", [])],
            new_journal=[JournalEntry.from_dict(j) for j in d.get("new_journal", [])],
            pending_target=TargetCursor.from_dict(pt) if pt else None,
            skills=[SkillView.from_dict(s) for s in d.get("skills", [])],
            gumps=[GumpView.from_dict(g) for g in d.get("gumps", [])],
            shop_buy=ShopBuy.from_dict(sb) if sb else None,
            shop_sell=ShopSell.from_dict(ss) if ss else None,
            popup=PopupMenu.from_dict(pu) if pu else None,
            corpse_of=[CorpseLink.from_dict(c) for c in d.get("corpse_of", [])],
            corpse_equip=[CorpseEquip.from_dict(c) for c in d.get("corpse_equip", [])],
        )


# --- Actions (mirror anima-core `enum Action`) ---------------------------------


class Action:
    """Base class for high-level intents the brain emits; the body executes them."""

    type: str

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError


@dataclass
class Walk(Action):
    """Step one tile in UO direction 0..7 (running optional)."""

    dir: int
    run: bool = False
    type: str = field(default="Walk", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "Walk", "dir": self.dir, "run": self.run}


@dataclass
class WalkTo(Action):
    """Queue a non-blocking auto-walk (click-to-walk) route to `(x, y)` —
    anima-net's `Session::advance_route` (an A* route, `anima-core::path`)
    drives it one step per `pump` call at its own cadence, routing around
    static obstacles (mountains, buildings) a greedy tile-by-tile `Walk` can't.
    Unlike every other Action, this one only *starts* something: there is no
    "arrived"/"blocked" reply on the wire — the brain must infer progress (or
    a stall) from `x`/`y` deltas across successive Observations, exactly like
    `skills/movement.py::GoTo` does. A later `Walk` cancels an in-flight
    route; a later `WalkTo` replaces it with a fresh one (mirrors
    `Session::apply_action`).

    `x`/`y` ride the wire as `u16`: `anima-net`'s `json.rs::action_from_json`
    requires both as present, whole-number JSON values and **errors** (not a
    silent 0/map-origin default) on a missing/non-integer coordinate;
    `action_from_dict` mirrors that by requiring both keys (a missing one
    raises `KeyError`, matching the "must error, not silently default"
    discipline `json.rs`'s own comment states). Neither side range-checks:
    an out-of-range integer would wrap in the Rust `as u16` cast, so keep
    coordinates on the map (every current producer already does).
    """

    x: int
    y: int
    type: str = field(default="WalkTo", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "WalkTo", "x": self.x, "y": self.y}


@dataclass
class Say(Action):
    text: str
    type: str = field(default="Say", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "Say", "text": self.text}


@dataclass
class Attack(Action):
    serial: int
    type: str = field(default="Attack", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "Attack", "serial": self.serial}


@dataclass
class Use(Action):
    """Double-click ("use") an item or mobile."""

    serial: int
    type: str = field(default="Use", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "Use", "serial": self.serial}


@dataclass
class Click(Action):
    """Single-click (request the name/label)."""

    serial: int
    type: str = field(default="Click", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "Click", "serial": self.serial}


@dataclass
class PickUp(Action):
    serial: int
    amount: int = 1
    type: str = field(default="PickUp", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "PickUp", "serial": self.serial, "amount": self.amount}


@dataclass
class WarMode(Action):
    on: bool
    type: str = field(default="WarMode", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "WarMode", "on": self.on}


@dataclass
class Equip(Action):
    """Equip an item from the pack to a worn layer (1 = one-handed weapon)."""

    serial: int
    layer: int
    type: str = field(default="Equip", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "Equip", "serial": self.serial, "layer": self.layer}


@dataclass
class Drop(Action):
    serial: int
    x: int = 0
    y: int = 0
    z: int = 0
    container: int = 0xFFFFFFFF
    type: str = field(default="Drop", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "Drop", "serial": self.serial, "x": self.x, "y": self.y,
                "z": self.z, "container": self.container}


@dataclass
class CastSpell(Action):
    spell: int
    type: str = field(default="CastSpell", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "CastSpell", "spell": self.spell}


@dataclass
class GumpResponse(Action):
    """Answer an open gump (0xB0/0xDD) — e.g. press a craft-menu button."""

    serial: int
    gump_id: int
    button: int = 0
    switches: list[int] = field(default_factory=list)
    entries: list[tuple[int, str]] = field(default_factory=list)
    type: str = field(default="GumpResponse", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "GumpResponse",
            "serial": self.serial,
            "gump_id": self.gump_id,
            "button": self.button,
            "switches": list(self.switches),
            "entries": [list(e) for e in self.entries],
        }


@dataclass
class TargetObject(Action):
    """Answer a pending target cursor by selecting an object/mobile."""

    serial: int
    type: str = field(default="TargetObject", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "TargetObject", "serial": self.serial}


@dataclass
class TargetGround(Action):
    """Answer a pending target cursor by selecting a ground location."""

    x: int
    y: int
    z: int = 0
    graphic: int = 0
    type: str = field(default="TargetGround", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "TargetGround", "x": self.x, "y": self.y, "z": self.z, "graphic": self.graphic}


@dataclass
class BuyItems(Action):
    """Answer an open `ShopBuy` window (0x3B) — buy `items` (each `(serial,
    amount)`, the container item's serial and how many of it to buy) from `vendor`.
    """

    vendor: int
    items: list[tuple[int, int]] = field(default_factory=list)
    type: str = field(default="BuyItems", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "BuyItems", "vendor": self.vendor, "items": [list(i) for i in self.items]}


@dataclass
class SellItems(Action):
    """Answer an open `ShopSell` window (0x9F) — sell `items` (each `(serial,
    amount)`, a pack item's serial and how many of its stack to sell) to `vendor`.
    """

    vendor: int
    items: list[tuple[int, int]] = field(default_factory=list)
    type: str = field(default="SellItems", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "SellItems", "vendor": self.vendor, "items": [list(i) for i in self.items]}


@dataclass
class PopupRequest(Action):
    """Request the right-click context (popup) menu for `serial` (0xBF/0x13)."""

    serial: int
    type: str = field(default="PopupRequest", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "PopupRequest", "serial": self.serial}


@dataclass
class PopupSelect(Action):
    """Choose entry `index` from the open context menu for `serial` (0xBF/0x15)."""

    serial: int
    index: int
    type: str = field(default="PopupSelect", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "PopupSelect", "serial": self.serial, "index": self.index}


def action_from_dict(d: dict[str, Any]) -> Action:
    """Parse an Action from its JSON form (round-trips ``Action.to_dict``)."""
    t = d["type"]
    match t:
        case "Walk":
            return Walk(dir=d["dir"], run=d.get("run", False))
        case "WalkTo":
            return WalkTo(x=d["x"], y=d["y"])
        case "Say":
            return Say(text=d["text"])
        case "Attack":
            return Attack(serial=d["serial"])
        case "Use":
            return Use(serial=d["serial"])
        case "Click":
            return Click(serial=d["serial"])
        case "PickUp":
            return PickUp(serial=d["serial"], amount=d.get("amount", 1))
        case "WarMode":
            return WarMode(on=d["on"])
        case "Equip":
            return Equip(serial=d["serial"], layer=d["layer"])
        case "Drop":
            return Drop(serial=d["serial"], x=d.get("x", 0), y=d.get("y", 0),
                        z=d.get("z", 0), container=d.get("container", 0xFFFFFFFF))
        case "CastSpell":
            return CastSpell(spell=d["spell"])
        case "GumpResponse":
            return GumpResponse(
                serial=d["serial"], gump_id=d["gump_id"], button=d.get("button", 0),
                switches=list(d.get("switches", [])),
                entries=[tuple(e) for e in d.get("entries", [])],
            )
        case "TargetObject":
            return TargetObject(serial=d["serial"])
        case "TargetGround":
            return TargetGround(x=d["x"], y=d["y"], z=d.get("z", 0), graphic=d.get("graphic", 0))
        case "BuyItems":
            return BuyItems(vendor=d["vendor"], items=[tuple(i) for i in d.get("items", [])])
        case "SellItems":
            return SellItems(vendor=d["vendor"], items=[tuple(i) for i in d.get("items", [])])
        case "PopupRequest":
            return PopupRequest(serial=d["serial"])
        case "PopupSelect":
            return PopupSelect(serial=d["serial"], index=d["index"])
        case _:
            raise ValueError(f"unknown action type: {t!r}")
