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
from .obsview import banked_amount, pack_amount
from .obsview import owned_tool_readout, pack_serial  # noqa: F401 — re-exported, see below
from .persona import Persona
from .skills.base import SkillContext
from .skills.harvest import BACKPACK_LAYER
from .skills.hunt import GOLD_GRAPHIC
from .skills.market import SELL_REACH, _bank_reserve
from .warrior_life import WarriorLife

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


# --- observation readbacks — now in `obsview.py`, re-exported here -------------------
#
# This section was headed "the ~15 copy-pasted `_pack` helpers, once". The count was
# right; the "once" was not. These four were canonical only for the RUNNERS — the five
# Life modules went on keeping twenty hand-written copies of the same readbacks, and
# drifted anyway: a distance clause that was never back-ported, a missing-backpack guard
# that was never forward-ported. `obsview.py` is the one definition now; see its module
# docstring for what each drift cost live.
#
# `pack_serial`, `pack_amount`, `banked_amount` and `owned_tool_readout` are RE-EXPORTED
# from the import above (hence its `noqa: F401` — `pack_serial` and `owned_tool_readout`
# are no longer used inside this module, only through it), because `village.py`,
# `live_tinker_bank_gate.py`, `live_urgent_bank_gate.py` and `tests/test_life_runner.py`
# all import them from HERE. Moving the definitions must not move anybody's imports.


# --- staging (verified, never assumed) -----------------------------------------------

class StagingError(RuntimeError):
    """A shop failed to stage, resolved to an already-placed NPC, or landed on a tile
    another shop occupies. Raised (not printed) by strict staging: a solo Life with a
    missing or aliased shop is a broken run, and half-verified staging is the same trap as
    unverified staging in better clothes."""


def stage_shops(gm: GmControl, *, z: int, anchor: tuple[int, int],
                spots: dict[str, tuple[str, tuple[int, int]]],
                exclude, strict: bool = True,
                placed: dict[int, str] | None = None,
                serials_out: dict | None = None) -> tuple[dict, set]:
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

    `serials_out`, when given, is filled with `{(x, y): serial}` for every shop actually
    staged — the SHOP-IDENTITY PIN. Runners hand it to the agent as the `shop_serials`
    memory key, and the market skills' own resolver prefers it over its
    nearest-to-requested-spot guess. That guess is a coin flip exactly where it matters
    most: a vendor and a banker one tile either side of a requested spot are EQUIDISTANT
    from it, and the live urgent-band gate caught the tinker asking the Tinker NPC to
    open a bank box (two runs out of three; the third guessed right). Identity, not
    distance — this project's oldest lesson, applied one layer deeper than staging.
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
        if serials_out is not None:
            serials_out[(mob.pos.x, mob.pos.y)] = mob.serial
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
    life_factory: Callable[..., Any]      #: (body, persona, routes, **knobs) -> Life
    #: (gm, serial, body) -> Staged. Runs inside the GM context; use `stage_shops` and
    #: the module helpers rather than re-implementing them.
    stage: Callable[[GmControl, int, Any], Staged]
    #: Optional extra status fields: (life, obs) -> str appended to the standard line.
    status_extra: Callable[[Any, Observation | None], str] | None = None
    #: Tuning knobs forwarded to `life_factory` as keyword arguments — the harness's
    #: only channel from a caller (a genome, a bandit, a sweep script) into a Life's
    #: thresholds. It has a default because it is genuinely optional, NOT because it is
    #: decoration: audit proposal 5 gave the Lives knob PARAMETERS while the spec's
    #: factory type stayed `(body, persona, routes)`, so a spec could not express a
    #: threshold at all. That gap made CLAUDE.md's deferral precondition (a), "the
    #: genome's axes can steer a full Life", false in practice, and the Phase-7
    #: evolution-vs-random rerun waits on exactly that. Filled by the two shipped
    #: runners' own `knobs=` argument (`village.run_carpenter_life` /
    #: `run_woodsman_life`) — the ENTRY POINT, added because a channel wireless at the
    #: caller's end is a channel only the tests can reach, which is the same "asserted
    #: but not wired" shape `build_life` below exists to prevent. Every key here must be
    #: a knob the Life routes through `anima2/knobs.py` — a raw threshold tuned from out
    #: here is a new drift avenue, not an axis (see that module's docstring for what one
    #: already cost). ENFORCED by `__post_init__` against `knob_names`; that sentence
    #: was a comment and nothing else until a reviewer walked a non-knob through it.
    knobs: dict[str, Any] = field(default_factory=dict)
    #: The allowlist `knobs` is checked against — the KNOBS set of the class this spec's
    #: factory actually builds (`CarpenterLife.KNOBS`, `TinkerLife.KNOBS`, ...). Defaults
    #: to the base `WarriorLife.KNOBS`, which is the right set for four of the five Lives
    #: and FAIL-SAFE for the fifth: a tinker spec that forgets to declare it rejects
    #: `bank_trip_surplus` loudly, offline, at construction — the wrong direction to fail
    #: in, but the harmless one. The factory is a lambda closing over its Life class, so
    #: the spec cannot introspect the set; it has to be told.
    knob_names: frozenset[str] = WarriorLife.KNOBS

    def __post_init__(self) -> None:
        """Reject a `knobs` key that is not a knob, at SPEC construction.

        Two separate failures, one check. The first is silent: `build_life` splats
        `knobs` into the Life constructor, whose other parameters are identity rather
        than thresholds, so `knobs={"profession": "mage"}` on a carpenter spec built a
        Life that staged, labelled and reported itself as a carpenter (`spec.profession`
        drives all three, including the `ready=` list in `telemetry_line`) while its goal
        policy, capability cognition and disagreement detector ran as a mage, with the
        carpenter's `decide` rule still choosing what to want. A permanent want-vs-refuse
        standoff that the operator's own status line contradicts — the exact rule-vs-gate
        class `obsview.py` and `knobs.py` were written to end, re-entered through the
        tuning channel. See `WarriorLife.KNOBS` for why `profession` is the likely key
        and not a contrived one.

        The second is merely loud, but expensively placed: a genuine TYPO
        (`disagreement_tick`) does raise `TypeError` — from `build_life`, which `run()`
        reaches only AFTER the login, the GM staging, the provenance gold-wipe and the
        seed grant. Nobody has spent a shard slot on that yet; the point is that the
        first person to would get a spawned, logged-in, seeded character abandoned behind
        an unhandled traceback for a one-character mistake. `knobs` is fully known here,
        before the first packet, so this is where it is checked.
        """
        unknown = set(self.knobs) - set(self.knob_names)
        if unknown:
            raise ValueError(
                f"{self.profession} spec: {sorted(unknown)} is not a tuning knob. "
                f"Knobs for this Life: {sorted(self.knob_names)}. Every key must be a "
                "threshold the Life reads back through anima2/knobs.py's clamp; a "
                "constructor parameter that is identity (profession) or cognition "
                "(steering) is not one, and tuning it here recreates the rule-vs-gate "
                "drift this channel exists to avoid.")


class LifeRunner:
    """Owns the run sequence every solo Life shares. See the module docstring."""

    def __init__(self, spec: LifeSpec, *, host: str = "127.0.0.1", port: int = 2594,
                 ticks: int = 600, monitor: bool = False,
                 persist_insights: bool = False) -> None:
        self.spec = spec
        self.host, self.port, self.ticks, self.monitor = host, port, ticks, monitor
        self.persist_insights = persist_insights

    def build_life(self, body, routes: dict):
        """Construct the Life the way `run()` does — the spec's factory plus its KNOBS.

        A named seam, not a wrapper for its own sake: `run()` needs a live shard (a
        spawned body, a GM context, a worker thread), so the knob channel could only
        ever be proved by re-implementing this one line in a test, which is exactly how
        a channel ends up asserted-but-not-wired. Offline tests call THIS, and the live
        path calls it too, so "the tuned value reaches the constructed Life" is one
        fact rather than two that must be kept in step.

        The splat is safe only because `LifeSpec.__post_init__` already checked every
        key against the Life's own `KNOBS` allowlist — see it for what rode this line
        unvalidated before, and why the check belongs at spec construction and not here.
        """
        spec = self.spec
        return spec.life_factory(body, Persona(name=spec.persona_name), routes,
                                 **spec.knobs)

    def staged_line(self, life, staged: Staged) -> str:
        """The one line a live operator reads before the day starts: where this Life
        stands, what it was seeded with, and the reserve it will actually keep.

        A named seam for the same reason `build_life` is one. The reserve is read off
        the BUILT Life through `market._bank_reserve` — the single read point the decide
        rule, the `bank_gold` gate and `BankGold`'s own FSM share — and it MUST be read
        here rather than composed by the spec: `run()` calls `spec.stage()` BEFORE
        `build_life()`, so a `Staged.banner` is structurally incapable of seeing a tuned
        value. `run_carpenter_life` baked `carpenter_life.BANK_RESERVE` into its banner
        exactly that way; accurate only while nothing tuned that runner, and it would
        have started printing a false number to a live operator the moment the runner
        grew its `knobs` argument. Call it AFTER `econ_memory` lands — a spec's own
        `econ_memory` may carry `bank_reserve`, and that write is the last one.
        """
        seed = f" and {staged.seed_gold}g seed" if staged.seed_gold else ", broke"
        reserve = _bank_reserve(life.econ_agent.memory)
        return (f"staged: {self.spec.persona_name}@{staged.home}{seed}"
                f"  (reserve {reserve})"
                + (f"  {staged.banner}" if staged.banner else ""))

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

        life = self.build_life(body, staged.routes)
        self._wire_persistence(life)
        life.memory.update(staged.memory)
        life.econ_agent.memory.update(staged.econ_memory)
        life.set_leash(staged.home, staged.leash)
        # AFTER `econ_memory` — see `staged_line` for why that ordering is the only
        # correct read point for the reserve.
        print(self.staged_line(life, staged) + "\n")

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


    def _wire_persistence(self, life) -> None:
        """Phase-6 persistence for a Life (audit #8): the hunt agent — the one that
        does the living — reflects through the tiered LLM, its insights persist to
        `data/insights.jsonl` under the persona's key, and a prior session's insights
        are LOADED at construction so live hours finally compound instead of being
        discarded at exit. Mirrors `run_village`'s own live-verified wiring; degrades
        honestly (a note, not a crash) when no LLM provider builds."""
        if not self.persist_insights:
            return
        try:
            from pathlib import Path

            from .cognition import (
                LLMCognition,
                LLMReflection,
                ReflectingCognition,
                ThreadedCognition,
            )
            from .llm import build_tiered_clients, with_role
            from .memory import load_insights

            clients = build_tiered_clients()
            insights = load_insights(Path("data") / "insights.jsonl",
                                     self.spec.persona_name)
            prior = insights.recent(1)
            if prior:
                print(f"  resumed insight: \"{prior[-1].text[:70]}\"")
            # Role-tagged clients (health-check follow-up #2): chatter and reflection
            # both ride the cheap tier under the degraded provider, and untagged they
            # are indistinguishable in the ledger — which is exactly how a
            # heuristic-authored insight passed for an LLM one until forensics
            # compared the TEXT against the fallback template.
            life.hunt_agent.cognition = ThreadedCognition(ReflectingCognition(
                LLMCognition(clients["cheap"], job=self.spec.profession),
                LLMReflection(with_role(clients["cheap"], "reflection")),
                insights=insights,
            ))
            print(f"  persistence: insights load+persist for {self.spec.persona_name}")
        except Exception as e:  # noqa: BLE001 — persistence must never break the life
            print(f"  persistence requested but not wired ({e}); running without")
