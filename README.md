# anima2

> *Anima* (Latin: soul) — a real character living in Britannia.

An autonomous AI agent that **plays Ultima Online**. This repo is the **brain**:
it reads a structured world, decides, and emits actions. The body is
[`anima-core`](https://github.com/hulryung-uo/anima-client) — a Rust headless
UO client. The brain never parses packets or touches a socket.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

[Design](docs/DESIGN.md) ·
[How we change it](docs/IMPLEMENTATION.md) ·
[Autonomy roadmap](docs/AUTONOMY-ROADMAP.md) ·
[Watch a run](docs/MONITORING.md)

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
  and a gate disagree.
- **A real economy, on the shard.** Three supply chains coordinate only through
  items on the ground. The flagship is miner → tinker tongs: the one
  positive-margin loop at vendor prices.
- **Agents cannot write their own score.** Foundry is an independent fitness
  kernel (MAP-Elites, eval harness). Live claims are proven on ServUO, with
  predictions written down before the day.

## Why this shape

Sandbox UO has no reward gradient worth training on, and an LLM per tick is
too slow and too expensive to keep a character alive. So the stack is
**priors + a skill library + a curriculum first**; evolution tunes bottlenecks
later. Three planes stay separate: **Play** (the contract), **Control** (GM
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
| **Live a profession** | Five Lives on one orchestrator. The tinker is the flagship: buy iron or fetch delivered ingots, craft tongs, sell, bank, replace a worn tool. |
| **Trade with others** | Tinker → mage (gold), lumberjack → carpenter (boards), miner → tinker (iron / tongs). Coordination is items on the ground — no private channel. The last of those is the one positive-margin loop. |
| **Watch** | `--monitor` serves a read-only client view per agent (`http://127.0.0.1:8801/`). `--narrate` makes the character say *what* and *why*. |
| **Measure** | Independent GM-read fitness, a repeatable eval harness, a MAP-Elites archive. A tie against random search is reported as a tie. |

Phases 2–6 of the original roadmap are complete (cognition and memory,
economy, learning stack, measurement, living village). The autonomy track
and the next Foundry rerun are governed by
[`docs/AUTONOMY-ROADMAP.md`](docs/AUTONOMY-ROADMAP.md) — a larger evolution
budget is not the next milestone.

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
```

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
