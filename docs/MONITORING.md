# Watching an agent play

`--monitor` serves a **read-only web view of each agent's own character**, on loopback:

```
$ python -m anima2.village --pipeline --monitor
  animat153595: the tinker  watch: http://127.0.0.1:8801/
  animam153595: the mage    watch: http://127.0.0.1:8802/
```

Open either URL and you see the real UO client — terrain, sprites, journal — rendered
from that agent's live session. `--monitor` works the same for `--warriors N`.

## Why it is not a second login

The obvious design — log a spectator in as the same character — cannot work. A UO shard
allows exactly **one session per character**, and ServUO enforces it by disposing the
older one (`Server/Network/PacketHandlers.cs`):

```csharp
if (m.NetState != null) { m.NetState.Dispose(); }
```

So a "monitor login" would kick the agent off its own body — the opposite of watching it.
The agent already holds the only session that character gets, so the viewer **attaches to
that session** instead of competing for it: the bridge renders frames from the session it
owns and serves them over HTTP.

Consequences worth knowing:

- **One viewer per agent.** Each agent runs its own bridge process and therefore its own
  port (`village.MONITOR_PORT_BASE`, then one per agent). There is no way to watch several
  characters through one connection, because there is no one connection.
- **The view survives a reconnect.** `ResilientIpcBody` passes the port to both of its
  spawn factories, so a restarted body comes back on the same URL.

## Read-only is structural

`PlayConfig::read_only` refuses `POST /input`, `/login` and `/character` with **403** in the
HTTP layer itself, before the request body is read — so a click can never reach the action
or login channels:

```
$ curl -X POST -d walk_north http://127.0.0.1:8801/input
read-only monitor: input is disabled          # HTTP 403
```

This matters beyond tidiness: the brain must stay the only thing driving the body, or a
live run stops being evidence of what the brain can do.

## What it costs

Rendering a frame happens on the bridge's own thread — **in the brain's critical path** —
so it is bounded twice:

- **only while watched**: no frame is built unless `/scene.json` was fetched in the last 5s,
  so an unwatched monitor costs one clock read per command;
- **at most every 250ms**, the same floor the human client uses.

The cost is *measured*, not assumed. The bridge reports it to stderr and warns past the same
30ms threshold the human client uses:

```
[anima-agent] monitor frames n=50 avg=…ms max=…ms
[anima-agent] slow monitor frame: …ms
```

If you are chasing a performance question, read those numbers before blaming (or clearing)
the monitor.

## When the game view is the wrong tool

The view shows *the world*. It does not show why an agent chose what it chose — its READY
set, its held goal, whether its worker is even still running. The village's own status line
carries that, and it is usually the faster answer:

```
artisan[tongs=5 gold=140] purse[mage_sees=0 artisan_sees=140 at=(2609,476) mage_is=1away]
mage[hp=86/90 gold=0 ash=16] artisan_ready=['craft_tongs'] artisan_goal=deliver_gold
```

A frozen agent is the case to watch for: if every counter (steps, reward, hp, position) is
identical across samples, the worker has **stopped**, and nothing read from its observation
after that point means anything — see `_run_worker`'s `DISCONNECTED` / `BUDGET SPENT`
markers, which exist because that ambiguity once cost three runs and a wrong root cause.

A frozen GOAL inside a running agent is the subtler cousin, and it cost a fourth run
(2026-08-03): the worker was fine, the character was walking, and `admitted=sell_furniture`
printed for 272 ticks with nothing executing it — the frame's owner agent had simply
stopped being ticked. `admitted=` now carries the frame's `@age/budget` in the owning
agent's own ticks, so a goal nobody is advancing is one whose age does not move while the
samples keep arriving, and three markers name the state outright: `+hold` (legitimate — the
orchestrator is finishing an owed transaction the rule stopped wanting), `!frozen` (nobody
is ticking this frame; if the character is not dead, that is a regression), `!overdue` (past
its own budget — the Life also prints a throttled `FRAME OVERDUE` line). Full legend:
`docs/WOODSMAN.md`. When that was written the orchestrator-side fix behind those markers was
proven **offline only** — `docs/AUDIT-2026-07-29.md`, 2026-08-03 §5 — so the next live run's
status line WAS the verification.

**Three live runs the same day ran it, and the verdict is split (audit §6).** LIVE-PROVEN:
the `@age/budget` clock on all 306 samples, and `+hold` on 31 of them in one 1800-tick
forge-pair run, with the frame's age advancing 1:1 and the frame retiring inside its budget;
the A/B that started this went from 30 lying lines out of 32 to **0 out of 33** on the same
command. STILL OFFLINE-ONLY at that point: `!overdue` and the throttled `FRAME OVERDUE` line
had **zero live ticks** — no frame went overdue — and `!frozen`'s clean sheet was entailed by
that rather than earned, since the telemetry can only print it when a death episode is open
or a frame is overdue-and-unrepaired.

**A fourth run the same day put the overdue state on a shard for the first time** — the
forced-state gate `anima2/live_frame_overdue_gate.py` (audit §7). A `craft_tongs` frame went
overdue at economy tick **301** against `deadline_tick=300`, `_repair_overdue_frame` closed
the craft FSM's own gump, and the hold released one tick later into `mode=hunt` with the
frame still live. So the overdue state itself is live-proven; `!frozen` on a live frame with
the character not dead is still the regression signal, and it is now a state a shard has
actually produced (as `!frozen!overdue` together, which is its legitimate form).

**Operational consequence for this doc, and it is the whole reason the gate recorded every
tick instead of sampling: do NOT read bound 3 off the `+hold`+`!overdue` pairing.** That
pairing exists for exactly ONE tick — the repair-and-extend tick — and `LifeRunner` samples
every 4.0 s (~9 ticks), so a sampling monitor misses it roughly 8 times in 9. The
sample-independent signals are the worker's own unthrottled prints: `FRAME OVERDUE` appears
on the FIRST overdue tick always (`_run_worker` prints on `_overdue % _QUIET_TICKS == 0`
starting at `_overdue == 0`), and `_repair_overdue_frame` is identified by a
`closing an unowned … ` line **immediately before** it with **no** `RULE-vs-GATE
DISAGREEMENT` line — the detector's copy of that repair always prints one alongside. Audit
§7.6 has the full reading key, including how to tell a bound-2 near-miss from a miss.

*A gap this monitoring doc should own, found by the same runs (audit §6.5, follow-up 17):*
`run_forge_pair`'s status line prints **no hp and no death flag for either agent**.
`life_runner.hp_readout` exists and returns `DEAD`, but the pair line never calls it, so
when the 1800-tick run's miner stopped producing at t=765 — reward and steps frozen, no
smelt and no deliver on the 126 samples that followed — nothing on the tape said so, and a
death, a lost tool and a dead vein remain indistinguishable. This is the second sighting of
the 2026-07-30 health check's "liveness line for NON-Life agents" item.

**That gap is closed as of 2026-08-05 — the "did it stop" half on 2026-08-03 and the
"is it alive" half now.** See the two new sections below. Note the fix did NOT land where
follow-up 18 proposed it (an `hp=` inside `run_forge_pair`'s `grimm[…]` group): that is one
group on one runner. It landed in `_run_worker`, which every runner drives, so the reading
covers the forge pair, the supply pair, the warrior village, the artisan+mage pipeline,
`run_village` and `LifeRunner.run` alike.

## Work-liveness: `eps=`, `NO OUTPUT`, `!stalled` (2026-08-03)

`NO PROGRESS` is **body**-liveness — it watches reward, steps, speech AND position, so an
agent that keeps WALKING resets it forever. That is exactly how the forge miner died in the
open, twice. In the 1800-tick run of 2026-08-03 his ten `NO PROGRESS` pulses all read the
identical string `for 40 ticks`, three of them before he stopped producing at all; the alarm
carried the same text in the healthy half and the dead half, which is zero information.

So `_run_worker` now carries a second, different alarm: **work**-liveness.

| surface | what it is |
|---|---|
| `eps=N` on the per-agent status line | every skill outcome the agent's ledgers have recorded — terminal OR rewarded. For a **Life** it is the SUM of the hunt and economy ledgers, because `Life.episodes` is the hunt one alone and a carpenter can record 0 there while retiring hundreds of capability frames. Printed for every agent, always. |
| `** <name>: NO OUTPUT for <n> ticks (eps=N unchanged since t=K, skill=<s>) — no skill has finished or paid since **` | the alarm, every 240 ticks of silence, **escalating** (240 → 480 → 720 …) so two pulses can never be confused the way ten identical `40`s were. |
| `!stalled` on the status line | printed while the stall holds, so an operator who missed the alarm scrolling past still sees it. |
| `[BUDGET SPENT · STALLED n]` | folded into the terminal suffix, because the terminal line is what a post-hoc reader looks at first and the miner's said `out+176.9 steps=139 … [BUDGET SPENT]`. |

Two things about it are worth knowing before you read one:

- **240 ticks is measured.** Sample cadence in both 2026-08-03 forge logs is 9 ticks median /
  10 max; the miner's longest *healthy* reward-silence stretch across them is **159 ticks**
  (two full relocations with every swing stuck, then live rock). 240 produces zero false
  positives on both healthy windows and is 1.51× that worst case. 160 also scores zero and
  clears it by ONE tick, which is not a margin.
- **It only speaks while the agent is running a skill that is supposed to finish or pay.** An
  agent in `wander` / `capability_wait` / `curriculum_wait` is idle BY DESIGN — the default
  village roster's `townsfolk` is defined `work_skill=None`, so wandering is its whole job and
  it records nothing forever. Ungated, this alarm fires on it at 240/480/720/960 and ends the
  run `!stalled`, i.e. 100% wrong on a perfectly healthy agent. `Agent.last_skill_name` is
  what arms it. If you see `NO OUTPUT` beside `skill=wander`, that is a bug in the gate, not
  a stalled agent.

**What it still misses:** it is orthogonal to WALKING, not to every zero-reward terminal
skill. An agent whose work is dead but which cycles death/resurrection (`RecoverDeath`) or
finishes a bandage (`Survive`) once per 240 ticks keeps `eps=` moving and stays silent. That
shape has never been observed; it is the known hole.

**Never run on a shard.** Every number above is read off the two existing forge logs or
produced offline. Audit §8.1 and §8.5.

## Death: `hp=`, `deaths=`, `DIED` / `BACK ALIVE` (2026-08-05)

The other half of follow-up 17, named three times before it was built (2026-07-30, follow-up
17, follow-up 18). The work-liveness line above says an agent STOPPED; this says whether it
DIED, which is the difference between a corpse, a lost pickaxe and a dead vein.

| surface | what it is |
|---|---|
| `hp=<n>/<max>` \| `hp=DEAD` \| `hp=?` on the per-agent status line | the LEVEL signal, via `life_runner.hp_readout` — the one definition, now the only one (`_pipeline_line` used to re-derive it inline, and that copy was the only hp on any village line). |
| `deaths=N` on the per-agent status line | the EDGE count, printed for every agent **including at 0**. |
| `** <name>: DIED at (x,y) — death #N **` | one line per death, unthrottled — an EDGE, like `FRAME RETIRED` and unlike `FRAME OVERDUE`'s level signal. |
| `** <name>: BACK ALIVE at (x,y) after <n> ticks dead (death #N) **` | the recovery and how long it took. A death resolved in 30 ticks and one the agent never returns from are the same number in a death COUNT. |
| `** <name>: DEAD at first observation @(x,y) — counted as death #1, though it happened before this worker's first tick **` | a run that opens on a corpse. Counted, so the run does not read as death-free; named apart, because this worker did not watch it happen. |

**Why both, and not just `hp=`.** The level decays. Run the same frozen-miner shape twice,
once with two deaths in it, and the ONLY difference in the entire tape is one field — the
two runs below are real output from this tree, identical in `out+`, `eps=`, `steps=`,
`!stalled` and `hp=`:

```
Grimm     miner      @(395,50) t=345 hp=80/80 deaths=0 out+0.0 eps=11 steps=345 says=0 !stalled  [BUDGET SPENT · STALLED 246]
Grimm     miner      @(395,50) t=345 hp=80/80 deaths=2 out+0.0 eps=11 steps=345 says=0 !stalled  [BUDGET SPENT · STALLED 246]
```

That shape is not hypothetical — it is the exact hole the section above names as its own:
an agent cycling death/resurrection keeps `eps=` moving through `RecoverDeath`'s terminal
statuses and stays silent under the work-liveness alarm. `deaths=` is what closes it.

**Why the count is not read off `Agent.memory["death_episode"]`.** That marker already
exists and `Agent.tick` maintains it — but it is per-AGENT and a Life owns two, each with
its own `death_observed_dead` flag, exactly one ticked per orchestrator tick. Measured on a
real `CarpenterLife` over `MockBody`, not argued: for ONE death seen first by the economy
agent and then by the hunt agent under the death override, `hunt + econ` reports **2**; for
TWO deaths seen by one agent each, `max(hunt, econ)` reports **1**. A sum double-counts, a
max under-counts. One body has one death, and the worker watching that body counts it once.
Both reductions are pinned as failing mutants in `tests/test_forge_relocation.py`.

**Never run on a shard.** Offline only. What a live run would add: whether a real death is
observed at all on the pair runners (no forge log has ever contained one), and whether the
ghost stretch is short enough that `hp=DEAD` ever appears on a ~4s sample — it may not, which
is the whole reason `deaths=` is read per tick.

## Frame retirements: `retired=` and `FRAME RETIRED` (2026-08-03)

`want=` / `admitted=` / `ready=` all describe the frame that is HERE. A frame that has already
gone is simply *absent* — which is why bound 1 of the exit-edge hold (the FSM's own give-up
ladder) could not be told from an ordinary successful sale on **any** log: both leave the same
hole, and a low frame age is not a give-up signature (audit §6.3). Two surfaces close that:

- **`** <name>: FRAME RETIRED <capability>#<id> age=<a>/<budget> -> <reason> **`**, printed by
  `_run_worker` **every tick, unthrottled**, because a retirement is an EDGE — one per
  transaction — and the ~4s status sampling misses edges. `giveup` and `expired` carry their
  bound in the line: `-> giveup (bound 1: the FSM's give-up ladder)`,
  `-> expired (bound 2: the frame's own deadline)`. `achieved` is unannotated; it is the
  ordinary outcome and glossing it would bury the two that are not.
- **`retired=6:4a/1g/1x`** on the Life status line — the LEVEL signal for an operator joining
  late or grepping afterwards. `a` achieved, `g` giveup, `x` expired (then `r`/`c`).

Two properties that make them trustworthy, and one that limits them:

- The reason is read off **`frame.outcome` alone**, which `GoalStack._archive` has stamped on
  every retirement since the goal stack was written. Nothing new is recorded to produce it, so
  the answer does not depend on WHEN it is read. That matters concretely: an earlier design
  tested a marker in agent memory, and because that marker is a single slot every later
  transaction overwrites, 116 of 117 give-ups flipped to "no ladder ran" when the same history
  was re-read at the end of the run — the one error direction that erases bound-1 evidence.
- Reading durable per-frame history is also what lets a retirement that lands *between* two
  4-second samples still be reported.
- **The tally is a bounded window, and it says so.** Goal-stack history holds 128 frames; past
  that the oldest are deleted. Measured offline, a carpenter retires 58 / 117 / 176 / 234
  frames at 1000 / 2000 / 3000 / 4000 ticks while history saturates from tick ~2182 — so the
  field switches to `retired>=128:…` once the cap binds rather than reporting 128 of 176 as if
  it were a total. The per-tick alarm remains the exact per-frame record.

**`FRAME RETIRED … -> giveup` has never printed on a shard.** Bound 1 is OBSERVABLE now; it is
not exercised. Audit §8.3.

## The walk's own target: `trip=` (2026-08-13, follow-up 32)

Every field above describes the *frame*. None describes the **walk inside it** — and that is
where the flagship chain's worst recorded day was lost. On 2026-08-11 a tinker retired **203
`sell_tongs` frames, every one at age 8**, banked 0 gold and ended holding 5 unsold tongs
(audit §30.2). `mkt_phase=sell` showed on 134 samples with `sell_stage` never written once, so
each trip died *before its first stage* — inside `_walk_route` (§31). Every frame field read
healthy throughout: admitted, ready, unfrozen, age 8 of a 180-tick budget.

Follow-up 32 asked for `pos=`. **Its premise was wrong**: `@(x,y)` has been on the per-agent
line since **2026-06-30** (`6f279a7`), six weeks before that day, and was on every sample of
it. A position with nothing to compare it against is not a diagnosis. What was missing is the
coordinate's counterpart.

| surface | what it is |
|---|---|
| `trip=<mkt_phase>` | which market walk is in progress — `sell`, `bank`, `buy`, `toolbuy` and their `_return` legs. `trip=craft` is the idle phase between trips; `trip=none` is an agent with no market state at all; `trip=?` is a readout that failed. **There is no state that renders blank** — the `deaths=` rule, one field to the left. |
| `to=(x,y)` \| `to=(x,y)+N` | the tile the walk is stepping toward — `route[leg]`, **not** `route[-1]`. `+N` is how many waypoints remain after it. The distinction is live: `profession.VENDOR_SPOT` is a two-leg route through a walled corridor, while `life_runner.stage_shops` produces single-waypoint ones. |
| `d=4>2` \| `d=1<=2` | chebyshev distance to `to=`, against the reach that leg needs — `final_reach` on the last leg, **0** on intermediate ones. The comparator is printed so no reader has to subtract (the `!overdue` rule). `grep 'd=[0-9]*>'` selects every not-yet-arrived sample of a run. |
| `stall=3/6` \| `stall=-` | `_market_walk_toward`'s no-progress counter over its give-up limit. `-` is **not** "arrived" and not zero: the counter is written on every greedy step and popped only on a leg advance or the give-up itself, so an ordinary arrival leaves `stall=0/6` behind. |

What the 203-give-up day would have printed, reproduced offline by driving a real
`CarpenterLife` into a walled approach — the live arithmetic exactly, `(n, 'sell_furniture', 8,
180, 'giveup')` repeating:

```
Sten      carpenter  @(5,5) t=11 hp=80/80 deaths=0 trip=sell to=(10,10) d=5>2 stall=5/6 ...
```

Five tiles from a vendor he must be two from, not moving, five ticks into a six-tick give-up.

Three properties, and one thing this does **not** do:

- **Nothing new is written to make it readable.** Every value is a read-time projection of
  state that already exists for a non-telemetry reason (`mkt_phase`, `cap_{ns}_route`,
  `bs_stand`, `{tag}_leg`, `{tag}_stall`). A key written on only some paths is worse than no
  key — the `*_leg` lesson — and the way never to have that problem is to add no key.
- **It mirrors `_walk_route` rather than re-deriving it**: final-waypoint reach tested first,
  then `route[leg]`, with the same per-leg reach rule. A readout that computes arrival its own
  way will eventually disagree with the walk it reports on.
- **It is on `_run_worker`'s line, so every runner and every agent gets it.** Mounting it on
  `life_runner.telemetry_line` was the first attempt and could not work: that requires a Life,
  and `run_village`'s trade blacksmith — the only production agent carrying the multi-waypoint
  route — is a plain `Agent`. Review-caught.
- **It cannot attribute the live failure.** It shows a blocked approach *produces* the age-8
  signature and that the line now renders it. Which cause the live day had still needs a forge
  day (follow-up 30). The prediction is written down in audit §36.5 *before* that run.

## Implementation

| Piece | Where |
|---|---|
| `read_only` flag, 403 enforcement | `anima-client` `crates/anima-net/src/play_server.rs` |
| `PlayServer::into_monitor()` → `Monitor` | same file — keeps the serving half, drops login |
| `Monitor::publish` / `watching` / `build_stats` | same file |
| `ANIMA_MONITOR_PORT`, publish per command | `crates/anima-net/src/bin/agent.rs` |
| `monitor_port=` passthrough | `anima2/ipc_body.py` |
| `--monitor`, port allocation | `anima2/village.py` (`_monitor_ports`) |

Two subtleties are load-bearing:

- **stdout is a protocol.** The bridge speaks NDJSON on stdout, so every `println!` in
  `play_server.rs` is an `eprintln!`. A single stray stdout line makes the brain drop the
  body with "bridge emitted malformed NDJSON" — which is exactly what happened the first
  time this was wired up.
- **the monitor keeps its own journal cursor.** `Session::observation()` advances the
  session's single shared cursor; rendering a frame for a spectator must never consume
  journal lines the brain has not seen yet.
