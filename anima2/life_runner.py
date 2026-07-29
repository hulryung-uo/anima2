"""LifeRunner — the harness a Life runs on, so profession N+1 inherits every lesson.

Five runners grew in `village.py` by copy-paste, and the copies drifted: each new one
shipped WITHOUT some piece of infrastructure its predecessor had already paid for live —
the want/admitted/ready telemetry (added after an artisan stalled invisibly), the leash
on BOTH agent memories (added after a carpenter wandered off its own supply), staged-NPC
readback (added after a Banker resolved to the Weaponsmith's serial), starter-gold
provenance (added after a carpenter banked 940 unearned gold and reported it as income).
The commit record calls this out plainly: "carrying lessons forward is the part I keep
skipping."

This module makes the lessons STRUCTURAL. `LifeSpec` is a dataclass whose load-bearing
fields have no defaults — a runner that forgets its profession key or staging function
does not quietly run without telemetry, it fails to construct. `LifeRunner.run()` owns
the sequence nobody should re-implement: spawn (with the monitor seat), login throttle,
GM staging via the spec, **provenance always** (starter gold deleted before any seed is
granted), leash applied to BOTH agent memories, the worker thread with budget scaling,
and the standard status line — mode, want/admitted/ready, hp, pack gold, banked gold
(read from the bank BOX, the way `live_bank_goal.py` proves a deposit), and the loud
rule-vs-gate disagreement line.

The helpers are exported separately so the older multi-agent runners (`run_supply_pair`,
`run_warrior_village`, `run_artisan_mage_village`) can share the same code paths without
being forced through the single-agent harness shape.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .capabilities import ready_capability_ids
from .contract import Observation
from .control import GmControl
from .ipc_body import ResilientIpcBody
from .live_common import GM_RELOGIN_COOLDOWN_S, fresh_suffix, login_throttle
from .persona import Persona
from .skills.base import SkillContext
from .skills.harvest import BACKPACK_LAYER
from .skills.hunt import GOLD_GRAPHIC
from .skills.market import BANKBOX_LAYER, SELL_REACH

#: First HTTP port handed to `--monitor`; each agent gets the next one up. Loopback
#: only (the bridge hardcodes 127.0.0.1), so this never exposes a shard to the network.
MONITOR_PORT_BASE = 8801


def monitor_ports(enabled: bool, roles: list[str]) -> dict[str, int | None]:
    """One viewer port per role, or all `None` when monitoring is off.

    Each agent runs its own bridge process and therefore its own viewer — a shard
    allows exactly one session per character, so there is no way to watch several
    characters through one connection.
    """
    if not enabled:
        return {r: None for r in roles}
    return {r: MONITOR_PORT_BASE + i for i, r in enumerate(roles)}


# --- observation readbacks (the ~15 copy-pasted `_pack` helpers, once) ---------------

def pack_serial(obs: Observation | None) -> int | None:
    """OUR backpack — owner-filtered, since a neighbour's pack shares the layer."""
    if obs is None:
        return None
    return next((i.serial for i in obs.items
                 if i.layer == BACKPACK_LAYER and i.container == obs.player.serial), None)


def pack_amount(obs: Observation | None, graphics) -> int:
    """Summed amount of `graphics` (an int or a set) in OUR backpack."""
    bp = pack_serial(obs)
    if bp is None:
        return 0
    if isinstance(graphics, int):
        graphics = {graphics}
    return sum(i.amount for i in obs.items if i.graphic in graphics and i.container == bp)


def banked_amount(obs: Observation | None) -> int:
    """Gold sitting in THIS character's bank box — the deposit proof `live_bank_goal.py`
    uses. A falling pack count alone is not evidence of banking: it is equally
    consistent with spending it, dropping it, or dying with it."""
    if obs is None:
        return 0
    box = next((i.serial for i in obs.items
                if i.layer == BANKBOX_LAYER and i.container == obs.player.serial), None)
    if box is None:
        return 0
    return sum(i.amount for i in obs.items
               if i.graphic == GOLD_GRAPHIC and i.container == box)


def owned_tool_readout(obs: Observation | None, graphics) -> str:
    """`"yes"`/`"NO"` for a tool WE could actually use — ours or loose on the ground,
    never a neighbour's. Reporting "yes" for the Weaponsmith's axe is how a status line
    once hid the exact defect that broke a run."""
    if obs is None:
        return "NO"
    bp = pack_serial(obs)
    return "yes" if any(i.graphic in graphics
                        and i.container in (bp, obs.player.serial, None)
                        for i in obs.items) else "NO"


# --- staging (verified, never assumed) -----------------------------------------------

class StagingError(RuntimeError):
    """A shop failed to stage, resolved to an already-placed NPC, or landed on a tile
    another shop occupies. Raised (not printed) by strict staging: a solo Life with a
    missing or aliased shop is a broken run, and half-verified staging is the same trap as
    unverified staging in better clothes."""


def stage_shops(gm: GmControl, *, z: int, anchor: tuple[int, int],
                spots: dict[str, tuple[str, tuple[int, int]]],
                exclude, strict: bool = True,
                placed: dict[int, str] | None = None) -> tuple[dict, set]:
    """Stage every shop in `spots` (`key -> (npc_name, (x, y))`), READ BACK where each
    actually landed, and verify identity, uniqueness and reach.

    Every clause here is a live-caught lesson:
    - excludes the NPCs already placed, not just the player — `find_mobile_near` returns
      the nearest mobile, and with shops a tile apart that was a Banker resolving to the
      Weaponsmith's own serial (a banker_spot pointing at a vendor with no bank box);
    - prints each shop's real distance from `anchor` and flags OUT OF REACH — an
      unreachable shop is outwardly indistinguishable from a broken planner;
    - two shops on one tile is an error, not a curiosity.

    Returns `(routes, npc_tiles)`. With `strict=False` failures print and leave the
    route unset (the older multi-agent runners' behavior); with `strict=True` they raise
    `StagingError`.
    """
    routes: dict = {}
    # `placed` may be shared across calls: a multi-anchor runner stages shops for two
    # agents, and a shop placed for one must never answer for the other's.
    placed = placed if placed is not None else {}
    exclude = set(exclude) if not isinstance(exclude, int) else {exclude}
    for key, (npc, (nx, ny)) in spots.items():
        mob = gm.stage_npc(npc, nx, ny, z, exclude={*exclude, *placed})
        if mob is None or mob.serial in placed:
            msg = (f"{npc}: FAILED to stage — {key} left unset" if mob is None else
                   f"{npc}: resolved to the {placed[mob.serial]} NPC — {key} left unset")
            if strict:
                raise StagingError(msg)
            print(f"  {msg}")
            continue
        placed[mob.serial] = key
        reach = max(abs(mob.pos.x - anchor[0]), abs(mob.pos.y - anchor[1]))
        routes[key] = [(mob.pos.x, mob.pos.y)]
        flag = "" if reach <= SELL_REACH else "  ** OUT OF REACH **"
        print(f"  {npc}: at ({mob.pos.x},{mob.pos.y}), {reach} from the stand{flag}")
        if strict and reach > SELL_REACH:
            raise StagingError(f"{npc} landed {reach} tiles from the stand — out of reach")
    tiles = [tuple(v[0]) for v in routes.values()]
    if len(set(tiles)) != len(tiles):
        msg = f"two shops share a tile: {routes}"
        if strict:
            raise StagingError(msg)
        print(f"  ** {msg} **")
    return routes, set(tiles)


def enforce_gold_provenance(gm: GmControl, body, serial: int) -> None:
    """Delete every coin in the character's pack, so all later gold is EARNED.

    A fresh ServUO account arrives with ~1000 gold; a carpenter once banked it and the
    readout reported 940 "earned" — an impressive headline that was false. Run this
    BEFORE granting any seed."""
    st = [body.observe() for _ in range(3)][-1]
    pack = next((i.serial for i in st.items
                 if i.layer == BACKPACK_LAYER and i.container == serial), None)
    for i in st.items:
        if i.container == pack and i.graphic == GOLD_GRAPHIC:
            gm.command_on("[Delete", i.serial)


# --- telemetry -----------------------------------------------------------------------

def telemetry_line(life, profession: str, obs: Observation | None) -> str:
    """`want=<intent> admitted=<goal actually on the stack> ready=<gate verdicts>`.

    `want` alone is a trap: it is INTENT, and an unadmitted goal looks identical to a
    busy one from outside — that ambiguity cost three runs and one wrong root cause.
    """
    try:
        ready = ready_capability_ids(
            profession, SkillContext(obs=obs, persona=life.persona,
                                     memory=life.econ_agent.memory),
        ) if obs is not None else ()
        cur = life.econ_agent.goal_stack.current
        admitted = cur.goal.params.get("capability") if cur else None
    except Exception:  # noqa: BLE001 — telemetry must never break the run
        ready, admitted = ("?",), "?"
    return f"want={life.target_cap} admitted={admitted} ready={list(ready)}"


def hp_readout(obs: Observation | None) -> str:
    """A frozen agent looks the same as a busy one; a dead one explains everything."""
    if obs is None:
        return "?"
    p = obs.player
    return "DEAD" if p.dead else f"{p.hits}/{p.hits_max}"


# --- the harness ---------------------------------------------------------------------

@dataclass
class Staged:
    """What a spec's staging step hands back to the harness."""

    routes: dict
    #: Where this life LIVES — the harness leashes BOTH agent memories to it. The leash
    #: went to one memory of two once, and the profession that runs its economy agent
    #: nearly every tick wandered out of pickup range of its own supply.
    home: tuple[int, int]
    leash: int | None = None
    #: Extra keys for the hunt/work agent's memory (e.g. `harvest_nodes`).
    memory: dict = field(default_factory=dict)
    #: Extra keys for the economy agent's memory (e.g. `craft_spot`).
    econ_memory: dict = field(default_factory=dict)
    #: Gold granted AFTER provenance (a seed the profession genuinely needs to start,
    #: e.g. a carpenter's first board batch — it cannot make its own material). 0 = none.
    seed_gold: int = 0
    banner: str = ""


@dataclass
class LifeSpec:
    """Everything profession-specific about running one Life. No defaults on the
    load-bearing fields: forgetting one is a TypeError at construction, not a lesson
    re-learned against the live shard."""

    profession: str                       #: capability-registry key AND worker job label
    persona_name: str
    account_prefix: str
    life_factory: Callable[..., Any]      #: (body, persona, routes) -> Life
    #: (gm, serial, body) -> Staged. Runs inside the GM context; use `stage_shops` and
    #: the module helpers rather than re-implementing them.
    stage: Callable[[GmControl, int, Any], Staged]
    #: Optional extra status fields: (life, obs) -> str appended to the standard line.
    status_extra: Callable[[Any, Observation | None], str] | None = None


class LifeRunner:
    """Owns the run sequence every solo Life shares. See the module docstring."""

    def __init__(self, spec: LifeSpec, *, host: str = "127.0.0.1", port: int = 2594,
                 ticks: int = 600, monitor: bool = False) -> None:
        self.spec = spec
        self.host, self.port, self.ticks, self.monitor = host, port, ticks, monitor

    def run(self, worker: Callable) -> None:
        """`worker` is the village's `_run_worker` (injected to avoid an import cycle —
        village imports this module)."""
        spec = self.spec
        print(f"raising a {spec.profession} at {self.host}:{self.port}")
        acct = f"{spec.account_prefix}{fresh_suffix()}"
        seat = MONITOR_PORT_BASE if self.monitor else None
        try:
            body = ResilientIpcBody.spawn(self.host, self.port, acct, acct,
                                          pump_ms=400, monitor_port=seat)
        except Exception as e:  # noqa: BLE001
            print(f"  {acct}: login failed ({e})")
            return
        watch = f"  watch: http://127.0.0.1:{seat}/" if seat else ""
        print(f"  {acct}: {spec.persona_name} the {spec.profession}{watch}")

        login_throttle(GM_RELOGIN_COOLDOWN_S)
        serial = body.ready["player"]["serial"]
        with GmControl.spawn(self.host, self.port) as gm:
            gm.hide()
            staged = spec.stage(gm, serial, body)
            # Provenance ALWAYS, and always before any seed: measured income must be
            # earned income. This is the harness's guarantee, not the spec's memory.
            enforce_gold_provenance(gm, body, serial)
            if staged.seed_gold:
                gm.command_on(f"[AddToPack Gold {staged.seed_gold}", serial)

        life = spec.life_factory(body, Persona(name=spec.persona_name), staged.routes)
        life.memory.update(staged.memory)
        life.econ_agent.memory.update(staged.econ_memory)
        life.set_leash(staged.home, staged.leash)
        seed = f" and {staged.seed_gold}g seed" if staged.seed_gold else ", broke"
        print(f"staged: {spec.persona_name}@{staged.home}{seed}"
              + (f"  {staged.banner}" if staged.banner else "") + "\n")

        status: dict[int, str] = {}
        lock = threading.Lock()
        started = time.monotonic()
        budget = self.ticks * getattr(life, "tick_budget_scale", 1)
        t = threading.Thread(target=worker,
                             args=(life, budget, 0, status, lock, spec.profession),
                             daemon=True)
        t.start()
        while t.is_alive():
            time.sleep(4.0)
            obs = getattr(life.body, "last_obs", None)
            extra = f" {spec.status_extra(life, obs)}" if spec.status_extra else ""
            with lock:
                snap = [status[i] for i in sorted(status)]
            # Net earned per hour: (pack + banked) - seed, over wall time. Provenance
            # deleted the starter gold, so this is genuinely EARNED — and a negative
            # rate is the audit's economic finding made visible live: both deployed
            # craft loops destroy value at vendor prices, and a society scaled on a
            # bleeding loop decays to bankruptcy without a line like this saying so.
            earned = pack_amount(obs, GOLD_GRAPHIC) + banked_amount(obs) - staged.seed_gold
            hours = max(1e-9, (time.monotonic() - started) / 3600.0)
            print(f"— {spec.profession} [{life.mode}] "
                  f"{telemetry_line(life, spec.profession, obs)}{extra} "
                  f"hp={hp_readout(obs)} gold={pack_amount(obs, GOLD_GRAPHIC)} "
                  f"banked={banked_amount(obs)} "
                  f"net={earned:+d}g ({earned / hours:+.0f}g/h) —")
            for line in snap:
                print(f"  {line}")
        t.join(timeout=5)
        print(f"\nthe {spec.profession}'s day is done")
