# CLAUDE.md — anima2

**Read [`docs/DESIGN.md`](docs/DESIGN.md) first.** It is the source of truth:
what anima2 is, the decision history (the *why*), architecture, the
Observation/Action contract, the learning plan, the roadmap, and what to reuse
from `anima` (v1). This project is designed to be resumable from that doc alone.

## What this is
A new, from-scratch **autonomous AI agent** that plays Ultima Online — the
**Brain**. It drives a body, [`anima-core`](../anima-client/crates/anima-core)
(Rust headless UO client), through a structured **Observation/Action contract**.
Clean redesign of `../anima` (v1, Python); mines v1 for assets and lessons.

## Current phase
**Phase 3 in progress (economy & interaction loop), items 1–2 done.** Phase 2
(cognition + memory) closed out — see PHASE2.md. The Python brain drives
**live ServUO characters** via the `anima-agent` NDJSON bridge — from a single
agent (`live.py`) up to a working **village** (`village.py`) of agents each
staged (Control plane, `control.py::GmControl`) into a profession
(`profession.py`): miner (mine + smelt ingots, and **deliver** them),
lumberjack (grove-aware chopping), fisher, blacksmith (gump-driven MAKE-loop
crafting, **fetch** dropped ingots when starved, and now **sell daggers to a
vendor + bank the gold**), townsfolk. Package adds
`skills.harvest`/`smelt`/`craft`/`market`
(`Mine`/`Chop`/`Fish`/`MineAndSmelt`/`MineSmeltDeliver`/`Blacksmith`/
`BlacksmithMarket`) · `memory` (`EpisodicMemory` + `ReflectionMemory`) ·
`cognition` gains `ReflectingCognition` (episodes → persistent `Insight`s
feeding later goal/speech prompts) and `LLMCognition` in-character chatter + a
clamped `goal:goto` · `forum` (LLM-written in-character posts to uotavern,
`village.py --forum`) · `contract` now carries `GumpResponse`/`GumpView` for
crafting gumps, `ShopBuy`/`ShopSell`/`BuyItems`/`SellItems` for vendor
transactions, and `PopupMenu`/`PopupRequest`/`PopupSelect` for right-click
context menus · `wiki` (read-only semantic memory over the local `../uowiki`
docs tree; optionally grounds `LLMCognition`/`LLMReflection` prompts with a
compact excerpt). **Phase 3 item 1 — the first inter-agent economy loop —
is live-verified** (`live_trade.py`): a miner mines, smelts, and hauls
ingots to a co-located blacksmith that has run its own stock dry, drops
them, and the blacksmith picks them up and crafts again — no contract
changes needed (`Drop`/`PickUp` already existed). **Phase 3 item 2 — closing
the loop into gold — is live-verified** (`live_market.py`): the blacksmith
sells surplus daggers to a staged vendor via its right-click context menu
(0x9E `SellList` → `SellItems`, dagger entries only — a plain `Say`
"vendor sell" turned out to be unreachable on real ServUO, see PHASE3.md
bug 1), then banks the proceeds at a staged `Banker` the same way (opens the
bank box, then the established lift-then-place two-step) — a manually
curated waypoint route around the trade smithy's own narrow corridor
(`profession.py`'s `VENDOR_SPOT`/`BANKER_SPOT`), since a single straight-line
greedy walk can't reach either from the smith's stand tile. See PHASE3.md
for the full breakdown of both items (including several Phase-2-vintage bugs
the live scenarios finally exercised: a wrong CraftGump button, a tool that
silently breaks, an anvil blocking the delivery corridor, a proximity-failure
CraftGump reshow that froze the MAKE loop, and — for item 2 — a stale bridge
binary, a wrong-distance `find_mobile_near`, and a wandering vendor NPC).
205 tests green, ruff clean. **Next:** PHASE3.md items 3–4 (hunt/loot, A*
navigate — see DESIGN.md §10).

## Dev
- Offline: `uv venv && uv pip install -e ".[dev]"` · `python -m anima2` · `pytest -q` · `ruff check .`
- Live: build the bridge in the sibling repo (`cd ../anima-client && cargo build -p anima-net`),
  then `python -m anima2.live <host> <port> <user> <pass> [--goto X Y] [--llm]`.
- The bridge bin + JSON shapes live in `../anima-client/crates/anima-net` (`src/bin/agent.rs`,
  `src/json.rs`) — keep them in lockstep with `contract.py`.

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

## Likely stack (open — DESIGN.md §9)
Python brain talking to anima-core over the contract via IPC (reuse v1's
brain/Foundry/wiki/LLM assets). LLM provider abstracted, default to latest Claude
family, tiered (Haiku/Sonnet/Opus); **never in the fast loop**. Consult the
`claude-api` skill when wiring LLM calls.

## Key references
`../anima` (v1: personas, planner, Foundry kernel, wiki flywheel), `../uowiki`
(semantic memory + MCP tools), `../anima-client/docs/DESIGN.md` (the body + the
original contract sketch), `../servuo` (local test shard).
