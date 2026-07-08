"""Live proof: hunt/loot (DESIGN.md §10 Phase 3 item 3), plus PHASE4.md item 3's
skill-library live gate (`--skill-library`).

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

`--skill-library` (PHASE4.md item 3, opt-in — the default run above is
byte-for-byte unchanged without it) additionally:

 - wires a `SkillLibrary` into the hunter's `Agent` (`skill_library=`,
   `profession="hunter"`) so every terminal/rewarded `Hunt` outcome is
   ledgered to `data/skill_ledger.jsonl` (or `--ledger-path`), then, after
   the run, spawns a **second, freshly started Python process**
   (`_cross_process_readback`) that reads that file from disk — never this
   process's own in-memory `SkillLibrary` — and reports `stats("hunt",
   "hunter")` back, cross-checked against this process's own episodic count;
 - runs a **second** hunter alongside the first: hunter A is staged the
   existing way (`Hunt()` constructed directly); hunter B's skill is
   whatever `SkillLibrary.retrieve("hunt weak creatures")` hands back,
   instantiated with **no literal `Hunt(...)` call anywhere in that
   construction path** — an explicit `isinstance(retrieved_skill, Hunt)`
   assertion in this script is what actually proves it's the same class, not
   just "something that also loots." Both hunters are provenance-safe
   (starting gold deleted) and expected to reach the same order of magnitude
   of loot cycles — proving retrieval-then-instantiate behaves identically
   to hand-wiring, not merely that persistence works;
 - reads back each hunter's gold via a new `GmControl.get_property("Gold",
   ...)` helper and logs it against the ledger's own summed reward —
   **advisory only** (PHASE4.md item 3's measurement-independence caveat:
   the ledger's reward is the agent's own computed value, never a hard
   pass/fail gate).

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_hunt [--ticks N] [--mongbats N] [--min-cycles N]
       [--skill-library] [--ledger-path PATH]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .agent import Agent
from .cliloc import resolve_entry
from .contract import Observation
from .control import GmControl
from .ipc_body import IpcBody
from .persona import Persona
from .planner import Planner
from .profession import HUNTING_SPOT, PROFESSIONS
from .skill_library import SkillLibrary
from .skills.base import Skill
from .skills.harvest import BACKPACK_LAYER
from .skills.hunt import GOLD_GRAPHIC, LOOT_GRAPHICS, Hunt

# How many independently-confirmed kill->corpse->loot cycles the proof
# requires before calling the loop demonstrated — mirrors `live_market.py`'s
# `MIN_SELL_TRIPS`: a single successful cycle can't distinguish "this works
# reliably" from "got lucky once".
MIN_LOOT_CYCLES = 2
# How far around a hunter's own stand tile counts as "its hunting pocket" for
# the GM wipe and the mongbat spawn spread — small enough to stay well clear
# of any other calibrated spot on the map, generous enough to fit several
# mongbats without stacking them on the exact same tile.
POCKET_RADIUS = 8
# Hunter B's stand tile, for the `--skill-library` differential-parity leg —
# offset from `HUNTING_SPOT` (not the exact same tile) within the pocket's
# own documented open extent ("2-4 real tiles in every one of the 8
# directions before anything blocks" — `profession.py`'s own comment) so the
# two hunters don't contend for the same tile while running one after the
# other in the same session, independent of how quickly hunter A's
# connection actually vacates the world on close.
HUNTER_B_OFFSET: tuple[int, int] = (2, 2)


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


def _cross_process_readback(ledger_path: Path, skill_name: str, profession: str) -> dict | None:
    """Spawn a SECOND, freshly started Python process that imports
    `SkillLibrary` fresh and reads `ledger_path` from disk — never this
    process's own in-memory `SkillLibrary` instance — the decisive proof
    that persistence isn't a no-op (PHASE4.md item 3's own load-bearing
    claim, live rather than just offline). Returns `None` (with the
    subprocess's stderr printed) on any failure — this readback is part of
    the live gate's own evidence, not something the run should crash over.
    """
    script = (
        "import json\n"
        "from anima2.skill_library import SkillLibrary\n"
        f"lib = SkillLibrary(ledger_path={str(ledger_path)!r})\n"
        f"s = lib.stats({skill_name!r}, {profession!r})\n"
        "print(json.dumps({'count': s.count, 'mean_reward': s.mean_reward, "
        "'success_rate': s.success_rate}))\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  cross-process readback FAILED to launch: {e}")
        return None
    if proc.returncode != 0:
        print(f"  cross-process readback FAILED (exit {proc.returncode}): {proc.stderr.strip()}")
        return None
    try:
        return json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        print(f"  cross-process readback produced unparseable output: {proc.stdout!r}")
        return None


def _run_one_hunter(
    args: argparse.Namespace,
    *,
    account: str,
    persona_name: str,
    skill: Skill,
    label: str,
    stand: tuple[int, int],
    skill_library: SkillLibrary | None,
    profession: str,
) -> dict:
    """Stage one hunter at `stand`, run it for up to `args.ticks`, and return
    a result dict. Faithful extraction of the original single-hunter script
    body — used both for the always-on default run and (twice) for the
    `--skill-library` differential-parity leg.
    """
    hx, hy = stand
    print(f"\n=== hunter [{label}] account={account} stand=({hx},{hy}) ===")

    with IpcBody.spawn(args.host, args.port, account, account, pump_ms=400) as hunter_ipc:
        hunter_serial = hunter_ipc.ready["player"]["serial"]
        print(f"hunter: {account} serial={hunter_serial}")

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

        hunter = Agent(
            body=hunter_body, persona=Persona(name=persona_name, combat_disposition="aggressive"),
            planner=Planner([skill]), skill_library=skill_library, profession=profession,
        )

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
                    print(f"  [{label}] tick {t:4d}: LOOT CYCLE {completed_cycles}/{args.min_cycles} complete — "
                          f"corpse {prev_head:#x} yielded {delta} valuables "
                          f"(pack {head_start_valuables} -> {val_now}); running total looted={total_looted}")
                else:
                    print(f"  [{label}] tick {t:4d}: corpse {prev_head:#x} finished with no observed gain "
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
                    print(f"  [{label}] tick {t:4d}: KILLED mongbat {link.killed:#x} -> corpse {link.corpse:#x}")

            queue = hunter.memory.get("hunt_queue", [])
            head = queue[0] if queue else None
            if head != prev_head:
                _note_head_change(t, head, _pack_valuables(obs))

            for j in obs.new_journal:
                text = resolve_entry(j)
                if "gold" in text.lower() or "corpse" in text.lower() or "loot" in text.lower():
                    print(f"  [{label}] tick {t:4d}: [journal] {text}")

            if t % 50 == 0:
                gold = _pack_valuables(obs)
                print(f"  [{label}] tick {t:4d}: SNAPSHOT hunts={len(kills_seen)} looted_cycles={completed_cycles} "
                      f"pack_valuables={gold} hp={obs.player.hits}/{obs.player.hits_max}")

            if completed_cycles >= args.min_cycles:
                print(f"\n[{label}] {args.min_cycles} loot cycles demonstrated by tick {t} — stopping early.")
                break

        # A loot run still in progress when --ticks runs out has an
        # unresolved head — don't silently drop it from the evidence log.
        if prev_head is not None:
            _note_head_change(args.ticks, None, _pack_valuables(hunter_body.observe()))

        result = {
            "account": account,
            "label": label,
            "kills": len(kills_seen),
            "cycles": completed_cycles,
            "total_looted": total_looted,
            "reward": hunter.episodes.total_reward(),
            "episodes_recorded": hunter.episodes.total_recorded,
            "serial": hunter_serial,
        }

    # Advisory GM gold readback (PHASE4.md item 3) — a *separate*, short GM
    # connection after the hunter's own has closed, so it never contends
    # with the hunter's own IPC traffic. Never fails the run either way.
    if skill_library is not None:
        try:
            with GmControl.spawn(args.host, args.port) as gm2:
                gm2.hide()
                result["gm_gold_readback"] = gm2.get_property("Gold", result["serial"])
        except Exception as e:  # noqa: BLE001 — advisory only, never fatal
            result["gm_gold_readback"] = f"<error: {e}>"

    engaged = result["kills"] > 0
    looted = result["cycles"] >= args.min_cycles
    print(f"\n--- result [{label}] ---")
    print(f"mongbats killed:              {result['kills']}")
    print(f"loot cycles (corpse-tied):    {result['cycles']} (need >= {args.min_cycles}) -> {looted}")
    print(f"total valuables looted:       {result['total_looted']}")
    print(f"episodic reward:              {result['reward']:.1f}")
    print(f"episodes recorded:            {result['episodes_recorded']}")
    if "gm_gold_readback" in result:
        print(f"GM gold readback (advisory):  {result['gm_gold_readback']!r}")
    if engaged and looted:
        print(f"[{label}] HUNT/LOOT CONFIRMED: engage -> kill -> corpse -> open -> loot, live "
              f"({result['cycles']} cycles, all corpse-tied).")
    else:
        print(f"[{label}] CHAIN INCOMPLETE within --ticks — see the timeline above.")
    return result


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
    ap.add_argument("--hunter2-account", default="animahunt2",
                    help="the retrieval-built hunter B's account (--skill-library only)")
    ap.add_argument("--skill-library", action="store_true",
                    help="opt-in (PHASE4.md item 3): wire a SkillLibrary into the hunter's Agent, "
                         "record outcomes to the ledger, run a second hunter built via "
                         "SkillLibrary.retrieve() instead of Hunt() directly, and cross-check "
                         "the ledger from a second freshly started Python process")
    ap.add_argument("--ledger-path", default=None,
                    help="override data/skill_ledger.jsonl (mainly for isolated test runs)")
    args = ap.parse_args()

    hx, hy = HUNTING_SPOT
    print(f"hunter at {HUNTING_SPOT}")

    skill_library = None
    ledger_path = None
    if args.skill_library:
        ledger_path = Path(args.ledger_path) if args.ledger_path else Path("data") / "skill_ledger.jsonl"
        skill_library = SkillLibrary(ledger_path=ledger_path)
        print(f"skill-library: ON — ledger at {ledger_path.resolve()}")

    result_a = _run_one_hunter(
        args, account=args.hunter_account, persona_name="Ragnar", skill=Hunt(),
        label="A hand-wired (Hunt() direct)", stand=(hx, hy),
        skill_library=skill_library, profession="hunter",
    )

    if not args.skill_library:
        return  # default path: byte-for-byte the original single-hunter script

    # --- differential parity leg: hunter B, retrieval-built --------------------
    # `SkillLibrary` instantiated fresh here (not reusing `skill_library`
    # above) purely so `retrieve()` is demonstrably independent of any
    # in-process outcome state — it's a static, read-only registry lookup,
    # unaffected by what's been recorded to the ledger so far.
    retrieved = SkillLibrary().retrieve("hunt weak creatures", k=1)
    assert retrieved, "SkillLibrary.retrieve('hunt weak creatures') returned nothing"
    retrieved_cls = retrieved[0].skill_cls
    # No literal `Hunt(...)` anywhere in this construction path — `skill_cls`
    # is whatever the registry handed back; the isinstance check below is
    # what actually proves it's the same class, not this line.
    hunter_b_skill = retrieved_cls()
    print(f"\nretrieval: SkillLibrary.retrieve('hunt weak creatures')[0] = "
         f"'{retrieved[0].name}' ({retrieved_cls.__name__})")
    assert isinstance(hunter_b_skill, Hunt), (
        f"retrieved skill {retrieved_cls!r} is not a Hunt — retrieval-vs-hand-wiring parity broken"
    )
    print("isinstance(retrieved_skill, Hunt) = True")

    bx, by = hx + HUNTER_B_OFFSET[0], hy + HUNTER_B_OFFSET[1]
    result_b = _run_one_hunter(
        args, account=args.hunter2_account, persona_name="Ragnar-B", skill=hunter_b_skill,
        label="B retrieved (SkillLibrary.retrieve)", stand=(bx, by),
        skill_library=skill_library, profession="hunter",
    )

    # --- differential parity check ----------------------------------------------
    print("\n=== differential parity (hand-wired vs retrieved) ===")
    print(f"A hand-wired: cycles={result_a['cycles']} looted={result_a['total_looted']} "
         f"reward={result_a['reward']:.1f}")
    print(f"B retrieved:  cycles={result_b['cycles']} looted={result_b['total_looted']} "
         f"reward={result_b['reward']:.1f}")
    both_reached_min = result_a["cycles"] >= args.min_cycles and result_b["cycles"] >= args.min_cycles
    print(f"both hunters completed >= {args.min_cycles} loot cycles: {both_reached_min}")

    # --- cross-process ledger readback -------------------------------------------
    print("\n=== cross-process ledger readback ===")
    combined_episodes = result_a["episodes_recorded"] + result_b["episodes_recorded"]
    combined_reward = result_a["reward"] + result_b["reward"]
    print(f"in-process (this script's own Agent objects): hunter A episodes={result_a['episodes_recorded']} "
         f"hunter B episodes={result_b['episodes_recorded']} combined={combined_episodes} "
         f"combined_reward={combined_reward:.1f}")
    readback = _cross_process_readback(ledger_path, "hunt", "hunter")
    print(f"cross-process (fresh `{sys.executable} -c ...`, reading {ledger_path.resolve()} from disk): {readback}")
    if readback is not None:
        count_match = readback["count"] == combined_episodes
        print(f"ledger count == combined in-process episodic count: {readback['count']} == "
             f"{combined_episodes} -> {count_match}")
        print(f"ledger mean_reward*count vs combined_reward (order of magnitude): "
             f"{readback['mean_reward'] * readback['count']:.1f} vs {combined_reward:.1f}")

    # --- advisory GM gold readback ------------------------------------------------
    print("\n=== advisory: GM gold readback (never a hard pass/fail — see module docstring) ===")
    for res in (result_a, result_b):
        print(f"hunter [{res['label']}] ({res['account']}): GM [Get Gold -> "
             f"{res.get('gm_gold_readback')!r}  vs  ledger-summed episodic reward={res['reward']:.1f}")


if __name__ == "__main__":
    main()
