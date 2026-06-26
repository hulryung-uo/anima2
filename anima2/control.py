"""Control plane — GM-driven scenario setup (DESIGN.md §3.1, A5).

A UO client is "just a player" and cannot reset/teleport/grant by itself. The
Control plane is a **separate** component: a GM-account connection that issues
ServUO `[` commands (over the same bridge, as speech + target answers) to set up
repeatable scenarios for the agent — give tools, set skills, teleport. Kept out
of the brain and the body. Technique mirrors anima v1's `foundry/kernel/gm.py`.

GM `[` commands that need a target are answered via the target-cursor contract:
serial targets → `TargetObject`, ground targets → `TargetGround`.
"""

from __future__ import annotations

from .contract import Say, TargetGround, TargetObject
from .ipc_body import IpcBody


class GmControl:
    """Drives a GM-account body to set up scenarios for another character."""

    def __init__(self, body: IpcBody) -> None:
        self.body = body

    @classmethod
    def spawn(
        cls,
        host: str = "127.0.0.1",
        port: int = 2594,
        username: str = "hulryung",
        password: str = "1212",
        *,
        bridge: str | None = None,
    ) -> GmControl:
        return cls(IpcBody.spawn(host, port, username, password, bridge=bridge, pump_ms=400))

    def close(self) -> None:
        self.body.close()

    def __enter__(self) -> GmControl:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- primitives ------------------------------------------------------------

    def hide(self) -> None:
        """Become an invisible groundskeeper so the agent never perceives us."""
        self.body.act(Say(text="[Self Set Hidden true"))
        self.body.observe()

    def _await_cursor(self, tries: int = 8) -> bool:
        """Pump until the `[` command opens a target cursor (or give up)."""
        for _ in range(tries):
            if self.body.observe().pending_target is not None:
                return True
        return False

    def command_on(self, command: str, serial: int) -> bool:
        """Run a `[` command that targets an entity by serial."""
        self.body.act(Say(text=command))
        if not self._await_cursor():
            return False
        self.body.act(TargetObject(serial=serial))
        self.body.observe()
        return True

    def command_at(self, command: str, x: int, y: int, z: int) -> bool:
        """Run a `[` command that targets a ground location."""
        self.body.act(Say(text=command))
        if not self._await_cursor():
            return False
        self.body.act(TargetGround(x=x, y=y, z=z))
        self.body.observe()
        return True

    def command_area(self, command: str, x1: int, y1: int, x2: int, y2: int, z: int) -> bool:
        """Run an area `[` command (e.g. `[WipeNPCs`) — two ground corners."""
        self.body.act(Say(text=command))
        if not self._await_cursor():
            return False
        self.body.act(TargetGround(x=x1, y=y1, z=z))
        if not self._await_cursor():
            return False
        self.body.act(TargetGround(x=x2, y=y2, z=z))
        self.body.observe()
        return True

    def find_item_near(self, x: int, y: int, graphic: int | None = None):
        """Find a spawned item at a tile (optionally by graphic). Returns ItemView | None."""
        for it in self.body.observe().items:
            if it.pos.x == x and it.pos.y == y and (graphic is None or it.graphic == graphic):
                return it
        return None

    def find_mobile_near(self, x: int, y: int, max_dist: int = 1):
        """Find a spawned mobile near a tile. Returns MobileView | None (nearest)."""
        cands = [m for m in self.body.observe().mobiles
                 if abs(m.pos.x - x) <= max_dist and abs(m.pos.y - y) <= max_dist]
        return min(cands, key=lambda m: m.distance) if cands else None

    def create_world(self, pumps: int = 60) -> list[str]:
        """Generate the full standard world via ServUO's built-in `[CreateWorld`.

        `nogump` runs every generator (Decorate, SignGen, DoorGen, TelGen,
        spawners…) with no gump or target. Generation takes a while; we pump to let
        it run and return any journal lines (progress / completion). Requires the GM
        account to have Administrator access.
        """
        self.body.act(Say(text="[CreateWorld nogump"))
        lines: list[str] = []
        for _ in range(pumps):
            for j in self.body.observe().new_journal:
                lines.append(j.text)
        return lines

    def go(self, x: int, y: int) -> tuple[int, int, int]:
        """`[Go` self to (x, y); returns the server-settled (x, y, z)."""
        self.body.act(Say(text=f"[Go {x} {y}"))
        pos = self.body.observe().player.pos
        for _ in range(6):
            if (pos.x, pos.y) == (x, y):
                break
            pos = self.body.observe().player.pos
        return (pos.x, pos.y, pos.z)

    # --- scenarios -------------------------------------------------------------

    def setup_miner(self, char_serial: int, x: int = 2567, y: int = 493) -> tuple[int, int, int]:
        """Stage a character to mine: pickaxe in pack, Mining 35, teleported to the
        calibrated Minoc ridge spot (anima v1's proven LANE_SPOTS[0])."""
        self.hide()
        gx, gy, gz = self.go(x, y)
        self.command_on("[AddToPack Pickaxe", char_serial)
        self.command_on("[Set Skills.Mining.Base 35", char_serial)
        self.command_on(f"[Set X {gx} Y {gy} Z {gz}", char_serial)
        return (gx, gy, gz)
