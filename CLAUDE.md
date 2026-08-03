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
  **The channel is LIVE-PROVEN end to end as of 2026-08-03.** `python -m anima2.village
  --carpenter --knob bank_reserve=400 --ticks 300` against a real ServUO shard printed
  `staged: Sten@(2609, 474) and 129g seed  (reserve 400)` — 400 is the TUNED value read
  back off the economy agent's own memory through `skills/market.py::_bank_reserve`, not
  the module default of 129 (`carpenter_life.BANK_RESERVE`) — so command line →
  `_parse_knobs` → runner argument → `LifeSpec.knobs` → `LifeRunner` → the Life's economy
  memory → every clamped reader has now carried a value on a shard. That retires
  "offline-proven only" for the knob channel, and **nothing else**: the tuned value was
  behaviourally INERT in that run (the carpenter ended on 93 gold, below both 129 and
  400, so no banking decision could differ), so the CHANNEL is proven and STEERING is
  not. Run recorded in `docs/AUDIT-2026-07-29.md` (2026-08-03 entry).
  **What is still missing is the half §E's criterion is actually about — a SEARCHER on
  the other end.** `foundry/archive.py::Genome`'s four axes (`profession`, `sociability`,
  `deliver_threshold`, `cognition_tier`) map onto no `LifeSpec.knobs` entry at all, so no
  genome yet steers any Life; the four inline runners (`run_supply_pair`,
  `run_forge_pair`, `run_warrior_village`, `run_artisan_mage_village` — five of the seven
  sites, and the flagship positive-margin miner→tinker pair is among them) build their
  Lives with no factory and have no seam to forward through; only four thresholds ride
  the channel at all (`bank_reserve`, `econ_grace`, `disagreement_ticks`, the tinker's
  `bank_trip_surplus`); §E's retreat thresholds and rest timing cannot become knobs while
  the capability manifest validator forbids per-instance survival state; and the one live
  run to use a tuned knob used ONE, on the cheapest of the two wired runners, where it
  changed nothing it could have changed.
  Detail and follow-ups: `docs/AUDIT-2026-07-29.md` (2026-08-02 entry, follow-ups 2-4;
  2026-08-03 entry for the live proof).

Do NOT burn a multi-hour single-GM live budget on the rerun before that; a
stale "Next:" pointer here almost caused exactly that (`docs/AUDIT-2026-07-29.md`).

**The exit-edge hold: live-proven in part, and the parts that are not are named (2026-08-03).**
`WarriorLife.tick` gained an **exit-edge hold** — the economy mode is held while a goal
frame is live, so a transaction the rule stopped wanting still gets finished, bounded three
ways: the FSM's give-up ladder, the frame's deadline, and (because neither of those is
general) an overdue frame releasing the hold. It fixes a defect found ON a live run and it
changes **which inner agent is ticked**, the hot path of all five professions. Three live
runs on 2026-08-03 retired "OFFLINE ONLY" for the DEFECT and for exactly **one** of the
three bounds. *(An earlier draft of this section said two of three; the corrected reading,
§6.3, is that bound 1 was never distinguishable from ordinary success on these logs.)*

- **LIVE-PROVEN.** The defect is gone: the same command, knob and tick count that produced
  30 lying status lines out of 32 (`admitted=sell_furniture` with `furniture=0`, nothing
  executing it) produced **0 out of 33**, with net gold, banked and end state identical.
  The hold itself was directly observed **31 times** in an 1800-tick forge-pair run, its
  frame's `@age` advancing 1:1 and the frame retiring inside its budget; frame ages reset
  across frames instead of climbing forever; **bound 2, the deadline, closed two frames**
  (`craft_tongs` at 292/300 holding 4 tongs of a batch of 5, `buy_iron` at 177/180 holding
  1 iron — neither achieved, and neither family writes a run-finished marker, so
  `expire_due` is the only thing that could have closed either); and the rule-vs-gate
  detector and its stale-UI repair both still fired, twice, after frames retired — so the
  hold does not mask them.
- **NOT PROVEN, for two different reasons.** **Bound 1 (the FSM give-up ladder) is
  unexercised as far as these logs can tell:** every `sell_tongs` and `bank_gold` frame
  closed on a SUCCESSFUL sale or deposit, which `CapabilityGoalComplete` also closes by its
  ACHIEVEMENT branch, and the status line cannot tell the branches apart — a ladderless
  `buy_iron` frame closed just as fast (last seen at age 4), so a low max age is no
  give-up-ladder signature. **Bound 3 has ZERO live ticks:** **no frame ever went overdue**,
  so the overdue release plus `_repair_overdue_frame` — the bound added because the FIRST
  version of this fix livelocked the wedged world, measured in `docs/AUDIT-2026-07-29.md`
  §5's first refutation as *"four of the five Lives were pinned in economy mode with
  `hunt_after = 0` for the whole 3000-tick window"* — never ran. No death occurred
  mid-transaction either, so the death override is unexercised live too, and **`!frozen`'s
  clean sheet (0 of 306 samples) is ENTAILED by those two facts, not independent of them**:
  `life_runner.py` prints `!frozen` only when a frame is live AND the mode is not economy,
  and `tick` forces economy whenever a frame is live unless a death episode is open or the
  frame is overdue-and-unrepaired. A forced-state gate is how to reach bound 3 on purpose
  (`docs/AUDIT-2026-07-29.md` §6.3 says what to build) — but an ordinary run can reach it by
  coincidence, because `_craft_can_yield` refuses with ANY surface open and these runs had
  14 `ui=gump` samples (all `craft_tongs`), just never one sitting at a deadline.
- **Watch for, on any live run:** `!frozen` on a live frame while the character is not dead
  (the regression detector), a `+hold` whose `@age` stops climbing (the old defect wearing
  the new marker), and `FRAME OVERDUE` (bounds 1 and 2 both failed).

Full evidence: `docs/AUDIT-2026-07-29.md`, 2026-08-03 §5 (the fix) and §6 (the live runs),
follow-ups 12, 15, 16, 17. **Two separate, non-hold defects those runs found are still
open.** (1) A stale vendor BUY window that would not clear ate the last 556 ticks of the
1800-tick run (follow-up 16) — the `+2528g/h` final-sample rate is an average over a run
whose last third earned nothing. (2) **The miner stopped producing at t=765 and nothing
flagged it** (follow-up 17): the flagship miner→tinker chain did bank 503g (+585g net) over
1800 ticks, but Grimm's cumulative reward froze at `out+176.9` and never moved again, and he
never smelted or delivered on any of the 126 remaining samples — five of the six deposits,
and everything above the first 23g, were the tinker working through ONE 69-ingot delivery
that landed at t≈756. The chain's supply side stopped at 43% of the run, which the headline
numbers do not show.

## Dev
- Offline: `uv venv && uv pip install -e ".[dev]"` · `python -m anima2` · `pytest -q` · `ruff check .`
- Live: build the bridge in the sibling repo (`cd ../anima-client && cargo build -p anima-net`),
  then `python -m anima2.live <host> <port> <user> <pass> [--goto X Y] [--llm]`.
- The bridge bin + JSON shapes live in `../anima-client/crates/anima-net` (`src/bin/agent.rs`,
  `src/json.rs`) — keep them in lockstep with `contract.py`. The lockstep is ENFORCED by one
  number, `ipc_body.SUPPORTED_SCHEMA_VERSION`, checked against the bridge's `ready` line: a
  mismatch aborts the run at the handshake with `unsupported bridge schema N; expected M`.
  Sibling-repo work on the body therefore lands here as a bump plus a serializer diff —
  2026-08-03 spent a live attempt learning that (`docs/AUDIT-2026-07-29.md`, 2026-08-03).

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
