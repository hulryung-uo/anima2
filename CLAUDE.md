# CLAUDE.md — anima2

**Read [`docs/DESIGN.md`](docs/DESIGN.md) first.** It is the source of truth:
what anima2 is, the decision history (the *why*), architecture, the
Observation/Action contract, the learning plan, the roadmap, and what to reuse
from `anima` (v1). This project is designed to be resumable from that doc alone.

## What this is
A new, from-scratch **autonomous AI agent** that plays Ultima Online — the
**Brain**. It drives a body, [`anima-core`](../anima-client/crates/anima-core)
(Rust headless UO client), through a structured **Observation/Action contract**.
Clean redesign of `../anima` (v1, Python); mines v1 for assets and lessons.

## Current phase
**Phase 3 complete (economy & interaction loop), all four items done.** Phase 2
(cognition + memory) closed out — see PHASE2.md. The Python brain drives
**live ServUO characters** via the `anima-agent` NDJSON bridge — from a single
agent (`live.py`) up to a working **village** (`village.py`) of agents each
staged (Control plane, `control.py::GmControl`) into a profession
(`profession.py`): miner (mine + smelt ingots, and **deliver** them),
lumberjack (grove-aware chopping), fisher, blacksmith (gump-driven MAKE-loop
crafting, **fetch** dropped ingots when starved, and **sell daggers to a
vendor + bank the gold**), hunter (engage weak creatures, then **loot their
corpses**), townsfolk. Package adds
`skills.harvest`/`smelt`/`craft`/`market`/`hunt`
(`Mine`/`Chop`/`Fish`/`MineAndSmelt`/`MineSmeltDeliver`/`Blacksmith`/
`BlacksmithMarket`/`Hunt`) · `memory` (`EpisodicMemory` + `ReflectionMemory`) ·
`cognition` gains `ReflectingCognition` (episodes → persistent `Insight`s
feeding later goal/speech prompts) and `LLMCognition` in-character chatter + a
clamped `goal:goto` · `forum` (LLM-written in-character posts to uotavern,
`village.py --forum`) · `contract` now carries `GumpResponse`/`GumpView` for
crafting gumps, `ShopBuy`/`ShopSell`/`BuyItems`/`SellItems` for vendor
transactions, `PopupMenu`/`PopupRequest`/`PopupSelect` for right-click
context menus, `CorpseLink`/`CorpseEquip` for corpse loot/equipment
links, and `WalkTo` for the bridge's non-blocking A* route driver ·
`wiki` (read-only semantic memory over the local `../uowiki`
docs tree; optionally grounds `LLMCognition`/`LLMReflection` prompts with a
compact excerpt). **Phase 3 item 1 — the first inter-agent economy loop —
is live-verified** (`live_trade.py`): a miner mines, smelts, and hauls
ingots to a co-located blacksmith that has run its own stock dry, drops
them, and the blacksmith picks them up and crafts again — no contract
changes needed (`Drop`/`PickUp` already existed). **Phase 3 item 2 — closing
the loop into gold — is live-verified** (`live_market.py`): the blacksmith
sells surplus daggers to a staged vendor via its right-click context menu
(0x9E `SellList` → `SellItems`, dagger entries only — a plain `Say`
"vendor sell" turned out to be unreachable on real ServUO, see PHASE3.md
bug 1), then banks the proceeds at a staged `Banker` the same way (opens the
bank box, then the established lift-then-place two-step) — a manually
curated waypoint route around the trade smithy's own narrow corridor
(`profession.py`'s `VENDOR_SPOT`/`BANKER_SPOT`), since a single straight-line
greedy walk can't reach either from the smith's stand tile. **Phase 3 item 3
— hunt/loot — is live-verified** (`live_hunt.py`): a bare-handed hunter
(Wrestling 50) engages Mongbats at a live-calibrated, unpopulated pocket
(`profession.py`'s `HUNTING_SPOT`), and once one dies (`Observation.corpse_of`
links its corpse to a serial the hunter attacked) opens the corpse and loots
the gold into its pack — repeated, corpse-tied kill→loot cycles, gold
provenance-safe (the fresh account's starting gold is GM-deleted first).
**Phase 3 item 4 — A* navigate — is live-verified** (`live_navigate.py`):
`skills/movement.py::GoTo` now delegates to the bridge's non-blocking route
driver (`Action::WalkTo`/`Session::advance_route` — a different mechanism
than the originally-scoped blocking `Session::navigate_to`, see PHASE2.md
A3's note) instead of greedy tile-by-tile stepping, monitoring progress
purely from position deltas (no route state reaches the observation JSON)
and falling back to the old greedy stepping only when the route makes no
progress at all — which is what keeps `MockBody` working unchanged. A
**differential** live proof on a Minoc-ridge course 36 tiles apart, greedy-
blocked by rock: a control run forced into pure greedy stepping wedges
immediately (0 progress), while the real `GoTo` arrives and navigates all
the way back (round trip). See PHASE3.md for the full breakdown of all four
items (including several Phase-2-vintage bugs the live scenarios finally
exercised: a wrong CraftGump button, a tool that silently breaks, an anvil
blocking the delivery corridor, a proximity-failure CraftGump reshow that
froze the MAKE loop, a stale bridge binary, a wrong-distance
`find_mobile_near`, a wandering vendor NPC, item 3's two "open field"
calibration candidates that turned out to already be inhabited, and item 4's
own "distance must improve" progress-signal bug plus a GM-invisible one-way
alcove trap).
**Phase 4 item 2 — cognition cost tiering +
prompt caching — is live-verified** (`village.py --llm-tiers
{anthropic,replicate,stub}`): a single auditable `llm.py::ROLE_TIER` table
routes each cognition role to a cost tier, `build_tiered_clients()` tries
`AnthropicClient` per tier first and degrades to one reused `ReplicateClient`
(`degraded=True`) when Anthropic isn't provisioned — this environment's own
case, confirmed again at this item's landing — and every call is logged to
`data/llm_usage.jsonl` via `_UsageLoggingClient`. Live leg (a) (`--llm-tiers
replicate`, provider-agnostic) shows real per-role routing (40 cheap / 5
standard / 0 heavy calls over one miner's session, tracking the
`cognition_interval`-vs-`every_n_reconsiders` cadence difference) with the
usage ledger's line counts matching the script's own call tally exactly —
catching a real bug live (failed calls were silently un-logged; fixed by
logging on `finally`, not just on success). Leg (b) (Anthropic,
`cache_read_input_tokens` on a real cache hit) stays deferred — no
`ANTHROPIC_API_KEY` provisioned here. **Phase 4 item 1 — the wiki write
loop — is live-verified** (`live_wiki_report.py`, run against a disposable,
remote-less clone of `../uowiki`, never the real repo): `wiki.py::Wiki`
gains `file_report()` (write+slugify+`git add`+`git commit` — **never**
`git push`, a whole-test-file `subprocess.run` argv spy proves it) guarded by
a ported `circuit_breaker.py` (`Wiki._report_breaker`, keyed on `(page,
claim_fingerprint)`, repurposed as a filing dedup/cooldown gate rather than a
reliability breaker), and `cognition.py` gains `LLMWikiReportProducer` — a
wiki-contradiction judge whose `ReportDraft.page` is always filled in by
code from the reflection's own wiki search hit, **never** read from the
model's JSON reply, wired into `ReflectingCognition(..., wiki_reporter=None)`
as a byte-for-byte no-op when unset. The live gate's multi-cycle proof is
non-vacuous by a wide margin: 3 identical-claim judge calls collapsed to 1
commit, then 54 more repeat calls of a second claim collapsed to exactly 1
more commit (57 judge calls total, 2 commits) — read back and provenance-
checked against an independent `wiki.search()` call, not the judge's own
say-so — while a paired differential-inertness run with `wiki_reporter=None`
wrote zero new files and left the clone's commit count unchanged. Caught one
live bug (`cognition_interval=1` let chatter re-trigger every tick and
starve `Mine` entirely — fixed by raising it to `12`, matching
`live_reflect.py`'s own tuned default) and one offline regression inherited
from a prior, crashed implementation attempt (a dropped `Counter` import
silently emptied the whole wiki index — every page's `_weighted_terms` call
raised, swallowed by a broad per-page `except`). 321 tests green (up from
274), ruff clean. **Phase 4 item 3 — skill library v0** adds
`skill_library.py::SkillLibrary`: a static `REGISTRY` covering every
exported `Skill` subclass, `retrieve()` (natural-language keyword ranking
over name+description, reusing `_textindex.py`'s scoring), and a persisted,
cross-process-readable `data/skill_ledger.jsonl` outcome ledger
(`record_outcome()`/`stats()`) — `Agent` gains an optional `skill_library=`
collaborator (byte-for-byte no-op when unset) and every `Skill` gains
`diagnose()` (a one-line reason it can't run right now). **Phase 4 item 4 —
`deliver_threshold` bandit tuning — is live-verified** (`live_trade.py
--tuner`): `skill_tuning.py::ParamTuner` is a UCB1 bandit over
`MineSmeltDeliver.deliver_threshold`'s discrete candidate grid, persisted
through item 3's own ledger (`param`/`param_value` fields) so a tuner's
pull counts survive a process restart; `village.py --tune-deliver-threshold`
picks a value per miner at agent-construction time and records the
session's outcome. The live gate is a **positive/negative control pair**
(`--deliver-threshold 5` vs `20` on a fixed, non-early-stopped tick window —
established `5` as better on this scenario, mean reward 39.3 vs 19.57 across
three repeats each) followed by an 8-session **tuner-driven** run
(`--tuner --sessions 8 --candidates 5,20`) whose pull distribution
concentrated 7-of-8 on `deliver_threshold=5` — the control pair's own
winner — confirmed by a **fresh subprocess** reading the ledger from disk,
never the live process's own memory. The gate's first attempt failed
honestly (a flat `{5:2, 8:2, 12:1, 20:1}` pull distribution pointing away
from the control pair) — the fix and the live-caught root causes (an
unstable per-episode-mean reward metric, an unrecorded zero-episode "live
wedge" poisoning an arm, and too few sessions to let UCB1 concentrate over
four candidates) are documented in full in PHASE4.md item 4, along with a
follow-up bug this diagnosis surfaced: `Harvest`/`Mine` could intermittently
freeze mid-session under a long, uninterrupted mining phase, independent of
`deliver_threshold`'s value — **resolved in a pre-Phase-5 hardening pass**
(two confirmed ServUO server-side "no" signals, `Harvest.step()` never
checked for either; see PHASE4.md item 4's "Resolved" note for the full
root-cause trace and the windowed stuck-rate + `WalkTo`-relocation fix).
**Phase 4 item 5 — automatic curriculum — is live-verified** and completes
the phase: `curriculum.py::CurriculumController` (cadence-gated on its own
daemon thread, mirroring `ReflectingCognition`) tracks a hand-written
`MILESTONES` catalog of Observation/EpisodicMemory-derived predicates (so
they can't be gamed by self-report), records one `Episode(kind="milestone")`
per achieved-transition into the agent's own memory (idempotent, survives
restart via `data/milestones.jsonl`), and — when 2+ milestones are eligible
— asks the tiered `curriculum_pick` client to pick one name off the shown
list, falling back deterministically on any bad reply. `village.py
--curriculum` opts it in (observational only: nothing steers behaviour from
`curriculum_milestone` yet). Live gate (`live_curriculum.py`): the GM boosts
a miner's Mining past 50 mid-run, the `miner_mining_50` milestone fires
exactly once (read directly from `EpisodicMemory`), and it STILL fires under
a pure-garbage LLM — the achievement predicate is deterministic and
LLM-independent. A pre-Phase-5 hardening pass then resolved the
`Harvest`/`Mine` intermittent-freeze bug (resource-bank exhaustion +
pack-full — windowed stuck-rate detection + `WalkTo` relocation) and the
`GmControl.get_property` empty-readback bug (now `get_property_value`, a
typed live-verified readback) — see PHASE4.md item 4's "Resolved" note.
**Phase 5 item 1 — the independent fitness oracle — is live-verified**
(`live_fitness_gate.py`): `anima2/foundry/` (the human-owned kernel the
learning code provably never imports — an AST-level import-graph guard test)
ports v1's locked-weight `compute_fitness` + a `TrajectoryRecorder` whose
load-bearing channel is a separate GM connection's `[Get` reads (the server,
not the agent, reports the numbers). The differential gate: an honest miner
vs an agent rigged to self-report 300,000 reward — self-report ranks the
gamer first, the independent fitness ranks the honest worker first (277.5 vs
0.0; the rigged agent's 225 denied moves zero its viability gate), and the
ranking is unchanged with the in-process channel (b) excluded — plus a
post-run cross-check from a FRESH GM connection while the subjects are still
online. **Phase 5 item 2 — the repeatable eval harness — is live-verified**
(`live_eval_gate.py`): `foundry/eval.py` adds `EvalConfig`/`EvalResult`/
`run_eval`/`run_eval_multi` (fixed-window, no-early-stop, multi-seed
mean/stdev, a `spot_pool=` rotation across `MINING_SPOTS[0..3]` so
back-to-back mining seeds don't share one thinning `HarvestBank`) and a
runtime `assert_kernel_clean` git-diff guard (proven by 5 offline,
subprocess-stubbed tests; deferred live since the harness itself is
mid-development and uncommitted — every real caller still gets the check).
`anima2/live_common.py` consolidates the six copy-pasted `_RecordingBody`s
plus the wipe/login-throttle/gate-verdict conventions every live script had
grown independently (`live_fitness_gate.py`/`live_mine.py`/`live_trade.py`
migrated; five more scripts still carry their own copy, a follow-up). The
live gate's own dress rehearsal caught a real bug: `TappedBody.tap_observation`
was crediting a fresh character's starting gold as "produced during the
window," a phantom `produce_term` floor identical across every variant —
including one staged with no pickaxe at all — fixed by seeding the
backpack's baseline amounts without emitting a delta on the tick the
backpack is first identified. Live gate: leg (a) repeatability held (two
`run_eval_multi(seeds=3)` runs of the same variant, 59.21 vs 23.78,
within a 60.73 tolerance band derived from the runs' own spread — wide but
honest, driven by Mining's real per-swing gain-chance randomness and a
4-spot pool reused across 12 evals in ~9 minutes, not harness noise); leg
(b) ordering held decisively (a real miner at 60.98 vs a no-pickaxe agent
that provably cannot mine at 2.4243, a ~25x gap dwarfing both sides' own
stdev), both cross-process-verified from a fresh `python -c` reading
`data/eval_results.jsonl`. **Phase 5 item 3 — the behavior descriptor +
MAP-Elites archive — is landed offline** (its live proof folds into item
4's evolution gate, per the spec): `foundry/descriptor.py` +
`foundry/archive.py` port v1's cell key and the reliability-discounted
promotion rule (`mean − λ·pstdev`, the optimizer's-curse guard) verbatim,
with Genome as four named config fields (never code) and an append-only
replayed `data/archive.jsonl`. **Phase 5 item 4 — the evolution loop — is
live-verified and completes Phase 5** (`live_evolve_gate.py`):
`foundry/evolve.py` adds the MAP-Elites loop (`evolve()`, mutating one of
`Genome`'s four named config axes per step off a sampled elite) and a
`random_search()` baseline built from the same shared step-driver, both
bounded by `max_genomes` and a `foundry/STOP` kill switch, sequential-only
(`MAX_CONCURRENT_EVALS` pinned at `1` — this project's shard has exactly one
GM account). `foundry/_filelock.py` (`fcntl.flock`-based `append_line_locked`,
proven by 6 real concurrent subprocesses writing 240 lines with zero torn or
lost lines) closes Phase 4 item 3's multi-process ledger-write follow-up,
wired into every `data/*.jsonl` append in `archive.py`/`eval.py`. Adversarial
review caught a must-fix before any verdict was trusted: the gate/tests were
selecting each arm's champion by raw-fitness argmax and only then reading its
reliability — re-importing the optimizer's curse item 3's reliability
discount exists to prevent; fixed by `Archive.best_by_reliability()`, now the
selector everywhere a comparative verdict is drawn, regression-pinned by 2 new
tests, and the fix demonstrably mattered in the live run (the evolve arm's
raw-fitness and reliability champions genuinely diverged). The live gate (8
genomes/arm x 2 seeds x 200 ticks, interleaved E/R over a shared
`MINING_SPOTS` cursor for drain fairness) passed its full infrastructure
check (spot fairness, live kill-switch proof, kernel-guard offline-proven
per item 2's precedent, no early halt, and item 3's own folded per-cell-elite
recompute proof, all cross-process-verified) and came back an **honest tie**
on the comparative verdict (margin −12.42 against an 18.57 noise band) — the
expected outcome given three of the four genome axes are live-inert under
today's bare-`Mine()` eval scenario, as `evolve.py`'s own docstring states.
530 tests green, ruff clean. **Next:** Phase 6 — DESIGN.md §10's society
scale-out (persistent lives, inter-agent relationships, the forum as village
chronicle) is the next named phase; item 4's own live gate also surfaced a
richer-eval-scenarios follow-up (today's harness leaves most genome axes
live-inert, so a decisive evolution-vs-random differential needs
multi-profession/cognition-aware scenarios) — see PHASE5.md's "Notes carried
into Phase 6" section for both and the other carried-forward items.

## Dev
- Offline: `uv venv && uv pip install -e ".[dev]"` · `python -m anima2` · `pytest -q` · `ruff check .`
- Live: build the bridge in the sibling repo (`cd ../anima-client && cargo build -p anima-net`),
  then `python -m anima2.live <host> <port> <user> <pass> [--goto X Y] [--llm]`.
- The bridge bin + JSON shapes live in `../anima-client/crates/anima-net` (`src/bin/agent.rs`,
  `src/json.rs`) — keep them in lockstep with `contract.py`.

## Non-negotiable principles (DESIGN.md §2)
- **Brain ⊥ Body.** anima2 reads Observations and emits Actions — it **never**
  parses packets or touches a socket. The body (anima-core) owns the wire.
- **Hierarchical, two-rate loop.** Fast loop (~100–250ms) is deterministic skills
  + reflexes + planner, **no LLM**. Slow loop (seconds–min, async) is LLM
  cognition that *steers* — it never sits in the hot path.
- **Priors + skill library + curriculum before gradient RL.** Sandbox UO has no
  reward gradient; LLM priors + the `../uowiki` "textbook" + a curriculum are the
  fast accelerant. RL/Foundry evolution optimize bottlenecks later.
- **Three planes kept separate:** Play (the contract) · Control (GM scenario
  control, reuse v1 Foundry kernel) · Director (curriculum). Control plane lives
  outside both brain and body.
- **Reuse v1's hard-won assets, rebuild its structure** (DESIGN.md §8).

## Likely stack (open — DESIGN.md §9)
Python brain talking to anima-core over the contract via IPC (reuse v1's
brain/Foundry/wiki/LLM assets). LLM provider abstracted, default to latest Claude
family, tiered (Haiku/Sonnet/Opus); **never in the fast loop**. Consult the
`claude-api` skill when wiring LLM calls.

## Key references
`../anima` (v1: personas, planner, Foundry kernel, wiki flywheel), `../uowiki`
(semantic memory + MCP tools), `../anima-client/docs/DESIGN.md` (the body + the
original contract sketch), `../servuo` (local test shard).
