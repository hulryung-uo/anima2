# How to implement anima2

This is the method, not a backlog. A change that skips these steps is how
this project has already shipped a wrong fix and had to retract it
(`docs/AUDIT-2026-07-29.md` §25 → §26).

Read [`DESIGN.md`](DESIGN.md) for *what this is* and *why*. Read
[`OBSERVATIONS.md`](OBSERVATIONS.md) for the input side of a new finding.
This file is only *how a change is chosen, built, and closed*.

Last updated: 2026-08-22.

---

## 1. What to work on

Triage in this order, because it is the order of increasing cost
(`OBSERVATIONS.md`):

1. **Reproducible offline** — a fixture and a test, then a fix.
2. **Needs measurement** — an instrument plus a prediction written down
   *before* the next live run. The prediction must name what would refute it.
3. **Neither yet** — a numbered follow-up in
   [`AUDIT-2026-07-29.md`](AUDIT-2026-07-29.md), so it is not lost.

Do **not** pick work from a stale "Next:" pointer, a larger evolution
budget, or a feeling that the agent "looks better." AUTONOMY-ROADMAP §E
still holds: re-run evolution versus random only when every searched axis
changes a meaningful live trajectory. That rerun is waiting on a searcher
that can steer a Life, which is a design question, not a wiring one.

Do **not** burn a multi-hour live GM budget to discover a defect that a
hand-built observation can name.

### Current queue (2026-08-22)

Live-scored:

| Item | Score | Tape |
|---|---|---|
| Follow-up 42 / §43.4 apron | PASS | `forge-20260818-0003` |
| Follow-up 40 / §44.3 `silent=` | Instrument PASS | same |
| Follow-up 41 / §45.2 `map=` | PASS | same |
| Follow-up 15 (craft) live | **PASS** | `forge-20260818-0039` |
| Stale `bs_stand` (§48) live | **PASS** | `.logs/forge-20260818-0100` |
| Chop cursor vs process_logs (§49) live | **PASS** | `.logs/woodsman-20260818-1941` |
| Dry Yew grove hop (§51) live | **PASS hop / FAIL resume** | `.logs/woodsman-20260822-1225` |

Closed offline, live-unverified:

| Item | What | Where |
|---|---|---|
| DeliverBoards sibling of 42 | Return stall / exhausted WalkTo is not arrival | `skills/woodwork.py`, audit §46 |
| Follow-up 15 remainder | fetch / process / deliver write `cap_run_finished_goal_id` | `skills/carpentry.py`, `skills/woodwork.py` |
| Silent second grove (§51.3) | After a hop, `win=` vanishes and logs stay 0 — `tree=`/`d=` splits stale nodes from a mute stand | `village.py::harvest_aim_readout` |

Still open, do not start without a fixture or a written prediction:

- Genome axes → Life knobs — design, not a splat. `profession` is identity
  and the allowlist refuses it.
- Foundry eval measuring a Life — `foundry/eval.py::_build_agent` still
  builds a bare `Agent`.
- Retreat / rest timing as knobs — the capability manifest forbids
  per-instance survival state on purpose.

Live watch: `./scripts/run_forge_pair.sh` (Safari + `.logs/`) or
`uv run python -m anima2.monitor_watch` — not Chrome, not `anima-desktop`
(`docs/MONITORING.md`).

---

## 2. How a change is built

### 2.1 Start from a named fact

The first sentence of the work is an observation or an audit follow-up,
not a theory. "The miner resumes `ph=mine` at `(2609, 475)`" is a fact.
"Greedy walk is bad" is a theory. The fixture uses the live coordinates
when they exist; a sketch of the same shape is weaker.

### 2.2 Write the test that fails on the old code

One test per mutant you care about. A mutant is a one-line regression of
the fix (treat stall as arrival; `near_enough = 2`; deliver mid-relocate).
If the test also passes on the old code, it is not pinning the defect.

Do not weaken a pinned assertion to make a new change green. If a pool
size or a stand list moves, re-derive it and say so — that is how
`max_rise` was found (`AUDIT` §41.4).

### 2.3 Change one lever

The audit names the lever when it names the follow-up. Follow-up 42's
lever was "arriving at a stand before resuming," not "which nodes are
installed." A second, plausible fix that measures to zero is recorded
and left undone (Fix 2 in §41.6).

Keep Chop / woodsman paths off a miner-only lever. `nodes_are_reprobeable`
exists because dropping a grove is the end of that trade (§35.2).

### 2.4 Single source for a fact

A threshold, a facet, a graphic, a stand coordinate — one reader. If two
names must exist, one is derived from the other and a test asserts
identity. `obsview.py` and `knobs.py` are the standing examples.
`uomap.play_map` is the facet reader: `LUMBER_MAP` is a fallback, not the
authority.

### 2.5 What the fast loop may not do

DESIGN.md §2, unchanged: no LLM, no file I/O, no packet parsing inside
`Skill.step()`. New persisted state follows `data/*.jsonl` (lazy,
gitignored, corrupt-line-tolerant, lock-guarded). New observation fields
are a contract bump, not a convenience.

### 2.6 Instrument only what the next run must adjudicate

A new counter that nothing can refute is noise. `silent=` exists because
§44 predicts a stranded no-verdict stand will climb it to the relocate
limit; if a future tape shows `win=None` and `silent=0` on that stand,
the watchdog is looking at the wrong ticks.

---

## 3. How a change is closed

Offline close (required):

```
uv run pytest -q
uv run ruff check .
```

Record the close in `AUDIT-2026-07-29.md` the same day, in the voice of
that file: mechanism, what changed, mutants, **what this does NOT
settle**. "I changed something and it feels better" is not a close.

Live close (only when a shard is the right next cost):

- Write the prediction *before* the run, including the refuting branch.
- Keep the tape.
- Score the prediction. A miss is a finding, not a reason to delete the
  instrument.

---

## 4. What not to implement

- An evolution-vs-random rerun before a searcher steers a Life on a
  positive-margin loop. A larger `--genomes` is not an autonomy
  milestone.
- LLM-authored executable skills. Gated on config-space evolution being
  proven live, which it is not (Phase 6 item 6 was an honest loss).
- Per-instance survival knobs that break the capability manifest
  (`vars(Survive) == {}`).
- Folding `obsview` into `live_*.py` gate scripts. They are one-shot
  proofs and several deliberately mirror a skill's own definition.
- Scaling the lumberjack→carpenter pair as an economy. Every carpentry
  recipe destroys value at vendor prices (`CARPENTER.md`). The pair is a
  mechanism proof.

---

## 5. Commands

Offline:

```
uv run pytest -q
uv run ruff check .
uv run pytest tests/test_smelt.py tests/test_harvest.py tests/test_uomap.py tests/test_boardtrade.py -q
```

Live, flagship pair (prediction already written for the next day):

```
./scripts/run_forge_pair.sh
# or:
PYTHONUNBUFFERED=1 uv run python -m anima2.village --forge-pair --ticks 1800 --monitor --narrate \
    2>&1 | tee .logs/forge-$(date +%Y%m%d-%H%M).log
```

Watch with Safari or `uv run python -m anima2.monitor_watch` — not Chrome, and
not `anima-desktop` (second login kicks the agent). See `docs/MONITORING.md`.

Watch the tape for: `ph=mine` on the smithy apron `(2609, 475)` (should
be gone), `silent=` climbing to a hop on a no-verdict stand, and
`mine survey: map=N` matching the body's `Observation.map_index`.
For the stale-`bs_stand` prediction (§48.3): no late-day
`trip=sell_return to=(2611,473) d=2>0` while Pim stands at `(2609,474)`,
and no age-11 sell giveup cluster after successful sales.