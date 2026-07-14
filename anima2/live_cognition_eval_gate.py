"""PHASE6.md item 5's live verification gate: cognition-aware eval — making
`cognition_tier` and `sociability` genuinely move the recorded trajectory.

Two legs, honest about the one confirmed-live provider (Replicate qwen from
`../anima/config.yaml`; no `ANTHROPIC_API_KEY` is provisioned here, the same
constraint PHASE4.md item 2 documented — the Anthropic-specific leg stays
deferred, noted not silently skipped):

**The decisive signal is RAW `EvalResult.speech_sent`, not the coarse
`sociability_bin`.** Item 4's own fishing gate demoted `descriptor_cell` to
informational precisely because it is too coarse to carry a magnitude claim,
and used a raw channel-(b) scalar (`produce_value_rate`) to decide. This gate
follows the identical house standard: `speech_sent` (the raw count of lines
the agent actually voiced) is now a persisted `EvalResult` field alongside
the other raw summary scalars, so the verdict reads a real count off disk.
The `sociability_bin` is still printed, but only as informational context —
deciding a magnitude claim on a single `SOCIABILITY_EDGES` (0.02) crossing
would pass even a marginal straddle (chatty raw 0.021 vs low 0.019), which
raw counts rule out.

**Leg (a) — PRESENCE/ABSENCE (`cognition_tier` is no longer inert).** A
`"mining"` `run_eval_multi` with `cognition_provider="replicate",
cognition_tier="cheap", sociability=<high>` (a real model is called, its
in-character lines are voiced) vs. the IDENTICAL scenario with
`cognition_provider=None` (today's bare mode — the real off-switch, no
cognition, no speech). Every cognition-aware seed's `speech_sent` is `> 0`;
every bare seed's is exactly `0` (a `NullCognition` agent structurally cannot
speak) — read back from disk, not from this process's own memory.

**Dose-response (not just presence/absence).** `sociability=<high>` vs.
`sociability=<low>`, same tick window, same `cognition_tier`, same seed count —
the high-sociability run's mean raw `speech_sent` must be MEASURABLY higher:
`>= 3x` the low mean AND at least 3 more lines on average, a real magnitude gap
that a bin-edge straddle could never satisfy. This is the real differential
`op_sociability` needs to have anything to bite on — a chattier persona
provably speaks more.

**Why bank drain is not a threat to this gate (unlike the mining fitness
gates).** The signal here is SPEECH, not ore: an agent whose `HarvestBank`
has drained still ticks, still reconsiders, still voices what its cognition
queues — so `speech_sent` is unaffected by whether any ore was actually
mined during the window. Spots are still rotated across `MINING_SPOTS[0..3]`
(good-citizen hygiene, per `foundry/eval.py`'s own `spot_pool=` precedent),
but a drained bank cannot invalidate a speech reading the way it invalidates a
`produce_value_rate` one.

**Cross-process readback.** A fresh `python -c` subprocess reads
`data/eval_results.jsonl` from disk and reproduces both legs' verdicts from the
persisted `speech_sent` counts alone — the "fresh channel, never the live
process's own memory" discipline this package has used since PHASE4.md.

LIVE HYGIENE: fresh account prefixes per run/side (never reused — a unix-time
`--suffix`), `data/eval_results.jsonl` cleared first so the readback reflects
only this proof, rotated `MINING_SPOTS[0..3]` per side, and the login throttle
`foundry/eval.py::run_eval` already applies before every eval.

Requires a running ServUO, the built bridge (`cargo build -p anima-net`), and
Replicate configured in `../anima/config.yaml`.
Usage: python -m anima2.live_cognition_eval_gate [--ticks N] [--seeds N]
       [--high-soc 0.9] [--low-soc 0.05] [--tier cheap] [--skip-kernel-guard]
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

from .foundry.eval import EvalConfig, run_eval_multi
from .live_common import fresh_suffix, login_throttle, print_gate_verdict
from .profession import MINING_SPOTS

#: The four confirmed-viable mining spots (`live_eval_gate.py`'s own POOL) —
#: each side gets a distinct one-position rotation so no two sides open at the
#: same spot (bank-drain hygiene, though speech is drain-immune here — see the
#: module docstring).
POOL = tuple(MINING_SPOTS[:4])
POOL_ROT1 = POOL[1:] + POOL[:1]
POOL_ROT2 = POOL[2:] + POOL[:2]


def _soc_bin(result) -> int:
    """The `sociability_bin` off one `EvalResult`'s persisted `descriptor_cell`
    (`(profession_focus, sociability_bin)`); `0` if the cell is empty (a legacy
    or malformed line)."""
    cell = result.descriptor_cell
    return cell[1] if len(cell) > 1 else 0


def _fmt_ints(vals: list[int]) -> str:
    return "[" + ", ".join(str(v) for v in vals) + "]"


def _run_side(args: argparse.Namespace, label: str, *, sociability: float | None,
              provider: str | None, tier: str | None, spot_pool, results_path: Path) -> dict:
    prefix = f"cogeval_{label}{args.suffix}"
    print(f"\n=== SIDE={label}: cognition_provider={provider!r} cognition_tier={tier!r} "
          f"sociability={sociability!r} ===")
    print(f"  account_prefix={prefix} spot_pool={spot_pool}")
    cfg = EvalConfig(
        scenario_id="mining", ticks=args.ticks, account_prefix=prefix,
        cognition_provider=provider, cognition_tier=tier, sociability=sociability,
    )
    multi = run_eval_multi(
        cfg, seeds=args.seeds, spot_pool=spot_pool,
        kernel_repo_root=args.kernel_repo_root, results_path=results_path,
    )
    bins = [_soc_bin(r) for r in multi.results]
    cells = [tuple(r.descriptor_cell) for r in multi.results]
    speech = [r.speech_sent for r in multi.results]
    mean_bin = statistics.fmean(bins) if bins else 0.0
    mean_speech = statistics.fmean(speech) if speech else 0.0
    print(f"  descriptor cells: {cells}")
    print(f"  sociability_bin per seed: {_fmt_ints(bins)} mean={mean_bin:.4f} (coarse — informational)")
    print(f"  RAW speech_sent per seed: {_fmt_ints(speech)} mean={mean_speech:.4f} (the decisive magnitude signal)")
    return {"label": label, "prefix": prefix, "bins": bins, "mean_bin": mean_bin,
            "speech": speech, "mean_speech": mean_speech, "cells": cells}


def _cross_process_readback(results_path: Path) -> dict | None:
    """A fresh Python process reads `results_path` from disk and groups each
    result's `descriptor_cell` `sociability_bin` by `config.account_prefix`
    (unique per side here) — never this process's own in-memory results.
    Returns `None` (stderr printed) on any failure; this readback is evidence,
    not something the run should crash over."""
    script = (
        "import json\n"
        "from anima2.foundry.eval import read_eval_results\n"
        f"results = read_eval_results({str(results_path)!r})\n"
        "by_prefix = {}\n"
        "for r in results:\n"
        "    by_prefix.setdefault(r.config.account_prefix, []).append(r.speech_sent)\n"
        "print(json.dumps(by_prefix))\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=False,
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=200)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--tier", default="cheap", help="cognition tier for the cognition-aware sides")
    ap.add_argument("--high-soc", type=float, default=0.9, help="the chatty/high sociability value")
    ap.add_argument("--low-soc", type=float, default=0.05, help="the quiet/low sociability value")
    ap.add_argument("--results-path", default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--skip-kernel-guard", action="store_true",
                     help="pass kernel_repo_root=None to every eval, skipping assert_kernel_clean — "
                          "only for developing this harness itself pre-commit (the guard is proven "
                          "offline via subprocess-stubbed tests either way; see PHASE5.md item 2)")
    ap.add_argument("--suffix", default=None,
                     help="account-name suffix (default: unix time, for freshness)")
    args = ap.parse_args()
    args.kernel_repo_root = None if args.skip_kernel_guard else "."
    args.suffix = args.suffix or fresh_suffix()

    results_path = Path(args.results_path) if args.results_path else Path("data") / "eval_results.jsonl"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text("")
    print(f"cleared {results_path.resolve()} — this run's own eval_results.jsonl")
    print(f"config: ticks={args.ticks} seeds={args.seeds} tier={args.tier} "
          f"high_soc={args.high_soc} low_soc={args.low_soc} suffix={args.suffix}")

    # Chatty/high (cognition-aware) — reused for BOTH legs: presence/absence
    # (vs bare) and dose-response (vs low).
    chatty = _run_side(args, "chatty", sociability=args.high_soc, provider="replicate",
                       tier=args.tier, spot_pool=POOL, results_path=results_path)
    login_throttle()
    # Bare (the real off-switch: cognition_provider=None) — the presence/absence
    # baseline. `cognition_tier`/`sociability` set but ignored by design.
    bare = _run_side(args, "bare", sociability=args.high_soc, provider=None,
                     tier=args.tier, spot_pool=POOL_ROT1, results_path=results_path)
    login_throttle()
    # Low (cognition-aware, quiet persona) — the dose-response low side.
    low = _run_side(args, "low", sociability=args.low_soc, provider="replicate",
                    tier=args.tier, spot_pool=POOL_ROT2, results_path=results_path)

    # --- in-process verdicts (RAW speech_sent, not the coarse bin) --------
    # Presence/absence: every chatty seed voiced at least one line; every bare
    # (off-switch) seed voiced exactly zero (a NullCognition agent structurally
    # cannot speak). Dose-response: the chatty mean must be MEASURABLY higher
    # than the quiet mean — a real magnitude gap (>= 3x AND >= 3 more lines on
    # average), not a single sociability_bin edge-crossing that a marginal
    # straddle (chatty raw 0.021 vs low 0.019) would also satisfy. Reading raw
    # speech_sent keeps this gate consistent with item 4's own "raw channel
    # decides a magnitude claim, the descriptor bin is only informational."
    chatty_speaks = bool(chatty["speech"]) and all(s > 0 for s in chatty["speech"])
    bare_silent = bool(bare["speech"]) and all(s == 0 for s in bare["speech"])
    hi, lo = chatty["mean_speech"], low["mean_speech"]
    dose_response = hi >= 3.0 * lo and (hi - lo) >= 3.0
    print("\n=== IN-PROCESS SUMMARY ===")
    print(f"presence/absence: chatty speech {_fmt_ints(chatty['speech'])} (all > 0: {chatty_speaks}) vs "
          f"bare speech {_fmt_ints(bare['speech'])} (all == 0: {bare_silent})")
    print(f"dose-response: mean RAW speech high({args.high_soc})={hi:.4f} vs low({args.low_soc})={lo:.4f} "
          f"-> high measurably higher (>=3x and >=+3 lines): {dose_response}")

    # --- cross-process readback ------------------------------------------
    print("\n=== cross-process readback ===")
    login_throttle()
    readback = _cross_process_readback(results_path)
    print(f"cross-process (fresh `{sys.executable} -c ...`, reading {results_path.resolve()} from disk): "
          f"{readback}")

    xr_presence = None
    xr_dose = None
    if readback is not None:
        try:
            xr_chatty = statistics.fmean(readback[chatty["prefix"]])
            xr_bare = statistics.fmean(readback[bare["prefix"]])
            xr_low = statistics.fmean(readback[low["prefix"]])
            xr_presence = xr_chatty > 0 and xr_bare == 0
            xr_dose = xr_chatty >= 3.0 * xr_low and (xr_chatty - xr_low) >= 3.0
            print(f"cross-process presence/absence: mean speech chatty={xr_chatty:.4f} bare={xr_bare:.4f} "
                  f"-> chatty speaks & bare silent: {xr_presence}")
            print(f"cross-process dose-response: mean speech high={xr_chatty:.4f} low={xr_low:.4f} "
                  f"-> high measurably higher (>=3x and >=+3 lines): {xr_dose}")
        except (KeyError, statistics.StatisticsError) as e:
            print(f"cross-process readback verdict reproduction FAILED: {e}")

    print("\n=== GATE VERDICT (cognition-aware eval) ===")
    print_gate_verdict(
        {
            "cognition_aware_speaks_in_process": chatty_speaks,
            "bare_off_switch_silent_in_process": bare_silent,
            "dose_response_high_over_low_in_process": dose_response,
            "presence_absence_cross_process": bool(xr_presence),
            "dose_response_cross_process": bool(xr_dose),
        },
        label="GATE",
        detail=(f"raw speech means — chatty={chatty['mean_speech']:.4f} bare={bare['mean_speech']:.4f} "
                f"low={low['mean_speech']:.4f}"),
    )


if __name__ == "__main__":
    main()
