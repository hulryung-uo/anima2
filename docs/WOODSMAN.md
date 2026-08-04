# Bjorn — a lumberjack's life

The third profession to live autonomously (after the swordsman and the mage), and the
first that does not fight for a living.

```
python -m anima2.village --woodsman [--monitor]
```

## Why this profession was worth building

Not for variety. The first two lives stop working for reasons that are structurally the
same — something they carry runs out or is lost, and the fix is one purchase. A woodsman
differs in two ways that exercise parts of the system the others never touched.

**Its chain is longer.** A warrior turns a fight straight into gold. A woodsman has to
carry its value through `tree -> log -> board -> gold`, so the rule has to prefer the
link that moves material *furthest along* rather than the first one that is ready:

```
axe (lost or broken)  >  sell boards  >  process logs  >  bank  >  chop
```

Selling above processing is the ordering lesson the artisan's chain-priority client had
to learn live: an agent that always takes the first ready step keeps making more of what
it already has and never finishes anything.

**Its tool is consumable.** A blade is lost to a specific event; a reagent pouch empties
predictably; an axe just breaks mid-swing. The rule prefers an axe lying on the ground
(free — and the shape a tinker's `deliver_hatchet` produces) over buying one, and a
woodsman that can do neither keeps chopping rather than stalling at a shop it cannot use.

What is *not* handled here is the other half of that difference: the world itself runs
out. A grove thins until swinging at it earns nothing, and that is left to `Harvest`'s
own windowed stuck-rate relocation, not to this rule.

## What the first day cost — two defects, both invisible from outside

Bjorn chopped 20 logs and then did nothing for an entire run, twice, with the
orchestrator selecting `process_logs` correctly every tick.

### 1. Three skills reached for somebody else's tool

`ProcessLogs._axe`, `Harvest._tool` and `Blacksmith._tool` scanned every item in view by
graphic with no owner filter — and the staged Weaponsmith standing one tile away *wears
and sells axes*. `Use` on a stranger's tool does nothing.

`harvest.owned_tool` now backs all three, written as a **refusal** rather than an
allow-list: reject a tool held by another mobile or inside another mobile's backpack,
allow everything else. The first attempt was an allow-list (ours / worn / loose) and it
mis-rejected tools whose owner could not be confirmed — e.g. with our own backpack out of
view — breaking 16 tests that described legitimate behaviour.

The knowledge was already in the file: `Harvest._backpack`, immediately above the finder
that had the bug, carries a long docstring about why picking items by shape rather than
owner returns a neighbour's. It was never applied to the tool finders beside it.

### 2. A worn tool did not count as a tool

This was the one that actually stalled the run, and it had no outward signature at all
until the telemetry to see it was built:

```
want=process_logs  admitted=None  ready=[]  [axe_in_pack=False worn=True]
```

A lumberjack, of course, **wields its axe**. Every part was individually right:

| component | verdict on a worn axe |
|---|---|
| `ProcessLogs._axe` (the skill that swings it) | I have an axe |
| the rule that decides — `WoodsmanLife._has_axe` then, `obsview.owns` now | so process the logs |
| ServUO `BaseAxe.OnDoubleClick` | reach + accessibility, no backpack required |
| **`capabilities._owned_tool` (the gate)** | **no tool** |

**Pointer update (2026-08-02):** `WoodsmanLife._has_axe` no longer exists. It was one of
twenty hand-copied Observation readbacks across the five Life modules, and it is
`anima2/obsview.py::owns(obs, AXE_GRAPHICS)` now — one definition, written to mirror
`capabilities._owned_tool` clause-for-clause so this table's bottom two rows can never
disagree again. The worn-tool widening this page paid for moved into `owns`' docstring
with it, and the merge also fixed a defect `_has_axe` had carried since it was written:
no `bp is not None` guard, so with our own pack out of the observation an axe lying on the
GROUND read as owned (`docs/AUDIT-2026-07-29.md`, 2026-08-02).

So no goal was ever admitted, and the capability leaf sat returning `RUNNING` with
nothing to do — which from outside is indistinguishable from working.

The direction of the fix was **measured, not chosen**: a raw conversion probe with the
axe packed converts 20 logs in two ticks, and a second probe with the axe *equipped*
(`container` = the player, layer 2) does exactly the same. The gate was the odd one out,
so it was widened. Its old behaviour had a quieter cost too — a worn tool read as no
tool, so a `buy_tool` trigger could spend gold on one the agent was already holding.

The regression pins the **agreement** between gate and skill, not either side's answer:
the defect was a disagreement, so that is the property that must not drift again.

## Telemetry that earned its place

`ready=[]` says the gate refused. It does not say *which condition* did, and the
candidates differ in kind — a tool the gate cannot see, a cursor another skill left open,
a market phase never cleared. So the runner prints all three layers separately:

```
want=<what the orchestrator wants>  admitted=<the goal actually admitted>@<age>/<budget><markers>  ready=<what the gate allows>  retired=<n>:<mix>
[axe_in_pack=? worn=? cursor=? mkt=?]
```

`want` alone is a trap: it is *intent*, and an unadmitted goal looks identical to a busy
one. The `axe=` readout is owner-filtered for the same reason — reporting "yes" for the
Weaponsmith's axe is exactly how that display would have hidden the first bug.

`admitted=` had the same trap on its own side, and it cost a run: a frame is on the stack
whether or not anybody is ticking it. So it carries the frame's `@age/budget` — both in
ECON-AGENT ticks, the clock the deadline is counted in and the one that STOPS when the
frame stops being ticked — plus up to two markers:

| marker | meaning |
| --- | --- |
| `+hold` | the rule stopped wanting this capability and the orchestrator is finishing it anyway (`WarriorLife.tick`'s exit-edge hold). Legitimate; it is why `want=None admitted=X` is now a normal pairing. |
| `!frozen` | a live frame whose agent is NOT the one being ticked — a death episode, or a frame the hold has released. Its `@age` stops moving while the lines keep printing. |
| `!overdue` | the frame is past its own budget. Printed alongside either of the above, because "age > budget" is a comparison nobody makes by eye. The Life prints `FRAME OVERDUE` too, throttled. |

An `admitted=` with no `@` means no frame is on the stack at all (`admitted=None`).

**`retired=` (added 2026-08-03) is the fourth layer, and it exists because the first three all
describe the frame that is HERE.** A frame that has already gone is simply absent from them —
which is exactly why bound 1 of the exit-edge hold, the FSM's own give-up ladder, was
indistinguishable from an ordinary successful sale on every live log
(`docs/AUDIT-2026-07-29.md` §6.3: a low frame age is NOT a give-up signature; a ladderless
`buy_iron` frame closed at age 4 just like a completed sale).

| field | meaning |
| --- | --- |
| `retired=0` | nothing has retired yet on this Life's economy goal stack. |
| `retired=6:4a/1g/1x` | six capability frames have retired: 4 `achieved`, 1 `giveup` (bound 1), 1 `expired` (bound 2). Order is fixed (`a`, `g`, `x`, then `r`/`c`), never alphabetical, so the mix always reads achieved-first. |
| `retired>=128:…` | goal-stack history is BOUNDED at 128 frames and has started deleting its oldest. The count is a floor from here on; the per-tick alarm below is the exact record. |

Alongside it, `_run_worker` prints one **unthrottled** line per retirement, because a
retirement is an edge and the 4-second sampling misses edges:

```
  ** Sten: FRAME RETIRED sell_furniture#1 age=17/180 -> giveup (bound 1: the FSM's give-up ladder) **
```

The reason comes from `frame.outcome` — stamped by `GoalStack._archive` since the goal stack
was written — so it is a projection of durable state and gives the same answer whenever it is
read. That is not a nicety: the design this replaced consulted a single marker slot in agent
memory that every later transaction overwrites, and 116 of 117 give-ups read as "no ladder
ran" when the same history was re-read later. **Bound 1 is now OBSERVABLE. It has still never
been exercised on a shard** — audit §8.3.

Two more fields ride the *worker's* own line (the `Name job @(x,y) t=… out+… eps=… steps=…`
one, not the telemetry line above): `eps=` is every skill outcome the agent's ledgers have
recorded — for a Life the SUM of hunt and economy, since the hunt ledger alone can sit at 0
for thousands of ticks — and `!stalled` / `· STALLED n` mark a work-liveness stall. Legend:
`docs/MONITORING.md`.

*Provenance, because it changes how to read these on the next run:* the run that made
`admitted=` lie was live (2026-08-03); when this legend was written the orchestrator fix
that makes `+hold` a legitimate state, and these markers themselves, were proven **OFFLINE
ONLY** — no shard had run them (`docs/AUDIT-2026-07-29.md`, 2026-08-03 §5).

**Three live runs the same day split that in two (`docs/AUDIT-2026-07-29.md` §6), and the
split is per-marker:**

| marker | status after 2026-08-03 |
| --- | --- |
| `@age/budget` | **LIVE-PROVEN.** On all 306 samples across the three runs, and the ages advance 1:1 with the owning agent's ticks and RESET across frames. |
| `+hold` | **LIVE-PROVEN.** 31 samples of it in one 1800-tick forge-pair run — the mechanism directly observed, its frame's clock moving the whole way and the frame retiring inside its budget. |
| `!overdue` / `FRAME OVERDUE` | **OFFLINE-ONLY on these three runs — zero live ticks.** No frame went overdue on any of them. *(Superseded the same day by a fourth run: see the row below the table.)* |
| `!frozen` | **OFFLINE-ONLY on these three runs, and its live zero proved nothing.** It is only PRINTABLE when a death episode is open or a frame is overdue-and-unrepaired; neither happened, so 0-of-306 is entailed by the row above, not independent evidence. |

**Updated the same day by a fourth run — the forced-state gate
`anima2/live_frame_overdue_gate.py` (audit §7):**

| marker | status after the bound-3 gate |
| --- | --- |
| `!overdue` / `FRAME OVERDUE` | **LIVE-PROVEN.** A `craft_tongs` frame went overdue at economy tick 301 against `deadline_tick=300`, `_repair_overdue_frame` closed the craft FSM's own gump, and the hold released one tick later. |
| `!frozen` | **LIVE-PROVEN in its LEGITIMATE form only** — as `!frozen!overdue` on the frame the hold had just released. Still NOT seen during a death episode; nobody has died mid-transaction on a shard yet. |

Reading these on a MONITORED run is not the same as reading them here. **The `+hold`+`!overdue`
pairing lasts exactly ONE tick** — the repair-and-extend tick — and `LifeRunner` samples every
4.0 s (~9 ticks), so it is missed ~8 times in 9; the gate above caught it only because it
recorded every tick. Use `FRAME OVERDUE` (always printed on the first overdue tick) and the
`closing an unowned …` line immediately before it instead. Audit §7.6.

So `!frozen` on a live frame while the character is not dead **and without `!overdue` beside
it**, or a `+hold` whose `@age` stops climbing, is still the first thing to look for rather
than a curiosity — that combination is the hold being defeated, and nothing has yet shown it
on a shard.

**Bound 1, after 2026-08-03 §8: OBSERVABLE, NOT EXERCISED — and the two words are kept apart
deliberately.** `retired=` and `FRAME RETIRED … -> giveup` give the give-up ladder the
signature it never had, so a future run can recognise it. No run has produced one. The count
of live-proven bounds is unchanged: bound 2 and bound 3, not bound 1. Proving it still needs
a transaction that FAILS on a shard — a sale the vendor refuses, a bank trip that cannot
reach its banker — and the cheapest path to one is naming a run-finished marker on the buy
families' give-up branch (audit follow-up 19).

## Live result

With both fixed, the chain turns unattended and repeats:

```
admitted=process_logs  ready=['process_logs']         logs 20 -> 0, boards 0 -> 20
admitted=sell_boards                                  boards 20 -> 0
admitted=None          ready=['sell_boards','bank_gold']
gold: 0 -> 40 -> 80 ...
```

Gold is deleted at staging, so every coin is one Bjorn earned selling boards.

Staging follows the village's own hard rule: the three shops (Carpenter for boards,
Weaponsmith for axes, Banker) are placed inside the market skill's reach of the grove
stand — so the trip short-circuits `_walk_route`'s final-reach check and needs no greedy
crossing — and then **read back** from the server with their real distance printed, since
an `[Add`-ed NPC settles a tile or two off the request.
