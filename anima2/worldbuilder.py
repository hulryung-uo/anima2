"""WorldBuilder — GM-driven construction of a scenario "world".

Generalizes the Control plane from staging one character (`GmControl.setup_miner`)
to **building the environment agents live in**: clearing an area, placing
structures (forge/anvil), vendors (Blacksmith/Banker/Provisioner…), signs, and
doors. Drives a `GmControl` via ServUO `[Add` / `[WipeNPCs` commands. Kept in the
Control plane — separate from the brain and the body (DESIGN.md §3.1).

ServUO types used (verified against the local shard): doors are `DarkWoodDoor`
etc. taking a `DoorFacing`; signs are `Sign` (named via `[Set Name`); vendors are
`BaseVendor` subclasses (`Blacksmith`, `Banker`, `Provisioner`, ...).
"""

from __future__ import annotations

from dataclasses import dataclass, field

SIGN_GRAPHIC = 0x0B95  # ServUO Sign default itemID


@dataclass
class Placement:
    """One thing to place, at an (x, y) offset from the scene origin."""

    kind: str  # "vendor" | "door" | "sign" | "item"
    spec: str  # ServUO type, or sign text
    dx: int
    dy: int
    facing: str = "SouthCW"  # doors only


@dataclass
class Scene:
    """A named scenario: an origin tile + things to place around it."""

    name: str
    placements: list[Placement] = field(default_factory=list)


class WorldBuilder:
    """Builds a `Scene` into the live world via a `GmControl`."""

    def __init__(self, gm) -> None:
        self.gm = gm

    # --- primitives ------------------------------------------------------------

    def clear(self, x: int, y: int, z: int, radius: int = 8) -> None:
        """Wipe stray NPCs and items in a box so re-runs don't accumulate clutter."""
        self.gm.command_area("[WipeNPCs", x - radius, y - radius, x + radius, y + radius, z)
        self.gm.command_area("[WipeItems", x - radius, y - radius, x + radius, y + radius, z)

    def add_vendor(self, vendor_type: str, x: int, y: int, z: int) -> int | None:
        self.gm.command_at(f"[Add {vendor_type}", x, y, z)
        m = self.gm.find_mobile_near(x, y)
        return m.serial if m else None

    def add_door(self, door_type: str, facing: str, x: int, y: int, z: int) -> None:
        self.gm.command_at(f"[Add {door_type} {facing}", x, y, z)

    def add_sign(self, text: str, x: int, y: int, z: int) -> int | None:
        """Place a sign and label it with `text` (via `[Set Name` on the new item)."""
        self.gm.command_at("[Add Sign", x, y, z)
        sign = self.gm.find_item_near(x, y, graphic=SIGN_GRAPHIC)
        if sign is None:
            return None
        self.gm.command_on(f'[Set Name "{text}"', sign.serial)
        return sign.serial

    def add_item(self, item_type: str, x: int, y: int, z: int) -> None:
        self.gm.command_at(f"[Add {item_type}", x, y, z)

    # --- scenes ----------------------------------------------------------------

    def build(self, scene: Scene, ox: int, oy: int, oz: int) -> dict:
        """Place every element of `scene` around origin (ox, oy, oz). Returns a
        summary of what was created (serials where known)."""
        created: dict[str, list] = {"vendors": [], "signs": [], "doors": [], "items": []}
        for p in scene.placements:
            x, y = ox + p.dx, oy + p.dy
            if p.kind == "vendor":
                created["vendors"].append((p.spec, self.add_vendor(p.spec, x, y, oz)))
            elif p.kind == "door":
                self.add_door(p.spec, p.facing, x, y, oz)
                created["doors"].append((p.spec, (x, y)))
            elif p.kind == "sign":
                created["signs"].append((p.spec, self.add_sign(p.spec, x, y, oz)))
            else:
                self.add_item(p.spec, x, y, oz)
                created["items"].append((p.spec, (x, y)))
        return created


def blacksmith_shop() -> Scene:
    """A small smithy: forge + anvil to work, a Blacksmith vendor, a sign, a door."""
    return Scene(
        name="blacksmith_shop",
        placements=[
            Placement("item", "Forge", -1, 0),
            Placement("item", "Anvil", 1, 0),
            Placement("vendor", "Blacksmith", 0, -1),
            Placement("vendor", "Banker", 2, -1),
            Placement("sign", "Anima Smithy", 0, -2),
            Placement("door", "DarkWoodDoor", 0, 2, facing="SouthCW"),
        ],
    )
