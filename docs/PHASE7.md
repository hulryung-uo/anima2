# Phase 7 — Work Breakdown

Phase 7 = **redeem the evolution loop, close the skill-ledger honesty gap,
sharpen memory retrieval** — four small, independently-landable items, each
closing a real, already-named open loop by reusing an already-existing
mechanism at a new call site. No item below invents a new mechanism: items 1-2
extend `foundry/evolve.py`'s own already-proven config-space search and
`live_evolve_gate.py`'s own already-proven gate machinery; item 3 reuses
Phase 5 item 1's already-shipped, already-live-verified independent-channel
pattern (`foundry/trajectory.py::TrajectoryRecorder`'s GM-read channel (a));
item 4 reuses `_textindex.py`'s already-shipped, already-calibrated-by-
precedent keyword scoring (`skill_library.py::retrieve()`/`wiki.py::
Wiki.search()` both already ship it). No item is expected to touch the
Observation/Action contract (`contract.py`) — the fast loop stays
deterministic, no LLM/file I/O inside any `Skill.step()`, every new persisted
state follows the established `data/*.jsonl` convention (lazily created,
gitignored, corrupt-line-tolerant on read, `threading.Lock`-guarded for
same-process concurrency), and LLM calls stay slow-loop-only,
provider-abstracted through `llm.py` — DESIGN.md §2's non-negotiables,
unchanged, reaffirmed per item below where load-bearing.

**Where this comes from.** PHASE6.md item 6's own comparative live gate — the
decisive evolution-vs-random rerun on the enriched (Phase 6 items 4-5) harness
— came back an **honest loss**: random search beat the evolution loop
(margin −29.32, outside the ±21.99 noise band), reported as-is per this
project's own "report a tie/loss as such" discipline (PHASE5.md item 4).
PHASE6.md item 6's own "As landed" section named two compounding causes: (1)
8 genomes is far too small for MAP-Elites to exploit (only 5 mutations after
3 bootstrap seeds); (2) `op_profession`'s fisher swaps land in the fishing
scenario, whose single pinned `HarvestBank` is drained by back-to-back fisher
evals — "the exact bank-drain item 4's own gate documented, which the evolve
gate does **not** rotate away from" — and named both a larger budget and a
fishing-spot rotation as **Phase 7 candidates**, verbatim. PHASE6.md's own
"Notes carried into Phase 7" section additionally carries forward: the
LLM-authored skill DSL (gated on config-space evolution being "both proven
live," restated below — not yet met), a fuller memory-stream retrieval layer,
decaying/weighted relationship scores, hunter/blacksmith `Scenario` entries,
cost-tier budgets derived from curriculum/task difficulty, and the
subprocess-isolation upgrade for trajectory channel (b). DESIGN.md §11 also
still lists "Skill-ledger reward independence" as a standing open item, flagged
in PHASE4.md item 3 for "a cheap partial mitigation... not solved." Items 1-2
below are the direct redemption of item 6's loss; item 3 is the cheap partial
mitigation §11 names; item 4 is the literal gap PHASE6.md item 1 left standing
(`ReflectionMemory` has zero retrieval logic beyond pure recency) — narrower
than the fuller memory-stream ambition PHASE6.md defers for lack of a
calibratable formula, deliberately.

**Re-reading the code for this design pass surfaced a THIRD, previously
undiagnosed compounding cause of item 6's loss** (item 1 below fixes it
alongside the two PHASE6.md already named) — verified directly against the
current source, not asserted: `live_evolve_gate.py::_run_interleaved` builds
its spot-rotation window from a MINING-only pool (`POOL = tuple(MINING_SPOTS
[:4])`, via `_spot_window(cursor, args.seeds)`) and hands it to
`evolve.evaluate_genome(candidate, cfg, n=round_i, eval_fn=...,
spot_pool=spot_pool)` **unconditionally, regardless of `candidate.
profession`**. `evaluate_genome` (`foundry/evolve.py`) applies it just as
unconditionally (`effective_spot_pool = spot_pool if spot_pool is not None
else cfg.spot_pool`), forwarded through `default_eval_fn` into `run_eval_multi
(eval_cfg, seeds=..., spot_pool=effective_spot_pool, ...)` with no profession
check anywhere on that path; `run_eval_multi` does `seed_spot = spot_pool[i %
len(spot_pool)] if spot_pool else cfg.spot`, overriding `cfg.spot`, and
`run_eval`'s own `spot = cfg.spot or scenario.spot` means a non-`None`
`cfg.spot` always wins. Net effect on a `--scenario-pool all` run: a drawn
**fisher** genome's session gets staged (`gm.stage(serial, spot[0], spot[1],
...)`) at a **Minoc mining coordinate**, while `cfg.nodes` is never touched
anywhere on this path (`evolve.py` has no `nodes`/`nodes_pool` concept at all
today), so `run_eval`'s `nodes = cfg.nodes if cfg.nodes is not None else
scenario.nodes` falls back to the *original* Vesper Bay water tile from
`SCENARIOS["fishing"].nodes` — a fisher character ends up standing on a
Minoc ridge, hundreds of tiles from the one water tile it's told to cast at.
This is a plausible, compounding cause of item 6's fisher genomes scoring ~4
fitness (item 6's own recorded number) that its own diagnosis didn't name —
not a replacement for the drained-bank cause item 6 already found, an
addition to it. Item 1 below fixes both together, since they're the same
missing piece (a profession-aware pool) seen from two angles.

**On the LLM-authored skill DSL (DESIGN.md §6 item 3 / thread A) — explicitly
NOT a Phase 7 item, stated plainly rather than silently dropped.** Three
separate prior phases (4, 5, 6) named it and deferred it without ever
prototyping even a minimal interpreter — there is no `eval()`/`exec()`/
`compile()` call, and no code-execution sandbox of any kind, anywhere in this
codebase family (`anima2` or v1 `../anima`); a Phase 7 attempt would be
greenfield-designing a brand-new safety primitive, not extending a proven one.
More importantly, PHASE6.md's own stated precondition for escalating to it —
**"the natural next escalation once config-space evolution (Phase 5) and its
richer eval harness (this phase) are both proven live, not attempted before
then"** — is not honestly met: item 6 came back a **loss** for evolution, not
proof that config-space search meaningfully beats a baseline. Items 1-2 below
exist specifically to give that precondition a fair, confound-free test with
the two (now three) known confounds removed; only once evolution demonstrably
works at this small, safe scale does it make sense to trust a much larger,
much riskier code-synthesis loop layered on top of it. The DSL's own design
(a real, not hand-waved, safe-by-construction shape — a closed vocabulary
built directly off `skill_library.py::REGISTRY`, never `eval`/`exec`) is
carried into Phase 8 as the named next escalation, explicitly gated on item
2's verdict — see "Notes carried into Phase 8" below.

**Dependency order.** Item 1 → item 2 is a hard dependency: item 2 spends a
materially larger live-shard budget exercising item 1's fix, and running it
before the fix lands would spend that budget re-confirming a bug this design
pass already found by reading the code, not testing anything new. Items 3 and
4 are independent of items 1-2 and of each other — either can land first, or
both in parallel; nothing in this phase requires a specific interleaving
beyond item 1 → item 2.

Status legend: ✅ done · 🚧 in progress · ⏳ todo. **Items 1 and 4 are done;
items 2 and 3 remain.**

---

## Item 1 — Fix profession-conditional pool routing + thread the fishing `nodes_pool` through `evolve.py`/`live_evolve_gate.py` ✅

**Redeem the loss's first diagnosed cause (drained fishing banks) and the
third, undiagnosed one this design pass's own re-read of the code found (a
fisher genome staged at a mining coordinate) — together, since they're the
same missing piece.** No new mechanism: `run_eval_multi` (`foundry/eval.py`)
already accepts a `nodes_pool=` parameter (Phase 6 item 4) that rotates the
water node in lockstep with a shore-stand `spot_pool=`; it has simply never
been threaded up through `evolve.py`/`live_evolve_gate.py`. This item is that
plumbing, plus the profession-conditional routing fix the plumbing's absence
was masking.

### Scope

Files: `anima2/foundry/evolve.py`, `anima2/live_evolve_gate.py`,
`tests/test_foundry_evolve.py`, `tests/test_live_evolve_gate.py`,
`docs/PHASE7.md` (this document, created here).

1. **`EvolutionConfig` gains two new, both-optional fields** — `spot_pool`'s
   existing shape/meaning is untouched: `nodes_pool: Sequence[tuple[tuple[int,
   int, int, int], ...]] | None = None` (`Scenario.nodes`-shaped — the exact
   shape `run_eval_multi` already accepts) and `fishing_spot_pool:
   Sequence[tuple[int, int]] | None = None` (shore-stand coords,
   `FISHING_SPOTS[i][0]`-shaped). Fishing needs its *own* spot field, not
   `spot_pool` reused for both professions — reusing one field for both is
   exactly today's bug. Both default `None`: every existing `EvolutionConfig()`
   construction (every Phase 5/6 test and gate) is byte-for-byte unchanged.
2. **The fix is pushed into `evaluate_genome` itself, not just the calling
   script — defense-in-depth, so a future caller can't reintroduce the same
   class of bug by a different path.** `evaluate_genome(g, cfg, *, n,
   eval_fn=default_eval_fn, spot_pool=None, nodes_pool=None,
   fishing_spot_pool=None)`. After resolving `scenario_id =
   PROFESSION_SCENARIO.get(g.profession, ...)` (unchanged), compute
   `is_fishing = SCENARIOS[scenario_id].nodes is not None` — a **generic,
   structural** check (never a hardcoded `"fisher"`/`"fishing"` string), so a
   future third `nodes`-bearing scenario is covered automatically and a
   future `Mine`-shaped scenario without `nodes` is excluded automatically
   (`evolve.py`'s own `from .eval import ...` grows one name, `SCENARIOS`).
   Branch:
   - **`is_fishing`**: `effective_spot_pool` resolves from `fishing_spot_pool
     if fishing_spot_pool is not None else cfg.fishing_spot_pool`;
     `effective_nodes_pool` from `nodes_pool if nodes_pool is not None else
     cfg.nodes_pool`. A mining-shaped `spot_pool=`/`cfg.spot_pool` argument,
     if one still reaches this call for a fisher genome, is silently ignored
     for it — there is nothing wrong to report, the correct pool was resolved
     from the fishing-specific fields instead.
   - **not `is_fishing`** (mining, or any future non-`nodes` scenario):
     `effective_spot_pool` resolves exactly as today (`spot_pool if spot_pool
     is not None else cfg.spot_pool`), and `effective_nodes_pool` is **forced
     to `None`**, regardless of what was passed — the actual defense-in-depth
     half of the fix: a `nodes_pool` argument can now never reach a mining
     eval by any call path, present or future (`Mine`/`Fish` both subclass
     `Harvest` and both read `ctx.memory["harvest_nodes"]` generically —
     confirmed directly against `skills/harvest.py` — so a leaked
     `nodes_pool` would silently corrupt a mining eval's staging, not just be
     inert).
   - `effective_nodes_pool` is forwarded into `eval_fn(g, eval_cfg,
     seeds=cfg.seeds_per_genome, spot_pool=effective_spot_pool,
     nodes_pool=effective_nodes_pool, kernel_repo_root=cfg.kernel_repo_root,
     results_path=cfg.results_path)`.
3. **`default_eval_fn` gains a `nodes_pool=None` passthrough parameter**,
   forwarded unchanged into `run_eval_multi(eval_cfg, seeds=seeds,
   spot_pool=spot_pool, nodes_pool=nodes_pool, kernel_repo_root=
   kernel_repo_root, results_path=results_path)` — `run_eval_multi` already
   accepts `nodes_pool` (Phase 6 item 4); this is the last mile of plumbing
   `evolve.py`'s own module docstring already names as missing ("Which
   Genome axes actually move live fitness"), not a new mechanism.
4. **`live_evolve_gate.py`**:
   - `FISH_POOL = tuple(FISHING_SPOTS[:4])` (module-level, mirrors `POOL =
     tuple(MINING_SPOTS[:4])`) — the four `FISHING_SPOTS` entries with the
     strongest live track record (`[0]` is the scenario's own default and the
     spot Phase 6 item 4's first, pre-rotation-fix run used; `[1..3]` are the
     three Phase 6 item 4's own rotated live gate confirmed produce real
     fish). `FISHING_SPOTS[4:8]` are untested in any live gate so far and are
     **deliberately excluded** — named as a risk below, not silently widened.
   - `_fish_window(cursor, width)`, mirroring `_spot_window`'s exact
     non-degenerate wraparound arithmetic (`FISH_POOL[(cursor + i) %
     len(FISH_POOL)]` for stands, the matched `FISHING_SPOTS` water-node
     counterpart for nodes), returning `(stand_window, nodes_window)` pairs.
   - **Two independent cursors, not one.** The existing `cursor` (today
     advances by exactly one on every arm-round, unconditionally) is changed
     to advance only on rounds whose drawn `candidate.profession ==
     "miner"`; a new, separate `fish_cursor` advances only on rounds whose
     drawn `candidate.profession == "fisher"`. This split is necessary, not
     optional, once mining and fishing genomes stop sharing one pool: leaving
     the mining cursor advancing on every round (including fisher rounds
     that no longer touch a mining spot at all) would silently distort the
     mining-only fairness proof by consuming cursor positions no mining eval
     actually used. The genome's profession is known as soon as
     `step_fn(archive)` returns, i.e. before `evaluate_genome` is called, so
     the loop body computes the right window for that specific candidate
     before evaluating it.
   - The existing offline `_prove_spot_fairness` pre-proof stays mining-scoped
     and unchanged in spirit — it proves a property of the `(cursor, width)`
     arithmetic in isolation (how many *calls* visit each pool spot evenly),
     not a claim about how many of a live run's *rounds* turn out to be
     mining rounds, which is stochastic and unknowable before the run starts.
     A parallel `_prove_fish_spot_fairness(n_rounds, width)` proves the
     identical arithmetic property for `_fish_window`/`FISH_POOL`, called
     with `args.genomes` as an upper bound on how many times either cursor
     could possibly advance (the worst case: every round draws the same
     profession) — cheap, offline, no dependency on the live stochastic draw.
   - No new CLI flag: this fix activates automatically whenever
     `--scenario-pool all` is in play (the only mode that can draw a fisher
     genome at all); `--scenario-pool mining` (the default) never exercises
     any of this item's new code paths, exactly as before.

### Key design decisions

- **Push the guard into the lowest shared function, not just the calling
  script.** `evaluate_genome`'s own `is_fishing` branch is what makes this
  safe even if a future caller (a hypothetical second live-gate script, a
  notebook, a future `village.py` integration) gets the routing wrong at its
  own call site — mirrors this project's existing "the ruler is unreachable
  from the learning code" discipline (the import-graph guard), applied here
  to a routing bug instead of an import.
- **A mismatched pool argument is silently ignored, not an error.** A
  fishing-shaped pool handed to a mining genome (or vice versa) is exactly
  the kind of stale/irrelevant input this project's "`None`-default is a
  no-op" convention already treats as harmless everywhere else; raising here
  would just be a different way of saying "the caller isn't supposed to do
  that," and the routing fix already makes it impossible to matter.
- **`is_fishing` is a generic structural check (`Scenario.nodes is not
  None`), never a hardcoded profession-name string.** A future third
  `nodes`-bearing profession (a second `Harvest` subclass at a fixed water/
  bank tile, say) is covered automatically, with zero change to this
  function — the same "the live dict, not a hardcoded pair" discipline
  `_profession_pool` already uses for `PROFESSION_SCENARIO` itself.
- **`FISHING_SPOTS[:4]`, not the full 8-entry pool.** Widening beyond the
  four spots with a real live track record would mean betting this item's
  own smoke gate on untested geometry; named explicitly as a risk (below) and
  a Phase 8+ follow-up, not silently done now.
- **No contract change.** This item is pure config/plumbing (dataclass
  fields, function parameters) inside already-hand-written, already-kernel-
  owned Python (`anima2/foundry/`) plus a live-gate script outside the
  import guard. `contract.py`'s Action/Observation shapes are untouched.

### Offline tests (planned)

`tests/test_foundry_evolve.py` (~6-8 new tests):

- **The load-bearing regression test — written against the CURRENT,
  pre-fix code first, confirmed to fail red, before the fix lands.**
  `evaluate_genome(fisher_genome, cfg, spot_pool=<a mining-shaped pool>)`
  with a capturing stub `eval_fn`: today, that mining-shaped `spot_pool`
  reaches the fisher genome's `EvalConfig`-building call unconditionally —
  this is the exact leak that (plausibly) sent item 6's fisher genomes to a
  mining coordinate. After the fix, the same call shows the mining pool was
  never applied (the fisher genome's effective pool came from
  `fishing_spot_pool`/`cfg.fishing_spot_pool` instead).
- **The mirror test:** a `nodes_pool` passed while evaluating a MINER genome
  never reaches that call (`effective_nodes_pool` stays `None` regardless of
  what was passed).
- A fisher genome's `nodes_pool`/`fishing_spot_pool` ARE correctly forwarded
  and rotate index-aligned — mirrors the existing `spot_pool` passthrough
  test for mining genomes.
- `_fish_window`/`FISH_POOL` wraparound/non-degeneracy proof, mirroring
  `_spot_window`'s own existing (offline, no shard) test shape.
- `_prove_fish_spot_fairness`'s own arithmetic proof, mirroring
  `_prove_spot_fairness`'s.
- Every pre-existing evolve test (30 as of Phase 6's landing) passes
  unchanged — both new `EvolutionConfig` fields default `None`.

### Live verification gate

A bounded SMOKE-scale run of the now-fixed `live_evolve_gate.py` itself —
not a new script: `python -m anima2.live_evolve_gate --ticks 150 --seeds 2
--genomes 6 --scenario-pool all --cognition-provider stub --suffix
phase7item1smoke`, fresh accounts, `--cognition-provider stub` (deterministic,
no live LLM — this smoke test needs zero LLM involvement, only that the
right characters get staged at the right tiles and actually work).

Decisive checks, read from a **fresh subprocess** parsing
`data/eval_resultsphase7item1smoke.jsonl` (`run_eval_multi`'s own
`results_path` passthrough already writes one `EvalResult` per seed, each
carrying its own resolved `config.spot`/`config.nodes` — the ACTUAL staged
pair, not a printed approximation):

1. Every row with `scenario_id == "fishing"` has `config.spot` in
   `FISHING_SPOTS[:4]`'s stand set, **never** a `MINING_SPOTS` coordinate —
   the direct, cross-process-readback-confirmed proof the leak is gone.
2. Across the run's fishing rows, at least two DIFFERENT `FISHING_SPOTS`
   stand values appear — proves the `fish_cursor` genuinely advances, not
   pinned to one spot.
3. At least one fishing row shows materially nonzero
   `fitness.produce_value_rate` (mirroring Phase 6 item 4's own "every
   with-pole seed nonzero" bar) — the decisive signal that the pre-fix bug
   (a fisher structurally unable to reach water) is actually gone, not just
   that the coordinates look right on paper.
4. Every mining row's `config.spot` stays within `MINING_SPOTS[:4]` exactly
   as before — a regression check that the fix didn't disturb the
   already-working mining path.
5. The offline `_prove_spot_fairness`/`_prove_fish_spot_fairness` proofs
   print `True` before any live eval runs, exactly like today's mining-only
   proof does.

Poll-don't-wait: at `--genomes 6`, this is a short (well under an hour)
unattended run — the operator launches it and checks back, not a
multi-hour commitment (that's item 2, below).

### References

`anima2/foundry/evolve.py`, `anima2/foundry/eval.py` (`run_eval_multi`'s
already-shipped `nodes_pool=`), `anima2/live_evolve_gate.py`,
`anima2/profession.py` (`MINING_SPOTS`, `FISHING_SPOTS`),
`tests/test_foundry_evolve.py`, `tests/test_live_evolve_gate.py`,
PHASE6.md item 4 (the `nodes_pool` mechanism this item threads one layer
higher), PHASE6.md item 6 (the loss this item redeems, and its own two
named causes).

### As landed ✅

**What landed, exactly as scoped.** `EvolutionConfig` gained two both-optional
fields — `nodes_pool` (`Scenario.nodes`-shaped) and `fishing_spot_pool`
(shore-stand coords) — both defaulting `None`, so every existing
`EvolutionConfig()` construction is byte-for-byte unchanged. The fix itself
lives in `evaluate_genome` (defense-in-depth, the lowest shared function, so no
future caller can reintroduce the leak by a different path): after resolving
`scenario_id`, it computes `is_fishing = SCENARIOS[scenario_id].nodes is not
None` — a **generic structural** check, never a hardcoded `"fisher"`/`"fishing"`
string (proven by `test_is_fishing_is_structural_not_a_profession_name_string`,
which monkeypatches a third `"angler"` profession onto the `nodes`-bearing
fishing scenario and confirms it routes as fishing purely from the scenario's
structure). A fishing genome resolves its pools ONLY from the fishing-specific
fields (`fishing_spot_pool`/`nodes_pool`, falling back to
`cfg.fishing_spot_pool`/`cfg.nodes_pool`); a non-fishing genome resolves
`effective_spot_pool` exactly as before AND has `effective_nodes_pool` **forced
to `None` regardless of what was passed** — the actual defense-in-depth half: a
`nodes_pool` can now never reach a mining eval by any call path (`Mine`/`Fish`
both read `ctx.memory["harvest_nodes"]` generically, confirmed against
`skills/harvest.py::Harvest._current_node`, so a leaked mining `nodes_pool` would
silently corrupt a mining eval's staging, not just be inert). `default_eval_fn`
gained a `nodes_pool=None` passthrough into `run_eval_multi` (which has accepted
it since item 4) — the last mile of plumbing. `live_evolve_gate.py` gained
`FISH_POOL = tuple(FISHING_SPOTS[:4])`, `_fish_window` (mirroring `_spot_window`'s
exact non-degenerate wraparound, returning matched `(stand_window, nodes_window)`
pairs where each node is `((water_x, water_y, water_z, 0),)`), a
`_prove_fish_spot_fairness` (mirroring `_prove_spot_fairness`), and — the
necessary-not-optional part — **two independent cursors**: the existing mining
`cursor` now advances only on miner rounds and a new `fish_cursor` only on fisher
rounds (the genome's profession is known once `step_fn(archive)` returns, before
`evaluate_genome`). No new CLI flag: the fishing path activates automatically
under `--scenario-pool all`.

**The RED-first regression evidence.** The load-bearing regression
(`test_mining_shaped_spot_pool_never_reaches_a_fisher_eval`) was written against
the CURRENT pre-fix code FIRST and confirmed to fail RED before the fix landed —
a mining-shaped `spot_pool` handed to a fisher genome reached the fisher's
eval-cfg-building call unconditionally:

```
    assert captured["spot_pool"] != mining_pool
E   assert [(2567, 493), (2611, 474)] != [(2567, 493), (2611, 474)]
```

The Minoc mining coordinate `[(2567, 493), (2611, 474)]` reaching a fisher's eval
is the exact routing bug this design pass's own re-read named. After the fix the
same call resolves the fisher's pool from the (unset here) fishing fields, so the
mining pool is never applied — the test goes green. Plus the mirror
(`test_nodes_pool_never_reaches_a_miner_eval` — a `nodes_pool`/`cfg.nodes_pool`
passed while evaluating a miner is forced `None` by any path), the fisher
forward-and-rotate index-aligned test, a cfg-fallback resolution test, a
new-fields-default-`None`/miner-path-unchanged regression pin, and — in
`test_live_evolve_gate.py` — `_fish_window` matched-pair/wraparound/non-degeneracy
proofs and two `_prove_fish_spot_fairness` arithmetic proofs (one asserting its
counts match `_prove_spot_fairness`'s exactly, anti-drift).

**The live SMOKE gate** (`--ticks 150 --seeds 2 --genomes 6 --scenario-pool all
--cognition-provider stub --suffix phase7item1smoke`, real ServUO, fresh
accounts) passed all five decisive checks, read from a FRESH subprocess parsing
`data/eval_resultsphase7item1smoke.jsonl` (24 rows: 8 fishing, 16 mining):

1. **Every fishing row's `config.spot` ∈ `FISHING_SPOTS[:4]` stands, never a
   `MINING_SPOTS` coord.** All 8 fishing rows landed at `(2866, 647)` /
   `(2869, 639)` / `(2876, 636)` / `(2894, 636)` — each with its **matched water
   node** in `config.nodes` (`((2865, 646, -5, 0),)`, `((2868, 638, -5, 0),)`,
   `((2873, 633, -5, 0),)`, `((2898, 632, -5, 0),)` respectively), index-aligned.
   The direct, cross-process proof the fisher-at-mining-coord leak is gone.
2. **≥2 distinct fishing stands** — in fact all four appeared (`fish_cursor`
   rotated cleanly through the whole pool).
3. **≥1 fishing row materially nonzero `produce_value_rate`** — all 8 were
   nonzero (`[331.9, 165.8, 276.4, 281.1, 280.9, 224.6, 225.0, 225.2]`); no bank
   starved, which is the whole point of the rotation fix.
4. **Every mining row's `config.spot` ∈ `MINING_SPOTS[:4]`** (all 16), each with
   `config.nodes = None` — the defense-in-depth forcing confirmed live, and the
   already-working mining path undisturbed.
5. **Both offline fairness proofs printed `True` before any live eval**
   (`spot_fairness_ok = True`, `fish_spot_fairness_ok = True`, each 3/3/3/3 across
   its four spots).

The decisive live moment is EVO round 4: `op=op_profession genome=g_00005
prof=fisher spots=[(2876, 636), (2894, 636)] nodes=[((2873, 633, -5, 0),),
((2898, 632, -5, 0),)]` — a MAP-Elites mutation swapping a miner elite into a
fisher, staged at fishing stands with matched water nodes rather than the Minoc
ridge it would have hit pre-fix. Infrastructure gate PASSED
(`spot_fairness_design_ok`, `fish_spot_fairness_design_ok`,
`kill_switch_live_proven`, kernel-guard offline-proven, no early halt, per-cell
elite recompute all `True`); enrichment sanity PASSED (`both_professions_sampled
= True`). The comparative verdict came back `RANDOM WON` (margin −28.0, outside
the noise band) — **expected and irrelevant to item 1**: this is item 1's own
SMOKE at a tiny 6-genome budget, not item 2's decisive larger-budget rerun; item
1's job is the routing/infrastructure correctness above, which held completely,
not the evolution-vs-random verdict (that is item 2). 648 tests green (up from
637 — 6 new in `test_foundry_evolve.py`, 5 new in `test_live_evolve_gate.py`;
the 7 pre-existing evolve stub `eval_fn`s gained a mechanical `nodes_pool=None`
param forced by the new passthrough, assertions unchanged), ruff clean.

---

## Item 2 — The decisive evolution-vs-random redemption rerun, larger budget ⏳

**Redeem the second diagnosed cause of item 6's loss — exercise item 1's fix
at item 6's own full scale, with a larger budget.** No new mechanism: this
item is a CLI invocation of the now-fixed `live_evolve_gate.py`, at a bigger
`--genomes` budget than item 6's own 8. PHASE6.md item 6's own words: "8
genomes is far too small for MAP-Elites to exploit — only five mutations
after 3 bootstrap seeds." 20 genomes = 3 bootstrap seeds + 17 mutation
steps — a real, not cosmetic, increase in MAP-Elites' compounding room.

### Scope

Files: `anima2/live_evolve_gate.py` (no code change expected beyond what item
1 already added — this item is a CLI invocation, not a code item),
`tests/test_foundry_evolve.py` (one new offline sanity test), `docs/PHASE7.md`
(record the outcome here, honestly, exactly as PHASE6.md item 6 did).

- **Budget:** `--genomes 20` (2.5x item 6's 8); ticks/seeds unchanged (200/2)
  to keep wall-clock growth roughly linear and bounded.
- **Offline:** one new test bumping the existing stubbed convergence-test
  harness (`tests/test_foundry_evolve.py::
  test_convergence_evolve_beats_random_search_baseline_same_budget` and/or a
  sibling) to `max_genomes=20` — a cheap sanity pin confirming nothing
  structural breaks at that scale (`_drive`'s `for n in range(cfg.
  max_genomes)` loop already generalizes to any value; this is not new
  logic).
- **Persistence:** reuses existing schemas (`data/archive_evolve_gate*.jsonl`
  / `data/archive_random_gate*.jsonl` / `data/eval_results*.jsonl`) under a
  fresh `--suffix phase7redeem`; no new fields.

### Key design decisions

- **A rerun exercising item 1's fix at scale, not a new mechanism.**
  `_drive`, the reliability-discounted promotion rule, and the kill switch
  are untouched — this item exercises the (now-fixed) genome-generation and
  eval-staging surface at a larger budget, nothing else.
- **Wall-clock cost, named explicitly rather than left implicit** (the
  house style this item adopts as its own template for any future
  larger live-gate rerun): 20 genomes × 2 seeds × 2 arms (evolve + random) =
  **80 seed-evals**, versus item 6's 32 — 2.5x the live-shard time.
  PHASE5.md item 2's own bare-`Mine()` gate (no cognition) ran 12 evals in
  ~9 live minutes (~45s/eval); this item's cognition-aware `replicate` evals
  (item 6's own budget shape, unchanged) run measurably slower — every eval
  periodically calls a real Replicate qwen model on top of the tick loop —
  so a conservative, honestly-labeled estimate is a **multi-hour, single-
  sitting** live-shard commitment, not the ~9-minute pace of a bare mining
  gate, entirely against the single `hulryung` GM account
  (`MAX_CONCURRENT_EVALS=1`, strictly sequential — no way to parallelize
  this without a second GM account, which this project doesn't have). The
  operator launches it and polls for completion — never sits blocking on
  the terminal — matching this project's own poll-don't-wait discipline for
  long-running live gates.
- **Honest about the outcome, not just the process — restated because it's
  the whole point of this item, not a formality.** The infrastructure gate
  (below) is an unconditional pass bar, exactly as item 6's own. The
  comparative verdict is reported as whatever it actually is: a decisive win
  is the hoped-for outcome (it's what this item exists to test for), but a
  tie — or even another loss — is a legitimate, publishable result under
  this project's own "report a tie/loss as such" discipline
  (PHASE5.md item 4, PHASE6.md item 6). This item's job is removing the two
  (now three) named confounds, not guaranteeing a particular verdict.

### Offline tests (planned)

One new test in `tests/test_foundry_evolve.py`: the existing stubbed
convergence-test harness re-run at `max_genomes=20` (instead of its current
smaller fixture budget) completes without error and produces a strictly
larger `records` list than at the smaller budget — a cheap structural pin,
not a new claim about convergence quality (the existing convergence test
already covers that at its own fixture scale).

### Live verification gate

`python -m anima2.live_evolve_gate --ticks 200 --seeds 2 --genomes 20
--scenario-pool all --cognition-provider replicate --suffix phase7redeem`,
fresh accounts throughout, item 1's fixed pool routing active, real qwen
throughout.

- **Infrastructure gate (unconditional pass bar, same five flags item 6
  required, now checked PER POOL):** spot fairness — checked
  INDEPENDENTLY for the mining-spot subsequence AND the fishing-spot/node
  subsequence (each pool's own cursor advances only on its own profession's
  rounds, per item 1's fix — each subsequence's own fairness must hold on
  its own terms, not as one blended mining-only check the way item 6's gate
  checked it) — plus `kill_switch_live_proven`, `kernel_guard_offline_
  proven_live_skipped_per_item2_precedent`, `run_completed_without_
  early_halt`, `per_cell_elites_recompute_matches`.
- **Enrichment sanity (unconditional, same as item 6):** both `miner` and
  `fisher` genuinely sampled; `sociability_bins` show variety above `"low"`.
- **NEW sanity this item adds, differential against item 6's own historical,
  already-committed numbers (not re-invented):** this run's fisher genomes'
  per-seed fitness floor is materially higher than item 6's own recorded ~4-
  fitness floor for fisher evals — the decisive proof item 1's fix mattered
  at full scale, not just in the item 1 smoke test.
- **Comparative verdict, reported honestly either way:**
  `Archive.best_by_reliability()` evo-best vs. rand-best, margin vs. the
  data-derived noise band (`2 × pooled per-genome per-seed pstdev`, the exact
  formula items 5/6 already used, not re-invented). A decisive win is the
  hoped-for outcome; a tie or even another loss is a legitimate, publishable
  result under this project's own discipline — this item's job is removing
  the named confounds, not guaranteeing a particular verdict.
- **Cross-process, independently recomputed** from the raw archive rows read
  fresh from disk, exactly as items 4/5/6 required.
- `docs/PHASE7.md` and `CLAUDE.md`'s changelog entry record the exact
  outcome, whichever way it lands, when this item actually runs.

### References

`anima2/live_evolve_gate.py`, `anima2/foundry/evolve.py`,
`anima2/foundry/archive.py` (`Archive.best_by_reliability`),
`anima2/foundry/eval.py`, PHASE5.md item 4 (the gate shape/verdict formula
this item reuses), PHASE6.md item 6 (the loss this item redeems, and its
own two named causes — a larger budget is the second one), item 1 above (the
fix this item exercises at scale).

---

## Item 3 — Skill-ledger provenance: an independent-channel corroboration check for self-reported skill/profession rewards ⏳

**Close the DESIGN.md §11-named, standing open item** — verified directly
against both DESIGN.md §11 and `skill_library.py`'s own module docstring:
"the ledger's `reward` field is the agent's own computed `SkillResult.
reward`... **not** an independently GM-verified channel. Weaker than
DESIGN.md A6's 'agents can't lie' standard... flagged for a cheap partial
mitigation, not solved." This item is that cheap partial mitigation. It
reuses Phase 5 item 1's already-shipped, already-live-verified independent
channel wholesale — no new safety mechanism, just a new call site.

### Scope

Files: `anima2/skill_library.py` (additive only — zero change to the
existing `REGISTRY`/`record_outcome`/`stats` shape or its own tests),
`anima2/live_skill_ledger_provenance.py` (new standalone gate driver,
mirroring `live_fitness_gate.py`'s structure directly — `foundry/eval.py::
run_eval`'s own shape: one staged agent, one recorder, one fixed window;
deliberately NOT wired into `village.py`'s concurrent multi-agent roster
in this item — see "Key design decisions" below), `tests/
test_skill_library.py`.

**New `skill_library.py` additions (all additive — the existing `REGISTRY`/
`record_outcome`/`stats`/`_ledger_lock`/`_DEFAULT_LEDGER` shape is untouched
byte for byte):**

- `CORROBORATION_LEDGER_PATH = Path("data") / "skill_ledger_corroboration.jsonl"`
- `record_corroboration(agent_key, profession, self_reported, independent, *,
  ts=None, path=CORROBORATION_LEDGER_PATH) -> dict` — appends `{ts,
  agent_key, profession, self_reported, independent, ratio, flagged,
  tolerance}` under a new `_corroboration_log_lock` (mirrors `_ledger_lock`
  exactly).
- `read_corroboration(path=CORROBORATION_LEDGER_PATH) -> list[dict]`
  (corrupt-line-tolerant, mirrors `_read_ledger`).
- `flagged` — a COARSE, ONE-DIRECTIONAL adversarial-detection gate, not a
  calibrated trust score (deliberately boolean, unlike `foundry/
  fitness.py::viability_gate` — verified directly against that module:
  `viability_gate` is itself a continuous `float` in `[0, 1]`, the product
  of three continuous factors (`gate = alive * liveness * (1.0 -
  loop_pen)`) that SCALES `total = gate * inner` rather than admitting or
  rejecting anything pass/fail. `flagged`'s boolean shape is a deliberate
  choice for a narrower job: a single "is this self-report implausible"
  verdict, not a magnitude anything downstream needs to weight by — not a
  precedent this item is claiming to follow): `flagged = self_reported >
  independent * 3.0 + 5.0` (a generous multiplier+floor, documented in the
  docstring as intentionally loose). Only over-claiming is the risk "agents
  can't lie" cares about, so there is no symmetric under-claiming check.
- `skill_library.py` **stays** in `test_foundry_import_guard.py`'s protected
  module list (verified directly against that test file: `curriculum.py`,
  `skill_tuning.py`, `cognition.py`, `skill_library.py`, and every
  `anima2/skills/*.py` file) — so `record_corroboration`/`read_corroboration`
  take plain floats/strings as parameters and never import
  `anima2.foundry.trajectory`/`anima2.foundry.fitness` themselves. The
  `TrajectoryRecorder`/`GmControl` usage that PRODUCES the `independent`
  number lives entirely in `live_skill_ledger_provenance.py` (outside the
  guard, computing both numbers and then calling `skill_library.
  record_corroboration(...)` with plain floats) — the same separation of
  concerns `live_fitness_gate.py` already models for `foundry/fitness.py`.

**The independent signal, precisely — CHANNEL (A), not (b).** Deliberately
NOT `foundry.fitness.compute_fitness(...).produce_value_rate` — PHASE6.md
item 4's own "As landed" section documents that field as **channel (b)** (an
in-process `TappedBody` observation tap, "protected only by the static
import-graph guard... not true process isolation," per `trajectory.py`'s own
module docstring — corroborating only in a single interpreter). Using it here
would quietly weaken exactly the "agents can't lie" property this item exists
to add. The independent signal is `foundry.trajectory.TrajectoryRecorder`'s
**channel (a)** — a `SkillStat.gain` (the server-reported skill-base delta,
read via `[Get Skills.<Name>.Base` over a SECOND, separate `control.
GmControl` connection the measured agent's own code has no write path to) —
the exact load-bearing signal Phase 5 item 1's own gate used to rank the
honest miner over the rigged one (Mining 35.0→36.3, server-read). This item's
own live gate deliberately uses a **miner**, not a fisher: `Mine`'s reward is
computed from skill-base gain (`skills/harvest.py::Harvest.step()`, `reward =
base - prev`) — the same quantity channel (a) reads independently, a clean,
apples-to-apples corroboration. `Fish`'s reward is direct catch count, and
Fishing's own skill base "gains very slowly" within an eval-sized window
(a `skills/harvest.py` comment on the `Fish` reward, corroborated by
PHASE6.md item 4's own finding) — a poor corroboration pairing,
deliberately not used here.

**The self-reported signal** is the SUM of that session's own `SkillResult.
reward` values for `Mine.name` ("mine") under `profession="miner"`, already
computed by `skill_library.py::record_outcome`'s existing ledger — `Agent
(skill_library=..., profession=...)` already calls `record_outcome` on every
skill result (`agent.py`, verified) — summed by the gate script over that
LEG's own dedicated ledger file, not a new field on the existing schema (see
"Key design decisions" for why a per-leg ledger file, not a new `agent_key`
column on the existing ledger).

**Adversarial fixture (mirrors `live_fitness_gate.py::_RiggedSkill` closely,
but must do the SAME real work as the honest leg, not different busy-work —
the point here is narrower than Phase 5 item 1's own fixture):** a thin
`Skill` subclass wrapping `Mine` — `step()` delegates to `super().step(ctx)`
for the real action/status/mining, then returns a `SkillResult` with the
SAME action/status but a `reward` inflated by a large constant (e.g.
`real_reward * 50 + 500`) — so channel (a)'s Mining-gain reading is
UNCHANGED between the honest and adversarial legs (the same real mining
happened, the same real world), while the self-reported ledger sum diverges
wildly. Both legs use the `stub` cognition provider (deterministic, no live
LLM) — this proof needs zero LLM involvement, removing qwen flakiness as a
confound entirely, matching how `live_fitness_gate.py`'s own scenario also
needed no cognition.

**Sequencing.** Legs run SEQUENTIALLY, one agent (and its own body + GM
connection pair) at a time — mirrors `foundry/eval.py::run_eval`'s own shape
exactly: one `IpcBody` and one `GmControl` connection open CONCURRENTLY for
the duration of that one agent's own session (`TrajectoryRecorder.start()`
before ticking, `.finish()` after — the established channel-(a) pattern,
never a new "wait until the body closes first" mechanism), both closed
before the NEXT leg's own pair opens. This is what keeps the single-GM-
account/`MAX_CONCURRENT_EVALS=1` discipline every other live gate in this
project honors, and is why this item is a standalone script, not wired into
`village.py`'s own concurrent multi-agent roster.

### Key design decisions

- **Reuses Phase 5 item 1's load-bearing channel (a), corrected to never be
  channel (b).** Stated explicitly because it's the one place this design
  could have quietly failed its own stated goal: had this item used
  `produce_value_rate` (an easy, tempting choice, since it's already a
  persisted `EvalResult` field), the whole "agents can't lie" property would
  rest on a signal PHASE6.md item 4 itself documents as corroborating-only
  in a single interpreter — not the mistake this item makes.
- **A per-leg ledger file, not a new `agent_key` column on the existing
  schema.** `skill_library.py::record_outcome`'s existing record shape
  (`{ts, skill_name, profession, reward, status, param, param_value}`) has
  no per-agent identity field — adding one would be a real schema change to
  an already-shipped, already-tested ledger. Each leg instead constructs its
  own `SkillLibrary(ledger_path=<leg-specific path>)` (mirrors
  `live_chronicle.py`'s own "give every retry attempt its own ledger file"
  precedent) — the self-reported sum for a leg is simply every row in that
  leg's own dedicated file, no new field needed, "additive only" preserved
  exactly.
- **`flagged` is boolean and one-directional, not a calibrated trust score.**
  A deliberate departure from `foundry/fitness.py::viability_gate`, not a
  mirror of it — verified directly against that module, `viability_gate` is
  a continuous `[0, 1]` multiplier (`alive * liveness * (1.0 - loop_pen)`)
  that SCALES `total`, never a pass/fail gate itself. `flagged` goes boolean
  here because this item is not claiming to measure HOW MUCH an agent is
  lying, only whether a self-report is implausibly larger than the
  independent channel supports, generously tolerant of honest measurement
  noise (the multiplier + floor).
- **Miner, not fisher, for this item's own live gate.** Fishing's skill base
  barely moves within a session window (documented, above) — using it here
  would make honest sessions look falsely suspicious (near-zero independent
  signal against ANY nonzero self-report). A future extension to other
  professions is possible but not required for this item's own proof.
- **Deliberately NOT wired into `village.py`'s concurrent multi-agent
  roster in this item.** `village.py`'s worker threads run several agents'
  sessions concurrently; a per-agent second `GmControl` connection there
  would need a shared/sequential-readback design this item doesn't attempt
  — named explicitly as a Phase 8+ follow-up (below), not silently left
  unaddressed. This leaves the skill ledger's day-to-day, real village-run
  reward channel exactly as un-independently-verified as it is today — only
  this item's standalone gate proves the mechanism sound at single-agent
  granularity, matching Phase 5 item 1's own original scope, not extending
  it to production village runs.
- **No contract change.** `village.py`/`live_*.py` scripts are outside
  `test_foundry_import_guard.py`'s protected module list (verified directly:
  only `anima2/skills/`, `curriculum.py`, `skill_tuning.py`, `cognition.py`,
  `skill_library.py` are guarded) — `live_skill_ledger_provenance.py`
  importing `anima2.foundry.trajectory` is consistent with existing
  precedent (`live_fitness_gate.py` already does exactly this), and the
  import guard itself stays untouched and green, with `skill_library.py`
  itself remaining foundry-import-free (the point above).

### Offline tests (planned)

`tests/test_skill_library.py` (~5-7 new tests): `record_corroboration`
writes the documented schema; `flagged` computed correctly across a small
table of `(self_reported, independent)` pairs — a small delta stays
unflagged, a `>3x+5` delta flags; corrupt-trailing-line-tolerant read;
append-only; the load-bearing regression pin — the existing `data/
skill_ledger.jsonl`/`record_outcome`/`stats` shape and its own tests are
byte-for-byte untouched (this item never mutates the existing ledger, only
adds a sibling file, and no existing function's signature changes). A
regression pin against `test_foundry_import_guard.py`: `skill_library.py`
still doesn't import `anima2.foundry` after this item lands (the existing
guard test itself needs no change — it already scans this file — but a
`grep`-level check in this item's own test suite that `record_corroboration`/
`read_corroboration` take no `TrajectoryRecorder`/`GmControl`-typed argument
is a cheap, explicit belt-and-suspenders pin).

### Live verification gate

`live_skill_ledger_provenance.py`, fresh accounts, one miner staged
sequentially per leg, area wiped, reusing `live_common.py` conventions
(`wipe_area`, `login_throttle`, `fresh_suffix`, `print_gate_verdict`).

- **Leg A (honest):** an unmodified `Mine()` agent
  (`Agent(body=..., persona=..., planner=Planner([Mine()]),
  skill_library=SkillLibrary(ledger_path=<leg-A-path>), profession="miner")`)
  runs a bounded-tick session (`--ticks 200`, `--cognition-provider stub` —
  matches Phase 5 item 1's own bare mining gate, no cognition needed) while a
  SECOND, concurrently-open `GmControl` connection's `TrajectoryRecorder`
  reads Mining `[Get` at window start and end. After the run: `self_reported`
  = sum of `reward` over every row in leg A's own ledger file for
  `skill_name == "mine"`; `independent` = the recorder's own `Mining` gain.
  `record_corroboration("achA-honest", "miner", self_reported, independent)`
  is called, and a FRESH subprocess reads `data/skill_ledger_corroboration_
  gate_<suffix>.jsonl` from disk and confirms `flagged=False`.
- **Leg B (adversarial, the decisive differential):** identical setup, a
  FRESH account and a FRESH leg-specific ledger file, but the reward-override
  wrapper (above) in place of the bare `Mine()`. Real mining happens
  identically (same real actions, same real world) — the fresh subprocess
  readback confirms `independent` is materially UNCHANGED from leg A's own
  reading (same order of magnitude), while `self_reported` is wildly higher
  (mirrors `live_fitness_gate.py`'s own 300,000-vs-277.5 gap in spirit, now
  scoped to the skill ledger's own weaker channel) — `record_corroboration`
  writes `flagged=True`.
- This directly reproduces Phase 5 item 1's own decisive proof shape
  (self-report ranks the gamer first, the independent channel doesn't), now
  scoped to the skill ledger's own weaker channel. Both legs' verdicts
  reproduced by a fresh cross-process readback, never the live process's own
  memory.

### References

`anima2/skill_library.py`, `anima2/live_skill_ledger_provenance.py` (new),
`anima2/live_fitness_gate.py` (`_RiggedSkill`, the structural precedent this
item's own adversarial fixture adapts), `anima2/foundry/trajectory.py`
(`TrajectoryRecorder`, channel (a)/(b)), `anima2/foundry/fitness.py`
(`viability_gate` — the continuous `[0, 1]` scaling multiplier `flagged`
deliberately departs from, boolean instead, per "Key design decisions"
above),
`anima2/live_chronicle.py` (the per-attempt-own-ledger-file precedent),
`tests/test_foundry_import_guard.py` (the protected-module list this item
respects), DESIGN.md §11 ("Skill-ledger reward independence," the open item
this closes), PHASE4.md item 3 ("flagged for a cheap partial mitigation, not
solved" — this item's own origin), PHASE5.md item 1 (the independent-channel
mechanism this item reuses wholesale).

---

## Item 4 — Situation-relevant insight retrieval — replace unconditional `recent(3)` with relevance ranking, reusing `_textindex.py`'s existing scoring ✅

**Close the literal gap PHASE6.md item 1 left standing.** `ReflectionMemory`
(`memory.py`) has zero retrieval logic beyond pure recency (`recent(n)`), and
`ReflectingCognition.reconsider()` (`cognition.py`, `ctx.insights =
self.insights.recent(3)`) calls it UNCONDITIONALLY every cadence cycle,
regardless of what the agent is currently doing. This is deliberately NOT the
deferred "full Smallville memory-stream" (importance × recency × relevance-
WEIGHTED ranking needing a calibrated formula this project has no ground
truth for, per PHASE6.md's own stated reason for deferring it, and per
DESIGN.md §11's own precedent against inventing un-cited free parameters —
`foundry/fitness.py`'s own locked, v1-cited weights are the model for what a
groundable formula looks like, and this project has no such citation for a
memory-stream importance/decay rate) — it is a strict, narrower move: reuse
the SAME keyword-relevance scoring `_textindex.py` already provides and
`skill_library.py::retrieve()`/`wiki.py::Wiki.search()` already ship with, as
a ranking key, with recency as only a tie-break — zero new weights, zero new
decay rate, nothing to calibrate that isn't already calibrated (by virtue of
already being live-shipped in two other places) or trivially deterministic
(a tie-break).

### Scope

Files: `anima2/memory.py`, `anima2/cognition.py`, `tests/test_memory.py`,
`tests/test_cognition.py`.

1. `ReflectionMemory` gains `relevant(query: str, k: int = 3) -> list[Insight]`:
   scores every stored `Insight.text` against `query` using `_textindex.py`'s
   existing functions **imported directly, never reimplemented** —
   `weighted_terms((insight.text, 1))` (a single, equally-weighted field —
   there's no title/description split for a one-sentence `Insight`, unlike
   `wiki.py`'s title >> body weighting or `skill_library.py`'s name >>
   description weighting) scored against `_terms(query)` via `score_terms`.
   Ties broken by recency (deque position, most-recent-first among equal
   scores). **The safety net:** if `score_terms` returns `0` for every
   stored `Insight` (an empty query, a stub/garbage query, or genuinely no
   topical overlap — `score_terms`'s own documented contract: `0` means "no
   overlap at all"), `relevant()` falls back to returning `recent(k)`
   **exactly, byte for byte**. This makes the method a strict, provable
   superset of today's behavior: never worse than the recency baseline when
   it can't determine relevance, and every existing caller passing no query
   (or an empty string) reproduces `recent(k)` unchanged.
2. `ReflectingCognition.__init__` gains `insight_retrieval: Literal["recent",
   "relevant"] = "recent"` (default preserves today's exact behavior,
   zero-behavior-change like every optional collaborator before it —
   `wiki=None`, `skill_library=None`, `wiki_reporter=None`, `insights=None`,
   all mirrored). When `"relevant"`, `reconsider()` builds a query string
   **CODE-composed** (never LLM-composed, matching `LLMWikiReportProducer`'s/
   `compose_post_llm`'s established "code computes the fact" discipline)
   from `ctx.goal.kind`/`ctx.goal.params` (when `ctx.goal` is set — it is
   often `None`, e.g. a bare work-skill planner with no cognition-set goal
   yet, in which case the query is built from episodes alone) plus the
   `.summary` text of the last 2-3 entries already in `ctx.episodes` — no new
   LLM call anywhere in this item — and calls `self.insights.relevant(query,
   k=3)` instead of `recent(3)`.

### Key design decisions

- **`_textindex.py`'s scoring is imported, not reimplemented — pinned
  byte-for-byte, not just claimed in a docstring.** A dedicated offline test
  asserts `relevant()`'s own internal score for a fixture `Insight` equals
  `score_terms(_terms(query), weighted_terms((insight.text, 1)))` called
  directly against `_textindex.py` — the anti-drift technique that catches a
  future refactor that "helpfully" starts reimplementing the scoring inline
  instead of calling the shared function (the same discipline
  `test_skill_library.py::test_registry_covers_every_exported_skill` already
  applies to a different drift risk in this codebase).
- **Fallback-to-`recent(k)` is the safety net, not a nicety.** It bounds this
  item's worst case to "no worse than today," never "solved" — keyword-
  overlap-only relevance can miss a genuinely relevant insight that shares no
  vocabulary with the composed query; the explicit, tested fallback is the
  honestly-named limit of what this item claims.
- **Query composed from `ctx.goal`/`ctx.episodes`, never from an LLM call —
  restated because it's the load-bearing safety property here too.** The
  LLM never gets to choose WHICH insights are relevant by writing the query
  itself; code does, mirroring the "code composes the fact, the LLM only
  narrates" discipline `LLMWikiReportProducer`/`_chronicle_grounding_line`
  already established.
- **A second seam alongside `LLMCognition._situation`'s existing "Lessons
  learned" line and `forum.py`'s existing grounding line — explicitly
  tested for composability, not assumed safe by construction.**
  `ReflectingCognition.reconsider()` sets `ctx.insights` BEFORE
  `LLMCognition._situation` ever reads it (`learned = " | ".join(str(i) for
  i in ctx.insights[-3:])`) — this item changes WHICH `Insight` objects
  populate that list, never the string-building code itself, so no new
  seam is introduced by construction; but because a future phase could add a
  genuinely second, independent hook onto this SAME `_situation`/`forum.py`
  grounding-line seam, this item adds one small, explicit regression test
  proving `relevant()`-selected insights reach `_situation`'s prompt with no
  interaction/clobbering (reusing PHASE6.md item 3's own `session2`
  assertion shape) — a template for whenever a later phase lands a second
  hook alongside this one.
- **No contract change.** This item is a pure, in-memory re-ranking function
  over already-persisted, already-hand-written `Insight` text — never code,
  never an LLM call. `contract.py` is untouched.

### Offline tests (planned)

`tests/test_memory.py` + `tests/test_cognition.py` (~4-6 new tests):

- **Non-vacuous differential:** seed a `ReflectionMemory` with ~20 `Insight`s
  where a few OLDER ones are topically about mining ("the east vein pays
  better in the morning", "ore is richer near the ridge at dawn") and the
  MOST RECENT ones are about something else entirely (vendor prices).
  `relevant("mining ore", k=3)` returns the older, topical insights;
  `recent(3)` on the SAME fixture returns the most-recent, off-topic ones —
  the non-vacuous proof that relevance genuinely beats pure recency, not a
  restatement of it.
- **Fallback proof:** `relevant("zzz nonsense no matches", k=3)` returns
  byte-for-byte the same list as `recent(3)` on the identical fixture —
  proves graceful degrade to the safe baseline, not silent failure.
- **Anti-drift pin:** `relevant()`'s own per-`Insight` score equals
  `score_terms(_terms(query), weighted_terms((insight.text, 1)))` computed
  directly against `_textindex.py`, for a small fixture table.
- `ReflectingCognition(insight_retrieval="recent")` (the default) is
  byte-for-byte unchanged behavior against the existing test suite —
  regression pin.
- **Query-composition test:** `insight_retrieval="relevant"` with a
  hand-built `ctx.goal`/`ctx.episodes` produces the expected composed query
  text (a stub that captures the query passed to `relevant()`), proving the
  "code, not LLM, composes the fact" property directly.
- **Seam-composability test:** a fixture `ctx.insights` set by `relevant()`'s
  own selection reaches `LLMCognition._situation`'s "Lessons learned" line
  with no other prompt content altered — mirrors PHASE6.md item 3's own
  `session2` assertion shape.

### Live verification gate

Mirrors `live_persistent_lives.py`'s shape directly — a scripted reflection
client, fresh accounts, wiped area. Pre-seed `data/insights_gate_<suffix>.
jsonl` (via `load_insights`-compatible writes, matching the documented
schema) with, for a single `agent_key`:

- one OLD, topically mining-relevant insight (early `ts`/`episode_ticks`),
- one MORE RECENT, off-topic (e.g. vendor-price) insight (later `ts`/
  `episode_ticks`).

Run two sessions on a mining goal, staged identically, differing ONLY in
`insight_retrieval`:

- **Session A** (`insight_retrieval="relevant"`): capture the FIRST
  `reconsider()` prompt handed to the (stub/capturing) inner cognition
  (mirrors PHASE6.md item 1's own first-tick-prompt capture) — confirm it
  carries the OLD, topical insight text.
- **Session B** (`insight_retrieval="recent"`, today's default) on the
  IDENTICAL seeded ledger: confirm its first prompt instead carries the
  newer, off-topic insight.

This is the decisive live differential: same disk state, same goal, only the
retrieval mode differs, and the two sessions' captured prompts provably
diverge. Provenance: prompt text captured in-process is sufficient here
(mirrors PHASE6.md item 3's own `session2` leg rationale — the property
under test is prompt construction, not a GM-verifiable reward), cross-checked
against the SAME `data/insights_gate_<suffix>.jsonl` read fresh from disk by
both sessions before either runs (confirming both rows are present and
attributed to the same `agent_key`, so the divergence in Session A vs.
Session B's prompts is due to the retrieval MODE, not a difference in what
was actually persisted). Whether either session needs a real live ServUO
connection at all, or can construct its `ctx` purely in-process/subprocess
(mirroring PHASE6.md item 3's own `session2` leg, which needed zero live
connection for an identical prompt-construction claim), is the implementer's
own choice at landing time.

### As landed ✅

`ReflectionMemory.relevant()` ranks insights with the shared `_textindex.py`
tokenizer and scorer, using newest-first ordering only to break equal scores.
A zero-overlap or empty query returns `recent(k)` exactly, preserving the old
behavior as the safety baseline.

`ReflectingCognition` accepts `insight_retrieval="recent" | "relevant"`; the
default remains `"recent"`, while the opt-in mode composes its query in code
from the current goal and last three episode summaries. No LLM call, file I/O,
fast-loop work, or contract change was added. The prompt-construction property
was verified offline, as explicitly permitted by the gate above: the same
memory containing an older mining insight and a newer vendor insight feeds the
mining insight into `LLMCognition` only in relevant mode. Five new regression
tests cover topical-vs-recent selection, exact fallback, shared-scorer reuse,
deterministic query composition, and the existing prompt seam. Full baseline:
**653 tests green, Ruff clean**.

### References

`anima2/memory.py`, `anima2/cognition.py` (`ReflectingCognition`,
`LLMCognition._situation`'s "Lessons learned" line), `anima2/_textindex.py`
(`score_terms`/`weighted_terms`/`_terms`, imported not reimplemented),
`anima2/skill_library.py::retrieve()` and `anima2/wiki.py::Wiki.search()`
(the two prior call sites this item's scoring is already calibrated by
precedent from), `anima2/live_persistent_lives.py`,
`anima2/live_forum_chronicle.py` (`session2`'s zero-live-connection prompt-
construction precedent), PHASE6.md item 1 (the literal gap this item
closes), PHASE6.md's "Notes carried into Phase 7" (the fuller memory-stream
follow-up this item deliberately does not attempt).

---

## Out of scope this phase (decided and justified, not silently dropped)

- **LLM-authored skill DSL with sandboxing** — still out of scope, explicitly
  gated on item 2's verdict (see the intro above and "Notes carried into
  Phase 8" below). Doing it now would mean building a new sandbox before its
  own stated prerequisite (config-space evolution proven to beat random,
  live) is satisfied — exactly the kind of speculative work this phase's own
  minimal-coherent scope is meant to avoid.
- **Hunter/blacksmith `Scenario` entries** (PHASE6.md item 4's own deferred
  follow-up, restated in its "Notes carried into Phase 7") — still deferred.
  A hunter scenario is now understood to be lower-risk than previously
  scoped (`GmControl.command_at`'s `[Add`-a-creature primitive is already
  live-proven, `village.py`/`live_hunt.py`), but adding it is still a
  genuinely new eval-harness surface (creature staging, a combat-disposition
  concept `Scenario` doesn't have yet), and this phase's four items already
  fill its own "lands in a day or two, each" bar without it. Named as the
  next follow-up after item 2 lands, not attempted here — see "Notes carried
  into Phase 8."
- **A full Smallville-style memory-stream retrieval** (importance/recency/
  relevance-WEIGHTED ranking over raw episodic memory) — deferred, for the
  same reason PHASE6.md deferred it: no calibrated importance/relevance
  formula to port from (unlike `foundry/fitness.py`'s locked, v1-cited
  weights), and item 4's narrower keyword-relevance move sidesteps the
  calibration problem entirely rather than fencing it in.
- **Continuous, decaying relationship-strength scores** over `chronicle.py`'s
  raw event tallies — deferred, unchanged from PHASE6.md's own reasoning:
  there's still no ground truth to calibrate a decay rate against, and a
  simple, auditable count remains strictly more verifiable for this
  project's own live gates.
- **A second GM account / `MAX_CONCURRENT_EVALS > 1`** — unchanged from
  Phase 5/6; this project's persistent shard still has exactly one GM
  account (`hulryung`), and nothing in this phase's scope needs concurrent
  evals (if anything, item 2's larger budget makes the single-account
  constraint MORE load-bearing, not less).
- **Running the measured eval agent as a separate OS subprocess** (the full
  v1-grade "channel (b) becomes mechanically independent" upgrade
  PHASE5.md item 1 named as a future path) — still not required; channel
  (a) remains the load-bearing independent signal for every item in this
  phase, including item 3's new call site.
- **Wiring item 3's corroboration mechanism into `village.py`'s concurrent
  multi-agent roster** — explicitly out of scope this phase (see item 3's
  own "Key design decisions"); needs a shared/sequential-readback design
  this item doesn't attempt.
- **Widening item 1's fishing pool beyond `FISHING_SPOTS[:4]`** — deferred
  until a quick live smoke-test of `[4:8]` confirms they're viable the same
  way `[0..3]` already are; blind inclusion risks betting a larger budget
  (item 2) on untested geometry.
- **Cost-tier budgets derived from curriculum/task difficulty** — still
  deferred, carried unchanged since Phase 4.

---

## Notes carried into Phase 8

- **The LLM-authored skill DSL — the named next escalation, EXPLICITLY gated
  on item 2's redemption rerun actually proving evolution beats random,
  live.** If (and only if) item 2 reports a decisive win (margin outside the
  data-derived noise band, in evolution's favor), the DSL becomes the honest
  next step PHASE6.md's own carried note names; if item 2 reports a tie or
  another loss, the DSL's own stated precondition is STILL not met, and the
  next step is instead a further redemption attempt (a richer eval harness —
  hunter/blacksmith Scenarios, below — or a still-larger budget), not the
  DSL. The design itself, sketched here as a real, not hand-waved, shape
  worth carrying forward wholesale once the precondition is met:
  - A **closed vocabulary** built directly off `skill_library.py::REGISTRY`
    (anti-drift enforced by `test_skill_library.py::
    test_registry_covers_every_exported_skill`, which already fails loudly
    if `REGISTRY` and `skills/__init__.py::__all__` drift) — an LLM-authored
    "program" never names an arbitrary Python callable, only a `SkillEntry.
    name` already in the registry.
  - A **non-Turing-complete, ≤6-step, tick-capped straight-line grammar** —
    no loop/branch primitives, so termination is trivial and the interpreter
    is a fixed, hand-written sequencer, never `eval`/`exec`/`compile`
    (verified: zero such calls exist anywhere in this codebase family today
    — a Phase 8 DSL would be the first).
  - **Params whitelisted onto existing `skill_tuning.py` candidate grids** —
    never a free float an LLM could set to an unbounded/adversarial value.
  - An **AST/substring guard test** mirroring `test_foundry_import_guard.py`'s
    own precedent — a regression-pinned proof that no `eval`/`exec`/
    `compile`/`subprocess`/`socket` token reaches the interpreter's own
    source, the same load-bearing style this project already uses for the
    kernel-import guard.
  - A **differential admission rule**: a synthesized program is admitted
    only if it TIES-OR-BEATS a negative-control baseline of its own first
    step's underlying skill run alone, under the SAME noise-band convention
    `live_evolve_gate.py` already uses (items 1-2's own formula) — never a
    bare positive observation, mirroring this project's own "never
    promote/admit on a bare positive signal" discipline (the exact rule
    `Archive.best_by_reliability()` already applies to genome promotion,
    extended to program admission).
  - The eventual live safety gate's own template: a **negative-control
    payload proven to touch zero live ticks** before any `GmControl`/`Agent`
    object is even constructed — the same "prove the dangerous path never
    executes" shape this project's own kernel-integrity guard
    (`assert_kernel_clean`) and kill-switch proofs already use.
  - The Action surface (`contract.py`'s closed, typed dataclass enum) stays
    the only effect surface any synthesized program can reach the live game
    through — a synthesized "skill" only ever sequences/parameterizes
    EXISTING `skill_library.py::REGISTRY` `Skill` subclasses, never
    LLM-generated Python, so even a maximally adversarial program's worst
    case is bounded to "plays badly / wastes ticks," never a new capability
    to touch a socket, file, or subprocess.
- **Hunter `Scenario`** — the next follow-up after item 2 lands, reusing the
  already-live-proven `GmControl.command_at('[Add ...')` primitive
  (`village.py`'s forge/anvil staging, `live_hunt.py`'s Mongbat spawning) for
  a `Scenario.spawns`/`combat_disposition`-shaped staging step — a
  genuinely low-invention way to get a third scenario-supported profession
  without new GM plumbing, directly answering PHASE6.md's other named
  carried-forward item.
- **Blacksmith `Scenario`** — a further follow-up, riskier than hunter:
  needs `Scenario.structures` staging (forge/anvil placement,
  `profession.py::Profession.structures`'s shape, not yet mirrored in
  `Scenario`) plus a starved-ingot supply loop design. If attempted, its
  PRIMARY NAMED RISK, worth stating up front rather than discovering
  mid-gate: `CraftGump`-driven crafting has THREE separately-documented past
  live-fragility incidents in this project's own history (a wrong CraftGump
  button, a tool that silently breaks, a proximity-failure CraftGump reshow
  that froze the MAKE loop — all named in PHASE3.md/CLAUDE.md's own
  changelog) — any blacksmith `Scenario` inherits this fragility surface by
  construction and should budget for it explicitly, not assume a gump-driven
  skill behaves as predictably in a fixed-window eval as `Mine`/`Fish` do.
- **A fuller memory-stream retrieval layer** (importance × recency ×
  relevance-weighted, Generative-Agents-shaped) over item 4's narrower
  keyword-relevance move — the natural, larger follow-up once persistent
  `Insight`s accumulate enough real multi-session volume to justify it.
  Should the citation discipline item 4 itself couldn't fully satisfy (no
  calibrated formula to port): cite the paper-shape (Park et al. 2023,
  "Generative Agents") explicitly, and name every project-owned constant
  (a decay half-life, an importance weight) as CHOSEN, not derived — the
  same honest-labeling discipline `foundry/fitness.py`'s own "facts of the
  ruler, ported verbatim, cited to v1" precedent models, applied here where
  there is no prior calibrated source to port from.
- **Decaying/weighted relationship scores** over `chronicle.py`'s raw event
  tallies — deferred for the same reason, unchanged since PHASE6.md's own
  note, until there's real multi-session chronicle data to calibrate any
  such formula against. When eventually attempted: (a) restrict the score
  to COMPARATIVE-ONLY use (ranking/margin comparisons only, never surfaced
  as an absolute number) — mirrors `foundry/fitness.py`'s own posture and
  forecloses the "an invented number means nothing on its own" objection;
  (b) a live gate proving real wall-clock decay arithmetic within one
  sitting will need a FORESHORTENED half-life/decay-constant override
  (exercising the real formula at an accelerated rate for the gate's own
  duration only, explicitly named as a live-gate-only limitation, never a
  claim about the shipped default) — the same technique PHASE6.md's own
  carried notes point toward for any future decay-based feature.
- **Skill-ledger provenance wired into `village.py`'s concurrent roster** —
  item 3's own standalone gate proves the mechanism sound at single-agent
  granularity; extending it to production village runs needs a shared/
  sequential-readback design (one GM connection serving several concurrently
  running agents' corroboration checks without violating
  `MAX_CONCURRENT_EVALS=1`) not designed here.
- **`FISHING_SPOTS[4:8]`** — untested in any live gate so far; a quick smoke
  test of their own viability (mirroring how `MINING_SPOTS[0..3]` were
  originally calibrated) is the prerequisite for ever widening item 1's
  fishing pool beyond `[:4]`.
- **Cost-tier budgets derived from curriculum/task difficulty** — still
  open, carried since Phase 4.
- **The subprocess-isolation upgrade for trajectory channel (b)** — still
  open, carried since Phase 5 item 1; channel (a) remains sufficient for
  every gate this phase's items — including item 3's new one — design
  around.

---

## References

- DESIGN.md §2 (non-negotiables — reaffirmed per item above), §6 (learning
  ladder — item 3 targets §11's own skill-ledger gap, item 4 the reflection/
  retrieval gap), §10 (roadmap — this phase's entry points here), §11 (open
  decisions — "Skill-ledger reward independence" targeted by item 3;
  "LLM-authored, executable skills" explicitly not attempted this phase, per
  the intro above).
- PHASE6.md — item 6's own "As landed" (the loss this phase's items 1-2
  redeem, and its own two named causes), its "Out of scope this phase" and
  "Notes carried into Phase 7" sections (the DSL's own stated precondition,
  the memory-stream/relationship-score deferrals items 3-4 respect, the
  hunter/blacksmith follow-ups carried into this document's own "Notes
  carried into Phase 8"), item 1 (`ReflectionMemory`'s persistence layer
  item 4 extends with retrieval), item 4 (the `nodes_pool` mechanism item 1
  threads one layer higher).
- PHASE5.md — item 1 (the independent-channel "agents can't lie" pattern
  item 3 reuses wholesale), item 4 (the comparative gate shape/verdict
  formula items 1-2 reuse unchanged).
- `anima2/foundry/evolve.py`, `anima2/foundry/eval.py`,
  `anima2/foundry/archive.py`, `anima2/foundry/trajectory.py`,
  `anima2/foundry/fitness.py`, `anima2/live_evolve_gate.py`,
  `anima2/live_fitness_gate.py`, `anima2/skill_library.py`,
  `anima2/memory.py`, `anima2/cognition.py`, `anima2/_textindex.py`,
  `anima2/wiki.py`, `anima2/live_common.py`,
  `tests/test_foundry_import_guard.py` — the existing modules every item
  above extends.
- Papers/ideas (restated from DESIGN.md §12): Voyager (arXiv 2305.16291 —
  the DSL's own eventual ancestor, deliberately not attempted this phase);
  Generative Agents (Stanford Smallville — memory/reflection/social, the
  direct inspiration for item 4 and the fuller memory-stream follow-up
  carried into Phase 8).
