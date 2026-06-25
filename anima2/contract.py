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
    distance: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ItemView:
        return cls(
            serial=d["serial"],
            graphic=d.get("graphic", 0),
            amount=d.get("amount", 0),
            pos=Position.from_dict(d.get("pos", {})),
            container=d.get("container"),
            distance=d.get("distance", 0),
        )


@dataclass
class JournalEntry:
    serial: int
    name: str
    text: str
    msg_type: int
    hue: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JournalEntry:
        return cls(
            serial=d.get("serial", 0),
            name=d.get("name", ""),
            text=d.get("text", ""),
            msg_type=d.get("msg_type", 0),
            hue=d.get("hue", 0),
        )


@dataclass
class Observation:
    """A perception snapshot. ``mobiles`` and ``items`` are sorted by distance."""

    player: PlayerView = field(default_factory=PlayerView)
    mobiles: list[MobileView] = field(default_factory=list)
    items: list[ItemView] = field(default_factory=list)
    new_journal: list[JournalEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Observation:
        return cls(
            player=PlayerView.from_dict(d.get("player", {})),
            mobiles=[MobileView.from_dict(m) for m in d.get("mobiles", [])],
            items=[ItemView.from_dict(i) for i in d.get("items", [])],
            new_journal=[JournalEntry.from_dict(j) for j in d.get("new_journal", [])],
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


def action_from_dict(d: dict[str, Any]) -> Action:
    """Parse an Action from its JSON form (round-trips ``Action.to_dict``)."""
    t = d["type"]
    match t:
        case "Walk":
            return Walk(dir=d["dir"], run=d.get("run", False))
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
        case _:
            raise ValueError(f"unknown action type: {t!r}")
