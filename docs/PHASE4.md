# Phase 4 — Work Breakdown

Phase 4 = the learning stack (DESIGN.md §6/§10): the fuller `../uowiki` loop
(reads **and** writes), cognition cost tiering + prompt caching, a
Voyager-style skill-library registry with a persisted outcome ledger, a
discrete-grid bandit that tunes one already-exposed skill constant, and an
automatic curriculum of hand-written, Observation-derived milestones. It
builds directly on Phase 2/3 machinery that already exists and is
live-verified: `wiki.py::Wiki`'s read-only index (PHASE2.md B1),
`cognition.py::ReflectingCognition`'s cadence-gated background-thread
pattern (PHASE2.md B1), `memory.py::EpisodicMemory`/`ReflectionMemory`,
`skills/base.py::Skill`'s `name`/`description` pair (already documented as
"the seed of the Voyager-style skill library"), and
`skills/smelt.py::MineSmeltDeliver.deliver_threshold` — a real, already
live-tested (`live_trade.py --deliver-threshold`) attribute-settable class
constant (no constructor involved — `MineSmeltDeliver` defines no
`__init__`, nor does anything else in its MRO), proof the "bandit slots in
here" seam DESIGN.md A3 names is real, not hypothetical.

**Unlike Phase 2/3, no item in this phase is expected to touch the
Observation/Action contract at all** — every item is pure brain-side
(anima2-only) work: new modules plus optional collaborators wired into
existing classes (`wiki=None`-style constructor args, cadence-gated daemon
threads). None of the 5 items below need the 4-lockstep checklist
(PHASE2.md, bottom) — the same "no contract changes needed" shape Phase 3
item 1 turned out to be, generalized to the whole phase. If live
verification of some item surfaces a genuine need for a new Action or
Observation field, that would be a scope surprise worth flagging explicitly
when it happens (as Phase 3 items 2–4 each did for their own surfaces) —
none is anticipated going in.

Every item is an **optional, no-op-by-default collaborator**: `wiki=None`,
`skill_library=None`, `wiki_reporter=None`, and cadence-gated background
threads that never run unless a caller opts in. 256 tests must stay green
after each item lands, with **zero migration** of any existing caller — the
same discipline `wiki.py`'s own `wiki=None` default and
`ReflectingCognition`'s optional `reflection`/`insights` args already
established in Phase 2. Wherever a scaffold item's only offline proof is
"nothing changed when unset," its live gate below also carries a
**differential-inertness leg** (two live runs, feature off vs on, same
course, same account shape) — a stronger, harder-to-fake version of that
same claim, per the house style this document borrows from PHASE3.md's own
differential proofs (item 4's greedy-vs-`WalkTo` control run).

Every LLM ask this phase introduces is either a yes/no judgment on a claim
the code already knows the ground truth for (item 1's wiki-contradiction
judge), or a pick-one-name-off-a-shown-list (item 5's curriculum picker) —
never free-form JSON with new fields to hallucinate. Both reuse, **by
name**, v1's `../anima/anima/planner/strategy.py::StrategySelector.
_is_strategy_viable` pattern: the LLM proposes, code validates the proposal
against ground truth it already computed, and silently falls back to a safe
default on any mismatch — never a crash, never a bogus commit or milestone
switch. This is qwen3-via-Replicate's proven JSON flakiness (PHASE2.md B1,
`live_reflect.py`) treated as a first-class constraint, the same way every
existing `cognition.py` LLM call already is (`_parse_json`/`_parse_insights`,
tolerant of bare prose, code fences, and outright garbage).

Dependency order (shallow — a partial landing still ships real value):
item 2 (cost tiering) is independent and lands first, so items 1's
wiki-judge call and 5's curriculum-picker call are tiered from birth rather
than retrofitted. Item 3 depends on item 1 (shares an extracted
`_textindex.py`). Item 4 depends on item 3 (reuses its ledger file and
record shape). Item 5 depends on items 1 and 2 (its wiki-contradiction tie-in
reuses item 1's hook unchanged; its picker call needs a tiered client).

**Deliberately out of scope this phase, unchanged from the scout's own top
flag:** LLM-authored, executable code. Every item composes existing,
hand-written `Skill` subclasses with *learned parameters, retrieval, and
picks* — never new code an LLM writes and this process then runs. A genuine
Voyager-style code-synthesis loop needs a sandboxing design this phase does
not attempt; see "Notes carried into Phase 5" at the end of this document.

Status legend: ✅ done · 🚧 in progress · ⏳ todo

**Every item below is ⏳.**

---

## Item 1 — Wiki write loop: `Wiki.file_report()` + filing circuit breaker ⏳

**Close DESIGN.md §6 item 1's write half** (the read half — `wiki.py::Wiki.
search()`/`excerpt()` — has been live-verified since PHASE2.md B1). An
agent's reflection can now propose that reality contradicts a wiki page;
code validates the claim's target page against its own already-computed
search hit (never the LLM's own say-so) before ever writing anything, and a
ported circuit breaker stops a live reflection loop from flooding
`reports/open/` with duplicate filings of the same claim.

### Scope

- **`anima2/wiki.py`** gains `Wiki.file_report(agent, page, claim, observed,
  expected, evidence, *, force=False) -> Path | None` — a direct port of the
  write+slugify+commit logic already proven twice: `../anima/tools/
  wiki_report.py` (118 lines, stdlib-only) and `../uowiki/tools/
  mcp_server.py::file_report` (lines 158–186), which the scout confirmed are
  functionally identical front-ends over one mechanism. Writes the exact
  5-line template (`# <claim>` / `- page:` / `- observed:` /
  `- expected-per-wiki:` / `- evidence:`) to `reports/open/YYYY-MM-DD-
  <agent>-<slug>.md` under the wiki repo root, collision-safe via a `-2`/
  `-3` filename suffix (`unique_path`'s exact algorithm), then `git add` +
  `git commit -m "report(<agent>): <claim>"` via `subprocess.run` —
  **never** `git push` (both v1 sources this ports from already omit it;
  a `subprocess.run` argv spy across the whole test file proves `"push"`
  never appears). Refuses (`None`) if `page` isn't a real page under the
  wiki root unless `force=True` — mirrors `wiki_report.py`'s own `--force`
  flag (v1's CLI: refuses by default, `--force` files against a missing
  page anyway, "to propose a new one"). `mcp_server.py::file_report` has
  **no** such parameter at all — it raises `FileNotFoundError`
  unconditionally whenever `page` can't be resolved via a direct path or
  `_slug_to_path()`, no bypass exists on that side, so `force=True` is new
  plumbing this item adds, not a precondition both v1 sources already
  enforce.
- **A second env var, independent of the read root:** `wiki.py`'s existing
  `ANIMA2_WIKI_ROOT` (default `Wiki`'s `self.root`, the *docs* tree used for
  reads) stays untouched; `file_report`'s git operations root is
  independently overridable via a new `ANIMA2_WIKI_REPO_ROOT` env var
  (falling back to `self.root.parents[2]`, i.e. the wiki repo root implied
  by the existing docs-tree layout, when unset). Cleaner than inferring the
  repo root purely from `self.root`'s relative position — lets a test or a
  live script point reads and writes at two independently-chosen fixture
  trees without one implying the other.
- **`anima2/circuit_breaker.py`** (new) — a near-verbatim port of `../anima/
  anima/planner/circuit_breaker.py` (218 lines, zero anima-internal
  imports, the scout's own "just port this file" verdict): `CircuitBreaker
  (max_failures, cooldown_s)`, `record_failure`/`record_success`/`is_open`/
  `prune_expired`/`snapshot`/`open_targets`, generic over any `Hashable`
  target key. `Wiki` gets a `_report_breaker: CircuitBreaker` keyed on
  `(page, claim_fingerprint)` (a stable hash of the normalized claim text) —
  `file_report` consults `is_open(key)` first; a second call for the same
  `(page, claim)` inside the cooldown (e.g. 24h wall-clock) is a silent
  no-op (`None`, zero commits), `record_success`/`record_failure` bracket
  every attempt.
- **`anima2/_textindex.py`** (new) — `wiki.py`'s `_stem`/`_terms`/
  `_WORD_RE`/`_STOPWORDS`/`_weighted_terms`-style weighted scoring
  (verified present in `wiki.py` today at exactly those names) extracted
  into a tiny shared module with no `wiki.py`-specific imports; `wiki.py`
  imports from it instead of defining its own copy. Reused again by item 3's
  `SkillLibrary.retrieve()` — the only reason this extraction is in scope
  now rather than left as `wiki.py`-private.
- **`anima2/cognition.py`** gains a `WikiReportProducer` Protocol
  (`maybe_file_report(episodes, persona, wiki) -> ReportDraft | None`), a
  `ReportDraft` dataclass (`page`, `claim`, `observed`, `expected`,
  `evidence`), `NullWikiReportProducer` (always `None` — the offline
  default), and `LLMWikiReportProducer`. The LLM producer reuses
  `LLMReflection`'s own already-computed wiki search hit — the same
  `WikiPage` `_wiki_line_for` resolves for the reflection prompt — so the
  LLM is asked only `{"contradiction": true/false, "claim": "...",
  "observed": "...", "expected": "..."}`; `ReportDraft.page` is filled in by
  code from the search hit's own `slug`, **never** taken from the model's
  reply. This is exactly `strategy.py::_is_strategy_viable`'s pattern
  applied to a new call site: the LLM's proposal (yes, contradicted) is
  judged, but the one field that could do real damage if hallucinated (which
  page to write against) is never sourced from the model at all — a
  structurally stronger guarantee than the usual "validate then fall back,"
  because there is no invalid page value the LLM could even produce for
  `file_report` to reject. A malformed reply, `contradiction: false`, or any
  exception is a silent no-op — same discipline as every other JSON ask in
  this file.
- **`ReflectingCognition.__init__(..., wiki_reporter=None)`** — invoked from
  `_reflect_bg` (verified: `cognition.py`'s existing background reflection
  thread, non-overlap-guarded via `_reflecting`/`_reflect_lock`, broad
  `except Exception` around the whole body) right after `self.reflection.
  reflect(...)` succeeds. `wiki_reporter=None` is a byte-for-byte no-op —
  every existing `ReflectingCognition` caller (today: only `live_reflect.py`
  constructs one directly; `village.py` doesn't wire reflection at all yet)
  is unaffected.
- **`live_wiki_report.py`** (new) — mirrors `live_reflect.py`'s wiring
  (`ReflectingCognition(LLMCognition(...), LLMReflection(...), ...)` with a
  real `wiki.Wiki`, the only existing live script that already builds this
  combination), adding `wiki_reporter=LLMWikiReportProducer(...)`.

### Key design decisions

- **The judge never supplies the target page.** Restated from Scope because
  it's the one new safety property no prior `cognition.py` JSON ask has:
  every other structured LLM reply in this codebase (goal JSON, insight
  array) is validated-then-used; this one has an entire class of bad output
  (a hallucinated or nonexistent page) that's structurally impossible to
  reach `file_report`, because the code never reads `page` from the reply.
- **Circuit breaker, not a rate limiter.** A `(page, claim_fingerprint)` key
  (not just `page`) means a genuinely new, different claim about the same
  page still files — only exact repeats are suppressed. Mirrors why v1 built
  a per-target breaker rather than a single global cooldown.
- **All live testing runs against a disposable local clone/worktree of
  `../uowiki`, never the real repo.** `live_wiki_report.py` asserts `git
  remote -v` is empty on its target root before doing anything and refuses
  otherwise — the same "own-shard-adjacent pollution" discipline this
  project already learned the hard way once (see the `anima2-live-
  verification` memory note), applied to a sibling git repo instead of a
  game shard.
- **Differential-inertness leg.** Because `wiki_reporter` defaults to
  `None`, a `ReflectingCognition` built without it must behave byte-for-byte
  like today's (unchanged) reflection loop — proven live, not just by an
  offline optional-arg test (see Live verification gate).

### Offline tests (planned)

`tests/test_wiki.py` (extended): `file_report` against a `tmp_path`
git-initialized fixture with one real page — exact 5-line body,
collision-suffix on a repeat claim, refuses without `force` on a
nonexistent page, exactly one commit whose diff touches only the new file
(`git log`/`git show`), and a `subprocess.run` spy asserting `"push"` never
appears in any invoked argv across the whole test file. Circuit breaker:
the same `(page, claim)` filed twice inside the cooldown produces one commit
not two; a different claim on the same page produces a second commit; an
expired cooldown re-opens filing. `ANIMA2_WIKI_REPO_ROOT` independently
redirects git operations while `ANIMA2_WIKI_ROOT` still governs reads (two
different fixture trees, neither implying the other).

`tests/test_circuit_breaker.py` (new): a close port of whatever unit tests
exist for v1's `circuit_breaker.py` (or fresh ones against the ported
module) — failure-threshold open/half-open/closed transitions,
`prune_expired` bounding memory, `snapshot()`/`open_targets()`.

`tests/test_cognition.py` (extended): `LLMWikiReportProducer` with
`StubLLMClient` — well-formed JSON → `ReportDraft.page` equals the wiki
search hit's slug, proven by rigging the stub to *claim* a different,
nonexistent page name in its JSON reply and asserting that value is ignored
entirely; malformed JSON / `contradiction: false` / no wiki configured →
`None`, zero exceptions raised, and (the no-wiki case specifically) **zero**
`LLMClient.complete` calls — cost discipline, same idiom item 2's tiering
tests use. **Negative control:** a reflection window with no episodes
suggesting anything wrong and a stub client always answering
`contradiction: false` files zero reports across repeated `reconsider()`
calls — not just the single-call unit test, a multi-tick loop, so a
producer that "passes" only because it's never invoked with the failing
input can't sneak through.

### Live verification gate

`live_wiki_report.py` (needs a disposable local clone/worktree of
`../uowiki`, remote-less, verified by the script itself before any
filesystem write): drives one agent through `ReflectingCognition` with a
forced "yes, contradiction" LLM answer for a synthetic claim about a real
page (`skills/mining.md`) across a session, watchdog-bounded by a
`--ticks`/timeout argument like every other `live_*.py` script in this
repo.

- **Multi-cycle, non-vacuous circuit-breaker proof:** cycles 1–3 use the
  identical claim text and must produce **exactly 1 commit** (the breaker
  suppresses cycles 2 and 3, confirmed via `git log --oneline` count on the
  disposable clone); cycle 4 switches to a different claim text and must
  produce a **2nd commit**. A broken or absent breaker would show 3–4
  commits instead of 1-then-1 — this cannot pass by luck.
- **Provenance-aware:** every filed report's page must equal the live wiki
  search hit actually returned for the reflection window in play (read the
  committed file's `- page:` line back and compare against the search index
  independently, not against what the LLM said).
- **Differential-inertness leg:** an identical session run with
  `wiki_reporter=None` (the default) against the same fixture wiki and the
  same forced-LLM setup must produce a tick-for-tick identical goal/speech
  trace to a pre-item-1 baseline run of `live_reflect.py` on the same
  course — and zero filesystem writes under `reports/open/` — proving the
  opt-out path really is inert, not just "untested."

### References

`anima2/wiki.py`, `anima2/cognition.py` (`ReflectingCognition`,
`LLMReflection`, `_wiki_line_for`), `../anima/tools/wiki_report.py`,
`../anima/anima/planner/circuit_breaker.py`, `../anima/anima/planner/
strategy.py::StrategySelector._is_strategy_viable`, `../uowiki/tools/
mcp_server.py::file_report`, `../uowiki/CLAUDE.md` ("Discrepancy reports").

---

## Item 2 — Cognition cost tiering + prompt caching ✅

**Make DESIGN.md §7's "tiered Haiku/Sonnet/Opus, cache aggressively" real**,
without touching the `LLMClient` Protocol or any existing cognition class's
constructor shape — tiering is purely *which concrete client* a role gets
handed at construction time. Lands first (no dependencies) so items 1 and 5's
new LLM call sites are tiered from birth.

### Scope

- **`anima2/llm.py`** gains `HAIKU_MODEL = "claude-haiku-4-5-20251001"` and
  `OPUS_MODEL = "claude-opus-4-8"` alongside the existing `DEFAULT_MODEL =
  "claude-sonnet-4-6"` (all three ids already named in DESIGN.md §7 — this
  item is the first thing that actually uses them; consult the `claude-api`
  skill for current ids/pricing when implementing, since model ids drift).
- **A single auditable `ROLE_TIER: dict[str, str]` table** — one place every
  call site looks itself up in, rather than deciding a tier inline per
  cognition class: `{"chatter": "cheap", "reflection": "standard",
  "wiki_judge": "standard", "curriculum_pick": "standard"}` (`LLMCognition`'s
  frequent in-character chatter is `"chatter"`; `LLMReflection` and item 1's
  `LLMWikiReportProducer` are `"reflection"`/`"wiki_judge"`; item 5's picker
  is `"curriculum_pick"`). Adding a future call site is a one-line addition
  to this table, not a new per-class decision.
- **`build_tiered_clients() -> dict[str, LLMClient]`** returning `{"cheap":
  ..., "standard": ..., "heavy": ...}` plus a `degraded: bool` flag (carried
  on the returned mapping, e.g. as a 4th `"degraded"` key or a small wrapper
  object — exact shape decided at implementation time): tries
  `AnthropicClient` per tier first (needs `ANTHROPIC_API_KEY` + the
  `anthropic` package — confirmed absent from this shell's env today, see
  Live verification gate); on any failure to construct all three, falls
  back to a **single** `ReplicateClient.from_v1_config()` instance reused
  for `"cheap"`/`"standard"`/`"heavy"` alike (the only provider actually
  live-verified in this repo to date has exactly one model, so tiering
  degrades to a documented no-op instead of crashing) and sets
  `degraded=True`.
- **Usage logging** (`data/llm_usage.jsonl`, gitignored, mirrors item 3's
  ledger convention): `build_tiered_clients()` wraps each returned client in
  a thin `_UsageLoggingClient` that records one JSON line per `complete()`
  call — `{ts, role, tier, model, latency_s}` always, plus best-effort
  `prompt_tokens`/`completion_tokens`/`cache_read_input_tokens` when the
  wrapped client exposes them (checked via a `last_usage` attribute
  `AnthropicClient` populates from the SDK response's `usage` object after
  each call; absent on `ReplicateClient`/`StubLLMClient`, so those fields
  are just omitted — no-op-safe, never a crash on a provider that doesn't
  report tokens). Makes the tiering plumbing itself an offline-inspectable
  artifact, not provable only by a live call-counting wrapper.
- **`AnthropicClient.__init__` gains `cache_system: bool = True`.**
  `complete()` sends `system` as `[{"type": "text", "text": system,
  "cache_control": {"type": "ephemeral"}}]` when enabled and the system text
  is long enough to be worth caching (below Anthropic's minimum cacheable
  block size, caching a short persona system prompt is a wasted write, not
  a win — consult the `claude-api` skill for the current threshold), else
  the current plain-string form. Purely internal to `AnthropicClient`;
  `StubLLMClient`/`ReplicateClient` untouched.
- **`village.py`** gets an opt-in `--llm-tiers {anthropic,replicate,stub}`
  flag (mirrors the existing `--forum`/`--chatter` boolean-flag pattern),
  defaulting to today's existing single-`ReplicateClient` behavior when
  omitted — zero change to any currently-passing live script unless the
  flag is passed. When set, `LLMCognition` (chatter) is built off
  `clients[ROLE_TIER["chatter"]]` and `LLMReflection` off
  `clients[ROLE_TIER["reflection"]]`.

### Key design decisions

- **`ROLE_TIER` over ad hoc per-class picking.** Keeps this item's blast
  radius small (one dict to read to know the whole phase's tiering policy)
  and easy to extend as items 1/5 add call sites, without touching
  `build_tiered_clients()` itself.
- **Degradation is explicit and tested, not silent.** `degraded=True` lets
  a caller (or a test) tell "really tiered" from "one client wearing three
  hats" apart — DESIGN §7's ambition is honestly gated on a key nobody has
  confirmed is provisioned in this environment (see Live verification
  gate), not quietly pretended.
- **Deriving cost-tier *budgets* from curriculum/task difficulty** (a cheap
  and elegant follow-on once item 5's milestone catalog is real — harder
  tasks could justify more `"heavy"`-tier calls) is **explicitly deferred**,
  not designed here: this item's `ROLE_TIER` is a fixed, global,
  call-site-keyed policy. Gating tiering's landing on curriculum machinery
  existing first would be the mistake to avoid (a design considered and
  rejected during scoring for exactly this reason) — tiering lands
  standalone; difficulty-derived budgets are a Phase 5+ refinement.

### Offline tests (planned)

`tests/test_llm.py` (new — no dedicated LLM test file exists yet; today's
LLM coverage lives inside `tests/test_cognition.py`/`test_reflection.py`
via `StubLLMClient`): with no `ANTHROPIC_API_KEY` and no v1 `config.yaml`
present, `build_tiered_clients()` returns the degraded single-`Replicate`
form with `degraded=True`, and — the real regression risk this change
introduces — a monkeypatch proves `anthropic.Anthropic(...)`/
`urllib.request.urlopen` are **never called** in that path (an offline
process must never dial out to a provider it isn't configured for). With a
fake env key + a stubbed `anthropic` module, three distinct model ids land
on cheap/standard/heavy and `degraded=False`. `AnthropicClient.complete()`
cache-control shape tested against a stubbed `anthropic.Anthropic` (records
`messages.create` kwargs, no network): `cache_control` present when
`cache_system=True` + long system text, absent otherwise. Usage sink: a
stub client with no `last_usage` attribute still produces a valid JSON line
(latency only, other fields omitted, never a `KeyError`/`AttributeError`).

### Live verification gate

Two-legged, honest about the one confirmed-live provider:

- **(a) Runs today, provider-agnostic:** a short `village.py --llm-tiers
  replicate` session with each tier's client call-counting-wrapped for the
  script only — asserts `cheap_calls > 0` and `standard_calls > 0`, counted
  through *separate* client instances, with a cheap:standard call ratio
  tracking the cadence difference (`cognition_interval` chatter vs
  `every_n_reconsiders=5` reflection) over N ticks. Proves the routing
  plumbing actually dispatches by role, not that Replicate's cost/latency
  differs by tier (it can't — same underlying model). Also confirms
  `data/llm_usage.jsonl` accumulates one line per call with the correct
  `role`/`tier` fields, cross-checked against the call-counting wrapper's
  own tally.
- **(b) Gated on a provisioned key, explicitly not blocking this item's
  landing:** rerun with `--llm-tiers anthropic` and inspect the SDK
  response's `cache_read_input_tokens` (surfaced into `data/llm_usage.jsonl`
  via the usage sink) on a second same-persona agent's second
  `reconsider()` call, to prove caching is live, not just requested —
  flagged as a follow-up since no `ANTHROPIC_API_KEY` is confirmed present
  in this environment today (`env | grep -i anthropic` found none; only the
  Replicate token in `../anima/config.yaml` has been live-verified end to
  end in this repo, per PHASE2.md B1/`live_reflect.py`).

### Done — what landed

- **`anima2/llm.py`** gains `HAIKU_MODEL = "claude-haiku-4-5"`, `OPUS_MODEL =
  "claude-opus-4-8"`; `DEFAULT_MODEL` moved from `claude-sonnet-4-6` to
  `claude-sonnet-5` (confirmed via the `claude-api` skill: current, newer than
  the id this repo had on file — DESIGN.md §7 updated to match, bare aliases
  throughout, no date suffixes, matching this file's existing style). Model ids
  drift; re-consult the skill if these look stale later.
- **`ROLE_TIER: dict[str, str]`** — exactly the table the spec named
  (`chatter`→cheap, `reflection`/`wiki_judge`/`curriculum_pick`→standard).
  `wiki_judge`/`curriculum_pick` have no call site yet (items 1/5 land later)
  but are tiered from birth per this file's own dependency-order note.
- **`build_tiered_clients(*, provider="auto", usage_log=None) -> TieredClients`**
  — `TieredClients` is a small `dict` subclass (`clients[tier]` indexing
  unchanged, `.degraded: bool` as a real attribute — the "wrapper object"
  option from the open shape decision). `provider` is a second implementation-
  time decision beyond what the spec asked for: `"auto"` (default) tries
  `AnthropicClient` for all three tiers, falling back to the degraded
  single-`ReplicateClient` form silently on any construction failure;
  `"anthropic"` makes the same attempt but **propagates** a construction
  failure instead of swallowing it (an explicit ask deserves an explicit
  answer); `"replicate"` forces the degraded form outright regardless of
  `ANTHROPIC_API_KEY` (what the live gate's leg (a) uses, so it proves routing
  without needing a live Anthropic key); `"stub"` is every tier sharing one
  `StubLLMClient` (fully offline). The degraded fallback (`_replicate_tiers()`)
  never returns `None` — even with zero Replicate credentials configured it
  constructs an empty-key `ReplicateClient`, so every caller always gets a
  real `LLMClient` (a failing `.complete()` call is already tolerated
  everywhere: `ThreadedCognition`/`ReflectingCognition` catch and fall back to
  the current goal, `LLMReflection` falls back to `HeuristicReflection`).
- **`_UsageLoggingClient`** (`data/llm_usage.jsonl`, gitignored, `data/`
  created lazily on first write) — one JSON line per `complete()` **attempt**
  (`{ts, role, tier, model, latency_s, ok}`, plus best-effort `prompt_tokens`/
  `completion_tokens`/`cache_read_input_tokens` on a successful call whose
  client exposes `last_usage`). `role` is derived once, at wrap time, as the
  first `ROLE_TIER` entry mapped to that tier — correct for every call site
  wired today; a future caller needing a second, distinct role on an
  already-populated tier (`wiki_judge`/`curriculum_pick`, once items 1/5 land,
  both sharing `standard` with `reflection`) can re-wrap with an explicit
  `role=` — no new API needed, since it's already a plain constructor arg.
  **Logs on `finally`, not only on a clean return** — see "Bug found live"
  below for why that isn't optional.
- **`AnthropicClient.__init__` gains `cache_system: bool = True`** and a
  `last_usage` attribute (the SDK response's `usage` object, `None` until the
  first call). `complete()` sends `system` as a single cache-marked block
  (`[{"type": "text", "text": ..., "cache_control": {"type": "ephemeral"}}]`)
  when `cache_system` is on and the system text clears the model's minimum
  cacheable-prefix size — `_CACHE_MIN_TOKENS` (per-model table from the
  `claude-api` skill's Prompt Caching reference: Haiku 4.5/Opus 4.8 → 4096
  tokens; Sonnet 5 isn't in that table yet, so it's approximated at its
  immediate predecessor Sonnet 4.6's 2048 — documented as an approximation,
  not a confirmed number, in the code comment). Gate uses a cheap chars/4
  token estimate (`_approx_tokens`), not a real token count — never billed
  against, only needs to separate "clearly short" from "clearly long enough."
- **`village.py`** gets `--llm-tiers {anthropic,replicate,stub}` (unset by
  default — zero effect on any existing roster). When set, it supersedes
  `--chatter`: each agent gets `ThreadedCognition(ReflectingCognition(
  LLMCognition(clients[ROLE_TIER["chatter"]], ...), LLMReflection(
  clients[ROLE_TIER["reflection"]])))` — the first time `village.py` wires
  reflection at all (previously chatter-only). A small `_CountingClient`
  (script-local, not persisted — contrast the persisted `_UsageLoggingClient`
  underneath it) tallies calls per tier for the live gate's own
  counter-vs-ledger cross-check, printed at the end of the run.

### Key decisions confirmed or changed from the spec

- **Model ids**: `claude-sonnet-5` (not `claude-sonnet-4-6`) for the
  `"standard"` tier / `DEFAULT_MODEL` — confirmed newer via the `claude-api`
  skill at implementation time, per this item's own instruction to consult it.
- **Prompt-caching minimum for Sonnet 5 is an approximation, not a confirmed
  number** — the skill's cached Prompt Caching table doesn't list Sonnet 5 yet
  (only Opus 4.8/4.7/4.6/4.5/Haiku 4.5 at 4096; Fable 5/Sonnet 4.6/Haiku 3.5/3
  at 2048; Sonnet 4.5/4.1/4/3.7 at 1024). Treated as no more permissive than
  its immediate predecessor Sonnet 4.6 (2048) — flagged in code and here for a
  future pass to confirm once the skill's table is updated.
- **A `provider` argument on `build_tiered_clients()`** beyond the bare
  auto-fallback the spec described — needed so `village.py --llm-tiers
  replicate` (leg (a) below) can force the degraded form deterministically
  rather than depending on whether `ANTHROPIC_API_KEY` happens to be set in
  the shell that runs it.
- **`ok: bool` added to the usage-log schema**, beyond the spec's literal
  `{ts, role, tier, model, latency_s}` — see "Bug found live" below; this
  wasn't a design preference, a live run surfaced why it's needed.

### Bug found live: usage log silently dropped failed calls

The first version of `_UsageLoggingClient.complete()` called `self._log(...)`
only after a clean return. Leg (a)'s first live run (`village.py --llm-tiers
replicate --ticks 600`, one miner) showed a real gap: the script's own
call-counting wrapper tallied **41 cheap / 7 standard** calls, but
`data/llm_usage.jsonl` held only **24 cheap / 2 standard** lines. Root cause:
some Replicate calls raised (an HTTP failure, ~0.3s latency — fast, not the
90s `urlopen` timeout, and confirmed unrelated to the credentials themselves,
which a standalone `ReplicateClient.complete()` call succeeded with
immediately after) and the un-logged exception propagated straight through
`_UsageLoggingClient` to `ThreadedCognition`/`ReflectingCognition`'s own
`except Exception` — silently swallowed there by design, but silently
un-logged here by omission. Fixed by moving the log call into a `finally`
(logging every *attempt*, `ok: True`/`False`), with `last_usage` only ever
read on `ok: True` — otherwise a failed call would report the *previous*
successful call's stale token counts (`last_usage` isn't cleared on failure).
Two regression tests added (`test_usage_logging_client_logs_a_failed_call_and_
reraises`, `test_usage_logging_client_does_not_misattribute_stale_usage_on_
failure`); the exact scenario the live gate's counter-vs-ledger cross-check
exists to catch, catching a real bug on its very first live run.

### Offline tests

`tests/test_llm.py` (new, 18 tests): `build_tiered_clients()` degrades to a
real (never `None`) single-`ReplicateClient` form with nothing configured at
all (no `ANTHROPIC_API_KEY`, no `anthropic` package — `sys.modules["anthropic"]
= None` forces the same `ImportError` this shell's actual missing package
produces — no v1 `config.yaml`, no `REPLICATE_API_TOKEN`), with
`urllib.request.urlopen` monkeypatched to raise if called at all — proving
no dial-out in that path; a fake key + a stubbed `anthropic` module lands
three distinct model ids on cheap/standard/heavy with `degraded=False`;
`provider="replicate"` bypasses Anthropic even when it would otherwise
succeed; `provider="anthropic"` propagates a construction failure instead of
degrading; `provider="stub"` is fully offline. `AnthropicClient.complete()`'s
cache-control shape tested against a stubbed `anthropic.Anthropic` (records
`messages.create` kwargs): present for a long system prompt, absent for a
short one, absent when `cache_system=False`. Usage sink: a client with no
`last_usage` (`StubLLMClient`) still logs a valid line (latency/role/tier
only); an `AnthropicClient`'s token counts land correctly; a failing client
still logs (`ok: False`, re-raises) and never misattributes a prior
successful call's usage to the failed one. 274 tests green (up from 256),
`ruff check .` clean.

### Live verification gate

**(a) — run, provider-agnostic (Replicate).** `python -m anima2.village
--miners 1 --lumberjacks 0 --fishers 0 --blacksmiths 0 --townsfolk 0
--hunters 0 --ticks 600 --llm-tiers replicate` against the live ServUO shard
(one miner, `cognition_interval=12` chatter cadence, `every_n_reconsiders=5`
reflection cadence — both defaults, untouched). Two full runs (the second
after the usage-log fix above); the second run's numbers, cross-checked three
ways:

```
llm-tiers (replicate): degraded — one client answers every tier
...
day's work done.

— llm tiers — (degraded=True) —
  cheap: 40 calls
  standard: 5 calls
  heavy: 0 calls
```

- **Role routing, not vacuous:** `cheap:standard` = 40:5 = 8:1 (run 1: 41:7 ≈
  5.9:1) — both track the cadence difference (chatter every reconsider,
  reflection every 5th), and `heavy` stayed at exactly 0 both runs (no call
  site routes there yet — correct, not a bug). A broken router would show a
  flat or reversed ratio; this cannot pass by luck.
- **Ledger cross-check, exact match:** `data/llm_usage.jsonl` line counts by
  tier after the fix — `{"cheap": 40, "standard": 5}` — identical to the
  script's own in-process tally above, split `{"cheap": 24 ok / 16 failed,
  "standard": 2 ok / 3 failed}` (qwen3-via-Replicate flakiness, PHASE2.md B1's
  own documented characteristic — concurrent chatter+reflection calls sharing
  one Replicate account plausibly explains the failure rate; a standalone call
  with the same credentials succeeded immediately). Every logged line carries
  the correct `role`/`tier` (`{"cheap": "chatter"}`, `{"standard":
  "reflection"}`) — read back from the file, not asserted from memory.
- **Provenance-real, not staged text:** real in-character chatter reached the
  transcript from actual qwen3 completions, e.g. *"I reckon the ore's runnin'
  hot near the lower shaft—third strike's the charm"* — not a canned/stub
  string.

**(b) — Anthropic, `cache_read_input_tokens` on a second `reconsider()` —
explicitly deferred, per this item's own instruction.** `ANTHROPIC_API_KEY`
is not provisioned in this environment (confirmed again at this item's
landing: the `anthropic` package itself is absent from `.venv`, matching this
file's original note). Not attempted. The offline cache-control-shape tests
above are the closest available proof that the *request* is built correctly;
whether Anthropic's servers actually serve it from cache is unverified and
flagged as a follow-up, unchanged from the original scope.

### References

`anima2/llm.py`, `anima2/cognition.py` (`LLMCognition`, `LLMReflection`),
`anima2/village.py`, `tests/test_llm.py`, DESIGN.md §7, `claude-api` skill.

---

## Item 3 — Skill library v0: registry, keyword retrieval, persisted outcome ledger ⏳

**A registry-plus-ledger, not a code-generation system.** Wraps the
existing hand-written `Skill` subclasses — no new executable code is ever
authored by an LLM in this phase — and adds two things composability alone
doesn't give the codebase yet: natural-language retrieval over
`name`+`description` (reusing item 1's extracted `_textindex.py`, not a
reimplementation), and a persisted, cross-restart, cross-process-readable
outcome ledger — the first thing in anima2 to survive a process restart.

### Scope

- **`anima2/skill_library.py`** (new): `SkillEntry(name, description,
  skill_cls, tags=())`; `SkillLibrary` — a static registry covering every
  skill currently exported from `anima2/skills/__init__.py` (verified
  today: `Blacksmith`, `BlacksmithMarket`, `Chop`, `Combat`, `Fish`, `GoTo`,
  `Greet`, `Harvest`, `Hunt`, `Mine`, `MineAndSmelt`, `MineSmeltDeliver`,
  `SpeakPending`, `Wander`), built without importing `cognition.py`/
  `planner.py` — mirrors v1 `../anima/anima/planner/modes.py`'s
  deliberately dependency-free style (a `Mode` dataclass + a `MODES` dict,
  zero `anima/` imports, "loads anywhere").
- **`SkillLibrary.retrieve(query, k=3)`** reuses `_textindex.py`'s weighted
  scoring over `name + description` — the same title/description-dominant
  weighting `wiki.py` already validated against the real ~2.4k-page corpus
  (PHASE2.md B1), no embeddings.
- **`SkillLibrary.record_outcome(skill_name, profession, reward, status, *,
  param=None, param_value=None)`** appends one JSON line to
  `data/skill_ledger.jsonl` (`ts, skill_name, profession, reward, status,
  param, param_value` — the last two `None` unless a caller like item 4's
  tuner is recording a tuned run). A corrupted or partial trailing line is
  skipped, never raised — matches `wiki.py`'s "degrade, never crash"
  discipline for a broken frontmatter block. `SkillLibrary.stats(skill_name,
  profession)` returns `count`/`mean_reward`/`success_rate`, lazily built
  from the ledger on first read (mirrors `Wiki`'s lazy-index pattern), then
  kept warm in memory. Multiple agents/processes sharing one ledger path
  append-only (no read-modify-write races within a single process; see
  "Notes carried into Phase 5" for the untested multi-process-write case).
- **`Agent.__init__` gains an optional `skill_library=None`** (verified
  today: `Agent.__init__` takes `body, persona, planner, reflexes=None,
  cognition=None, *, goal=None, cognition_interval=20, episodes_window=20`
  — no `skill_library` param exists yet). `Agent.tick()` gets one guarded
  call right after `result = skill.step(ctx)` (verified today: `agent.py`
  line 99), using the **exact same filter** that already gates episodic
  recording (`if result.reward or result.status is not Status.RUNNING:`,
  verified at `agent.py` line 103) — zero behavior change when `None`.
- **`Skill.diagnose(ctx) -> str | None`** added to the ABC (default `None`
  when `can_run` is `True` — verified: `Skill` today has only `can_run`/
  `step`, no `diagnose`). One-line overrides on `Blacksmith`/`Hunt`/
  `MineSmeltDeliver` (e.g. "starved of ingots, no pile in range") — feeds
  item 5's eligibility reasoning without an LLM guessing why a skill can't
  run. Mines the *idea* from v1's `../anima/anima/skills/base.py`
  `can_execute`/`diagnose` precondition pattern (the scout's own citation),
  not its async plumbing.

### Key design decisions

- **Registry, not generation — the scout's top-flagged risk is sidestepped,
  not solved.** Every entry wraps an existing hand-written class; nothing
  here authors new code. A safe-by-construction JSON-recipe/composition DSL
  (fixed interpreter, never `eval`/`exec`, a whitelist of already-existing
  primitives) is the natural next-phase graft onto this ledger once it's
  proven live — deliberately not attempted in this item (see "Notes carried
  into Phase 5").
- **`_textindex.py` reuse, not reimplementation.** Item 3 depends on item 1
  specifically for this extraction — `SkillLibrary.retrieve()` and
  `Wiki.search()` share one scoring implementation.
- **Measurement-independence caveat, stated plainly (not glossed over):**
  the ledger's `reward` field is the agent's own computed `SkillResult.
  reward` — the same value already recorded into `EpisodicMemory` today —
  **not** an independently GM-verified channel. This is weaker than
  DESIGN.md A6's "agents can't lie" standard (which describes v1 Foundry's
  wire-level, packet-parsed fitness, a different — and heavier — mechanism
  anima2 has no equivalent of yet). Cheap, optional corroboration: the live
  gate below has the GM connection independently read back a plain gold/
  skill-value signal (via `GmControl.command_on` and the resulting
  self-directed journal line, the same primitive `stage()` already issues
  `[Set` commands through) and cross-checks its order of magnitude against
  the ledger's own summed reward — advisory, not a hard pass/fail gate, but
  worth doing because it's nearly free given machinery that already exists.
- **`data/` is new** — a local, gitignored directory for runtime-generated
  state (`skill_ledger.jsonl` here; `llm_usage.jsonl` from item 2 and
  `milestones.jsonl` from item 5 land alongside it), not source-controlled.

### Offline tests (planned)

`tests/test_skill_library.py` (new): the registry covers every currently-
exported skill (a test that fails loudly if `skills/__init__.py`'s
`__all__` ever adds an entry this registry doesn't know about);
`retrieve("mine ore")` deterministically ranks `Mine`/`MineAndSmelt`/
`MineSmeltDeliver` above `Fish`/`Chop` (same style as `test_wiki.py`'s
ranking assertions); `record_outcome` + `stats` round-trip correctly across
repeated calls; **two separate `SkillLibrary` instances pointed at the same
`tmp_path` ledger** — writes from instance A are visible reading fresh from
instance B (proves persistence isn't a no-op, the load-bearing claim of
this whole item); a hand-corrupted trailing line in the ledger file is
skipped, not fatal. **Negative control:** an idle/no-op skill run (a
fixture `Status.RUNNING` result with `reward=0.0`, the exact case the
episodic-recording filter already excludes) produces **zero** ledger lines
across many ticks — not just "the happy path writes correctly," proof the
hook doesn't over-record. `diagnose()` returns `None` exactly when
`can_run` is `True`, a non-empty reason on the overridden skills' known-
blocked fixtures (a starved `Blacksmith`, a `Hunt` with an empty queue).

`tests/test_agent_loop.py` (extended): `Agent(skill_library=None)` behaves
byte-for-byte identically to today's `Agent` across a fixed `MockBody`
scenario (the offline half of the differential-inertness claim).

### Live verification gate

Reuses `live_hunt.py`'s existing scenario (gold-only loot, provenance-safe
starting-gold deletion — already the strongest "confirmed" reward signal
in the repo), extended with an opt-in `--skill-library` flag (mirrors the
`--mongbats`/`--min-cycles` convention already in that script).

- **Cross-process readback (multi-cycle):** after the run, a **second,
  freshly started Python process** reads `data/skill_ledger.jsonl` from
  disk — not the live process's memory — and its
  `stats("hunt", "hunter").count` must match the transcript's printed
  loot-cycle count, with `mean_reward`'s order of magnitude matching the
  transcript's own episodic reward total. A broken wiring (the hook never
  actually called) would leave the ledger empty or stale, failing this
  check outright — it cannot pass vacuously.
- **Differential parity proof (retrieval vs hand-wiring):** stage two
  hunters in the same session — hunter A's planner is built the existing
  way (`Hunt()` constructed directly, as `profession.py` does today);
  hunter B's planner is built by calling `SkillLibrary.retrieve("hunt
  weak creatures")` and instantiating whatever class comes back, with an
  explicit `isinstance(retrieved_skill, Hunt)` assertion in the live
  script itself — and the class name `Hunt` never appears anywhere else in
  hunter B's own construction path. Both hunters must complete an
  equivalent number of loot cycles across the same window (not identical —
  live combat has natural variance — but the same order of magnitude, and
  both provenance-safe). Proves retrieval-then-instantiate is behaviorally
  identical to hand-wiring, not just that persistence works.
- **Advisory corroboration:** the GM connection independently reads back a
  gold figure via `[Get Gold` targeting each hunter (a new, small
  `GmControl` helper) and logs whether it's the same order of magnitude as
  the ledger's summed reward for that hunter — see the measurement-
  independence caveat above; not a hard gate.

### References

`anima2/skill_library.py`, `anima2/skills/__init__.py`, `anima2/agent.py`,
`anima2/skills/base.py`, `anima2/live_hunt.py`, `../anima/anima/planner/
modes.py`, `../anima/anima/skills/base.py` (`can_execute`/`diagnose`
pattern).

---

## Item 4 — Skill parameter tuning: discrete-grid bandit over an existing skill constant ⏳

**Genuinely close DESIGN.md A3's "bandit/Q-learning later" seam** — scoped
down to the one place today's one-work-skill-per-profession architecture
actually presents a real tunable, rather than inventing a speculative
multi-skill selection problem to justify more machinery.

### Scope

- **`anima2/skill_tuning.py`** (new): `ParamSpec(name, candidates:
  tuple[float, ...])`; `ParamTuner` — UCB1 over `candidates` for one
  `(skill_name, param_name)` pair, backed by `dict[value, (count,
  total_reward)]`. `ParamTuner.load_from_ledger(path, skill_name,
  param_name)` reconstructs those counts from item 3's
  `skill_ledger.jsonl` using the `param`/`param_value` fields that item 3's
  `record_outcome` already carries — no new persistence format.
- **First and only tunable this item lands:** `MineSmeltDeliver.
  deliver_threshold` — verified today as a real, already-live-tested `int =
  10` class attribute (`skills/smelt.py` line 196), already driven from the
  CLI in `live_trade.py --deliver-threshold` (line 154: `miner_skill.
  deliver_threshold = args.deliver_threshold`). Not a constructor param —
  `MineSmeltDeliver` defines no `__init__`, nor does anything else in its
  MRO (`MineAndSmelt`/`Mine`/`Harvest`/`Skill`); the CLI (and, per Wiring
  below, this tuner) both set it via post-construction attribute
  assignment. Candidates e.g. `(5, 8, 12, 20)`.
- **Wiring:** `village.py` calls `ParamTuner.choose()` once per miner **at
  construction time** (agent-session granularity — the smallest thing that
  closes the loop without new mid-session re-parameterization plumbing).
  Because `Profession.planner()` (verified: `profession.py`, builds
  `[SpeakPending(), GoTo(), self.work_skill(), Greet(), Wander()]` from
  inside one method) doesn't hand the caller the constructed skill instance
  directly, `village.py` locates it after the fact —
  `next(s for s in planner.skills if isinstance(s, MineSmeltDeliver))` —
  and sets `.deliver_threshold = chosen_value` on that instance before the
  agent starts ticking. At session end/checkpoint, records `(value,
  session_mean_reward)` back via `SkillLibrary.record_outcome(...,
  param="deliver_threshold", param_value=chosen_value)`.

### Key design decisions

- **Session-granularity choice, not mid-session re-tuning.** A value is
  picked once per agent-session and held fixed — avoids a whole class of
  "did switching the threshold mid-delivery strand the miner" bugs item 5's
  mid-transaction-defer discipline (see below) exists to guard against
  elsewhere; here it's sidestepped by construction instead.
- **One real tunable, not a speculative grid.** `deliver_threshold` is the
  only attribute-settable numeric knob on a work skill in this codebase
  today that plausibly trades off two real outcomes (fewer, bigger
  deliveries vs. more frequent smaller ones) — this item does not invent
  additional tunables on other skills to pad scope.

### Offline tests (planned)

`tests/test_skill_tuning.py` (new, seeded RNG): `ParamTuner` with a
synthetic deterministic reward function (candidate `8` always pays more)
converges to choosing `8` with high probability after N simulated
`update()` calls — the classic bandit-convergence test. `load_from_ledger`
reconstructs identical counts/totals from a hand-written fixture JSONL as
an equivalent direct sequence of `update()` calls. Missing/empty ledger
initializes every candidate at zero pulls, never crashes.

### Live verification gate

**Differential + multi-cycle**, reusing `live_trade.py`'s exact staged
scenario:

- **Two forced control runs (positive/negative control pair):**
  `--deliver-threshold 5` vs `--deliver-threshold 20` on an otherwise-
  identical fresh-account staging — record each run's miner episodic
  reward from the existing transcript output. Establishes which value is
  actually better on this scenario, a ground truth the offline test cannot
  provide (the offline bandit test only proves convergence on a synthetic
  reward function, not that this constant matters live).
- **Tuner-driven runs:** a new opt-in `--tuner --sessions 6` mode drives
  the staged scenario 6 times in one script invocation, each session
  picking its `deliver_threshold` via `ParamTuner.choose()` and recording
  the outcome through `SkillLibrary.record_outcome`. A **fresh process**
  then reads back the accumulated `data/skill_ledger.jsonl` (same cross-
  process-readback discipline as item 3) and the tuner's empirical pull
  distribution across the 6 sessions must visibly concentrate on whichever
  forced-run value scored higher in the control pair above — not a flat or
  uniform split. A no-op or broken tuner shows uniform allocation
  uncorrelated with the known-better control value; this cannot pass
  vacuously, by the same "forced ground truth first, then prove the
  learner finds it" shape PHASE3.md item 4's greedy-vs-`WalkTo` differential
  already uses.

### References

`anima2/skill_tuning.py`, `anima2/skills/smelt.py` (`MineSmeltDeliver.
deliver_threshold`), `anima2/live_trade.py`, `anima2/skill_library.py`
(item 3), DESIGN.md A3.

---

## Item 5 — Automatic curriculum: milestone catalog + cadence-gated picker ⏳

**Voyager's spirit (difficulty ratchets, tasks are proposed) without
Voyager's free-form task/skill invention**, which this codebase has no
safety infrastructure for. Every milestone's completion predicate is
Observation/EpisodicMemory-derived so it can never be gamed by an agent's
self-report — the Foundry kernel's "independently observable fitness"
principle (the scout's citation of `../anima/foundry/kernel/fitness.py`)
applied without needing any of that kernel's wire-level trajectory-capture
machinery, since anima2's own Observation contract already carries the
needed signals (skill values, gold, episode counts).

### Scope

- **`anima2/curriculum.py`** (new): `Milestone(name, description,
  profession, is_achieved: Callable[[SkillContext], bool], progress:
  Callable[[SkillContext], float])` — pure data, no anima2-internal imports
  beyond `contract`/`skills.base`, mirroring v1 `modes.py::Mode`'s
  zero-import discipline (Item 3 already cites this same file for the same
  reason — the second reuse of one v1 pattern this phase).
- **A small, hand-written `MILESTONES: dict[str, list[Milestone]]`
  catalog**, 2–3 entries per existing profession (miner/fisher/blacksmith/
  lumberjack/hunter — verified today's set from `profession.py`'s
  `PROFESSIONS` dict), every predicate Observation/EpisodicMemory-derived:
  e.g. miner — "reach Mining 50" (`obs.player.skills`), "deliver 20 ingots
  in a session" (count of `MineSmeltDeliver` delivery episodes); blacksmith
  — "bank 100 gold"; hunter — "complete 5 loot cycles" (mirrors
  `live_hunt.py`'s own `MIN_LOOT_CYCLES` gate, now expressed as a
  milestone).
- **`CurriculumController`** — cadence-gated exactly like
  `ReflectingCognition` (counts reconsiders, runs on its own daemon thread,
  non-overlap guard, broad `except Exception`, never blocks goal delivery —
  the same pattern verified in `cognition.py` today): computes eligible-
  and-unachieved milestones for the agent's profession; 0–1 eligible →
  picks deterministically, **zero LLM calls**; 2+ eligible → asks the
  tiered `"curriculum_pick"`-role client (item 2's `ROLE_TIER`) to pick
  **one name off the shown list** — never free-form — reusing
  `strategy.py::_is_strategy_viable`'s pattern again: the LLM's pick is
  checked against the shown list (ground truth already computed), and any
  parse failure or a hallucinated non-list name falls back to the
  deterministic heuristic ("lowest current `progress()`" — explore-what's-
  furthest-behind). Chosen milestone is exposed as
  `ctx.memory["curriculum_milestone"]` — **additive/observational only in
  this landing**: no new `Goal` kind, no planner change, nothing reads it to
  drive behavior yet.
- **Mid-transaction defer guard**, ported in spirit from `strategy.py`'s
  own "never switch strategy mid-batch" check: even though nothing consumes
  `curriculum_milestone` to drive behavior yet, the controller defers
  *changing* it while `ctx.memory` shows the agent mid a multi-phase skill
  transaction (e.g. `MineSmeltDeliver`'s `deliver`/`return` phase,
  `BlacksmithMarket`'s `sell`/`bank` phase, `Hunt`'s open/loot phase) —
  keeps the previous pick until the agent is between phases. Defensive
  scaffolding for the day a future item turns this into a real `Goal`
  adoption (at which point switching mid-flight could genuinely strand a
  skill), landed now while it's cheap, rather than retrofitted under
  pressure later.
- **Restart-survives ratchet.** An achieved-transition (not-achieved →
  achieved, exactly once) records one `Episode(kind="milestone", ...)` into
  the agent's `EpisodicMemory` **and** appends one line to a new
  `data/milestones.jsonl` (`ts, persona, profession, milestone`) — the
  controller reads this file at construction time to seed its
  already-achieved set, so a process restart doesn't lose curriculum
  progress or re-fire an already-recorded milestone's episode. Mirrors item
  3's ledger discipline (persisted, cross-restart, read-at-construction).
- **`village.py`** gets an opt-in `--curriculum` flag (mirrors `--forum`/
  `--chatter`), zero effect on rosters without it.
- **Ties to item 1, no new code needed:** item 1's `WikiReportProducer`
  hook, unchanged, is what closes the loop when a milestone outcome
  contradicts a wiki claim (e.g. a milestone's own progress signal
  disagreeing with a wiki-stated number) — the reflection loop that already
  drives item 1's judge sees the same episodes this item adds.

### Key design decisions

- **The LLM's role is deliberately the smallest possible.** Pick one name
  off a short, code-generated, already-eligibility-filtered menu, with a
  working deterministic fallback exercised whenever 0–1 options exist or
  the LLM answers badly — so, like `HeuristicCognition`/`HeuristicReflection`
  before it, the curriculum has meaningful behavior with **zero** LLM
  calls, and the LLM is a thin, occasionally-consulted refinement never the
  only path to a working decision.
- **No new `Goal` kind, no planner change, this landing.** Deliberately
  scoped down from DESIGN.md §6's fuller "automatic curriculum" ambition —
  honest about not attempting real behavior-steering yet rather than
  inventing planner wiring speculatively. `ctx.memory["curriculum_milestone"]`
  is there for reflection/forum prompts and future items to read.
- **Cost-tier budgets derived from milestone difficulty** are explicitly
  **not** attempted here (see item 2's own note) — a natural refinement
  once this catalog is proven live, deferred rather than gating this item's
  landing on machinery that doesn't exist yet.

### Offline tests (planned)

`tests/test_curriculum.py` (new): every milestone's `is_achieved`/
`progress` unit-tested against hand-built `SkillContext`/`Observation`
fixtures at exact boundaries (Mining=49.9 not achieved, Mining=52.0
achieved). **Negative control, not just boundary values:** a zero-progress
fixture (a freshly staged character, no relevant episodes at all) must
leave every milestone's `is_achieved` `False` and `progress()` at its floor
— an idle/off-task agent must never spuriously read as having made
progress. `CurriculumController` with 0/1 eligible milestones makes
**zero** `LLMClient.complete` calls (assert on a `StubLLMClient`'s call
count — cost discipline, same idiom as item 2's tiering test); with 2+
eligible and a well-formed `{"milestone": "<valid name>"}` reply, picks
that one; a name not in the shown list / garbage prose / malformed JSON all
fall back to the deterministic lowest-progress heuristic (three separate
garbage-input tests). An achieved-transition records exactly one
`Episode` — a still-achieved milestone on a later tick must not spam a
second one (explicit idempotency test), and a controller re-constructed
against a `data/milestones.jsonl` fixture that already records the
milestone as achieved must not re-fire it (the restart-survives test).
Mid-transaction defer: a fixture where `ctx.memory` shows an in-progress
`MineSmeltDeliver` delivery phase must not change `curriculum_milestone`
even when a newly-eligible milestone would otherwise win the heuristic.

### Live verification gate

**Differential**, reusing `live_trade.py`'s staging (GM-set exact starting
skills, matching how Phase 3 already stages precise values):

- Rather than waiting on organic skill gain, the GM connection boosts the
  miner's Mining skill mid-run via the existing `GmControl.command_on`
  primitive (`[Set Skills.Mining.Base 51`, the same command family
  `stage()` already issues at setup time — verified in `control.py`) to
  force a live, observable crossing of the "reach Mining 50" milestone's
  threshold deterministically rather than hoping it happens organically
  within a bounded run.
- Confirm from the **live Observation stream** (no test-only hook) that the
  controller's chosen milestone flips once the threshold crosses, and
  exactly one `Episode(kind="milestone")` lands — confirmed by a **direct
  read of the agent's `EpisodicMemory`** post-run, not a log line.
- **Rerun the identical scenario with the curriculum LLM call swapped for a
  `StubLLMClient` returning pure garbage the whole time** — the same
  achieved-transition and episode-recording must still happen, proving the
  deterministic `is_achieved`/fallback path is load-bearing and the design
  doesn't secretly depend on the LLM cooperating. A controller that only
  worked when the LLM answered sensibly fails this differential rerun.
- **Differential-inertness leg:** the same session run without
  `--curriculum` must produce a tick-for-tick identical action trace to a
  pre-item-5 baseline `live_trade.py` run — the opt-in flag changes
  nothing in the fast loop when unset.

### References

`anima2/curriculum.py`, `anima2/cognition.py` (`ReflectingCognition`'s
cadence pattern), `anima2/skill_library.py` (item 3, `diagnose()` feeds
eligibility reasoning), `anima2/live_trade.py`, `anima2/control.py`
(`GmControl.command_on`/`stage`), `../anima/anima/planner/modes.py`,
`../anima/anima/planner/strategy.py`, `../anima/anima/planner/goals.py`
(`is_satisfied_fn`/`progress_fn` shape — the model for `Milestone.
is_achieved`/`progress`), `../anima/foundry/kernel/fitness.py`
("independently observable" principle).

---

## Notes carried into Phase 5 / open follow-ups

Stated plainly rather than deferred silently, per this project's own
documentation habit (PHASE3.md's own "Bugs found live"/"Follow-up"
sections):

- **LLM-authored code remains explicitly out of scope.** Every item in this
  phase composes existing hand-written skills with learned parameters/
  retrieval/picks; a real Voyager-style code-synthesis loop needs a
  sandboxing design (an AST-allowlist interpreter, or a fixed
  composition-DSL over already-existing primitives, never `eval`/`exec`)
  this phase does not attempt. The natural next step once item 3's ledger
  is proven live.
- **Skill-ledger reward is agent-self-reported**, not independently
  GM-verified — weaker than DESIGN.md A6's "agents can't lie" standard,
  which describes v1 Foundry's wire-level, packet-parsed fitness (a
  heavier mechanism anima2 has no equivalent of). Item 3's advisory
  `[Get Gold` corroboration is a cheap partial mitigation, not a fix.
- **Multi-process concurrent ledger writes are untested.** `data/
  skill_ledger.jsonl`'s single-process append-only writes are safe under
  CPython's GIL; a fleet of villages writing the same file simultaneously
  is a real scenario once multi-village deployment is real, and should get
  an explicit file-lock or per-process-path convention before then.
- **Cross-repo git writes from an autonomous thread** (item 1) reintroduce
  the "own-shard-adjacent pollution" risk class this project has already
  been bitten by once (`anima2-live-verification` memory note) — mitigated
  by the circuit breaker, the tested never-push invariant, and disposable-
  clone-only live testing, but a future increment running this against the
  real `../uowiki` for the first time deserves the same care as a first
  live-shard run.
- **Cost-tier budgets derived from curriculum/task difficulty** (item 2's
  own note, item 5's own note) is a natural refinement once both land, not
  attempted in this phase.

---

## References

- DESIGN.md §6 (learning & accumulation ordering), §7 (LLM strategy), §10
  (roadmap — this phase's entry now points here), §11 (open decisions).
- PHASE2.md B1 — the read-only wiki index and the reflection loop this
  phase's items 1 and 5 build directly on top of.
- PHASE3.md — the multi-cycle/differential/provenance-aware live-proof house
  style every gate above follows; item 4's greedy-vs-`WalkTo` differential
  is the direct model for this phase's item 4 gate.
- `anima2/wiki.py`, `anima2/cognition.py`, `anima2/llm.py`,
  `anima2/agent.py`, `anima2/skills/base.py`, `anima2/skills/smelt.py`,
  `anima2/profession.py`, `anima2/control.py`, `anima2/village.py` — the
  existing modules every item above extends.
- `../anima/anima/planner/circuit_breaker.py`, `../anima/anima/planner/
  strategy.py`, `../anima/anima/planner/modes.py`, `../anima/anima/planner/
  goals.py`, `../anima/tools/wiki_report.py`, `../anima/anima/skills/
  base.py`, `../anima/foundry/kernel/fitness.py` — the v1 assets this
  phase's items mine or port.
- `../uowiki/tools/mcp_server.py`, `../uowiki/CLAUDE.md` — the wiki-side
  half of item 1's mechanism (confirmed functionally identical to the v1
  CLI script this phase actually ports from, since anima2 has no MCP
  client at runtime).
