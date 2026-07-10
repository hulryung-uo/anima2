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

import re
from collections.abc import Iterable

from .contract import Say, TargetGround, TargetObject
from .ipc_body import IpcBody

#: A ServUO `[Get <prop>` reply always echoes the exact property name back,
#: e.g. `"Skills.Mining.Base = 42.5"`, `"TotalGold = 1000 (0x3E8)"`,
#: `'Name = "Anima"'` — or, on failure, `"Property '<prop>' not found."` /
#: `"You must be at least <level> to get the property '<prop>'."` (live-
#: observed against a real ServUO shard; see `parse_property_reply`'s own
#: docstring). This matches the value half after the echoed `"<prop> = "`
#: prefix, stripping an optional trailing hex annotation (`" (0x...)"`) that
#: numeric replies carry.
_NUMERIC_VALUE = re.compile(r"^(-?\d+(?:\.\d+)?)(?:\s*\(0x[0-9A-Fa-f]+\))?$")


def parse_property_reply(raw: str | None, prop: str) -> float | str | None:
    """Parse a `GmControl.get_property` raw reply into a typed value.

    `raw` may be several `" | "`-joined journal lines (the raw variant's own
    noisy-journal fallback) — this picks out the one that actually echoes
    `prop` back (ServUO's `[Get`/`[Set` always do, live-confirmed: see
    `control.py`'s module docstring reference in PHASE4.md item 3/PHASE5.md
    item 1) rather than assuming the first line is the reply. Returns:
      - `float` for a numeric reply (`"Str = 60 (0x3C)"` → `60.0`,
        `"Skills.Mining.Base = 42.5"` → `42.5`) — always `float`, never
        `int`, matching this function's own return type;
      - `str` for a non-numeric reply, quotes stripped (`'Name = "Anima"'`
        → `"Anima"`) or left as-is for a compound value ServUO doesn't quote
        (`"Location = (3734, 2222, 20)"` → `"(3734, 2222, 20)"`);
      - `None` if `raw` is `None`, empty, or no line echoes `prop` at all
        (an error reply like `"Property 'Gold' not found."` — the property
        name that server-side ServUO actually uses often differs from the
        in-game display name, e.g. `TotalGold`, not `Gold` — or an access-
        level-denied reply, neither of which echoes `"<prop> = "`).
    """
    if not raw:
        return None
    prefix = f"{prop} = "
    line = next((seg for seg in raw.split(" | ") if seg.startswith(prefix)), None)
    if line is None:
        return None
    value = line[len(prefix):]
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    m = _NUMERIC_VALUE.match(value)
    if m:
        return float(m.group(1))
    return value


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

    def get_property(self, prop: str, serial: int, *, pumps: int = 6) -> str | None:
        """Run `[Get <prop>` targeting `serial` and return the resulting
        self-directed journal text (or `None` if nothing new arrived within
        `pumps` observations) — the same `[` + `TargetObject` mechanism
        `command_on` already uses (`stage()`'s own `[Set` commands go through
        it), except this command's answer is a journal line, not a property
        write, so the caller reads it back afterward.

        Hardened past PHASE4.md item 3's "returned empty" finding: this
        collects journal text across up to `pumps` observations instead of
        returning on the first tick that has *any* new journal content — in
        a noisy scene (e.g. mid-combat, `live_hunt.py`'s own case) an
        unrelated system/combat line can land in an earlier pump than the
        actual `[Get` reply, and the old early-return would hand back that
        unrelated line (or, if the reply lands one pump later than an empty
        one, silently miss it — indistinguishable from "the server sent
        nothing"). Once a line that echoes `prop` back (ServUO's own `[Get`
        reply convention — see `parse_property_reply`) shows up, this stops
        polling and returns everything collected so far, `" | "`-joined; the
        exact-echo line is what `get_property_value` below picks out. Still
        advisory-only raw plumbing (PHASE4.md item 3's own measurement-
        independence caveat) — parsing now happens in `get_property_value`,
        not here, so this keeps returning the full raw text for eyeballing.
        """
        self.body.act(Say(text=f"[Get {prop}"))
        if not self._await_cursor():
            return None
        self.body.act(TargetObject(serial=serial))
        lines: list[str] = []
        prefix = f"{prop} = "
        for _ in range(pumps):
            obs = self.body.observe()
            lines.extend(j.text for j in obs.new_journal)
            if any(line.startswith(prefix) for line in lines):
                break
        return " | ".join(lines) if lines else None

    def get_property_value(self, prop: str, serial: int, *, pumps: int = 6) -> float | str | None:
        """`get_property` + `parse_property_reply` — a typed readback (Phase
        5 item 1's load-bearing independent channel: `[Get` reads the
        *server's* value, which the measured agent's own code can't forge).
        `None` on no reply / an error reply (bad property name, insufficient
        access level); see `parse_property_reply` for the exact typing.
        """
        return parse_property_reply(self.get_property(prop, serial, pumps=pumps), prop)

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

    def find_mobile_near(self, x: int, y: int, max_dist: int = 3,
                         exclude: int | Iterable[int] | None = None, retries: int = 3):
        """Find a spawned mobile near a tile. Returns MobileView | None (nearest
        to `(x, y)` — NOT `m.distance`, which is relative to *this* body's own
        position, not the query tile; sorting by that instead could return a
        mobile merely close to us, e.g. our own staged character passing by,
        rather than the one actually at `(x, y)` — live-caught while chasing
        down a `[Add`-spawned NPC's serial for `live_market.py`'s evidence log).

        `max_dist` is generous by default, not the ~1-tile a naive "it's
        right where I asked" assumption would suggest: `[Add`'s ground-target
        placement can settle a tile or two off the exact requested spot
        (live-observed) — matches `skills/market.py`'s own
        `MOBILE_SEARCH_RADIUS` for the same reason. `retries` re-observes a
        few times before giving up empty-handed — a just-`[Add`-ed NPC's own
        position report can lag a tick or two behind the command that
        spawned it.

        `exclude` drops one serial, or a whole collection of them, from
        consideration — a staged character can legitimately be standing
        within `max_dist` of a spot a `[Add`-ed NPC was *also* just placed at
        (workplace tiles are typically only a few apart), and picking the
        wrong one silently mislabels which mobile is "the vendor"/"the
        banker" (live-caught: a GM command meant for the freshly-added NPC
        landed on the staged character instead). A caller that knows its
        *entire* roster of agent serials (e.g. `village.py`, once more than
        one agent is staged) should pass all of them — excluding only the
        character currently being staged still leaves *other* known agents
        (e.g. a co-located trade miner standing within a widened radius of
        the trade smithy's own vendor/banker spots) eligible to be
        mis-resolved as the NPC. There's no reliable "is this an NPC, not a
        player" signal in the observation surface today (a `[Add`-ed
        vendor/banker's `body`/`notoriety` look just like an ordinary human
        player character's) — a thorough `exclude` set plus nearest-to-
        `(x, y)` is the best available proxy.
        """
        if exclude is None:
            excluded: frozenset[int] = frozenset()
        elif isinstance(exclude, int):
            excluded = frozenset({exclude})
        else:
            excluded = frozenset(exclude)

        for _ in range(max(1, retries)):
            cands = [m for m in self.body.observe().mobiles
                     if abs(m.pos.x - x) <= max_dist and abs(m.pos.y - y) <= max_dist
                     and m.serial not in excluded]
            if cands:
                return min(cands, key=lambda m: max(abs(m.pos.x - x), abs(m.pos.y - y)))
        return None

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

    def stage_npc(self, command: str, x: int, y: int, z: int, *,
                  exclude: int | Iterable[int] | None = None):
        """`[Add <command>` at `(x, y, z)`, find the spawned mobile, and pin it
        with `[Set CantWalk true` so it can't wander off (`VendorAI.
        DoActionWander` roams an idle `BaseVendor`/`Banker` — live-caught
        drifting a freshly-added NPC out of a fixed search radius/route).

        `find_mobile_near`'s own docstring already covers *finding* a
        `[Add`-ed NPC that settled a tile or two off the requested spot, but a
        mis-settle isn't always harmless: if it lands on a route waypoint
        another agent's greedy walk needs to cross (e.g. `skills/market.py`'s
        hub tile), pinning it there with `CantWalk` doesn't just misplace the
        NPC — it permanently walls off that tile (live-caught: a banker
        `[Add`-ed at `BANKER_SPOT`'s final waypoint settled one step short, on
        the hub tile itself, denying every future step the smith tried to take
        through it — the same "genuinely blocked, not desynced" `DenyWalk` a
        real collision would produce). So before pinning, this corrects the
        position back to the exact requested spot whenever it drifted, the
        same `[Set X Y Z` lift `stage()` already uses to place a character.
        Returns the mobile (now sitting exactly on `(x, y)` and pinned), or
        `None` if nothing was found to pin.
        """
        self.command_at(f"[Add {command}", x, y, z)
        npc = self.find_mobile_near(x, y, exclude=exclude)
        if npc is None:
            return None
        if (npc.pos.x, npc.pos.y) != (x, y):
            self.command_on(f"[Set X {x} Y {y} Z {z}", npc.serial)
        self.command_on("[Set CantWalk true", npc.serial)
        return npc

    def stage(
        self,
        char_serial: int,
        x: int,
        y: int,
        *,
        skills: dict[str, float] | None = None,
        items: list[str] | None = None,
    ) -> tuple[int, int, int]:
        """Stage a character for work: set skills, add tools to the pack, and
        teleport it to the (server-settled) workplace at (x, y). The Control plane
        in one call — generalizes `setup_miner` to any profession.

        Teleporting `char_serial` needs the GM to be **near the character's
        current position**, not near the destination — live-confirmed against
        a real ServUO shard: unlike `[Set Skills...`/`[AddToPack` (grant
        commands, which succeed at any distance) and `[Get` (also
        distance-independent, see `get_property`), `[Set X`/`[Set Y`/`[Set Z`
        (the position sub-properties `[Set X {gx} Y {gy} Z {gz}` below
        writes) silently reply "That is too far away" (cliloc 500446) when
        the GM issuing them isn't near where the character *currently* is.
        Every existing caller reuses a small pool of characters that have
        already been staged near a calibrated spot before, so the GM (parked
        at the destination by the `self.go(x, y)` below, which is *also*
        wherever it left off last time) is fortuitously already close enough
        — but a genuinely fresh account (never staged before, PHASE5.md
        item 1's live gate needs several) spawns far from any calibrated
        spot, and the teleport used to no-op silently (`command_on`'s return
        value went unchecked): skills/items land fine (they don't need
        proximity), but the character itself never leaves its login point,
        so every downstream distance-relative assumption (tools in reach,
        workplace geometry) silently breaks too. Fixed by reading the
        character's own current position back first
        (`get_property_value("X"/"Y", ...)` — itself distance-independent)
        and detouring the GM there before the `[Set X Y Z`, then returning
        to the work location — a cheap self-teleport round trip, harmless
        when the account was already warm (the detour lands on/near the same
        spot `self.go(x, y)` already did).
        """
        self.hide()
        gx, gy, gz = self.go(x, y)
        cur_x = self.get_property_value("X", char_serial)
        cur_y = self.get_property_value("Y", char_serial)
        detoured = isinstance(cur_x, float) and isinstance(cur_y, float)
        if detoured:
            self.go(int(cur_x), int(cur_y))
        self.command_on(f"[Set X {gx} Y {gy} Z {gz}", char_serial)
        if detoured:
            self.go(x, y)
        for skill, base in (skills or {}).items():
            self.command_on(f"[Set Skills.{skill}.Base {base}", char_serial)
        for item in items or []:
            self.command_on(f"[AddToPack {item}", char_serial)
        return (gx, gy, gz)

    def setup_miner(self, char_serial: int, x: int = 2567, y: int = 493) -> tuple[int, int, int]:
        """Stage a miner at the calibrated Minoc ridge (anima v1's LANE_SPOTS[0])."""
        return self.stage(char_serial, x, y,
                          skills={"Mining": 35}, items=["Pickaxe", "Pickaxe"])
