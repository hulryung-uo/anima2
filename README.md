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

**Phase 1 scaffold running.** The Python brain loop drives a persona against a
`MockBody` (no server/Rust needed): perceive → reflexes → planner → skill → act.
Contract mirrored from anima-core; 7 tests green.

```bash
uv venv && uv pip install -e ".[dev]"
python -m anima2     # demo: a miner walks to the worksite, then wanders
pytest -q            # 7 passing
```

Next: an IPC bridge to `anima-net` (drive a live ServUO character) and the LLM
cognition loop. See [`docs/DESIGN.md`](docs/DESIGN.md) §10 for the full roadmap.

## Family

| Project | Role |
|---------|------|
| [`anima-core`](../anima-client/crates/anima-core) | Body — UO protocol, world, assets, path (Rust, headless) |
| [`anima-client`](../anima-client) | Cross-platform client wrapping anima-core (+ web renderer) |
| [`anima`](../anima) (v1) | Original Python AI player + Foundry evolution (mined for assets/lessons) |
| **anima2** | **Brain** — this project |
