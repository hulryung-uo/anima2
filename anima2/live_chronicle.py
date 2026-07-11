"""Live verification gate: the village chronicle (PHASE6.md item 2).

Reuses `live_trade.py`'s exact staged miner+blacksmith trade scenario (fresh
accounts, area wiped) — but as a **standalone driver** with its own
round-robin tick loop, mirroring `live_persistent_lives.py`'s own "standalone
driver, not a `village.py` CLI wrapper" precedent (PHASE6.md item 1's own "as
landed" note): `village.py`'s roster login hardcodes `anima{i}` account
names with no override, and this gate needs fresh accounts per session/leg
(`live_common.py::fresh_suffix`). The REAL `village.py`/`chronicle.py` code
is exercised directly — this script's tick loop calls `village.
_chronicle_events_this_tick` (the exact per-profession detector dispatcher
`village.py::_run_worker` calls) and the real `chronicle.ChronicleLedger.
queue_event`/`flush`, never a reimplementation of either. `_run_worker`'s own
threading is already exercised live by every other `--llm-tiers`/
`--curriculum` gate; this gate's value-add is proving the DETECTOR + LEDGER
logic against a real live shard.

**The decisive evidence leg — provenance, not self-agreement.** This gate
also builds a wholly INDEPENDENT oracle, hand-written in this script (never
calling `chronicle.py`/`village.py`'s own detector functions), that watches
the same `miner.memory["smelt_phase"]` transition and correlates it against
`miner.episodes` by hand. This turns out to be *necessary*, not decorative:
`MineSmeltDeliver.name == "mine_smelt_deliver"` labels EVERY reward-bearing
episode this skill ever records — mining-phase skill-gain episodes
(`skills/harvest.py::Harvest.step()`) and smelting-phase ingot-gain episodes
(`skills/smelt.py::MineAndSmelt._smelt_step`) included, not only delivery
confirmations (verified by reading both directly) — so "count every
reward>0 `mine_smelt_deliver`-named episode" alone would badly OVER-count
relative to `delivered_ingots` chronicle events. The oracle disambiguates
the same way the real detector does: only an episode recorded on the exact
tick `smelt_phase` transitions `"deliver"` -> `"return"` counts as a
delivery. See PHASE6.md item 2's "As landed" note for why this is a
clarification of the spec's own phrasing, not a divergence from it.

Every session opens with a GM wipe of the trade spot's own debris (mirrors
`live_trade.py`'s own `WIPE_RADIUS` bounding-box wipe — `[Add`/ground drops
are additive on a persistent shard).

**Retry isolation (live-caught, second gate run).** An independent run of
this gate found a real accounting bug in the gate script itself (never in
the shipped code): when leg A session 0's first attempt staged, confirmed
one real delivery, then stalled, its already-queued-but-unflushed chronicle
events sat in a ChronicleLedger object that the retry then REUSED for its
own (successful) attempt — both attempts' real, confirmed events got
flushed together into one file on success, while the independent oracle
(reset fresh per attempt, by design — it has no business remembering a
previous attempt's own history) only reflected the winning attempt. Ledger
count/sum (3 events, 28.0) vs. oracle (2 events, 16.0) — a real mismatch,
but entirely a gate-methodology bug: the ledger itself faithfully recorded
every real, confirmed delivery from both attempts, exactly as designed.
Fixed by giving every attempt its OWN ledger file (`_attempt_ledger_path`,
`..._a0.jsonl` -> `..._a0_r0.jsonl`, `..._a0_r1.jsonl`, ...) — only the
winning attempt's own file is read back for the decisive comparison, and a
stalled attempt's file still exists afterward for forensic review.

**Bank-drain resilience.** The same run showed leg B (solo miner) wedge on
all `MAX_WEDGE_ATTEMPTS` with ZERO episodes each, immediately after leg A's
two sessions had just mined ~45 ingots' worth of ore from the exact same
spot (`TRADE_MINE_SPOT`) — a resource-bank exhaustion pattern (banks
respawn over real minutes, far longer than this gate's own retry cooldown),
not the pre-8ead6eb intermittent-freeze bug the original `STALL_TICKS`
comment named (that one's root cause — resource-bank exhaustion + a
pack-full edge case Harvest.step() never checked for — was already fixed by
a windowed stuck-rate + WalkTo-relocation hardening pass; see the
`anima2-harvest-freeze` memory note). Leg A/leg C are structurally pinned to
`TRADE_MINE_SPOT` (the one live-calibrated spot with a co-located,
route-calibrated smithy — `profession.py`'s own extensive comments on why
no other `MINING_SPOTS` entry has one) and can't rotate away from it. Leg B
needs no blacksmith at all, so it draws from `_SOLO_MINE_SPOT_POOL` — the
OTHER `MINING_SPOTS` entries, never `TRADE_MINE_SPOT` — cycling to a
different spot on every retry attempt too, so it never competes with leg
A/C for the same bank and never re-hammers a spot it just drained itself.

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_chronicle [--sessions N] [--ticks N] [--deliver-threshold N]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from .agent import Agent
from .chronicle import ChronicleLedger
from .control import GmControl
from .ipc_body import IpcBody
from .live_common import (
    GM_RELOGIN_COOLDOWN_S,
    RecordingBody,
    fresh_suffix,
    login_throttle,
    print_gate_verdict,
    wipe_bounds,
)
from .persona import Persona
from .planner import Planner
from .profession import MINING_SPOTS, TRADE_MINE_SPOT, TRADE_SMITH_SPOT
from .skills import Blacksmith
from .skills.smelt import MineSmeltDeliver
from .village import _accumulate_deliver_reward, _chronicle_events_this_tick, _pack_ingot_count

#: PHASE3.md item 1's own "every ingot traces back to a specific delivery"
#: standard, reused here as this item's own "multi-cycle" bar (the item's
#: Scope's own words: "run long enough for at least 2 confirmed deliveries").
MIN_DELIVERIES = 2
#: Mirrors `live_trade.py`'s own trade-spot bounding-box wipe radius.
WIPE_RADIUS = 10
#: A session with zero recorded miner episodes is a live wedge — retried
#: with a fresh account, bounded, mirroring `live_trade.py::_run_tuner`'s
#: identical guard. Historically this was the pre-8ead6eb intermittent
#: Mine/Harvest freeze; that root cause (resource-bank exhaustion + a
#: pack-full edge case) is fixed. What's left, expected here specifically,
#: is plain resource-bank exhaustion from THIS gate's own back-to-back
#: sessions hammering the same calibrated spot faster than it can
#: respawn (real minutes) — see the module docstring's "Bank-drain
#: resilience" section for the live evidence and the actual fix
#: (`_SOLO_MINE_SPOT_POOL` rotation for leg B; leg A/C can't rotate, being
#: pinned to the one calibrated trade-smithy spot).
MAX_WEDGE_ATTEMPTS = 3
#: A live-caught refinement of the same guard: a wedge (of either cause
#: above) can also strike PARTWAY through a session (a few early episodes
#: record fine, then nothing — `miner_episodes_recorded > 0`, so the
#: zero-episode check alone wouldn't catch it, and waiting out the full
#: `session_ticks` budget to find out is expensive). If NO new miner episode
#: lands for this many consecutive ticks, the session gives up early and the
#: retry wrapper treats it exactly like a zero-episode wedge.
STALL_TICKS = 250
#: Solo-miner (leg B) spot pool — every `MINING_SPOTS` entry EXCEPT
#: `TRADE_MINE_SPOT` (`MINING_SPOTS[1]`, reserved for leg A/C's trade
#: pairing) — mirrors `foundry/eval.py`'s own `spot_pool=` rotation
#: precedent (`live_evolve_gate.py`/CLAUDE.md: "a spot_pool= rotation across
#: MINING_SPOTS[0..3] so back-to-back mining seeds don't share one thinning
#: HarvestBank"), applied here per retry attempt instead of per eval seed.
_SOLO_MINE_SPOT_POOL: tuple[tuple[int, int], ...] = tuple(s for s in MINING_SPOTS if s != TRADE_MINE_SPOT)


def _attempt_ledger_path(base: Path | None, attempt: int) -> Path | None:
    """Give retry `attempt` its own ledger file — `None` stays `None`
    (chronicle disabled entirely). See the module docstring's "Retry
    isolation" section for why this exists: a stalled attempt's real,
    confirmed events must never share a file (and therefore a flush batch)
    with a later, successful attempt's own events, or a cross-process
    readback of "the" ledger silently mixes two different attempts' worth of
    deliveries — exactly the bug this gate's own second run caught.
    """
    if base is None:
        return None
    return base.with_name(f"{base.stem}_r{attempt}{base.suffix}")


def _run_trade_session(
    args: argparse.Namespace, *, deliver_threshold: int, miner_account: str, smith_account: str,
    miner_persona: str, smith_persona: str, session_ticks: int,
    chronicle_path: Path | None, paired: bool, label: str,
    mine_spot: tuple[int, int] = TRADE_MINE_SPOT,
) -> dict:
    """One staged session — mirrors `live_trade.py::_run_session`'s own
    staging exactly (miner + forge; blacksmith + forge/anvil + a
    deliberately thin ingot stock so it needs delivered ingots), driven by
    THIS script's own round-robin tick loop (see the module docstring for
    why, not `village.py`'s threaded `_run_worker`). `paired=False` stages a
    solo miner (no blacksmith, no `smithy_drop`) — the differential leg.
    `chronicle_path` is THIS attempt's own ledger file (already resolved by
    the caller via `_attempt_ledger_path` — retry isolation, see the module
    docstring); this function owns the whole lifecycle of its
    `ChronicleLedger` (construct, queue, flush) rather than receiving one
    built elsewhere, so a stalled attempt's events can never bleed into a
    later attempt's file. `mine_spot` defaults to `TRADE_MINE_SPOT` (leg
    A/C, pinned to the one calibrated trade-smithy location); leg B (solo)
    passes a rotating spot from `_SOLO_MINE_SPOT_POOL` instead.
    """
    mx, my = mine_spot
    chronicle_ledger = ChronicleLedger(ledger_path=chronicle_path) if chronicle_path is not None else None
    print(f"\n=== {label}: miner={miner_account} paired={paired} mine_spot=({mx},{my}) "
          f"chronicle={'ON' if chronicle_ledger else 'OFF'} ===")

    with IpcBody.spawn(args.host, args.port, miner_account, miner_account, pump_ms=400) as miner_ipc:
        miner_serial = miner_ipc.ready["player"]["serial"]
        login_throttle(args.stagger)

        smith_ipc = IpcBody.spawn(args.host, args.port, smith_account, smith_account, pump_ms=400) \
            if paired else None
        try:
            smith_serial = smith_ipc.ready["player"]["serial"] if smith_ipc is not None else None

            with GmControl.spawn(args.host, args.port) as gm:
                gm.hide()
                if paired:
                    sx, sy = TRADE_SMITH_SPOT
                    x1, y1 = min(mx, sx) - WIPE_RADIUS, min(my, sy) - WIPE_RADIUS
                    x2, y2 = max(mx, sx) + WIPE_RADIUS, max(my, sy) + WIPE_RADIUS
                else:
                    x1, y1, x2, y2 = mx - WIPE_RADIUS, my - WIPE_RADIUS, mx + WIPE_RADIUS, my + WIPE_RADIUS
                wipe_bounds(gm, x1, y1, x2, y2)

                mgx, mgy, mgz = gm.stage(miner_serial, mx, my, skills={"Mining": 35},
                                         items=["Pickaxe", "Pickaxe"])
                gm.command_at("[Add Forge", mgx + 1, mgy + 1, mgz)
                gm.command_on(f'[Set Name "{miner_persona}"', miner_serial)
                print(f"GM staged miner at ({mgx},{mgy},{mgz}) + forge")

                if paired:
                    sgx, sgy, sgz = gm.stage(smith_serial, sx, sy, skills={"Blacksmith": 35},
                                             items=["SmithHammer 999", f"IronIngot {args.smith_ingots}"])
                    gm.command_at("[Add Forge", sgx, sgy - 1, sgz)
                    gm.command_at("[Add Anvil", sgx, sgy + 1, sgz)
                    gm.command_on(f'[Set Name "{smith_persona}"', smith_serial)
                    print(f"GM staged blacksmith at ({sgx},{sgy},{sgz}) + forge/anvil, "
                          f"{args.smith_ingots} starting ingots")

            miner_body = RecordingBody(miner_ipc)
            miner_body.observe()
            miner_body.observe()

            miner_skill = MineSmeltDeliver()
            miner_skill.deliver_threshold = deliver_threshold
            miner = Agent(body=miner_body, persona=Persona(name=miner_persona), planner=Planner([miner_skill]))
            if paired:
                miner.memory["smithy_drop"] = TRADE_SMITH_SPOT

            smith = None
            smith_body = None
            if paired:
                smith_body = RecordingBody(smith_ipc)
                smith_body.observe()
                smith_body.observe()
                smith = Agent(body=smith_body, persona=Persona(name=smith_persona), planner=Planner([Blacksmith()]))

            # --- the independent oracle (see the module docstring) ---------
            #
            # `oracle_deliver_reward_accum` mirrors `village.py::_run_worker`'s
            # own `deliver_phase_reward` running total — hand-written here,
            # never calling `_accumulate_deliver_reward`/`_delivered_ingots`
            # — because a real multi-pile delivery pays its confirmed reward
            # across SEVERAL ticks (one per confirmed pile-drop), not as one
            # lump sum on the exact tick `smelt_phase` flips to `"return"`
            # (live-caught by this very gate's first run — see PHASE6.md
            # item 2's "As landed" note). Checking only the transition
            # tick's own episode silently misses real deliveries.
            oracle_deliveries: list[tuple[int, float]] = []
            oracle_deliver_reward_accum = 0.0
            # `real_deliver_phase_reward` feeds the REAL `_chronicle_events_
            # this_tick`/`_delivered_ingots` call below via the REAL
            # `_accumulate_deliver_reward` — the actual shipped accumulation
            # logic, not a stand-in for it.
            real_deliver_phase_reward = 0.0
            prev_miner_memory: dict = dict(miner.memory)
            prev_miner_recorded = miner.episodes.total_recorded
            prev_smith_memory: dict = dict(smith.memory) if smith is not None else {}
            prev_smith_recorded = smith.episodes.total_recorded if smith is not None else 0
            fetch_entry_ingots: int | None = None
            picked_up_events = 0
            last_progress_tick = 0
            stalled = False

            t = 0
            for t in range(session_ticks):
                miner.tick()
                mobs = miner_body.last_obs
                assert mobs is not None
                new_miner_episode = None
                if miner.episodes.total_recorded > prev_miner_recorded:
                    new_miner_episode = miner.episodes.recent(1)[0]
                    last_progress_tick = t

                # The independent oracle's own accumulation (hand-written —
                # see above).
                if (prev_miner_memory.get("smelt_phase") == "deliver" and new_miner_episode is not None
                        and new_miner_episode.summary.startswith("mine_smelt_deliver")
                        and new_miner_episode.reward > 0):
                    oracle_deliver_reward_accum += new_miner_episode.reward
                if (prev_miner_memory.get("smelt_phase") == "deliver"
                        and miner.memory.get("smelt_phase") == "return"
                        and oracle_deliver_reward_accum > 0):
                    oracle_deliveries.append((t, oracle_deliver_reward_accum))
                    print(f"  {label} tick {t:4d}: [oracle] CONFIRMED delivery — "
                          f"reward={oracle_deliver_reward_accum:.1f} "
                          f"(deliveries so far: {len(oracle_deliveries)})")
                    oracle_deliver_reward_accum = 0.0
                elif miner.memory.get("smelt_phase") != "deliver":
                    oracle_deliver_reward_accum = 0.0

                # The REAL chronicle path — the exact functions village.py's
                # own _run_worker calls, and the exact ChronicleLedger.
                real_deliver_phase_reward = _accumulate_deliver_reward(
                    real_deliver_phase_reward, prev_miner_memory, new_miner_episode,
                )
                if chronicle_ledger is not None:
                    for kind, to_persona, amount in _chronicle_events_this_tick(
                        "miner", smith_persona if paired else None,
                        prev_miner_memory, miner.memory, new_miner_episode,
                        fetch_entry_ingots=None, pack_ingots_now=0,
                        deliver_phase_reward=real_deliver_phase_reward,
                    ):
                        chronicle_ledger.queue_event(tick=miner.ticks, from_persona=miner_persona,
                                                     to_persona=to_persona, kind=kind, amount=amount)
                if miner.memory.get("smelt_phase") != "deliver":
                    real_deliver_phase_reward = 0.0
                prev_miner_memory = dict(miner.memory)
                prev_miner_recorded = miner.episodes.total_recorded

                if smith is not None:
                    smith.tick()
                    sobs = smith_body.last_obs
                    assert sobs is not None
                    new_smith_episode = None
                    if smith.episodes.total_recorded > prev_smith_recorded:
                        new_smith_episode = smith.episodes.recent(1)[0]
                    pack_ingots_now = _pack_ingot_count(sobs)
                    if prev_smith_memory.get("bs_state") != "fetch" and smith.memory.get("bs_state") == "fetch":
                        fetch_entry_ingots = pack_ingots_now  # baseline captured once, at fetch entry
                    if chronicle_ledger is not None:
                        for kind, to_persona, amount in _chronicle_events_this_tick(
                            "blacksmith", miner_persona,
                            prev_smith_memory, smith.memory, new_smith_episode,
                            fetch_entry_ingots=fetch_entry_ingots, pack_ingots_now=pack_ingots_now,
                        ):
                            if kind == "picked_up_ingots":
                                picked_up_events += 1
                                print(f"  {label} tick {t:4d}: [chronicle] picked_up_ingots amount={amount:.1f}")
                            chronicle_ledger.queue_event(tick=smith.ticks, from_persona=smith_persona,
                                                         to_persona=to_persona, kind=kind, amount=amount)
                    if smith.memory.get("bs_state") != "fetch":
                        fetch_entry_ingots = None
                    prev_smith_memory = dict(smith.memory)
                    prev_smith_recorded = smith.episodes.total_recorded

                if t % 100 == 0:
                    print(f"  {label} tick {t:4d}: SNAPSHOT miner phase="
                          f"{miner.memory.get('smelt_phase', 'mine'):8s} "
                          f"pack_ingots={_pack_ingot_count(mobs):3d} oracle_deliveries={len(oracle_deliveries)}")

                if len(oracle_deliveries) >= MIN_DELIVERIES:
                    print(f"\n{label}: reached {MIN_DELIVERIES} confirmed deliveries by tick {t} — stopping.")
                    break

                if t - last_progress_tick > STALL_TICKS:
                    stalled = True
                    print(f"\n{label}: no new miner episode for {STALL_TICKS} ticks (last progress at tick "
                          f"{last_progress_tick}) — likely resource-bank exhaustion at ({mx},{my}) (see "
                          f"MAX_WEDGE_ATTEMPTS's own docstring — the pre-8ead6eb freeze this used to name "
                          f"is already fixed) — giving up this attempt rather than running out the full "
                          f"{session_ticks}-tick budget.")
                    break

            # Flush THIS attempt's own queue to its OWN file now, whether it
            # stalled or succeeded — retry isolation (see the module
            # docstring): a later attempt must never share a ledger object
            # (and therefore a flush batch) with this one.
            n_flushed = chronicle_ledger.flush() if chronicle_ledger is not None else 0
            if chronicle_ledger is not None:
                print(f"  {label}: flushed {n_flushed} chronicle event(s) to {chronicle_path}")

            return {
                "label": label,
                "miner_persona": miner_persona,
                "smith_persona": smith_persona if paired else None,
                "oracle_deliveries": oracle_deliveries,
                "picked_up_events": picked_up_events,
                "ticks_run": t + 1,
                "miner_episodes_recorded": miner.episodes.total_recorded,
                "stalled": stalled,
                "chronicle_ledger_path": chronicle_path,
                "chronicle_flushed": n_flushed,
            }
        finally:
            if smith_ipc is not None:
                smith_ipc.close()


def _run_trade_session_with_retry(
    args: argparse.Namespace, *, suffix_tag: str,
    mine_spots: Sequence[tuple[int, int]] | None = None, **kwargs,
) -> dict:
    """Retries `_run_trade_session` (fresh account suffix each attempt,
    bounded) on a zero-episode/stalled "live wedge" outcome — mirrors
    `live_trade.py::_run_tuner`'s identical guard — **and** on a raised
    connection/login exception (a `_run_trade_session` that never even got
    staged is exactly as retryable as one that staged and then wedged;
    letting the exception propagate would crash the whole gate over one
    throttled login instead of just losing this attempt). `GM_RELOGIN_
    COOLDOWN_S` between attempts: each attempt opens and closes its own
    `GmControl.spawn(...)` connection on the shared `hulryung` GM account,
    which `live_common.py`'s own module docstring documents as leaving a
    stale server-side session for a while after logout — reconnecting too
    soon is a real, previously-hit failure mode, not a hypothetical one.

    `kwargs` carries `chronicle_path` (the BASE path, or `None` — this
    function resolves it to a per-ATTEMPT path via `_attempt_ledger_path`,
    the retry-isolation fix) and `mine_spot`-less callers get `TRADE_MINE_
    SPOT` from `_run_trade_session`'s own default; `mine_spots`, if given, is
    a pool this function cycles through per attempt instead (leg B's
    bank-drain resilience — see the module docstring).
    """
    base_miner_account = kwargs.pop("miner_account")
    base_smith_account = kwargs.pop("smith_account")
    base_chronicle_path: Path | None = kwargs.pop("chronicle_path")
    result = None
    for attempt in range(MAX_WEDGE_ATTEMPTS):
        if attempt > 0:
            print(f"[{suffix_tag}] cooling down {GM_RELOGIN_COOLDOWN_S}s before retry "
                  f"(stale GM session + login throttle)...")
            time.sleep(GM_RELOGIN_COOLDOWN_S)
        attempt_path = _attempt_ledger_path(base_chronicle_path, attempt)
        session_kwargs = dict(kwargs)
        if mine_spots:
            session_kwargs["mine_spot"] = mine_spots[attempt % len(mine_spots)]
        try:
            result = _run_trade_session(
                args, miner_account=f"{base_miner_account}r{attempt}",
                smith_account=f"{base_smith_account}r{attempt}",
                chronicle_path=attempt_path, **session_kwargs,
            )
        except Exception as e:  # noqa: BLE001 — a throttled login/connection drop must not crash the whole gate
            more_left = attempt + 1 < MAX_WEDGE_ATTEMPTS
            print(f"[{suffix_tag}] attempt {attempt + 1} raised {type(e).__name__}: {e} — "
                  + ("retrying with a fresh account" if more_left else
                     f"all {MAX_WEDGE_ATTEMPTS} attempts failed"))
            if not more_left:
                raise
            continue
        if result["miner_episodes_recorded"] > 0 and not result.get("stalled"):
            return result
        more_left = attempt + 1 < MAX_WEDGE_ATTEMPTS
        reason = "stalled mid-session (no progress for STALL_TICKS)" if result.get("stalled") else \
            f"produced ZERO miner episodes over {result['ticks_run']} ticks"
        print(f"[{suffix_tag}] attempt {attempt + 1} {reason} — a live wedge, not a real signal — "
              + ("retrying with a fresh account" if more_left else
                 f"all {MAX_WEDGE_ATTEMPTS} attempts wedged"))
    return result


def _cross_process_chronicle_readback(ledger_path: Path, miner_persona: str, smith_persona: str) -> dict | None:
    """Spawn a SECOND, freshly started Python process that reads
    `ledger_path` from disk via a fresh `ChronicleLedger` — never this
    process's own in-memory instance — mirrors `live_trade.py::
    _cross_process_readback`'s identical discipline for item 4's own ledger.
    """
    script = (
        "import json\n"
        "from anima2.chronicle import ChronicleLedger\n"
        f"ledger = ChronicleLedger(ledger_path={str(ledger_path)!r})\n"
        f"events = ledger.between({miner_persona!r}, {smith_persona!r})\n"
        "deliveries = [e for e in events if e.kind == 'delivered_ingots']\n"
        f"all_for_miner = ledger.events_for({miner_persona!r})\n"
        "print(json.dumps({'count': len(deliveries), 'sum_amount': sum(e.amount for e in deliveries), "
        "'total_events_for_miner': len(all_for_miner)}))\n"
    )
    try:
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False)
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--stagger", type=float, default=4.0)
    ap.add_argument("--sessions", type=int, default=2, help="independent paired sessions (multi-cycle proof)")
    ap.add_argument("--ticks", type=int, default=900, help="max ticks per paired session (leg A / leg C)")
    ap.add_argument("--solo-ticks", type=int, default=400, help="ticks for the differential solo-miner leg")
    ap.add_argument("--deliver-threshold", type=int, default=5)
    ap.add_argument("--smith-ingots", type=int, default=15)
    args = ap.parse_args()

    suffix = fresh_suffix()
    flags: dict[str, bool] = {}

    # --- LEG A: paired trade scenario, chronicle ON, N independent sessions ---
    for session_i in range(args.sessions):
        if session_i > 0:
            print(f"\ncooling down {GM_RELOGIN_COOLDOWN_S}s between leg A sessions...")
            time.sleep(GM_RELOGIN_COOLDOWN_S)
        base_ledger_path = Path("data") / f"chronicle_gate_{suffix}_a{session_i}.jsonl"
        miner_persona = f"Grimm-ch{suffix}-{session_i}"
        smith_persona = f"Tormund-ch{suffix}-{session_i}"
        result = _run_trade_session_with_retry(
            args, suffix_tag=f"leg A session {session_i}",
            deliver_threshold=args.deliver_threshold,
            miner_account=f"achA{session_i}{suffix}", smith_account=f"achB{session_i}{suffix}",
            miner_persona=miner_persona, smith_persona=smith_persona,
            session_ticks=args.ticks, chronicle_path=base_ledger_path, paired=True,
            label=f"[leg A session {session_i + 1}/{args.sessions}]",
        )
        # Retry isolation: read back exactly the WINNING attempt's own file
        # (`result["chronicle_ledger_path"]`), never a session-level path
        # that might have been shared across attempts — the bug an
        # independent run of this gate caught (see the module docstring's
        # "Retry isolation" section).
        ledger_path = result["chronicle_ledger_path"]
        oracle = result["oracle_deliveries"]
        oracle_count, oracle_sum = len(oracle), sum(r for _, r in oracle)
        print(f"  session {session_i}: flushed {result['chronicle_flushed']} chronicle event(s) to "
              f"{ledger_path}; oracle deliveries={oracle_count} sum={oracle_sum:.1f} "
              f"picked_up_events={result['picked_up_events']}")

        readback = _cross_process_chronicle_readback(ledger_path, miner_persona, smith_persona)
        print(f"  session {session_i}: cross-process readback (fresh `{sys.executable} -c ...` "
              f"reading {ledger_path.resolve()} from disk): {readback}")

        flags[f"leg_a_session{session_i}_at_least_{MIN_DELIVERIES}_deliveries"] = oracle_count >= MIN_DELIVERIES
        flags[f"leg_a_session{session_i}_chronicle_count_matches_independent_episode_oracle"] = (
            readback is not None and readback["count"] == oracle_count
        )
        flags[f"leg_a_session{session_i}_chronicle_sum_matches_independent_episode_oracle"] = (
            readback is not None and abs(readback["sum_amount"] - oracle_sum) < 1e-6
        )

    # --- LEG B: differential — solo miner, chronicle ON, must record zero ---
    # Bank-drain resilience: draws from _SOLO_MINE_SPOT_POOL (never
    # TRADE_MINE_SPOT, which leg A above just mined heavily) — see the
    # module docstring's "Bank-drain resilience" section.
    print(f"\ncooling down {GM_RELOGIN_COOLDOWN_S}s before leg B...")
    time.sleep(GM_RELOGIN_COOLDOWN_S)
    base_solo_path = Path("data") / f"chronicle_gate_{suffix}_solo.jsonl"
    solo_persona = f"Grimm-ch{suffix}-solo"
    solo_result = _run_trade_session_with_retry(
        args, suffix_tag="leg B solo", mine_spots=_SOLO_MINE_SPOT_POOL,
        deliver_threshold=args.deliver_threshold,
        miner_account=f"achSolo{suffix}", smith_account="unused",
        miner_persona=solo_persona, smith_persona="",
        session_ticks=args.solo_ticks, chronicle_path=base_solo_path, paired=False,
        label="[leg B differential: solo miner]",
    )
    solo_ledger_path = solo_result["chronicle_ledger_path"]
    solo_ledger = ChronicleLedger(ledger_path=solo_ledger_path)
    solo_events = solo_ledger.events_for(solo_persona)
    print(f"  solo miner ({solo_result['miner_episodes_recorded']} episodes recorded): "
          f"chronicle events={len(solo_events)} (expect 0), ledger path {solo_ledger_path}")
    flags["leg_b_solo_miner_had_real_activity"] = solo_result["miner_episodes_recorded"] > 0
    flags["leg_b_solo_miner_zero_chronicle_events"] = len(solo_events) == 0
    # Mirror leg C's rigor: events_for() reads [] both for a missing file and
    # an empty/mismatched one — assert the file was never created at all, so a
    # regression that spuriously creates it (an errant mkdir+open) can't hide.
    flags["leg_b_solo_ledger_path_never_created"] = not solo_ledger_path.exists()
    print(f"  solo ledger path never created: {not solo_ledger_path.exists()}")

    # --- LEG C: inertness — chronicle disabled entirely ------------------------
    # Pinned to TRADE_MINE_SPOT like leg A (the one calibrated trade-smithy
    # spot — can't rotate; see the module docstring). It ran cleanly even
    # right after leg A's own heavy usage in the run that caught leg B's
    # bank-drain bug, so no additional resilience is added here.
    print(f"\ncooling down {GM_RELOGIN_COOLDOWN_S}s before leg C...")
    time.sleep(GM_RELOGIN_COOLDOWN_S)
    base_inertness_path = Path("data") / f"chronicle_gate_{suffix}_inertness.jsonl"
    inert_result = _run_trade_session_with_retry(
        args, suffix_tag="leg C inertness",
        deliver_threshold=args.deliver_threshold,
        miner_account=f"achInA{suffix}", smith_account=f"achInB{suffix}",
        miner_persona=f"Grimm-ch{suffix}-inert", smith_persona=f"Tormund-ch{suffix}-inert",
        session_ticks=args.ticks, chronicle_path=None, paired=True,
        label="[leg C inertness: chronicle OFF]",
    )
    # chronicle_path=None throughout means _run_trade_session never builds a
    # ChronicleLedger at all, for any attempt — nothing could have written to
    # ANY per-attempt variant of this path either; checked directly rather
    # than trusting `inert_result["chronicle_ledger_path"]` (always None
    # here) so the assertion names a concrete path, matching leg B's own
    # style.
    inertness_path = _attempt_ledger_path(base_inertness_path, 0)
    print(f"  inertness leg: {inert_result['miner_episodes_recorded']} miner episodes recorded "
          f"(positive control), oracle deliveries={len(inert_result['oracle_deliveries'])}, "
          f"ledger path {inertness_path} exists={inertness_path.exists()}")
    flags["leg_c_inertness_engine_still_ran"] = inert_result["miner_episodes_recorded"] > 0
    flags["leg_c_inertness_ledger_path_never_created"] = not inertness_path.exists()

    print()
    print_gate_verdict(flags, label="PHASE6_ITEM2_CHRONICLE", detail="village chronicle relationship ledger")


if __name__ == "__main__":
    main()
