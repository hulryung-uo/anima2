"""Live proof: hunt/loot (DESIGN.md §10 Phase 3 item 3).

GM wipes the calibrated hunting pocket (`profession.HUNTING_SPOT` — see that
module's comment for how it was found: real, collision-checked `Walk` probing
of open Britain-plains ground, well away from the trade zone at
(2609-2611, 473-475) so mongbats never wander close enough to aggro the
vendor/banker or cross the trade miner's own delivery walk) of prior debris
(items AND mobiles — a stray corpse or two from an earlier run would pollute
`corpse_of` attribution), stages ONE bare-handed hunter (Wrestling 50,
Tactics 50 — no weapon needed, see `profession.py`'s own comment) on a fresh
account with its starting gold deleted (so every gold piece it ever holds
from here on is provably corpse loot, mirroring `live_market.py`'s same
provenance trick), then `[Add`s several Mongbats nearby — **not** pinned with
`CantWalk`, unlike every vendor/banker this package stages elsewhere: those
are pinned so a *passive* NPC holds still for a scripted route; a Mongbat is
the opposite case — `AI_Melee`/`FightMode.Closest` (`Scripts/Mobiles/Normal/
Mongbat.cs`) already makes it approach and attack on its own once aggroed,
and `CantWalk` would neuter exactly that (a pinned Mongbat spawned a few
tiles off could never close the distance at all). The brain then runs
`skills/hunt.py::Hunt` tick by tick and the script watches for, **in order**:

 1. the hunter engages (WarMode, then Attack) a nearby Mongbat,
 2. it dies (`corpse_of` links a corpse to a serial we attacked) and the
    hunter opens the corpse and loots it — gold **confirmed gained** in the
    pack, attributed to the *specific* corpse it came from (tracking
    `hunt_queue[0]`'s own turnover, not `hunt_phase`'s coarser engage/loot
    edges — a single uninterrupted loot run can drain more than one queued
    corpse, and phase transitions alone would under-count that), and
 3. this repeats: **`MIN_LOOT_CYCLES`** (>= 2) independently-confirmed
    kill → corpse → loot cycles, each tied to an actual pack gold increase
    while that corpse was being processed, not merely a phase edge.

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_hunt [--ticks N] [--mongbats N] [--min-cycles N]
"""

from __future__ import annotations

import argparse

from .agent import Agent
from .cliloc import resolve_entry
from .contract import Observation
from .control import GmControl
from .ipc_body import IpcBody
from .persona import Persona
from .planner import Planner
from .profession import HUNTING_SPOT, PROFESSIONS
from .skills.harvest import BACKPACK_LAYER
from .skills.hunt import GOLD_GRAPHIC, LOOT_GRAPHICS, Hunt

# How many independently-confirmed kill->corpse->loot cycles the proof
# requires before calling the loop demonstrated — mirrors `live_market.py`'s
# `MIN_SELL_TRIPS`: a single successful cycle can't distinguish "this works
# reliably" from "got lucky once".
MIN_LOOT_CYCLES = 2
# How far around HUNTING_SPOT counts as "the hunting pocket" for the GM wipe
# and the mongbat spawn spread — small enough to stay well clear of any other
# calibrated spot on the map, generous enough to fit several mongbats without
# stacking them on the exact same tile.
POCKET_RADIUS = 8


def _backpack_serial(obs: Observation) -> int | None:
    bp = next((i for i in obs.items if i.layer == BACKPACK_LAYER and i.container == obs.player.serial), None)
    return bp.serial if bp is not None else None


def _pack_valuables(obs: Observation) -> int:
    """Total whitelisted loot (gold + the verified-but-unexercised gem
    graphics — see `skills/hunt.py`) currently held in the pack. Mongbats
    only ever drop gold in practice, but this mirrors `Hunt._pack_valuables`
    exactly rather than hard-coding gold alone, so the proof would still be
    correct if the whitelist is ever widened.
    """
    bp = _backpack_serial(obs)
    if bp is None:
        return 0
    return sum(i.amount for i in obs.items if i.graphic in LOOT_GRAPHICS and i.container == bp)


class _RecordingBody:
    """Wraps a `Body`, caching the last `Observation` (see `live_trade.py`/`live_market.py`)."""

    def __init__(self, inner: IpcBody) -> None:
        self._inner = inner
        self.last_obs: Observation | None = None

    def observe(self) -> Observation:
        self.last_obs = self._inner.observe()
        return self.last_obs

    def act(self, action) -> None:
        self._inner.act(action)

    @property
    def connected(self) -> bool:
        return self._inner.connected


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=1500)
    ap.add_argument("--mongbats", type=int, default=6, help="how many to [Add near the hunter")
    ap.add_argument("--min-cycles", type=int, default=MIN_LOOT_CYCLES)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    # Fresh account each run (see live_trade.py's note: `[AddToPack`/`[Add` are
    # additive on a persistent shard, so reusing a name quietly piles debris).
    ap.add_argument("--hunter-account", default="animahunt")
    args = ap.parse_args()

    hx, hy = HUNTING_SPOT
    print(f"hunter at {HUNTING_SPOT}")

    with IpcBody.spawn(args.host, args.port, args.hunter_account, args.hunter_account, pump_ms=400) as hunter_ipc:
        hunter_serial = hunter_ipc.ready["player"]["serial"]
        print(f"hunter: {args.hunter_account} serial={hunter_serial}")

        with GmControl.spawn(args.host, args.port) as gm:
            gm.hide()
            # Wipe stray debris (mongbat corpses, wandering mongbats) from
            # earlier runs — `[Add` is additive and a leftover corpse would
            # pollute `corpse_of` attribution (a corpse we never actually
            # killed) — mirrors live_market.py/live_trade.py's own wipe step.
            gm.command_area("[WipeNPCs", hx - POCKET_RADIUS, hy - POCKET_RADIUS,
                            hx + POCKET_RADIUS, hy + POCKET_RADIUS, 30)
            gm.command_area("[WipeItems", hx - POCKET_RADIUS, hy - POCKET_RADIUS,
                            hx + POCKET_RADIUS, hy + POCKET_RADIUS, 30)

            prof = PROFESSIONS["hunter"]
            hgx, hgy, hgz = gm.stage(hunter_serial, hx, hy, skills=prof.skills, items=prof.items)
            print(f"GM staged hunter at ({hgx},{hgy},{hgz}): {prof.skills}, bare-handed (Wrestling only)")

            # Delete the fresh character's starting gold (CharacterCreation.cs
            # grants it automatically) so every gold piece the hunter ever
            # holds from here on is provably corpse loot, not starting
            # capital — the same trick `live_market.py` uses for the smith.
            hunter_obs = hunter_ipc.observe()
            starting_bp = _backpack_serial(hunter_obs)
            starting_gold = [i for i in hunter_obs.items if i.graphic == GOLD_GRAPHIC and i.container == starting_bp]
            for g in starting_gold:
                gm.command_on("[Delete", g.serial)
            if starting_gold:
                total = sum(g.amount for g in starting_gold)
                print(f"GM deleted the hunter's starting gold ({total}) — "
                      f"every gold piece from here on is provably corpse loot")

            # Spawn mongbats a couple of tiles out in a ring around the
            # hunter — not pinned (see the module docstring: a Mongbat must
            # be free to close the distance and attack on its own). Placed
            # within Combat's own `engage_range` (10) so the hunter notices
            # them immediately rather than waiting for a wander-in.
            ring = [(2, 0), (-2, 0), (0, 2), (0, -2), (2, 2), (-2, -2), (2, -2), (-2, 2)]
            spawned = 0
            for i in range(args.mongbats):
                dx, dy = ring[i % len(ring)]
                if gm.command_at("[Add Mongbat", hgx + dx, hgy + dy, hgz):
                    spawned += 1
            print(f"GM spawned {spawned}/{args.mongbats} mongbats around the hunter (unpinned — they aggro in)")

        hunter_body = _RecordingBody(hunter_ipc)
        # Let the teleport + pack grant + mongbat spawns settle.
        hunter_body.observe()
        hunter_body.observe()

        skill = Hunt()
        hunter = Agent(body=hunter_body, persona=Persona(name="Ragnar", combat_disposition="aggressive"),
                       planner=Planner([skill]))

        # Evidence tracking — attribute each pack gold increase to the
        # *specific* corpse being processed when it happened
        # (`hunt_queue[0]`'s own turnover), not to `hunt_phase` edges: a
        # single uninterrupted loot run can drain more than one queued
        # corpse (two mongbats dying close together), and phase transitions
        # alone would under-count that (see the module docstring).
        prev_head: int | None = None
        head_start_valuables = 0
        completed_cycles = 0
        kills_seen: set[int] = set()
        total_looted = 0

        def _note_head_change(t: int, new_head: int | None, val_now: int) -> None:
            nonlocal prev_head, head_start_valuables, completed_cycles, total_looted
            if prev_head is not None:
                delta = val_now - head_start_valuables
                if delta > 0:
                    completed_cycles += 1
                    total_looted += delta
                    print(f"  tick {t:4d}: LOOT CYCLE {completed_cycles}/{args.min_cycles} complete — "
                          f"corpse {prev_head:#x} yielded {delta} valuables "
                          f"(pack {head_start_valuables} -> {val_now}); running total looted={total_looted}")
                else:
                    print(f"  tick {t:4d}: corpse {prev_head:#x} finished with no observed gain "
                          f"(empty, or gave up)")
            if new_head is not None:
                head_start_valuables = val_now
            prev_head = new_head

        for t in range(args.ticks):
            hunter.tick()
            assert hunter_body.last_obs is not None
            obs = hunter_body.last_obs

            for link in obs.corpse_of:
                if link.killed in hunter.memory.get("hunt_attacked", ()) and link.killed not in kills_seen:
                    kills_seen.add(link.killed)
                    print(f"  tick {t:4d}: KILLED mongbat {link.killed:#x} -> corpse {link.corpse:#x}")

            queue = hunter.memory.get("hunt_queue", [])
            head = queue[0] if queue else None
            if head != prev_head:
                _note_head_change(t, head, _pack_valuables(obs))

            for j in obs.new_journal:
                text = resolve_entry(j)
                if "gold" in text.lower() or "corpse" in text.lower() or "loot" in text.lower():
                    print(f"  tick {t:4d}: [journal] {text}")

            if t % 50 == 0:
                gold = _pack_valuables(obs)
                print(f"  tick {t:4d}: SNAPSHOT hunts={len(kills_seen)} looted_cycles={completed_cycles} "
                      f"pack_valuables={gold} hp={obs.player.hits}/{obs.player.hits_max}")

            if completed_cycles >= args.min_cycles:
                print(f"\n{args.min_cycles} loot cycles demonstrated by tick {t} — stopping early.")
                break

        # A loot run still in progress when --ticks runs out has an
        # unresolved head — don't silently drop it from the evidence log.
        if prev_head is not None:
            _note_head_change(args.ticks, None, _pack_valuables(hunter_body.observe()))

        engaged = len(kills_seen) > 0
        looted = completed_cycles >= args.min_cycles
        print("\n--- result ---")
        print(f"mongbats killed:              {len(kills_seen)}")
        print(f"loot cycles (corpse-tied):    {completed_cycles} (need >= {args.min_cycles}) -> {looted}")
        print(f"total valuables looted:       {total_looted}")
        print(f"episodic reward:              {hunter.episodes.total_reward():.1f}")
        if engaged and looted:
            print(f"\nHUNT/LOOT CONFIRMED: engage -> kill -> corpse -> open -> loot, live "
                  f"({completed_cycles} cycles, all corpse-tied).")
        else:
            print("\nCHAIN INCOMPLETE within --ticks — see the timeline above.")


if __name__ == "__main__":
    main()
