# Observations — what a human saw the agent do, and why it looked wrong

This file is the INPUT side of a loop the project did not have. Everything else in `docs/`
is written after a diagnosis; this is written before one, by someone watching.

## Why it exists

Every instrument this project has measures something it already suspected. `trip=` was
built after a wedge, `landed=` after a thrash, the mining cause split after a freeze. That
works, and it is slow: §41's diagnosis needed eight agents, a port of ServUO's
line-of-sight algorithm, and a day's tape diffed segment by segment — to establish that a
miner was standing at the foot of a cliff swinging at ore above his own eye.

**A person watching the character would have said "why is he hitting a wall?" in ten
seconds.** That judgement is cheap, it is available before anyone knows what to measure,
and until now there was nowhere to put it.

## How to produce the raw material

```
./scripts/run_forge_pair.sh
# equivalent core:
# PYTHONUNBUFFERED=1 uv run python -m anima2.village --forge-pair --ticks 1800 --monitor --narrate
```

Logs land under `.logs/forge-TIMESTAMP.log` (and `~/anima-logs/` when writable).
Watch without Chrome: Safari on the printed `watch:` URLs, or
`uv run python -m anima2.monitor_watch` (writes `.logs/monitor-*.log`).
Do not open `anima-desktop` on the same characters — that kicks the agent.

- `--monitor` serves the read-only client view per agent (`http://127.0.0.1:8801/`), so the
  character can be watched playing.
- `--narrate` makes it say what it is doing and **why**: a short clause spoken in-game (so
  it appears in the journal in that same view) and a `~~` line in the log carrying the
  tick, the tile, and the evidence — the threshold that fired, the tile being walked to,
  the distance still to cover, the age of the frame.

Narration costs no agent tick: speech only rides a tick the agent already spent idle, and
the line is throttled to intent CHANGES. See `anima2/narrate.py`.

**The log is the durable half.** Watching live is optional — `grep '~~' <tape>` reconstructs
what the agent believed at every transition afterwards, which is the thing §41 had to infer.

## How to write an observation

One entry per thing that looked wrong. The valuable part is the FIRST sentence — what you
saw — not a theory about the cause. A wrong theory attached to a real observation is still
a real observation; a theory with no observation is a guess.

```markdown
### YYYY-MM-DD  <one line: what looked wrong>
- **Saw:** what the character did, in plain words.
- **It said:** the `~~` line or in-game clause at that moment, if there was one.
- **Where:** tape path + tick, or coordinates.
- **Why it looked wrong:** what you expected instead.
```

`Saw` and `It said` are separate on purpose: the gap between them is the most useful signal
this file can carry. An agent doing the wrong thing for a reason it states clearly is a
*rule* problem; an agent doing something its own narration does not explain is a *code*
problem; and an agent whose narration is simply wrong about itself is a *narrator* problem,
which is the one that would quietly poison everything else here.

## How an entry becomes work

Triage, in this order, because it is the order of increasing cost:

1. **Reproducible offline** → a fixture and a test, then a fix. Cheapest and strongest.
2. **Needs measurement** → an instrument plus a prediction written down BEFORE the next
   run (§34.4, §41.5 are the pattern). The prediction must name what would REFUTE it.
3. **Neither yet** → a numbered follow-up in `docs/AUDIT-2026-07-29.md`, so it is not lost.

An entry is only closed by evidence, and "I changed something and it feels better" is not
evidence — §25 diagnosed the mine pool that way and §26 had to retract it.

How a chosen entry is built and closed — fixture, mutants, single source, what
the fast loop may not do, what a live run is for — is
[`IMPLEMENTATION.md`](IMPLEMENTATION.md).

---

## Entries

### 2026-08-18  Bjorn chopped 20 logs then stood still with the axe cursor open
- **Saw:** A freshly staged lumberjack at Yew `(518, 1042)` filled the pack
  with 20 logs in ~18 ticks, then did not move, convert, or sell for the
  rest of a 600-tick day.
- **It said:** `want=process_logs admitted=None ready=[] axe=yes logs=20
  boards=0` from t=18 to t=600; `RULE-vs-GATE DISAGREEMENT` 585 ticks;
  `NO PROGRESS for 440 ticks`. Monitor `target {active: 1, kind: 1}`.
- **Where:** `.logs/woodsman-20260818-1926.log`. Account `animawood48787`.
- **Why it looked wrong:** the rule wanted the conversion the skill already
  knows how to finish (`Use(axe)` leftover → `TargetObject(log)`). The
  gate treated that harvest cursor as idle-UI dirt.
- **Later the same day (verify tape):** `.logs/woodsman-20260818-1941.log`
  — `admitted=process_logs@1/180 cursor=yes`; conversions age=3; sells
  age=5; banked 50g above the 150 reserve. §49 live-closed.

### 2026-08-18  The Yew grove ran dry and Bjorn stood on it for 429 ticks
- **Saw:** After a working chop→process→sell→bank loop, Bjorn stayed at
  `(518, 1042)` with `logs=0 boards=10` from ~t=173 to t=600, still
  swinging (`cursor` toggling yes/no), producing nothing.
- **It said:** `NO PROGRESS` 360/400/440; end line `[BUDGET SPENT · STALLED 429]`.
  Five trees in reach; 138 groves surveyed near `YEW_FOREST`.
- **Where:** `.logs/woodsman-20260818-1941.log`. Account `animawood49689`.
- **Why it looked wrong:** lumber banks are 4x3 / 20-45 logs / 20-30 min
  respawn (`Lumberjacking.cs`). Cycling `harvest_idx` on 500493 never
  hops, `Chop.no_resource_clilocs` was empty so the relocate window never
  saw the dry grove, and the runner never seeded `harvest_spot_pool`.
- **Later (verify tape):** `.logs/woodsman-20260822-1225.log` — `pool 12`,
  `win=` climbed, `reloc=(517, 1093)`, `pool=` 12→11. Logs did not resume.
  §51 live-closed as hop PASS / resume FAIL.

### 2026-08-22  The hop landed and Bjorn chopped nothing for 270 ticks
- **Saw:** After a productive morning (+280g, 18/18 frames), Bjorn walked
  from Yew `(518, 1042)` to `(517, 1093)` and stood there swinging until
  BUDGET SPENT, `logs=0 boards=10`.
- **It said:** `reloc=(517, 1093)` t=243–317; arrived t=326; then no `win=`;
  `NO PROGRESS` 40…240; `[BUDGET SPENT · STALLED 366]`. `pool=11`.
- **Where:** `.logs/woodsman-20260822-1225.log`. Account `animawood369135`.
- **Why it looked wrong:** the hop the 1941 tape asked for fired. The new
  stand did not yield, and Chop's window does not sample 500446, so too-far
  versus a mute tile cannot be told apart. Next tape needs `tree=`/`d=`.

### 2026-08-18  Late-day sell returns walked to an early craft tile and gave up at age 11
- **Saw:** After a productive morning of sells from `@(2611,473)`, Pim settled
  at craft_spot `@(2609,474)`. Every late `sell_return` still targeted
  `(2611,473)` at `d=2>0`, and sell/bank frames gave up at age 11/14 with
  the gold already taken.
- **It said:** `trip=sell_return to=(2611,473) d=2>0 stall=5/6` (20 samples);
  `FRAME RETIRED sell_tongs#… age=11/180 -> giveup` ×15; bank age-14 ×5;
  day ended `want=fetch_iron admitted=sell_tongs@9/180` on that wedge.
- **Where:** `~/anima-logs/forge-20260818-0039.log`, Pim t≈1241–1800.
- **Why it looked wrong:** the sale had already worked. The hold was
  finishing a return to a frozen `bs_stand` from the first craft tile of
  the day (`setdefault`), not a failed vendor trip. §34.6 already refused
  anchoring home to `craft_spot` alone.
- **Later the same day (verify tape):** `.logs/forge-20260818-0100.log` —
  banked 1553g; **0** stale `sell_return`; **0** age-11 sell giveups;
  49× sell `age=5 -> achieved`. §48 live-closed.

### 2026-08-18  A finished craft sat on the iron the miner had already delivered
- **Saw:** Pim standing at the forge with 3 tongs, pack iron 0, and a pile of
  ingots at his feet, for the better part of a craft budget, twice.
- **It said:** `want=fetch_iron` `admitted=craft_tongs@26/300` …
  `@296/300` `ready=['fetch_iron']` `pim_sees=12` then 22, then
  `FRAME RETIRED craft_tongs#4 age=300/300 -> expired`. Fetch landed in 8
  ticks (`iron=20`). Same shape on `craft_tongs#34`.
- **Where:** `~/anima-logs/forge-20260818-0003.log`, Pim t≈105–402 and
  t≈1057–1063. Coordinates `(2611, 473)`.
- **Why it looked wrong:** the rule already wanted fetch and the gate was
  ready. The hold was finishing a craft that had already finished. §6.4
  waited on an empty drop; this time the delivery was there.
- **Later the same day (verify tape):** `forge-20260818-0039` —
  `craft_tongs#17 age=22/300 -> giveup`, `fetch_iron#18 age=4/180 ->
  achieved`; 0× craft `300/300 expired`. Follow-up 15 craft live-closed.

### 2026-08-24  Bram stood on his spawn tile for 203 samples and died with 95 bandages left
- **Saw:** A freshly staged swordsman never left `(2587, 408)`. Two Ettins on
  adjacent tiles beat him from 150 HP to dead over 214 ticks while he wrapped
  bandages that healed nothing.
- **It said:** `skill=survive` throughout; `plate=6/6` `blade=0x13FFr5`
  `bandages=95`. No alarm fired — he was neither stalled nor idle.
- **Where:** `~/anima-logs/warrior-20260824-0236-slip.log`, t=18–214.
- **Why it looked wrong:** he was *trying* to run. `Survive` emitted `Walk`
  every tick. The away-vector cancelled between the east and west Ettins and
  the code committed NORTH — which is a third spawn tile. Audit §54.

### 2026-08-24  Bram killed two Ettins, then was chased down by prey that cannot walk
- **Saw:** A good opening — two kills, 372 gold looted — then a slow death at
  `(2585, 411)` with creatures crowding him however far he retreated.
- **It said:** `foes=` oscillating `d3,d3,d3 -> d1,d3,d3 -> d2,d3,d3` while the
  position line never changed, and once `foes=d0,...` — a hostile on his own
  tile. `kills=2 out+322.0`.
- **Where:** `~/anima-logs/warrior-20260824-0309-foes.log`, t≈300–409.
- **Why it looked wrong:** `run_warrior_village` pins every prey it stages
  (`[Set CantWalk true`). Pinned creatures cannot close distance, so oscillating
  distances around a stationary warrior are impossible — unless the pin never
  took. It sent the command and never read it back. Audit §55.
