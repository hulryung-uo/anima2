"""Shared conventions for `live_*.py` scripts (PHASE5.md item 2's
consolidation rider).

Every live script that stages a scenario on the persistent local ServUO shard
independently grew the same handful of copy-paste patterns: a `Body` wrapper
that caches the last `Observation` so a driver loop can inspect it without
paying for a second `observe()` pump (`_RecordingBody` — six near-identical
copies before this module: `live_trade.py`, `live_hunt.py`, `live_market.py`,
`live_navigate.py`, `live_smelt.py`, `live_curriculum.py`), a GM area wipe
before staging (`[WipeItems`/`[WipeNPCs` over a radius — `[Add`/ground drops
are additive on a persistent shard, see the `anima2-live-verification` memory
note), a fresh-account-per-run naming convention (a unix-time suffix, since
reusing a name piles debris onto whatever a prior run left), a `time.sleep`
between logins to dodge ServUO's per-IP login throttle, and a final
`[FLAG] name = value` verdict-line print each gate script's own `main()`
hand-rolled. This module extracts all five, matching what the migrated
scripts already did — one copy instead of many, not a new abstraction.

**Migrated to use this module** (Phase 5 item 2): `live_fitness_gate.py`,
`live_mine.py`, `live_trade.py`, and `foundry/eval.py` itself (`live_eval_gate.py`
is built on it from the start). **Not yet migrated** (still carry their own
copy — a follow-up, not urgent; each one works as-is): `live_hunt.py`,
`live_market.py`, `live_navigate.py`, `live_smelt.py`, `live_curriculum.py`.
"""

from __future__ import annotations

import time
from typing import Any

from .contract import Action, Observation
from .control import GmControl


class RecordingBody:
    """Wraps a `Body`, caching the last `Observation` so a driver loop can
    inspect it without paying for a second `observe()` pump on top of the
    one `Agent.tick()` already does — with more than one agent (or a probe
    closure) reading state every loop iteration, that doubling matters for
    wall-clock time. Ported verbatim from `live_trade.py`'s original
    `_RecordingBody` (see this module's own docstring for the full list of
    scripts that grew their own copy).
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.last_obs: Observation | None = None

    def observe(self) -> Observation:
        self.last_obs = self._inner.observe()
        return self.last_obs

    def act(self, action: Action) -> None:
        self._inner.act(action)

    @property
    def connected(self) -> bool:
        return self._inner.connected


#: ServUO login throttle — sleeping this long between account logins avoids a
#: rejected login; `LOGIN_BURST_COOLDOWN_S` is the longer cooldown a script
#: should sleep after several logins in a row (e.g. before opening a
#: post-run cross-check connection). Named defaults, not re-guessed numbers —
#: ported from `live_fitness_gate.py`'s original hand-picked 4s/8s split.
LOGIN_THROTTLE_S = 4.0
LOGIN_BURST_COOLDOWN_S = 8.0
#: The single shared GM account (`hulryung`) leaves a stale server-side
#: session for a while after logout — live-caught by `foundry/eval.py`'s
#: `run_eval` reconnecting it every eval (unlike each eval's own unique,
#: never-reused subject account): a `LOGIN_THROTTLE_S` gap was enough for
#: the subject login but the very next `GmControl.spawn(hulryung)` still hit
#: a mid-session "connection closed by server" a few `[Get` calls in — the
#: same "stale GM hulryung session blocks re-login for ~15s after a run"
#: finding recorded in the `anima2-project` memory note. Named separately
#: from `LOGIN_BURST_COOLDOWN_S` since it's about ONE specific
#: always-reused account, not a general "several logins in a row" cooldown.
GM_RELOGIN_COOLDOWN_S = 15.0


def login_throttle(seconds: float = LOGIN_THROTTLE_S) -> None:
    """Sleep between ServUO account logins — dodges the login throttle every
    live script that spawns more than one `IpcBody`/`GmControl` in quick
    succession has independently had to add."""
    time.sleep(seconds)


def fresh_suffix() -> str:
    """A unix-time-derived account-name suffix, freshness-guaranteeing every
    caller's account names without a shared counter — ported from
    `live_fitness_gate.py`'s original `--suffix` default."""
    return str(int(time.time()) % 1_000_000)


def wipe_bounds(gm: GmControl, x1: int, y1: int, x2: int, y2: int, z: int = 20) -> None:
    """`[WipeItems`/`[WipeNPCs` over the ground rectangle `(x1,y1)-(x2,y2)` —
    the two-corner form `live_trade.py`'s own bounding-box wipe (between a
    miner spot and a smith spot) needs; `wipe_area` below is the common
    center+radius case built on top of this."""
    gm.command_area("[WipeItems", x1, y1, x2, y2, z)
    gm.command_area("[WipeNPCs", x1, y1, x2, y2, z)


def wipe_area(gm: GmControl, cx: int, cy: int, radius: int, z: int = 20) -> None:
    """`wipe_bounds` over a `radius`-tile square centered on `(cx, cy)` —
    every staged scenario opens with this so a prior run's debris never
    pollutes the next. Ported verbatim from `live_trade.py`/
    `live_fitness_gate.py`'s own wipe step."""
    wipe_bounds(gm, cx - radius, cy - radius, cx + radius, cy + radius, z)


def print_gate_verdict(flags: dict[str, bool], *, label: str = "GATE", detail: str = "") -> bool:
    """Print each named boolean flag as `[FLAG] name = value`, then one final
    `[FLAG] {label} {PASSED|FAILED}[: {detail}]` line — the per-flag +
    verdict-line convention every live gate script's own `main()` already
    hand-rolled (e.g. `live_fitness_gate.py`'s three RANKING flags + its own
    closing GATE line). Returns the overall verdict (`all(flags.values())`)
    so a caller can use it as an exit signal too.
    """
    for name, value in flags.items():
        print(f"[FLAG] {name} = {value}")
    passed = all(flags.values())
    verdict = "PASSED" if passed else "FAILED"
    suffix = f": {detail}" if detail else ""
    print(f"[FLAG] {label} {verdict}{suffix}")
    return passed
