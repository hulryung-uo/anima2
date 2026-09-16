# Autonomy improvement pass — 2026-09-13

The requested scope is an aggressive, broad improvement of the existing agent. The
baseline is `e0d9d24`: 1695 offline tests pass, Ruff passes, and the offline demo runs.
No live process was running when this pass started.

The work is organized around observed barriers to continued autonomous play:

1. **Nonblocking Life steering.** The optional real model in `_LifeClient` is called
   synchronously by `Agent.tick`. Move only model work off-thread; preserve synchronous
   deterministic admission, current readiness checks, bounded pending decisions, and
   survival/goal ownership. Prove a blocked model cannot block ticks or apply stale work.
2. **Progress-aware market movement.** Audit sections 70–71 record a pinned banker
   blocking sales. First measure best remaining distance, including moving without
   approaching; then improve obstacle recovery without removing the bounded-failure
   behavior. Verify both reachable detours and genuinely unreachable destinations.
3. **Trade after workplace relocation.** Audit section 72 records 7 sales before a
   grove hop and 0/38 after it. Keep shops in the world and make journeys from the new
   workplace complete. Pin the recorded coordinates and exercise outbound and return
   legs with the actual Life/capability machinery.
4. **Usable Life configuration search.** Give Life knobs an explicit configuration
   model and a reproducible search/export/apply path. Identity must remain separate from
   thresholds, invalid settings must fail before connecting, and flat offline axes must
   be reported rather than advertised as improvements. Do not alter the fitness ruler.

Each item needs regression tests that fail on the baseline, the complete offline suite,
and Ruff. Live changes need a prediction recorded before running, with outcomes scored
against it. Any unavailable live verification stays explicitly unverified. Current-state
documentation will be reconciled with the implementation, preserving historical audits.

## Results

Items 1–4 are implemented and the planned verification is complete. The two live
discoveries in monitoring and NPC placement are also fixed and covered by regressions.

- Last full offline run: **1753 passed in 8.83s**, Ruff clean, including delivery-home
  continuity, stationary-production monitoring, and fresh NPC placement readback.
- Six deliberate regressions were rejected: no sidestep, unbounded distance drift,
  stale workplace, blocking model, stale goal ownership, and candidate ABA reuse.
  The turn-first fixture also rejects removing confirmed-turn handling, reproducing
  the first live run's age-8 give-up exactly. Mutants ran in temporary copies, never
  in the live working tree.
- Actual CLI search: seven evaluations, `bank_reserve=129,200,400` crossed with
  `econ_grace=2,6`, seed 13. Reserve changes banked gold in two matched contexts;
  grace is flat in three matched contexts. Report and candidate are
  `.logs/life-search-20260913.json` and `.logs/life-candidate-20260913.json`.
- Profile validation/routing is tested through all six village CLI runner branches.
  Profiles contain only role plus typed thresholds; the old four-axis Genome and
  independent fitness ruler are unchanged.
- Workplace continuity also covers board deliveries: each new delivery snapshots
  the current verified workplace, while an in-flight delivery keeps its original
  return target. This closes the same stale-home defect in the supply-pair path.
- The second forge run exposed false `NO PROGRESS` warnings while a stationary
  tinker was completing sales and deposits. Life exposes hunt reward, so that pulse
  missed economy production. Successful frame retirements now reset it; failed
  frames do not. Regression tests drive both outcomes through the actual worker.
  Existing thresholds and the emitted-step pulse are preserved.
- The focused detour setup exposed stale NPC placement readback: `stage_npc`
  moved a wandered NPC but returned the observation from before that move, so
  `stage_shops` saved an obsolete route. Staging now pins, places, then verifies
  a fresh observation of the same serial at the exact requested coordinates.
  Refused placement and a different nearby serial fail closed. Three regression
  cases pass in the 36-test control/harness suite. This corrects the fixture's
  world readback; it does not move shops during agent play.

## Live predictions (written before execution)

Live checks will use a disposable copy of the local ServUO save, bound to loopback;
the original shard/save will not be modified. Brain/bridge both advertise schema 31.

- Forge pair, 1800 ticks: the tinker remains alive, at least one delivered iron batch
  reaches it, and a sale and bank deposit complete. A reproduced banker obstruction
  should produce a changed walk direction and recovery, rather than a long sequence
  of same-location sale give-ups. Refuting outcomes are failure to trade after an
  observed detour, a new indefinite walk, or damage/death in the staged pocket.
- Woodsman, 900 ticks: after a verified grove relocation, `wander_home` and the next
  transaction's return home follow the reached workplace. At least one sale after a
  hop must complete without moving the vendor. A run with no hop is inconclusive for
  this claim. A hop followed only by give-ups refutes the proposed closure.
  The run also applies `.logs/woodsman-default-profile-20260913.json`, exporting
  the existing default reserve through `LifeProfile`, to check the file-to-live
  channel without changing the policy threshold. A mismatched staging readback
  would refute that channel.
- Model-delay safety is checked offline with a blocked model and real Life ticks;
  this pass does not claim that a network model made better choices on the shard.
- Focused forge detour, at most 180 ticks, after the paired run exits: stage a seller
  at `(2610,476)`, banker at `(2610,475)`, vendor at `(2610,473)`, and eight granted
  tongs. Enter the same economy-to-economy sale edge as the historical obstruction.
  Require observed movement away from the blocked direct heading, all eight items
  sold for 56 gold, a successful frame, and return to the exact home tile. Any first
  frame give-up or timeout refutes recovery. This is a granted-inventory movement
  fixture, not income evidence. Driver: `.logs/verify-forge-detour.py`.

These runs test continued operation, not a controlled gold-per-hour improvement or
the unimplemented fresh-character capstone.

### First forge run: refuted, stopped after diagnosis

`.logs/improve-forge-20260913.log` reached the original `(2610,476)` wedge. Both
characters remained healthy, iron was delivered, but the tinker changed headings and
retired repeated sales at age 8 without moving. This refutes the first sidestep fix.
The run was deliberately interrupted after the repeated failure was established.

ServUO's `Server/Mobile.cs:3120` explains the missing condition: a direction change
only turns the character; translation requires another request in the same direction.
The immediate-step MockBody hid this. Market movement now repeats a confirmed turn
before considering another sidestep, and the journey tests run in both immediate-step
and ServUO turn-first modes. The original six-denied-step bound is preserved; turns
still consume the independent 24-tick distance budget. A second forge run will test
the same prediction with this missing condition included.

### Second forge run: paired operation confirmed

`.logs/improve-forge-turns-20260913.log` finished normally at 1800 ticks per worker.
Both survived. Pim completed 91 of 97 retired frames: 13 iron fetches, 32 crafts,
32 sales, 12 deposits and two iron purchases. One craft and five purchases gave up;
no sale gave up. The final purse held 82 gold and the bank 958, starting broke.
No `DIED`, `WEDGED WALK`, `FRAME OVERDUE` or `NOTHING LANDS` alarm fired.

The natural run did not establish another occurrence of the exact banker obstruction;
it confirms continued production and banking, while the focused gate separately tests
that geometry. It is not a controlled before/after income comparison: the disposable
world also retained resource depletion from the interrupted first run.

This process was started before the production-pulse correction and printed 29
`NO PROGRESS` warnings: 28 from the productive tinker, and one from the miner.
The tinker's successful economy frame history directly contradicts its warnings;
the miner's separate warning is not scored as that defect. The new regression fixture
kills the old pulse; the running process was not
hot-patched or restarted to hide its output.

### Focused detour setup findings

An initial driver construction error was caught before play and corrected. The next
setup rejected a banker readback at `(2610,474)` instead of requested `(2610,475)`;
no gameplay ran, so this is an invalid fixture, not a movement verdict.
`.logs/improve-forge-detour-stale-staging-20260913.log` preserves that failure.
The fresh-readback correction above is in place for the next attempt, which keeps
the original movement prediction and exact coordinates.

With correct placement, a fresh full-stamina character walked through the banker and
completed the sale/return in eight ticks. That log's initial driver printed PASS by
mistakenly counting a return heading as a detour; inspection of the coordinates
invalidates that verdict for obstacle recovery. It only confirms the transaction and
turn-first return. The log is retained as
`.logs/improve-forge-detour-push-through-20260913.log`.

ServUO `Server/Mobile.cs:3541` permits pushing through a mobile at full stamina.
The next fixture sets stamina to 20 before play, requires the first northward step
to be observed refused, and counts changed headings only on the outbound sale leg.
The coordinates, 180-tick bound, exact sale amount and return requirements stay fixed.
The attempted value of 20 turned out to equal this fresh character's maximum, so
the strengthened driver correctly rejected another push-through (eight ticks).
The next setup reads `StamMax`, sets one quarter of it, and requires the first live
observation to show `0 < stamina < dexterity`, avoiding an assumed maximum.

### Focused detour: confirmed with an observed refusal

`.logs/improve-forge-detour-20260913.log` passes the strengthened gate in **12 ticks**.
The seller begins at `(2610,476)` with stamina `5/20`; north is refused, then a turn
and retry northeast is also refused. A turn and retry northwest reaches `(2609,475)`.
It sells all eight granted tongs for 56 gold and returns to `(2610,476)`, alive, with
a successful sale frame. The outbound sequence is `N, NE, NE, NW, NW`; the return is
`SE, SE`. This supplies live evidence for both the fan and the confirmed-turn retry.
The baseline greedy walker and the first turn-unaware fan cannot produce that trace.
No shop moved during play; staging was complete and the GM disconnected first.

### Woodsman: relocation continuity confirmed

`.logs/improve-woodsman-20260913.log` finished normally at **900 ticks**, alive, with
610 gold banked and 150 in the pack, starting broke. The profile's reserve of 150
matched the actual staging readback. There were 45 achieved frames of 46: 16 log
processing jobs, 17 sales and 12 deposits. One processing job gave up at age 5; the
resulting boards were sold next and work continued. No sale gave up, and none of
`DIED`, `NO PROGRESS`, `WEDGED WALK`, `NOTHING LANDS` or `FRAME OVERDUE` fired.

| Verified workplace | Successful sales | Successful deposits |
|---|---:|---:|
| Original `(518,1042)` | 6 | 2 |
| First relocation `(523,1039)` | 7 | 7 |
| Second relocation `(513,1046)` | 4 | 3 |

The first post-hop sale finished at age 14 and the second grove's first sale at age 29,
including return legs. Readouts show `home` and the next trip's `return` adopting each
reached workplace, and the character resuming work there. Shops stayed at their original
coordinates: seller `(519,1042)`, tool vendor `(517,1042)`, banker `(518,1043)`.
That is **11 successful sales after relocation**, versus §72's zero of 38 attempts;
it confirms the recorded continuity prediction without restaging shops during play.
This is not a controlled income-rate comparison with the historical world.

A third relocation reached `(523,1045)` near the end of the budget; no subsequent
transaction began before tick 900, so this run does not score trading from that third
workplace. Arbitrary long-wall routing and fresh-character autonomy remain outside
the evidence from these fixtures.

### Close

The final full suite passes **1753 tests**, with Ruff and `git diff --check` clean.
All gameplay processes finished or were explicitly interrupted and identified above.
The disposable ServUO process was stopped after verification; the original shard and
save were not run or modified. Local logs, the profile/search outputs and the disposable
copy are retained for inspection. No model API was needed for these live checks.
