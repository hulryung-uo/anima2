"""PHASE5.md item 2's live verification gate: the repeatable eval harness
(`foundry/eval.py`) proven on a live shard, two legs — plus a quick probe
that decides leg (b)'s own pairing empirically before running it.

**Probe.** The originally-scoped leg (b) pairing is `mining_50` (Mining 50)
vs `mining` (Mining 35) — but PHASE5.md item 2's own note flags a real risk:
ServUO's skill-GAIN chance tends to fall as skill approaches a resource's
gain ceiling, so a Mining-50 character can plausibly gain skill *slower* than
a Mining-35 one on easy ore (iron), inverting the naive "more skill -> higher
fitness" assumption `skill_gain_rate` is built on. `_quick_probe` runs one
short, single-seed, same-spot session at each skill level and compares
`skill_gain_rate` empirically before committing to a pairing:
  - if Mining 50 wins the probe, leg (b) uses the original `mining_50` vs
    `mining` pairing;
  - otherwise it falls back to a pairing with a provable, cannot-invert
    ground truth: `mining` (Mining 35, WITH a pickaxe) vs the same scenario
    with `item_overrides=()` (NO pickaxe). `skills/harvest.py::Harvest.step`
    with no pickaxe in reach falls into its "open the pack, find nothing,
    repeat" branch forever — `skill_gain_total` is analytically 0 (no tool
    ever gets used) and `worth_term`/`produce_term` are 0 too (nothing is
    ever mined), so `fitness.py`'s `skill_term`/`worth_term`/`produce_term`
    are *provably* zero regardless of any live noise; only a small,
    duration-driven (not skill-driven) `behavior_bonus` survives, which can't
    scale to compete with a real miner's `skill_term`. This can never invert.

**Leg (a) — REPEATABILITY.** The `mining` variant (Mining 35), evaluated by
`run_eval_multi(seeds=3)` TWICE. The tolerance band is stated explicitly and
derived FROM the data: `2 x` the average of the two runs' own per-seed
`stdev_fitness` — not a re-guessed constant. A scorer whose number is noise,
not signal, fails this (the two means land arbitrarily far apart relative to
their own within-run spread).

**Leg (b) — ORDERING (differential).** The pairing the probe picked,
evaluated by `run_eval_multi(seeds=3)` per side. The higher-fitness side must
rank above the lower on MEAN fitness, with the gap surviving seed averaging.

**Cross-process readback.** A fresh `python -c` subprocess reads
`data/eval_results.jsonl` from disk (never this process's own in-memory
`MultiEvalResult`s) and reproduces both legs' verdicts independently — the
"fresh channel, never the live process's own memory" discipline this package
has used since PHASE4.md items 3-5.

LIVE HYGIENE: fresh account prefix per run/side (never reused), `data/
eval_results.jsonl` cleared first so the readback reflects only this proof,
distinct/rotated `MINING_SPOTS[0..3]` per run (`run_eval_multi`'s own
`spot_pool=`) so back-to-back seeds don't share one thinning `HarvestBank`,
a login throttle before every real eval (`foundry/eval.py::run_eval`'s own
built-in one), and full transcripts (never piped through `tail`).

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_eval_gate [--ticks N] [--probe-ticks N]
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

from .foundry.eval import EvalConfig, run_eval, run_eval_multi
from .live_common import fresh_suffix, login_throttle, print_gate_verdict
from .profession import MINING_SPOTS

POOL = tuple(MINING_SPOTS[:4])  # the only four confirmed-viable spots
POOL_ROTATED = POOL[1:] + POOL[:1]  # one-position rotation, for a run/side that follows another


def _fmt(vals: list[float]) -> str:
    return "[" + ", ".join(f"{v:.3f}" for v in vals) + "]"


def _quick_probe(args: argparse.Namespace) -> str:
    print("\n=== QUICK PROBE: Mining 35 vs Mining 50 skill_gain_rate, same spot, single seed ===")
    probe_spot = POOL[0]
    cfg_35 = EvalConfig(
        scenario_id="mining", ticks=args.probe_ticks, account_prefix=f"evalprobe35{args.suffix}", spot=probe_spot,
    )
    r35 = run_eval(cfg_35, kernel_repo_root=args.kernel_repo_root)
    print(f"probe Mining 35: skill_gain_rate={r35.fitness.skill_gain_rate:.3f}/hr "
          f"skill_gain_total={r35.skill_gain_total:.2f} fitness={r35.score:.3f}")

    cfg_50 = EvalConfig(
        scenario_id="mining_50", ticks=args.probe_ticks, account_prefix=f"evalprobe50{args.suffix}", spot=probe_spot,
    )
    r50 = run_eval(cfg_50, kernel_repo_root=args.kernel_repo_root)
    print(f"probe Mining 50: skill_gain_rate={r50.fitness.skill_gain_rate:.3f}/hr "
          f"skill_gain_total={r50.skill_gain_total:.2f} fitness={r50.score:.3f}")

    if r50.fitness.skill_gain_rate > r35.fitness.skill_gain_rate:
        print("probe result: Mining 50 > Mining 35 skill_gain_rate -> using the originally-scoped "
              "mining_50-vs-mining pairing for leg (b).")
        return "mining_50"
    print("probe result: Mining 50 did NOT beat Mining 35's skill_gain_rate -> falling back to the "
          "documented no-pickaxe pairing (Mining 35 WITH a pickaxe vs WITHOUT one) for leg (b), per "
          "this module's own docstring.")
    return "no_pickaxe"


def _leg_a(args: argparse.Namespace, results_path: Path) -> dict:
    print("\n=== LEG (a): REPEATABILITY — mining (Mining 35), run_eval_multi(seeds=3) TWICE ===")
    print(f"spot pools: run1={POOL} run2={POOL_ROTATED}")

    cfg1 = EvalConfig(scenario_id="mining", ticks=args.ticks, account_prefix=f"evala_r1{args.suffix}")
    run1 = run_eval_multi(
        cfg1, seeds=3, spot_pool=POOL, kernel_repo_root=args.kernel_repo_root, results_path=results_path,
    )
    print(f"run1: per_seed={_fmt(run1.per_seed_fitness)} mean={run1.mean_fitness:.4f} "
          f"stdev={run1.stdev_fitness:.4f}")

    cfg2 = EvalConfig(scenario_id="mining", ticks=args.ticks, account_prefix=f"evala_r2{args.suffix}")
    run2 = run_eval_multi(
        cfg2, seeds=3, spot_pool=POOL_ROTATED, kernel_repo_root=args.kernel_repo_root, results_path=results_path,
    )
    print(f"run2: per_seed={_fmt(run2.per_seed_fitness)} mean={run2.mean_fitness:.4f} "
          f"stdev={run2.stdev_fitness:.4f}")

    pooled_stdev = (run1.stdev_fitness + run2.stdev_fitness) / 2.0
    tolerance_band = 2.0 * pooled_stdev
    diff = abs(run1.mean_fitness - run2.mean_fitness)
    repeatable = diff <= tolerance_band if tolerance_band > 0 else diff == 0.0
    print(f"tolerance band = 2 x pooled per-seed stdev = 2 x {pooled_stdev:.4f} = {tolerance_band:.4f}")
    print(f"|mean1 - mean2| = {diff:.4f} -> within band: {repeatable}")

    return {
        "mean1": run1.mean_fitness, "mean2": run2.mean_fitness,
        "stdev1": run1.stdev_fitness, "stdev2": run2.stdev_fitness,
        "tolerance_band": tolerance_band, "diff": diff, "repeatable": repeatable,
        "prefix1": cfg1.account_prefix, "prefix2": cfg2.account_prefix,
    }


def _leg_b_configs(pairing: str, args: argparse.Namespace) -> tuple[EvalConfig, EvalConfig, str, str]:
    """Returns (cfg_expected_lower, cfg_expected_higher, label_lower, label_higher)."""
    if pairing == "mining_50":
        cfg_lower = EvalConfig(scenario_id="mining", ticks=args.ticks, account_prefix=f"evalb_lo{args.suffix}")
        cfg_higher = EvalConfig(scenario_id="mining_50", ticks=args.ticks, account_prefix=f"evalb_hi{args.suffix}")
        return cfg_lower, cfg_higher, "mining (Mining 35)", "mining_50 (Mining 50)"
    cfg_lower = EvalConfig(
        scenario_id="mining", ticks=args.ticks, account_prefix=f"evalb_lo{args.suffix}", item_overrides=(),
    )
    cfg_higher = EvalConfig(scenario_id="mining", ticks=args.ticks, account_prefix=f"evalb_hi{args.suffix}")
    return (
        cfg_lower, cfg_higher,
        "mining, NO pickaxe (provably can't mine)", "mining (Mining 35, WITH pickaxe)",
    )


def _leg_b(args: argparse.Namespace, pairing: str, results_path: Path) -> dict:
    print(f"\n=== LEG (b): ORDERING — pairing={pairing} ===")
    cfg_lower, cfg_higher, label_lower, label_higher = _leg_b_configs(pairing, args)
    print(f"spot pools: [{label_lower}]={POOL} [{label_higher}]={POOL_ROTATED}")

    result_lower = run_eval_multi(
        cfg_lower, seeds=3, spot_pool=POOL, kernel_repo_root=args.kernel_repo_root, results_path=results_path,
    )
    print(f"[{label_lower}]: per_seed={_fmt(result_lower.per_seed_fitness)} "
          f"mean={result_lower.mean_fitness:.4f} stdev={result_lower.stdev_fitness:.4f}")

    result_higher = run_eval_multi(
        cfg_higher, seeds=3, spot_pool=POOL_ROTATED,
        kernel_repo_root=args.kernel_repo_root, results_path=results_path,
    )
    print(f"[{label_higher}]: per_seed={_fmt(result_higher.per_seed_fitness)} "
          f"mean={result_higher.mean_fitness:.4f} stdev={result_higher.stdev_fitness:.4f}")

    gap = result_higher.mean_fitness - result_lower.mean_fitness
    ordering_holds = result_higher.mean_fitness > result_lower.mean_fitness
    print(f"gap (higher - lower) = {gap:.4f}; ordering holds (higher mean > lower mean): {ordering_holds}")

    return {
        "pairing": pairing, "label_lower": label_lower, "label_higher": label_higher,
        "mean_lower": result_lower.mean_fitness, "mean_higher": result_higher.mean_fitness,
        "gap": gap, "ordering_holds": ordering_holds,
        "prefix_lower": cfg_lower.account_prefix, "prefix_higher": cfg_higher.account_prefix,
    }


def _cross_process_readback(results_path: Path) -> dict | None:
    """Spawn a SECOND, freshly started Python process that imports
    `read_eval_results` fresh and reads `results_path` from disk — never
    this process's own in-memory `MultiEvalResult`s — mirrors
    `live_trade.py`/`live_hunt.py`'s identical discipline for their own
    ledgers. Groups fitness totals by `config.account_prefix` (unique per
    run/side in this script) so the caller can reproduce each leg's verdict
    independently. Returns `None` (with the subprocess's stderr printed) on
    any failure — this readback is part of the live gate's own evidence, not
    something the run should crash over.
    """
    script = (
        "import json\n"
        "from anima2.foundry.eval import read_eval_results\n"
        f"results = read_eval_results({str(results_path)!r})\n"
        "by_prefix = {}\n"
        "for r in results:\n"
        "    by_prefix.setdefault(r.config.account_prefix, []).append(r.fitness.total)\n"
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
    ap.add_argument("--ticks", type=int, default=250)
    ap.add_argument("--probe-ticks", type=int, default=100)
    ap.add_argument("--results-path", default=None)
    ap.add_argument("--skip-probe", action="store_true",
                     help="skip the quick probe and go straight to the mining_50-vs-mining pairing")
    ap.add_argument("--pairing", choices=("auto", "mining_50", "no_pickaxe"), default="auto",
                     help="force leg (b)'s pairing instead of running the in-script probe — 'auto' "
                          "(default) runs _quick_probe fresh every invocation. A low --probe-ticks "
                          "count is noise-prone (a single ServUO gain-chance roll can flip a short "
                          "probe's outcome either way); if a prior run's probe already settled the "
                          "direction (see PHASE5.md item 2's 'as landed' section for the recorded "
                          "result), pass it directly here to skip re-probing on a repeat gate run")
    ap.add_argument("--skip-kernel-guard", action="store_true",
                     help="pass kernel_repo_root=None to every eval, skipping assert_kernel_clean — "
                          "only for developing this harness itself pre-commit (the guard is proven "
                          "offline via subprocess-stubbed tests either way; see PHASE5.md item 2)")
    ap.add_argument("--suffix", default=None,
                     help="account-name suffix (default: unix time, for freshness) — every account "
                          "prefix in this script gets it appended, so re-running against a "
                          "persistent shard never reuses a character (and its leftover pack "
                          "contents) from a prior run, including this script's own smoke tests")
    args = ap.parse_args()
    args.kernel_repo_root = None if args.skip_kernel_guard else "."
    args.suffix = args.suffix or fresh_suffix()

    results_path = Path(args.results_path) if args.results_path else Path("data") / "eval_results.jsonl"
    # Clear first so the cross-process readback below reflects ONLY this
    # proof (mirrors live_trade.py --tuner's identical convention) — the
    # file is gitignored/disposable.
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text("")
    print(f"cleared {results_path.resolve()} — this run's own eval_results.jsonl")

    if args.pairing != "auto":
        pairing = args.pairing
        print(f"\n=== pairing forced via --pairing={pairing} — skipping the in-script probe ===")
    elif args.skip_probe:
        pairing = "mining_50"
    else:
        pairing = _quick_probe(args)
    login_throttle()

    leg_a = _leg_a(args, results_path)
    login_throttle()
    leg_b = _leg_b(args, pairing, results_path)

    print("\n=== cross-process readback ===")
    readback = _cross_process_readback(results_path)
    print(f"cross-process (fresh `{sys.executable} -c ...`, reading {results_path.resolve()} from disk): "
          f"{readback}")

    xr_repeatable = None
    xr_ordering_holds = None
    if readback is not None:
        try:
            xr_mean1 = statistics.fmean(readback[leg_a["prefix1"]])
            xr_mean2 = statistics.fmean(readback[leg_a["prefix2"]])
            xr_diff = abs(xr_mean1 - xr_mean2)
            xr_repeatable = (
                xr_diff <= leg_a["tolerance_band"] if leg_a["tolerance_band"] > 0 else xr_diff == 0.0
            )
            print(f"cross-process leg (a): mean1={xr_mean1:.4f} mean2={xr_mean2:.4f} diff={xr_diff:.4f} "
                  f"(reusing the in-process tolerance band {leg_a['tolerance_band']:.4f} — same data, "
                  f"reproduced from disk) -> repeatable={xr_repeatable}")

            xr_lower = statistics.fmean(readback[leg_b["prefix_lower"]])
            xr_higher = statistics.fmean(readback[leg_b["prefix_higher"]])
            xr_ordering_holds = xr_higher > xr_lower
            print(f"cross-process leg (b): mean_lower={xr_lower:.4f} mean_higher={xr_higher:.4f} "
                  f"-> ordering_holds={xr_ordering_holds}")
        except (KeyError, statistics.StatisticsError) as e:
            print(f"cross-process readback verdict reproduction FAILED: {e}")

    print("\n=== GATE VERDICT ===")
    print_gate_verdict(
        {
            "leg_a_repeatable_in_process": leg_a["repeatable"],
            "leg_a_repeatable_cross_process": bool(xr_repeatable),
            "leg_b_ordering_holds_in_process": leg_b["ordering_holds"],
            "leg_b_ordering_holds_cross_process": bool(xr_ordering_holds),
        },
        label="GATE",
        detail=f"pairing={pairing} leg_a_diff={leg_a['diff']:.4f}/band={leg_a['tolerance_band']:.4f} "
               f"leg_b_gap={leg_b['gap']:.4f}",
    )


if __name__ == "__main__":
    main()
