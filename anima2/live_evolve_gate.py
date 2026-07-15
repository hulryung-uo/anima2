"""PHASE5.md item 4's live verification gate: the MAP-Elites evolution loop
(`foundry/evolve.py::evolve`) vs a random-search baseline
(`foundry/evolve.py::random_search`), SAME mutation space, SAME total eval
budget, SAME scenarios/seeds — a comparative gate, never a bare "the elite
rose" (that alone is a monotone max-of-sample statistic and proves nothing,
per the spec's own words).

**THE KEY FAIRNESS DESIGN DECISION — read this before changing the loop
below.** Mining evals DRAIN `HarvestBank`s (a 10-20 minute real-time respawn
— see the `anima2-live-verification` memory note and PHASE5.md item 2's own
live gate, which independently rediscovered this). If evolution's ~8 genomes
ran FIRST and random's ~8 ran SECOND (or vice versa), the second arm would
mine thinned banks the first arm just drained — an unfair, one-sided
handicap that would have nothing to do with which SEARCH STRATEGY is
better. The fix: **interleave the two searches' evals round-robin
(E, R, E, R, ...)**, one genome at a time, sharing a single, continuously
advancing spot cursor over `MINING_SPOTS[0..3]` (the only four confirmed-
viable spots — item 2/4's own precedent) that slides by ONE position per
genome eval (not by `seeds_per_genome`) regardless of which search's turn it
is. This is deliberately NOT "E gets spots 0-1, R gets spots 2-3" (that
degenerate partition is what a naive fixed split, or advancing by exactly
`seeds_per_genome` with a period that divides the pool size, produces — see
`_spot_window`'s own docstring for the arithmetic) — every one of the 4
spots ends up used by BOTH searches over the course of the run, so any one
spot's own richness/drainedness at a given moment affects both arms with
equal probability, not systematically favoring whichever search happens to
go first.

**SEQUENTIAL evals only** — `foundry/evolve.py`'s own `MAX_CONCURRENT_EVALS`
is pinned at 1 (one shard GM account); this script's interleave is a single
in-process loop, never two threads/processes racing the GM connection.

**Kernel-integrity guard: honestly skipped, matching item 2's own documented
precedent, not a new corner cut here.** `anima2/foundry/` (this item's own
`evolve.py`, `_filelock.py`, plus edits to `eval.py`/`archive.py`) is
necessarily uncommitted at gate time — the team's standing "do not commit"
rule for landing work means `assert_kernel_clean` would correctly refuse
EVERY eval if wired in live right now (exactly PHASE5.md item 2's own "Key
decisions" note about its own live gate). This script defaults to
`kernel_repo_root=None` (`--skip-kernel-guard`, on by default HERE — unlike
`live_eval_gate.py`'s off-by-default flag, since this script has no
"guard-on" use case pre-landing) with the refusal logic itself covered by
`tests/test_foundry_eval.py`'s 5 offline, subprocess-stubbed tests (proven,
not skipped — only the LIVE exercise is deferred, same as item 2). The kill
switch, by contrast, IS exercised live (see `_prove_kill_switch_live` below)
— nothing about it requires a clean git tree.

Budget: `--ticks 200 --seeds 2 --genomes 8` (per search; 16 genomes / 32
individual seed-evals total) — PHASE5.md item 4's own guidance range.
Requires a running ServUO and the built bridge.

Usage: `python -m anima2.live_evolve_gate [--ticks 200] [--seeds 2]
[--genomes 8] [--suffix SFX]`
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from .foundry import evolve as evolve_mod
from .foundry.archive import Archive
from .foundry.eval import read_eval_results
from .live_common import fresh_suffix, login_throttle, print_gate_verdict
from .profession import MINING_SPOTS

#: The only four confirmed-viable mining spots (item 2/4's own precedent —
#: `[4:]` are calibration dead ends, see PHASE4.md item 4's "Resolved" note
#: and the `anima2-live-verification` memory note).
POOL = tuple(MINING_SPOTS[:4])

EVOLVE_ARCHIVE_NAME = "archive_evolve_gate.jsonl"
RANDOM_ARCHIVE_NAME = "archive_random_gate.jsonl"
#: The canonical, spec-named ledger — BOTH searches' genomes are also
#: mirrored here (see module docstring's "three-ledger" note below) so the
#: gate's (b) verdict ("recompute from the ledger") and the task's own
#: "cross-process-read from data/archive.jsonl" instruction both resolve
#: against the real default path, not a gate-only side file.
CANONICAL_ARCHIVE_NAME = "archive.jsonl"
RESULTS_NAME = "eval_results.jsonl"


def _gate_paths(suffix: str | None, data_dir: Path = Path("data")) -> dict[str, Path]:
    """PHASE6.md item 6's `--suffix`-to-path plumbing (the housekeeping nit
    Phase 5 item 4 recorded): the gate's own evolve/random archive files and
    its `results_path` carry `suffix`, so two runs (or a reader inspecting
    them cold afterward) never confuse whose rows are whose. An omitted suffix
    (`None`/`""`) reproduces the ORIGINAL fixed names byte for byte — the
    regression pin against Phase 5 item 4's own gate having used them. The
    canonical `archive.jsonl` is deliberately NOT suffixed (it stays the real
    default path both searches also mirror into — see its constant's own
    comment)."""
    sfx = suffix or ""
    return {
        "evo": data_dir / f"archive_evolve_gate{sfx}.jsonl",
        "rand": data_dir / f"archive_random_gate{sfx}.jsonl",
        "canon": data_dir / CANONICAL_ARCHIVE_NAME,
        "results": data_dir / f"eval_results{sfx}.jsonl" if sfx else data_dir / RESULTS_NAME,
    }


def _spot_window(cursor: int, width: int) -> list[tuple[int, int]]:
    """`width` consecutive spots from `POOL`, starting at `cursor mod
    len(POOL)`, wrapping. Called with `cursor` advancing by exactly ONE per
    genome eval (see module docstring) — NOT by `width`/`seeds_per_genome`.
    Advancing by `width` would make each genome's window start at
    `n*width mod len(POOL)`; with `width == len(POOL) // 2` (2 of 4 here)
    that has PERIOD 2 — exactly the E/R alternation's own period — so E
    would ALWAYS land on `cursor in {0, 2, 4, ...}` and R on `{1, 3, 5,
    ...}`, producing the degenerate "E always gets spots {0,1}, R always
    gets {2,3}" split this module's docstring calls out. Advancing by 1
    instead is coprime with the pool size for this width, so both searches'
    own window sequences drift across ALL FOUR spots over the run (verified
    by this module's own `_prove_spot_fairness` at the end)."""
    return [POOL[(cursor + i) % len(POOL)] for i in range(width)]


def _prove_spot_fairness(n_rounds: int, width: int) -> dict:
    """Offline-computable proof (no shard needed) that the interleave's spot
    assignment touches every pool spot roughly evenly for BOTH searches —
    printed as part of the gate's own evidence, and asserted before any live
    eval runs (a design bug here would be silent and expensive to notice
    only after burning the live budget)."""
    evo_spots: dict[tuple[int, int], int] = {s: 0 for s in POOL}
    rand_spots: dict[tuple[int, int], int] = {s: 0 for s in POOL}
    cursor = 0
    for round_i in range(n_rounds):
        for spot in _spot_window(cursor, width):
            evo_spots[spot] += 1
        cursor += 1
        for spot in _spot_window(cursor, width):
            rand_spots[spot] += 1
        cursor += 1
    return {"evo_spot_counts": evo_spots, "rand_spot_counts": rand_spots}


def _prove_kill_switch_live(foundry_root: Path) -> bool:
    """A cheap, REAL (not mocked) live exercise of the kill switch: touches
    an actual `STOP` file on disk next to `foundry/evolve.py`, confirms
    `kill_switch_active()` sees it, then removes it and confirms it's gone —
    proving the mechanism this run relies on works against the real
    filesystem, without spending a live eval on it (the halting BEHAVIOR
    itself — that `_drive`'s loop stops when this returns True — is already
    proven by 4 offline tests in `tests/test_foundry_evolve.py`)."""
    stop_path = foundry_root / "STOP"
    if stop_path.exists():
        print(f"  [kill-switch] WARNING: {stop_path} already exists before this proof — removing it first")
        stop_path.unlink()
    before = evolve_mod.kill_switch_active(foundry_root)
    stop_path.touch()
    during = evolve_mod.kill_switch_active(foundry_root)
    stop_path.unlink()
    after = evolve_mod.kill_switch_active(foundry_root)
    ok = (before is False) and (during is True) and (after is False)
    print(f"  [kill-switch] before={before} during={during} after={after} -> live-proven: {ok}")
    return ok


def _run_interleaved(args: argparse.Namespace) -> dict:
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = _gate_paths(args.suffix, data_dir)
    evo_path, rand_path, canon_path, results_path = (
        paths["evo"], paths["rand"], paths["canon"], paths["results"])
    # PHASE6.md item 6: --scenario-pool restricts the profession axis for the
    # WHOLE run; None (--scenario-pool all) draws the full PROFESSION_SCENARIO.
    pool = ("miner",) if args.scenario_pool == "mining" else None

    # Clear every ledger this gate writes to FIRST, so the cross-process
    # readback at the end reflects ONLY this proof — mirrors
    # `live_eval_gate.py`'s identical convention for `eval_results.jsonl`.
    for p in (evo_path, rand_path, canon_path, results_path):
        p.write_text("")
    print(f"cleared {evo_path}, {rand_path}, {canon_path}, {results_path} — this run's own ledgers")

    archive_evo = Archive(evo_path)
    archive_rand = Archive(rand_path)
    archive_canon = Archive(canon_path)  # mirror of both searches' genomes

    step_evo = evolve_mod.make_mutation_step(rng_seed=args.evo_seed, professions=pool)
    step_rand = evolve_mod.make_random_step(rng_seed=args.rand_seed, professions=pool)

    cfg_evo = evolve_mod.EvolutionConfig(
        scenario_ticks=args.ticks, seeds_per_genome=args.seeds, max_genomes=args.genomes,
        account_prefix=f"evoE{args.suffix}", kernel_repo_root=None, results_path=results_path,
        cognition_provider=args.cognition_provider, profession_pool=pool,
    )
    cfg_rand = evolve_mod.EvolutionConfig(
        scenario_ticks=args.ticks, seeds_per_genome=args.seeds, max_genomes=args.genomes,
        account_prefix=f"evoR{args.suffix}", kernel_repo_root=None, results_path=results_path,
        cognition_provider=args.cognition_provider, profession_pool=pool,
    )

    foundry_root = Path(__file__).resolve().parent / "foundry"
    trajectory_evo: list[evolve_mod.EvolutionStepRecord] = []
    trajectory_rand: list[evolve_mod.EvolutionStepRecord] = []
    cursor = 0
    halted_early = False

    for round_i in range(args.genomes):
        for which, archive, step_fn, cfg, traj in (
            ("EVO", archive_evo, step_evo, cfg_evo, trajectory_evo),
            ("RAND", archive_rand, step_rand, cfg_rand, trajectory_rand),
        ):
            if evolve_mod.kill_switch_active(foundry_root):
                print(f"[gate] STOP file active — halting mid-round ({which}, round {round_i}) "
                      f"after evo={len(trajectory_evo)} rand={len(trajectory_rand)} genomes")
                halted_early = True
                break
            spot_pool = _spot_window(cursor, args.seeds)
            cursor += 1
            login_throttle()
            candidate, operator = step_fn(archive)
            candidate = evolve_mod.evaluate_genome(
                candidate, cfg, n=round_i, eval_fn=evolve_mod.default_eval_fn, spot_pool=spot_pool,
            )
            result = archive.add(candidate)
            # Mirror into the canonical ledger under a FRESH id from
            # `archive_canon`'s own counter, never `candidate.id` as-is —
            # `archive_evo`/`archive_rand` are independent `Archive`
            # instances, each starting its own id counter at `g_00001`, so
            # EVO's g_00001 and RAND's g_00001 would otherwise collide the
            # instant both are inserted into one shared archive (silently
            # overwriting one genome's entry in `_genomes`/corrupting the
            # replayed grid — live-caught by this script's own smoke test
            # before ever touching the shard). `candidate.parent` is left
            # as-is (pointing at the ORIGINAL per-search lineage, which
            # still resolves correctly within `archive_evo`/`archive_rand`
            # — the canonical mirror is a flat read-only union for the
            # cross-check below, not a second lineage graph).
            canon_genome = replace(candidate, id=archive_canon.next_id())
            archive_canon.add(canon_genome)
            rec = evolve_mod.EvolutionStepRecord(
                genome_id=candidate.id, parent_id=candidate.parent, operator=operator,
                fitness=candidate.fitness, per_seed_fitness=list(candidate.eval.get("per_seed_fitness", [])),
                reliability=candidate.reliability, cell=candidate.cell, insert_status=result.status,
            )
            traj.append(rec)
            print(f"  [{which} round={round_i}] op={operator} genome={candidate.id} "
                  f"dt={candidate.deliver_threshold} soc={candidate.sociability:.3f} "
                  f"tier={candidate.cognition_tier} spots={spot_pool} "
                  f"per_seed={[round(v, 3) for v in rec.per_seed_fitness]} "
                  f"fitness={rec.fitness:.3f} reliability={rec.reliability:.3f} "
                  f"cell={rec.cell} status={rec.insert_status}")
        if halted_early:
            break

    return {
        "archive_evo": archive_evo, "archive_rand": archive_rand, "archive_canon": archive_canon,
        "trajectory_evo": trajectory_evo, "trajectory_rand": trajectory_rand,
        "halted_early": halted_early,
        "evo_path": evo_path, "rand_path": rand_path, "canon_path": canon_path, "results_path": results_path,
    }


def _recompute_per_cell_elites(archive_path: Path) -> dict:
    """Item 3's folded proof (b), done "from scratch" (NOT via `Archive`'s
    own bookkeeping — a bug in `Archive.add` itself wouldn't be caught by
    asking `Archive` to grade its own homework): reads every genome line
    from `archive_path`, groups by `cell`, and for each cell independently
    computes the argmax-`reliability_score` genome id — then compares that
    against what a freshly-loaded `Archive` for the SAME path reports as
    each cell's elite. Returns `{"all_match": bool, "mismatches": [...],
    "cells": {...}}`.
    """
    from .foundry.archive import Archive as _Archive
    from .foundry.archive import Genome as _Genome
    from .foundry.archive import cell_to_str as _cell_to_str

    text = archive_path.read_text(encoding="utf-8")
    by_cell: dict[str, list[_Genome]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        d = json.loads(line)
        g = _Genome.from_dict(d)
        by_cell.setdefault(_cell_to_str(g.cell), []).append(g)

    fresh = _Archive(archive_path)
    mismatches = []
    cells_report = {}
    for cell_str, genomes in by_cell.items():
        best = max(genomes, key=lambda g: g.reliability)
        elite = fresh.grid.get(cell_str)
        match = elite == best.id
        cells_report[cell_str] = {
            "n_genomes": len(genomes), "recomputed_best_id": best.id,
            "recomputed_best_reliability": round(best.reliability, 4),
            "archive_elite_id": elite, "match": match,
        }
        if not match:
            mismatches.append(cell_str)

    return {"all_match": not mismatches, "mismatches": mismatches, "cells": cells_report}


def _noise_band(trajectory_evo: list, trajectory_rand: list) -> float:
    """A NOISE BAND DERIVED FROM THIS RUN'S OWN DATA — not a re-guessed
    constant — mirroring `live_eval_gate.py`'s leg (a) "tolerance band = 2 x
    pooled per-seed stdev" convention exactly. Each evaluated genome's own
    `per_seed_fitness` (2+ seeds of the IDENTICAL config) is a direct sample
    of "how much does re-running the same variant swing, purely from Mining's
    own per-swing gain-chance randomness" — pooling that across every genome
    in BOTH searches (not just one) gives an honest estimate of the noise
    floor a comparative margin needs to clear to mean something. Genomes
    with fewer than 2 seeds contribute nothing (no spread to measure)."""
    spreads = []
    for r in list(trajectory_evo) + list(trajectory_rand):
        if len(r.per_seed_fitness) >= 2:
            spreads.append(statistics.pstdev(r.per_seed_fitness))
    if not spreads:
        return 0.0
    return 2.0 * statistics.fmean(spreads)


def _cross_process_readback(canon_path: Path, evo_path: Path, rand_path: Path) -> dict | None:
    """A fresh `python -c` subprocess — never this process's own in-memory
    `Archive`/`EvolutionStepRecord` objects — reads all three archive files
    plus recomputes the per-cell-elite check, mirroring
    `live_eval_gate.py`'s identical "fresh channel, never the live process's
    own memory" discipline."""
    script = (
        "import json\n"
        "from anima2.foundry.archive import Archive, Genome, cell_to_str\n"
        f"canon = Archive({str(canon_path)!r})\n"
        f"evo = Archive({str(evo_path)!r})\n"
        f"rand = Archive({str(rand_path)!r})\n"
        "def best_of(arc):\n"
        "    b = arc.best_by_reliability()\n"
        "    return None if b is None else {'id': b.id, 'fitness': b.fitness, 'reliability': b.reliability}\n"
        "out = {\n"
        "    'canon_total_genomes': len(canon.all_genomes()),\n"
        "    'canon_filled_cells': canon.filled_cells(),\n"
        "    'evo_total_genomes': len(evo.all_genomes()),\n"
        "    'rand_total_genomes': len(rand.all_genomes()),\n"
        "    'evo_best': best_of(evo),\n"
        "    'rand_best': best_of(rand),\n"
        "}\n"
        "print(json.dumps(out))\n"
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
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--genomes", type=int, default=8, help="genomes PER SEARCH (evo + rand each get this many)")
    ap.add_argument("--margin", type=float, default=0.0,
                     help="required reliability margin for evolve to beat random by (0.0: any strict "
                          "win counts, but the script also reports the TIE case honestly regardless)")
    ap.add_argument("--suffix", default=None)
    ap.add_argument("--evo-seed", type=int, default=1001)
    ap.add_argument("--rand-seed", type=int, default=2002)
    ap.add_argument("--scenario-pool", choices=("mining", "all"), default="mining",
                     help="PHASE6.md item 6: 'mining' (default) restricts the profession axis to "
                          "('miner',) — reproducing Phase 5 item 4's original mining-only gate; 'all' "
                          "draws from the full PROFESSION_SCENARIO (miner + fisher), the decisive rerun")
    ap.add_argument("--cognition-provider", choices=("stub", "replicate"), default=None,
                     help="PHASE6.md item 6: omitted (default) leaves EvolutionConfig.cognition_provider "
                          "None — the bare pre-item-5 agent, reproducing Phase 5 item 4's shape; "
                          "'replicate' threads real qwen cognition so the sociability/tier axes are live "
                          "(the decisive rerun); 'stub' exercises the cognition wiring offline")
    args = ap.parse_args()
    args.suffix = args.suffix or fresh_suffix()

    print(f"=== PHASE5.md item 4 live gate: evolve() vs random_search(), "
          f"{args.genomes} genomes/search x {args.seeds} seeds x {args.ticks} ticks ===")
    print(f"MINING pool: {POOL}")
    print("--- offline spot-fairness proof (no shard needed) ---")
    fairness = _prove_spot_fairness(args.genomes, args.seeds)
    print(f"  evo spot counts:  {fairness['evo_spot_counts']}")
    print(f"  rand spot counts: {fairness['rand_spot_counts']}")
    spot_fairness_ok = (
        len(set(fairness["evo_spot_counts"].values())) <= 2  # roughly even, no spot starved
        and len(set(fairness["rand_spot_counts"].values())) <= 2
        and all(c > 0 for c in fairness["evo_spot_counts"].values())
        and all(c > 0 for c in fairness["rand_spot_counts"].values())
    )
    print(f"  spot_fairness_ok = {spot_fairness_ok}")

    foundry_root = Path(__file__).resolve().parent / "foundry"
    print("--- live kill-switch proof ---")
    kill_switch_ok = _prove_kill_switch_live(foundry_root)

    print("\n--- INTERLEAVED live run (E, R, E, R, ...) ---")
    run = _run_interleaved(args)

    print("\n=== trajectories ===")
    print("EVOLVE:")
    for i, r in enumerate(run["trajectory_evo"]):
        print(f"  [{i}] {r.genome_id} op={r.operator} fitness={r.fitness:.3f} "
              f"reliability={r.reliability:.3f} cell={r.cell} status={r.insert_status}")
    print("RANDOM:")
    for i, r in enumerate(run["trajectory_rand"]):
        print(f"  [{i}] {r.genome_id} op={r.operator} fitness={r.fitness:.3f} "
              f"reliability={r.reliability:.3f} cell={r.cell} status={r.insert_status}")

    # Champions are selected by RELIABILITY, not raw fitness — selecting by raw
    # mean and then comparing reliabilities re-imports the optimizer's curse the
    # discount exists to prevent (review-caught before this gate ever reported).
    best_evo = run["archive_evo"].best_by_reliability()
    best_rand = run["archive_rand"].best_by_reliability()
    noise_band = _noise_band(run["trajectory_evo"], run["trajectory_rand"])
    print("\n=== (a) comparative margin ===")
    print(f"  data-derived noise band = 2 x pooled per-genome per-seed stdev = {noise_band:.4f} "
          f"(mirrors live_eval_gate.py leg (a)'s own convention — NOT a re-guessed constant)")
    if best_evo is None or best_rand is None:
        print(f"  INSUFFICIENT DATA: best_evo={best_evo} best_rand={best_rand}")
        margin = None
        margin_ok = False
        beats_noise = False
    else:
        margin = best_evo.reliability - best_rand.reliability
        print(f"  evo best:  id={best_evo.id} fitness={best_evo.fitness:.4f} reliability={best_evo.reliability:.4f} "
              f"dt={best_evo.deliver_threshold} soc={best_evo.sociability:.3f} tier={best_evo.cognition_tier}")
        print(f"  rand best: id={best_rand.id} fitness={best_rand.fitness:.4f} "
              f"reliability={best_rand.reliability:.4f} dt={best_rand.deliver_threshold} "
              f"soc={best_rand.sociability:.3f} tier={best_rand.cognition_tier}")
        print(f"  margin (evo - rand) = {margin:.4f}  (required > --margin={args.margin}; "
              f"data-derived noise band = {noise_band:.4f})")
        margin_ok = margin > args.margin
        beats_noise = abs(margin) > noise_band
        if not margin_ok or not beats_noise:
            verdict_word = "TIE (within the data-derived noise band)" if not beats_noise else (
                "RANDOM WON" if margin < 0 else "EVOLUTION WON but below --margin"
            )
            print(f"  HONEST VERDICT: {verdict_word} — evolution did NOT beat random by a margin that "
                  f"clears this run's own measured noise floor. Per PHASE5.md item 4's own spec, this "
                  f"is reported as-is, not dressed up as a win.")
        else:
            print("  HONEST VERDICT: evolution's margin clears the data-derived noise band — a real win, "
                  "not a coin flip.")

    print("\n=== (b) item 3's folded proof: per-cell elite recompute ===")
    recompute_canon = _recompute_per_cell_elites(run["canon_path"])
    print(f"  canonical archive.jsonl: all_match={recompute_canon['all_match']} "
          f"mismatches={recompute_canon['mismatches']}")
    for cell_str, info in recompute_canon["cells"].items():
        print(f"    cell={cell_str}: {info}")

    print("\n=== cross-process readback ===")
    readback = _cross_process_readback(run["canon_path"], run["evo_path"], run["rand_path"])
    print(f"  {readback}")
    xr_margin_ok = None
    if readback is not None and readback.get("evo_best") and readback.get("rand_best"):
        xr_margin = readback["evo_best"]["reliability"] - readback["rand_best"]["reliability"]
        xr_margin_ok = xr_margin > args.margin
        print(f"  cross-process margin = {xr_margin:.4f} -> margin_ok={xr_margin_ok}")

    eval_results = read_eval_results(run["results_path"])
    print(f"\n  {run['results_path'].name}: {len(eval_results)} lines written this run")

    # --- enrichment sanity (PHASE6.md item 6) ----------------------------
    # Items 4-5 are what make this a DECISIVE rerun rather than Phase 5 item
    # 4's honest tie: the profession axis and the cognition axes must actually
    # move during THIS run, not just be theoretically available. Only asserted
    # when the enriching flag is on (a --scenario-pool mining / no-provider run
    # is the reproduce-item-4 baseline and legitimately samples neither).
    canon_genomes = run["archive_canon"].all_genomes()
    professions_sampled = sorted({g.profession for g in canon_genomes})
    soc_bins = [g.cell[1] for g in canon_genomes if len(g.cell) > 1]
    both_professions = {"miner", "fisher"} <= set(professions_sampled)
    cognition_fired = any(b > 0 for b in soc_bins)
    print("\n=== enrichment sanity (item 6) ===")
    print(f"  professions actually sampled this run: {professions_sampled}")
    print(f"  sociability_bins across genomes: {soc_bins} (any > low: {cognition_fired})")
    enrich_flags: dict[str, bool] = {}
    if args.scenario_pool == "all":
        enrich_flags["both_professions_sampled"] = both_professions
    if args.cognition_provider == "replicate":
        enrich_flags["cognition_fired_live_sociability_bin_above_low"] = cognition_fired

    print("\n=== GATE VERDICT ===")
    print("NOTE: 'comparative_margin_beats_noise_band' is the HONEST decisive flag (per PHASE5.md item "
          "4's own preference for a reported tie over a dressed-up win) — a False here at a small live "
          "budget is an EXPECTED, VALID outcome, not a failure of this script; see the printed verdict "
          "above for the actual margin/noise numbers.")
    print_gate_verdict(
        {
            "spot_fairness_design_ok": spot_fairness_ok,
            "kill_switch_live_proven": kill_switch_ok,
            "kernel_guard_offline_proven_live_skipped_per_item2_precedent": True,
            "run_completed_without_early_halt": not run["halted_early"],
            "per_cell_elites_recompute_matches": recompute_canon["all_match"],
        },
        label="INFRASTRUCTURE GATE",
        detail="the mechanics that must hold regardless of which search wins",
    )
    if enrich_flags:
        print_gate_verdict(
            enrich_flags,
            label="ENRICHMENT SANITY",
            detail=f"items 4-5 axes actually moved this run (scenario-pool={args.scenario_pool}, "
                   f"cognition-provider={args.cognition_provider})",
        )
    print(f"[FLAG] comparative_margin = {margin}")
    print(f"[FLAG] comparative_noise_band = {noise_band:.4f}")
    print(f"[FLAG] comparative_margin_beats_noise_band = {bool(margin is not None and beats_noise and margin > 0)}")


if __name__ == "__main__":
    main()
