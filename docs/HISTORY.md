# Phase history — the full narrative

Moved verbatim from CLAUDE.md (audit proposal 4): 37KB of per-phase history was a
per-session context tax and a second divergence surface. Nothing was edited in the
move; per-phase detail also lives in docs/PHASE2.md ... PHASE7.md.

Three things have been added since the move, all at the end of this file: the
between-phase entry (the offline single-source/knob-channel refactor, 2026-08-02), the
live-run entry that followed it (2026-08-03), and the corrected forward pointer that
closes the file (2026-08-02).

The phase narrative above them is otherwise verbatim, with ONE edit, stated here because
"untouched" was the first version of this sentence and it was false: the Phase 7 entry's
trailing `**Next:** Phase 7 item 2 ...` clause was first DELETED outright and is now
struck through in place, annotated, with its replacement at the end of the file. A
review caught the deletion against `git diff --stat` (50 insertions, 3 deletions) while
this paragraph claimed none. That clause is not incidental prose — it is the stale
forward pointer the audit's top risk 4 / proposal 4 is about, i.e. the evidence for a
lesson still being cited, so a silent deletion was the worse of the two errors.

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
into Phase 6" section for both and the other carried-forward items. **Work
breakdown written** — see [`PHASE6.md`](docs/PHASE6.md): six items.
**Phase 6 item 1 — persistent lives — is live-verified**: `memory.py::
ReflectionMemory` gains optional `persist_path`/`agent_key` constructor kwargs
(`record()` appends one `{ts, agent_key, text, episode_ticks, episode_count}`
JSON line when set, under a new `_insights_log_lock` — byte-for-byte no-op
otherwise) and a new `load_insights(path, agent_key)` module function (the
"load at construction, append incrementally" idiom `skill_library.py`/
`curriculum.py` already established, now ported to `ReflectionMemory`);
`village.py --persist-insights` wires it into `data/insights.jsonl`, scoped to
the existing `--llm-tiers` branch only. The live gate (`live_persistent_lives.py`,
four legs each run as its own subprocess/genuinely new OS process) is
decisive: a fresh persona reflects and persists real insight text to disk
(confirmed by an independent fresh-process readback), then a **genuinely new
process** — same account, same character — loads that insight from disk and
surfaces it in its very first cognition prompt before reflecting even once
itself; a different persona sharing the same ledger file sees none of it
(cross-persona isolation); an identical run with persistence off genuinely
reflects in memory (a positive control proving the engine ran) yet leaves the
ledger byte-for-byte unchanged on disk (inertness). All four legs passed on
the first attempt. Caught one bug along the way, in the live-gate script
itself (not the shipped code): the scripted reflection client was wired
directly as `ReflectingCognition`'s `reflection` producer instead of wrapped
in `cognition.LLMReflection`, so `_reflect_bg`'s broad exception guard
silently swallowed an `AttributeError` every cadence cycle — fixed by
matching `live_wiki_report.py`'s own established wrapping pattern. 540 tests
green (up from 530), ruff clean. **Phase 6 item 2 — the village chronicle
relationship ledger — is live-verified**: `chronicle.py::ChronicleLedger`
mines "who helped whom" from confirmed trade/market/hunt interactions
already computed by the fast loop, via a deliberate `queue_event()`/
`flush()` split — worker threads only ever append to an in-memory queue
(`threading.Lock`-guarded, zero disk I/O), and `village.py`'s **main**
thread flushes the whole batch to `data/chronicle.jsonl` once, right after
`for t in threads: t.join()`, mirroring the exact "compute in worker
threads, persist once from the joined main thread" shape the
`deliver_threshold` tuner's own outcome recording already established.
`village.py --chronicle` wires five pure, unit-tested per-profession event
detectors (`delivered_ingots`/`picked_up_ingots`/`sold_to_vendor`/
`banked_gold`/`looted_corpse`) into `_run_worker` (the hunter dispatch emits
one zero-amount `looted_corpse` event per extra corpse retired in the same
tick, keeping the event *count* faithful to `Hunt._advance`'s own same-tick
recursion without inventing a per-corpse split of an unknowable combined
accumulator), with each side's counterpart persona supplied statically from
the trade-pairing wiring `village.py` already computes. The live gate
(`live_chronicle.py`, a standalone driver mirroring item 1's own
`live_persistent_lives.py` precedent) is decisive: two independent staged
sessions each produce exactly 2 confirmed `delivered_ingots` events whose
count *and* summed amount match a wholly independent oracle — hand-written
in the gate script, never calling the shipped detector code — built by
walking the miner's own `agent.episodes` transcript directly, cross-checked
against a **fresh subprocess** reading the ledger from disk (22.0 and 14.0
ingots, exact matches both times); a solo miner (no blacksmith paired, staged
at a rotated, never-shared mining spot) records real mining activity but
zero chronicle events, and the ledger file for that persona is never even
created; and an identical run with `--chronicle` off keeps the underlying
economy working normally (15 episodes, 2 full delivery cycles) while never
touching `data/chronicle.jsonl` at all. The live gate caught a real bug
before it could ship: a first-draft `delivered_ingots` (and, by the same
flaw, `looted_corpse`) checked only the exact phase-exit tick's own episode
reward, but a multi-pile ingot haul (`INGOT_GRAPHICS` has 4 distinct
graphics, like `ORE_GRAPHICS`) pays its confirmed reward across several
ticks — one per confirmed pile-drop — so that tick's own reward is often
`0.0` even for a real delivery; the blacksmith's own `picked_up_ingots`
(pack-delta based, immune to the bug) kept firing correctly while the
miner's `delivered_ingots` stayed silent, exposing the gap directly. Fixed
by accumulating confirmed reward across the whole phase rather than reading
one tick's episode. An independent second run of the gate then caught two
more bugs — both in the **gate script itself**, never the shipped code:
retried attempts sharing one `ChronicleLedger` mixed a stalled attempt's
real events into the winning attempt's own flush batch (fixed by giving
every retry attempt its own ledger file); and the solo-miner leg wedged on
all retries because it mined the exact spot leg A had just drained (fixed
by rotating the solo leg across the *other* `MINING_SPOTS` entries, mirroring
`foundry/eval.py`'s own `spot_pool=` precedent). 590 tests green (up
from 540), ruff clean. **Phase 6 item 3 — the forum as continuing chronicle
— is live-verified and completes Phase 6's first thread**: `forum.py::
compose_post`/`compose_post_llm` gain optional `yesterday`/`chronicle_events`
parameters (both `None` by default, byte-for-byte unchanged output) — a
CODE-composed grounding sentence (`_chronicle_grounding_line`, tallying this
persona's own confirmed `ChronicleEvent`s by kind/counterpart, e.g. "You
delivered ingots to Tormund3 twice today.") is spliced into the prompt/
heuristic body before any LLM call, mirroring `cognition.py::
LLMWikiReportProducer`'s "code composes the fact" discipline — the LLM only
ever turns an already-true fact into prose. `post_day` gains a `data/
forum_log.jsonl` local mirror (every attempted post, `remote_ok` reflecting
the real forum call's own outcome) so later verification never depends on an
unverified forum-side read API. `village.py`'s existing `--forum` block
threads both new parameters through with no new flag: a per-agent
`session_chronicle` list (fed by `chronicle.ChronicleLedger.queue_event()`'s
now-returned `ChronicleEvent` — a small additive change) and a
`yesterday_texts` snapshot taken right after `load_insights()`, before this
session's own reflections can overwrite it. The live gate
(`live_forum_chronicle.py`, a standalone driver reusing item 2's own staging
+ item 1's own `ReflectingCognition`/`ReflectionMemory(persist_path=...)`
directly) is decisive and used the REAL live uotavern forum + REAL Replicate
qwen client throughout: a paired miner+blacksmith session posts real,
qwen-written prose whose content names the blacksmith's exact persona
("Dropped them off with Tormund87..."), confirmed by a fresh-subprocess
readback of `data/forum_log.jsonl`; a solo miner's post from the same gate
run names neither paired persona (the negative-control half of the same
grounding claim); an identical paired run with persistence/chronicle both
off posts real prose with none of the item's own tells ("Yesterday", any
grounding verb phrase); and a **genuinely separate OS process** (`--leg
session2`, no live connection at all — the property under test is prompt
construction, not live play) loads session1's own persisted insight text
from disk and confirms it reaches the prompt handed to a capturing stub
client. Three bugs were caught and fixed, all in the gate script itself,
never the shipped code: a discarded, stalled retry attempt was still
publishing to the real forum before being thrown away (fixed by gating the
post on `not stalled`); the inertness leg had no delivery signal to stop on
and mined far more than needed at the one shared, non-rotatable trade spot
session1 had just used, wedging on all 3 retries (fixed by stopping as soon
as a modest positive episode count is reached); and the gate's first,
technical dash-suffixed persona names (`"Grimm-fc782135"`) were reliably
paraphrased away by genuine LLM prose, while short, `village.py`-shaped
names (`"Grimm87"`/`"Tormund87"`, matching the real `f"{persona_name}{idx}"`
convention) survive it far more reliably — fixed by renaming every gate
persona accordingly, with a wholly distinct name root per leg so no
substring check can cross-match. The item's own bundled one-time real
`../uowiki` write check was deliberately NOT run: `live_wiki_report.py`'s
`_assert_no_remote` unconditionally refuses any repo with a configured git
remote, and `../uowiki` genuinely has one — a real, unresolved tension left
to an explicit human decision rather than resolved by weakening the check.
602 tests green (up from 590), ruff clean.
**Phase 6 item 4 — richer eval scenarios (a second scenario-supported
profession) — is live-verified**: `foundry/eval.py::Scenario` gains a
`nodes` field and a new `SCENARIOS["fishing"]` entry (a `Fish()` scenario at
`FISHING_SPOTS[0]`), and `foundry/evolve.py::PROFESSION_SCENARIO` gains its
second entry (`{"miner": "mining", "fisher": "fishing"}`) — which, with no
other `evolve.py` change, makes `op_profession`/`_active_mutation_operators`
a real, non-no-op mutation axis for the first time (the old
single-entry-`PROFESSION_SCENARIO` tripwire test was consciously rewritten
against a locally-monkeypatched dict, not left to bit-rot). The live gate
(`live_eval_gate.py --scenario fishing`) reuses Phase 5 item 2's own ordering
(differential) proof shape — `"fishing"` WITH a `FishingPole` vs the same
scenario with `item_overrides=()` (no pole, `Harvest`'s "find nothing"
branch), `run_eval_multi(seeds=3)` per side — and PASSED all four gating flags
(ordering holds in- and cross-process, mean_with 4.5436 > mean_without 2.5241,
gap 2.0195 dwarfing both sides' stdev; every with-pole seed's
`produce_value_rate` nonzero `[201.8, 302.8, 303.2]`, every no-pole seed a
provable `0.0`). Two decisions made honestly at landing, both documented in
full in PHASE6.md item 4's "As landed": (a) `fitness.produce_value_rate`
(channel (b), confirmed fish in the pack), not the `descriptor_cell`, is the
decisive "the harness scored real fishing" signal — Fishing's channel-(a)
skill-BASE delta essentially never registers within an eval-sized window even
while fish genuinely land, so the descriptor cells read all-`NONE` and that
flag was demoted to informational, provable from the gate's own transcript
rather than any claim about runs outside it; (b) the ordering leg needed a
matched-pair spot rotation the original single-fixed-spot scope didn't — a
live run first drained one fishing bank's third seed to `0.0`
(`[134.6, 237.7, 0.0]`, the same 8×8 `HarvestBank` 5-15-fish/10-20-min
respawn mechanism ore veins use, verified against `../servuo/.../Fishing.cs`),
fixed by adding an `EvalConfig.nodes` override + a `run_eval_multi(nodes_pool=)`
companion that rotates the water node in lockstep with `spot_pool`'s shore
stand (both `None`-default, byte-for-byte no-ops for every mining caller), so
each with-pole seed fishes a distinct `FISHING_SPOTS[1..3]` bank. A first-cut
`nodes`-shape bug (a bare 4-tuple where a tuple-of-nodes was needed, flattened
by `list(nodes)` into an int `harvest.py` couldn't unpack) was caught on the
first rotated run and pinned by a new `tests/test_live_eval_gate.py`. 615
tests green (up from 602), ruff clean.
**Phase 6 item 3's deferred one-time real-`../uowiki` write is now resolved
(human-approved opt-in)**: `live_wiki_report.py` gained a no-op-by-default
`--allow-remote-repo` flag (relaxes ONLY `_assert_no_remote`'s refusal, with a
printed WARNING — never enables pushing; `file_report` still only commits) and
a `--live-llm` flag that swaps the *scripted* judge (which FABRICATES a
synthetic claim — disposable-clone proof only) for the REAL Replicate qwen
judge, guarded by an interlock that refuses `--allow-remote-repo` with the
scripted judge so a fabrication can never reach a remoted repo. Run once against
the real `../uowiki` (`--live-llm --allow-remote-repo`, real qwen throughout):
healthy live mining (16 episodes), the real judge invoked 7 times over real
Mining episodes vs the real `skills/mining` page, and it flagged **zero
contradictions** — nothing committed, nothing pushed, HEAD unchanged
(`cb02d178`), `reports/open/` still only `.gitkeep`. This is the spec's own
blessed valid outcome (no genuine discrepancy — the wiki is accurate for what
the miner observed; no report was fabricated or hand-written to force a commit).
Four new offline tests (`tests/test_live_wiki_report.py`); 619 tests green (up
from 615), ruff clean — see [`PHASE6.md`](docs/PHASE6.md) item 3's "RESOLVED"
note.

**Phase 6 item 5 — cognition-aware eval — is live-verified**: the eval harness
can now make `cognition_tier`/`sociability` genuinely move the recorded
trajectory. `EvalConfig.cognition_provider` (default `None`) is the single real
off-switch — when `None`, `run_eval` builds the bare pre-item-5 agent
regardless of the other two fields (load-bearing, since `Genome.cognition_tier`
is required/never-`None`); a concrete provider builds a cognition-aware
`Planner` + `ThreadedCognition(LLMCognition(..., talkativeness_gate=True))`.
`LLMCognition`'s new opt-in `talkativeness_gate` finally makes
`Persona.talkativeness` causal (nothing had ever read it). The live gate
decides on RAW `EvalResult.speech_sent` (a new persisted field — review caught
the first draft deciding on the too-coarse `sociability_bin`, which would have
mis-flagged a genuine pass): chatty (`0.9`) mean 4.0 lines vs quiet (`0.05`)
0.33 vs bare off-switch exactly 0 — a real ~12x dose-response, cross-process
confirmed. 630 tests green (up from 619), ruff clean.

**Phase 6 item 6 — the decisive evolution-vs-random rerun — is live-verified,
and Phase 6 is COMPLETE.** `foundry/evolve.py` threads a defaulted-`None`
`professions` pool uniformly through every genome-generation surface (seed/
random draw AND the mutation operators, so a `--scenario-pool mining` run can't
leak a `fisher` genome by any path); `live_evolve_gate.py` gains
`--scenario-pool {mining,all}`/`--cognition-provider {stub,replicate}` and the
`--suffix`-to-ledger-path fix. With items 4-5 landed the genome's axes finally
move live (the gate's ENRICHMENT SANITY confirmed both `miner` and `fisher`
sampled and cognition firing). The comparative verdict came back honest and
unfavorable: **random search decisively beat the evolution loop** (margin
−29.3, outside the ±22.0 noise band) — evolution's best stayed a bootstrap seed
while its five mutations wasted budget on drained-fishing-bank `fisher` swaps,
and 8 genomes is far too few for MAP-Elites to exploit. Infrastructure +
enrichment gates both passed; the loss is reported as-is, not re-rolled (larger
budget + fishing-spot rotation named as Phase 7 candidates). 637 tests green (up
from 630), ruff clean. **Work breakdown written** — see
[`PHASE7.md`](docs/PHASE7.md): four items, none landed yet. Redeeming item
6's loss (thread B) comes first, before closing the skill-ledger honesty gap
and sharpening insight retrieval; the LLM-authored skill DSL stays
explicitly out of scope, gated on item 2's own verdict — see PHASE7.md's
intro.
**Phase 7 item 1 — profession-conditional pool routing + fishing `nodes_pool`
threading — is live-verified**: `EvolutionConfig` gains both-optional
`nodes_pool`/`fishing_spot_pool` (default `None`, byte-for-byte no-op for every
existing construction); the fix is pushed into `evaluate_genome` itself
(defense-in-depth) — `is_fishing = SCENARIOS[scenario_id].nodes is not None` (a
GENERIC structural check, never a hardcoded profession string) routes a fisher
genome's pools from the fishing-specific fields, while a non-fishing genome
resolves `spot_pool` as before AND has `nodes_pool` FORCED to `None` by any path
(so a leaked `nodes_pool` can never corrupt a mining eval's staging — `Mine`/
`Fish` both read `ctx.memory["harvest_nodes"]` generically). `default_eval_fn`
gains a `nodes_pool=` passthrough into `run_eval_multi` (which has accepted it
since item 4); `live_evolve_gate.py` gains `FISH_POOL=tuple(FISHING_SPOTS[:4])`,
`_fish_window` (matched `(stand, nodes)` windows), `_prove_fish_spot_fairness`,
and TWO independent cursors (the mining `cursor` advances only on miner rounds,
a new `fish_cursor` only on fisher rounds), activating automatically under
`--scenario-pool all`. The load-bearing regression was written RED-first against
the pre-fix code (a mining-shaped `spot_pool` — `[(2567, 493), (2611, 474)]`, a
Minoc coord — reached a fisher genome's eval-cfg call), confirmed failing, then
made green. The live smoke gate (`--genomes 6 --scenario-pool all
--cognition-provider stub`, real ServUO, fresh accounts) passed all five decisive
checks via a fresh-subprocess readback of `data/eval_resultsphase7item1smoke.jsonl`
(24 rows): every fishing row staged at a `FISHING_SPOTS[:4]` stand with its
matched water node (all four stands appeared, `produce_value_rate` nonzero on all
8 — no bank starved), never a mining coord; every mining row at `MINING_SPOTS[:4]`
with `nodes=None`; both fairness proofs `True`. The decisive live moment: an EVO
`op_profession` mutation swapped a miner elite into a fisher and staged it at
fishing stands with matched water nodes, not the Minoc ridge it would have hit
pre-fix. Comparative verdict `RANDOM WON` (margin −28) is expected/irrelevant at
this 6-genome smoke budget — item 2 is the decisive larger-budget rerun. 648
tests green (up from 637), ruff clean. ~~**Next:** Phase 7 item 2 — the decisive
evolution-vs-random redemption rerun at a larger (`--genomes 20`) budget,
exercising item 1's fix at item 6's own full scale.~~ *(Superseded 2026-08-02 — see
the corrected forward pointer at the end of this file. Struck through rather than
deleted, and deleted is what happened first: this sentence IS the stale pointer that
almost burned a multi-hour single-GM live budget, so it is the primary evidence for
the audit's top risk 4 / proposal 4 and removing it would have destroyed the exhibit
while keeping the lesson that cites it.)*

**Between-phase work (2026-08-02) — two single-source modules, a knob channel, and a
record correction.** Not a phase and not on the numbered roadmap: an offline refactor
plus the paperwork it owed. Two new modules sit under all five Lives. `anima2/obsview.py`
is the ONE definition of what an Observation says we have (pack / worn / ground-in-reach /
bank box): twenty hand-copied readbacks across the five Life modules and four more in
`life_runner.py` collapse into one function each, and merging them exposed a real defect
rather than mere duplication — three Lives wrote `i.container in (bp, player)` with no
`bp is not None` guard, so with our own pack out of view a tool lying on the GROUND read
as owned while the gate correctly refused. `anima2/knobs.py` is the one clamped read every
LIFE tuning knob goes through (`wander_leash` is the standing exception — it rides
`Staged.leash` and clamps itself inline), generalizing the `bank_reserve` lesson (a
malformed value read raw by the rule and clamped by the gate had already recreated the
rule-vs-gate drift class THROUGH the tuning knob itself). Audit proposal 5's constructor
parameters, marked done since 2026-07-30, turned out to be **wireless**: no production
site could pass one. They
have an entry point now — `village.run_carpenter_life(knobs=)` / `run_woodsman_life(knobs=)`
→ `LifeSpec.knobs` → `LifeRunner.build_life` → the Life — but only two of seven
Life-construction sites have it, no `Genome` axis maps onto a knob, and — when this was
written — no live run had used a tuned one (one has since, on the carpenter; see the
2026-08-03 entry below), so CLAUDE.md's precondition (a) stays PARTIAL and the Phase 7
item 2 rerun stays deferred. The channel also has a GUARD it shipped without: `LifeSpec.knobs`
splatted into the Life constructor unchecked, so `knobs={"profession": "mage"}` — the
first axis name in `Genome`, i.e. the likeliest key the searcher this channel exists for
would send — built a carpenter that staged, labelled and reported itself as a carpenter
while deciding as a mage, a permanent want-vs-refuse standoff contradicted by the
operator's own status line. Each Life declares a `KNOBS` allowlist now and
`LifeSpec.__post_init__` enforces it, which also moves a typo'd axis from "TypeError
after the login, the staging, the gold-wipe and the seed grant" to "ValueError before
the first packet". Review-caught. Ruff's rule selection is pinned in `pyproject.toml` (an unpinned
`select` means the linter's next release edits the codebase's standards for it). And the
concordance suite, which had run and earned its keep since the audit, turned out to be
blind along three whole axes at once — every fixture injected a backpack, every knob was
pinned at its module default, and both craft lattices pinned `craft_spot` to the player's
own tile. A 150,000-state differential probe found four disagreement classes hiding there;
the fourth, `carpenter craft_carpentry`, was **measured and then left out of the written
record**, which is the failure mode `docs/AUDIT-2026-07-29.md` exists to prevent. It is
recorded there now, in full, with its verdict: one unguarded terminal branch that wanted a
craft admission refuses — forever, since nothing else in a craft chain fires with material
already in the pack — and reachable on a shipped runner today via `run_supply_pair`'s
tolerated unset `vendor_spot`. Fixed, plus the `craft_spot` axis that would have caught
it. **The honest half: ALL of this is offline.** 1436 tests green, both ruff gates clean,
zero live runs — every claim above is an offline measurement or a code reading, and the
live half of the ledger is unchanged since forge18.

**One live run (2026-08-03) — the knob channel proved, a bridge a version behind, and a
goal frame nobody was ticking.** `python -m anima2.village --carpenter --knob
bank_reserve=400 --ticks 300 --monitor` against a local ServUO shard: **ONE run of 300
ticks, 32 telemetry samples — not an endurance test and not a comparison.** It is the
first live exposure of anything in the 2026-08-02 entry above, all of which closed with
"ALL of this is offline". **The headline is a success**: the staging banner printed
`staged: Sten@(2609, 474) and 129g seed  (reserve 400)` — 400 is the TUNED value, read
back by `LifeRunner.staged_line` through `market._bank_reserve` off the built Life's own
economy memory, not the module default of 129 (which is also the seed printed one clause
earlier, so a dropped knob would have been invisible). Command line → `_parse_knobs` →
`run_carpenter_life(knobs=)` → `LifeSpec.knobs` → `LifeRunner.build_life` → the Life's
memory → every reader clamped in `knobs.py` is now a channel that has carried a value on a
shard, which is the one thing CLAUDE.md's precondition (a) was missing. **It retires
nothing else, and the same run says why**: the tuned value was behaviourally INERT (the
carpenter finished on 93 gold; the bank branch is out of reach at 129 and at 400 alike), no
`Genome` axis maps onto a knob, and five of the seven Life-construction sites still have no
seam — so the channel is proven and STEERING is not, and the Phase 7 item 2 rerun stays
deferred. **The first attempt never reached the shard**: `unsupported bridge schema 17;
expected 16` — `anima-client` had moved to 17 in `867556f` while `ipc_body.py` still
declared 16, and nothing but a live attempt checks that integer. The bump was verified by
DIFFING THE SERIALIZER rather than trusting the changelog (no JSON key removed or renamed;
the only deleted line touching anything this brain emits is the `Say` parse arm, which
gained an OPTIONAL `mode` that defaults to plain speech), and v17's `terrain` — local
walkability, standing Z, closed-door serials, `None` unless the driver surveyed it — is
offered by the body and read by nobody here, which matters because "the brain cannot tell a
wall from open ground" is a root cause this project already paid for live (forge12's
relocation pool, half of it burned on the mountain's wrong flank). That is now a named
follow-up, not a vague idea. **And the run walked straight into the one shape the audit had
written down as a blind spot and never seen**: 30 consecutive samples, `t=28` to the budget
at `t=300`, reading `want=None admitted=sell_furniture ready=[]` with the throne already
sold — a live `GoalFrame` on the economy agent's stack for 272 ticks, `grep -c DISAGREEMENT`
= 0. CONFIRMED and root-caused, with a correction to the shape's own name: it is not a
finished goal that never came off but one stranded MID-transaction (`mkt_phase='sell'`,
`sell_paid=24.0`, neither terminal marker ever set), because `WarriorLife.tick` ticks one
inner agent per tick and `decide_mode` left economy mode on the very tick the vendor took
the throne — so the frame's owner agent stopped being ticked, and BOTH retirement paths
(`CapabilityGoalComplete` and `expire_due`) live inside `Agent.tick`. Reproduced offline
over MockBody the same day: 400 orchestrator ticks, 5 econ ticks, frame still ACTIVE, goal
history empty. **The claim that it disables the disagreement detector is REFUTED**, and
recorded as such: in this run `mode` was `hunt` and `target_cap` `None`, so the detector's
first two conjuncts already failed and the zero count is just the rule correctly answering
"wait rather than stall"; an offline A/B shows the goal-stack guard costs ~15 ticks of
delay (alarm at +30 with the frame, +15 without) and the repair fired in both arms. What
the strand costs unconditionally is that the telemetry LIES — 272 ticks of
`admitted=sell_furniture` with nothing executing it, the want-vs-admitted ambiguity
`telemetry_line`'s own docstring says cost three runs and one wrong root cause,
reintroduced on the other side. Deferred deliberately (audit follow-up 10: every candidate
repair changes the orchestrator's tick shape or the goal lifecycle, and one sighting is not
enough to choose) — *and then fixed the same day; the entry below.* **The economics
reproduced the stated warning exactly**: 129g seed → 69 (20 boards at 3g) → 93 (one
throne, +24g), `net=-36g`, and the run ended BELOW the price of
its own next attempt (93 against `BOARD_BATCH_COST` 114), which prices a self-supplying
carpenter out of the market in a single cycle — `docs/CARPENTER.md` now carries both live
measurements and the −33g/−36g reconciliation (the vendor's shelf, not the brain, sets the
batch). Zero code changed as a result of the run; the record did. Full evidence:
`docs/AUDIT-2026-07-29.md`, the 2026-08-03 entry and follow-ups 10-11.

**The frame the orchestrator froze — fixed the same day, and the fix needed a bound of its
own (2026-08-03).** The deferral above lasted hours, not weeks: a verification pass
confirmed the mechanism is structural to `WarriorLife.tick`, so all five Lives inherit it,
and measured the price in an A/B whose two arms end in an identical world — a 15-tick delay
on any later disagreement, ~20 economy ticks walking the return leg of a sale that finished
280 ticks earlier, and the unconditional one, **telemetry that lies**. The repair is a
FOURTH option none of follow-up 10's three: an **exit-edge hold**. `decide_mode` is pure
over `(obs, memory)` and structurally cannot see the goal stack, so it answers "hunt" on
the very tick the transaction's own world-fact flips; `tick` now HOLDS the economy mode
while a frame is live, so the frame's own agent keeps being ticked and reaches its own
retirement. One agent per orchestrator tick is unchanged, `decide_mode` is untouched (all
five stay pure, so the ~150k-point concordance lattice is untouched), and `want=` stays the
rule's own answer — fixing the `admitted=` lie by rewriting `want=` would only move the
ambiguity. **The interesting half is the bound**, because the first implementation claimed
two and had none that were general: only `SellItemCapability` and `BankGoldCapability` ever
write the `cap_run_finished_goal_id` marker that closes an unachieved run, so every buy /
craft / fetch / deliver frame has no give-up ladder at all; and every `*_can_yield` in
`capabilities.py` carries the same unconditional "idle UI" clause, so ONE unowned gump —
forge15's own wedge — blocks the deadline backstop and the readiness gates together. Review
measured the consequence, and it was re-measured for the record against a two-bound build
of the tree: with an unowned gump and any non-sell/non-bank frame the two-bound hold pinned
four of the five Lives in economy mode for the whole 3000-tick window, their work agent
never ticked again — **a total livelock strictly worse than the zombie frame it was written
to fix**, which at least kept hunting. (A stale vendor BUY window does NOT reach it: the
buy FSM's own popup stage cancels one, commit `8cdd2f0` — only the gump has no FSM-level
owner.) So a THIRD bound shipped — an OVERDUE frame (past its own deadline on the economy
clock the hold keeps advancing) releases the hold, and first gets
`_clear_stale_ui` pointed at it, capped at
three closes per frame. Death overrides the hold for the whole EPISODE, not the ghost
window: keying it on `obs.player.dead` alone was caught taking the body back from
`RecoverDeath` the tick after resurrection and deferring gear recovery by 177 ticks — the
naked death-loop `WarriorLife` exists to end. Two of the implementation's own claims are
recorded as REFUTED rather than as claims ("the hold is self-limiting"; "no offline world
reaches the overdue state"). 22 new tests (1440 → 1462), no existing assertion touched, both
ruff gates clean; A/B against the pre-fix tree over 1000 randomized worlds: ticks in which a
live frame's clock did not move **129,376 → 12,096**, frames retired 9,362 → 10,312, longest
hold **270 ticks with none over 320**, at a **−4%** work-tick cost. The original repro now
retires `('sell_furniture','failure')` and the masking A/B flags at 15 in BOTH arms. **The
honest half, when this was written: ALL of it was OFFLINE** — the defect was found live and
the fix changes WHICH INNER AGENT IS TICKED, the hot path of every profession, and the shard
was down. Detail: `docs/AUDIT-2026-07-29.md` 2026-08-03 §5 and follow-ups 12-14.

**The shard came back the same day, and the record splits in two (2026-08-03, three runs).**
The A/B is the cleanest evidence in the audit trail: the same command, the same knob, the
same 300 ticks, before and after — **30 lying status lines out of 32 became 0 out of 33**
(`admitted=sell_furniture` while `furniture=0` and nothing was executing it), with the
carpenter's net gold, banked and end state identical, so the fix cost that Life nothing
measurable. An 1800-tick forge-pair run then showed the hold ITSELF for the first time:
**`+hold` on 31 samples**, its frame's `@age` advancing 1:1 with the orchestrator's ticks
and the frame retiring inside its 300-tick budget; frame ages that RESET across frames
rather than climbing forever; **bound 2, the frame's own deadline, closing two unachieved
frames** (`craft_tongs` 292/300 with 4 tongs of 5, `buy_iron` 177/180 with 1 iron — neither
family writes a run-finished marker and neither frame was achieved, so `expire_due` is the
only thing that could have closed either); and the rule-vs-gate detector plus its stale-UI
repair both firing, twice, right after frames retired — the reviewer's specific worry (a
longer-lived frame muzzling the detector) not visible. **What the runs did NOT do is the
half worth remembering: TWO of the three bounds are still unproven live.** No frame ever
went overdue, so the third bound — the overdue release and `_repair_overdue_frame`, the
bound the review had to add because the two-bound build livelocked the wedged world (the
audit's §5 measured **four of the five Lives pinned in economy mode with `hunt_after = 0`
for the whole 3000-tick window**; a "24 of 24 worlds" figure stood in this sentence and in
`57422e4`'s pushed commit message, and it is unsourced — see the CORRECTION note in the
audit's §5) — has ZERO live ticks *(true of these three runs; a fourth run the same day
closed it — next paragraph)*, and neither did the death override. And **bound 1, the
FSM give-up ladder, is unexercised as far as these logs can tell**: every `sell_tongs` and
`bank_gold` frame closed on a SUCCESSFUL sale or deposit, which `CapabilityGoalComplete`
closes by its achievement branch, and the status line cannot name the branch — a ladderless
`buy_iron` frame closed just as fast, at age 4, so a low max age is no signature.
`!frozen` on none of 306 samples is likewise entailed rather than earned: the telemetry can
only print it when a death episode is open or a frame is overdue-and-unrepaired, and
neither happened. Reaching bound 3 on purpose needs a forced-state gate (a surface arriving
mid-transaction), though an ordinary run can also stumble into it — `_craft_can_yield`
refuses on ANY open gump, including the craft FSM's own, and these runs had 14 `ui=gump`
samples that simply never coincided with a deadline. Three things the runs found that are
NOT the hold: a terminal-but-unachieved
`craft_tongs` frame — 4 tongs of a batch of 5, iron exhausted — sat admitted 266 ticks past
`cap_craft_stage=finished` purely because craft has no run-finished marker (follow-up 15);
a vendor BUY window left behind by a finished `buy_iron` trip would not clear, eating
the last **556 ticks** of the 1800-tick run through three whole `buy_iron` budgets — which
makes the `+2528g/h` an average over a run whose last third earned nothing, and puts
forge16's wedge shape back on the open list at one goal lifetime per recurrence
(follow-up 16); and **the miner stopped producing at t=765 with nothing flagging it**
(follow-up 17) — the flagship miner→tinker chain did bank **503g (+585g net)** over 1800
ticks, but Grimm's reward froze at `out+176.9` and he never smelted or delivered again on
any of the 126 remaining samples, so five of the six deposits and everything above the
first 23g were the tinker working through ONE 69-ingot delivery. The chain's supply side
stopped at 43% of the run; `run_forge_pair`'s status line prints no hp for either agent, so
the log cannot even say whether he died. **The next live run's checklist still stands:**
`!frozen` on a live frame that is not dead is the regression detector, a `+hold` whose
`@age` stops climbing is the old defect wearing the new marker,
and `FRAME OVERDUE` means both of the first two bounds failed. Detail:
`docs/AUDIT-2026-07-29.md` 2026-08-03 §6, follow-ups 12/15/16/17.

**Bound 3 fired on a shard the same day, in a gate built to force it — and bound 1 still has
not (2026-08-03, `anima2/live_frame_overdue_gate.py`).** The paragraph above says reaching
bound 3 on purpose needs a forced-state gate, and adds that an ordinary run might stumble
into it. **The second half is wrong and the gate is what measured it wrong:**
`CraftItemCapability.max_goal_steps = 240` sits BELOW the 300-tick craft deadline, so an
ordinary craft aborts and closes its own gump *before* the frame can go overdue — waiting for
those 14 `ui=gump` samples to coincide with a deadline is not a plan. The lever that works is
a starve: `Survive` is `skills[0]` of every profession's capability planner, so a wounded
character's economy agent is ticked every tick while its capability FSM never is — nothing
answers the gump, and `expire_due` still runs because it sits upstream of skill selection.
The gate stages a tinker with no vendor and no banker, lets `craft_tongs` open its OWN gump,
wounds via `[Set RawStr 2000` + `[Set Hits 50` (raising the ceiling to ~1050, not lowering
the floor — the run logged **hp=80**, which on a default 80-point bar would have been 100%
and would have broken the starve silently), teleports off the craft spot so the rule says
`hunt`, and rides the frame to its deadline recording EVERY tick. Verdict, first attempt,
exit 0, ~4 minutes: `want=None hold=True` for **299 consecutive ticks**; then at economy tick
**301** against `deadline_tick=300` — the `>` comparator, one tick past where `expire_due`'s
`>=` would have won if the frame could yield — `frame_overdue` True and
`_repair_overdue_frame` closing `an unowned gump id=2066278152`, the craft FSM's own, with
`gumps` 1 → 0 on the next observation; then at econ **302** the hold RELEASED — `mode=hunt`,
frame still live and still overdue, which is §5's documented worst case *"a stale frame, but
alive"*, reached live. The repair's one-tick extension is the numeric signature that
distinguishes it from the cheap surface-free release (which drops the hold on the overdue
tick itself). Staging self-check: `cap_craft_steps` frozen at 2 for the whole 300-tick
window, so the surface stayed open by construction, not luck; `rule_gate_disagreement` None
throughout, so the close belongs to `_repair_overdue_frame` and not to `_detect_disagreement`'s
copy. 7 offline tests (1462 → 1469) reproduce the whole path over `MockBody` and kill three
mutants — including M1, literally the pre-review two-bound `holding` clause that livelocked,
and M3, the `>` → `>=` comparator flip. **What is still unexercised, and the gate does not
touch any of it: bound 1 (the FSM give-up ladder — it needs a transaction that FAILS, and
this gate stops the FSM stepping at all), the death override, the `OVERDUE_REPAIRS` cap (one
close spent of three), any extension beyond 1 tick, and `_clear_stale_ui`'s vendor BUY/SELL
branches at an overdue frame.** No economy claim: no gold moved, by construction. Detail:
`docs/AUDIT-2026-07-29.md` 2026-08-03 §7.

**The three defects those runs found were answered OFFLINE the same day — and the shard went
down before any of it could be verified live (2026-08-03).** 1469 → **1495 tests**, both ruff
clean, nothing committed. (1) **The silent miner is no longer silent — at the SECOND time of
asking, which is the finding.** A liveness line for non-Life agents was named in this project's
own 2026-07-30 health check, never adopted, and the identical failure recurred on the identical
runner four days later; only then was it built. `village._run_worker` now watches
`_work_recorded` — the agent's own count of terminal-or-rewarded skill outcomes, summed over
the hunt AND economy ledgers — and prints an escalating `** NO OUTPUT for 240 ticks (eps=N
unchanged since t=K, skill=mine) **`, with `eps=` on every status line, `!stalled` while it
holds and `· STALLED n` in the terminal suffix. The 240 is measured, not chosen: sample cadence
in both forge logs is 9 ticks median / 10 max, Grimm's longest HEALTHY reward-silence stretch
across them is **159 ticks**, and 240 has zero false positives on both healthy windows while
160 clears 159 by a single tick. It could not simply be "no episodes for a while": the default
roster's `townsfolk` is *defined* `work_skill=None`, so `Wander` is its whole job and it records
nothing — measured through `_run_worker`, that agent fired the alarm at 240/480/720/960 and
ended `!stalled` while behaving exactly as specified. So a new pure-telemetry field
`Agent.last_skill_name` arms the alarm only while a non-idle skill is running. The first draft
also excluded every Life (`mode is None`), which left `run_supply_pair` and
`run_warrior_village` with ZERO coverage; summing both ledgers replaced it — a healthy offline
carpenter records 0 in its hunt ledger against 234 in its economy one over 4000 ticks, with a
worst combined silence of 17 ticks. **Half the follow-up was still open at this point: the tape
now said an agent STOPPED, and still could not say whether it DIED** — `run_forge_pair` called
`hp_readout` for neither agent. (Closed 2026-08-05; see the entry below.)
(2) **The `buy_iron` wedge that ate 556 live ticks is fixed offline, and its
mechanism is named** — three defects, not the one the audit guessed at: `buy_offer_reopens`
survived its own trip, so one unlucky trip left every later trip giving up on its first
`window` tick; the "re-roll" emitted no action against a window that is a SNAPSHOT, so it
re-rolled nothing; and no give-up path closed the window the trip had opened. All three fixed
and mirrored to the tool-buy FSM, the exit-edge close carrying its safety argument in its own
docstring (it is the OWNER cleaning up, on the tick its own FSM decided the trip was over, and
only for the vendor serial that trip recorded). The audit's other candidate — the cancel is
sent but the surface does not close — is untouched and still open. (3) **Bound 1 of the
exit-edge hold became OBSERVABLE, which is not the same as exercised, and the count of
live-proven bounds is unchanged at two.** `life_runner.retirement_reason` reads
`frame.outcome` — stamped by `GoalStack._archive` since the goal stack was written — and the
runner prints `** FRAME RETIRED sell_furniture#1 age=17/180 -> giveup (bound 1: the FSM's
give-up ladder) **` per tick, with a `retired=1:1g` tally on the ~4s line. The design it
replaced would have read a marker in agent memory; that was falsified before it shipped — the
marker is a single slot every later transaction overwrites, and 116 of 117 give-ups flip to
"no ladder ran" when re-read later, the one error direction that ERASES bound-1 evidence.
**No live run happened.** The shard at `127.0.0.1:2594` was down — four probes, connection
REFUSED — so the liveness line, the wedge fix and the bound-1 signature have all never touched
a shard, `live_frame_overdue_gate.py` was not re-run (bound 3 is neither reconfirmed nor
regressed), and the two live buy gates, which had to be taught that an empty-list `BuyItems` is
a CANCEL and not a purchase, have never executed with that change. Detail:
`docs/AUDIT-2026-07-29.md` 2026-08-03 §8, follow-ups 16/17 (updated) and 18-22 (new).

**Whether the agent DIED — the reading the tape still could not take, at the FOURTH time of
asking (2026-08-05).** The process finding outranks the code again: this was named on
2026-07-30, again as follow-up 17, again as follow-up 18 ranked *"above everything else on
this list"*, and sat two more days. A death, a lost pickaxe and a dead vein all produced the
same evidence — `out+176.9` frozen, `eps=45` frozen, position still moving because the miner
kept relocating. Now `hp=<n>/<max>|DEAD` and `deaths=N` ride the per-agent status line, with
unthrottled `** DIED at (x,y) — death #N **` / `** BACK ALIVE … after n ticks dead **` edges
beside them. **Both readings are needed and this was measured, not asserted**: two runs of the
same frozen-miner shape, one with two deaths in it, produce status lines identical in `out+`,
`eps=`, `steps=`, `!stalled` **and `hp=`** — the level signal decays when the agent is
resurrected, and the only field that differs in the entire tape is `deaths=0` vs `deaths=2`.
That is exactly the hole the work-liveness alarm documented as its own: an agent cycling
death/resurrection keeps `eps=` moving through `RecoverDeath`'s terminal statuses. It landed
in **`village._run_worker`**, not in `run_forge_pair`'s `grimm[…]` group as follow-up 18
specified — one group on one runner cannot carry a one-tick edge against a 4.0 s sampling
loop, and the worker covers all six runners, already holds the tick's observation, and prints
its snapshot directly beneath every runner's aggregate line. `_pipeline_line`'s inline
re-derivation of the same readout is gone (it was the only hp on any village line and no
other runner could reach it), and the artisan beside it — the half of that pipeline that earns
the gold — has one for the first time. **The obvious one-line implementation is wrong, and it
was measured rather than reasoned about**: `Agent.memory["death_episode"]` already counts
alive→dead edges, but a Life owns TWO agents with separate memories and ticks one per
orchestrator tick, so on a real `CarpenterLife` a `sum` reports **2** for one death and a
`max` reports **1** for two. Both are pinned as mutants failing 4 of 18 tests each, each on
the assertion written for it. **Offline only** — the shard was still down, and no forge log
has ever contained a death. 1495 → **1500 tests**, ruff clean. Detail:
`docs/AUDIT-2026-07-29.md` §9, `docs/MONITORING.md`.

**All seven Life-construction sites can be tuned, and the searcher still cannot tune them
(2026-08-05).** Audit follow-up 2, closed. Two of seven sites had the channel; the other
five were hand-written `XLife(...)` calls inside `run_forge_pair` (the FLAGSHIP
positive-margin tinker), `run_supply_pair` (a woodsman AND a carpenter),
`run_warrior_village` and `run_artisan_mage_village`. The hazard the follow-up named was
never the missing argument — it was *"threading a dict through hand-written construction
per runner (no single point that stays true)"*, i.e. five copies of a check whose only
value is that everyone runs it identically. So the change is three shared pieces and four
thin call sites: `validate_knobs` (the check and its refusal text, lifted out of
`LifeSpec.__post_init__`, which delegates now), `build_tuned_life` (`LifeRunner.build_life`
for runners with no spec, reading the allowlist off the CLASS so no site can declare the
wrong one — which `LifeSpec`, whose factory is a lambda, structurally cannot do), and
`_route_knobs` (CLI role routing, replacing a blanket guard that was an allowlist of the
two wired runners rather than a property). **The PLACEMENT is the part a shared helper
cannot choose for you**, and the mutation test found the mutant is worse than a late
traceback: with the pre-flight check removed, `run_forge_pair`'s login loop catches the
resulting exception per role and a one-character typo prints *"the pair needs both;
aborting"* — two spawned accounts and a message blaming the shard. `--knob
[ROLE:]KEY=VALUE` now reaches all six Life-bearing runners; `--supply-pair` REQUIRES the
prefix, because both its Lives have a `bank_reserve` and picking one silently is the same
misreporting the roster refusal exists to stop. Two single-source repairs fell out of it:
`tinker_life.bank_trip_surplus` became a named read point when the staged banner became
its second reader, and `run_supply_pair` prints reserves at all for the first time.
**What this does NOT do is the half §E's criterion is about.** The channel is complete and
nothing is pushing values into it: `Genome`'s four axes map onto no knob, and three of them
are not knob-shaped even in principle (`profession` is identity and the allowlist REFUSES
it by design, `sociability` is a `Persona` field, `cognition_tier` builds an LLM client),
so a genome→Life bridge is a design question and not wiring. `foundry/eval.py::_build_agent`
builds a bare `Agent` and has never measured a Life at all. **Offline only** — the shard is
still down, and no tuned knob has ever changed a live trajectory on any site, which is §E's
criterion word for word and why precondition (a) is still not met. 1500 → **1506 tests**,
ruff clean. Detail: `docs/AUDIT-2026-07-29.md` §10.

**The knob that rode a second channel, and §E's first named axis (2026-08-05).** Audit
follow-up 4, closed. `knobs.py` calls itself "the ONE clamped read every tuning knob goes
through" and its own opening paragraph had to carve out `wander_leash`, which arrived via
`Staged.leash` and was clamped a second way inside `Wander._homeward`. The unification went
toward the FLOOR and not toward the class-default fallback, and that direction is the
finding: the fallback is DISCONTINUOUS (`-1` → 8 while `1` → 1 and `2` → 2), so a searcher
stepping one below the floor would leap to the shipped default instead of resting on the
boundary — disqualifying for a knob whose whole purpose is to be searched. The floor is 1
rather than `knob_int`'s natural 0 for `disagreement_ticks`' reason: the only tile inside a
0-leash is `wander_home` itself, so the skill would be disabled rather than tuned. One
stated behaviour change — a stored `0` used to be honoured and now clamps; nothing passes 0.
**"Needs no Life work" was the follow-up's one wrong word.** `wander_leash` is the only knob
something ELSE already writes: every runner calls `set_leash(home, derived)` after
construction, including Sten's live-caught `min(max(1, shop_reach), PICKUP_RADIUS - 1)`.
Left alone that write would silently overwrite a tuned value — a channel that reports
success and changes nothing, which is the exact defect the previous two entries have been
closing, wearing a knob that LOOKS wired. So the tuned value wins, `_leash_tuned` records
the difference between "unset" and "chosen", and the derived value keeps every bit of its
old authority when untuned (mutation-tested: the unconditional write fails 2 tests). Three
banners had to move off a local or a module constant onto a read of the built Life —
`LifeRunner.staged_line`, `run_supply_pair` (which now says `TUNED from a derived N`) and
`run_forge_pair` — the third time in three days. **Offline only**, and it is an axis nothing
searches: `Genome` has no leash field, and adding one is the same design question §10.5
names. 1506 → **1510 tests**, ruff clean. Detail: `docs/AUDIT-2026-07-29.md` §11.

**The headline defect class in constant form, merged — and the pair the audit ranked last
was the dangerous one (2026-08-07).** Audit follow-up 6, closed. The concordance lattice
catches a rule and a gate disagreeing about a VALUE and structurally cannot catch them
disagreeing about a DEFINITION, because a constant written twice is numerically locked
until somebody edits one copy — and the edit IS the failure. Two of the three pairs were
one DECISION recorded twice (`capabilities._UPGRADE_RESERVE`'s comment was near word-for-word
`warrior_life.UPGRADE_RESERVE`'s; the fetch gate's said it read the class attribute "so it
stays in lockstep with the craft gate that consumes it", a property no comment can hold).
Both moved into the skills layer, the only place both readers can import from — `capabilities`
importing a Life would be a cycle. **The third was ranked last and described only as
"`0x0E21` restated as a literal", and it was the one that mattered:** `skills/warrior.
BANDAGE_GRAPHIC` sat three lines below an import of the frozenset holding the same number,
and the two were read by OPPOSITE SIDES of one decision — `WarriorLife.decide` counted the
single art while `buy_bandage`'s gate counts the whole family. Measured on one constructed
observation with the family grown by one graphic: the rule counts 3 and wants `buy_bandage`,
the gate counts 23 and refuses — a permanent want-vs-refuse standoff one added graphic away,
invisible to the concordance suite because a singleton family agrees at every lattice point.
The rule reads the family now, so both names are bound to the same frozenset object and the
disagreement is unrepresentable rather than merely absent. `BANDAGE_GRAPHIC` survives, because
a vendor OFFER is placed against one art and not a family — as `min(BANDAGE_GRAPHICS)`, not
`next(iter(...))`, since a frozenset has no order and a buy offer that changes identity
between runs is the worst available failure. The three tests assert `is` rather than `==`
(equality passes again the moment a second definition computes the same number) and walk the
AST to reject a bare literal; both mutants fail exactly one test each. **Nothing behavioural
changed** — every number is identical today and no live evidence is claimed. 1510 →
**1513 tests**, ruff clean. Detail: `docs/AUDIT-2026-07-29.md` §12.

**The buy FSM's missing half — and measuring it changed the fix (2026-08-07).** Audit
follow-up 20, closed. `8cdd2f0` gave `_buy_step` an already-open-window branch (the server
ignores a popup request while a window is up, so a leftover window throws the whole trip
away) and never mirrored it into `_toolbuy_step`. The follow-up called that "a real
asymmetry between two copies of the same FSM, and it is unmeasured, so it was not guessed
at" — and it was right to say unmeasured, because **the asymmetry was not one-sided**.
Having no pre-check, `_toolbuy_step` got a genuinely FRESH window per re-roll: after its own
cancel it simply waited and re-requested. `_buy_step`, with the branch, re-adopted the window
it had just cancelled while the observation lag lasted and re-read an identical snapshot — a
window IS a snapshot — spending an attempt on it. So each FSM had one half, and mirroring
the branch alone would have taken the tool buy's good half away. The follow-up's own
CONDITIONAL remedy ("the fix, if one or two ever proves too few, is a marker saying THIS trip
just cancelled this window") is therefore mandatory rather than optional: it is what stops
the mirror being a regression. Driving the real FSMs against a simulated shard and counting
FRESH window openings per trip (5 is the whole budget): without the marker 3/3/2/2/1 by lag
0/1/2/3/5, with it **5 at every lag**, identical on both FSMs. The stage is ONE method with a
namespace argument now, because copying the branch would have produced a third thing to keep
in step. **An unbounded loop fell out of moving one line**: the `POPUP_TIMEOUT` count used to
run BELOW the window branch, and the foreign-window branch returned its cancel before
reaching it — so a foreign window that never closed produced a cancel every tick forever,
bounded only by the frame deadline outside the FSM, never by the stage timeout that exists to
bound it. Whether it ever fired live is unknown; no log distinguishes "cancelled repeatedly"
from "waiting". Both mutants implemented: mirroring without the marker fails 4 tests,
reverting the tool path fails 3 — all on the `toolbuy` parametrization and none on `buy`,
which is the asymmetry itself visible as a test signature. **Offline only**; the lag table is
a simulation and neither FSM has run on a shard since. 1513 → **1521 tests**, ruff clean.
Detail: `docs/AUDIT-2026-07-29.md` §13.

**Reading the week's own work, and finding the fix's own defect (2026-08-07).** Five commits
of hot-path change had landed offline with no live budget to validate any of them, so the
next step was to read them rather than write more. The finding worth the entry:
`wander_leash` went into `WarriorLife.KNOBS` two days earlier, and `KNOBS` is INHERITED — so
all five Lives and all six runners accept it. **`run_warrior_village` never calls
`set_leash`**, deliberately (a warrior roams while hunting), and `Wander._homeward` returns
on a missing `wander_home` *before it ever reads the leash*. So `--warriors 5 --knob
wander_leash=3` accepted the knob, stored it, read it back as 3, and steered nothing — a
channel reporting a success that cannot happen, which is the exact defect the previous three
entries are all instances of, reintroduced BY the fix for it, through the one property
nobody checked: that adding a knob to a base class adds it to runners that cannot use it.
The repair is `leash_readout`, the single read of what the leash will ACTUALLY do rather than
what it is set to (`3` vs `3 (inert: no wander_home)`), used by every banner; `wander_home`
became a named read at the same time so `_homeward`'s shape check and the readout's cannot
drift. Not fixed by giving warriors a home — the absence is the design and the banner was
the thing lying. Two smaller findings: `run_artisan_mage_village`, the sixth and last runner,
printed neither of its mage's tunable numbers; and the popup-stage merge tightened
`POPUP_TIMEOUT` (the wait ticks now count), measured as degrading only at lag >= 12 and never
falling below what it replaced. **Five occurrences of the lying-banner class is enough, so it
is a test now**: an AST walk asserting every inline runner READS one of the four clamped read
points and the two `LifeSpec` runners still reach `staged_line`. It asserts reading, not
printing, because a printed module constant is accurate exactly until somebody tunes that
runner. Also verified: all 100 modules import clean, including the untested `live_*` gates.
**Nothing live** — the shard is down and the whole week remains unvalidated on a shard.
1521 → **1523 tests**, ruff clean. Detail: `docs/AUDIT-2026-07-29.md` §14.

**An independent review of the week, and the five defects a self-review missed
(2026-08-07).** The self-review above found three things; an independent high-effort pass
over the same range found five more, all real and all reproduced before being fixed — which
is the entry's point, since reading your own diff is worth doing and is not a substitute.
(1) `wander_leash` is clamped downward only and OVERRIDES leashes that are correctness
constraints: `run_forge_pair` and `run_supply_pair` both derive
`min(max(1, shop_reach), PICKUP_RADIUS - 1)` so the agent stays in pickup range of its own
drop, and `--knob wander_leash=40` silently stops the flagship chain closing. Not fixed
with a ceiling — an exploration radius that cannot explore upward is not an axis — but by
making the banner name the override. (2) The new `set_leash` precedence applied to EVERY
call, so `live_frame_overdue_gate`'s deliberate mid-run re-leash became a no-op against a
tuned Life and the gate could report a bogus pass; a tuned value now outranks the STAGING
call only and a later one wins. (3) The foreign-window cancel was sent exactly once: a
dropped packet ended the trip on POPUP_TIMEOUT with the blocking window still open, and the
exit-edge repair deliberately refuses another vendor's window, so nothing else would close
it. The code it replaced was unbounded but self-healing; it is now both. (4) The per-trip
counters survive a frame torn down MID-trip, because `_CLEANUP_KEYS` is only popped when a
trip ends normally — and bound 2 makes that teardown a measured shape, not a hypothetical.
Cleared at the start of a new goal, NARROWLY: the first attempt popped all of
`_CLEANUP_KEYS` for all four families, broke six bank tests and was reverted, because
clearing the stage keys is a wider behaviour change on the flagship path that nothing
offline can validate (follow-up 23). (5) `_route_knobs` silently dropped a duplicated knob
— `bank_reserve=1` and `carpenter:bank_reserve=2` are different parser keys landing on the
same knob — which is the one failure its own docstring says it exists to prevent. All five
mutation-checked. **Offline only**, and two of the five were latent rather than live.
1523 → **1527 tests**, ruff clean. Detail: `docs/AUDIT-2026-07-29.md` §15.

**The shard came back, and the week met it (2026-08-07).** Everything from the death
readout through the review fixes was written with `127.0.0.1:2594` down, and every entry
ends in some form of "offline only". The shard answered. **The first live attempt would
have died at the handshake**: anima-client had reached schema **26** while
`SUPPORTED_SCHEMA_VERSION` sat at **18** — eight versions of drift, accumulated silently,
invisible until somebody tries to run, and NOTHING was broken by it. The drift alone was
the outage. Verified by diffing the serializer rather than the changelog (452 insertions,
seven deletions, all seven being the changelog sentence, the constant, a version test and
gump-element lines already discussed), then cross-checked mechanically because reading a
diff is how a rename gets missed: all 19 action types `contract.py` emits are still
accepted and all 57 observation keys it reads are still emitted. Proven rather than
asserted — a handshake probe read back `schema_version=26 player=17563` and a real
observation. **Then three gates, three passes.** `live_buy_goal` (`iron=0->15
gold=200->125 opens=1 cancels=0`) and `live_toolbuy_goal` (`tools=0->1 gold=100->87`) are
the two §8.5 recorded as NOT RUN: they had never executed with the empty-list-cancel
filtering, let alone with the merged popup stage, and the tool-buy one is the FSM that
GAINED the entry-edge branch. `live_frame_overdue_gate` passed all seven flags and
reproduced 2026-08-03 almost tick for tick — overdue at economy tick **301** against
`deadline_tick=300`, the repair closing the craft gump on that tick, the hold extended
exactly ONE economy tick and released into `mode=hunt` with the frame still live, after
**299** held ticks with the FSM provably starved. It also clears review finding 2 by
construction, being the gate whose mid-run re-leash invariant that finding was about.
**What three green gates do NOT establish, because the temptation is to read them as "the
week is validated":** `cancels=0` on both buy gates means the re-roll path, the marker, the
foreign-window branch and the cancel re-send — the actual subject of the last two entries —
were never entered; bound 1 is still unexercised; the death readout has never fired, so a
field that has only ever printed 0 is not a tested field; no tuned knob ran; the forge pair
did not run, so the seven-site wiring, the leash axis and the banner work have still never
printed on a shard; and these are minutes-long staged gates, not the 1800-tick days this
project actually learns from. 1527 tests, ruff clean. Detail:
`docs/AUDIT-2026-07-29.md` §16.

**A tuned knob changed a live trajectory — §E's criterion, met (2026-08-07).**
`--forge-pair --knob bank_trip_surplus=10 --ticks 1200`, on the flagship miner->tinker
chain, with the predictions written down BEFORE the run so it could be refuted rather than
narrated. The urgent bank branch is `gold > bank_reserve + bank_trip_surplus`: 82+75=157 by
default, 82+10=92 tuned, so any admission in **93..157** is a decision the default rule
could not make. Tuning DOWN was deliberate — 2026-08-03's live knob run was behaviourally
INERT because the carpenter's gold never reached the tuned value, and a lower bar cannot
fail that way. All three predictions held: the banner printed `trip surplus 10` (the first
of §10's five newly-wired sites to carry a value on a shard); **six of nine `bank_gold`
admissions landed at 105/112/112/117/117/117**, every one inside the band; and the run
banked **293g against the untuned baseline's 193g**, net +410g. Three other things printed
live for the first time: the death readout (`hp=83/83 deaths=0` — exercised, NOT tested,
since a field that has only printed 0 proves only that it prints), `leash_readout` (bare and
correctly so, both its branches silent), and the work-liveness alarm firing TRULY on the
flagship pair, naming its culprit skill (`eps=41 unchanged since t=452, skill=buy_iron`).
**And the run measured follow-up 19, whose only stated blocker was that nobody had measured
it.** `retired=44:41a/3x` — all three expiries are `buy_iron` at exactly `180/180`, with 55
samples showing the frame ADMITTED, the gate READY, `mkt_phase=craft`, and no `+hold`,
`!frozen` or `!overdue`: the rule wants it, the gate allows it, and nothing executes. The
give-up sets `cap_buy_finished_goal_id` and NOT `cap_run_finished_goal_id`, the marker
`CapabilityGoalComplete`'s give-up branch reads — a marker `market.py` sets on the sell and
bank paths and on neither buy path. Cost: **540 dead economy ticks, 45% of the run**, on the
one positive-margin chain here. **What the run does NOT establish**: one axis at one value
with no controlled A/B; no evidence the steer was GOOD rather than real (the same run
starved working capital to 77 gold against a 75-gold iron batch, and whether the knob CAUSED
the stalls is unproven — the trips died in the popup stage, not on affordability); the
re-roll path and the closing-window marker still never fired; and bound 1 is STILL
unexercised, since all three give-ups retired through bound 2 instead. 1527 tests, ruff
clean. Detail: `docs/AUDIT-2026-07-29.md` §17.

**Follow-up 19 applied, and the re-run did NOT prove it (2026-08-09).** `market.py` now
writes the neutral `cap_run_finished_goal_id` on both buy families' return phases, where
the sell and bank wrappers have written it since forge1/forge13 — set UNCONDITIONALLY,
because `CapabilityGoalComplete` tests achievement FIRST and returns SUCCESS there, so it
is the give-up branch's KEY and not a give-up flag. Offline, a frame that used to ride to
`180/180` retires `-> giveup` at **age 17**; mutation-checked precisely (removing only the
two new markers) at 1 failing test; and `live_buy_goal` re-run after the change still
reports `exact_goal_frame_succeeded_once`, so an achieved buy still retires as achieved.
**Four bound-2 tests were standing on the defect** — `_wedged_buy_life` documented its own
third leg as "a BUY frame has no give-up ladder at all" — and moved to a CRAFT vehicle,
since `CraftItemCapability` still writes no marker; keeping them on a buy frame would have
meant preserving the defect to keep testing around it. One discarded experiment worth
knowing: starving the buy FSM by wounding to 12 HP does NOT give a bound-2 world, because
it starves `expire_due` too (that runs inside `Agent.tick`) — bound 3's livelock, not
bound 2's deadline. **The live re-run settles nothing about the fix, and the reason is one
row of the table**: the tinker never needed to buy iron at all the second time (Grimm's
delivery covered it), so `buy_iron` was admitted on **0 samples** against 60 before. The
zero expiries are zero because there were no buy frames, not because the marker closed
them, and **bound 1 remains unexercised live**. The economics differ (233g vs 293g) on
supply variance, not on the fix — two runs on different mining luck are not an A/B, and
nothing here should be attributed to follow-up 19 in either direction. What the re-run does
show is no regression: 27 retirements, all achieved, zero expiries, zero deaths. **Bound 1
needs a purpose-built gate, not patience** — the same conclusion §7.1 reached for bound 3,
now recorded as follow-up 24: a `live_buy_giveup_gate.py` staging a vendor with no iron
offer would print bound 1's first live signature AND exercise the re-roll path and the
closing-window marker that four sections record as never having run on a shard. One gate,
four gaps. 1528 tests, ruff clean. Detail: `docs/AUDIT-2026-07-29.md` §18.

**Bound 1, live — the last of the three, on a purpose-built gate (2026-08-09).**
`anima2/live_buy_giveup_gate.py`, follow-up 24: first attempt, exit 0, all eleven flags.
The principle is §7.1's, restated — **reaching a bound needs forced state, not patience** —
after two 1200-tick forge runs failed to produce a failing buy by luck. The forced state is
ONE substitution and nothing is injected: where `live_buy_goal` stages a Blacksmith whose
stock includes iron, this stages a **Healer** at the same spot — a real vendor with a real
Buy entry and a real shop window, stocking bandages and no iron — so the FSM meets a world
where its offer genuinely does not exist, which is the condition `OFFER_REOPEN_ATTEMPTS`
was written for. `rerolls=4/4 cancels=5 iron=0 retired=(1, 'buy_ingots', 21, 180,
'giveup')`. **Age 21 against a 180-tick budget**, where the same shape burned all 180
eleven days running. `cancels=5` is four re-roll closes plus the exit-edge close, and
`nothing_was_bought` confirms every one was ServUO's EndVendorBuy rather than a purchase —
the distinction two live gates once got wrong. **One run closed four recorded gaps**: bound
1 (named as unexercised in six sections), the partial-subset re-roll path, §15's
`{ns}_closing_window` marker, and follow-up 19's marker, none of which had ever run on a
shard. All three bounds of the exit-edge hold are now live-proven. **What it does not
prove**: that an ordinary forge day retires a buy frame this way (the 2026-08-09 re-run
never entered the path); anything about contention, since it is a closed one-capability
fixture; or that `OFFER_REOPEN_ATTEMPTS = 4` is the right NUMBER, since this vendor never
had the offer at all. And it has no offline reproduction — the bound-3 gate has seven —
because that needs a `MockBody` able to answer a vendor window, which is follow-up 22,
now the blocker for two live artifacts rather than one. 1528 tests, ruff clean. Detail:
`docs/AUDIT-2026-07-29.md` §19.

**A vendor in `MockBody`, and the bound-1 gate reproduced offline (2026-08-09).** Audit
follow-up 22, deferred twice on the argument that "adding a vendor to `MockBody` is a
production-code change for a test's benefit". What retired that argument is that two live
artifacts ended up standing on the other side of it: §8.2's Life-level wedge reproduction
ran from a scratch harness that no longer exists, and §19's bound-1 gate — the only live
proof of the give-up ladder there is — shipped with NO offline reproduction, where the
bound-3 gate has seven. `MockBody`'s own docstring calls it "a test double", so "production
code" was always the weaker half of the framing. **The design decision worth defending is
that `MockVendor.windows` is a list of OPENINGS, not a stock list**: ServUO shows goods in
partial SUBSETS, which is the pairing bug `OFFER_REOPEN_ATTEMPTS` exists for, so one element
means every opening is identical (§19's Healer) and several let a re-roll genuinely find
what the first window lacked. A flat stock would have made the re-roll path untestable by
construction. The gate is now reproduced against the real `_buy_step` with nothing injected,
with a control that stocks the offer and requires the buy to still complete — without it the
give-up test would pass against an FSM that could no longer buy anything. Mutation-checked.
**Two process notes.** The first mutation run reported PASS with no mutant applied: a
mutation test that does not assert it mutated something is a green light measuring nothing.
And the control first failed with `bought=0`, which reads exactly like a broken FSM and was
a FIXTURE bug — the vendor's for-sale graphic is not one of `INGOT_GRAPHICS`, those being
the pack pile-size variants a bought stack merges into; the fixture reads
`BuyIron.buy_offer_graphic` off the skill now so it cannot drift. **And the FRAME half followed the same day, landing within TWO TICKS of the
shard**: a Life whose rule can reach exactly one branch — closed by construction, with no
`banker_spot` at all so both bank branches go without depending on a gold threshold a knob
could move — retires `(1, 'buy_iron', 19, 180, 'giveup')` against the live gate's
`(1, 'buy_ingots', 21, 180, 'giveup')` on the same 180-tick budget, with five vendor
openings matching the live `cancels=5`. That correspondence is the real result: it says the
double models the exchange faithfully, which is the only thing that makes an offline
reproduction worth having. The control retires `achieved` at age 7 and is the load-bearing
half for follow-up 19's SAFETY rather than its benefit — the marker is written
unconditionally, so if achievement ever stopped being tested first, every successful buy
would report as a give-up. **Bound 1 is now covered at three levels — FSM, frame, and a
shard — where eleven days ago it was covered at none.** Inert for
every existing fixture — `vendors` defaults empty — which is why all 1528 prior tests pass
unchanged. 1528 → **1534 tests**, ruff clean. Detail: `docs/AUDIT-2026-07-29.md` §20.

**Regression sweep: four live gates, four passes, and two of them byte-identical
(2026-08-09).** `MockBody` cannot reach a shard, but `market.py` had changed the same day
(follow-up 19) and the gates had not been re-run since. `live_buy_goal`, `live_toolbuy_goal`,
`live_buy_giveup_gate` and `live_frame_overdue_gate` all passed, exit 0. **The finding is
not the passes — it is that both FORCED-STATE gates reproduced their previous verdicts field
for field**, across different accounts, different vendor serials and different shard
sessions: bound 1 gave `rerolls=4/4 cancels=5 iron=0 retired=(1, 'buy_ingots', 21, 180,
'giveup')` twice, and bound 3 gave `overdue=(299, 301) repair=(299, 301) released=(300, 302)
extension=1 ... craft_steps_seen=[2]` twice. That determinism is what makes these gates
regression DETECTORS rather than anecdotes: a forge run's numbers swing with mining luck
(293g in one entry, 233g in the next on the same command), so a forge run can only say
"something happened", while a gate with a fixed verdict string can say "this specific thing
still happens" — and a change that shifts age 21 to age 180 will read as a diff, not as
noise. It also retro-justifies §7.1's insistence on forced state over patience: the property
that makes a bound reachable on demand is the same one that makes it comparable across runs.
**Not covered**: no forge day (these are closed fixtures, minutes long, and the 1800-tick
shapes this project learns from are untouched); the sell and bank families, which follow-up
19 did not change and no gate here covers; and `MockBody`'s vendor itself, which is not
live-verifiable by construction — the two-tick agreement in the previous entry is evidence
about one exchange, not about the double. 1536 tests, ruff clean. Detail:
`docs/AUDIT-2026-07-29.md` §21.

**Next (forward pointer corrected 2026-08-02):** NOT Phase 7 item 2. The `--genomes 20`
evolution-vs-random rerun is DEFERRED by CLAUDE.md's "Two roadmaps, one decision",
which is the authority on what runs next — it applies AUTONOMY-ROADMAP.md §E's criterion
("Re-run evolution versus random only when
every searched axis changes a meaningful live trajectory; a larger budget alone is not
an autonomy milestone") and names two preconditions: (a) the genome's axes can steer a
full Life — Life thresholds as constructor parameters routed through single sources
(audit proposal 5); and (b) at least one positive-margin economy loop exists, so
gold-per-life fitness means something. Read CLAUDE.md before acting on any "next" here:
a stale forward pointer of exactly this shape almost burned a multi-hour single-GM live
budget (docs/AUDIT-2026-07-29.md, top risk 4 / proposal 4).

