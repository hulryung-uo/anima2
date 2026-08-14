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
python -m anima2.village --forge-pair --ticks 1800 --monitor --narrate \
    > ~/anima-logs/forge-$(date +%Y%m%d-%H%M).log 2>&1
```

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

---

## Entries

_(none yet — this file was created 2026-08-14, with `--narrate`.)_
