# anima2 — working guide

Read [`docs/DESIGN.md`](docs/DESIGN.md) for the architecture and decisions. For
current work, read [`docs/IMPROVEMENTS-2026-09-13.md`](docs/IMPROVEMENTS-2026-09-13.md).
The old handoff, including the warrior's development history and earlier open items,
is preserved in [`docs/HANDOFF-2026-08-26.md`](docs/HANDOFF-2026-08-26.md).

## What this is

A Python 3.12+ autonomous Ultima Online brain. It consumes structured Observations
and emits Actions through the Rust `anima-client/anima-core` body. The objective is
continued life without routine GM intervention: survive, work, trade, recover, resume.
The current village runners still stage workplaces, equipment and shops through GM
fixtures. Do not describe staged runs as proof of fresh-character autonomy.

## Architecture and entry points

- `contract.py`, `body.py`, `ipc_body.py`: the body boundary. Bridge schema 31 is
  checked at handshake. Body JSON lives in the sibling repository's
  `crates/anima-contract-json`; the bridge executable is `anima-agent`.
- `agent.py`, `planner.py`, `skills/`: observe, safety interrupts, ordered skill
  selection, deterministic state machines, one emitted action per tick.
- `goals.py`, `capabilities.py`, `capability_cognition.py`: durable intention stack,
  immutable profession capabilities, closed-vocabulary selection, observation-derived
  progress and success. Goal changes cancel native routes; stale cognition is rejected.
- `warrior_life.py`: shared two-agent Life orchestrator, separate work/economy memories,
  exactly one inner agent ticked per tick. Mage, woodsman, carpenter and tinker inherit
  the lifecycle. Live economy frames hold their mode, bounded by FSM give-up, deadline
  and overdue release; death/recovery and being killed can override that hold.
- `life_runner.py`, `village.py`: construction, staging verification, gold provenance,
  multiple workers and telemetry. New runners must reuse these shared seams.
- `obsview.py`: shared Life observation readers. `knobs.py`: shared clamping, including
  `wander_leash`. Rules, readiness gates and execution must agree on each fact.
- `cognition.py`, `memory.py`, `wiki.py`: optional model cognition, episodic/reflective
  memory, knowledge retrieval. Model computation belongs off the playing thread.
- `life_config.py`: strict portable Life profiles. `foundry/life_search.py`: budgeted
  offline configuration search using `foundry/life_eval.py` and the production factory.
- `foundry/`: independent fitness, trajectories, MAP-Elites and evaluation. The measured
  agent must not import or change the ruler; preserve `test_foundry_import_guard.py`.

## Current verification, 2026-09-13

Offline: **1753 tests pass**, Ruff passes. The active pass adds:

- Optional Life model calls run in one bounded background request. A five-second wait
  falls back to the rule. Late results cannot cross candidate disappearance/reappearance,
  goal ownership changes, or death. A hung worker cannot cause unbounded thread creation.
- Market movement fans out after refusals. ServUO changes facing before moving, so a
  confirmed turn is followed by a movement request in the same direction. Six refused
  movement attempts or 24 ticks without a new best distance abandon the leg.
- `trip=... drift=N/24` and the market `WEDGED WALK` signal measure distance progress,
  including sideways movement and oscillation. The watchdog survives failed-trip retries.
- Successful economy frames reset stationary liveness pulses, preventing a productive
  crafter with frozen hunt reward from being reported as idle. Failed frames do not.
- A verified grove arrival updates both wandering homes and the next market return home.
  Existing transactions keep their own home; failed relocations do not claim arrival.
- `--profile` applies saved settings to all six Life-bearing runner shapes. Duplicate
  parameters, unknown roles/knobs and malformed profiles fail before connecting.
- NPC staging returns a fresh observation of the pinned, placed serial, so a shop route
  cannot be derived from its position before teleporting. Unverified placement fails.

Live verification completed on a disposable loopback-only copy of the shard:

- Forge pair: both alive at 1800 ticks, 32 sales, 958 gold banked, 91/97 frames achieved.
- Forced banker obstruction: a verified denied step, `N, NE, NE, NW, NW` approach,
  eight tongs sold for 56 gold, exact return, successful frame in 12 ticks.
- Woodsman: alive at 900 ticks, 610 gold banked, 45/46 frames achieved. Eleven sales
  completed after two workplace relocations; shops stayed fixed. A third late hop
  had no subsequent transaction before the budget ended.

The first fan was live-refuted before the turn fix, and invalid detour fixtures are
retained in the active record. These are continued-operation and geometry checks,
not controlled income uplift or proof of arbitrary routing. All test processes are
stopped; the original shard/save was not modified.

## Development and validation

```sh
uv run pytest -q
uv run ruff check .
uv run python -m anima2
uv run python -m anima2.village --forge-pair --ticks 1800 --monitor --narrate
uv run python -m anima2.village --woodsman --ticks 900 --monitor
```

Use [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md): name the observed fact, reproduce
it offline when possible, change one lever at a time, and state a falsifiable prediction
before a live run. Retain refutations alongside confirmations. Tests are regression
evidence, not a definition of autonomy. `.logs/` contains local run artifacts.

Model steering is optional. Deterministic admission remains synchronous; only model
work goes off-thread. Do not wrap that admission in an async layer that races against
Life mode transitions. Skills must do no model calls, file I/O, or packet parsing.

A live run needs the sibling bridge, classic UO assets and ServUO. Stage evaluations
sequentially: the fixture has one GM account and a second login evicts the first.
Watch with the bridge's read-only monitor, never a second player login. Monitoring
and alarm definitions are in [`docs/MONITORING.md`](docs/MONITORING.md).

## Remaining boundaries

The miner-to-tinker tongs chain has positive-margin live evidence. The board-to-carpenter
chain demonstrates coordination, but vendor-priced carpentry destroys value.
The new offline search measures transfer of staged gold, not earned income or combat.
It reports flat/unmeasured axes explicitly. An exported candidate is not a live-proven
improvement. Legacy Foundry `Genome` remains a separate four-axis representation;
LifeProfile separates role identity from Life thresholds instead of splatting it.

The evolution-versus-random rerun remains deferred: every searched axis must change a
meaningful live trajectory before a larger search budget is justified. Do not implement
LLM-authored executable skills or break the capability survival-instance manifest to
create new knobs. Preserve the independent fitness weights in `foundry/fitness.py`.
