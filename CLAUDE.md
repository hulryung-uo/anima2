# CLAUDE.md — anima2

**Read [`docs/DESIGN.md`](docs/DESIGN.md) first.** It is the source of truth:
what anima2 is, the decision history (the *why*), architecture, the
Observation/Action contract, the learning plan, the roadmap, and what to reuse
from `anima` (v1). This project is designed to be resumable from that doc alone.

## What this is
A new, from-scratch **autonomous AI agent** that plays Ultima Online — the
**Brain**. It drives a body, [`anima-core`](https://github.com/hulryung-uo/anima-client/tree/main/crates/anima-core)
(Rust headless UO client), through a structured **Observation/Action contract**.
Clean redesign of `../anima` (v1, Python); mines v1 for assets and lessons.

## Current state (2026-07-29)

Phases 1–7(item 1) of the original roadmap are COMPLETE and live-verified — the full
narrative is in [`docs/HISTORY.md`](docs/HISTORY.md), per-phase detail in
[`docs/PHASE2.md`](docs/PHASE2.md) … [`docs/PHASE7.md`](docs/PHASE7.md). 1300+ tests,
ruff clean. Highlights that matter for resuming:

- **Foundry** (`anima2/foundry/`): independent fitness kernel, repeatable eval harness,
  MAP-Elites archive + evolution loop. The Phase-7 evolution-vs-random rerun is
  DELIBERATELY DEFERRED — see "Two roadmaps, one decision" below.
- **Autonomy thread** ([`docs/AUTONOMY-ROADMAP.md`](docs/AUTONOMY-ROADMAP.md)): the
  immutable capability registry + closed capability cognition (B3/B4), and five
  autonomous per-profession "Lives" on one orchestrator (`anima2/warrior_life.py` base;
  mage/woodsman/carpenter/**tinker** subclasses — the tinker is the flagship
  positive-margin one, and older "four Lives" phrasings here predate it) — each
  hunts/works, restocks, banks, and
  self-reports rule-vs-gate disagreements. Runners ride `anima2/life_runner.py`
  (staging verification, gold provenance, both-memory leash, want/admitted/ready
  telemetry — every clause a live-caught lesson; see `docs/AUDIT-2026-07-29.md`). Two
  single-source modules sit under all of them: `anima2/obsview.py` (every observation
  readback — what is in OUR pack, worn on us, loose in reach, in our bank box) and
  `anima2/knobs.py` (the one clamped read every LIFE tuning knob goes through —
  `wander_leash` is the standing exception: it rides `Staged.leash` and is clamped
  inline in `skills/movement.py`, to the class default rather than a floor; audit
  follow-up 4).
- **Three live inter-agent supply chains**, coordinating only through items on the
  ground: tinker→mage (gold), lumberjack→carpenter (boards), miner→tinker (iron/tongs
  — `run_forge_pair` sets the miner's `smithy_drop`, and forge15 records the tinker
  fetching 38 delivered ingots off the ground). The third is the ONLY positive-margin
  one, and it is the one precondition (b) below rests on; "two chains" stood here while
  the same file called the forge pair live-proven. Economics warning: at
  vendor prices every carpentry recipe destroys value (`docs/CARPENTER.md`) — the
  board chain is a mechanism proof, NOT an economy. Profession docs:
  `docs/SWORD-WARRIOR.md`, `docs/MAGE-AND-PIPELINE.md`, `docs/WOODSMAN.md`,
  `docs/CARPENTER.md`, `docs/MONITORING.md`.

## Two roadmaps, one decision

`docs/PHASE7.md` item 2 names a `--genomes 20` evolution-vs-random rerun as next. The
newer [`docs/AUTONOMY-ROADMAP.md`](docs/AUTONOMY-ROADMAP.md) §E states the actual
criterion, quoted verbatim:

> "Re-run evolution versus random only when every searched axis changes a meaningful
> live trajectory; a larger budget alone is not an autonomy milestone."

**Decision: the rerun WAITS** until (a) the genome's axes can steer a full Life —
Life thresholds as constructor parameters routed through single sources, audit
proposal 5 — and (b) at least one positive-margin economy loop exists, so
gold-per-life fitness means something. The two are at DIFFERENT stages (2026-08-02):

- **(b) MET.** The miner→tinker tongs pair is live-proven at positive margin (audit
  proposal 6; forge8/forge14 banked deposits), so gold-per-life measures something.
- **(a) STILL PARTIAL — do not read it as done.** Two of seven Life-construction sites
  are now steerable END TO END: `village.run_carpenter_life(knobs={...})` and
  `run_woodsman_life(knobs={...})` take a caller-side dict and forward it
  `LifeSpec.knobs` → `LifeRunner.build_life` → the Life's own memory/attributes, every
  reader clamped in `anima2/knobs.py`. Before 2026-08-02 the whole channel was WIRELESS:
  proposal 5's constructor parameters existed but no production site could pass one.
  **What is still missing is the half §E's criterion is actually about — a SEARCHER on
  the other end.** `foundry/archive.py::Genome`'s four axes (`profession`, `sociability`,
  `deliver_threshold`, `cognition_tier`) map onto no `LifeSpec.knobs` entry at all, so no
  genome yet steers any Life; the four inline runners (`run_supply_pair`,
  `run_forge_pair`, `run_warrior_village`, `run_artisan_mage_village` — five of the seven
  sites, and the flagship positive-margin miner→tinker pair is among them) build their
  Lives with no factory and have no seam to forward through; only four thresholds ride
  the channel at all (`bank_reserve`, `econ_grace`, `disagreement_ticks`, the tinker's
  `bank_trip_surplus`); §E's retreat thresholds and rest timing cannot become knobs while
  the capability manifest validator forbids per-instance survival state; and **no live run
  has ever used a tuned knob** — the whole channel is offline-proven only.
  Detail and follow-ups: `docs/AUDIT-2026-07-29.md` (2026-08-02 entry, follow-ups 2-4).

Do NOT burn a multi-hour single-GM live budget on the rerun before that; a
stale "Next:" pointer here almost caused exactly that (`docs/AUDIT-2026-07-29.md`).

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

## Stack (resolved in Phase 1 — DESIGN.md §9; what is still open is §11)
Python brain talking to anima-core over the contract via IPC (reuse v1's
brain/Foundry/wiki/LLM assets). LLM provider abstracted, default to latest Claude
family, tiered (Haiku/Sonnet/Opus); **never in the fast loop**. Consult the
`claude-api` skill when wiring LLM calls.

## Key references
`../anima` (v1: personas, planner, Foundry kernel, wiki flywheel), `../uowiki`
(semantic memory + MCP tools), `../anima-client/docs/DESIGN.md` (the body + the
original contract sketch), `../servuo` (local test shard).
