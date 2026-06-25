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
