# Phase 6 — Work Breakdown

Phase 6 = **the living village** (DESIGN.md §10's society scale-out, carried out
of Phase 5 to keep that phase coherent around measurement + evolution — see
PHASE5.md's "Notes carried into Phase 6" section) **plus** the eval/evolution
follow-ups Phase 5 item 4's own live gate surfaced. Two threads, one phase,
because both are about the same underlying gap: Phase 2-5 built agents that
*work* and *learn within a session*, but nothing yet makes a session **matter
to the next one** — an agent's reflective takeaways vanish when the process
exits, its trades with neighbors leave no record anyone (agent or human) can
read back, its forum posts are isolated one-shot blurbs, and the evolution
loop's own comparative gate came back an honest tie because three of four
genome axes have nowhere to bite in the one live eval scenario that exists.
Both threads are "make what already happens leave a durable, checkable trace"
— the first for the society layer (village.py/forum.py), the second for the
Foundry kernel (foundry/eval.py/evolve.py).

**Thread A — the living village (items 1-3).** Generative Agents' three
pillars applied to what's *already live-verified and running* rather than
invented from scratch: persistent memory across sessions (item 1, closing
the gap that `EpisodicMemory`/`ReflectionMemory` are pure in-process state
today — `agent.py`, `memory.py`), relationships mined from the real trade/
hunt/market interactions the economy loop already exercises live (item 2 —
`skills/smelt.py::MineSmeltDeliver`, `skills/craft.py::Blacksmith`,
`skills/market.py::BlacksmithMarket`, `skills/hunt.py::Hunt`), and the
uotavern forum becoming a genuine continuing chronicle instead of an
isolated daily post (item 3 — `forum.py`). Note what's *already* persistent
and therefore out of this thread's scope: a character's own skills/gold/
items/position live server-side in ServUO itself (free, no anima2 code
needed — the same account name in `village.py`'s roster is the same
character every run), and Phase 4 item 5's milestone achievements already
survive a restart via `data/milestones.jsonl`. What's missing is the
**reflective** layer — the `Insight`s Generative Agents' own "reflection
tree" is built from — and the **social** layer — who worked with whom, and
what the village's own public record says about it.

**Thread B — richer eval scenarios (items 4-6).** PHASE5.md item 4's live
gate closed with an honest tie and named the reason precisely in its own
module docstring (`foundry/evolve.py`): the one live-scoreable scenario
(`foundry/eval.py::SCENARIOS["mining"]`, a bare `Mine()` with no cognition
and no persona speech) leaves `sociability`, `cognition_tier`, and
`deliver_threshold`'s counterpart `profession` axis live-inert, so both the
mutation-guided and random searches sample near-identical phenotypes and the
residual fitness spread is Mining's own per-swing gain-chance RNG, not
search quality. Items 4-5 give three of those four axes a real live signal
each (`deliver_threshold` already has one, from Phase 4 item 4); item 6
reruns the comparative gate on the enriched harness and reports whichever
way it lands, honestly, per this project's own established "report a tie as
a tie" discipline (PHASE5.md item 4).

Status legend: ✅ done · 🚧 in progress · ⏳ todo. **Items 1-3 are done
(persistent lives; the village chronicle relationship ledger; the forum as
continuing chronicle — all three live-verified) — items 4-6 remain ⏳ todo.**

**Dependency order.** Threads A and B touch disjoint code (village.py/
forum.py/memory.py vs. foundry/eval.py/evolve.py/cognition.py) and can land
in either interleaving or in parallel. Within each thread: item 2 reuses
item 1's "load at construction, append on write, mirrors skill_library.py's
ledger convention" pattern (a learning-curve dependency, not a hard one —
item 2 could land first, it would just be reinventing a pattern item 1 also
needs); item 3 has a real dependency on both — it composes item 1's
persisted insights and item 2's relationship events into forum prompts, so
it lands last in thread A. In thread B, item 5 is easiest to justify once
item 4 exists (a second scenario makes "does `cognition_tier`/`sociability`
matter" a more meaningful question than asking it of the one existing
scenario alone) but has no hard code dependency on it; item 6 needs both 4
and 5 landed — it's the rerun of the comparative gate against the now-richer
harness.

**Constraints, unchanged from every prior phase (DESIGN.md §2), reaffirmed
per item below where they're load-bearing:** the fast loop stays
deterministic — no LLM, no file I/O inside `Skill.step()`; every new disk
write happens either on a cognition/reflection background thread (mirroring
`cognition.py::ReflectingCognition._reflect_bg`'s existing file-writing
precedent from Phase 4 item 1) or in `village.py`'s own per-agent worker
thread (mirroring how it already prints status lines and records skill-tuner
outcomes) — never inside a skill's fast-loop `step()`. The brain never
touches packets — no item below needs a `contract.py` change (verified per
item; Control-plane orchestration inside `foundry/eval.py`/`control.py`
covers everything items 4-6 need). LLM calls stay slow-loop-only,
provider-abstracted through `llm.py`, and JSON-flaky-tolerant (item 5 reuses
`build_tiered_clients()`/`LLMCognition` unchanged). All new persisted state
follows the established `data/*.jsonl` convention — lazily created,
gitignored, corrupt-line-tolerant on read, `threading.Lock`-guarded for
same-process concurrency; `anima2/foundry/_filelock.py`'s cross-process
`fcntl.flock` is reserved for the kernel package's own ledgers
(`archive.jsonl`/`eval_results.jsonl`) per its own module docstring, and
none of thread A's new ledgers cross that boundary (they're written by
`village.py`'s own single process, multiple threads — the exact scenario
`skill_library.py`'s/`curriculum.py`'s plain `threading.Lock` already
covers, not the multi-process scenario `_filelock.py` exists for) — so
thread A's new ledgers use a plain lock, matching `skill_library.py`/
`curriculum.py`, not `_filelock.py`. The offline suite stays green
(530 passing at Phase 6's start) after every item, and `ruff check .` clean.

---

## Item 1 — Persistent lives: disk-backed reflection memory ✅

**Close the literal gap in "persistent lives across sessions": an agent's
distilled `Insight`s vanish when the process exits.** Everything else that
makes a character "the same soul" across runs already exists or already
persists — ServUO's own server-side skills/gold/items/position (free), and
Phase 4 item 5's `data/milestones.jsonl` restart-survives ratchet
(`curriculum.py::CurriculumController._load_achieved`). `ReflectionMemory`
(`memory.py`) — the Generative-Agents-style layer `ReflectingCognition`
periodically distills recent episodes into (PHASE2.md B1) — is pure
in-process `deque` state today, with no disk backing at all. Raw
`EpisodicMemory` deliberately stays session-only (see "Key design
decisions" below) — `Insight`s are its designed compression, and the right
persistence unit.

### Scope

- **`memory.py::ReflectionMemory`** gains two new, both-optional constructor
  kwargs: `persist_path: Path | None = None` and `agent_key: str | None =
  None`. `record()` appends the existing in-memory `deque.append` exactly as
  today, **plus**, only when `persist_path` is set, one JSON line
  (`{ts, agent_key, text, episode_ticks, episode_count}`) to `persist_path` —
  guarded by a new module-level `threading.Lock` (mirrors
  `curriculum.py::_milestones_log_lock`/`skill_library.py::_ledger_lock`
  exactly; several agents' `ReflectingCognition`s could reflect around the
  same wall-clock moment in a multi-agent `village.py` roster, same
  justification those two ledgers already give). `persist_path=None` (the
  default) makes this a byte-for-byte no-op — zero behavior change for every
  existing `ReflectionMemory()` caller (today: `ReflectingCognition.__init__`
  building its own default instance).
- **`memory.py::load_insights(path, agent_key, capacity=20) -> ReflectionMemory`**
  (new module function): reads every parseable line of `path` (missing file
  → empty, corrupted/partial trailing line skipped — the exact
  `skill_library.py::_read_ledger`/`curriculum.py::_load_achieved` "degrade,
  never crash" discipline), filters to `agent_key`'s own rows, seeds a
  `ReflectionMemory(capacity=capacity, persist_path=path, agent_key=agent_key)`
  with the last `capacity` matching `Insight`s (oldest-first, so the deque's
  own `maxlen` truncation keeps the most recent — matching `ReflectionMemory`'s
  own recency contract), and returns it already wired for `record()` to keep
  appending to the same file. This is the "load at construction, write
  incrementally" idiom `SkillLibrary`/`CurriculumController` both already
  establish, applied to the one place in this codebase that doesn't have it
  yet.
- **`village.py`** gains `--persist-insights` (opt-in, unset by default —
  zero effect on any currently-passing roster). When set **and** reflection
  is wired at all (today: only via `--llm-tiers`, the one flag that builds
  `LLMReflection`/`ReflectingCognition` — see `run_village`'s existing
  `tiered_clients is not None` branch), `village.py` calls
  `load_insights(INSIGHTS_PATH, persona.name)` per agent **before**
  constructing `ReflectingCognition`, and passes the result as `insights=`
  (an already-existing constructor kwarg — verified in `cognition.py`,
  `ReflectingCognition.__init__(..., insights: ReflectionMemory | None =
  None)`). No change to `ReflectingCognition` itself is needed for the read
  side; only the write side (this item's `persist_path`/`agent_key`
  plumbing) is new.
- **`data/insights.jsonl`** — new, gitignored (extends the existing `data/`
  blanket ignore), lazily created on first `record()` with `persist_path`
  set.
- **`live_persistent_lives.py`** (new) — the live gate driver (see below),
  reusing `live_reflect.py`'s wiring shape (a scripted/forced reflection
  client for determinism, mirroring `live_wiki_report.py`'s own
  `_CyclingJudgeClient` precedent) and `live_common.py`'s conventions
  (`RecordingBody`, `wipe_area`, `login_throttle`, `print_gate_verdict`).

### Key design decisions

- **Insights, not raw episodes, are the persistence unit.** `EpisodicMemory`
  stays session-only, unchanged. Two reasons, stated rather than assumed:
  (a) volume — episodes record every reward-bearing tick (`agent.py::tick`'s
  existing filter), hundreds per session, where insights are already the
  designed few-per-reflection-cycle compression (`ReflectingCognition`'s own
  `every_n_reconsiders`/`min_new_episodes` cadence caps how often this even
  happens); (b) it mirrors Generative Agents' own "reflection tree" idea
  directly — persist the *distillation*, not the *stream*, which is also
  what keeps this item small and landable rather than opening a second,
  bigger "what's the right episodic-memory persistence format" design
  question this phase doesn't need to answer.
- **Keyed by persona name, not a new identity system.** `village.py` already
  names characters deterministically from roster order (`f"{prof.persona_name}
  {idx}"`, e.g. `"Grimm0"`) and that name is already the restart-survives key
  `curriculum.py::_milestones_log_lock`'s ledger uses. Reusing it here rather
  than inventing an agent-id scheme keeps this item's blast radius to one
  file (`memory.py`) plus `village.py`'s own wiring.
- **`persist_path=None` is a byte-for-byte no-op — restated because it's
  load-bearing, not because it's novel.** Every prior phase's optional
  collaborator (`wiki=None`, `skill_library=None`, `wiki_reporter=None`,
  `cognition=None`) follows this; this item is no exception, and the live
  gate's differential-inertness leg (below) proves it live, not just by an
  offline default-arg test.
- **Plain `threading.Lock`, not `foundry/_filelock.py`'s cross-process
  flock.** `data/insights.jsonl` is written by one `village.py` process's
  several agent-worker threads — the exact same-process, multi-thread
  scenario `skill_library.py`/`curriculum.py` already handle with a plain
  lock (CPython's GIL plus a lock around the `open("a")`/`write()` is
  sufficient there, and is sufficient here for the identical reason). The
  cross-process case `_filelock.py` guards against (two independently
  launched `python -m anima2...` processes) doesn't arise for this ledger
  today, matching this project's existing convention of reserving
  `_filelock.py` for the kernel package's own ledgers.

### Offline tests (planned)

`tests/test_memory.py` (extended): `ReflectionMemory(persist_path=None)`
(the default) is byte-for-byte identical to today's behavior across a
`record()`/`recent()` sequence — no file created, no lock touched.
`ReflectionMemory(persist_path=tmp_path/"i.jsonl", agent_key="Grimm0")`:
each `record()` call appends exactly one well-formed JSON line with the
expected fields; a hand-corrupted trailing line in a pre-seeded fixture file
is skipped on `load_insights`, not fatal; **the load-bearing case** — two
separate `ReflectionMemory` instances (or one instance destroyed and a fresh
`load_insights` call standing in for a process restart) pointed at the same
`persist_path`/`agent_key` see each other's writes, mirroring
`skill_library.py`'s own "two instances, same ledger" persistence proof
(`test_skill_library.py`). `load_insights` filters strictly by `agent_key` —
a fixture file with two different agents' rows interleaved yields only the
requested agent's insights, in recency order, capped at `capacity`. A
missing `persist_path` file yields an empty, correctly-wired
`ReflectionMemory` (not an error) — the fresh-persona case.

`tests/test_reflection.py`/`test_cognition.py` (extended): `village.py`'s
own wiring (`load_insights` → `insights=` → `ReflectingCognition`) is
covered at the unit level: `ReflectingCognition(inner, reflection,
insights=load_insights(fixture_path, "Grimm0"))`'s first `reconsider()` call
surfaces the pre-seeded insight text via `ctx.insights` **before** any
reflection this session has run — proves the seed reaches the goal-prompt
path, not just that `load_insights` itself parses correctly.

### Live verification gate

`live_persistent_lives.py` (needs ServUO on :2594 + the built bridge, fresh
accounts, area wiped first — `live_common.py` conventions). A scripted
reflection client (mirrors `live_wiki_report.py`'s `_CyclingJudgeClient`)
returns a fixed, recognizable insight string (e.g. `"The east vein pays
better in the morning."`) so what gets persisted is unambiguous and can't be
confused with organically-generated text.

- **Session 1 (fresh persona, e.g. `Grimm-life1`):** `--persist-insights`,
  run long enough for `ReflectingCognition`'s existing cadence
  (`every_n_reconsiders`/`min_new_episodes`, unchanged defaults) to fire at
  least once. After the run, a **fresh Python process** reads
  `data/insights.jsonl` from disk (not this run's own memory) and confirms
  exactly the scripted insight text is recorded under `agent_key ==
  "Grimm-life1"`.
- **Session 2 (same persona, same account — a genuinely new process, not a
  reused one):** constructs `ReflectingCognition` via `load_insights(...,
  "Grimm-life1")` **before the agent ticks even once**, with the scripted
  client now answering a **different** string (so any match can't be
  coincidental repetition). Assert, from the very first tick's `ctx.insights`
  (surfaced identically to `LLMCognition._situation`'s "Lessons learned"
  line), that session 1's insight text is present — this is the non-vacuous
  core: it can only be there if it was loaded from disk, since this session
  has recorded zero reflections of its own yet.
- **Differential — a different persona (`Marina-life1`) started fresh:**
  confirms `load_insights` returns an **empty** `ReflectionMemory` — no
  cross-contamination between personas sharing one ledger file, proving the
  `agent_key` filter is real, not a no-op that happens to return everything.
- **Differential-inertness leg:** an identical session run **without**
  `--persist-insights` (the default) leaves `data/insights.jsonl` completely
  unchanged (same byte count / same `git status`-style diff-nothing check
  this project's live scripts already use for the wiki clone in
  `live_wiki_report.py`) — proves the opt-out path is inert, not merely
  untested.
- **Provenance-aware throughout:** every assertion reads `data/insights.jsonl`
  from a subprocess invocation independent of the live process's own memory,
  the same "fresh channel, never the live process's own memory" discipline
  every gate since Phase 4 item 4 has used.

### As landed (live-verified)

Landed exactly to spec — `memory.py::ReflectionMemory` gains `persist_path`/
`agent_key` (keyword-only, both `None` by default) and `record()` appends the
documented `{ts, agent_key, text, episode_ticks, episode_count}` JSON line
under a new module-level `_insights_log_lock` (mirrors `curriculum.py::
_milestones_log_lock` exactly) only when `persist_path` is set; the new
`load_insights(path, agent_key, capacity=20)` module function does the
"degrade, never crash" read (missing file → empty, corrupt trailing line
skipped, non-matching `agent_key` filtered out) and returns a `ReflectionMemory`
already wired to keep appending to the same file. `village.py` gains
`--persist-insights` and `INSIGHTS_PATH = Path("data") / "insights.jsonl"`;
wiring is scoped to the existing `tiered_clients is not None` branch exactly as
specified — `--persist-insights` alone (without `--llm-tiers`) has no effect,
matching the "only takes effect when reflection is itself wired" rule. No
change to `cognition.py` was needed, as predicted.

**One clarification worth stating, not a divergence:** the spec's live-gate
prose describes session 1 as "`--persist-insights`", but per the item's own
"References"/Scope section `live_persistent_lives.py` is a **standalone
driver mirroring `live_reflect.py`'s wiring shape directly** (its own
`ReflectingCognition(..., insights=...)` construction), not a wrapper that
shells out to `village.py`'s CLI — so "`--persist-insights`" in that
prose means *this item's persistence wiring, exercised the same way
`village.py` exercises it*, not a literal subprocess invocation of
`village.py`. The gate additionally structures all four legs (session1,
session2, differential, inertness) as **independent `--leg` subprocesses**
or a genuinely new process — `live_persistent_lives.py`'s own module
docstring), each with its own retry loop (bounded at 3 attempts, a fresh
account per retry) guarding against this project's known intermittent
Mine/Harvest live freeze (`anima2-harvest-freeze` memory note) — belt-and-
suspenders beyond what the spec strictly required, since only session 2 is
explicitly named as needing process isolation.

**Bug caught before it could produce a false-negative gate result:** the
live-gate script's first draft passed a raw scripted `LLMClient`-shaped
object (`.complete(system, user)`) directly as `ReflectingCognition`'s
`reflection` parameter, which expects a `ReflectionProducer`
(`.reflect(episodes, persona)`). `ReflectingCognition.reconsider()` has no
guard around that call, so `_reflect_bg`'s broad `except Exception: pass`
silently swallowed the resulting `AttributeError` every cadence cycle — a
150-tick session1 run completed cleanly, printing plausible-looking
episode/reward progress, with `reflect_calls` staying `0` throughout and
*zero* insights ever recorded. Fixed by wrapping the scripted client in
`cognition.LLMReflection(...)`, exactly `live_wiki_report.py`'s own pattern
— the fix that was already used everywhere else in this codebase, just
missed in this new script's first draft. This was a bug in the **live-gate
script**, not in `memory.py`/`village.py`'s shipped code.

**Live gate — PASSED, all four legs, first attempt (no retries needed):**

```
[Grimm-life1-762710-a0] preloaded insights from disk BEFORE first tick: 0 (persist=True)
[Grimm-life1-762710-a0] done. episodes=32 reward=0.7 reflect_calls=5 insights_in_memory=5
[FLAG] session1_reflected_in_memory = True

=== independent readback #1: a FRESH `python -c` process reads data/insights.jsonl ===
  agent_key='Grimm-life1-762710-a0': {'count': 5, 'texts': ['The east vein pays better
  in the morning.', ... x5]}

[Grimm-life1-762710-a0] preloaded insights from disk BEFORE first tick: 5 (persist=True)
[Grimm-life1-762710-a0] tick   0: episodes=1 reward=0.0 reflect_calls=0 insights_in_memory=5
[Grimm-life1-762710-a0] first reconsider prompt (tick 0): '...Lessons learned: The east
  vein pays better in the morning. | The east vein pays better in the morning. | The east
  vein pays better in the morning.\n...'
[FLAG] session2_preloaded_from_disk_before_first_tick = True
[FLAG] session2_first_tick_prompt_has_session1_insight = True
[FLAG] session2_first_tick_prompt_lacks_session2_own_insight = True

[Marina-life1-762710] preloaded insights from disk BEFORE first tick: 0 (persist=True)
[FLAG] differential_cross_persona_isolated = True

[Grimm-life2-762710-a0] preloaded insights from disk BEFORE first tick: 0 (persist=False)
[Grimm-life2-762710-a0] done. episodes=28 reward=0.3 reflect_calls=5 insights_in_memory=5
[FLAG] inertness_leg_reflected_in_memory = True   # positive control: it DID reflect...
[FLAG] inertness_leg_disk_unchanged = True         # ...but nothing hit disk

[FLAG] session1_insight_persisted_to_disk_fresh_process_readback = True
[FLAG] differential_marina_never_sees_grimm_insight_fresh_readback = True
[FLAG] PHASE6_ITEM1_PERSISTENT_LIVES PASSED
```

Session 2's own first-tick prompt (captured before it had reflected even
once itself) carried session 1's exact insight text three times over (the
"Lessons learned" line's `ctx.insights[-3:]` window) — the decisive,
non-vacuous proof: `load_insights()` ran in a genuinely separate OS process,
before that process's first tick, and the result reached the goal-prompt
path. The differential leg (`Marina-life1`, a different persona sharing the
same `data/insights.jsonl`) preloaded zero insights. The inertness leg is
the sharpest control: with persistence off, reflection still fired 5 times
in memory (proving the engine genuinely ran, not merely "did nothing"), yet
`data/insights.jsonl` was byte-for-byte identical before and after — the
opt-out path is inert by omission of a write, not by omission of the whole
feature.

**Offline: 540 tests green (530 + 9 new — 7 in `tests/test_memory.py`, 2 in
`tests/test_reflection.py`), 3 consecutive full-suite runs, `ruff check .`
clean.**

### References

`anima2/memory.py`, `anima2/cognition.py` (`ReflectingCognition`'s existing
`insights=` kwarg, `_reflect_bg`), `anima2/village.py`, `anima2/curriculum.py`
(`_load_achieved`/`_record_achieved` — the pattern this item ports to a
second ledger), `anima2/skill_library.py` (the "two instances, one ledger"
persistence proof this item's own offline test mirrors), `anima2/
live_reflect.py`, `anima2/live_wiki_report.py` (`_CyclingJudgeClient` — the
scripted-client-for-determinism precedent), `anima2/live_common.py`.

---

## Item 2 — The village chronicle: an inter-agent relationship ledger ✅

**Mine "who helped whom" from the trade/hunt/market interactions that
already run live, rather than inventing new instrumentation inside the
skills themselves.** `skills/smelt.py::MineSmeltDeliver`,
`skills/craft.py::Blacksmith`, `skills/market.py::BlacksmithMarket`, and
`skills/hunt.py::Hunt` already gate every reward on a **confirmed** pack
delta (PHASE3.md's own "reward on the observed outcome" discipline, verified
directly in each skill's `_payout`) and already expose the exact phase
transitions a relationship event needs — `ctx.memory["smelt_phase"]`
(`"mine"`/`"smelt"`/`"deliver"`/`"return"`), `ctx.memory["mkt_phase"]`
(`"craft"`/`"sell"`/`"sell_return"`/`"bank"`/`"bank_return"`), and
`ctx.memory["hunt_phase"]`/`ctx.memory["hunt_looted"]` — the same keys
`curriculum.py::_mid_transaction` already reads by name (verified: those
three exact key/value pairs, `curriculum.py` lines ~302-308). This item adds
no new skill logic and no contract surface; it's a `village.py`-level
observer over state the economy loop already, provably, computes correctly.

### Scope

- **`anima2/chronicle.py`** (new): `ChronicleEvent(ts, tick, from_persona,
  to_persona, kind, amount, detail)` — `to_persona` is `None` for an
  agent-to-world event (a hunter's confirmed loot, a blacksmith's confirmed
  sale to an NPC vendor or deposit at an NPC banker — real economic
  activity, but not an inter-agent edge since vendors/bankers are staged
  NPCs, not `Persona`-bearing agents). `ChronicleLedger` gains **two**
  operations, deliberately split (see "Key design decisions" below for why
  this isn't `skill_library.py`'s usual single `record_outcome`-appends-
  immediately shape): `queue_event(...)` — `threading.Lock`-guarded, O(1),
  **appends only to an in-memory list**, no file I/O at all — and
  `flush(path=None)` — writes every currently-queued event to
  `data/chronicle.jsonl` as one batch of JSON lines and clears the queue.
  Plus the read side: `events_for(persona, since_tick=0)`, `between
  (persona_a, persona_b)` (both directions), and `recent(n)` for
  prompt-grounding (item 3) — all reading from disk (`_read_ledger`-style,
  corrupt-line-tolerant), matching `skill_library.py`'s own read-side
  convention exactly.
- **`village.py`** gains `--chronicle` (opt-in, unset by default). When set,
  each agent's worker thread (`_run_worker`) detects the four confirmed-
  event triggers below from `ctx.memory`/`ctx.episodes` (already computed by
  the fast loop, already read every iteration for the existing status line)
  and calls `chronicle.queue_event(...)` **only** — the in-memory, O(1),
  no-I/O operation. **`village.py`'s MAIN thread flushes once**, calling
  `chronicle.flush()` right after `for t in threads: t.join()` — the exact
  same "compute in worker threads, persist once from the joined main
  thread" shape `village.py`'s own existing `deliver_threshold` tuner
  outcome recording already uses (verified: `run_village` builds the
  `SkillLibrary`/`ParamTuner` before the roster loop starts, but the actual
  `skill_lib.record_outcome(...)` call happens in the **main thread's**
  post-`join()` loop, once per agent, only after every worker thread has
  already finished — `village.py`'s `for agent, job, chosen in agents:`
  block following the `for t in threads: t.join()` line). Every counterpart
  persona name is supplied **statically from wiring** — `village.py`
  already knows the trade pairing (`smithy_drop`/`vendor_spot`/
  `banker_spot` are only ever set when `has_trade_pair` co-locates one
  specific miner with one specific blacksmith — verified in `run_village`'s
  existing staging block), so no skill needs to learn its counterpart's
  identity; `village.py` already has both `Persona` objects in scope at
  construction time.
  - **`delivered_ingots`** (miner → blacksmith): the tick
    `ctx.memory["smelt_phase"]` transitions **out of** `"deliver"` (i.e. the
    prior tick was `"deliver"`, this tick is `"return"`) **and** the miner's
    most recent `mine_smelt_deliver`-named episode this tick carries
    `reward > 0` (verified: `MineSmeltDeliver.name = "mine_smelt_deliver"`,
    `_payout` already only pays on confirmed pack loss) — `amount` is that
    episode's own `reward` (already the confirmed ingot count, per
    PHASE3.md item 1's "reward only for ingots confirmed gone from the
    pack").
  - **`picked_up_ingots`** (blacksmith ← miner, same edge, reverse
    direction): analogous detection off `Blacksmith`'s (`name = "blacksmith"`)
    fetch-state exit (`ctx.memory["bs_state"]` transitioning out of
    `"fetch"`) with a confirmed reward-bearing episode.
  - **`sold_to_vendor`** / **`banked_gold`** (blacksmith → `None`, world
    events): `ctx.memory["mkt_phase"]` transitioning out of `"sell"`/
    `"bank"` respectively with a reward-bearing `blacksmith_market`-named
    episode.
  - **`looted_corpse`** (hunter → `None`, a world event): growth in
    `len(ctx.memory.get("hunt_looted", ()))` since the last check (mirrors
    `curriculum.py::_memory_list_len_threshold`'s exact "this skill's own
    bookkeeping list grew" signal — the same ground-truth source that
    milestone already uses).
  Every detector is a **pure read** of `ctx.memory`/`ctx.episodes` already
  computed by the fast loop, called from the worker thread — but the
  **write** it triggers (`queue_event`) is a plain in-memory append, never
  file I/O; all file I/O happens once, later, on the main thread's `flush()`
  call, never inside `_run_worker`'s loop.
- **`data/chronicle.jsonl`** — new, gitignored, lazily created.

### Key design decisions

- **Raw event tallies, not a Smallville-style decaying trust/affinity
  score.** A continuous "relationship strength" number would need a
  calibrated formula (recency weight, importance weight, decay rate) this
  project has no ground truth to tune against — the same reason
  `foundry/fitness.py`'s weights are locked kernel constants rather than
  free parameters, applied here: an un-groundable number is worse than an
  honest count. `events_for`/`between`'s callers (item 3) can derive
  "delivered ingots 3 times today" directly from the raw tally, which is
  exactly what a forum post needs and is trivially auditable against the
  ledger.
- **`village.py` detects events, skills stay unchanged.** The alternative —
  teaching `MineSmeltDeliver`/`Blacksmith`/etc. their counterpart's persona
  name directly — would mean threading a new constructor/memory-key concept
  through four skill classes for a fact `village.py` already knows
  structurally from its own roster-pairing logic. Keeping the skills
  untouched also means this item carries zero risk to the live-verified
  Phase 3 trade/hunt/market loops — it's a pure observer, provably unable to
  change what those skills do (no skill imports or is imported by
  `chronicle.py`).
- **Queue in the tick thread, flush once from the main thread — corrected
  from an earlier draft's false precedent, stated plainly.** An earlier
  version of this design claimed `_run_worker` "already prints status lines
  and records skill-tuner outcomes," as if to justify writing directly to
  `data/chronicle.jsonl` from inside the per-agent worker thread. Verified
  false: `_run_worker` (`village.py`) does **zero** disk I/O today — its
  only side effect is an in-memory `status[idx] = line` write under `lock`,
  read back by the main thread's own print loop. The tuner's ledger write
  (`skill_lib.record_outcome(...)`) is real, but it does **not** run inside
  any worker thread either — it runs in the **main thread**, once per
  agent, in the loop immediately after `for t in threads: t.join()`, i.e.
  strictly after every worker thread has already exited. That — not inline
  worker-thread I/O — is the real, verified precedent this item's
  `queue_event()`/`flush()` split follows: compute and detect inside the
  (many, concurrent) worker threads using only fast, in-memory operations;
  persist once, in the (single) main thread, after every worker has
  finished. The accepted tradeoff, named rather than hidden: a mid-run
  crash loses only that session's queued-but-unflushed events (never a
  torn/corrupt line, since nothing is written until the batch flush) — the
  same tradeoff `village.py`'s existing tuner-outcome recording already
  carries (it too only persists at session end), not a new risk this item
  introduces. A genuinely `_reflect_bg`-style background writer thread
  (periodic, off-thread, incremental) was considered and rejected as
  unnecessary complexity here — this ledger has no live-during-the-run
  reader (item 3's forum composer and this item's own live gate both read
  it only after the session ends), so there is nothing a periodic
  background flush would buy that a single end-of-session flush doesn't
  already provide, at the cost of a new thread-lifecycle (spawn/stop) this
  item would otherwise have to test.
- **`to_persona=None` events are kept, not dropped**, despite not being
  "relationships" in the strict agent-to-agent sense — the task's own
  framing ("mined from the real trade/hunt/market interactions") names hunt
  and market alongside trade, and a hunter's confirmed loot / a blacksmith's
  confirmed sale are real, provenance-safe economic life events a forum
  post (item 3) can equally ground itself in. `between(a, b)` — the
  strictly relational query — simply never returns them.
- **No retrieval/importance ranking.** `recent(n)`/`events_for(...,
  since_tick=)` are the only query shapes this item needs (item 3's own
  scope); a fuller memory-stream-style relevance ranking is explicitly
  deferred (see "Notes carried into Phase 7").

### Offline tests (planned)

`tests/test_chronicle.py` (new): `queue_event` followed by `flush` writes
exactly the queued events to `tmp_path`, in order, and **clears the
in-memory queue** (a second `flush()` with nothing newly queued writes
nothing more — not a duplicate of the first flush's lines); `queue_event`
alone, with no `flush()` call, writes **zero** bytes to disk — the
load-bearing proof that the split is real, not cosmetic. `events_for`/
`between`/`recent` round-trip correctly across repeated `flush()` calls and
a fresh instance re-reading the same `tmp_path` ledger (the same "two
instances, same file" persistence proof item 1 and `skill_library.py` both
already establish); a hand-corrupted trailing line is skipped, not fatal;
`between` is order-independent (`between(a, b) == between(b, a)`'s event
set, modulo direction fields) and returns `[]` for a pair with no shared
events, not an error. **Concurrency:** many threads calling `queue_event`
concurrently against one `ChronicleLedger`, then one `flush()` call,
produces exactly that many lines — no lost or torn events under the
`threading.Lock`-guarded in-memory append. `village.py`'s own detector
functions (extracted as small, unit-testable pure functions of
`(prev_memory, ctx.memory, ctx.episodes)` rather than buried in
`_run_worker`'s loop): each of the four triggers fires exactly once per
genuine phase-exit-with-reward transition against a hand-built fixture
sequence, and — the negative control — a sequence with phase churn but
**no** reward-bearing episode at the exit tick (e.g. a wedged/failed
delivery that never confirms) fires **zero** events, proving the detector
isn't gamed by phase-key noise alone.

### Live verification gate

Reuses `live_trade.py`'s exact staged miner+blacksmith trade scenario
(fresh accounts, area wiped), extended with `village.py --chronicle` wiring
(or a small dedicated driver reusing `live_trade.py`'s staging directly, per
implementer's choice at landing time).

- **Multi-cycle, cross-checked against an independently-recomputed count:**
  run long enough for **at least 2** confirmed deliveries (mirrors
  PHASE3.md item 1's own "every ingot traces back to a specific delivery"
  standard). After the run, a **fresh process** reads `data/chronicle.jsonl`
  and confirms: the count of `delivered_ingots` events for the pair equals
  the count of `mine_smelt_deliver`-named, reward>0 episodes recorded
  during a `"deliver"`-phase tick — recomputed **independently** by walking
  the miner's own `agent.episodes` transcript directly (`Episode.summary`/
  `Episode.reward`). This is a **new** evidence leg this item's own gate
  adds, not a reuse of `live_trade.py`'s existing per-tick "SNAPSHOT"
  printout — that printout is built from raw `Observation` fields
  (`_pack_ingots(obs)`, live journal lines), never from `agent.episodes`, so
  it cannot stand in for an episode-based cross-check on its own; this
  item's gate reads `agent.episodes` itself, independently of both the
  chronicle and of `live_trade.py`'s own SNAPSHOT lines. The sum of
  `delivered_ingots.amount` across the chronicle's events must also match
  this same independently-walked episode transcript's own summed reward for
  those ticks — not the chronicle's own say-so, and not `live_trade.py`'s
  printout either.
- **Differential:** a **solo** miner (no blacksmith paired — the existing
  `has_trade_pair`-false roster shape) produces **zero** chronicle events
  for that persona across the same window — proves the ledger doesn't
  fabricate relationships when none are structurally wired, not just that
  it records real ones correctly.
- **Provenance-aware:** cross-process readback throughout, the established
  "fresh channel, never the live process's own memory" discipline.

### As landed (live-verified)

Landed close to spec, with one clarification and one live-caught fix — both
documented here rather than left implicit.

**Landed as specified:** `anima2/chronicle.py`'s `ChronicleEvent`/
`ChronicleLedger` — the `queue_event()`/`flush()` split (in-memory,
`threading.Lock`-guarded queue; `flush()` is the only disk write, guarded by
a module-level `_chronicle_log_lock` mirroring `skill_library.py`'s own
`_ledger_lock`), plus the corrupt-line-tolerant read side (`events_for`/
`between`/`recent`). `village.py` gains `--chronicle`/`--chronicle-path`,
wires each side's static counterpart persona name into the staging block
exactly as specified (`trade_miner_persona`/`trade_smith_persona` computed
once from `online`, attached to the plan entries), and flushes once from
the main thread right after `for t in threads: t.join()`.

**Clarification, not a divergence: `picked_up_ingots`'s amount is a
confirmed pack-ingot delta, not an episode reward.** The spec's prose
describes it "with a confirmed reward-bearing episode," by analogy with
`delivered_ingots`. Verified false by reading `skills/craft.py::
Blacksmith.step()` directly: it has **no reward channel dedicated to the
fetch/pickup at all** — its only reward computation is Blacksmithing
skill-base gain, computed unconditionally every tick and attached to
whichever action that tick happens to return, fetch included, purely
incidentally. Gating on a coincident skill-gain would make `picked_up_ingots`
fire almost never. Implemented instead as a confirmed pack-ingot delta
(`_pack_ingot_count`, Observation-derived) over the fetch trip, baselined at
fetch-entry — the same "only a confirmed, observed outcome pays" discipline,
via the channel that's actually available.

**Bug caught by the live gate's own first attempt, live, before the run even
finished printing:** the first-draft `_delivered_ingots` (and, by the same
reasoning, `_looted_corpse`) checked only the exact phase-exit tick's own
episode reward. Live-caught: `INGOT_GRAPHICS` has 4 distinct graphics
(mirroring `ORE_GRAPHICS`'s own pile fragmentation — `Item.WillStack`
requires an exact graphic match), so a smelted haul is often 2-4 separate
piles, and `MineSmeltDeliver._deliver_step` pays its reward as one increment
**per confirmed pile-drop**, not as a lump sum on the tick `smelt_phase`
finally flips to `"return"` — that tick's own reward is frequently `0.0`
even for a real, fully-confirmed delivery (the earlier piles' rewards
already paid on earlier ticks). The smoking gun was direct: the
blacksmith's own `picked_up_ingots` (pack-delta based, immune to this bug)
fired twice with real, correct amounts (15 then 10 ingots picked up) while
the miner's own `delivered_ingots` stayed completely silent the whole
run — one side of a real relationship chronicled, the other not. Fixed by
accumulating confirmed reward across the **whole** `"deliver"` phase
(`_accumulate_deliver_reward`, extracted as its own independently-testable
function, called every tick by `_run_worker` and reset once the transition
fires), not just the exit tick — and, by the identical reasoning,
`_looted_corpse` was fixed the same way (`_accumulate_hunt_reward`) before
shipping with a latent version of the same bug (a corpse holding gold *and*
a gem pays across several ticks too), even though this item's own live gate
doesn't exercise `Hunt`. `sold_to_vendor`/`banked_gold` were checked and
left as single-episode-reward checks — a vendor sale is one `SellItems`
action covering every dagger at once, and `GOLD_GRAPHIC` never fragments
into multiple piles (`skills/market.py`'s own module docstring) — neither is
exposed to the pile-fragmentation pattern that motivated the other two
fixes. 7 new regression tests pin both fixes, two of which replay the exact
multi-tick accumulation sequence the live bug exhibited.

**Two gate-script bugs caught by an independent second run of this gate —
both in `live_chronicle.py` itself, never in the shipped `chronicle.py`/
`village.py` code, both fixed before the gate could be trusted:**

1. **Retry isolation.** Leg A session 0's first attempt staged, confirmed
   one real delivery (12.0 ingots), then stalled (an unrelated wedge); the
   retry wrapper correctly retried with a fresh account, which then
   completed cleanly (2 more deliveries, 8.0+8.0=16.0). But both attempts
   had been queuing into the **same** `ChronicleLedger` object, so the final
   `flush()` wrote all 5 events (2+3) from **both** attempts into one file,
   while the independent oracle — correctly reset fresh per attempt, since
   it has no business remembering a previous attempt's history — reflected
   only the winning attempt (2 events, 16.0). Readback: `{'count': 3,
   'sum_amount': 28.0, 'total_events_for_miner': 5}` vs. oracle `(2, 16.0)`
   — a real mismatch, but entirely a gate-methodology bug: the ledger itself
   faithfully recorded every real, confirmed delivery from both attempts,
   exactly as `queue_event`/`flush` are designed to. Fixed by giving every
   retry attempt its **own** ledger file (`_attempt_ledger_path`,
   `..._a0.jsonl` -> `..._a0_r0.jsonl`, `..._a0_r1.jsonl`, ...); only the
   winning attempt's own file is read back for the decisive comparison, and
   a stalled attempt's file still exists afterward for forensic review.
2. **Bank-drain resilience.** The same run showed leg B (solo miner) wedge
   on all `MAX_WEDGE_ATTEMPTS` with **zero** episodes each — not a partial
   stall, no progress at all, three fresh accounts in a row — immediately
   after leg A's two sessions had just mined ~45 ingots' worth of ore from
   the exact same spot (`TRADE_MINE_SPOT`). This is resource-bank
   exhaustion (banks respawn over real minutes, far longer than this gate's
   own ~15s retry cooldown), not the pre-8ead6eb intermittent-freeze bug the
   original `STALL_TICKS` comment named (that root cause — resource-bank
   exhaustion + a pack-full edge case `Harvest.step()` never checked for —
   was already fixed by a windowed stuck-rate + `WalkTo`-relocation
   hardening pass; see the `anima2-harvest-freeze` memory note and
   PHASE4.md item 4's own "Resolved" note). Leg A/leg C are structurally
   pinned to `TRADE_MINE_SPOT` (the one live-calibrated spot with a
   co-located, route-calibrated smithy — `profession.py`'s own extensive
   comments on why no other `MINING_SPOTS` entry has one) and can't rotate
   away from it. Leg B needs no blacksmith at all, so it now draws from
   `_SOLO_MINE_SPOT_POOL` — every other `MINING_SPOTS` entry, mirroring
   `foundry/eval.py`'s own `spot_pool=` rotation precedent
   (`live_evolve_gate.py`/CLAUDE.md: "a `spot_pool=` rotation across
   `MINING_SPOTS[0..3]` so back-to-back mining seeds don't share one
   thinning `HarvestBank`") — cycling to a different spot on every retry
   attempt too, so it never competes with leg A/C for the same bank and
   never re-hammers a spot it just drained itself.

**Live gate — PASSED, all legs, multi-cycle, provenance-checked, no retries
needed on the corrected run:**

Reused `live_trade.py`'s exact staged miner+blacksmith scenario as a
**standalone driver** (`live_chronicle.py`) rather than a `village.py
--chronicle` CLI wrapper — mirrors item 1's own `live_persistent_lives.py`
precedent, for the identical reason: `village.py`'s roster login hardcodes
`anima{i}` account names with no override, and this gate needs fresh
accounts per session/leg. The REAL `village._chronicle_events_this_tick`/
`chronicle.ChronicleLedger.queue_event`/`flush` are called directly from the
driver's own round-robin tick loop — never reimplemented. The gate also
builds a wholly **independent** oracle, hand-written in the driver (never
calling `chronicle.py`/`village.py`'s own detector functions), that watches
the same phase transition and correlates it against `agent.episodes` by
hand — the decisive cross-check leg the spec calls for.

- **Leg A — the decisive cross-check, two independent sessions, each its own
  isolated ledger file:**
  - Session 0: 2 confirmed deliveries (14.0 + 8.0 = 22.0 ingots) by tick 121.
    A **fresh subprocess** reading `data/chronicle_gate_773425_a0_r0.jsonl`
    from disk reported `{'count': 2, 'sum_amount': 22.0,
    'total_events_for_miner': 3}` — exactly matching the independent
    episode-transcript oracle (2 deliveries, sum 22.0).
  - Session 1 (fresh account): 2 confirmed deliveries (5.0 + 9.0 = 14.0) by
    tick 177. Fresh-subprocess readback of `..._a1_r0.jsonl`: `{'count': 2,
    'sum_amount': 14.0, 'total_events_for_miner': 3}` — again an exact
    match.
  - The blacksmith's own `picked_up_ingots` (the same edge, reverse
    direction, pack-delta-based) independently confirmed the first
    delivery's amount each time (14.0 and 5.0 respectively) — a third,
    structurally-independent evidence channel beyond the oracle and the
    ledger.
- **Leg B — differential (solo miner, chronicle ON), staged at a rotated
  spot `(2567,493)` never touched by leg A/C:** 26 real episodes recorded
  (genuine mining activity — the positive control, and markedly healthier
  than the previous run's zero, confirming the bank-drain fix) but **zero**
  chronicle events, and `data/chronicle_gate_773425_solo_r0.jsonl` was
  **never created at all** (`flush()` on an empty queue touches no file) —
  proves the ledger doesn't fabricate relationships when none are
  structurally wired.
- **Leg C — inertness (chronicle disabled entirely), pinned to
  `TRADE_MINE_SPOT` like leg A:** 15 real episodes recorded, including 2
  full confirmed delivery cycles (10.0 + 5.0 ingots) — the underlying
  economy loop demonstrably kept working exactly as it does with chronicle
  on — yet `data/chronicle_gate_773425_inertness_r0.jsonl` was **never
  created**. The opt-out path is inert by omission of a write, not by
  omission of the whole engine — the same sharpest-control shape item 1's
  own inertness leg established.
- **Provenance-aware throughout:** every decisive count/sum came from a
  **fresh subprocess** reading the WINNING attempt's own ledger file from
  disk, never the live process's own in-memory `ChronicleLedger` and never a
  file a different attempt might have written to; the cross-check oracle was
  written entirely separately from the shipped detector code it checks.

```
=== [leg A session 1/2]: miner=achA0773425r0 paired=True mine_spot=(2611,474) chronicle=ON ===
  [leg A session 1/2] tick  104: [oracle] CONFIRMED delivery — reward=14.0 (deliveries so far: 1)
  [leg A session 1/2] tick  107: [chronicle] picked_up_ingots amount=14.0
  [leg A session 1/2] tick  121: [oracle] CONFIRMED delivery — reward=8.0 (deliveries so far: 2)
  [leg A session 1/2]: flushed 3 chronicle event(s) to data/chronicle_gate_773425_a0_r0.jsonl
  session 0: cross-process readback (fresh `python -c ...`, reading
    .../data/chronicle_gate_773425_a0_r0.jsonl from disk):
    {'count': 2, 'sum_amount': 22.0, 'total_events_for_miner': 3}

=== [leg A session 2/2]: miner=achA1773425r0 paired=True mine_spot=(2611,474) chronicle=ON ===
  [leg A session 2/2] tick  152: [oracle] CONFIRMED delivery — reward=5.0 (deliveries so far: 1)
  [leg A session 2/2] tick  155: [chronicle] picked_up_ingots amount=5.0
  session 1: flushed 3 chronicle event(s) to data/chronicle_gate_773425_a1_r0.jsonl; oracle deliveries=2 sum=14.0
  session 1: cross-process readback (fresh `python -c ...`, reading
    .../data/chronicle_gate_773425_a1_r0.jsonl from disk):
    {'count': 2, 'sum_amount': 14.0, 'total_events_for_miner': 3}

=== [leg B differential: solo miner]: miner=achSolo773425r0 paired=False mine_spot=(2567,493) chronicle=ON ===
  [leg B differential: solo miner]: flushed 0 chronicle event(s) to data/chronicle_gate_773425_solo_r0.jsonl
  solo miner (26 episodes recorded): chronicle events=0 (expect 0)
  solo ledger path never created: True

=== [leg C inertness: chronicle OFF]: miner=achInA773425r0 paired=True mine_spot=(2611,474) chronicle=OFF ===
  inertness leg: 15 miner episodes recorded (positive control), oracle deliveries=2,
    ledger path data/chronicle_gate_773425_inertness_r0.jsonl exists=False

[FLAG] leg_a_session0_at_least_2_deliveries = True
[FLAG] leg_a_session0_chronicle_count_matches_independent_episode_oracle = True
[FLAG] leg_a_session0_chronicle_sum_matches_independent_episode_oracle = True
[FLAG] leg_a_session1_at_least_2_deliveries = True
[FLAG] leg_a_session1_chronicle_count_matches_independent_episode_oracle = True
[FLAG] leg_a_session1_chronicle_sum_matches_independent_episode_oracle = True
[FLAG] leg_b_solo_miner_had_real_activity = True
[FLAG] leg_b_solo_miner_zero_chronicle_events = True
[FLAG] leg_b_solo_ledger_path_never_created = True
[FLAG] leg_c_inertness_engine_still_ran = True
[FLAG] leg_c_inertness_ledger_path_never_created = True
[FLAG] PHASE6_ITEM2_CHRONICLE PASSED: village chronicle relationship ledger
```

**Offline: 590 tests green (540 + 50 new — 13 in `tests/test_chronicle.py`,
37 in `tests/test_village_chronicle.py`), 3 consecutive full-suite runs,
`ruff check .` clean.**

### References

`anima2/chronicle.py`, `anima2/village.py`, `anima2/live_chronicle.py` (the
live gate driver), `anima2/skills/smelt.py` (`MineSmeltDeliver`,
`smelt_phase`), `anima2/skills/craft.py` (`Blacksmith`, `bs_state`),
`anima2/skills/market.py` (`BlacksmithMarket`, `mkt_phase`),
`anima2/skills/hunt.py` (`Hunt`, `hunt_phase`/`hunt_looted`),
`anima2/curriculum.py` (`_mid_transaction` — the same phase-key reads,
`_memory_list_len_threshold` — the same "bookkeeping list grew" pattern),
`anima2/skill_library.py` (the ledger convention this module mirrors),
`anima2/live_trade.py`, `anima2/live_common.py` (`GM_RELOGIN_COOLDOWN_S`),
DESIGN.md §6 item 4 (Generative Agents), the `anima2-harvest-freeze` memory
note (the known live freeze this item's own gate also hit and retried past).

---

## Item 3 — The forum as continuing chronicle ✅

**Make the village's daily forum post actually a *chronicle* — grounded in
what happened, aware of what happened before — rather than an isolated,
memoryless daily blurb.** `forum.py::post_day`/`compose_post_llm` already
write in-character posts (Phase 2); this item composes items 1 and 2's new
persistence into that existing write path, additively.

### Scope

- **`forum.py::compose_post_llm`/`compose_post`** gain two new, both-optional
  parameters: `yesterday: str | None = None` (a single persisted insight's
  text, item 1) and `chronicle_events: list[ChronicleEvent] | None = None`
  (item 2, this persona's events from the current session). `None`/`None`
  (the default) reproduces today's exact prompt and output — byte-for-byte,
  same as every optional collaborator before it. When present, `yesterday`
  becomes one extra sentence of context in `compose_post_llm`'s `user`
  prompt (e.g. `"Yesterday you noted: {yesterday}"`) and `chronicle_events`
  becomes a short, code-composed factual line (e.g. `"You delivered ingots
  to Tormund3 twice today."`) spliced in **before** the LLM call — the same
  "code computes the grounding fact, the LLM only turns it into prose"
  pattern `cognition.py::LLMWikiReportProducer` already established for
  `ReportDraft.page` (Phase 4 item 1): the LLM is never the source of *which*
  event happened or *who* it was with, only of the sentence describing it.
- **`forum.py::post_day`** gains a `data/forum_log.jsonl` mirror (new,
  gitignored, lazily created, `threading.Lock`-guarded — same shape as this
  phase's other new ledgers): every **attempted** post (whether or not the
  remote `client.post()` call itself succeeds) is recorded locally as
  `{ts, persona, job, title, content, remote_ok}` **before** returning. This
  is deliberate, not incidental: it gives every later verification (this
  item's own live gate, a future audit) a **local**, cross-process-readable
  record of exactly what was composed and whether it actually reached
  uotavern, without depending on an unverified forum-side history-read API
  — `ForumClient` today only ever exposes `post()` (verified: no `list`/
  `recent`/`get` method exists), and this item does not assume or require
  one to exist. The remote `POST` itself is still exercised for real
  (proving the write side live, unchanged from Phase 2); the *chronicle*
  half of the claim (referencing yesterday, naming real interaction
  partners) is verified against this local mirror.
- **`village.py`**'s existing `if forum:` block (`run_village`, unchanged
  trigger — no new flag needed) passes `yesterday=`/`chronicle_events=`
  through to `post_day` whenever item 1's `insights`/item 2's `chronicle`
  collaborators were constructed for that agent this run — `None` for both
  when `--persist-insights`/`--chronicle` weren't passed, exactly
  reproducing today's forum behavior.
- **A one-time, human-supervised live check, bundled into this item's own
  live gate rather than a separate item** (the carried nit: "the first real
  `../uowiki` write deserves the same care as a first live-shard run" —
  PHASE4.md's "Notes carried into Phase 5"): `live_wiki_report.py` (Phase 4
  item 1, unchanged code) run **once**, deliberately, against the **real**
  `../uowiki` clone rather than a disposable one, with every existing safety
  check still enforced (`_assert_no_remote`'s refusal-on-a-real-remote logic
  is what this run must actually pass *through*, not around — this is the
  first time that check is exercised on a target it's meant to allow, not
  refuse), a single forced-claim cycle (not the full 57-call stress run —
  this is a one-time confirmation, not a repeat of Phase 4's own gate), and
  a `git status`/`git log` check before and after confirming exactly one new
  commit landed and `git remote -v`'s push URL was never touched (the
  existing whole-test-file `subprocess.run` argv spy already proves `"push"`
  never appears in code; this is the live-run analog: confirm the real
  repo's remote tracking ref didn't move either). This is **not** part of
  routine live-gate re-runs going forward — a deliberate, explicitly-called-
  out, one-time action, consistent with how a first live-shard run has
  always been treated in this project (`anima2-live-verification` memory
  note).

### Key design decisions

- **Grounding facts are code-composed, never LLM-sourced** — restated
  because it's the load-bearing safety property here too, the same shape
  Phase 4 item 1 established for wiki reports: the LLM turns an
  already-true fact into prose, it never gets to invent *which* fact is
  true. This is what makes the live gate's provenance check meaningful (see
  below) — a hallucinated interaction partner is structurally impossible to
  reach the prompt, because `chronicle_events` is never populated except
  from item 2's own already-confirmed ledger.
- **A local mirror ledger, not a dependency on an unverified remote read
  API.** Explicitly avoids the false economy of "just read history back from
  uotavern" — that API surface hasn't been verified to exist in this
  codebase (`ForumClient` has no read method today), and inventing one on
  spec without confirming the live service supports it would be exactly the
  kind of unverified-claim risk this document's own instructions warn
  against. The local mirror is fully within this repo's control and is
  sufficient for every check this item's gate needs.
- **`None`/`None` defaults keep every currently-passing `--forum` run
  byte-for-byte unchanged** — restated per this phase's standing discipline;
  proven by the live gate's differential-inertness leg, not just an offline
  default-arg test.

### Offline tests (planned)

`tests/test_forum.py` (extended): `compose_post_llm`/`compose_post` with
`yesterday=None, chronicle_events=None` produce byte-for-byte the same
output as today's tests (a regression pin, not a new assertion); with
`yesterday="..."` set, the composed `user` prompt (stubbed `LLMClient`, the
existing test style) contains that exact text; with `chronicle_events=[...]`
set, the grounding sentence names the correct counterpart and is present in
the prompt **before** the LLM call — a stub that ignores the prompt entirely
still gets a post whose fallback (`compose_post`, the no-LLM path) also
threads the grounding line through, so the property holds even off the LLM
path. `post_day`'s `data/forum_log.jsonl` mirror: records one line per
attempt including a failed remote post (`client.post` raising — the existing
`except Exception: return None` path, verified unchanged) with
`remote_ok=False`; two instances/a fresh read see each other's writes (the
same persistence proof pattern as items 1-2).

### Live verification gate

- **Grounded, provenance-checked forum posts (multi-session):** stage the
  same live trade pairing item 2's gate uses. Session 1: `--forum
  --chronicle --persist-insights`, at least one confirmed delivery event.
  After the run, read `data/forum_log.jsonl` (fresh process) and confirm the
  miner's posted content contains the paired blacksmith's exact persona
  name — **and**, as the negative-control half of the same check, that a
  **solo** miner's (no pairing) post from the same run does **not** mention
  any other persona's name anywhere in its content — proving the mention is
  grounded in a real event, not a stock phrase the composer always includes.
- **Cross-session reference:** session 2 (later, same persona, a genuinely
  new process): a scripted `LLMClient` stub forces the day's post prompt to
  be captured (not the reply) — assert the prompt handed to the model
  contains session 1's persisted insight text (verbatim, via item 1's
  `load_insights`), proving the "continuing" half of "continuing chronicle"
  is real at the prompt-construction level — the same "prove the request
  was built correctly" standard Phase 4 item 2 used for prompt-cache-control
  shape, rather than attempting to grade whether the model's prose *used*
  it well.
- **Differential-inertness leg:** an identical run with `--forum` alone
  (item 1/2's flags both unset) produces posts with no `yesterday`/
  `chronicle_events` content and is byte-for-byte identical to a pre-item-3
  baseline `--forum` run's composed output — the opt-in surface changes
  nothing when unused.
- **The real-`../uowiki` one-time check (bundled, separately reported):**
  run and reported as its own pass/fail line in this gate's summary, not
  folded into the pass/fail of the forum checks above — `git log`/`git
  status` on `../uowiki` before and after, confirming exactly one new real
  commit and an unchanged `git remote -v` push target.

### As landed (live-verified)

Landed close to spec, with one deliberately-deferred bullet and three
live-caught fixes — all four documented here rather than left implicit.

**Landed as specified:** `forum.py::compose_post`/`compose_post_llm` gain
`yesterday: str | None = None`/`chronicle_events: list[ChronicleEvent] |
None = None` (keyword-only, both `None` by default — byte-for-byte identical
output to every pre-item-3 caller, pinned by a regression test). A new
`_chronicle_grounding_line()` tallies `chronicle_events` by `(kind,
to_persona)`, filtered to events this persona is the ACTOR in
(`from_persona == persona_name` — "what I did", never "what happened to
me"), and produces exactly the spec's own example shape ("You delivered
ingots to Tormund3 twice today.") — spliced into `compose_post_llm`'s prompt
*and* `compose_post`'s own heuristic body *before* any LLM call, mirroring
`cognition.py::LLMWikiReportProducer`'s "code composes the fact" discipline
exactly; `compose_post_llm`'s fallback threads both new parameters through to
`compose_post`, so the property holds off the LLM path too. `post_day` gains
the `data/forum_log.jsonl` mirror (`_log_forum_post`, module-level
`_forum_log_lock`, same shape as `chronicle.py`'s/`memory.py`'s own
ledgers) — every attempt that gets far enough to compose a post (i.e.
`client.configured`) is recorded before returning, `remote_ok` reflecting
whether the real `client.post()` call itself succeeded, never depending on
an unverified forum-side read API. `village.py`'s existing `if forum:` block
passes `yesterday=`/`chronicle_events=` through with no new flag, sourced
from two small additions to the existing per-agent construction loop: a
`session_chronicle: dict[str, list[ChronicleEvent]]` (keyed by persona name,
pre-populated before any worker thread starts so each thread only ever
appends to a list it already owns) fed by `chronicle.ChronicleLedger.
queue_event()`'s own return value (a small, additive change —
`queue_event()` now returns the `ChronicleEvent` it queued; every existing
caller already ignored the return value, so this is a pure addition, not a
behavior change), and a `yesterday_texts: dict[str, str]` snapshotting each
agent's most recently loaded insight text right after `load_insights()` —
*before* this session's own reflections (if any) can append a newer one to
the same `ReflectionMemory`, so "yesterday" always means what was actually
persisted before this session started, never something this same session
just reflected on.

**Clarification, not a divergence: `live_forum_chronicle.py`'s own
"session2" leg needs no live ServUO connection at all.** The spec's
Scope/live-gate prose describes "session 2 (later, same persona, a
genuinely new process)" by analogy with item 1's own two-live-session
shape. Re-read against the item's own "Cross-session reference" bullet,
though, the actual claim under test is narrower and purely
prompt-construction-level: "assert the prompt handed to the model contains
session 1's persisted insight text... the same 'prove the request was built
correctly' standard Phase 4 item 2 used... rather than attempting to grade
whether the model's prose *used* it well." Proving that needs `memory.
load_insights()` (real, unmodified) and `forum.compose_post_llm()` (real,
unmodified) running in a genuinely separate OS process — nothing about live
UO play. `live_forum_chronicle.py`'s own `session2` leg is therefore a
`--leg session2` subprocess re-exec (mirroring `live_persistent_lives.py`'s
identical pattern) that never opens an `IpcBody`/`GmControl` connection at
all, finishing in well under a second versus the paired legs' multi-minute
live sessions.

**Deliberately NOT run — a real design tension surfaced, not resolved
unilaterally.** The spec's bundled "one-time real `../uowiki` write check"
describes running `live_wiki_report.py` (Phase 4 item 1, unchanged)
"deliberately, against the **real** `../uowiki` clone... with every existing
safety check still enforced (`_assert_no_remote`'s refusal-on-a-real-remote
logic is what this run must actually pass *through*, not around)." Verified
directly: `_assert_no_remote` (`live_wiki_report.py`) unconditionally
`sys.exit()`s on ANY repository with a configured git remote, with no
override flag; `git -C ../uowiki remote -v` confirms the real `../uowiki`
clone genuinely has one (`origin` -> a real GitHub repo). Those two facts
are in direct tension: the check that must be "passed through" refuses
every target it could possibly be pointed at that isn't a disposable clone,
by construction — it cannot be satisfied by running the script verbatim, and
weakening `_assert_no_remote` itself to admit one specific real repository
is exactly the kind of safety-check erosion this project's own live-gate
discipline exists to prevent, not something to do unilaterally inside a
routine item landing. This is left to an explicit human decision rather than
resolved either by skipping the spirit of the check or by quietly loosening
it — `live_forum_chronicle.py` prints a NOTE line to this effect in its own
gate summary rather than reporting a silent pass or fail for this bullet.

**Bug 1 (gate script only): a discarded, stalled attempt still published to
the live forum before the retry wrapper decided to discard it.** First
draft of `live_forum_chronicle.py::_run_forum_session` posted whenever
`miner.episodes.total_recorded > 0`, regardless of whether the session had
just given up via the `STALL_TICKS` guard (a live wedge — bank exhaustion at
the shared, non-rotatable `TRADE_MINE_SPOT`, the exact PHASE6.md item 2
"Bank-drain resilience" pattern, hit again here). The stalled attempt's
incomplete session still reached the real, live uotavern API before
`_run_forum_session_with_retry` threw its result away as "not a real
signal." Harmless to the gate's own VERDICT (the decisive readback always
reads the winning attempt's chronologically latest post), but a real,
live-visible side effect from data the gate itself judged unreliable. Fixed
by gating the post on `not stalled` — mirrors item 2's own "a discarded
attempt's data must never bleed into the winning attempt's own evidence"
lesson, applied to a live side effect instead of a ledger file.

**Bug 2 (gate script only): the inertness leg had no delivery signal to stop
on, so it mined far more than it needed to, at the one shared spot session1
had just mined too.** With chronicle tracking off (`chronicle_ledger is
None`, matching "item 1/2's flags both unset"), the leg's only stopping
conditions were the full `session_ticks` budget or a `STALL_TICKS` bailout —
both of which mean mining for a long time at `TRADE_MINE_SPOT`, immediately
after session1's own draw on the identical, non-rotatable spot (paired
scenarios have no alternate calibrated spot to rotate to — see item 2's own
"As landed" note). A real bank respawns over 10-20 real minutes
(`live_evolve_gate.py`'s own module docstring), far longer than this gate's
15-second inter-leg cooldown; all 3 retries wedged on one live run. Fixed by
stopping the leg as soon as it has a modest, clearly-positive episode
count — the only thing its own "engine still ran" positive control actually
needs, not a full session's worth of ore.

**Bug 3 (gate script only): the gate's first persona-naming scheme made the
"posted content contains the exact counterpart name" check flaky against
genuine LLM prose.** First draft named gate personas
`f"Grimm-fc{suffix}"`/`f"Tormund-fc{suffix}"` (a dash plus a 6-digit
UNIX-time-derived suffix). A real, in-character qwen completion reliably
turns this into "Tormund" alone once every few posts — the exact live
observation: `"Dropped the ingots with Tormund"`, the numeric ID silently
dropped. Verified with a standalone, offline check against the real
Replicate client (no live shard needed): a plain digit-suffixed name in
`village.py`'s own actual shape (`"Grimm0"`/`"Tormund3"`, i.e.
`f"{prof.persona_name}{idx}"`) survives genuine prose far more reliably than
a dash-plus-long-suffix name, both because it reads as a plausible
name-plus-digit and because `compose_post_llm`'s own 2-retry-then-fallback
discipline (`forum.py`, unchanged) means even a dropped attempt or two still
very likely lands on a post that names the counterpart, whether via genuine
prose or the code-composed fallback. Fixed by renaming every gate persona to
a short, `village.py`-shaped tag (`f"Grimm{short}"`/`f"Tormund{short}"`,
`short` = the last 2 digits of the run's own suffix) with a WHOLLY DISTINCT
name root per leg (`"Doran"` for the differential solo miner, `"Boru"`/
`"Aldric"` for the inertness pair) — never a shared prefix between two
personas, so a substring check can never mistake one persona's own signed
name for a mention of a different one. This is a property of genuine,
non-deterministic LLM prose generation, not a `forum.py` defect: the
grounding FACT reaching the prompt is deterministic and code-guaranteed (the
load-bearing safety property this item's Key design decisions section
states); which exact string a real model's free-form prose ends up
containing is not, and the shipped `compose_post_llm`'s own retry+fallback
design is what keeps the aggregate reliable, not a guarantee on any single
raw completion.

**Live gate — PASSED, all four legs, clean run (no retries needed) after
the three fixes above landed:**

```
=== [session1: paired, chronicle+reflection ON]: miner=fcA783787r1 paired=True mine_spot=(2611,474) chronicle=ON reflect=ON ===
GM staged miner at (2611,474,20) + forge
GM staged blacksmith at (2609,474,20) + forge/anvil, 15 starting ingots
  [session1: paired, chronicle+reflection ON] tick   46: [chronicle] delivered_ingots amount=5.0 (deliveries so far: 1)

[session1: paired, chronicle+reflection ON]: reached 1 confirmed delivery/ies and >=1 reflected insight by tick 46 — stopping.
  [session1: paired, chronicle+reflection ON]: reflection insights recorded this session: 4
  [session1: paired, chronicle+reflection ON]: forum post: ok (remote) (attempt logged to data/forum_log_gate_fc783787_paired.jsonl)
  session1: cross-process forum_log readback: {'count': 1, 'last_content': 'Swung the pickaxe hard today in
    the Minoc hills, six turns of solid strikes ringing against the stone. Each blow sang back with fire in
    my arms and dust in my throat—worth every ache when I pulled out those ingots. Dropped them off with
    Tormund87, who grinned like a madman, so I reckon they must be good ones.', 'last_remote_ok': True}

=== [differential: solo miner, chronicle ON]: miner=fcSolo783787r0 paired=False mine_spot=(2567,493) chronicle=ON reflect=OFF ===
GM staged miner at (2567,493,22) + forge
  [differential: solo miner, chronicle ON]: forum post: ok (remote) (attempt logged to data/forum_log_gate_fc783787_solo.jsonl)
  differential solo: cross-process forum_log readback: {'count': 1, 'last_content': 'Swung the pick like an
    old metronome today, twenty-two solid turns biting into the stubborn rock of the Minoc hills. Dust in my
    throat, sweat in my eyes, but each strike rang true—brought home about 42.5. Worth every ache.',
    'last_remote_ok': True}

=== [inertness: paired, chronicle+persist OFF]: miner=fcInA783787r0 paired=True mine_spot=(2611,474) chronicle=OFF reflect=OFF ===
GM staged miner at (2611,474,20) + forge
GM staged blacksmith at (2609,474,20) + forge/anvil, 15 starting ingots

[inertness: paired, chronicle+persist OFF]: no chronicle tracking, but a clear positive episode count by tick 76 — stopping.
  [inertness: paired, chronicle+persist OFF]: forum post: ok (remote) (attempt logged to data/forum_log_gate_fc783787_inertness.jsonl)
  inertness: cross-process forum_log readback: {'count': 1, 'last_content': 'Swung the pickaxe under a
    iron-gray sky, each strike ringing true through the quiet hills. Ten solid turns, and the stone
    yielded—about 6.6, gritty reward for grittier work. Dust in my throat, but my pockets heavier, and
    that's the way of it.', 'last_remote_ok': True}

=== session2: genuinely new process reads data/insights_gate_fc783787.jsonl for 'Grimm87' ===
[session2] loaded insights for 'Grimm87' from data/insights_gate_fc783787.jsonl (a genuinely new process —
  no live connection): 4 total, yesterday='Tormund pays fair for a clean haul.'
[FLAG] session2_loaded_insight_from_disk = True
[FLAG] session2_prompt_contains_session1_insight = True

[FLAG] session1_at_least_one_confirmed_delivery = True
[FLAG] session1_insight_recorded_for_continuity = True
[FLAG] session1_forum_post_attempted = True
[FLAG] session1_forum_log_readback_ok = True
[FLAG] session1_post_mentions_paired_counterpart_name = True
[FLAG] differential_solo_had_real_mining_activity = True
[FLAG] differential_solo_zero_chronicle_events = True
[FLAG] differential_solo_post_attempted = True
[FLAG] differential_solo_post_mentions_no_other_persona = True
[FLAG] inertness_engine_still_ran = True
[FLAG] inertness_post_has_no_grounding_tells = True
[FLAG] session2_loaded_insight_from_disk = True
[FLAG] session2_prompt_contains_session1_insight = True
[FLAG] PHASE6_ITEM3_FORUM_CHRONICLE PASSED: the forum as continuing chronicle
```

Every decisive claim was provenance-checked the established way: `session1`'s
and the differential leg's `forum_log.jsonl` readbacks came from a **fresh
`sys.executable -c` subprocess** reading disk directly, never the live
process's own memory (`_cross_process_forum_readback`, mirroring
`live_chronicle.py::_cross_process_chronicle_readback` exactly); the
grounding claim's negative-control half held in the same run — the solo
miner's post (real qwen prose, its own genuine mining day) named neither
`Grimm87` nor `Tormund87`, proving the paired miner's mention of `Tormund87`
was earned by a real, code-composed, confirmed delivery event, not a stock
phrase the composer always reaches for; the inertness leg's post, composed
with `yesterday=None, chronicle_events=None` exactly like every pre-item-3
`--forum` run, carried none of the item-3-specific tells ("Yesterday", or
any `forum._CHRONICLE_VERB` phrase); and `session2` — a genuinely separate
OS process, zero live connection — loaded session1's own persisted insight
text (`"Tormund pays fair for a clean haul."`, the scripted reflection
marker) from disk and confirmed it reached the prompt handed to a
prompt-capturing stub client, the "continuing" half of "continuing
chronicle" proven at the level Phase 4 item 2's own prompt-shape gate used.

**Offline: 602 tests green (590 + 12 new, all in `tests/test_forum.py`), 3
consecutive full-suite runs, `ruff check .` clean.**

### References

`anima2/forum.py`, `anima2/chronicle.py` (item 2), `anima2/memory.py`
(`load_insights`, item 1), `anima2/village.py`, `anima2/cognition.py`
(`LLMWikiReportProducer` — the code-composes-the-fact pattern this item
reuses), `anima2/live_forum_chronicle.py` (this item's own live gate
driver), `anima2/live_chronicle.py` (`MAX_WEDGE_ATTEMPTS`/`STALL_TICKS`/
`_SOLO_MINE_SPOT_POOL`/`_attempt_ledger_path`, reused directly),
`anima2/live_persistent_lives.py` (the `--leg` subprocess re-exec pattern
this item's own `session2` leg reuses), `anima2/live_wiki_report.py`,
`anima2/wiki.py` (`file_report`, `_assert_no_remote`'s live counterpart in
`live_wiki_report.py`), PHASE4.md item 1 ("Notes carried into Phase 5" —
the first-real-write nit this item's own deferred bullet responds to), the
`anima2-live-verification` memory note.

---

## Item 4 — Richer eval scenarios: a second scenario-supported profession ⏳

**Give `foundry/evolve.py::op_profession` a genuine second candidate.**
`PROFESSION_SCENARIO` (`evolve.py`) has exactly one entry today
(`{"miner": "mining"}`), which `evolve.py`'s own module docstring and
`_active_mutation_operators()` both already document as making
`op_profession` a "structurally-correct no-op" — excluded from every
mutation call specifically *because* there's nothing to swap to. This item
adds one, minimal-risk, second `Scenario`: fishing, chosen because
`Fish` (`skills/harvest.py`) is a `Harvest` subclass covered by the same
Phase 4 hardening (windowed stuck-rate + `WalkTo` relocation) `Mine` already
has, and `FISHING_SPOTS` is an already-calibrated, already-live-verified
pool (Phase 2/`village.py`'s own fisher profession).

### Scope

- **`foundry/eval.py::Scenario`** gains one new, backward-compatible field:
  `nodes: tuple[tuple[int, int, int, int], ...] | None = None` (default
  `None` — the existing `"mining"`/`"mining_50"` entries are untouched, no
  regression). This is a real, necessary addition, not a formality: `Fish`
  (unlike `Mine`, which probes tiles around the stand spot directly) reads
  `ctx.memory["harvest_nodes"]` for its exact water-tile cast target
  (verified: `skills/harvest.py::Harvest._current_node`, and `Fish`'s own
  class docstring) — without it, a staged fisher has nothing to cast at.
- **`foundry/eval.py::run_eval`** seeds `agent.memory["harvest_nodes"] =
  list(scenario.nodes)` right after constructing `agent`, when
  `scenario.nodes` is set — mirroring `village.py`'s own existing `if
  p["nodes"]: agent.memory["harvest_nodes"] = p["nodes"]` wiring exactly,
  now inside the eval harness too. A `None` (every existing scenario) is a
  no-op — the exact line `village.py` already guards the same way.
- **`SCENARIOS["fishing"]`** (new): `spot=` the fisher's stand tile,
  `nodes=` the corresponding water tile as a 4-tuple `(wx, wy, wz, 0)` (0 =
  no graphic, a land-target cast — matches `village.py`'s own fisher wiring
  exactly), both drawn from `profession.py::FISHING_SPOTS[0]`,
  `skills={"Fishing": 35}`, `items=("FishingPole",)`, `skill_names=
  ("Fishing",)`, `work_skill=Fish`.
- **`foundry/evolve.py::PROFESSION_SCENARIO`** gains a second entry:
  `{"miner": "mining", "fisher": "fishing"}`. No other change to
  `evolve.py` is needed — `op_profession`/`_active_mutation_operators`
  already handle an N-candidate `PROFESSION_SCENARIO` generically (verified:
  both are written against `len(PROFESSION_SCENARIO)`, not a hard-coded
  count).

### Key design decisions

- **Fishing, not hunter or blacksmith, is the first addition — deliberately
  the minimal-risk choice.** A hunter scenario would need `Scenario`/
  `run_eval` to also stage live creatures (a new kind of staging step
  `GmControl` doesn't have a helper for yet); a blacksmith scenario would
  need forge/anvil `structures` staging (`profession.py::Profession.
  structures`'s shape, not yet mirrored in `Scenario`) plus a starved-ingot
  supply loop to make a fixed-window eval meaningful at all. Fishing needs
  only the `nodes` field this item already adds — zero further schema
  growth. Both hunter and blacksmith scenarios are explicitly **deferred**
  (see "Notes carried into Phase 7") — two professions is already enough to
  make `op_profession` a real, non-trivial choice, which is all this item's
  own scope requires.
- **`nodes` defaults to `None`, not a required field** — the same
  "old callers/records never break" discipline `EvalResult.descriptor_cell`'s
  own default (`()`) already established for backward-compatible
  `Scenario`/`EvalConfig` growth.
- **This item is deliberately NOT "no-op-by-default" in the way items
  1-3/5-6 are — stated plainly, not glossed over.** `PROFESSION_SCENARIO`
  (`evolve.py`) is a **module-global** dict, not a per-call parameter;
  adding `"fisher": "fishing"` to it widens the shared search space for
  **every** future `evolve()`/`random_search()`/`op_profession`/
  `random_genome()`/`default_seed_genomes()` call in this process — process-
  wide, not scoped to a flagged gate or an opt-in flag the way every other
  item in this phase is. This is the whole point of the item (a second
  candidate is what makes `op_profession` non-trivial at all — a "no-op
  unless you ask for it" version would defeat the purpose), but it means
  the "no-op-by-default" framing DESIGN.md's own Phase 6 roadmap entry uses
  for this phase overall does not apply to this item; DESIGN.md's own
  wording is qualified accordingly (see that document's roadmap bullet).
  **A concrete, verified consequence:** `tests/test_foundry_evolve.py::
  test_op_profession_always_in_scenario_supported_professions` currently
  hard-asserts `set(evolve.PROFESSION_SCENARIO) == {"miner"}` and
  `child.profession == "miner"` as its own precondition — once this item
  lands, that precondition is permanently false (the module-global dict
  genuinely has 2 entries from then on), so this exact, already-passing
  test **will fail as written** unless it is consciously edited as part of
  this item's own offline-test work, not silently left to bit-rot or
  quietly deleted. The right fix, named here rather than left to
  implementation-time guesswork: replace its module-global-dependent
  assertion with one exercised against a **locally monkeypatched**
  single-entry `PROFESSION_SCENARIO` fixture (preserving the original
  test's real intent — "one candidate degrades `op_profession` to a
  same-value swap, and a future second candidate is a deliberate, noticed
  change" — without depending on the real global staying at size 1 forever),
  **and** add the new, separate two-entries-visits-both assertion this
  item's own offline tests need anyway (below) — two tests with two
  different, both-still-true claims, not one test silently overwritten to
  mean something else.
- **Item 6's `--scenario-pool` (below) is the mechanism that lets a later
  gate opt back OUT of this widened space for a specific run** — restated
  here since it's the direct answer to "then how does anything ever
  reproduce the pre-item-4 single-profession baseline again": item 6 adds a
  `profession_pool` restriction that `random_genome`/`default_seed_genomes`/
  the mutation operators all respect, independent of this item's own
  module-global change.

### Offline tests (planned)

`tests/test_foundry_eval.py` (extended): `SCENARIOS["fishing"]` is present
and its `nodes` field round-trips through a fixture `run_eval`-shaped
staging call (a stubbed `GmControl`/`IpcBody`, matching this test file's
existing style — `run_eval` itself needs a live shard, so this only proves
the staging/memory-seeding plumbing, not a live catch). `tests/
test_foundry_evolve.py` (extended): `PROFESSION_SCENARIO` has 2 entries;
**the regression this item exists to produce** — `_active_mutation_operators()`
now **includes** `op_profession` (today's equivalent test asserts it's
*excluded* at 1 entry; this item adds the paired assertion at 2); `op_profession`
run through many seeded calls visits both `"miner"` and `"fisher"`, never a
same-profession no-op swap (mirrors the existing `op_cognition_tier`
never-a-no-op-swap test's exact shape). **`test_op_profession_always_in_
scenario_supported_professions` is consciously edited, not left to fail or
silently deleted** — per "Key design decisions" above, its module-global-
dependent assertion is replaced with an equivalent one against a locally
monkeypatched single-entry `PROFESSION_SCENARIO` fixture, preserving its
original "one candidate degrades to a same-value swap" claim.

### Live verification gate

`live_eval_gate.py` (extended with a `--scenario {mining,fishing}` flag,
default `mining` — preserving Phase 5 item 2's own gate unchanged when
unset), rerun targeting `fishing`:

- **Ordering (differential), the exact proof shape Phase 5 item 2's own leg
  (b) already established and this item deliberately reuses rather than
  inventing a new one:** `"fishing"` (with `FishingPole`) vs. the same
  scenario staged with `item_overrides=()` (no pole — `Fish`'s own "open the
  pack, find nothing" branch, mirroring the mining scenario's own
  no-pickaxe fallback exactly), `run_eval_multi(seeds=3)` per side. Ordering
  must hold (with-pole mean fitness clearly above no-pole's, gap dwarfing
  both sides' own stdev) — cross-process read from `data/eval_results.jsonl`.
- **Descriptor sanity:** the with-pole runs' `descriptor_cell`'s
  `profession_focus` axis reads `"fishing"`/the fishing skill category (per
  `foundry/uoconst.py`'s existing skill-category mapping), not `NONE` or
  `"mining"` — a cheap, independent confirmation that the harness is
  actually scoring what it claims to be staging.

### References

`anima2/foundry/eval.py` (`Scenario`, `SCENARIOS`, `run_eval`),
`anima2/foundry/evolve.py` (`PROFESSION_SCENARIO`), `anima2/profession.py`
(`FISHING_SPOTS`, the `fisher` profession's own `village.py` wiring this
item mirrors), `anima2/skills/harvest.py` (`Fish`, `Harvest._current_node`),
`anima2/live_eval_gate.py`, PHASE5.md item 2 (the no-pickaxe ordering
pairing this item's own live gate reuses), PHASE5.md item 4's "Notes carried
into Phase 6" (the follow-up this item begins closing).

---

## Item 5 — Cognition-aware eval: making `cognition_tier` and `sociability` live ⏳

**Close the other two live-inert genome axes.** `foundry/evolve.py`'s own
module docstring states plainly that `sociability`/`cognition_tier` have
"zero effect" on `run_eval`'s trajectory, because the measured `Agent` is
built with no `cognition=` argument at all (`NullCognition`, `agent.py`'s
own default) — nothing ever reads `Persona.talkativeness` or picks an LLM
tier during the window. This item wires cognition into the eval harness
(making `cognition_tier` real: a genuinely different model gets called) and
adds the one piece of plumbing that's been missing from this codebase since
`Persona.talkativeness` was first defined (Phase 1/2) — **nothing has ever
read it**, verified across `cognition.py`/`agent.py`/`village.py` — so that
`sociability` moving actually changes observable behavior (how often the
agent speaks), not just a genome field with no causal path to anything.

### Scope

- **`cognition.py::LLMCognition`** gains two new, both-optional constructor
  kwargs: `talkativeness_gate: bool = False` and `rng: random.Random | None
  = None` (defaulting to a fresh `random.Random()` when unset — real
  randomness, matching how the rest of this codebase treats non-fast-loop
  randomness). `_queue_say` gains one new guard, **only active when
  `talkativeness_gate=True`**: before doing its existing cleaning/stashing
  work, draw `self.rng.random()` and skip queuing entirely (a silent no-op,
  the agent works quietly that tick) if the draw is `>= persona.
  talkativeness`. `talkativeness_gate=False` (the default) is a byte-for-
  byte no-op — **this is a real behavior change for every currently-live
  `LLMCognition` caller if it defaulted to `True`**, which is exactly why it
  doesn't: `Persona.talkativeness` defaults to `0.3` (`persona.py`), so an
  always-on gate would silently cut every existing persona's speech rate by
  ~70% relative to every prior live-verified chatter proof (Phase 2 through
  4). Opt-in only, preserving every prior gate's result unchanged.
- **`foundry/eval.py::EvalConfig`** gains three new, all-optional fields —
  `cognition_provider: str | None = None`, `cognition_tier: str | None =
  None` (a `llm.py::ROLE_TIER`-style tier key), `sociability: float | None =
  None` — with **`cognition_provider` as the single, real off-switch**,
  stated plainly because it's the load-bearing property this item's whole
  design hinges on: `cognition_provider is None` (the default) means
  `run_eval` builds the agent **exactly as today** —
  `Planner([scenario.work_skill()])`, no `cognition=` argument at all —
  **regardless of what `cognition_tier`/`sociability` happen to hold.**
  This matters concretely because `foundry/archive.py::Genome.
  cognition_tier` is a **required, never-`None`** field (defaults to
  `"cheap"`, and every `default_seed_genomes()`/`random_genome()` draw
  already sets it to a concrete `COGNITION_TIERS` value — verified,
  `evolve.py`) — so a weaker off-switch keyed on `cognition_tier is None`
  would never actually BE `None` for a genome-driven eval, and every
  `evolve()`/`random_search()` call would silently opt into the
  cognition-aware `Planner` shape the moment this item lands, whether or
  not that was ever asked for. Gating on `cognition_provider` instead — a
  field `evaluate_genome`/`default_eval_fn` populate from a **run-level**
  config, never from the genome itself (see below) — is what makes
  "unset by default" actually true for a genome-driven eval, not just for
  a bare hand-built `EvalConfig()`. When `cognition_provider` **is** a
  concrete string, `run_eval` instead builds `tiered =
  build_tiered_clients(provider=cfg.cognition_provider)`,
  `Planner([SpeakPending(), GoTo(), scenario.work_skill(), Greet(),
  Wander()])` (mirroring `profession.py::Profession.planner()`'s own shape,
  not the bare one-skill planner — a cognition-driven agent needs
  `SpeakPending` in its planner to ever voice anything `LLMCognition`
  queues), and `Agent(..., persona=Persona(name=..., talkativeness=cfg.
  sociability if cfg.sociability is not None else 0.3), cognition=
  ThreadedCognition(LLMCognition(tiered.clients[cfg.cognition_tier or
  "cheap"], job=scenario.id, talkativeness_gate=True)))`.
- **`foundry/evolve.py::EvolutionConfig`** gains one new field:
  `cognition_provider: str | None = None` — a **run-level** choice
  (offline/test default vs. a real live-gate run), deliberately **not**
  read off any `Genome` — `cognition_tier` stays the per-genome axis
  `op_cognition_tier` mutates, but *whether cognition runs at all* is this
  separate, orchestration-level switch alone. `foundry/evolve.py::
  default_eval_fn`/`evaluate_genome` thread `genome.cognition_tier`/
  `genome.sociability` into the `EvalConfig` they build
  **unconditionally** (harmless — `run_eval` ignores both fields entirely
  unless `cognition_provider` is set) **and** `cfg.cognition_provider` (the
  `EvolutionConfig` field, never the genome) into `EvalConfig.
  cognition_provider` — closing the exact gap `default_eval_fn`'s own
  docstring names today ("ignores `genome` … nowhere to feed sociability/…
  /cognition_tier into a live scenario") **without** silently changing what
  a bare `evolve()`/`random_search()` call does:
  `EvolutionConfig(cognition_provider=None)` (the default, unless a caller
  — like item 6's own CLI flag — explicitly sets it) means every
  genome-driven eval still builds the pre-item-5 bare-skill `Planner`, byte
  for byte, no matter what `cognition_tier` value each individual genome
  happens to carry. `evaluate_genome`'s existing signature/tests (per-seed
  spot-pool override, median-cell selection) are otherwise unchanged.
- **`village.py`** gains `--talkativeness-gate` (opt-in, unset by default):
  when set alongside `--llm-tiers`/`--chatter`, `LLMCognition` is
  constructed with `talkativeness_gate=True` — makes item 1-3's "living
  village" observably differentiate chatty vs. quiet personas too (a small,
  natural tie back to thread A, not required for thread B's own eval-harness
  goal but cheap given the underlying `cognition.py` change already exists).
- **`live_cognition_eval_gate.py`** (new) — the live gate driver.

### Key design decisions

- **`talkativeness_gate` is opt-in, not the default — the single most
  important decision in this item, stated plainly.** Every prior live
  chatter proof (Phase 2's `live_reflect.py`, Phase 4 item 2's tiering gate)
  assumed every valid model reply gets voiced; silently changing that
  default would be an unannounced regression to already-verified live
  behavior. Making `sociability` real is worth a new opt-in surface; it is
  not worth quietly reducing every persona's speech rate by default.
- **`cognition_provider` defaults to `None`, not `"stub"`/`"replicate"`/
  `"auto"` — a real off-switch, not merely a defanged provider choice.**
  Two properties this default buys, both load-bearing: (a) a bare
  `run_eval()` call never dials out unless explicitly configured to (the
  same "an offline process must never dial out to a provider it isn't
  configured for" property `llm.py::build_tiered_clients`'s own offline
  tests already enforce), and (b) — the property `"stub"` alone could
  never have provided, since `"stub"` is still a concrete provider string
  that would activate the cognition-aware `Planner` shape — a genome-driven
  eval whose `EvolutionConfig.cognition_provider` is left at its own
  default never threads cognition **at all**, regardless of how harmless
  the fully-offline `StubLLMClient` would be to actually call. Every real
  live gate below passes `cognition_provider="replicate"` explicitly (the
  one confirmed-live provider in this environment, per Phase 4 item 2's own
  honest finding, restated here since nothing about that finding has
  changed); offline tests exercise the cognition-aware path with
  `cognition_provider="stub"` (fully offline, no network) precisely to
  prove the wiring without needing a live shard.
  `cognition_provider=None` (every non-cognition-aware caller, including
  item 4's fishing scenario, every existing Phase 5 test, and every
  `evolve()`/`random_search()` call whose `EvolutionConfig` doesn't set it)
  never reaches `build_tiered_clients` at all, and never changes the
  `Agent`'s `Planner`/`cognition=` shape.
- **A deterministic-seed RNG for testability, not a hidden global.**
  `LLMCognition(rng=random.Random(seed))` in tests makes the gate's
  pass/fail boundary exactly reproducible (`talkativeness=0.0` → the seeded
  draw is always `>= 0.0`, never speaks; `talkativeness=1.0` → always speaks
  when the reply is valid) rather than relying on statistical assertions
  over many draws.

### Offline tests (planned)

`tests/test_cognition.py` (extended): `talkativeness_gate=False` (default)
reproduces every existing `LLMCognition`/`_queue_say` test byte-for-byte —
a regression pin, not a new behavior. With `talkativeness_gate=True` and a
seeded `rng`: `talkativeness=0.0` never queues `pending_say` across N calls
with an always-valid stub reply; `talkativeness=1.0` always queues it;
an intermediate value (with a fixed-seed `rng`) queues on the exact,
deterministic subset the seed predicts (asserted against the seed's own
known draw sequence, not re-derived).

`tests/test_foundry_eval.py` (extended): `run_eval` with
`cognition_provider=None` (the field's own default) is byte-for-byte
unchanged from today's behavior **even when `cognition_tier`/`sociability`
are non-`None`** — the load-bearing regression pin this item's whole design
depends on (proves the off-switch really gates on `cognition_provider`,
never on `cognition_tier`), run against the existing fixture-based tests,
proving items 4 and 5 together introduce zero risk to Phase 5's own
already-passing gates. With `cognition_provider="stub"` and
`cognition_tier="cheap"` wired through a fully offline `StubLLMClient`: the
resulting `TrajectorySummary.speech_sent` is nonzero for `sociability=1.0`
and exactly zero (seeded, deterministic) for `sociability=0.0` — fully
offline, no live network, proving the wiring end-to-end short of the live
gate.

`tests/test_foundry_evolve.py` (extended): `evaluate_genome`, given a spy
`eval_fn` (same technique the existing convergence test already uses),
receives an `EvalConfig` whose `cognition_tier`/`sociability` fields exactly
match the input `Genome`'s **regardless of `EvolutionConfig.
cognition_provider`** (proves the genome-field threading is unconditional)
**and** whose `cognition_provider` field exactly matches `EvolutionConfig.
cognition_provider` (proves that run-level field, never the genome, is the
source — a genome with a concrete `cognition_tier` must not leak a
non-`None` `cognition_provider` on its own). `EvolutionConfig()`'s own
default (`cognition_provider=None`) run through `evaluate_genome` against
the spy `eval_fn` produces an `EvalConfig` whose `cognition_provider is
None` — the regression pin proving a bare `evolve()`/`random_search()` call
stays inert by default.

### Live verification gate

Two-legged, honest about the one confirmed-live provider — the exact shape
Phase 4 item 2's own gate used, restated because the underlying constraint
(no `ANTHROPIC_API_KEY` confirmed provisioned) hasn't changed:

- **(a) Runs today, provider-agnostic (Replicate):** `run_eval`/
  `run_eval_multi` on `"mining"` with `cognition_provider="replicate",
  cognition_tier="cheap"` vs. the identical scenario with
  `cognition_provider=None` (today's bare mode, the real off-switch) — the
  cognition-aware run's recorded `TrajectorySummary.speech_sent` is nonzero and its
  `descriptor_cell`'s `sociability_bin` reads above `0` (`"low"`); the bare
  run's stays at exactly `0`/`"low"` — a clean, non-vacuous "`cognition_tier`
  is no longer inert" proof, cross-process read from `data/eval_results.jsonl`.
- **Dose-response, not just presence/absence:** `sociability=0.9` vs.
  `sociability=0.05`, same tick window, same `cognition_tier`, same seed
  pool — the high-sociability run's `speech_sent`/descriptor sociability
  value is **measurably higher**, not merely nonzero — the real differential
  this axis's mutation operator (`op_sociability`) needs to have anything to
  bite on.
- **(b) Anthropic-specific — explicitly deferred, per Phase 4 item 2's own
  precedent:** unattempted; not provisioned in this environment. Noted, not
  silently skipped.

### References

`anima2/cognition.py` (`LLMCognition`), `anima2/foundry/eval.py`
(`EvalConfig`, `run_eval`), `anima2/foundry/evolve.py` (`default_eval_fn`,
`evaluate_genome`, `EvolutionConfig`), `anima2/foundry/archive.py`
(`Genome.cognition_tier` — the required, never-`None` field this item's
off-switch design is built around), `anima2/llm.py` (`ROLE_TIER`,
`build_tiered_clients`), `anima2/persona.py` (`Persona.talkativeness`),
`anima2/village.py`,
`anima2/foundry/descriptor.py` (`compute_descriptor`, `SOCIABILITY_EDGES`),
`anima2/foundry/trajectory.py` (`TrajectorySummary.speech_sent`), PHASE4.md
item 2 (the two-leg live-gate shape this item reuses), `evolve.py`'s own
module docstring ("Which Genome axes actually move live fitness" — the gap
this item closes).

---

## Item 6 — The decisive evolution-vs-random differential, rerun ⏳

**Rerun Phase 5 item 4's comparative gate against the now-richer harness,
and fix the one small housekeeping nit that gate's own write-up flagged.**
With items 4-5 landed, `evolve()`/`random_search()` finally sample
genuinely different phenotypes: `op_profession` swaps between two real
scenarios, `op_cognition_tier`/`op_sociability` move real trajectory
dimensions. This item is the honest test of whether that enrichment is
enough to produce a decisive margin — reported whichever way it lands, per
this project's own "report a tie as a tie" standard (PHASE5.md item 4),
never dressed up.

### Scope

- **`live_evolve_gate.py`** (extended in place, not forked): fixes the
  known nit PHASE5.md item 4 recorded plainly ("`--suffix` disambiguates
  account names between runs but does **not** get plumbed into the
  archive/eval ledger file paths") — `--suffix` now also parameterizes
  `data/archive_evolve_gate{suffix}.jsonl`/`data/archive_random_gate
  {suffix}.jsonl`/a suffix-scoped `results_path` passed to
  `run_eval_multi`, so this item's own rerun doesn't accumulate rows on top
  of Phase 5 item 4's leftover files (the prior run's own note: "not a
  correctness bug in this run... just a housekeeping gap for anyone reading
  the files cold afterward" — this item is exactly that "anyone reading the
  files cold afterward" moment, so the fix lands now rather than being
  deferred again).
- **New flags, both opt-in with defaults reproducing Phase 5 item 4's exact
  original gate shape when omitted:**
  - `--scenario-pool {mining,all}` (default `mining`) sets
    `EvolutionConfig.profession_pool` (a **new** field this item adds — see
    the mechanism below) to `("miner",)` for `mining`, or `None` — the full,
    current, module-global `PROFESSION_SCENARIO`, deliberately not a
    hardcoded `("miner", "fisher")` pair, so this flag never goes stale if
    a third profession lands in a later phase — for `all`.
  - `--cognition-provider {stub,replicate}` (default `stub`) sets
    `EvolutionConfig.cognition_provider` — item 5's own field, already
    added there, reused unchanged here — at `--cognition-provider replicate`
    for the real live gate below.
- **The `profession_pool` mechanism, specified precisely (an earlier draft
  of this item asserted the restriction without naming one — corrected
  here):** `random_genome(archive, rng, professions: Sequence[str] | None =
  None)` and `default_seed_genomes(professions: Sequence[str] | None =
  None)` each gain one optional, defaulted parameter — `professions=None`
  (the default) reproduces today's exact behavior byte for byte (drawing
  from, or hard-coding `"miner"` against, the full module-global
  `PROFESSION_SCENARIO`, unchanged); a concrete sequence restricts the
  draw/seed-profession assignment to it instead. **Every one of the four
  mutation operators** (`op_deliver_threshold`, `op_sociability`,
  `op_cognition_tier`, `op_profession`) gains the same trailing, uniformly-
  shaped `professions: Sequence[str] | None = None` parameter — three
  ignore it, unchanged; `op_profession` swaps within `professions` (when
  given) instead of always the full `PROFESSION_SCENARIO`. This uniform
  shape is what lets `mutate()`'s own call site stay a single, generic
  `op(parent, rng, professions)` rather than special-casing `op_profession`
  — `mutate(parent, rng, archive, professions: Sequence[str] | None =
  None)` gains the matching parameter and forwards it unchanged.
  `_active_mutation_operators(professions: Sequence[str] | None = None)`
  gains the same parameter: its existing `len(PROFESSION_SCENARIO) < 2`
  check becomes `len(professions if professions is not None else
  PROFESSION_SCENARIO) < 2`. **This mutation-side threading closes a real
  gap, not a cosmetic one:** without it, a `--scenario-pool mining` run
  (with item 4 landed, so the module-global `PROFESSION_SCENARIO`
  genuinely has 2 entries) would still let `op_profession` mutate a
  mining-seeded elite into a `"fisher"` genome — which `evaluate_genome`'s
  own `PROFESSION_SCENARIO.get(g.profession, ...)` lookup would then
  actually stage and evaluate **live**, silently contaminating a run meant
  to be a mining-only baseline. With the pool threaded through both the
  seed/random side and the mutation side, `--scenario-pool mining`
  genuinely never produces a non-`"miner"` genome by any path, matching the
  "byte-for-byte the original gate" claim for real, not just for the
  bootstrap/random-draw half of it.
  `make_mutation_step(rng_seed, seed_genomes=None, professions:
  Sequence[str] | None = None)` and `make_random_step(rng_seed,
  professions: Sequence[str] | None = None)` each gain the same parameter,
  forwarding it into their own `default_seed_genomes`/`mutate`/
  `random_genome` calls. **`evolve()`/`random_search()` each gain exactly
  one line** — forwarding the new `EvolutionConfig.profession_pool` field
  into the step-maker call they already make
  (`make_mutation_step(cfg.rng_seed, seed_genomes, professions=cfg.
  profession_pool)` / `make_random_step(cfg.rng_seed, professions=cfg.
  profession_pool)`) — restated here since it corrects an earlier draft's
  overbroad "no change to `evolve()`/`random_search()`" claim: they do
  change, by exactly this one forwarded keyword argument each, defaulted
  so an existing caller that never sets `profession_pool` sees no
  difference. **`_drive`/`Archive` remain completely untouched** — `_drive`
  only ever calls the already-closed-over `step_fn`, never touches
  professions directly, and `Archive`'s promotion/persistence logic has no
  profession-specific branch to begin with. `MAX_CONCURRENT_EVALS` stays
  pinned at `1`, the kill-switch/`max_genomes` guards are unchanged, and
  `Archive.best_by_reliability()` is still the comparative selector — none
  of that machinery is touched by this item at all.

### Key design decisions

- **A rerun exercising new inputs, not a new core mechanism** — `_drive`,
  the reliability-discounted promotion rule, and the kill switch are
  untouched; only the (defaulted, backward-compatible) genome-generation
  surface (`random_genome`/`default_seed_genomes`/the mutation operators/
  the step-makers/`evolve()`/`random_search()`'s own one-line forwards, per
  "Scope" above) grows a `professions` parameter. Every piece of that core
  machinery was already proven correct by Phase 5 item 4's own offline
  convergence test and live infrastructure gate; this item doesn't
  re-litigate any of that — it exercises the **inputs** items 4-5 finally
  make non-degenerate, restricted or not per `--scenario-pool`.
- **The pool restriction had to reach `op_profession`, not just the
  seed/random draw, to mean what it claims.** Restated briefly from
  "Scope": a pool that only restricted `random_genome`/
  `default_seed_genomes` would still let a mutation silently produce (and
  `evaluate_genome` then actually stage and evaluate live) a genome outside
  the claimed pool — a `--scenario-pool mining` run that isn't actually
  mining-only isn't a real baseline-preserving mode, it's a mislabeled one.
  Threading `professions` through `mutate`/`op_profession`/
  `_active_mutation_operators` too is what makes the restriction airtight.
- **Honest about the outcome, not just the process.** The gate's pass/fail
  bar for the **infrastructure** flags (spot fairness, kill-switch proof,
  no-early-halt, per-cell-elite-recompute match) is unconditional, exactly
  like Phase 5 item 4's own gate. The **comparative verdict**
  (evolve-vs-random margin vs. the noise band) is reported as whatever it
  actually is — a genuine margin outside the noise band is this item's own
  stated goal, but "still a tie, now on a richer harness" is an honest,
  publishable outcome too, not a failure requiring more items to be
  invented on the spot. If it ties again, the honest next step (a full
  hunter/blacksmith `Scenario` per item 4's own deferred note, or a larger
  eval budget) is named as a Phase 7 candidate, not silently retried until
  it produces the answer the doc wants.

### Offline tests (planned)

`tests/test_foundry_evolve.py` (extended): `random_genome(archive, rng,
professions=None)` and `default_seed_genomes(professions=None)` are
byte-for-byte identical to today's existing tests (a regression pin);
`professions=("miner",)` restricts every draw/seed to `"miner"` across many
seeded calls — never `"fisher"`, even against a fixture `PROFESSION_
SCENARIO` genuinely holding both entries — and the mirror case,
`professions=("fisher",)`, restricts to `"fisher"` (proves the restriction
isn't one-directional/accidental). **The load-bearing regression test this
item exists to add:** `op_profession(g, rng, professions=("miner",))`,
called many times against a fixture `PROFESSION_SCENARIO` holding both
`"miner"` and `"fisher"`, **never** returns a `"fisher"` genome — the exact
mutation-side leak "Key design decisions" names is what this test is
written to catch (a version of `op_profession` that silently ignores its
`professions` argument fails it). `_active_mutation_operators(professions=
("miner",))` excludes `op_profession` even when the module-global
`PROFESSION_SCENARIO` fixture has 2+ entries — proving the pool, not just
the global dict's size, decides inclusion. `evolve(archive, cfg, ...)` with
`cfg.profession_pool=("miner",)` run end to end (stubbed `eval_fn`, the
existing convergence-test harness): every genome the run produces —
bootstrap seeds, random draws, and mutated children alike — has `profession
== "miner"`, zero exceptions across the run. (`test_op_profession_always_
in_scenario_supported_professions`'s own required edit is item 4's own
offline-test scope, not repeated here — see that item's "Key design
decisions"/"Offline tests.")

`tests/test_live_common.py`/a small extension to whichever test file covers
`live_evolve_gate.py`'s own helper functions (if any exist outside `if
__name__ == "__main__"` — per this file's own convention of keeping live
scripts thin): the `--suffix`-to-path plumbing produces the expected,
distinct file paths for two different `--suffix` values, and an omitted
`--suffix` reproduces the original fixed paths exactly (regression pin
against Phase 5 item 4's own gate having used those fixed names).

### Live verification gate

`python -m anima2.live_evolve_gate --ticks 200 --seeds 2 --genomes 8
--scenario-pool all --cognition-provider replicate --suffix
phase6gate1` (or the implementer's chosen budget — matching Phase 5 item
4's own scale unless a larger budget is warranted by this item's own
findings), fresh accounts throughout, same interleaved E/R spot-fairness
design Phase 5 item 4 established.

- **Infrastructure gate — unconditional pass bar, same five flags Phase 5
  item 4 required:** `spot_fairness_design_ok`, `kill_switch_live_proven`,
  `kernel_guard_offline_proven_live_skipped_per_item2_precedent`,
  `run_completed_without_early_halt`, `per_cell_elites_recompute_matches`.
- **Enrichment sanity, new to this item:** across the run's evaluated
  genomes, both `"miner"` and `"fisher"` professions are actually sampled
  (not just theoretically available) — a `--genomes 8` budget with zero
  fisher draws would indicate a wiring bug, not bad luck, given
  `random_genome`'s uniform draw; and at least one genome's
  `descriptor_cell` shows a `sociability_bin` above `"low"` — proving item
  5's cognition wiring actually fired live during this specific run, not
  just in isolation.
- **Comparative verdict, reported honestly either way:** `evo best` vs.
  `rand best` by `Archive.best_by_reliability()`, margin vs. the
  data-derived noise band (`2 x pooled per-genome per-seed pstdev`, the
  exact formula Phase 5 item 4 used) — **decisive** (margin exceeds the
  band) is the target outcome; **tie** (within the band) is reported as
  such, with the same honesty Phase 5 item 4's own write-up modeled, and is
  not treated as this item having failed to land.
- **Cross-process, independently recomputed**, exactly as Phase 5 item 4:
  the verdict is reproduced from the raw archive rows read fresh from disk,
  not the live process's own in-memory `Archive` objects.

### References

`anima2/live_evolve_gate.py`, `anima2/foundry/evolve.py`,
`anima2/foundry/archive.py` (`Archive.best_by_reliability`),
`anima2/foundry/eval.py`, PHASE5.md item 4 (the exact gate shape/verdict
formula this item reuses, and the `--suffix` nit this item fixes),
PHASE5.md's "Notes carried into Phase 6" (the follow-up this item
completes).

---

## Out of scope this phase (decided and justified, not silently dropped)

- **LLM-authored skill DSL with sandboxing** — still out of scope, carried
  forward unchanged from Phase 4 and Phase 5's own notes. Phase 5's
  config-space evolution (and this phase's richer scenarios for it) is the
  safe, no-synthesis version of "the agent invents new behavior"; a real
  Voyager code-synthesis loop needs an AST-allowlist/fixed-DSL sandbox
  design no phase through Phase 6 attempts. Flagged again as **Phase 7
  material** — nothing in this phase's design argues for pulling it
  forward, and folding it in here would break the phase's own coherence
  (thread A is about persistence/social grounding, thread B is about eval
  richness; code synthesis is neither).
- **A full Smallville-style memory-stream retrieval** (importance/recency/
  relevance-weighted ranking over raw episodic memory, replacing or
  augmenting item 1's simpler "persist the already-distilled Insights"
  approach) — deferred. Item 1 is the cheap, already-half-built version
  (the compression already exists; only the disk mirror was missing); a
  fuller memory-stream architecture is a bigger, riskier lift with no
  existing scaffold and no calibrated importance/relevance formula to port
  from (unlike `foundry/fitness.py`'s locked, v1-cited weights) — a natural
  Phase 7+ escalation once persistent Insights prove valuable in practice.
- **Continuous, decaying relationship-strength scores** (a numeric "trust"/
  "affinity" between agents, Smallville's own fuller relationship model) —
  deferred in favor of item 2's raw event tallies, for the same
  un-groundable-formula reason `foundry/fitness.py`'s weights are locked
  constants rather than free parameters: there's no ground truth to
  calibrate a decay rate against yet, and a simple, auditable count is
  strictly more verifiable for this phase's live gates.
- **A second GM account / `MAX_CONCURRENT_EVALS > 1`** — unchanged from
  Phase 5; this project's persistent shard still has exactly one GM account
  (`hulryung`), and nothing in this phase's scope needs concurrent evals.
- **Running the measured eval agent as a separate OS subprocess** (the
  full v1-grade "channel (b) becomes mechanically independent" upgrade
  PHASE5.md item 1 named as a future path) — still not required; channel
  (a) (the separate `GmControl` connection) remains the load-bearing
  independent signal for every item in this phase, exactly as Phase 5 left
  it.
- **Hunter and blacksmith `Scenario` entries** (item 4's own explicitly
  deferred follow-up) — two scenario-supported professions already unblocks
  `op_profession`/item 6's own goals; a hunter scenario needs new
  live-creature-staging plumbing and a blacksmith scenario needs
  `structures` staging plus a starved-supply loop design, both real,
  separable follow-ups for whichever phase next needs a third profession.
- **Cost-tier budgets derived from curriculum/task difficulty** — still
  deferred, carried unchanged from Phase 4 item 2's and item 5's own notes
  (and PHASE5.md's own restatement of the same open item); nothing in this
  phase's scope depends on it.
- **Cross-village / multi-shard federation, or persisting raw
  `EpisodicMemory` in full** — not attempted; item 1's Insight-only
  persistence is the deliberate compression layer, and multi-shard
  federation isn't implied by anything this phase's carried notes ask for.

---

## Notes carried into Phase 7 / open follow-ups

- **LLM-authored skill DSL with sandboxing** — restated from "Out of
  scope" above since it's the standing, multi-phase-carried item: the
  natural next escalation once config-space evolution (Phase 5) and its
  richer eval harness (this phase) are both proven live, not attempted
  before then.
- **A fuller memory-stream retrieval layer** over item 1's persisted
  Insights (importance/recency/relevance-weighted, Smallville-style) — a
  natural follow-up once persistent lives prove valuable in practice across
  more than a couple of sessions' worth of accumulated insights.
- **Decaying/weighted relationship scores** over item 2's raw event
  tallies — deferred for the same reason, once there's real multi-session
  chronicle data to calibrate any such formula against (there isn't yet,
  at Phase 6's own start).
- **Hunter/blacksmith `Scenario` entries** (item 4's own deferred
  follow-up) — a third and fourth scenario-supported profession, needing
  live-creature-staging and `structures`-staging plumbing respectively that
  don't exist in `foundry/eval.py`/`GmControl` yet.
- **Cost-tier budgets derived from curriculum/task difficulty** — still
  open, carried since Phase 4.
- **The subprocess-isolation upgrade for trajectory channel (b)** — still
  open, carried since Phase 5 item 1; channel (a) remains sufficient for
  every gate this phase's items design.

---

## References

- DESIGN.md §2 (non-negotiables — reaffirmed per item above), §6 (learning
  ladder, the Generative Agents item this phase's thread A closes the
  gap on), §10 (roadmap — this phase's entry points here), §11 (open
  decisions).
- PHASE5.md — "Notes carried into Phase 6" (both threads' own origin:
  society scale-out and the richer-eval-scenarios follow-up), item 4 (the
  gate shape/verdict formula thread B reuses and the `--suffix` nit item 6
  fixes), item 1 ("agents can't lie" independence discipline every item
  above respects).
- PHASE4.md — item 1 (`Wiki.file_report`, `LLMWikiReportProducer`'s
  code-composes-the-fact pattern items 2-3 reuse; the first-real-write nit
  item 3 closes), item 2 (the two-leg live-gate shape item 5 reuses), item
  3 (`SkillLibrary`'s ledger convention items 1-2 mirror), item 5
  (`CurriculumController`'s restart-survives ledger pattern item 1 mirrors
  and `_mid_transaction`'s phase-key reads item 2 reuses).
- `anima2/memory.py`, `anima2/cognition.py`, `anima2/village.py`,
  `anima2/forum.py`, `anima2/curriculum.py`, `anima2/skill_library.py`,
  `anima2/foundry/eval.py`, `anima2/foundry/evolve.py`,
  `anima2/foundry/archive.py`, `anima2/foundry/descriptor.py`,
  `anima2/foundry/trajectory.py`, `anima2/llm.py`, `anima2/persona.py`,
  `anima2/profession.py`, `anima2/skills/smelt.py`, `anima2/skills/craft.py`,
  `anima2/skills/market.py`, `anima2/skills/hunt.py`,
  `anima2/skills/harvest.py` — the existing modules every item above
  extends.
- Papers/ideas (restated from DESIGN.md §12): Voyager (arXiv 2305.16291);
  Generative Agents (Stanford Smallville — memory/reflection/social, the
  direct inspiration for thread A).
