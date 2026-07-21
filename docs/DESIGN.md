# anima2 — Design & Handoff

> **Purpose of this document:** make the project resumable from docs alone. A new
> Claude session (or human) should be able to read this and build anima2 without
> the original chat. It captures *what* anima2 is, *why* each decision was made,
> the architecture, the roadmap, and what to reuse from the existing `anima` (v1).

Last updated: 2026-07-16 · Status: **Phase 7 begun (redeem the evolution
loop, close the skill-ledger honesty gap, sharpen memory retrieval) — work
breakdown written, see [`PHASE7.md`](PHASE7.md); all four items ⏳, none
landed yet. Item 1 (up first) fixes a profession-conditional pool-routing bug
this design pass's own re-read of `evolve.py`/`live_evolve_gate.py` found (a
fisher genome's session can land at a mining coordinate) alongside the
fishing-`nodes_pool` rotation PHASE6.md item 6 already named, before item 2
reruns item 6's own comparative gate at a larger budget; items 3-4 close
DESIGN.md §11's standing skill-ledger-independence gap and PHASE6.md item 1's
literal recency-only retrieval gap. The LLM-authored skill DSL stays
explicitly out of scope, gated on item 2's own verdict — see PHASE7.md's
intro.** Phase 6 COMPLETE (the living village) —
all six items live-verified: persistent lives (disk-backed `ReflectionMemory`),
the village chronicle relationship ledger, and the forum as a continuing
chronicle (thread A); plus a second, fishing-based eval scenario, cognition-
aware eval making `cognition_tier`/`sociability` live, and the decisive
evolution-vs-random rerun (thread B). The rerun's comparative verdict came back
honest and unfavorable — random search decisively beat the evolution loop at a
small budget on the now-enriched harness (infrastructure + enrichment gates
both passed) — reported as-is, with a larger budget + fishing-spot rotation
named as Phase 7 follow-ups; see [`PHASE6.md`](PHASE6.md) item 6.** Phase 5
complete (independent measurement
+ evolution) — all four items done (items 1, 2, and 4 live-verified; item 3
landed offline with its live proof folded into item 4's gate); see
[`PHASE5.md`](PHASE5.md). Phase 4 complete (learning stack, all five items —
[`PHASE4.md`](PHASE4.md)); Phase 3 complete (economy & interaction loop), all
four items done.
Phase 2 (cognition + memory) closed out — see [`PHASE2.md`](PHASE2.md). The
Python brain drives **live ServUO characters** via the `anima-agent` IPC
bridge (perceive→reflexes→planner→skill→act) — from a single agent up to a
working **village** of agents (`village.py`) each holding a profession: miner
(mine + smelt ingots, and **deliver** them), lumberjack, fisher, blacksmith
(gump-driven crafting, **fetch** dropped ingots when starved, and **sell
daggers to a vendor + bank the gold**), hunter (engage weak creatures, then
**loot their corpses**), townsfolk, staged by the Control plane
(`control.py::GmControl`). The slow LLM cognition loop steers with
in-character chatter + a clamped `goal:goto`, periodically reflects episodic
memory into persistent `Insight`s, consults a local read-only index of the
companion wiki (`wiki.py::Wiki`) for a grounding excerpt, and can write
in-character posts to the uotavern forum. **Phase 3 item 1 — the first
inter-agent economy loop (a miner's ingots feed a blacksmith) — is
live-verified**, no contract changes needed. **Phase 3 item 2 — closing the
loop into gold — is live-verified**: the blacksmith sells surplus daggers to a
vendor via its right-click context menu and banks the proceeds at a banker
(`contract.py` gained `ShopBuy`/`ShopSell`/`BuyItems`/`SellItems` and
`PopupMenu`/`PopupRequest`/`PopupSelect`). **Phase 3 item 3 — hunt/loot — is
live-verified**: a bare-handed hunter engages weak creatures (Mongbats) and
loots gold from their corpses in repeated, corpse-tied cycles
(`contract.py` gained `CorpseLink`/`CorpseEquip`). **Phase 3 item 4 — A*
navigate — is live-verified**: `GoTo` now delegates to the bridge's
non-blocking route driver (`contract.py` gained `WalkTo`) instead of greedy
tile-by-tile stepping, monitoring progress from position deltas and falling
back to greedy only when the route makes no progress at all; a differential
live proof shows greedy wedging on a rock-blocked Minoc-ridge course a
straight line can't cross, while the real `GoTo` crosses it both ways (round
trip); see [`PHASE3.md`](PHASE3.md) for the full breakdown of all four items.
The autonomy B1 layer now places stable lifecycle frames around those Goals:
explicit child work suspends and resumes the exact parent, deadlines and
terminal outcomes are archived, progress is derived from Observations, native
routes are cancelled at ownership changes, and agent-unique CAS tokens prevent
late cognition from overwriting or crossing intention stacks. The dedicated
`live_goal_stack.py` gate passed all 20 flags, followed by a clean A4 death-
recovery rerun. B2 adds an opt-in, exact-schema curriculum Goal vocabulary,
Agent-side admission, profession-bound work capabilities, safe FSM yield
boundaries, and observation-derived milestone progress; its live gate converted
9 ingots to 14 through the admitted shipped miner skill with all 14 flags passing.
B3 separates execution authority from the retrieval skill library with an
immutable profession capability registry; its first sealed, deadline-bounded
`blacksmith/bank_gold` Goal moved the same live 100-gold serial from backpack to
bank box through the sole leased adapter, while a cross-profession control
produced zero actions. B4 adds a strict closed-JSON cognition selector over only
observation-ready opaque ids; an injected action field produced no frame/action,
while the exact choice completed the same live transaction through a sealed
COGNITION frame. All 16 B4 gate flags passed, followed by a clean B3 rerun.
See [`AUTONOMY-ROADMAP.md`](AUTONOMY-ROADMAP.md).
922 tests green, ruff clean; the full village, smelting, reflection,
wiki-grounded cognition, miner→blacksmith→vendor→bank trade loop, hunt/loot
loop, A* navigate differential proof, (Phase 4 item 2) role-tiered cognition
cost routing, (Phase 4 item 1) the wiki write loop (`Wiki.file_report()`
+ filing circuit breaker, LLM-judged, code-validated), and (Phase 4 item 4)
a UCB1 bandit tuning `MineSmeltDeliver.deliver_threshold` off item 3's
persisted skill-outcome ledger, and (Phase 4 item 5) an automatic curriculum
of Observation-derived milestones (`CurriculumController`, GM-forced Mining-50
crossing firing exactly one milestone `Episode`, still firing under a
garbage LLM) are all live-verified against ServUO on :2594 (the wiki write
loop specifically against a disposable, remote-less clone of `../uowiki`,
never the real repo).
See [`PHASE2.md`](PHASE2.md) for the Phase 2 close-out status and
[`PHASE3.md`](PHASE3.md)/[`PHASE4.md`](PHASE4.md) for the Phase 3/4
breakdowns.

---

## 1. What anima2 is (one paragraph)

anima2 is a **new, from-scratch autonomous AI agent that lives in Ultima Online** —
the **Brain** that drives a body. The body is [`anima-core`](https://github.com/hulryung-uo/anima-client)
(the Rust headless UO client we're building). anima2 perceives the world through a
structured **Observation/Action contract**, decides what to do with a **hierarchy
of skills + planner + LLM cognition**, remembers, talks in character, and **gets
better over time** by accumulating skills and following a curriculum. It is a
clean redesign of the original [`anima`](https://github.com/hulryung-uo/anima) (v1, Python) — same soul
("real characters living in Britannia"), better-separated architecture.

### The family (4 projects, one system)
| Project | Role | Lang | Status |
|---------|------|------|--------|
| [`anima-core`](https://github.com/hulryung-uo/anima-client/tree/main/crates/anima-core) | **Body** — UO protocol, world model, assets, pathfinding (no rendering) | Rust | login/framing + contract (target/cast/drop-equip/gump) + skills/gump/container observation + A\* pathfinding module + non-blocking `navigate` bridge command (`Action::WalkTo` / `Session::advance_route`) landed |
| [`anima-client`](https://github.com/hulryung-uo/anima-client) | The new cross-platform client wrapping anima-core (+ future web renderer) | Rust/TS | Phase 1 |
| [`anima`](https://github.com/hulryung-uo/anima) (v1) | Original Python AI player + **Foundry** evolution loop | Python | working; mined for assets/lessons |
| **`anima2`** (this) | **Brain** — the autonomous agent on top of anima-core | Python | Phase 3 complete (economy & interaction loop; inter-agent trade, sell/bank, hunt/loot, A* navigate); Phase 4 complete (learning stack: wiki write loop, cognition cost tiering, skill library v0, `deliver_threshold` bandit tuning, automatic curriculum — all five items live-verified); Phase 5 complete (independent
fitness oracle, repeatable eval harness, MAP-Elites archive, evolution loop — items 1/2/4 live-verified, item 3 landed offline); Phase 6 complete (all six items live-verified — persistent lives via disk-backed `ReflectionMemory`; the village chronicle relationship ledger; the forum as a continuing chronicle; a second, fishing-based eval scenario making `evolve.py::PROFESSION_SCENARIO`'s profession axis a real mutation; cognition-aware eval making `cognition_tier`/`sociability` genuinely move the trajectory behind a `cognition_provider` off-switch; and the decisive evolution-vs-random rerun, whose honest verdict was that random beat evolution decisively at this small budget on the enriched harness); Phase 7 begun (work breakdown written, see [`PHASE7.md`](PHASE7.md) — four items; item 1 live-verified — profession-conditional pool routing fixed and the fishing `nodes_pool` threaded through `evolve.py`/`live_evolve_gate.py`, so a fisher genome is staged at a fishing spot with its matched water node instead of a Minoc mining coordinate); autonomy A1/A2/A3/A4 and B1-B4 live-verified (survival, death/corpse recovery, bounded IPC restart, identity-preserving goal interrupts/deadlines, cognition isolation, closed curriculum work goals, an immutable operation capability, and strict closed-vocabulary capability cognition); 922 tests green |

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

**Observation** (body → brain): `player` {serial, pos(x,y,z), dir, body, dead,
hp/maxhp, mana, stam, gold, weight, war/hidden/poisoned flags, skills[]}, `nearby_mobiles[]`
{serial, name, pos, hp%, notoriety, flags}, `nearby_items[]` {serial, graphic,
amount, pos|container}, `journal_delta[]` (new chat/system lines), `pending`
{target_cursor?, gump?}, `tick`/timestamp.

**Action** (brain → body): `move{dir, run}`, `use{serial}` / `double_click{serial}`,
`attack{serial}`, `cast{spell}`, `use_skill{id}`, `say{text, type, hue}`,
`target{serial | x,y,z}`, `pickup{serial,amount}` / `drop{...}` / `equip{...}`,
`gump_response{...}`, `target_cancel`, `context_menu{...}`. Open gumps retain
their structured elements so safety-sensitive replies can validate real buttons
and localized content rather than matching layout substrings.

**Control plane** (separate channel, GM-only): `reset`, `teleport{x,y,z}`,
`grant{item}`, `set_skill{id,value}`, `spawn{...}`, `measure` (independent
packet-derived fitness). Mirrors anima Foundry kernel.

Design notes: keep Observation a *snapshot + deltas* (full state is cheap to read
from anima-core's `World`; journal is delta-only). Keep Action a small, total enum.
Version the schema and advertise it in the native ready event so the brain fails
early on an incompatible bridge. See anima-client DESIGN.md §3 for the original sketch.

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
  - **Haiku** (`claude-haiku-4-5`) — frequent/cheap: quick social replies, routine goal nudges.
  - **Sonnet** (`claude-sonnet-5`) — planning, curriculum proposal, reflection.
  - **Opus** (`claude-opus-4-8`) — hard reasoning, skill synthesis/debugging.
  - Phase 4 item 2 (`llm.py::ROLE_TIER`/`build_tiered_clients`) makes this real:
    one auditable role→tier table, `AnthropicClient` tried first per tier and a
    single reused `ReplicateClient` as the documented degraded fallback
    (`degraded=True`) when Anthropic isn't provisioned. Ids current as of that
    item's landing; re-consult the `claude-api` skill if they've drifted since.
- **Never in the fast loop.** LLM calls are async, batched where possible, and
  produce advisory state. Cache aggressively (personas, wiki excerpts, skill docs)
  — `AnthropicClient`'s `cache_system` flag (on by default) does this for the
  persona/job system prompt once it's long enough to clear Anthropic's minimum
  cacheable-prefix size for the model in play.
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

**✅ Resolved in Phase 1: Python brain over the contract via IPC** (see §11).
The original analysis, kept as the decision record:

- **Chosen:** anima2 brain in **Python**, talking to anima-core over the
  Observation/Action contract via **IPC** (landed as the `anima-agent` NDJSON
  bridge in anima-net). Rationale: reuse v1's Python brain/Foundry/wiki/LLM
  assets and the mature Python LLM ecosystem; keep the brain swappable.
- **Alternative (not taken):** Rust brain linked in-process with anima-core
  (single binary, best perf for many parallel agents) — but reimplements
  LLM/memory plumbing. The contract keeps this reversible.

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
    target in anima-core); delegate `GoTo` to anima-net's `Session::navigate_to`
    (A* from anima-core's `path` module); episodic memory.
- **Phase 2 — Cognition + memory.** *Close-out — see [`PHASE2.md`](PHASE2.md) for the
  itemized status.* Landed: gump-driven crafting (`GumpResponse`/`GumpView`, 0xB0/0xB1
  — the blacksmith MAKE loop) · mining + **smelting** end-to-end (`Mine`, `MineAndSmelt`)
  · fishing + lumberjacking (grove-aware, via the static-map tree finder) · episodic
  memory (`EpisodicMemory`) · a **reflection loop** (`ReflectingCognition` + persistent
  `Insight`s, Generative-Agents-style) · in-character LLM chatter + a clamped
  `goal:goto` (`LLMCognition`) · LLM-written forum posts (`forum.py`). Landed *early*
  (see the re-baselining note below): the Control plane (`control.py::GmControl`) and
  the society layer — a working multi-agent **village** (`village.py`) of staged
  professions (`profession.py`) that talk (`--chatter`) and chronicle their day
  (`--forum`) · **uowiki semantic memory** (`wiki.py::Wiki` — a local, read-only,
  keyword-indexed lookup over `../uowiki`'s docs tree; `LLMCognition`/
  `LLMReflection` consult it before betting on a mechanic, splicing a compact
  excerpt into the slow-loop prompt; filing discrepancy reports back is still
  Phase 4's fuller loop). Remaining: richer cognition (respond to journal lines
  aimed at the agent, a wider goal vocabulary beyond `goto`).
- **Phase 3 — Economy & interaction loop** *(redefined — see note below)* — ✅
  *complete, all four items done — see [`PHASE3.md`](PHASE3.md) for the itemized
  status.* ✅ **Inter-agent trade** (a miner's ingots feed a blacksmith):
  `skills/smelt.py::MineSmeltDeliver` adds a deliver/return phase to the
  miner's work skill (opt-in, greedy no-A* walk to a configured smithy point,
  two-step `PickUp`→`Drop` to the ground); `skills/craft.py::Blacksmith`
  fetches a dropped pile (two-step `PickUp`→`Drop`-into-pack) when starved,
  never fighting the gump state machine; `village.py` co-locates a
  miner+blacksmith pair at a live-calibrated trade spot
  (`profession.py`'s `TRADE_MINE_SPOT`/`TRADE_SMITH_SPOT`). **No contract
  changes needed** — `Drop`/`PickUp`/`TargetObject`/`GumpResponse` already
  existed. Live-verified end to end (`live_trade.py`): the smith crafts its
  starting stock to zero and stalls, the miner mines/smelts/delivers, the
  smith picks the ingots up and crafts again — see PHASE3.md for the full
  transcript and the several Phase-2-vintage bugs this scenario finally
  exercised (a wrong CraftGump button, a silently-breakable tool, a
  path-blocking anvil). ✅ **Bank + buy/sell** (closing the loop into gold):
  `contract.py` gained `ShopBuy`/`ShopSell`/`BuyItems`/`SellItems` (mirroring
  an already-implemented Rust-side surface) and `PopupMenu`/`PopupRequest`/
  `PopupSelect` (added mid-item, once live testing showed the ground truth's
  speech-keyword design is unreachable from `Say` — ServUO keyword matching
  needs client-side `speech.mul` encoding anima-core doesn't do; the
  right-click context menu sidesteps it and was *also* already implemented
  Rust-side); `skills/market.py::BlacksmithMarket` adds sell/bank phases to
  the blacksmith's work skill (opt-in, same composed-phase pattern, a
  manually curated waypoint **route** — not just a point — for the trade
  smithy's own narrow corridor). Live-verified end to end (`live_market.py`):
  crafts daggers, sells them to a vendor (context menu → `SellItems`, dagger
  entries only), banks the gold (context menu → lift-then-place into the
  bank box) — see PHASE3.md for the full transcript and the live-only bugs
  this item's own testing found (a stale bridge binary, a wrong-distance
  `find_mobile_near`, a wandering vendor NPC). ✅ **Hunt/loot** (engage weak
  creatures, kill them, loot their corpses): `contract.py` gained
  `CorpseLink`/`CorpseEquip` (`Observation.corpse_of`/`corpse_equip`,
  mirroring an already-implemented Rust-side surface — 0xAF `DisplayDeath` +
  0x89 `CorpseEquip`, verified directly against `anima-core`); `skills/
  hunt.py::Hunt` composes `Combat` (reused via subclassing, not duplicated)
  with an engage→loot phase pair: a kill is attributed by scanning
  `corpse_of` for a link whose `killed` serial `Hunt` has attacked (`Combat`
  always re-targets "nearest hostile" fresh each tick, so there's no sticky
  target to key off instead), then the corpse is walked to, `Use`-opened
  (an ordinary container, not a gump), and looted via the established
  lift-then-place two-step for a small whitelisted-graphics selection (gold
  plus two verified-but-unexercised gem graphics) — reward pays only for
  valuables **confirmed gained** in the pack, and every stage (walk, open,
  loot) is stall/attempt-bounded with a give-up cooldown (mirrors
  `BlacksmithMarket.giveup_cooldown_ticks`) so an abandoned corpse isn't
  retried immediately. `profession.py` gained a `hunter` profession
  (bare-handed — Wrestling alone reliably kills the calibrated target,
  Mongbat) at a newly live-calibrated, unpopulated `HUNTING_SPOT`;
  `village.py` gained an opt-in `--hunters N` roster knob (default 0,
  existing rosters untouched). Live-verified end to end (`live_hunt.py`): the
  hunter engages, kills, and loots Mongbat corpses in repeated cycles, each
  tied to the specific corpse that produced the confirmed gold gain (not
  just a coarse phase transition) — see PHASE3.md for the full transcript
  and the live-only calibration lesson this item's own testing found (an
  "open field" candidate isn't necessarily *empty* — two early
  `HUNTING_SPOT` candidates turned out to already have nearby wildlife/
  townsfolk). ✅ **A* navigate** (`GoTo` delegated from greedy stepping to the
  bridge's route driver): `contract.py` gained `WalkTo(x, y)`, mirroring
  `anima-net`'s already-implemented non-blocking `Action::WalkTo`/
  `Session::advance_route` (not the originally-scoped blocking
  `Session::navigate_to` — see PHASE2.md A3's updated note); `skills/
  movement.py::GoTo` now emits `WalkTo` once and monitors progress purely
  from position deltas (no route state reaches the observation JSON at all),
  bounded-retries a genuine stall, and falls back to the pre-A* greedy
  stepping if the route makes no progress whatsoever — which is what keeps
  `MockBody` (no route driver) working unchanged. Live-verified with a
  **differential** proof (`live_navigate.py`): on a Minoc-ridge course 36
  tiles apart where a straight line is rock-blocked, a forced-greedy control
  run wedges immediately (0 progress) while the real `GoTo` arrives and
  navigates all the way back (round trip) — see PHASE3.md item 4 for the
  full transcript and two live-only findings: a naive "distance must
  improve" progress signal misreads a healthy A* detour (which routinely
  moves *away* from the target first) as a stall, and a GM-calibrated
  destination can be a real character's one-way trap even when the GM itself
  never sees a problem (GM movement bypasses normal collision denial).
  Migrating `MineSmeltDeliver`'s/`BlacksmithMarket`'s own private greedy
  walkers to `WalkTo` (dropping their manually-curated waypoint routes) is a
  natural follow-up, explicitly out of this item's scope.
- **Phase 4 — The learning stack** *(work breakdown written — see
  [`PHASE4.md`](PHASE4.md) for the itemized status)*: five independently-landable,
  no-op-by-default items — the wiki write loop (`Wiki.file_report()` + a
  ported circuit breaker, ✅ live-verified) and cognition cost tiering +
  prompt caching (✅ live-verified) are done; a skill-library registry with a
  persisted outcome ledger, a discrete-grid bandit tuning
  `MineSmeltDeliver.deliver_threshold`, and an automatic curriculum of
  hand-written, Observation-derived milestones remain ⏳. No item is expected
  to touch the Observation/Action contract.
- **Phase 5 — Independent measurement + evolution** *(complete — see
  [`PHASE5.md`](PHASE5.md); items 1, 2, and 4 ✅ live-verified, item 3 ✅
  landed offline with its live proof folded into item 4's gate)*: an
  **independent fitness oracle** (ground truth the agent's own code can never
  write — closes A6's "agents can't lie" gap Phase 4 left open, ✅), a
  **repeatable eval harness** on the Control plane (fixed-window, multi-seed,
  kernel-integrity-guarded, ✅), a **MAP-Elites archive** over agent *configs*
  (no LLM-authored code, ✅), and an **evolution loop** that improves the
  population — reusing v1's human-owned `foundry/kernel/` (signal source
  swapped from raw-packet parsing to GM-read + observation-tap, ✅; the
  comparative live gate against a random-search baseline came back an honest
  tie, the expected result given today's eval scenario leaves most of the
  genome's mutation space live-inert — see PHASE5.md item 4 and its "Notes
  carried into Phase 6" follow-up). Society scale-out (persistent lives, the
  forum as village chronicle) is split into a Phase 6 note.
- **Phase 6 — The living village** *(work breakdown written — see
  [`PHASE6.md`](PHASE6.md) for the itemized status)*: six independently-
  landable items across two threads — items 1-3 and 5-6 are no-op-by-default
  (an unset flag/unset config field reproduces today's exact behavior);
  item 4 is the deliberate exception, stated plainly rather than folded into
  a blanket claim — it widens the shared, module-global `foundry/
  evolve.py::PROFESSION_SCENARIO` search space for every future
  `evolve()`/`random_search()` call, not just a flagged gate, which is the
  whole point of giving `op_profession` a second candidate (see PHASE6.md
  item 4's own "Key design decisions"). Thread A: persistent lives
  (disk-backed `Insight` memory across sessions), a village chronicle
  (an inter-agent relationship ledger mined from the already-live trade/
  hunt/market loops), and the uotavern forum becoming a genuine continuing
  chronicle. Thread B: the richer-eval-scenarios follow-up PHASE5.md
  item 4's own live gate surfaced — a second scenario-supported profession,
  a cognition-aware eval mode that finally gives `sociability`/
  `cognition_tier` real live signal, and a rerun of the evolution-vs-random
  comparative gate on the enriched harness (thread B). No item is expected
  to touch the Observation/Action contract. **All six items ✅ live-verified,
  Phase 6 COMPLETE** (item 1: `memory.py::ReflectionMemory`'s optional
  `persist_path`/`agent_key` + `load_insights()`, `village.py
  --persist-insights`; item 2: `chronicle.py::ChronicleLedger`, `village.py
  --chronicle`; item 3: `forum.py`'s optional `yesterday`/`chronicle_events`
  grounding, `village.py`'s existing `--forum` block — completing thread A;
  item 4: `foundry/eval.py::SCENARIOS["fishing"]` + `evolve.py::
  PROFESSION_SCENARIO`'s second entry; item 5: cognition-aware eval making
  `cognition_tier`/`sociability` live behind a `cognition_provider`
  off-switch; item 6: the decisive evolution-vs-random rerun on the enriched
  harness, an honest loss — completing thread B). See each item's own "As
  landed" section in [`PHASE6.md`](PHASE6.md) for its live-gate transcript.

- **Phase 7 — Redeem the evolution loop, close the skill-ledger honesty gap,
  sharpen memory retrieval** *(work breakdown written — see
  [`PHASE7.md`](PHASE7.md) for the itemized status)*: four independently-
  landable items, all no-op-by-default. Items 1-2 redo item 6's comparative
  gate with its diagnosed root causes fixed (a profession-conditional
  pool-routing bug this design pass's own re-read of the code found, on top
  of the drained-fishing-bank cause PHASE6.md item 6 already named) and a
  larger `--genomes` budget, reporting whichever verdict comes back,
  honestly. Item 3 closes DESIGN.md §11's standing "skill-ledger reward
  independence" gap by reusing Phase 5 item 1's independent-channel pattern
  (`foundry/trajectory.py`'s GM-read channel (a)) at a new call site. Item 4
  replaces `ReflectingCognition`'s unconditional `recent(3)` insight read
  with a keyword-relevance ranking reusing `_textindex.py`'s already-shipped
  scoring, falling back to `recent(k)` whenever it can't determine
  relevance. The LLM-authored skill DSL (§6 item 3) stays explicitly out of
  scope this phase — its own stated escalation precondition (config-space
  evolution proven to beat random, live) is not yet met, since item 6 came
  back a loss; items 1-2 exist to give that precondition a fair,
  confound-free test, and the DSL is carried into Phase 8 as the named next
  escalation only if they succeed. No item is expected to touch the
  Observation/Action contract.

> **Re-baselining note:** the original Phase 3→5 ordering (skill library, *then*
> Control plane, *then* evolution/society) got overtaken by events. The Control plane
> (`GmControl`) and the society elements (the village, professions, and the forum)
> arrived *early*, during Phase 2, because staging and exercising the new work skills
> live needed them. Phases 3–5 above are re-baselined to what's genuinely left.

---

## 11. Decisions resolved / still open
Resolved during Phase 1:
- **Brain language = Python over IPC.** ✅ (anima2 Python; `anima-agent` Rust bridge.)
- **IPC = NDJSON over stdio.** ✅ Request/response (`observe`/`act`/`pump`/`quit`), one JSON
  object per line; bridge logs in and owns the socket. (`anima-net/src/bin/agent.rs`.)
- **Where the contract lives = anima-core** (`src/agent.rs`), JSON shapes in `anima-net/json.rs`,
  mirrored by anima2 `contract.py` (round-trip tested both sides). ✅

Resolved during Phase 2:
- **Single-agent first vs multi-agent.** ✅ Resolved in favor of multi-agent: the
  village (`village.py`) runs a roster of agents concurrently, each Control-plane
  staged into a distinct profession and workplace.

Resolved during Phase 3:
- **`GoTo` greedy vs delegate to anima-net's route driver.** ✅ Resolved in favor
  of delegating: `GoTo` now emits `Action::WalkTo` (the non-blocking route driver
  that actually landed Rust-side — not the originally-scoped blocking
  `Session::navigate_to`, see PHASE2.md A3's updated note) and monitors progress
  from position deltas, falling back to the old greedy stepping only when the
  route makes no progress at all (keeps `MockBody` working unchanged). Live
  differential proof in PHASE3.md item 4: greedy wedges on a rock-blocked Minoc
  course a straight line can't cross; `WalkTo`-delegated `GoTo` crosses it both
  ways. `MineSmeltDeliver`'s/`BlacksmithMarket`'s own private greedy walkers
  still aren't migrated — noted as a follow-up in PHASE3.md item 4.

Resolved during the autonomy A4 vertical:
- **How a ghost discovers resurrection without GM coordinates.** ✅ The body
  exposes ServUO `0xE5/0xE6` waypoints in schema v8. `RecoverDeath` selects and
  boundedly navigates same-facet type-6 healers, rotates failed candidates, and
  still accepts only the structured free-resurrection gump. Corpse markers are
  location hints only; A2's independent body/position/item-serial attribution
  remains mandatory. A fresh process attaching after death but before ever
  receiving E5 remains safely quarantined because ServUO does not resend those
  markers on login.

Still open:
- **How much v1 code to port vs reimplement** (per-module, §8). PHASE4.md's
  work breakdown resolves this per-module for its own scope (`circuit_breaker.py`
  ported near-verbatim; `tools/wiki_report.py`'s write+commit logic ported;
  `modes.py`/`strategy.py`/`goals.py`/`skills/base.py`'s `diagnose()` mined for
  pattern only, not ported) — the general question stays open for later
  phases' modules.
- **Cognition cadence / cost controls** (model tier per call, caching, how often to
  reconsider) — design landed in PHASE4.md item 2 (a single auditable
  `ROLE_TIER` table + `build_tiered_clients()`, with an explicit `degraded`
  fallback to one shared client when only Replicate is configured); still ⏳
  to land, and its live cache-hit proof is explicitly gated on an
  `ANTHROPIC_API_KEY` this environment hasn't confirmed is provisioned.
- **Skill-ledger reward independence** (opened by PHASE4.md item 3): the
  planned `data/skill_ledger.jsonl` reward signal is the agent's own computed
  `SkillResult.reward`, not an independently GM-verified channel — weaker
  than A6's "agents can't lie" standard, which describes v1 Foundry's
  wire-level packet-parsed fitness. Flagged for a cheap partial mitigation
  (an advisory GM gold/skill readback) in PHASE4.md item 3, not solved.
  **Targeted by PHASE7.md item 3** (work breakdown written, ⏳ not yet
  landed): a sibling `data/skill_ledger_corroboration.jsonl` ledger, reusing
  Phase 5 item 1's independent-channel pattern (`foundry/trajectory.py`'s
  GM-read channel (a), never the corroborating-only channel (b)) at a new,
  standalone-gate call site — deliberately not wired into `village.py`'s
  concurrent multi-agent roster, so this remains a partial mitigation
  (proven sound at single-agent granularity) even once item 3 lands, not a
  full resolution of production village runs.
- **Skill-ledger multi-process concurrency** (opened by PHASE4.md item 3):
  append-only single-process writes are GIL-safe; a fleet of villages
  writing the same ledger file simultaneously is untested and needs an
  explicit file-lock or per-process-path convention before that's real.
- **LLM-authored, executable skills** (DESIGN.md §6 item 3's fuller
  ambition): PHASE4.md deliberately does not attempt this — every item
  composes existing hand-written skills with learned parameters/retrieval/
  picks. A safe-by-construction composition DSL (never `eval`/`exec`, a
  whitelist of existing primitives) is flagged in PHASE4.md as the natural
  next step once item 3's registry/ledger is proven live, not designed here.
  Restated by PHASE6.md's own "Notes carried into Phase 7": the natural next
  escalation once config-space evolution (Phase 5) **and** its richer eval
  harness (Phase 6) are **both proven live** — not yet true, since Phase 6
  item 6's own comparative rerun came back an honest **loss** for evolution,
  not proof. PHASE7.md items 1-2 exist specifically to give that precondition
  a fair, confound-free retest (Phase 6 item 6's own diagnosed causes, plus a
  third this design pass found, fixed; a larger budget); the DSL itself
  stays out of PHASE7.md's own scope either way, carried into Phase 8 as the
  named next escalation **only if** PHASE7.md item 2 reports a decisive
  evolution win — see PHASE7.md's intro and its "Notes carried into Phase 8"
  section for the DSL's own sketched, safe-by-construction shape (a closed
  vocabulary off `skill_library.py::REGISTRY`, a non-Turing-complete
  tick-capped grammar, never `eval`/`exec`).

---

## 12. References

- **Sibling docs:** [`anima-client/docs/DESIGN.md`](../../anima-client/docs/DESIGN.md) (the body; has the original Observation/Action + three-plane sketch), [`anima/CLAUDE.md`](../../anima/CLAUDE.md), [`anima/docs/FOUNDRY.md`](https://github.com/hulryung-uo/anima/blob/main/docs/FOUNDRY.md).
- **Papers/ideas:** Voyager (skill library + automatic curriculum, arXiv 2305.16291); Generative Agents (Stanford Smallville — memory/reflection/social); AlphaStar / OpenAI Five (structured interface > pixels).
- **Knowledge base:** `../uowiki` (companion wiki + MCP `wiki_search`/`wiki_read_page`/`wiki_file_report`).
- **Server for testing:** local ServUO shard (`../servuo`).
