# anima2 — Design & Handoff

> **Purpose of this document:** make the project resumable from docs alone. A new
> Claude session (or human) should be able to read this and build anima2 without
> the original chat. It captures *what* anima2 is, *why* each decision was made,
> the architecture, the roadmap, and what to reuse from the existing `anima` (v1).

Last updated: 2026-06-25 · Status: **Phase 1 functional end-to-end.** The Python
brain drives a **live ServUO character** via the `anima-agent` IPC bridge
(perceive→reflexes→planner→skill→act). Skills: Wander, GoTo, Combat, Greet,
SpeakPending. Slow LLM cognition loop is wired (provider-abstracted, non-blocking)
with an offline heuristic default. 20 tests green, ruff clean; navigation +
full-planner runs validated against ServUO on :2594.

---

## 1. What anima2 is (one paragraph)

anima2 is a **new, from-scratch autonomous AI agent that lives in Ultima Online** —
the **Brain** that drives a body. The body is [`anima-core`](../../anima-client)
(the Rust headless UO client we're building). anima2 perceives the world through a
structured **Observation/Action contract**, decides what to do with a **hierarchy
of skills + planner + LLM cognition**, remembers, talks in character, and **gets
better over time** by accumulating skills and following a curriculum. It is a
clean redesign of the original [`anima`](../../anima) (v1, Python) — same soul
("real characters living in Britannia"), better-separated architecture.

### The family (4 projects, one system)
| Project | Role | Lang | Status |
|---------|------|------|--------|
| [`anima-core`](../../anima-client/crates/anima-core) | **Body** — UO protocol, world model, assets, pathfinding (no rendering) | Rust | login/framing done |
| [`anima-client`](../../anima-client) | The new cross-platform client wrapping anima-core (+ future web renderer) | Rust/TS | Phase 1 |
| [`anima`](../../anima) (v1) | Original Python AI player + **Foundry** evolution loop | Python | working; mined for assets/lessons |
| **`anima2`** (this) | **Brain** — the autonomous agent on top of anima-core | TBD (likely Python) | planning |

anima2 is to the body what a driver is to a car. The Interface⊥Brain split (see
anima-client DESIGN.md D2) is the whole point: anima2 never parses bytes — it only
reads Observations and emits Actions.

---

## 2. Decision history & principles (the *why*)

Carried over from the long design discussion. Don't re-litigate without reason.

| # | Decision | Why |
|---|----------|-----|
| A1 | **Structured Observation/Action, never pixels** | The body exposes full game state; vision adds only cost/brittleness. (AlphaStar/OpenAI Five used structured interfaces.) |
| A2 | **Brain ⊥ Body.** anima2 talks to anima-core only via the contract | Swappable brains (scripted/RL/LLM) + swappable bodies (Rust core, later others). The brain never touches packets. |
| A3 | **Hierarchical brain** — deterministic skills (fast) / planner (mid) / LLM (slow) | LLM per-tick is fatal (latency/cost). LLM is for high-level goals, social, novelty, reflection; skills execute reliably. |
| A4 | **Priors + skill library + curriculum beat gradient RL for bootstrap** | Sandbox UO has no reward gradient. LLM priors + the companion wiki ("textbook") + a curriculum get to competence far faster than from-scratch RL. RL/evolution is a *later* optimizer for bottleneck skills only. |
| A5 | **Three planes: Play / Control / Director** | Keep "playing" (contract), "scenario control" (reset/teleport/grant — needs GM), and "what to learn next" (curriculum) as separate concerns. Control plane lives outside both body and brain. |
| A6 | **Reuse anima v1's hard-won assets, rebuild its structure** | v1 has working personas, procedure knowledge, a Foundry kernel (GM control plane, MAP-Elites, fitness), and the wiki flywheel. Reuse the knowledge; don't inherit the tangle. |
| A7 | **LLM provider abstracted; default to Claude** | Tiered models (cheap/frequent → strong/rare). Abstract so Ollama/others still work, but default to the latest Claude family. See §7. |

---

## 3. Architecture

### 3.1 The three planes

```
┌───────────────────────────────────────────────────────────────┐
│  DIRECTOR / CURRICULUM   — what to learn/do next, measure       │
│   automatic curriculum · skill-library growth · fitness         │
└───────────┬───────────────────────────────────┬───────────────┘
            │ Play plane                          │ Control plane
            │ (Observation / Action)              │ (reset/teleport/grant/
            ▼   = normal player                   ▼  set-skills/measure)
┌───────────────────────────┐         ┌──────────────────────────┐
│  anima2  BRAIN            │         │  Control plane            │
│  perception→skills→       │         │  GM account / server      │
│  planner→LLM→memory       │         │  save-restore (own shard) │
└───────────┬──────────────┘         │  (reuse anima Foundry     │
            │ Observation/Action      │   kernel's GM session)    │
            ▼                         └──────────────────────────┘
┌───────────────────────────┐
│  anima-core  BODY (Rust)   │
│  net · world · assets·path │
└───────────────────────────┘
```

- **Play plane** — the Observation/Action contract (§5). Everything a normal player can see/do. anima2 lives here.
- **Control plane** — scenario control for *repeatable* curriculum/eval (reset to known state, teleport, grant items, pin skills, measure independently). A UO client is "just a player" and can't do this alone; it needs a **GM account** or **server save/restore** on your own ServUO shard. anima v1's Foundry `kernel/gm.py` already implements this — reuse it. **Keep it out of the brain and the body.**
- **Director/Curriculum** — chooses tasks, grows the skill library, scores progress. The "teacher."

### 3.2 The brain hierarchy (inside anima2)

```
Observation (from anima-core)
        │
        ▼
┌──────────────────────────────────────────────┐
│ Perception adapter → working state            │  fast, every tick
├──────────────────────────────────────────────┤
│ Reflexes / survival rules (flee, heal, eat)   │  fast, pre-empts everything
├──────────────────────────────────────────────┤
│ Planner — pick a skill toward the goal        │  rules first; bandit/Q-learning later
├──────────────────────────────────────────────┤
│ Skills (deterministic, composable)            │  the "hands": move/mine/attack-loop/
│   the Voyager-style growing skill library     │  craft/bank/peace/hide/vendor...
├──────────────────────────────────────────────┤
│ Cognition (LLM) — goals, social, novelty,     │  SLOW loop (seconds–minutes),
│   reflection; proposes new skills/curriculum  │  runs async, never blocks reflexes
├──────────────────────────────────────────────┤
│ Memory — working · episodic · semantic(wiki)  │  feeds the LLM; records outcomes
│   · skill library                             │
├──────────────────────────────────────────────┤
│ Persona — identity, profession, sociability   │  shapes goals + voice
└──────────────────────────────────────────────┘
        │
        ▼
Action (to anima-core)
```

### 3.3 Two-rate control loop (critical for using LLMs sanely)
- **Fast loop (~100–250ms):** perceive → reflexes → planner → execute current skill → emit Action. No LLM. Deterministic, reliable, cheap. The agent is *always* alive here.
- **Slow loop (seconds–minutes, async):** the LLM reflects on recent episodes, sets/updates the high-level goal, handles social/novel situations, and proposes new skills or curriculum tasks. Its output is *advisory state* the fast loop reads (current goal, target location, conversation intent) — it never sits in the hot path.

This is how Voyager/Generative Agents stay usable: the LLM steers, scripts execute.

---

## 4. The mental model: perceive → decide → act → remember → improve

- **Perceive:** consume Observation; update working state + episodic memory.
- **Decide:** reflexes → planner → skill (fast); LLM sets the goal the planner serves (slow).
- **Act:** emit Actions through the contract; the body executes on the server.
- **Remember:** record skill outcomes (reward signals), journal events, social interactions.
- **Improve:** the Director proposes the next curriculum task; successful LLM-authored skills enter the library; (later) Foundry evolves whole-agent variants offline.

---

## 5. The Observation/Action contract (the seam to anima-core)

**This is the single most important interface to define first.** It is the stable
schema anima2 and anima-core agree on. Codify it in anima-core (the producer) and
mirror it in anima2 (the consumer). Draft shape:

**Observation** (body → brain): `player` {serial, pos(x,y,z), dir, hp/maxhp, mana,
stam, gold, weight, war/hidden/poisoned flags, skills[]}, `nearby_mobiles[]`
{serial, name, pos, hp%, notoriety, flags}, `nearby_items[]` {serial, graphic,
amount, pos|container}, `journal_delta[]` (new chat/system lines), `pending`
{target_cursor?, gump?}, `tick`/timestamp.

**Action** (brain → body): `move{dir, run}`, `use{serial}` / `double_click{serial}`,
`attack{serial}`, `cast{spell}`, `use_skill{id}`, `say{text, type, hue}`,
`target{serial | x,y,z}`, `pickup{serial,amount}` / `drop{...}` / `equip{...}`,
`gump_response{...}`, `context_menu{...}`.

**Control plane** (separate channel, GM-only): `reset`, `teleport{x,y,z}`,
`grant{item}`, `set_skill{id,value}`, `spawn{...}`, `measure` (independent
packet-derived fitness). Mirrors anima Foundry kernel.

Design notes: keep Observation a *snapshot + deltas* (full state is cheap to read
from anima-core's `World`; journal is delta-only). Keep Action a small, total enum.
Version the schema. See anima-client DESIGN.md §3 for the original sketch.

---

## 6. Learning & accumulation

Ordered by ROI (do the cheap, fast accelerants first — A4):

1. **LLM priors + the wiki (the textbook).** The agent reads the companion wiki
   (`../uowiki`, 835+ pages, MCP tools `wiki_search`/`wiki_read_page`) before
   betting on a mechanic, and files discrepancy reports when reality differs.
   This is the long-term semantic memory and the fastest competence boost.
2. **Curriculum.**
   - **Stage 0 = in-game tutorial (New Haven / Young status).** Reachable via the
     play plane, no GM — safe, guided basic-skill bootstrapping.
   - **Automatic curriculum (Voyager-style):** the LLM proposes the next
     achievable task given current skills + memory; difficulty ratchets up.
3. **Skill library (Voyager).** LLM-authored, executable skills indexed by
   natural-language-embedding; on a new task retrieve top-k and compose; on
   success, store the verified skill. Self-verifying via Observation feedback.
   Compounds capability and resists forgetting.
4. **Episodic memory + reflection (Generative Agents).** Store events; the slow
   loop summarizes/reflects to inform goals and social behavior.
5. **RL on bottleneck skills (later).** Bandit/Q-learning for skill *selection*;
   reward = skill-gain/gold/produced-value, gated by survival (mirror v1).
6. **Foundry evolution (later, offline).** MAP-Elites over agent variants
   (profession × sociability …), fitness from independent packet parsing, GM
   control plane for anti-variance. Reuse v1's `foundry/kernel/` design.

---

## 7. LLM strategy

- **Abstract the provider** (v1 had `LLMClient` for Ollama/OpenAI-compat) — keep that seam.
- **Default to the latest Claude family**, tiered to control cost/latency:
  - **Haiku** (`claude-haiku-4-5-20251001`) — frequent/cheap: quick social replies, routine goal nudges.
  - **Sonnet** (`claude-sonnet-4-6`) — planning, curriculum proposal, reflection.
  - **Opus** (`claude-opus-4-8`) — hard reasoning, skill synthesis/debugging.
- **Never in the fast loop.** LLM calls are async, batched where possible, and
  produce advisory state. Cache aggressively (personas, wiki excerpts, skill docs).
- (When implementing, consult the `claude-api` skill for current ids/pricing/tool-use.)

---

## 8. Relationship to anima v1 — reuse vs rebuild

anima v1 lives at `../anima`. It is a working Python system; mine it.

| v1 asset | anima2 plan |
|----------|-------------|
| `personas/*.yaml` (9 personas) | **Reuse** — identity/profession/sociability definitions. |
| `anima/procedures/` (18 procedures) + `docs/actions.md` | **Reuse as knowledge** — port to anima2 skills against the new contract. |
| `anima/brain/` (behavior_tree, llm, prompt, think) | **Rebuild** cleaner as the two-rate loop + cognition; keep prompt/think ideas. |
| `anima/planner/` (goals, meta_controller, modes, strategy, health, deadlock…) | **Mine heavily** — lots of hard-won logic (anti-stuck, circuit breakers, modes). |
| `anima/memory/` (database, journal, learning, retrieval, rewards) | **Rebuild** on the memory model (§6); reuse reward design. |
| `anima/skills/` (RL skills, base ABC) | **Reuse pattern** — Skill ABC + reward signals; evolve into the Voyager library. |
| `foundry/kernel/` (gm, eval, fitness, descriptor, archive, safety, trajectory) | **Reuse** — GM control plane + evolution; it's HUMAN-OWNED and well-designed. |
| wiki flywheel (`../uowiki`, `tools/wiki_report.py`, MCP) | **Reuse** — semantic memory + discrepancy reports. |
| RL methodology (`docs/reinforcement-learning.md`) | **Reference** — state encoding, Q-learning, reward design. |

v1 lessons baked into A1–A7: planner-based decisions > pure behavior tree; LLM
Think off during evals; independent packet-parsed fitness (agents can't lie);
anti-gaming (produce-credit deltas, held-out re-eval).

---

## 9. Language / runtime decision

**Open but leaning Python brain over the contract.**

- **Recommended:** anima2 brain in **Python**, talking to anima-core over the
  Observation/Action contract via **IPC** (e.g. JSON-RPC/length-prefixed JSON over
  a local socket or stdio; anima-core exposes a thin native shim that drives TCP
  and serves the contract). Rationale: reuse v1's Python brain/Foundry/wiki/LLM
  assets and the mature Python LLM ecosystem; keep the brain swappable.
- **Alternative:** Rust brain linked in-process with anima-core (single binary,
  best perf for many parallel agents) — but reimplements LLM/memory plumbing.
- Decide before Phase 1 code. The contract makes this reversible.

---

## 10. Roadmap

- **Phase 0 — ✅ Documentation + Observation/Action contract.** Contract defined in
  anima-core (`src/agent.rs`) and mirrored in anima2 (`contract.py`, JSON round-trip tested).
- **Phase 1 — ✅ Minimal autonomous loop (functional end-to-end).**
  - ✅ Brain: `contract` · `body` (Protocol) + `MockBody` · `persona` (loads v1 YAML) ·
    `skills` (`Wander`, `GoTo`, `Combat`, `Greet`, `SpeakPending`) · `planner` (priority rules) ·
    `reflexes` (stub) · `agent` (two-rate loop).
  - ✅ **IPC bridge** to a live body: `anima-net` ships the `anima-agent` NDJSON bridge
    (`src/bin/agent.rs` + `json.rs`); anima2 `IpcBody` spawns and drives it. **Validated live:**
    the brain navigated a ServUO character to a target and ran the full planner on :2594.
  - ✅ **Slow LLM cognition** wired: `llm.py` (LLMClient + Anthropic/Stub) + `cognition.py`
    (Heuristic default · LLMCognition · ThreadedCognition non-blocking). Speech via `SpeakPending`.
  - ⏭ Next: more skills (gather/mine, eat/heal, bank — need new contract Actions like UseSkill/
    target in anima-core); delegate `GoTo` to anima-core A* `navigate_to`; episodic memory.
- **Phase 2 — Cognition + memory:** episodic memory + reflection; wire the wiki as semantic
  memory; richer in-character speech. (LLM cognition seam already in place from Phase 1.)
- **Phase 3 — Skill library + automatic curriculum:** Voyager-style growth; tutorial stage 0; difficulty ratchet.
- **Phase 4 — Control plane + eval harness:** reuse Foundry GM kernel for repeatable episodes + independent fitness.
- **Phase 5 — Evolution & society:** MAP-Elites over variants; multi-agent social world (Generative Agents).

---

## 11. Decisions resolved / still open
Resolved during Phase 1:
- **Brain language = Python over IPC.** ✅ (anima2 Python; `anima-agent` Rust bridge.)
- **IPC = NDJSON over stdio.** ✅ Request/response (`observe`/`act`/`pump`/`quit`), one JSON
  object per line; bridge logs in and owns the socket. (`anima-net/src/bin/agent.rs`.)
- **Where the contract lives = anima-core** (`src/agent.rs`), JSON shapes in `anima-net/json.rs`,
  mirrored by anima2 `contract.py` (round-trip tested both sides). ✅

Still open:
- **How much v1 code to port vs reimplement** (per-module, §8).
- **`GoTo` greedy vs delegate to anima-core A\* `navigate_to`** (bridge needs a `navigate` cmd +
  UO data path). Greedy works in open terrain today.
- **Cognition cadence / cost controls** (model tier per call, caching, how often to reconsider).
- **Single-agent first vs multi-agent** (recommend single first).

---

## 12. References

- **Sibling docs:** [`anima-client/docs/DESIGN.md`](../../anima-client/docs/DESIGN.md) (the body; has the original Observation/Action + three-plane sketch), [`anima/CLAUDE.md`](../../anima/CLAUDE.md), [`anima/docs/FOUNDRY.md`](../../anima/docs/FOUNDRY.md).
- **Papers/ideas:** Voyager (skill library + automatic curriculum, arXiv 2305.16291); Generative Agents (Stanford Smallville — memory/reflection/social); AlphaStar / OpenAI Five (structured interface > pixels).
- **Knowledge base:** `../uowiki` (companion wiki + MCP `wiki_search`/`wiki_read_page`/`wiki_file_report`).
- **Server for testing:** local ServUO shard (`../servuo`).
