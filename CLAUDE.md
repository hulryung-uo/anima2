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
274), ruff clean. **Next:** Phase 4 item 3 — skill library v0 (registry,
keyword retrieval over `_textindex.py`, persisted outcome ledger) — see
[`docs/PHASE4.md`](docs/PHASE4.md) for the full five-item work breakdown
(learning stack: wiki write loop — done, cognition cost tiering — done,
skill-library registry + ledger, `deliver_threshold` bandit tuning, automatic
curriculum).

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
