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
- **(a) STILL PARTIAL — and the partial half MOVED on 2026-08-05, so read the split
  carefully.** **All SEVEN Life-construction sites are now steerable end to end**, up
  from two: the two `LifeSpec` runners (`village.run_carpenter_life(knobs={...})`,
  `run_woodsman_life(knobs={...})`) forward `LifeSpec.knobs` → `LifeRunner.build_life`,
  and the five inline ones (`run_forge_pair`'s tinker — the FLAGSHIP positive-margin
  chain — `run_supply_pair`'s woodsman AND carpenter, `run_warrior_village`'s roster,
  `run_artisan_mage_village`'s mage) go through `life_runner.build_tuned_life`, which
  reads the allowlist off the class it builds. Every reader clamped in `anima2/knobs.py`;
  every runner validates BEFORE its first packet (mutation-tested — with a late check the
  forge pair swallows the typo as two login failures and prints "the pair needs both;
  aborting"). `--knob [ROLE:]KEY=VALUE` reaches all six Life-bearing runners, and
  `--supply-pair` REQUIRES the role prefix because both its Lives have a `bank_reserve`.
  Before 2026-08-02 the whole channel was WIRELESS:
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
  the other end, and closing the construction sites did NOT touch it.** The channel is
  now complete and there is still nothing at the far end pushing values into it:
  `foundry/archive.py::Genome`'s four axes (`profession`, `sociability`,
  `deliver_threshold`, `cognition_tier`) map onto no knob at all, so no genome steers any
  Life — and three of those four are not knob-shaped even in principle (`profession` is
  identity and is REFUSED by the allowlist by design, `sociability` is a `Persona` field,
  `cognition_tier` builds an LLM client), so the bridge is a design question and not a
  wiring one. `foundry/eval.py::_build_agent` builds a bare `Agent` with one work skill,
  never a Life, so the fitness harness has never measured a Life at all. Only FIVE
  thresholds are knobs (`bank_reserve`, `econ_grace`, `disagreement_ticks`, the tinker's
  `bank_trip_surplus`, and `wander_leash` as of 2026-08-05 — §E's "exploration radius",
  audit follow-up 4 / §11: it used to ride a second channel with a second clamp, and it is
  the only knob a runner ALSO writes, so a tuned value now outranks `set_leash`'s derived
  one); §E's retreat thresholds and rest timing cannot become knobs while
  the capability manifest validator forbids per-instance survival state.
  **The "no tuned knob has ever changed a live trajectory" clause that stood here is
  RETIRED as of 2026-08-07** (audit §17): `--forge-pair --knob bank_trip_surplus=10`
  banked at gold 105/112/112/117/117/117, every one of them inside the 93..157 band where
  the DEFAULT rule says keep crafting — so §E's criterion is met on ONE axis, on the
  flagship positive-margin chain, against a written-down-beforehand prediction. What is
  still missing for precondition (a) is the rest: four other knobs unproven, no controlled
  A/B (293g banked vs a 193g run from another day is indicative, not controlled), and no
  evidence the steer was GOOD rather than merely real.
  Detail and follow-ups: `docs/AUDIT-2026-07-29.md` (2026-08-02 entry, follow-ups 2-4;
  2026-08-03 entry for the live proof; §10 for the seven-site wiring).

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
**A fourth run the same day — a forced-state gate, `anima2/live_frame_overdue_gate.py` —
added bound 3. So the standing count is bounds 2 and 3 live-proven, bound 1 not.**

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
- **BOUND 3 IS NOW LIVE-PROVEN TOO, by a purpose-built gate — `anima2/live_frame_overdue_gate.py`,
  first attempt, exit 0, ~4 min** (audit §7). On the shard: a `craft_tongs` frame went
  **overdue at economy tick 301 against `deadline_tick=300`** (the `>` comparator, one tick
  past where `expire_due`'s `>=` would have won had the frame been able to yield);
  `_repair_overdue_frame` closed the craft FSM's OWN gump on that tick
  (`closing an unowned gump id=2066278152`, `gumps` 1 → 0 next observation); the repair
  **extended the hold exactly one economy tick** and then the hold **released** — `mode=hunt`,
  frame still live and still overdue, the documented worst case *"a stale frame, but alive"*.
  The hold had been the orchestrator's for **299 consecutive ticks** (`want=None hold=True`)
  first. Reaching it needs forced state, not patience: `CraftItemCapability.max_goal_steps=240`
  is below the 300-tick deadline, so an ordinary craft closes its own gump before the frame
  can go overdue — the gate wounds the character so `Survive` (skills[0] of every capability
  planner) starves the FSM while `expire_due` keeps running. Measured self-check:
  `cap_craft_steps` frozen at 2 for the whole window. 7 offline tests reproduce it and kill
  three mutants, including M1 — literally the pre-review two-bound `holding` clause, the one
  measured in `docs/AUDIT-2026-07-29.md` §5's first refutation as *"four of the five Lives
  were pinned in economy mode with `hunt_after = 0` for the whole 3000-tick window"*. That
  is why this bound matters: it is what stops the fix being WORSE than the defect.
- **BOUND 1 IS NOW LIVE-PROVEN TOO (2026-08-09), by the purpose-built
  `anima2/live_buy_giveup_gate.py` — first attempt, exit 0, all eleven flags** (audit §19).
  A Healer staged where `live_buy_goal` stages a Blacksmith gives the buy FSM a real vendor
  window that genuinely lacks its offer, so the trip re-rolls its full budget (`rerolls=4/4`),
  cancels rather than buys (`cancels=5` — four re-rolls plus the exit-edge close,
  `nothing_was_bought`), gives up, writes follow-up 19's marker and retires
  `(1, 'buy_ingots', 21, 180, 'giveup')` — **age 21 against a 180-tick budget**, where
  before the same shape burned all 180. That one run also gave the partial-subset re-roll
  path and §15's `{ns}_closing_window` marker their first live exercise. **So all three
  bounds are now live-proven.** What it does NOT prove: that an ordinary forge day retires a
  buy frame this way (§18.3's re-run never entered the path), and there is no offline
  reproduction because that needs a `MockBody` vendor (follow-up 22).
  Historical note, kept because it stood for eleven days —
  **bound 1 was unexercised as far as any log could tell** — and the bound-3 gate does not touch it: every `sell_tongs` and `bank_gold` frame
  closed on a SUCCESSFUL sale or deposit, which `CapabilityGoalComplete` also closes by its
  ACHIEVEMENT branch, and the status line cannot tell the branches apart — a ladderless
  `buy_iron` frame closed just as fast (last seen at age 4), so a low max age is no
  give-up-ladder signature. Proving it needs a transaction that FAILS. **No death occurred
  mid-transaction on any run, so the death override is unexercised live too**, and
  **`!frozen`'s clean sheet on the three ordinary runs (0 of 306 samples) is ENTAILED by
  those absences, not independent of them**: `life_runner.py` prints `!frozen` only when a
  frame is live AND the mode is not economy, and `tick` forces economy whenever a frame is
  live unless a death episode is open or the frame is overdue-and-unrepaired. Also
  unreached: the `OVERDUE_REPAIRS` cap (one close spent of three), any extension above 1
  tick, and `_clear_stale_ui`'s vendor BUY/SELL branches *at an overdue frame*.
- **Watch for, on any live run (added 2026-08-13, follow-ups 32 and 35):** `trip=` on the
  per-agent line — `d=` frozen ABOVE its reach while `stall=` climbs to `5/6` is a wedged
  walk, the §30.2 failure that cost a whole day and printed nothing. A healthy trip's `d=`
  falls and ends `<=2`. Beside it, a `WEDGED WALK` line is the same failure after 240
  ticks of it. Neither has printed on a shard; the prediction for `trip=`'s first run is
  written down in `docs/AUDIT-2026-07-29.md` §36.5, before the run. Note that the OTHER
  two liveness alarms are structurally blind to this failure — `NO PROGRESS` because
  `steps` counts emitted walks, `NO OUTPUT` because a give-up records an episode (§37,
  the second is still open) — so do not read their silence as health here.
- **Watch for, on any live run:** `!frozen` on a live frame while the character is not dead
  (the regression detector), a `+hold` whose `@age` stops climbing (the old defect wearing
  the new marker), and `FRAME OVERDUE` (bounds 1 and 2 both failed). Do NOT use the
  `+hold`+`!overdue` pairing as a primary signal on a monitored run — it lasts exactly one
  tick and `life_runner` samples every ~9; read `FRAME OVERDUE` and the `closing an unowned`
  line immediately before it instead (audit §7.6).

Full evidence: `docs/AUDIT-2026-07-29.md`, 2026-08-03 §5 (the fix), §6 (the three ordinary
live runs) and §7 (the bound-3 gate), follow-ups 12, 15, 16, 17. **Two separate, non-hold
defects those runs found are now REDUCED, not closed — and this paragraph called them "still
open" for a day after they were fixed, which is the staleness this file has already been
burned by once.** (1) A stale vendor BUY window that would not clear ate the last 556 ticks
of the 1800-tick run (follow-up 16) — the `+2528g/h` final-sample rate is an average over a
run whose last third earned nothing. A trip now cancels the window it opened and the
per-trip re-roll counter is cleaned up, and a 1200-tick run cut `ui=shopbuy` from 75/208
samples to 2/136 — but a buy still stalled to 176/180 and expired on its deadline, so the
wedge is reduced and not gone. (2) **The miner stopped producing at t=765 and nothing flagged
it** (follow-up 17): the flagship miner→tinker chain did bank 503g (+585g net) over 1800
ticks, but Grimm's cumulative reward froze at `out+176.9` and never moved again, and he never
smelted or delivered on any of the 126 remaining samples — five of the six deposits, and
everything above the first 23g, were the tinker working through ONE 69-ingot delivery that
landed at t≈756. The chain's supply side stopped at 43% of the run, which the headline
numbers do not show. **Both halves of the missing readout now exist**: a work-liveness alarm
(`eps=`/`NO OUTPUT`/`!stalled`, live-fired twice and truly on a 1200-tick run) says an agent
STOPPED, and as of 2026-08-05 `hp=`/`deaths=`/`DIED`/`BACK ALIVE` on every agent of every
runner says whether it DIED — the distinction between a corpse, a lost pickaxe and a dead
vein, which no log before could make. The death half is OFFLINE ONLY; no forge log has ever
contained a death. Audit §9, `docs/MONITORING.md`.

**The freeze itself is now DIAGNOSED — three defects, fixed offline, unattributed live
(2026-08-12, audit §34).** After five runs freezing at ~58%, an adversarial review first
**refuted the hypothesis it started from** (ore banks are 8x8, so stands packed closer would
share one — they are not: 12 stands, 12 distinct banks, spacing exactly 8). What was wrong:
(1) `harvest_idx` advanced only on cliloc 500493, LUMBERJACKING's "no resources" message,
while mining's is 503040 — so `Mine` cycled its node list **never**, and 12 of the pool's 14
distinct ore banks were unreachable by construction, with any stand whose first node was
merely untargetable a total loss; (2) a blind relocation left the condemned stand's nodes
installed, so every swing after a 12-tile hop answered "That is too far away" — a FAILURE
verdict, so the window filled and it relocated again, forever; (3) `nodes[0]` was the
raster-order CORNER of the reach box, and relocation deliberately arrives one tile short, so
corner + one short exceeds ServUO's MaxRange=2. All three carry tests that fail on the old
code (9 mutants, all killed). **What no evidence says is which of the three the live dead
tail actually was** — that needs one ordinary forge day, and the cause split (`nores=`/
`inval=`/`packfull=`, a checkable partition) plus `banks=` were added to collect it. The
prediction is written down in §34.4 *before* the run, as this project requires:
`nores=` should dominate `inval=` on healthy early stands; if `inval=` dominates in the dead
tail instead, the lever is geometry rather than give-up speed. **Follow-up 28 is closed as
"do not"** — the 24-sample window cannot be shortened; measured per-stand false-fire floors
reach 17.3, and both arguments for shortening it were destroyed (§34.5).

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
