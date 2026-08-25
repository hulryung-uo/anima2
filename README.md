# anima2

> *Anima* (Latin: soul) — a real character living in Britannia.

An autonomous AI agent that **plays Ultima Online**. This repo is the **brain**:
it reads a structured world, decides, and emits actions. The body is
[`anima-core`](https://github.com/hulryung-uo/anima-client) — a Rust headless
UO client. The brain never parses packets or touches a socket.

**The goal is a character that keeps living without anyone watching.** Not a
bot that finishes an errand — an agent that hunts or works, notices it is
losing, retreats, heals, restocks, banks what it earned, comes back when it
dies, and does that for a whole day on a real server while nobody is at the
keyboard. Everything here is in service of that, and the measure of it is a
shard log, not a unit test.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

[Design](docs/DESIGN.md) ·
[How we change it](docs/IMPLEMENTATION.md) ·
[Autonomy roadmap](docs/AUTONOMY-ROADMAP.md) ·
[Watch a run](docs/MONITORING.md) ·
[What a run taught us](docs/AUDIT-2026-07-29.md)

![The anima-core body anima2 drives — live ServUO, real UO art](https://raw.githubusercontent.com/hulryung-uo/anima-client/main/docs/img/screenshot.png)

*The body this brain drives: isometric UO, journal, and HUD, served from the
same session the agent already owns. `--monitor` attaches a read-only view to
that session; it is not a second login.*

## Highlights

- **Structured play, not pixels.** The brain consumes an Observation and emits
  an Action. No screenshots, no packet parsing — the same split AlphaStar used
  against StarCraft.
- **Two-rate loop.** Fast loop (~100–250ms): reflexes, planner, skills. Always
  alive. Slow loop: LLM cognition that *steers* — goals, talk, reflection.
  The model is never in the hot path.
- **Profession lives, not scripted errands.** Warrior, mage, woodsman,
  carpenter, and tinker hunt or work, restock, bank, and report when a rule
  and a gate disagree. A sword-warrior now survives a full day and banks
  1844–2446 gold, unattended, on a shard.
- **A real economy, on the shard.** Three supply chains coordinate only through
  items on the ground. The flagship is miner → tinker tongs: the one
  positive-margin loop at vendor prices.
- **Agents cannot write their own score.** Foundry is an independent fitness
  kernel (MAP-Elites, eval harness). Live claims are proven on ServUO, with
  predictions written down before the day — including the ones that turn out
  wrong, which is where most of what we know came from.

## Why this shape

Sandbox UO has no reward gradient worth training on, and an LLM per tick is
too slow and too expensive to keep a character alive. So the stack is
**priors + a skill library + a curriculum first**; evolution tunes bottlenecks
later. UO is a good subject precisely because it is unfair: the world will
kill a character for standing in the wrong place, and it never explains why.
That forces every claim to be settled by measurement. Three planes stay separate: **Play** (the contract), **Control** (GM
scenario fixtures), **Director** (what to learn next). Control lives outside
both brain and body.

This is a from-scratch redesign of [`anima`](https://github.com/hulryung-uo/anima)
(v1). v1 is mined for personas, Foundry, and wiki lessons — not copied as
structure. The *why* behind each of those choices is
[`docs/DESIGN.md`](docs/DESIGN.md).

```
                    Director / curriculum
                              │
   anima2  BRAIN  ── Observation / Action ──▶  anima-core  BODY
   reflexes · planner · skills · LLM · memory · persona
```

## What works today

Live-verified on a real ServUO shard, not only offline tests:

| | |
| --- | --- |
| **Survive** | Retreat and bandage, cure poison, stay inert while dead, accept a verified resurrection, find a healer from server waypoints, recover the attributed corpse, resume the same goal after death or a killed IPC bridge. |
| **Work** | Nested goals keep parent identity; a sealed capability registry means cognition can only pick opaque, observation-ready ids. Craft, sell, buy, and bank are deadline-bounded transactions with observation-checked success. |
| **Live a profession** | Five Lives on one orchestrator. The tinker turns delivered ingots into tongs and sells them; the sword-warrior hunts, loots, restocks and banks — five consecutive unattended days at **1844–2446 gold, zero deaths**, 92–100% of its transactions achieved. A roster works too: three requested, two staged and both profitable — the third pocket has no walkable ground and the runner says so instead of putting a character in a lake. |
| **Trade with others** | Tinker → mage (gold), lumberjack → carpenter (boards), miner → tinker (iron / tongs). Coordination is items on the ground — no private channel. The last of those is the one positive-margin loop. |
| **Watch** | `--monitor` serves a read-only client view per agent (`http://127.0.0.1:8801/`). `--narrate` makes the character say *what* and *why*. |
| **Measure** | Independent GM-read fitness, a repeatable eval harness, a MAP-Elites archive. A tie against random search is reported as a tie. |

Phases 2–6 of the original roadmap are complete (cognition and memory,
economy, learning stack, measurement, living village). The autonomy track
and the next Foundry rerun are governed by
[`docs/AUTONOMY-ROADMAP.md`](docs/AUTONOMY-ROADMAP.md) — a larger evolution
budget is not the next milestone.

The warrior's arc is the sharpest measurement the project has: **sixteen
consecutive live days banked nothing** — twelve of them ending in a corpse —
and the seventeenth banked 325. Nothing about the agent's intelligence changed
in between. What changed is listed below.

## What the shard taught us

Every line below cost a live day and is written up with its evidence in
[`docs/AUDIT-2026-07-29.md`](docs/AUDIT-2026-07-29.md). They are here because
they generalise past this game.

**The world model was wrong far more often than the agent was.** For sixteen
days a warrior "could not bank". It could not *reach* a bank: the shops were
staged at a flat ±12 tiles and the hunting ground is a plateau ringed by
cliffs, while ServUO allows a land step of `+2`. That one fact also explained
"the agent never buys anything" and "the ghost never resurrects" — the healer
was behind the same wall. Later, two warriors of a three-man roster stood
motionless all day: they had been placed in **open water**, and our map model
read ground as height alone, so a flat lake looked like perfect walking
terrain. Now `tiledata.mul`'s passability flags are read, and the layout is
checked before a character is put in it.

**"Known flakiness" is usually a fact nobody looked up.** A vendor purchase
had been documented for months as stalling "~50% of runs". ServUO's
`Armorer.InitSBInfo` picks one of four stock tables with `Utility.Random(4)`,
and only two of them contain plate armour — fixed for that NPC's life. The
agent's give-up ladder had been behaving correctly the whole time, against a
shop that genuinely had nothing to sell. The 50% was the die.

**A false failure is not free.** A deposit that had actually succeeded was
being recorded as a give-up, because the trip's proof required walking back to
an exact tile a fighter never returns to. Fixing the *bookkeeping* more than
doubled the day's earnings — every mislabelled frame had made the give-up
ladder spend a real trip's worth of ticks.

**Watch for a budget that is spent once and never returned.** Three separate
bugs, months apart, had the same shape: a counter meant to bound a *futile*
action was charged for every attempt and only reset on success. A retreat, a
spawn placement, and a chase each ended a character's day after six blocked
steps.

**A body that cannot move is a wall, and that includes your own furniture.**
Pinned prey barricaded a warrior onto its own tile; later, a pinned *banker*
stood in the only corridor between a crafter and its vendor. The walk is
greedy and cannot sidestep, so one occupied tile is an impassable one.

**Instruments beat inference, and silence is not health.** Almost every
diagnosis here came from a field added *after* a run went wrong: nearest
hostile distances, the action actually emitted and for how long, the walk's
target and remaining distance, transactions-achieved versus retired. The
alarms that stayed quiet through real failures were structurally blind — one
counted only a kind of step the code never emitted. Counters print even at
zero, because "no line" and "nothing went wrong" must not look the same.

**An improvement can make another path harder to reach.** Teaching the warrior
to keep its armour on cut bandage consumption thirty-fold — and pushed the
restock errand out of a day's reach entirely, so the capability that buys
bandages became *harder* to exercise by getting better at not needing them.

**Cheap search cannot reach what only happens when you are hurt.** An offline
harness now scores a Life against banked gold for free. Four of five tuning
knobs score *identically* at every value in it, because they only bite when
something goes wrong, and what goes wrong is taking damage — which a packet
double does not simulate. That is measured and kept as a test, so the limit
cannot be quietly forgotten.

**Write the prediction before the run, including the branch that refutes it.**
Several of the results above are refutations of our own hypotheses — a diagonal
rule that turned out not to explain the tape, a bandage estimate wrong by
thirty times, a fix reverted because it reddened a live-proven guarantee. They
are in the record with the same weight as the confirmations.

## Quick start

**Offline** needs Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest -q          # MockBody + a fake bridge; no shard
uv run ruff check .
uv run python -m anima2   # a miner walks to work, then wanders
```

**Live** needs a running UO shard (local ServUO on `:2594` is the usual
fixture), classic UO data files, and the IPC bridge from the sibling repo:

```bash
( cd ../anima-client && cargo build -p anima-net )

# One character:
uv run python -m anima2.live 127.0.0.1 2594 animatest animatest --goto 3720 2216

# Flagship day — miner delivers iron, tinker turns it into tongs:
uv run python -m anima2.village --forge-pair --ticks 1800 --monitor --narrate
# Grimm  http://127.0.0.1:8801/    Pim  http://127.0.0.1:8802/

# A warrior's day — hunt, loot, restock, bank, unattended:
uv run python -m anima2.village --warriors 1 --ticks 1200 --monitor --narrate
```

The warrior's kit is what decides which economy legs a day can reach, so it is
tunable rather than fixed: `--warrior-bandages 3` starts below the restock
threshold, `--warrior-skip Katana` (or `PlateChest`) stages a character that
has to go and buy one. Defaults are the full kit, so every measured day stays
comparable.

LLM cognition is opt-in (`--llm`, `--chatter`, `--llm-tiers`) and needs
`ANTHROPIC_API_KEY` plus `uv pip install -e ".[llm]"`. The forge pair does
not. Schema version is checked at handshake: a mismatch with the bridge
aborts before a character is driven.

More village shapes (`--carpenter`, `--woodsman`, `--supply-pair`,
`--pipeline`) and the single-skill live gates (`python -m anima2.live_mine`,
`live_trade`, `live_bank_goal`, …) are in the module docstrings. How to
*watch* a day: [`docs/MONITORING.md`](docs/MONITORING.md). How to *write down*
what looked wrong: [`docs/OBSERVATIONS.md`](docs/OBSERVATIONS.md).

## Documentation

The project is meant to be resumable from docs, not from chat history.

| Doc | What it is |
| --- | --- |
| [`docs/DESIGN.md`](docs/DESIGN.md) | Source of truth: what anima2 is, the decision history, architecture, contract, learning plan |
| [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) | How a change is chosen, built, and closed (fixture first, one lever, prediction before a live day) |
| [`docs/AUTONOMY-ROADMAP.md`](docs/AUTONOMY-ROADMAP.md) | From staged worker to a character that keeps living without GM props |
| [`docs/AUDIT-2026-07-29.md`](docs/AUDIT-2026-07-29.md) | Live evidence: what a run actually proved, and what it did not |
| [`docs/MONITORING.md`](docs/MONITORING.md) | Read-only spectator view, status-line vocabulary, liveness alarms |
| [`docs/OBSERVATIONS.md`](docs/OBSERVATIONS.md) | What a human watching the character saw look wrong — written before anyone knows the cause |
| Profession notes | [`SWORD-WARRIOR`](docs/SWORD-WARRIOR.md) · [`MAGE`](docs/MAGE-AND-PIPELINE.md) · [`WOODSMAN`](docs/WOODSMAN.md) · [`CARPENTER`](docs/CARPENTER.md) |

Phase write-ups (`docs/PHASE2.md` … `PHASE7.md`) are the closed historical
record. [`docs/HISTORY.md`](docs/HISTORY.md) is the narrative through them.

## Family

| Project | Role |
| --- | --- |
| [`anima-core`](https://github.com/hulryung-uo/anima-client/tree/main/crates/anima-core) | **Body** — UO protocol, world, assets, path (Rust, headless) |
| [`anima-client`](https://github.com/hulryung-uo/anima-client) | Cross-platform client around that core (web renderer + desktop) |
| [`anima`](https://github.com/hulryung-uo/anima) (v1) | Original Python player + Foundry; mined for assets and lessons |
| **anima2** | **Brain** — this project |
| [`uowiki`](https://github.com/hulryung-uo/uowiki) | Semantic memory / textbook the slow loop can consult |

## Contributing

Issues and pull requests are welcome. For a code change, start from
[`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md): name a fact, pin it with a
test that fails on the old code, change one lever, write the prediction
before spending a live day. Do not pick work from a stale “Next:” pointer
or a larger evolution budget — that criterion is in AUTONOMY-ROADMAP §E.
