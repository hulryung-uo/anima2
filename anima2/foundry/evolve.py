"""The MAP-Elites evolution loop (PHASE5.md item 4) — kernel-owned.

**Improve the population, not just one agent** (DESIGN.md §6.6): sample an
elite from the archive (or a hand-authored seed genome when the archive is
still empty), mutate ONE of its four named config axes, `run_eval_multi` the
mutant on its scenario, fold a descriptor cell in, `Archive.add` it. Never
synthesizes code — every mutation stays inside `archive.py::Genome`'s four
named, typed fields (`profession` / `sociability` / `deliver_threshold` /
`cognition_tier`), so "config, not code" (item 3's own invariant) holds
across a whole evolution run, not just a single genome.

**SEQUENTIAL evals only — read this before changing `MAX_CONCURRENT_EVALS`.**
The persistent local ServUO shard this project develops against has exactly
ONE GM account (`hulryung`, `live_common.py::GM_RELOGIN_COOLDOWN_S`'s own
docstring) and every `run_eval`/`run_eval_multi` call opens a fresh `GmControl`
connection under that one account to stage + record. Two evals staging at
once would fight over the same GM session. PHASE5.md item 4's own scope names
"a concurrency cap" as part of the spec (mirroring v1 `safety.py::
MAX_CONCURRENT_EVALS`); this port PINS that cap at `1` for exactly the reason
above and evaluates every genome one at a time, in-process, in a plain `for`
loop — no thread/process pool anywhere in this module. `MAX_CONCURRENT_EVALS`
is kept as a named constant (not hard-coded `1` inline) purely so a **future**
second GM account can raise it without hunting for the number; nothing in
`evolve()` reads it as anything other than a loop-serialization fact today.

**Which Genome axes actually move live fitness — stated plainly, not glossed
over.** `foundry/eval.py`'s `SCENARIOS` registry had exactly one
scenario family live-scoreable at Phase 5 item 4's own landing: a bare
`Mine()` skill, no `Agent.cognition`, no `Persona`-driven speech.
PHASE6.md item 4 added a second, `Fish()`-based `"fishing"` scenario — see
below for what that changes and what's still inert. Against today's harness:
  - **`profession`** genuinely selects the eval's scenario, via
    `PROFESSION_SCENARIO` below — TWO entries as of PHASE6.md item 4
    (`"miner" -> "mining"`, `"fisher" -> "fishing"`), so the mutation
    operator (`op_profession`, fully general — swaps among
    `PROFESSION_SCENARIO`'s keys, whatever they are) is no longer a
    guaranteed no-op (`_active_mutation_operators` now includes it — see
    that function's own docstring). Hunter/blacksmith scenarios remain
    deferred (PHASE6.md item 4's own "Key design decisions" — they need
    staging machinery `Scenario`/`run_eval` don't have yet), so this is
    still a two-choice space, not the full `profession.py::PROFESSIONS`
    roster.
  - **`sociability`** (persona talkativeness) and **`cognition_tier`** (an
    `llm.py` tier key) are **live as of PHASE6.md item 5** — but only when a
    RUN-level `EvolutionConfig.cognition_provider` is set (the item-5
    off-switch; `None` by default). With it set, `evaluate_genome` threads
    both genome axes into `EvalConfig`, and `run_eval` stages a
    cognition-aware `Agent` (`ThreadedCognition(LLMCognition(...,
    talkativeness_gate=True))` on the genome's `cognition_tier`, its persona
    at the genome's `sociability`), so a genuinely different model gets called
    and a chattier persona speaks more — both now move the recorded
    trajectory (`speech_sent` → the descriptor's `sociability_bin`). With
    `cognition_provider=None` (a bare `evolve()`/`random_search()`), the
    measured `Agent` is still built with no `cognition=` argument
    (`NullCognition`) and a bare work-`Skill` planner, so both axes stay inert
    — byte for byte the pre-item-5 behavior. `deliver_threshold` walks
    `skill_tuning.DELIVER_THRESHOLD_CANDIDATES`, but the scenario's own
    work skill is `Mine`/`Fish` (no `deliver_threshold` attribute at all, not
    `MineSmeltDeliver`), so it remains the one still-inert axis.

  **This was an honest limitation of item 2's harness, not a defect
  introduced here, and it is exactly what PHASE5.md item 4's own "profession
  swaps between the SCENARIO-SUPPORTED professions" wording already flagged.
  `profession` stopped being inert at PHASE6.md item 4; `sociability`/
  `cognition_tier` stop being inert at PHASE6.md item 5 (this table's own
  update above), leaving only `deliver_threshold`.** Consequences, stated up
  front rather than discovered mid-gate:
  - The offline **convergence test** (this module's own required proof,
    below) does NOT depend on any of this — it drives `evolve()`/
    `random_search()` through an injected `eval_fn` that reads a genome's
    fields directly and returns a synthetic landscape keyed on ALL FOUR axes,
    proving the SELECTION/MUTATION/PROMOTION mechanics are sound on a clean
    signal, independent of what any particular live harness can score today.
  - The **live gate** (`anima2/live_evolve_gate.py`) is honest about the
    likely consequence: with three of the four axes still live-inert
    (`sociability`/`cognition_tier`/`deliver_threshold`, unaffected by
    PHASE6.md item 4's `profession` fix — `deliver_threshold` still has no
    live effect on the bare `Mine()`/`Fish()` scenarios either, neither being
    `MineSmeltDeliver`), the live comparative test still substantially
    measures whether directed search beats random search on a scenario's own
    per-swing RNG (item 2's live gate already documented this as
    substantial — swings from ~2 to ~95 fitness) — i.e. it may legitimately
    tie. PHASE5.md item 4's own spec anticipates exactly this ("if the
    margin is within noise ... the gate reports that rather than dressing a
    tie as a win") — PHASE6.md item 5 (a cognition-aware eval so the
    remaining three axes carry real live signal) is the honest next step,
    not a same-item hack.

**Bounded by two guards, both checked BETWEEN evals (never mid-eval — an
in-flight `run_eval_multi` call always finishes its current seed):**
`max_genomes` (an `EvolutionConfig` field, the per-run cap — mirrors v1
`safety.py::MAX_GENOMES_PER_RUN`, made a parameter rather than a hard-coded
500 since this port's live budgets are far smaller) and a `foundry/STOP`
kill-switch FILE (`kill_switch_active` below) — ported from v1
`safety.py::kill_switch_active`/`KILL_SWITCH_FILE` in spirit, adapted to
where "foundry" means here: v1's `foundry/STOP` sits at that repo's root;
anima2's kernel package IS `anima2/foundry/`, so the pinned file is
`anima2/foundry/STOP` (`_filelock.py`-adjacent, next to this module) rather
than a repo-root path — `touch anima2/foundry/STOP` halts a running loop
after its current genome finishes.

Every eval this module runs goes through `foundry/eval.py::run_eval_multi`,
which already wraps each seed in `_run_eval_with_retry` (transient
`IpcError`/`ConnectionError`/`OSError` retried on a fresh connection) and
`run_eval`'s own `assert_kernel_clean` call (when `kernel_repo_root` is not
`None`) — this module adds no second guard layer on top, it just doesn't
bypass either one.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Sequence

from ..llm import _TIER_MODEL  # {tier: model} — cognition tier keys, single source of truth
from ..skill_tuning import DELIVER_THRESHOLD_CANDIDATES
from .archive import Archive, Genome, InsertResult
from .eval import EvalConfig, MultiEvalResult, run_eval_multi

# --- run guards (ported in spirit from v1 foundry/kernel/safety.py) --------

#: PINNED at 1 — see this module's own docstring for why (one shard GM
#: account). Not read as a real pool size anywhere below; kept as a named
#: constant so a future second account has one obvious place to raise it.
MAX_CONCURRENT_EVALS = 1

#: `touch <this file>` halts a running `evolve()`/`random_search()` loop
#: after its in-flight genome finishes. Checked at the TOP of every
#: iteration, never mid-eval.
KILL_SWITCH_FILE = "STOP"


def kill_switch_active(foundry_root: str | Path | None = None) -> bool:
    """True iff `<foundry_root>/STOP` exists. `foundry_root=None` (the
    default) resolves to this module's OWN directory (`anima2/foundry/`),
    robust regardless of the caller's cwd — every real caller in this repo
    is invoked from the repo root, but a test importing this module from
    anywhere still finds the right `STOP` path."""
    root = Path(foundry_root) if foundry_root is not None else Path(__file__).resolve().parent
    return (root / KILL_SWITCH_FILE).exists()


# --- genome <-> scenario mapping --------------------------------------------

#: `Genome.profession` (a `profession.py::PROFESSIONS` key) -> the
#: `foundry/eval.py::SCENARIOS` id it evaluates on. TWO entries as of
#: PHASE6.md item 4 (`"fisher"` added — see this module's own docstring,
#: "Which Genome axes actually move live fitness," for what that changes and
#: what growing this table further takes).
PROFESSION_SCENARIO: dict[str, str] = {"miner": "mining", "fisher": "fishing"}

#: `llm.py::_TIER_MODEL`'s own keys (`"cheap"`/`"standard"`/`"heavy"`) —
#: reused directly rather than re-declared, so this module can never drift
#: out of sync with `llm.py`'s own tier table (PHASE5.md item 4's own
#: mutation-operator scope: "cognition_tier swaps llm.py tiers").
COGNITION_TIERS: tuple[str, ...] = tuple(_TIER_MODEL.keys())

#: Nudge size for `op_sociability` — `Persona.talkativeness`'s own documented
#: range is `0.0` (silent) to `1.0` (chatty); a genome's sociability is
#: clamped back into that range after every nudge.
SOCIABILITY_STEP = 0.15
SOCIABILITY_BOUNDS = (0.0, 1.0)


# --- mutation operators ------------------------------------------------------
#
# Each operator takes a PARENT `Genome` and a seeded `random.Random`, and
# returns a NEW `Genome` (via `dataclasses.replace`, so every field the
# operator doesn't touch — including `id`/`parent`/`eval`/`hypothesis`/`ts` —
# is carried over from the parent verbatim; `mutate()` below overwrites those
# five bookkeeping fields afterward, uniformly for every operator, so no
# individual operator needs to manage genome identity itself).


def _nearest_index(candidates: Sequence[float], value: float) -> int:
    """The index of `candidates`' closest member to `value` — handles a
    genome whose current value isn't exactly on today's grid (a hand-authored
    seed, or a value from a since-changed candidate tuple) by snapping to the
    nearest neighbor rather than raising."""
    return min(range(len(candidates)), key=lambda i: abs(candidates[i] - value))


def _profession_pool(professions: Sequence[str] | None) -> Sequence[str]:
    """The pool `op_profession`/`random_genome`/`default_seed_genomes` draw a
    profession from: an explicit `professions` restriction (PHASE6.md item 6's
    `--scenario-pool`) when given, else the full module-global
    `PROFESSION_SCENARIO` — deliberately the live dict, not a hardcoded pair,
    so a future third profession needs no change here."""
    return professions if professions is not None else list(PROFESSION_SCENARIO)


#: Every mutation operator takes a uniform `(g, rng, professions)` shape so
#: `mutate()`'s call site stays generic (`op(parent, rng, professions)`) — the
#: three axis operators below ignore `professions`; only `op_profession` reads
#: it. PHASE6.md item 6's `--scenario-pool` restriction MUST reach the mutation
#: side (not just the seed/random draw) or a `mining`-only run could still
#: mutate a mining elite into a `fisher` genome that `evaluate_genome` would
#: then stage and evaluate live — a mislabeled baseline, not a real one.
def op_deliver_threshold(g: Genome, rng: random.Random,
                         professions: Sequence[str] | None = None) -> Genome:
    """Walk `deliver_threshold` one step along
    `skill_tuning.DELIVER_THRESHOLD_CANDIDATES` (snapping to the nearest grid
    value first if `g`'s current value is off-grid), one step up or down,
    clamped at either end of the grid. Ignores `professions`."""
    idx = _nearest_index(DELIVER_THRESHOLD_CANDIDATES, g.deliver_threshold)
    new_idx = max(0, min(len(DELIVER_THRESHOLD_CANDIDATES) - 1, idx + rng.choice((-1, 1))))
    return replace(g, deliver_threshold=float(DELIVER_THRESHOLD_CANDIDATES[new_idx]))


def op_sociability(g: Genome, rng: random.Random,
                   professions: Sequence[str] | None = None) -> Genome:
    """Nudge `sociability` by up to `SOCIABILITY_STEP` in either direction,
    clamped within `SOCIABILITY_BOUNDS` (`Persona.talkativeness`'s own
    documented range). Ignores `professions`."""
    lo, hi = SOCIABILITY_BOUNDS
    delta = rng.uniform(-SOCIABILITY_STEP, SOCIABILITY_STEP)
    return replace(g, sociability=max(lo, min(hi, g.sociability + delta)))


def op_cognition_tier(g: Genome, rng: random.Random,
                      professions: Sequence[str] | None = None) -> Genome:
    """Swap to a different `llm.py` cognition tier (never a no-op swap to the
    same tier, unless `COGNITION_TIERS` has only one member). Ignores
    `professions`."""
    choices = [t for t in COGNITION_TIERS if t != g.cognition_tier] or list(COGNITION_TIERS)
    return replace(g, cognition_tier=rng.choice(choices))


def op_profession(g: Genome, rng: random.Random,
                  professions: Sequence[str] | None = None) -> Genome:
    """Swap to a different scenario-supported profession, drawn from
    `professions` (PHASE6.md item 6's pool restriction) when given, else the
    full `PROFESSION_SCENARIO`; never a no-op swap, unless there is only one
    candidate in the pool."""
    pool = _profession_pool(professions)
    choices = [p for p in pool if p != g.profession] or list(pool)
    return replace(g, profession=rng.choice(choices))


#: Every mutation touches exactly ONE axis per call — standard MAP-Elites
#: practice (small local steps from an elite explore its neighborhood rather
#: than jumping to an unrelated point every generation).
MUTATION_OPERATORS: tuple[Callable[..., Genome], ...] = (
    op_deliver_threshold, op_sociability, op_cognition_tier, op_profession,
)


def _active_mutation_operators(
    professions: Sequence[str] | None = None,
) -> tuple[Callable[..., Genome], ...]:
    """`MUTATION_OPERATORS` filtered to operators that can currently produce
    a genuinely different genome. Concretely: `op_profession` is excluded
    while the effective profession pool (`professions` if given, else the
    module-global `PROFESSION_SCENARIO`) has fewer than 2 candidates —
    otherwise it is a GUARANTEED no-op every single call (there is nothing
    else to swap to), which would waste a full quarter of every mutation
    budget on evaluating an exact duplicate of its parent's config
    (live-caught while tuning this module's own convergence test: a wasted
    quarter of a 24-genome budget is not a rounding error). PHASE6.md item 6:
    a `--scenario-pool mining` run passes `professions=("miner",)` so
    `op_profession` is excluded even though the real global dict now has 2
    entries — the POOL, not the dict's size, decides inclusion. Recomputed
    per `mutate()` call rather than cached, so a future THIRD profession needs
    no change here either."""
    pool = professions if professions is not None else PROFESSION_SCENARIO
    if len(pool) < 2:
        return tuple(op for op in MUTATION_OPERATORS if op is not op_profession)
    return MUTATION_OPERATORS


def mutate(parent: Genome, rng: random.Random, archive: Archive,
           professions: Sequence[str] | None = None) -> tuple[Genome, str]:
    """Apply one randomly chosen operator (from `_active_mutation_operators`
    — see its own docstring for why that can be a strict subset of
    `MUTATION_OPERATORS`) to `parent`, returning `(child, operator_name)`.
    `professions` (PHASE6.md item 6) is threaded uniformly into both the
    operator-selection filter and the chosen operator itself, so a pool
    restriction reaches `op_profession`, not just the seed/random draw.
    The child gets a fresh `archive.next_id()`, `parent` pointing at the
    parent's id, a `hypothesis` naming which operator ran, a fresh
    timestamp, and a cleared `eval` dict (the caller fills it in after
    evaluating)."""
    op = rng.choice(_active_mutation_operators(professions))
    child = op(parent, rng, professions)
    child.id = archive.next_id()
    child.parent = parent.id
    child.hypothesis = op.__name__
    child.ts = time.time()
    child.eval = {}
    return child, op.__name__


def random_genome(archive: Archive, rng: random.Random,
                  professions: Sequence[str] | None = None) -> Genome:
    """A genome sampled UNIFORMLY from the config space — the random-search
    baseline's own generator (never derived from an elite/parent, unlike
    `mutate()`). The profession is drawn from `professions` (item 6's pool)
    when given, else the full `PROFESSION_SCENARIO`; the other three axes span
    the same space `MUTATION_OPERATORS` covers, so the comparative gate's
    "SAME mutation space" constraint holds by construction."""
    return Genome(
        id=archive.next_id(),
        profession=rng.choice(list(_profession_pool(professions))),
        sociability=rng.uniform(*SOCIABILITY_BOUNDS),
        deliver_threshold=float(rng.choice(DELIVER_THRESHOLD_CANDIDATES)),
        cognition_tier=rng.choice(COGNITION_TIERS),
        parent=None,
        hypothesis="random_search",
        ts=time.time(),
    )


def default_seed_genomes(professions: Sequence[str] | None = None) -> list[Genome]:
    """A small, hand-picked set of genomes spanning the config space —
    `evolve()`'s bootstrap population when the archive is still empty (no
    elite exists yet to mutate from). `id`/`ts` are placeholders; `evolve()`
    assigns a real archive id before evaluating each one, exactly like a
    mutated child. With `professions` given (item 6's pool), each seed's
    profession is drawn from that pool (cycling to span it) instead of the
    default `"miner"` — so a `--scenario-pool mining` run's bootstrap stays
    all-miner while a hypothetical fisher-only pool would seed fisher."""
    pool = list(professions) if professions is not None else None
    def prof(i: int) -> str:
        return pool[i % len(pool)] if pool else "miner"
    return [
        Genome(id="seed", profession=prof(0), sociability=0.3, deliver_threshold=8.0,
               cognition_tier="cheap", hypothesis="seed"),
        Genome(id="seed", profession=prof(1), sociability=0.6, deliver_threshold=5.0,
               cognition_tier="standard", hypothesis="seed"),
        Genome(id="seed", profession=prof(2), sociability=0.1, deliver_threshold=20.0,
               cognition_tier="heavy", hypothesis="seed"),
    ]


def select_parent(archive: Archive, seeds: Sequence[Genome], rng: random.Random) -> Genome:
    """MAP-Elites selection: uniformly sample one CURRENT elite. Falls back
    to a uniformly sampled seed genome when the archive has no elites yet
    (`evolve()`'s own bootstrap phase evaluates every seed before this is
    ever reached in practice, but this stays correct standalone too)."""
    elites = archive.elites()
    return rng.choice(elites) if elites else rng.choice(list(seeds))


# --- default eval_fn: the real live path ------------------------------------


def default_eval_fn(
    genome: Genome,
    eval_cfg: EvalConfig,
    *,
    seeds: int,
    spot_pool: Sequence[tuple[int, int]] | None,
    kernel_repo_root: str | Path | None,
    results_path: str | Path | None,
) -> MultiEvalResult:
    """The real evaluator: runs `foundry/eval.py::run_eval_multi` on `eval_cfg`
    exactly as item 2 built it. It ignores its own `genome` parameter — the
    genome's axes are already baked into `eval_cfg` by `evaluate_genome`
    (`sociability`/`cognition_tier` since PHASE6.md item 5, staged live when
    `EvolutionConfig.cognition_provider` is set; `deliver_threshold` still has
    no scenario to bite on — see this module's docstring). Tests substitute a
    stub with this SAME signature (genome-aware, so an offline synthetic
    landscape CAN key off every axis) — see `tests/test_foundry_evolve.py`'s
    convergence test.
    """
    return run_eval_multi(
        eval_cfg, seeds=seeds, spot_pool=spot_pool,
        kernel_repo_root=kernel_repo_root, results_path=results_path,
    )


EvalFn = Callable[..., MultiEvalResult]


# --- config + one evaluated-genome record -----------------------------------


@dataclass
class EvolutionConfig:
    """Everything one `evolve()`/`random_search()` run needs beyond the
    archive itself. `account_prefix`/`account_suffix` together must be
    globally unique across BOTH searches in a comparative live gate run (the
    live gate script's own job — see `live_evolve_gate.py`), matching every
    other live script's fresh-account discipline.
    """

    scenario_ticks: int = 200
    seeds_per_genome: int = 2
    max_genomes: int = 8
    spot_pool: Sequence[tuple[int, int]] | None = None
    account_prefix: str = "evo"
    kernel_repo_root: str | Path | None = "."
    results_path: str | Path | None = None
    foundry_root: str | Path | None = None  # None -> this module's own directory
    rng_seed: int | None = None
    #: PHASE6.md item 5 — a RUN-level cognition switch, deliberately NOT read
    #: off any `Genome`: `cognition_tier` stays the per-genome axis
    #: `op_cognition_tier` mutates, but *whether cognition runs at all* is this
    #: separate, orchestration-level choice alone. `evaluate_genome` threads it
    #: into `EvalConfig.cognition_provider` (never the genome's own fields), so
    #: `None` (the default, unless a caller — e.g. item 6's CLI flag — sets it)
    #: means every genome-driven eval still builds the pre-item-5 bare-skill
    #: agent, byte for byte, no matter what `cognition_tier` each genome carries.
    #: `"stub"` (offline) exercises the wiring; `"replicate"` is the live gate.
    cognition_provider: str | None = None
    #: PHASE6.md item 6 — restrict the profession axis to this pool for the
    #: whole run (seed genomes, random draws, AND `op_profession` mutations),
    #: threaded through every genome-generation surface so the restriction is
    #: airtight (a `mining`-only run never produces a `fisher` genome by any
    #: path). `None` (the default, `--scenario-pool all`) draws from the full
    #: `PROFESSION_SCENARIO`; `("miner",)` (`--scenario-pool mining`)
    #: reproduces Phase 5 item 4's original mining-only gate byte for byte.
    profession_pool: Sequence[str] | None = None


@dataclass
class EvolutionStepRecord:
    """One genome's outcome, for a run's own telemetry/printout — NOT a
    second persistence format (the genome itself is already durably
    persisted by `Archive.add`'s own `data/archive.jsonl` append; this is
    just the in-memory trail `evolve()`/`random_search()` return so a caller
    (the live gate script) can print a trajectory without re-reading the
    ledger)."""

    genome_id: str
    parent_id: str | None
    operator: str
    fitness: float
    per_seed_fitness: list[float]
    reliability: float
    cell: tuple
    insert_status: str


def _account_suffix_for(prefix: str, n: int) -> str:
    """A short, deterministic per-genome account-name fragment — `n` (the
    0-based genome index within this run) is enough to keep every genome's
    fresh-account name unique WITHIN one `evolve()`/`random_search()` call;
    the caller's own `EvolutionConfig.account_prefix` is what keeps two
    concurrent-in-time searches (a live gate's evolution vs random arms)
    from colliding with each other."""
    return f"{prefix}{n}"


def evaluate_genome(
    g: Genome,
    cfg: EvolutionConfig,
    *,
    n: int,
    eval_fn: EvalFn = default_eval_fn,
    spot_pool: Sequence[tuple[int, int]] | None = None,
) -> Genome:
    """Runs `cfg.seeds_per_genome` seeds of `g`'s scenario (via
    `PROFESSION_SCENARIO`), and fills in `g.eval` with the shape
    `Archive.add`/`Genome.reliability` expect: `fitness` (the multi-seed
    mean), `per_seed_fitness` (the raw list — reliability needs the spread,
    not just the mean), and `cell` — item 3's descriptor cell, taken from the
    **median-fitness seed**, decided and documented here:

    `run_eval`'s recorded `TrajectorySummary` (and therefore its computed
    `Descriptor`) isn't persisted in full — only its `.cell` key survives
    onto `EvalResult.descriptor_cell` (see that field's own docstring) — so
    there is no single "mean trajectory" to compute one descriptor from
    directly; a per-seed vote has to pick ONE seed's cell to represent the
    genome. The best-fitness seed is available but is exactly the sample
    promotion's own `reliability_score` guard (item 3) exists to distrust (a
    lucky high-variance seed). The MEDIAN-fitness seed is the natural
    alternative: for an odd seed count it's the literal middle sample; for an
    even count (`seeds_per_genome=2`, this item's own live-gate budget) this
    takes the LOWER of the two sorted-by-fitness results
    (`sorted[(n-1)//2]`), i.e. deliberately the less-lucky of a pair rather
    than an average of two cells that might not even be adjacent — a small,
    stated bias toward the more conservative reading, consistent with this
    package's running "don't let a lucky sample decide" theme.

    `spot_pool`, when given, OVERRIDES `cfg.spot_pool` for this ONE genome
    only — the hook `live_evolve_gate.py`'s interleaved fairness rotation
    needs (a fixed, static `cfg.spot_pool` can't express "this specific
    genome, wherever it falls in a shared E/R sequence, gets THIS window of
    the pool" — see that script's own module docstring).
    """
    scenario_id = PROFESSION_SCENARIO.get(g.profession, next(iter(PROFESSION_SCENARIO.values())))
    # PHASE6.md item 5: thread the genome's own `cognition_tier`/`sociability`
    # UNCONDITIONALLY (harmless — `run_eval` ignores both unless
    # `cognition_provider` is set) and `cfg.cognition_provider` — the RUN-level
    # `EvolutionConfig` field, NEVER the genome — into the eval config. A bare
    # `EvolutionConfig()` leaves `cognition_provider=None`, so every
    # genome-driven eval still builds the pre-item-5 bare-skill agent whatever
    # `cognition_tier` each genome happens to carry (see that field's docstring).
    eval_cfg = EvalConfig(
        scenario_id=scenario_id, ticks=cfg.scenario_ticks,
        account_prefix=_account_suffix_for(cfg.account_prefix, n),
        cognition_provider=cfg.cognition_provider,
        cognition_tier=g.cognition_tier,
        sociability=g.sociability,
    )
    effective_spot_pool = spot_pool if spot_pool is not None else cfg.spot_pool
    multi = eval_fn(
        g, eval_cfg, seeds=cfg.seeds_per_genome, spot_pool=effective_spot_pool,
        kernel_repo_root=cfg.kernel_repo_root, results_path=cfg.results_path,
    )
    results = sorted(multi.results, key=lambda r: r.score)
    median = results[(len(results) - 1) // 2] if results else None
    cell = tuple(median.descriptor_cell) if median is not None else ()
    g.eval = {
        "fitness": multi.mean_fitness,
        "per_seed_fitness": multi.per_seed_fitness,
        "cell": list(cell),
    }
    return g


# --- the two searches --------------------------------------------------------
#
# Both searches are built from the SAME two pieces: a STATEFUL step function
# `Archive -> (candidate_genome, operator_name)` (`make_mutation_step`/
# `make_random_step` below — "stateful" because `make_mutation_step`'s
# bootstrap cursor must persist across calls) and the shared `_drive` loop
# (guard checks + evaluate + insert + record, identical for both). Exposing
# the step factories as their own functions (not buried as closures inside
# `evolve()`/`random_search()`) is what lets `live_evolve_gate.py` INTERLEAVE
# one evolve-step and one random-step at a time with a per-genome spot_pool
# override (`evaluate_genome`'s own `spot_pool=` parameter) — its own
# fairness requirement, which neither `evolve()` nor `random_search()` needs
# to know anything about.


def make_mutation_step(
    rng_seed: int | None, seed_genomes: Sequence[Genome] | None = None,
    professions: Sequence[str] | None = None,
) -> Callable[[Archive], tuple[Genome, str]]:
    """Returns a STATEFUL step function implementing `evolve()`'s own
    policy: evaluate EVERY seed genome as-is FIRST (bootstrapping the
    initial population — standard MAP-Elites initialization), THEN, once the
    seed pool is exhausted, mutate a uniformly sampled current elite.

    **Bootstraps the WHOLE seed pool, not just until the first elite
    appears** — a real, live-caught distinction, not a stylistic choice:
    gating the bootstrap on `not archive.elites()` (this function's first
    implementation) stops after the FIRST seed fills ANY cell, silently
    discarding the rest of the seed pool's own diversity — e.g. with 3 hand-
    picked seeds spanning different `sociability` values, only the first
    ever got evaluated, and mutation had to random-walk from ONE starting
    point instead of exploring from all three. Caught by this module's own
    convergence test tuning (`tests/test_foundry_evolve.py`): with the bug,
    `evolve()` sometimes lost to `random_search()` on identical budgets,
    because most of its budget went to `op_profession` mutations that were
    pure no-ops (see `_active_mutation_operators`) plus a slow random walk
    from a single seed, rather than actually exploiting its designed
    starting diversity.
    """
    rng = random.Random(rng_seed)
    seeds = list(seed_genomes) if seed_genomes is not None else default_seed_genomes(professions)
    seed_cursor = {"i": 0}

    def step(archive: Archive) -> tuple[Genome, str]:
        if seed_cursor["i"] < len(seeds):
            base = seeds[seed_cursor["i"]]
            seed_cursor["i"] += 1
            child = Genome(
                id=archive.next_id(), profession=base.profession, sociability=base.sociability,
                deliver_threshold=base.deliver_threshold, cognition_tier=base.cognition_tier,
                parent=None, hypothesis="seed", ts=time.time(),
            )
            return child, "seed"
        parent = select_parent(archive, seeds, rng)
        return mutate(parent, rng, archive, professions)

    return step


def make_random_step(rng_seed: int | None,
                     professions: Sequence[str] | None = None) -> Callable[[Archive], tuple[Genome, str]]:
    """Returns a STATEFUL (only for RNG continuity — no bootstrap/cursor)
    step function implementing `random_search()`'s policy: every genome is
    drawn UNIFORMLY from the config space (`random_genome`), never derived
    from an elite. `professions` (item 6's pool) restricts the profession
    axis, forwarded unchanged into `random_genome`."""
    rng = random.Random(rng_seed)

    def step(archive: Archive) -> tuple[Genome, str]:
        return random_genome(archive, rng, professions), "random"

    return step


def _drive(
    archive: Archive, cfg: EvolutionConfig, step_fn: Callable[[Archive], tuple[Genome, str]], eval_fn: EvalFn,
) -> list[EvolutionStepRecord]:
    """The guard checks (kill switch, `max_genomes`) and the
    evaluate-insert-record sequence — IDENTICAL for both searches; only
    `step_fn` (how the next candidate genome is produced) differs, which is
    exactly the one variable the comparative live gate needs held apart
    while everything else (eval budget, scenario/seed count, ledger paths)
    stays equal."""
    records: list[EvolutionStepRecord] = []
    for n in range(cfg.max_genomes):
        if kill_switch_active(cfg.foundry_root):
            print(f"[evolve] STOP file active — halting after {n}/{cfg.max_genomes} genomes")
            break
        candidate, operator = step_fn(archive)
        candidate = evaluate_genome(candidate, cfg, n=n, eval_fn=eval_fn)
        result: InsertResult = archive.add(candidate)
        records.append(EvolutionStepRecord(
            genome_id=candidate.id, parent_id=candidate.parent, operator=operator,
            fitness=candidate.fitness, per_seed_fitness=list(candidate.eval.get("per_seed_fitness", [])),
            reliability=candidate.reliability, cell=candidate.cell, insert_status=result.status,
        ))
    return records


def evolve(
    archive: Archive,
    cfg: EvolutionConfig,
    *,
    eval_fn: EvalFn = default_eval_fn,
    seed_genomes: Sequence[Genome] | None = None,
) -> list[EvolutionStepRecord]:
    """The MAP-Elites loop — `make_mutation_step` + `_drive`. Bounded by
    `cfg.max_genomes` and the `STOP` kill switch, checked between evals — see
    this module's own docstring and `make_mutation_step`'s for the bootstrap
    policy."""
    return _drive(archive, cfg,
                  make_mutation_step(cfg.rng_seed, seed_genomes, professions=cfg.profession_pool),
                  eval_fn)


def random_search(
    archive: Archive,
    cfg: EvolutionConfig,
    *,
    eval_fn: EvalFn = default_eval_fn,
    seed_genomes: Sequence[Genome] | None = None,
) -> list[EvolutionStepRecord]:
    """The comparative baseline PHASE5.md item 4's live gate requires —
    `make_random_step` + `_drive`. `seed_genomes` is accepted only for a
    matching call signature with `evolve()` (unused here — random search has
    no bootstrap phase, every draw is already unguided)."""
    del seed_genomes
    return _drive(archive, cfg,
                  make_random_step(cfg.rng_seed, professions=cfg.profession_pool), eval_fn)
