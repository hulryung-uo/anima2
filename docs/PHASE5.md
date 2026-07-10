# Phase 5 — Work Breakdown

Phase 5 = **the independent-measurement & evolution stack** (DESIGN.md §6.5-6.6,
§10). Phase 4 gave the agent ways to *learn* (wiki writes, tiered cognition, a
skill library, a bandit tuner, a curriculum) — but every one of those signals is
the agent's **own** (`SkillResult.reward`, self-recorded episodes). Phase 4's own
closing notes flag this plainly: the skill-ledger reward is *self-reported*,
weaker than DESIGN.md **A6's "agents can't lie" standard**, which describes v1
Foundry's wire-level, packet-parsed fitness. Phase 5 closes that gap: it builds an
**independent fitness oracle** (ground truth the agent's own code can never
write), a **repeatable eval harness** on top of the Control plane, a **MAP-Elites
archive**, and a small **evolution loop** that improves agent *configurations*
(never LLM-authored code — that stays out of scope, same as Phase 4). It reuses
v1's `../anima/foundry/kernel/` heavily — that kernel is human-owned and
well-designed (DESIGN.md §8 A6), and the fitness/descriptor/archive/safety
modules port with only their signal *source* changed (v1 parses raw packets;
anima2, being contract-based and never touching the wire, sources ground truth
from an **independent GM-read channel** instead).

**Unlike Phase 4, some items here MAY touch the Observation surface** only if the
independent fitness needs a signal the bridge doesn't already expose (e.g. a
GM `[Get`-derived stat readback) — but the first design instinct is to source
everything from the *existing* GmControl primitives and the observation JSON,
and to flag a genuine contract need explicitly (the 4-lockstep checklist,
PHASE2.md) if one surfaces, exactly as Phase 3 items 2-4 each did.

The load-bearing new principle, carried from v1 Foundry and DESIGN.md A6:

> **The ruler is kernel-owned and the agent can never edit it.** Fitness weights,
> descriptor bins, and the eval scenario live in a `foundry/` package the agent's
> learning code (skills, tuners, curriculum, any future skill-DSL) has no write
> path to; the eval harness reverts that package to a pinned state before every
> measurement, so a variant that "improves" by editing its own scorer is
> discarded. Fitness is computed from an **independent** channel, never from
> `Agent.episodes`/`SkillResult.reward`.

**Honest note on how independent "independent" is (a real architectural limit,
not hand-waved).** v1 achieves *mechanical*, code-can't-touch-it independence via
**OS-process separation**: `../anima/foundry/kernel/eval.py`'s `run_eval` spawns
the measured agent as a separate `subprocess.Popen`, and a separate `uo_proxy`
process (always launched from the trusted main repo) sits between agent and
server, logging raw packets to a file the agent process has no handle to. anima2's
own live scripts run everything — agent, driver, and the second `GmControl`
session — in **one** Python interpreter. So Phase 5's fitness independence rests
on **two channels of unequal strength**, and the design leans on the strong one:
> - **Channel (a) — the load-bearing one: a separate `GmControl` server
>   connection.** It reads the subject's skills/gold/alive-state by asking the
>   *server* (`[Get`), so it is genuinely independent regardless of process
>   boundary — the agent cannot forge what the server reports about it. This is
>   what the "agents can't lie" claim actually rests on, and it is why hardening
>   `GmControl.get_property` (which returned empty in Phase 4 item 3) is a
>   load-bearing part of item 1, not a nicety.
>
>   *(Hardened ahead of schedule, in a pre-Phase-5 pass — see item 1's own
>   scope note below: `get_property`/`get_property_value` now reliably parse
>   a typed value, live-verified against staged ground truth.)*
> - **Channel (b) — corroborating only: an observation-tap buffer.** In a single
>   interpreter this is protected by the static import-graph guard (below), which
>   catches *accidental* architectural violations but **not** an adversarial or
>   buggy write path (shared object references, monkeypatching, `sys.modules`).
>   Treated as corroboration for (a), never as a hard-independent signal — unless
>   and until a future increment runs the measured agent as a separate subprocess
>   (mirroring v1), at which point (b) becomes mechanically independent too. That
>   subprocess upgrade is noted as the path to full v1-grade independence; this
>   phase does not require it because channel (a) already carries the load.

Status legend: ✅ done · 🚧 in progress · ⏳ todo

**Every item below is ⏳.** Society scale-out (persistent lives, inter-agent
relationships, the forum as village chronicle — the "society" half of DESIGN.md
§10's Phase-5 line) is deliberately carried into a **Phase 6** note at the end:
folding it in here would dilute this phase's single coherent theme (independent
measurement + evolution). Dependency order: item 1 (fitness) → item 2 (eval
harness, needs fitness) → item 3 (descriptor + archive, independent of 1-2) →
item 4 (evolution, needs all three).

---

## Item 1 — Independent fitness oracle ✅

**Close DESIGN.md A6's gap: a fitness signal the agent's own code can never
write.** Ports v1 `../anima/foundry/kernel/fitness.py` (locked kernel-owned
weights, a viability gate, per-hour rates) with its *signal source* swapped from
raw-packet parsing to an independent GM-read + observation-tap channel, since
anima2 never touches the wire.

### Scope

- **`anima2/foundry/` (new package)** — a human-owned "kernel" the agent's
  learning modules never import from and have no write path to. Holds
  `fitness.py`, later `descriptor.py`/`archive.py`/`eval.py`/`evolve.py`. The
  package's own tests assert no module under `anima2/skills/`, `curriculum.py`,
  `skill_tuning.py`, or `cognition.py` imports it (a static import-graph guard —
  the ruler must be unreachable from the learning code, mirroring v1
  `safety.py`'s kernel-integrity intent at the import layer).
- **`foundry/trajectory.py`** — an **independent** recorder: a lightweight
  observer that samples ground truth over an eval window from a channel the
  measured agent does not own. Two sources, both independent of
  `Agent.episodes`: (a) a separate `GmControl` connection issuing `[Get` reads
  of the subject's skills/gold/alive-state at window start and end (the same
  command family `stage()` already uses); (b) a passive tap of the subject's
  own observation JSON stream recorded to a kernel-owned buffer the agent's
  reward logic can't mutate. Produces a `TrajectorySummary` (skill-gain
  totals, gold delta, produced-item value, alive fraction, action counts,
  deny/confirm counts) mirroring v1's `TrajectorySummary` fields. **Channel
  (a) — the GM `[Get` reads — is the load-bearing independent source** (the
  server, not the agent, reports the numbers); channel (b) corroborates but
  is not treated as hard-independent in a single interpreter (see the "how
  independent" note above). **`get_property` hardening — done in a
  pre-Phase-5 pass, not deferred to this item:** item 3 of Phase 4 flagged
  `[Get Gold` returning empty; the root cause was two-fold (`"Gold"` isn't a
  valid ServUO property name — it's `TotalGold` — and the old
  first-non-empty-tick return could miss the actual reply in a noisy scene).
  `control.py` now has `parse_property_reply`/`get_property_value` (a typed
  `float|str|None` readback, collecting across all pumps and picking the
  line that echoes the property name back) — live re-verified against a
  staged character (`Skills.Mining.Base=42.5`, fresh-account
  `TotalGold=1000`, both matched exactly by a second, independent `GmControl`
  connection reading the same character fresh). This item's own trajectory
  recorder can build directly on `get_property_value` rather than
  re-deriving a readback path.
- **`foundry/fitness.py`** — a near-verbatim port of v1's `compute_fitness`:
  `fitness = viability_gate × (skill_term + worth_term + produce_term +
  behavior_bonus)`, all per-hour rates, weights (`W_SKILL=1.0`/`W_WORTH=0.3`/
  `W_PRODUCE=0.2`, `GOLD_NORM`, the viability gate = `alive × liveness ×
  (1-loop_penalty)`) **locked as module constants** with a docstring stating the
  agent may not edit them. `_liveness` (anti-freeze: varied real actions) and
  `_loop_penalty` (deny/confirm ratio — a wall-walking proxy) port directly and
  are extra valuable given the known `Harvest`/`Mine` freeze (a frozen agent
  scores ~0 through the gate, which is *correct*).

### Key design decisions

- **Independent by construction, not by promise.** The whole point is that
  fitness is unreachable-by-write from the learning code. The import-graph guard
  test makes that a mechanical invariant, not a convention.
- **Ported weights stay locked and cited to v1.** The weights are "facts of the
  ruler, not of the agent" (v1 `fitness.py`'s own words); porting them verbatim
  (rather than re-guessing) keeps the ranking meaning consistent with the
  well-calibrated v1 kernel.
- **Reuses, doesn't reinvent, the "agents can't lie" mechanism** DESIGN.md A6
  names — the one open gap Phase 4 explicitly left.

### Offline tests (planned)

`tests/test_fitness.py` (new): `compute_fitness` on hand-built `Trajectory
Summary` fixtures reproduces v1's component math (skill/worth/produce terms, the
viability gate) at known inputs; a frozen trajectory (zero confirmed actions)
gates to ~0; a wall-walker (high deny ratio) is penalized; a spammer of one
action doesn't fake liveness (the ≥2-distinct-groups rule). The **import-graph
guard**: a test walks `anima2/skills/*`, `curriculum.py`, `skill_tuning.py`,
`cognition.py`, `skill_library.py` and asserts none imports `anima2.foundry`
(the ruler is unreachable from the learning code).

### Live verification gate

**Differential — the gaming agent vs the honest worker.** The non-vacuous proof
that this fitness is independent of self-report: stage two miners on identical
GM-staged scenarios. Agent A works honestly. Agent B is rigged to inflate its
**own** signal — e.g. its `SkillResult.reward`/skill-ledger entries are padded
(a scripted skill returning large `reward` while doing little real work). Then:

- The **self-reported** signal (each agent's own `episodes.total_reward()` /
  `skill_ledger.jsonl` sum) ranks B **above** A — the gameable metric.
- The **independent fitness** (computed by `foundry/fitness.py` from the
  load-bearing GM `[Get` channel) ranks A **above** B — because B's actual
  skill-gain/gold/produced-value rates, read from the *server's* ground truth
  the agent can't forge, are low.

A fitness that secretly leaned on the agent's own numbers would rank them the
same; the divergence is the proof, and it must hold on the **channel-(a)-only**
fitness (compute it once with channel (b) excluded and show the ranking is
unchanged — that is what proves the load-bearing signal doesn't secretly depend
on the in-process tap). Provenance: the fitness inputs are read back by an
independent `GmControl` connection post-run and cross-checked, the same "fresh
channel, never the live process's own memory" discipline items 3-5 of Phase 4
established.

### As landed (live-verified)

`anima2/foundry/` (`fitness.py` — v1's `compute_fitness` ported with the
locked weights and the "the agent may not edit this module" contract stated in
the docstring; `trajectory.py` — `TrajectoryRecorder` with channel (a) GM
`[Get` window reads via the hardened `get_property_value` as the load-bearing
source and a channel (b) `TappedBody` observation tap as corroboration),
`live_fitness_gate.py`, and three test files (35 tests: component math against
v1's formulas, frozen-trajectory gating, wall-walker penalty, one-action-
spammer liveness rule, and the **AST-level import-graph guard** proving no
module under `anima2/skills/`, `curriculum.py`, `skill_tuning.py`,
`cognition.py`, or `skill_library.py` imports `anima2.foundry`).

**Live gate — PASSED, all three rankings** (`python -m
anima2.live_fitness_gate`, fresh accounts, two distinct viable spots so the
subjects share no resource bank, fixed 300-tick windows, no early stop):

```
RANKING 1 self-report:        B(rigged) 300,000.0  >  A(honest) 1.3     (the gameable metric)
RANKING 2 independent fitness: A(honest) 277.54    >  B(rigged) 0.00
RANKING 3 channel-(a)-only:    A(honest) 36.92     >  B(rigged) 0.00    (ranking unchanged)
```

The rigged agent (a scripted skill self-reporting 1,000 reward per tick while
emitting junk walk/speech actions) tops the self-reported ledger by five
orders of magnitude — and scores **zero** on the independent fitness: the
server-read Mining gain is 0.00, and its 225 denied moves drive the loop
penalty to 1.0, zeroing the viability gate. The honest miner's Mining
35.0→36.3 (server-read) carries it. The post-run cross-check runs on a FRESH
GM connection **while the subjects are still online** (a logged-out mobile
can't be `[Get`-read — the first gate run's post-run check hit exactly that;
the script now owns the subject sessions until after the cross-check):
B matched exactly (35.0/35.0); A read 36.4 vs the recorder's 36.3 — one gain
quantum, the subject's final in-flight swing resolving server-side after the
window-end read, which is itself evidence the fresh channel reads live server
state rather than any cached snapshot.

Also exercised on the way, honestly noted: two transient live-infra failures
(a bridge broken-pipe at one session's tail; a ServUO login-throttle rejection
when gate runs were launched back-to-back) — both resolved by retry/cooldown,
neither a defect in the oracle.

### References

`anima2/foundry/fitness.py`, `anima2/foundry/trajectory.py`, `anima2/control.py`
(`GmControl` `[Get`/`get_property`, `stage`), `anima2/skill_library.py`
(the self-reported ledger this contrasts against), `../anima/foundry/kernel/
fitness.py`, `../anima/foundry/kernel/trajectory.py`, `../anima/foundry/kernel/
safety.py` (kernel-integrity intent), DESIGN.md A6/§6.5-6.6.

---

## Item 2 — Repeatable eval harness ⏳

**One command → one agent variant, one staged scenario, one independent fitness
number** — the unit of measurement everything above needs. Ports v1
`../anima/foundry/kernel/eval.py`'s `EvalConfig`/`EvalResult`/`run_eval` shape
onto anima2's Control plane.

### Scope

- **`foundry/eval.py`** — `EvalConfig` (scenario id, agent-variant config, window
  seconds, seed) → `run_eval(cfg) -> EvalResult`. Stages a **fixed** scenario via
  `GmControl` (fixed start-at-work location, known skills/items — the same
  precise staging Phase 3 already does, so variance is the variant's, not the
  scenario's), starts the independent `foundry/trajectory.py` recorder, runs the
  agent for a fixed window with **no early stop** (the item-4 lesson: early-stop
  makes windows incomparable), then computes `foundry/fitness.py` on the recorded
  trajectory. `EvalResult` carries the fitness breakdown + the variant config +
  the scenario, and is written to a kernel-owned `data/eval_results.jsonl`
  (cross-process readable, gitignored, per item 3's ledger convention).
- **`run_eval_multi(cfg, seeds=N)`** — averages fitness across N fresh-account
  repeats of the same variant. Directly tames the known `Harvest`/`Mine`
  intermittent freeze (Phase 4 item 4's follow-up): a frozen seed gates to ~0
  and is averaged out rather than trusted as the variant's true score — the same
  "average repeats, never trust one sample" discipline the item-4 control pair
  already used, now first-class in the harness.
- **Kernel-integrity guard (ported from v1 `safety.py`)** — before each eval,
  assert the `anima2/foundry/` tree matches a pinned git state (the ruler wasn't
  edited since the run began); refuse to score otherwise. **Defense-in-depth, not
  load-bearing this phase:** Phase 5's genomes are config-only (profession /
  sociability / `deliver_threshold` / cognition-tier), so no mutation operator
  can edit source — unlike v1, where a genome *was* source in a variant worktree
  and the revert was essential. Kept because it's cheap and becomes load-bearing
  the moment a future skill-DSL (a Phase-6 escalation) makes mutations touch code
  — but an implementer shouldn't over-invest in it now.

### Key design decisions

- **Fixed window, no early stop, multi-seed** — the three fixes Phase 4 item 4's
  live gate had to discover the hard way, baked into the harness from the start
  so every future measurement is comparable and freeze-robust.
- **Scenario staging reuses Phase 3's calibrated spots** (`TRADE_MINE_SPOT`,
  `HUNTING_SPOT`, …) — proven walkable/workable ground, no new calibration risk.

### Offline tests (planned)

`tests/test_eval.py` (new): `EvalConfig`/`EvalResult` round-trip to/from
`eval_results.jsonl`; `run_eval_multi` averaging math on stubbed per-seed
results (including a zero-fitness "frozen" seed pulling the mean down but not to
zero); the kernel-integrity guard refuses to score when a tampered-tree fixture
is presented (a `subprocess`-stubbed git-diff). The live `run_eval` body itself
is exercised by the live gate, not offline (it needs a shard).

### Live verification gate

- **Repeatability:** the *same* variant on the *same* scenario, evaluated by
  `run_eval_multi(seeds=3)` twice, yields mean fitness within a stated tolerance
  band (a scorer whose number is noise, not signal, fails this).
- **Ordering (differential):** a known-better variant scores measurably higher on
  the independent fitness — e.g. a miner staged at **Mining 50** vs one at
  **Mining 35** on the identical mining scenario: the higher-skill variant's
  skill-gain/produce rates, read from ground truth, must rank it above, with the
  gap surviving the seed averaging. Cross-process readback of
  `eval_results.jsonl` confirms the numbers from a fresh process.

### References

`anima2/foundry/eval.py`, `anima2/foundry/fitness.py`,
`anima2/foundry/trajectory.py`, `anima2/control.py`, `anima2/profession.py`
(calibrated scenario spots), `../anima/foundry/kernel/eval.py`,
`../anima/foundry/kernel/safety.py`, PHASE4.md item 4 (the fixed-window /
multi-seed / no-early-stop lessons this harness bakes in).

---

## Item 3 — Behavior descriptor + MAP-Elites archive ⏳

**The quality-diversity grid: keep the best agent OF EACH KIND, not just the
single best.** Ports v1 `../anima/foundry/kernel/descriptor.py` and `archive.py`.

### Scope

- **`foundry/descriptor.py`** — `compute_descriptor(summary) -> Descriptor` over
  the independent `TrajectorySummary`: `profession_focus` (which skill category
  the agent actually worked, categorical) × `sociability` (speech / total
  actions, binned) as the active cell key, with `aggression`/`mobility` computed
  but not yet in the key (v1's phased-activation approach). Bins are kernel-owned
  constants (locked, cited to v1). Descriptor reads *what kind* of agent (from
  behavior), decoupled from fitness's *how good*.
- **`foundry/archive.py`** — `Genome` (an anima2 **agent configuration**:
  profession, persona sociability, tuned `deliver_threshold`, cognition tier —
  explicitly **not** LLM-authored code, staying in this project's no-code-
  synthesis lane) + `Archive` (`add(genome) -> InsertResult`, `elites()`,
  `summary()`). **Promotion uses v1's reliability-discounted rule, ported
  verbatim, not a raw best-of-N max:** a genome displaces its cell's incumbent
  only if `reliability_score = mean(per_seed_fitness) − PROMOTION_LAMBDA ·
  pstdev(per_seed_fitness)` is higher (`../anima/foundry/kernel/archive.py:28-44`).
  This exists for a documented reason inline in that file — the "optimizer's
  curse": v1's `g_00070` recorded 91.9 from a lucky seed triple, held-out mean
  39, and its lucky run displaced a steadier elite. Porting the raw-fitness rule
  instead would silently reintroduce that failure and (per item 4's gate below)
  make "the elite rose" unfalsifiable. Persisted to a kernel-owned
  `data/archive.jsonl` (cross-process readable).

### Key design decisions

- **Genome = config, not code.** A variant is a point in a hand-defined config
  space (profession × sociability × tuned params), so evolution never authors or
  runs new code — the sandboxing problem Phase 4 deferred stays deferred, and
  MAP-Elites still has a real, diverse search space.
- **Descriptor decoupled from fitness** (v1's own invariant) — the grid expresses
  diversity, not a fitness ramp; a chatty miner and a silent miner occupy
  different cells even at similar fitness.

### Offline tests (planned)

`tests/test_archive.py` (new): `compute_descriptor` bins a fixture summary into
the expected cell; `Archive.add` inserts a first genome, and — the load-bearing
case — a genome with a **higher raw mean but higher variance** does NOT displace
a steadier incumbent whose `reliability_score` is higher (the ported optimizer's-
curse guard: a lucky high-variance run must not win), while a genuinely-better
low-variance genome does; a lower-scoring one does not; genomes in different
cells coexist; `elites()`/`summary()` report the grid correctly; round-trip
through `archive.jsonl`. A **negative control**: a genome with a degenerate
(all-zero) trajectory lands in the `NONE`-profession cell and never displaces a
real worker elite.

### Live verification gate

Folded into item 4's evolution run (a descriptor/archive with no evolution
driving it has nothing live to prove beyond the offline grid mechanics) — the
live gate is: real evaluated genomes land in sensible cells and the archive's
per-cell elite is the highest-fitness genome actually seen for that cell,
cross-checked from a fresh process reading `archive.jsonl`.

### References

`anima2/foundry/descriptor.py`, `anima2/foundry/archive.py`,
`anima2/foundry/fitness.py`, `../anima/foundry/kernel/descriptor.py`,
`../anima/foundry/kernel/archive.py`, DESIGN.md §6.6.

---

## Item 4 — Evolution loop (MAP-Elites over agent configs) ⏳

**Improve the population, not just one agent** — the offline optimizer DESIGN.md
§6.6 names, scoped to config-space so no code is ever synthesized.

### Scope

- **`foundry/evolve.py` + a small orchestrator** — the MAP-Elites loop: sample an
  elite from the archive, **mutate its config** (nudge `deliver_threshold` along
  item-4's grid, flip persona sociability, swap profession, change cognition
  tier), `run_eval_multi` the mutant on its scenario, `Archive.add` the result.
  Bounded by a cycle/genome cap and a `foundry/STOP` kill switch (port v1
  `safety.py`'s run guards). Parallel evals across fresh accounts respect a
  concurrency cap.
- **Reliability follow-ups that this item forces (both flagged in Phase 4's
  notes) land here:** (a) the `Harvest`/`Mine` intermittent-freeze hardening —
  a real fix to `Harvest.step()`'s cursor recovery, since a fleet of parallel
  evals can't afford ~⅓-½ frozen runs even with multi-seed averaging; (b)
  multi-process ledger-write safety — a file lock or per-process-path convention
  for `data/*.jsonl`, since parallel evals write the archive/results
  concurrently (Phase 4 item 3's own open note).

### Key design decisions

- **Config-space evolution, no code synthesis** — MAP-Elites over a hand-defined
  genome space keeps the whole phase inside the project's standing "no
  LLM-authored executable code without a sandbox" rule.
- **The ruler is reverted before every eval** (item 2's kernel-integrity guard) —
  so even as evolution mutates *agents*, it can never mutate the *scorer*; the
  A6 "editing the ruler == gaming the score" invariant holds across a whole run.

### Offline tests (planned)

`tests/test_evolve.py` (new): the mutation operators produce valid genomes within
the config space (a deterministic seeded test); the loop, driven by a **stubbed
`run_eval`** returning a synthetic fitness landscape where one config is best,
concentrates the archive's elites toward that config over generations (the
MAP-Elites analogue of item 4's bandit-convergence test — proves the loop
optimizes on a clean signal, offline, before trusting it live); the kill switch
halts a run; the concurrency cap is respected.

### Live verification gate

**Differential vs a random baseline — because "the elite rose" alone is
vacuous.** A cell's elite is a running *maximum* over reliability-discounted
scores, so it can only rise or hold as more genomes are evaluated — a plain
"end elite > start elite" bar would pass for *any* sampling procedure (directed
search, random mutation, even re-evaluating one fixed config under environmental
noise), simply because it's a max-of-increasing-sample statistic. So the gate is
**comparative**: run the evolution loop AND a random-search baseline (same
mutation space, same *total eval budget*, same scenarios/seeds) and require the
mutation-guided run's best reliability_score to beat the random baseline's by a
stated margin — standard QD practice. This is what distinguishes "evolution adds
value" from "we sampled N configs and kept the luckiest." Both runs' archives are
read back cross-process from `archive.jsonl`. A loop that "improves" by gaming is
additionally caught by the kernel-integrity guard refusing the tampered eval.
Honestly reported per the house style: both runs' wall-clock, seed count, eval
budget, and any frozen-seed rate are logged, so a thin result can't read as a
thorough one — and if the margin is within noise (a real possibility with a
small live budget), the gate reports **that** rather than dressing a tie as a
win.

### References

`anima2/foundry/evolve.py`, `anima2/foundry/archive.py`,
`anima2/foundry/eval.py`, `anima2/skill_tuning.py` (the `deliver_threshold` grid
a mutation walks), `anima2/skills/harvest.py` (the freeze fix this item forces),
`../anima/foundry/kernel/` (the whole kernel + `../anima/foundry/orchestrator`
run shape), DESIGN.md §6.6 (MAP-Elites), A6 (the locked ruler).

---

## Notes carried into Phase 6 / open follow-ups

- **Society scale-out (Generative Agents)** — persistent lives across sessions,
  inter-agent relationships and memory (who-helped-whom, mined from the trade/
  hunt loops that already exist), the uotavern forum as the village's chronicle.
  The "society" half of DESIGN.md §10's Phase-5 line, split out to keep Phase 5
  coherent around measurement + evolution. Builds directly on the existing
  `village.py`/`forum.py`/chatter machinery.
- **LLM-authored skill DSL with sandboxing** — still out of scope (carried from
  Phase 4). Phase 5's config-space evolution is the safe, no-synthesis version of
  "the agent invents new behavior"; a real Voyager code-synthesis loop needs the
  AST-allowlist/fixed-DSL sandbox design neither phase attempts. Once Phase 5's
  archive proves config-evolution works live, this is the natural next escalation.
- **Cost-tier budgets derived from curriculum/task difficulty** (Phase 4 items 2
  & 5's shared note) — a refinement still deferred.
- **First real `../uowiki` write** (Phase 4 item 1) — the write loop is proven
  against a disposable clone; the first run against the real repo deserves the
  same care as a first live-shard run.

---

## References

- DESIGN.md §6.5 (RL on bottleneck skills), §6.6 (Foundry evolution — MAP-Elites,
  independent fitness), §8 A6 (reuse v1's human-owned Foundry kernel), §10
  (roadmap — this phase's entry points here), §11 (open decisions).
- PHASE4.md — the self-reported-reward gap this phase closes; the fixed-window /
  multi-seed / no-early-stop / cross-process-readback / differential-gate house
  style every item above follows; the `Harvest`/`Mine` freeze and multi-process
  ledger follow-ups item 4 resolves.
- `../anima/foundry/kernel/fitness.py`, `descriptor.py`, `archive.py`, `eval.py`,
  `safety.py`, `trajectory.py`, and `../anima/foundry/orchestrator` — the v1
  Foundry assets this phase ports (signal source swapped: raw-packet → GM-read +
  observation-tap, since anima2 never touches the wire).
- `../anima/docs/FOUNDRY.md` — the full Foundry design (evolution loop, trusted
  kernel, MAP-Elites grid, locked fitness/descriptor) the v1 kernel implements.
