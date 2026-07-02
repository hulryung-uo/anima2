# anima2

> *Anima (Latin: soul)* — a real character living in Britannia.

An **autonomous AI agent that plays Ultima Online** — the **Brain** that drives a
body. A clean redesign of [`anima`](../anima) (v1), built on top of
[`anima-core`](../anima-client) (the new Rust headless UO client).

> **New here? Read [`docs/DESIGN.md`](docs/DESIGN.md)** — the full design & handoff
> doc (what anima2 is, why, architecture, roadmap, what to reuse from v1). This
> project is resumable from that doc alone.

## The idea

anima2 perceives UO through a structured **Observation/Action contract** (never
pixels, never raw packets), decides with a **hierarchy of deterministic skills +
planner + LLM cognition**, remembers, talks in character, and **improves** by
accumulating skills and following a curriculum. It is the *driver*; `anima-core`
is the *car*.

```
   Director / Curriculum   (what to learn next)
            │
   anima2  BRAIN  ── Observation/Action ──▶  anima-core  BODY (Rust)
   reflexes · planner · skills · LLM · memory · persona
```

- **Fast loop (~100–250ms):** perceive → reflexes → planner → skill → act. No LLM. Always alive.
- **Slow loop (seconds–min, async):** LLM sets goals, handles social/novelty, reflects, proposes new skills. Steers; never blocks.

## Status

**Phase 3 begun** (economy & interaction loop; see
[`docs/PHASE3.md`](docs/PHASE3.md)). Phase 2 (cognition + memory) closed out —
see [`docs/PHASE2.md`](docs/PHASE2.md). The Python brain drives **live ServUO
characters** through the `anima-agent` IPC bridge: perceive → reflexes → planner
→ skill → act. It scales from a single agent to a working **village** of agents
each holding down a profession — miner (mine + smelt ingots, and now **deliver**
them to a blacksmith), lumberjack (grove-aware chopping), fisher, blacksmith
(gump-driven crafting, and now **fetch** dropped ingots when starved),
townsfolk — staged by the Control plane. The slow LLM cognition loop steers with
in-character chatter and a clamped `goal:goto`, periodically reflects on
episodic memory into persistent insights that feed back into later prompts,
consults a local read-only index of the companion wiki (`../uowiki`) for a
grounding excerpt, and can write in-character posts to the uotavern forum.
**The first inter-agent economy loop is live-verified**: a miner hauls smelted
ingots to a co-located blacksmith that's run dry, drops them, and the
blacksmith picks them up and crafts again — see `live_trade.py`. 137 tests
green.

```bash
uv venv && uv pip install -e ".[dev]"
pytest -q                       # 137 passing (offline; uses MockBody + a fake bridge)
python -m anima2                # offline demo: a miner walks to work, then wanders

# Live (needs a running UO server + the built bridge):
( cd ../anima-client && cargo build -p anima-net )
python -m anima2.live 127.0.0.1 2594 animatest animatest --goto 3720 2216
#   add --llm to use Claude cognition (needs ANTHROPIC_API_KEY + pip install -e ".[llm]")

# A working village (Control-plane staged; defaults: 2 miners, 1 each of the rest,
#   60 ticks) — a roster with both a miner and a blacksmith co-locates the first
#   of each at a calibrated trade spot and wires up ingot delivery:
python -m anima2.village
#   add --chatter for LLM in-character speech + goal:goto (needs a Replicate key in
#     anima v1's config.yaml, or REPLICATE_API_TOKEN — no extra pip install)
#   add --forum to post each villager's day to uotavern (needs ANIMA_FORUM_API_KEY,
#     or the forum key in anima v1's config.yaml)

# Single-skill live proofs (GM stages the scenario, then the brain works it):
python -m anima2.live_mine      # mines ore, Mining skill rises
python -m anima2.live_smelt     # mines then smelts ore into ingots, end to end
python -m anima2.live_reflect   # LLM cognition + reflection, wiki-grounded prompts
python -m anima2.live_trade     # 2-agent inter-agent economy proof: miner -> blacksmith
```

Next: bank + buy/sell, hunt/loot, A* navigate — see
[`docs/PHASE3.md`](docs/PHASE3.md) and [`docs/DESIGN.md`](docs/DESIGN.md) §10
for the roadmap.

## Family

| Project | Role |
|---------|------|
| [`anima-core`](../anima-client/crates/anima-core) | Body — UO protocol, world, assets, path (Rust, headless) |
| [`anima-client`](../anima-client) | Cross-platform client wrapping anima-core (+ web renderer) |
| [`anima`](../anima) (v1) | Original Python AI player + Foundry evolution (mined for assets/lessons) |
| **anima2** | **Brain** — this project |
