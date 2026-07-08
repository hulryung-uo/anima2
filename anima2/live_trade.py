"""Live end-to-end trade-loop proof: two agents, one economy (DESIGN.md §10 Phase 3).

GM stages a miner (pickaxes, Mining 35, a forge at its ore bank) and a
blacksmith (tongs/hammer, forge+anvil, Blacksmith 35, deliberately only a
handful of ingots) co-located at the live-calibrated trade spot
(`profession.TRADE_MINE_SPOT`/`TRADE_SMITH_SPOT` — see that module for how it
was found). Both agents' fast loops then run tick-by-tick, round-robin (no
threads — keeps the printed evidence in a single, readable timeline), and the
script watches for, **in order**:

 1. the blacksmith crafts down to its deliberately-thin starting stock and
    stalls (no more Blacksmithing gain / a "not enough metal" cliloc),
 2. the miner mines, smelts, accumulates ingots past `deliver_threshold`, walks
    to the smithy, and `Drop`s them on the ground there,
 3. the blacksmith notices the ground pile and `PickUp`s it into its pack,
 4. the blacksmith **crafts again** — a Blacksmithing skill-base gain (or a
    successful MAKE cycle) *after* the pickup, proving the delivered metal is
    what unstuck it, not a GM top-up. No GM gifting sustains the loop past the
    initial stage: everything the smith crafts *after* the first stall came
    from the miner.

`--tuner --sessions N` (PHASE4.md item 4) drives this exact staged scenario N
times in one invocation, each session picking `deliver_threshold` via
`skill_tuning.ParamTuner.choose()` instead of a fixed CLI value, and recording
`(chosen, session_mean_reward)` through `SkillLibrary.record_outcome` — the
same ledger item 3 established. Every session (the default single run, both
control-pair legs, and every `--tuner` iteration) opens with a GM wipe of the
trade spot's own debris (`[WipeItems`/`[WipeNPCs`) — `[Add`/ground drops are
additive on a persistent shard (see the `anima2-live-verification` memory
note), and running this scenario many times in a row without one would let
stray ingots/extra forges from a prior session pollute the next.

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_trade [--ticks N] [--deliver-threshold N] [--smith-ingots N]
       python -m anima2.live_trade --tuner --sessions 6 [--tuner-ticks N]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from .agent import Agent
from .cliloc import resolve_entry
from .contract import Observation
from .control import GmControl
from .ipc_body import IpcBody
from .persona import Persona
from .planner import Planner
from .profession import TRADE_MINE_SPOT, TRADE_SMITH_SPOT
from .skill_library import SkillLibrary
from .skill_tuning import DELIVER_THRESHOLD_CANDIDATES, ParamSpec, ParamTuner, session_mean_reward
from .skills import Blacksmith
from .skills.base import Status
from .skills.craft import MIN_INGOTS, NOT_ENOUGH_METAL_CLILOC
from .skills.smelt import INGOT_GRAPHICS, MineSmeltDeliver

MINING_SKILL_ID = 45
BLACKSMITHING_SKILL_ID = 7

#: How far around the trade spot counts as "its own debris" for the GM wipe
#: every session opens with — generous enough to catch a delivered pile that
#: bounced a tile or two off the exact drop point, small enough to stay well
#: clear of any other calibrated spot on the map (mirrors `live_hunt.py`'s
#: own `POCKET_RADIUS`).
WIPE_RADIUS = 10


class _RecordingBody:
    """Wraps a `Body`, caching the last `Observation` (see `live_smelt.py`) so the
    driver loop below can inspect it without paying for a second `observe()` pump
    on top of the one `Agent.tick()` already does — with *two* agents ticking
    every loop iteration, that doubling matters for wall-clock time."""

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


def _backpack_serial(obs: Observation) -> int | None:
    bp = next((i for i in obs.items if i.layer == 0x15 and i.container == obs.player.serial), None)
    return bp.serial if bp is not None else None


def _pack_ingots(obs: Observation) -> int:
    bp = _backpack_serial(obs)
    if bp is None:
        return 0
    return sum(i.amount for i in obs.items if i.graphic in INGOT_GRAPHICS and i.container == bp)


def _ground_ingots_near(obs: Observation, radius: int = 8) -> int:
    return sum(i.amount for i in obs.items
              if i.graphic in INGOT_GRAPHICS and i.container is None and i.distance <= radius)


def _skill_base(obs: Observation, skill_id: int) -> float | None:
    s = next((s for s in obs.skills if s.id == skill_id), None)
    return s.base if s is not None else None


def _run_session(
    args: argparse.Namespace,
    *,
    deliver_threshold: int,
    miner_account: str,
    smith_account: str,
    session_ticks: int,
    label: str = "",
    stop_on_full_loop: bool = True,
) -> dict:
    """One full staged trade-loop run: GM-wipe the trade spot's own debris,
    stage a fresh miner + blacksmith pair there, set `deliver_threshold`, and
    tick both agents for `session_ticks` ticks. Returns the evidence every
    caller needs (`miner_reward`, `session_mean_reward`, and the four
    milestone flags) as a dict — faithful extraction of the original
    single-run script body (mirrors `live_hunt.py::_run_one_hunter`'s own
    refactor, for the same reason: PHASE4.md item 4's `--tuner` mode needs to
    run the identical scenario multiple times in one invocation, and the
    positive/negative control-pair legs need it runnable twice with
    different accounts/thresholds without duplicating ~150 lines of staging +
    evidence-tracking code).

    `stop_on_full_loop` (default `True`, matching the script's original,
    still-default single-run behavior) breaks out of the tick loop the
    moment the smith is confirmed crafting again from delivered metal —
    great for a standalone demo, **fatal for comparing candidates**: a
    lower `deliver_threshold` reaches that milestone sooner and so gets a
    *shorter* session, making `miner_reward`/`session_mean_reward` across
    different thresholds incomparable (live-caught: PHASE4.md item 4's first
    live-gate attempt recorded `session_mean_reward` — a per-EPISODE mean —
    over sessions that ran 51 to 450 ticks depending on which threshold got
    picked, and it neither concentrated nor agreed with the control pair).
    Every PHASE4.md item 4 caller below (`_run_tuner`, and `main()` when
    `--no-early-stop` is passed for the control-pair legs) sets this `False`
    so every session runs the identical, fixed `session_ticks` window — the
    one change that makes `miner_reward` (raw total reward over that fixed
    window, not a mean) directly comparable across candidates.
    """
    mx, my = TRADE_MINE_SPOT
    sx, sy = TRADE_SMITH_SPOT

    print(f"\n=== session {label} deliver_threshold={deliver_threshold} "
          f"miner={miner_account} smith={smith_account} (dist={max(abs(mx - sx), abs(my - sy))} tiles) ===")

    with IpcBody.spawn(args.host, args.port, miner_account, miner_account, pump_ms=400) as miner_ipc:
        miner_serial = miner_ipc.ready["player"]["serial"]
        print(f"miner: {miner_account} serial={miner_serial}")
        time.sleep(args.stagger)  # dodge the ServUO login throttle

        with IpcBody.spawn(args.host, args.port, smith_account, smith_account,
                           pump_ms=400) as smith_ipc:
            smith_serial = smith_ipc.ready["player"]["serial"]
            print(f"blacksmith: {smith_account} serial={smith_serial}")

            with GmControl.spawn(args.host, args.port) as gm:
                gm.hide()
                # Wipe stray debris (dropped ingots, wandering NPCs) left by a
                # prior run at this same calibrated spot — see the module
                # docstring: `[Add`/ground drops are additive on a persistent
                # shard, and this scenario runs many times in a row under
                # `--tuner`.
                x1, y1 = min(mx, sx) - WIPE_RADIUS, min(my, sy) - WIPE_RADIUS
                x2, y2 = max(mx, sx) + WIPE_RADIUS, max(my, sy) + WIPE_RADIUS
                gm.command_area("[WipeItems", x1, y1, x2, y2, 20)
                gm.command_area("[WipeNPCs", x1, y1, x2, y2, 20)

                mgx, mgy, mgz = gm.stage(miner_serial, mx, my, skills={"Mining": 35},
                                        items=["Pickaxe", "Pickaxe"])
                gm.command_at("[Add Forge", mgx + 1, mgy + 1, mgz)
                print(f"GM staged miner at ({mgx},{mgy},{mgz}) + forge")

                sgx, sgy, sgz = gm.stage(smith_serial, sx, sy, skills={"Blacksmith": 35},
                                        items=["SmithHammer 999", f"IronIngot {args.smith_ingots}"])
                # North/south of the stand spot, not east/west — matches
                # profession.py's blacksmith `structures`: an anvil is a
                # solid, blocking static, and east/west is the miner's
                # approach corridor (live-observed to seal it off entirely).
                gm.command_at("[Add Forge", sgx, sgy - 1, sgz)
                gm.command_at("[Add Anvil", sgx, sgy + 1, sgz)
                print(f"GM staged blacksmith at ({sgx},{sgy},{sgz}) + forge/anvil, "
                      f"{args.smith_ingots} starting ingots")

            miner_body = _RecordingBody(miner_ipc)
            smith_body = _RecordingBody(smith_ipc)

            # Let the teleports + pack grants + structure spawns settle.
            miner_body.observe()
            miner_body.observe()
            smith_body.observe()
            smith_body.observe()

            miner_skill = MineSmeltDeliver()
            miner_skill.deliver_threshold = deliver_threshold
            miner = Agent(body=miner_body, persona=Persona(name="Grimm"), planner=Planner([miner_skill]))
            miner.memory["smithy_drop"] = TRADE_SMITH_SPOT  # the only wiring the deliver phase needs

            smith = Agent(body=smith_body, persona=Persona(name="Tormund"), planner=Planner([Blacksmith()]))

            # Evidence tracking — flip each flag the first time its condition is
            # observed, printing the moment it happens so the transcript reads as
            # a timeline, not just periodic snapshots.
            smith_stalled = False
            miner_delivered = False
            smith_picked_up = False
            smith_recrafted = False
            last_miner_phase = "mine"
            deliver_entry_ingots: int | None = None  # pack count observed on entering "deliver"
            prev_smith_base: float | None = None
            prev_smith_ingots: int | None = None
            post_pickup_ingots: int | None = None  # peak pack count right after a pickup

            t = 0
            for t in range(session_ticks):
                miner.tick()
                assert miner_body.last_obs is not None
                mobs = miner_body.last_obs
                m_phase = miner.memory.get("smelt_phase", "mine")
                if m_phase != last_miner_phase:
                    print(f"  {label} tick {t:4d}: [miner] phase {last_miner_phase} -> {m_phase} "
                          f"(pack ingots={_pack_ingots(mobs)})")
                    if last_miner_phase != "deliver" and m_phase == "deliver":
                        deliver_entry_ingots = _pack_ingots(mobs)
                    if last_miner_phase == "deliver" and m_phase == "return":
                        # A wedged delivery leg also transitions deliver -> return
                        # (`MineSmeltDeliver._walk_toward` gives up rather than
                        # retrying forever) with nothing actually dropped — only
                        # flag DELIVERED if the pack really lost ingots over the
                        # leg, so this evidence line can't lie about a stalled trip.
                        dropped = (deliver_entry_ingots or 0) - _pack_ingots(mobs)
                        if dropped > 0:
                            miner_delivered = True
                            print(f"  {label} tick {t:4d}: [miner] DELIVERED — dropped {dropped} "
                                  f"ingots at the smithy")
                        else:
                            print(f"  {label} tick {t:4d}: [miner] deliver leg gave up wedged — "
                                  f"nothing dropped")
                last_miner_phase = m_phase
                for j in mobs.new_journal:
                    text = resolve_entry(j)
                    if j.cliloc in (501988, 501987, 501986) or "smelt" in text.lower():
                        print(f"  {label} tick {t:4d}: [miner journal] {text}")

                smith.tick()
                assert smith_body.last_obs is not None
                sobs = smith_body.last_obs
                s_base = _skill_base(sobs, BLACKSMITHING_SKILL_ID)
                s_ingots = _pack_ingots(sobs)
                ground = _ground_ingots_near(sobs)

                for j in sobs.new_journal:
                    text = resolve_entry(j)
                    if j.cliloc == NOT_ENOUGH_METAL_CLILOC:
                        if not smith_stalled:
                            print(f"  {label} tick {t:4d}: [smith journal] {text}  <- STALLED (out of metal)")
                        smith_stalled = True
                    elif "metal" in text.lower() or "blacksmith" in text.lower():
                        print(f"  {label} tick {t:4d}: [smith journal] {text}")

                if prev_smith_ingots is not None and s_ingots < MIN_INGOTS and prev_smith_ingots >= MIN_INGOTS:
                    smith_stalled = True
                    print(f"  {label} tick {t:4d}: [smith] pack ingots dropped below {MIN_INGOTS} "
                          f"({prev_smith_ingots} -> {s_ingots}) — about to stall")

                if prev_smith_ingots is not None and s_ingots > prev_smith_ingots:
                    print(f"  {label} tick {t:4d}: [smith] pack ingots {prev_smith_ingots} -> {s_ingots} "
                          f"(picked up delivered ore; {ground} still on the ground nearby)")
                    if smith_stalled:
                        smith_picked_up = True
                        post_pickup_ingots = s_ingots

                # Two independent confirmations that the smith is crafting *from
                # the delivered metal*, not just holding it: a skill-base gain
                # (probabilistic per craft — may not fire on any given attempt)
                # and pack ingots actually being spent back down from the
                # post-pickup peak (deterministic — every successful MAKE costs
                # ingots regardless of the skill-gain roll). Either is proof.
                if prev_smith_base is not None and s_base is not None and s_base > prev_smith_base + 1e-3:
                    if smith_stalled and smith_picked_up and not smith_recrafted:
                        smith_recrafted = True
                        print(f"  {label} tick {t:4d}: [smith] CRAFTED AGAIN from delivered metal "
                              f"(Blacksmithing {prev_smith_base:.1f} -> {s_base:.1f})")
                if (smith_picked_up and not smith_recrafted and post_pickup_ingots is not None
                        and s_ingots < post_pickup_ingots):
                    smith_recrafted = True
                    print(f"  {label} tick {t:4d}: [smith] CONSUMED delivered metal on a new craft "
                          f"(pack ingots {post_pickup_ingots} -> {s_ingots})")

                prev_smith_base, prev_smith_ingots = s_base, s_ingots

                if t % 25 == 0:
                    print(f"  {label} tick {t:4d}: SNAPSHOT miner phase={m_phase:8s} pack_ingots={_pack_ingots(mobs):3d} "
                          f"| smith pack_ingots={s_ingots:3d} ground_ingots={ground:2d} "
                          f"Blacksmithing={s_base if s_base is not None else float('nan'):.1f}")

                if smith_recrafted and stop_on_full_loop:
                    print(f"\n{label} full loop demonstrated by tick {t} — stopping early.")
                    break

            result = {
                "label": label,
                "deliver_threshold": deliver_threshold,
                "miner_account": miner_account,
                "smith_account": smith_account,
                "smith_stalled": smith_stalled,
                "miner_delivered": miner_delivered,
                "smith_picked_up": smith_picked_up,
                "smith_recrafted": smith_recrafted,
                "miner_reward": miner.episodes.total_reward(),
                "smith_reward": smith.episodes.total_reward(),
                "miner_episodes_recorded": miner.episodes.total_recorded,
                "session_mean_reward": session_mean_reward(miner.episodes),
                "ticks_run": t + 1,
            }

            print(f"\n--- result {label} ---")
            print(f"smith stalled (ran low on metal):     {result['smith_stalled']}")
            print(f"miner delivered ingots to the smithy:  {result['miner_delivered']}")
            print(f"smith picked delivered ingots up:      {result['smith_picked_up']}")
            print(f"smith crafted again from that metal:   {result['smith_recrafted']}")
            print(f"miner episodic reward (fixed-window total{'' if not stop_on_full_loop else ', NOT fixed — '
                  'early-stop was on'}): {result['miner_reward']:.1f}  "
                  f"smith episodic reward: {result['smith_reward']:.1f}  "
                  f"miner session_mean_reward (diagnostic only, NOT comparable across candidates — see "
                  f"`_run_session`'s own docstring): {result['session_mean_reward']:.4f} "
                  f"(episodes={result['miner_episodes_recorded']}, ticks={result['ticks_run']})")
            if smith_stalled and miner_delivered and smith_picked_up and smith_recrafted:
                print(f"\nTRADE LOOP CONFIRMED {label}: goods flowed miner -> ground -> blacksmith -> craft.")
            else:
                print(f"\nTRADE LOOP INCOMPLETE {label} within --ticks — see the timeline above.")

            return result


def _cross_process_readback(ledger_path: Path, spec: ParamSpec) -> dict | None:
    """Spawn a SECOND, freshly started Python process that imports
    `ParamTuner` fresh and reads `ledger_path` from disk via
    `ParamTuner.load_from_ledger` — never this process's own in-memory
    tuner — mirrors `live_hunt.py::_cross_process_readback`'s identical
    discipline for item 3's own ledger. Returns `None` (with the
    subprocess's stderr printed) on any failure.
    """
    script = (
        "import json\n"
        "from anima2.skill_tuning import ParamSpec, ParamTuner\n"
        f"spec = ParamSpec('deliver_threshold', {spec.candidates!r})\n"
        f"tuner = ParamTuner.load_from_ledger({str(ledger_path)!r}, 'mine_smelt_deliver', "
        "'deliver_threshold', spec)\n"
        "print(json.dumps({'pulls': tuner.pulls(), 'mean_rewards': tuner.mean_rewards(), "
        "'total_pulls': tuner.total_pulls}))\n"
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


def _parse_candidates(raw: str | None) -> tuple[int, ...]:
    """`--candidates '5,20'` -> `(5, 20)`; `None`/unset -> the full default
    grid (`DELIVER_THRESHOLD_CANDIDATES`). A narrower live-gate-only grid
    lets a bounded `--sessions` budget concentrate cleanly: UCB1 spends one
    forced pull per candidate before it can ever exploit, so 4 candidates
    over 6 sessions leaves only 2 exploitation pulls — structurally unable to
    show concentration (see `_run_tuner`'s own docstring and PHASE4.md item
    4's "Key decisions changed from spec"). `village.py`'s real wiring keeps
    the full `DELIVER_THRESHOLD_CANDIDATES` grid; only this live-gate script
    narrows it, and only when asked to.
    """
    if not raw:
        return DELIVER_THRESHOLD_CANDIDATES
    return tuple(int(c.strip()) for c in raw.split(","))


def _run_tuner(args: argparse.Namespace) -> None:
    """PHASE4.md item 4's live gate, tuner-driven leg: run the staged trade
    scenario `args.sessions` times in this one invocation, each session
    picking `deliver_threshold` via `ParamTuner.choose()` (in-process,
    informed by every prior session this same run has already recorded) and
    reporting the outcome through `SkillLibrary.record_outcome`. Then a
    FRESH subprocess reads the ledger back from disk and reconstructs an
    independent `ParamTuner` — the decisive, cannot-pass-vacuously proof: a
    broken/no-op tuner would leave that reconstructed `pulls()` flat/uniform
    across the candidate grid, uncorrelated with whichever value the
    control-pair legs showed was actually better on this scenario.

    Every session runs `_run_session(..., stop_on_full_loop=False)` — a
    FIXED `args.tuner_ticks` window, no early exit — so `miner_reward` (raw
    total reward over that fixed window) is directly comparable across
    candidates; see `_run_session`'s own docstring for why the first live-
    gate attempt's `session_mean_reward` metric wasn't. A session that comes
    back with **zero recorded episodes** is a live wedge (the miner made no
    confirmed progress at all in the whole window — observed live: a
    `deliver_threshold=12` session once sat at 0 pack ingots for all 450
    ticks), not a genuine "this value is bad" signal, so it is retried
    (fresh account suffix, bounded) rather than recorded — a poisoning 0.0
    would otherwise permanently sink that arm's mean.
    """
    ledger_path = Path(args.ledger_path) if args.ledger_path else Path("data") / "skill_ledger.jsonl"
    # Clear the ledger first so the cross-process readback below reflects
    # ONLY this proof — item 3's own ledger data is disposable (gitignored).
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("")
    candidates = _parse_candidates(args.candidates)
    print(f"tuner: cleared {ledger_path.resolve()} — {args.sessions} sessions, "
          f"{args.tuner_ticks} ticks each (fixed, no early-stop), candidates={candidates}")

    spec = ParamSpec("deliver_threshold", candidates)
    tuner = ParamTuner("mine_smelt_deliver", spec)
    skill_lib = SkillLibrary(ledger_path=ledger_path)

    max_attempts = 3
    session_results = []
    for i in range(args.sessions):
        chosen = tuner.choose()
        result = None
        for attempt in range(max_attempts):
            miner_account = f"{args.miner_account}tn{i}a{attempt}"
            smith_account = f"{args.smith_account}tn{i}a{attempt}"
            result = _run_session(
                args, deliver_threshold=chosen, miner_account=miner_account, smith_account=smith_account,
                session_ticks=args.tuner_ticks, stop_on_full_loop=False,
                label=f"[tuner {i + 1}/{args.sessions} attempt {attempt + 1}/{max_attempts}]",
            )
            if result["miner_episodes_recorded"] > 0:
                break
            more_left = attempt + 1 < max_attempts
            print(f"[tuner {i + 1}/{args.sessions}] attempt {attempt + 1} produced ZERO episodes over "
                  f"{result['ticks_run']} ticks — a live wedge, not a real deliver_threshold={chosen} signal — "
                  + ("retrying with a fresh account" if more_left else
                     f"all {max_attempts} attempts wedged — SKIPPING, no ledger record for this session"))

        session_results.append(result)
        if result["miner_episodes_recorded"] == 0:
            continue  # exhausted retries — never record a poisoning 0.0 (see this function's docstring)

        reward = result["miner_reward"]  # raw total over the fixed window — the stable, comparable objective
        tuner.update(chosen, reward)
        skill_lib.record_outcome("mine_smelt_deliver", "miner", reward, Status.SUCCESS,
                                 param="deliver_threshold", param_value=chosen)
        print(f"[tuner {i + 1}/{args.sessions}] recorded: deliver_threshold={chosen} "
              f"miner_reward(fixed-window total)={reward:.2f} (episodes={result['miner_episodes_recorded']}, "
              f"in-process pulls so far={tuner.pulls()})")

    print("\n=== tuner sessions summary ===")
    for i, r in enumerate(session_results):
        status = "SKIPPED (0 episodes, all retries wedged)" if r["miner_episodes_recorded"] == 0 else "recorded"
        print(f"  session {i + 1}: deliver_threshold={r['deliver_threshold']:>2} "
              f"miner_reward={r['miner_reward']:.2f} episodes={r['miner_episodes_recorded']} "
              f"ticks={r['ticks_run']} [{status}]")

    print("\n=== tuner in-process pull distribution (this script's own ParamTuner) ===")
    print(f"pulls: {tuner.pulls()}")
    print(f"mean rewards: { {k: round(v, 4) for k, v in tuner.mean_rewards().items()} }")

    print("\n=== cross-process ledger readback ===")
    readback = _cross_process_readback(ledger_path, spec)
    print(f"cross-process (fresh `{sys.executable} -c ...`, reading {ledger_path.resolve()} from disk): "
          f"{readback}")
    if readback is not None:
        best_value = max(readback["pulls"], key=lambda k: readback["pulls"][k])
        print(f"cross-process pulls: {readback['pulls']}")
        print(f"cross-process pull distribution concentrates on deliver_threshold={best_value} "
              f"({readback['pulls'][best_value]}/{readback['total_pulls']} pulls)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=600)
    ap.add_argument("--deliver-threshold", type=int, default=8,
                    help="miner pack ingots that trigger a delivery run")
    ap.add_argument("--smith-ingots", type=int, default=15,
                    help="ingots the blacksmith starts with — deliberately thin, so it starves")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--stagger", type=float, default=4.0, help="seconds between logins (ServUO throttle)")
    # Account names auto-create on first login (ServUO), but `[AddToPack` is
    # additive — re-running against the *same* two names on a persistent shard
    # piles more tools/ingots onto whatever a previous run already left, which
    # can quietly undermine "the smith starts nearly dry". Pick fresh names for
    # a clean scenario each time (or reuse on purpose to test a warm start).
    ap.add_argument("--miner-account", default="anima0")
    ap.add_argument("--smith-account", default="anima1")
    # PHASE4.md item 4 — opt-in, unset by default: zero effect on the plain
    # single-run path above unless passed.
    ap.add_argument("--tuner", action="store_true",
                    help="drive the staged scenario --sessions times, picking deliver_threshold "
                         "via ParamTuner.choose() each session and recording outcomes to the "
                         "skill ledger (Phase 4 item 4)")
    ap.add_argument("--sessions", type=int, default=6, help="--tuner only: how many sessions to run")
    ap.add_argument("--tuner-ticks", type=int, default=450,
                    help="--tuner only: fixed ticks per session, no early-stop (independent of --ticks, "
                         "which the plain single-run path uses)")
    ap.add_argument("--candidates", default=None,
                    help="--tuner only: comma-separated deliver_threshold candidates overriding the "
                         "default (5,8,12,20) — e.g. '5,20' to concentrate a bounded --sessions budget "
                         "on exactly the two values a control-pair run already compared")
    ap.add_argument("--ledger-path", default=None,
                    help="override data/skill_ledger.jsonl (--tuner only)")
    # Opt-in, unset by default: the plain single-run path above keeps its
    # original stop-on-success behavior unless this is passed. Pass this for
    # the control-pair legs (PHASE4.md item 4) so their `miner_reward` is a
    # fixed-window total directly comparable to `--tuner`'s own sessions,
    # which always run fixed-window (see `_run_session`'s own docstring).
    ap.add_argument("--no-early-stop", action="store_true",
                    help="run the full --ticks budget even after the trade loop is demonstrated once — "
                         "for a stable, comparable-across-thresholds total-reward metric")
    args = ap.parse_args()

    if args.tuner:
        _run_tuner(args)
        return

    _run_session(
        args, deliver_threshold=args.deliver_threshold, miner_account=args.miner_account,
        smith_account=args.smith_account, session_ticks=args.ticks, label="(single run)",
        stop_on_full_loop=not args.no_early_stop,
    )


if __name__ == "__main__":
    main()
