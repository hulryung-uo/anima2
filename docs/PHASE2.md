# Phase 2 — Work Breakdown

Phase 2 = **two workstreams**. The DESIGN.md headline ("cognition + memory") is
workstream B, but the real gate is **workstream A: expanding the contract** in
anima-core — without new Actions/Observation fields, the brain literally cannot
*express* mining, healing, crafting, or banking.

Status legend: ✅ done · 🚧 in progress · ⏳ todo

---

## Workstream A — anima-core contract expansion (critical path)

Each item must land in **4 lockstep places** (see checklist at bottom):
anima-core `agent.rs`/`world`/`net` → `anima-net` (`lib.rs` apply_action + `json.rs`)
→ anima2 `contract.py`. Port from the proven v1 `anima/anima/client/packets.py`.

### A1 — new outgoing Actions + builders
| Action | Packet | Unblocks | Status |
|--------|--------|----------|--------|
| **TargetObject / TargetGround** | 0x6C | mining, casting, healing, any targeted skill | ✅ |
| UseSkill | 0x12 (sub 0x24) | Hiding, Peacemaking, Anatomy, Stealth… | ⏳ |
| CastSpell | 0xBF/0x12 | magery, Heal/Cure | ⏳ |
| Drop / Equip | 0x08 / 0x13 | inventory, looting, gear | ⏳ |
| GumpResponse | 0xB1 | crafting menus (MAKE), banking, quests | ⏳ |
| ContextMenu req/resp | 0xBF 0x13/0x15 | banker, vendor, many NPCs | ⏳ |
| Buy / Sell | 0x3B / 0x9F | economy loop (sell ingots, buy tools) | ⏳ |

### A2 — new incoming parsing + World/Observation fields
| Packet | New state | Why | Status |
|--------|-----------|-----|--------|
| **0x6C target cursor** | `World.pending_target` → `Observation.pending_target` | brain must know the server is asking for a target | ✅ |
| **0x3A UpdateSkills** | `World.skills` → `Observation.skills[]` (value/base/cap/lock) | skill levels + **skill-gain reward signal** | ✅ |
| **0xC1 / 0xCC cliloc** | `JournalEntry.cliloc` + args; resolved brain-side via `anima2/cliloc.py` (Cliloc.enu) | the brain reads localized messages (mining/combat/loot/system) | ✅ |
| 0xB0 OpenGump | `pending_gump` (serial, buttons) | crafting/banking UI | ⏳ |
| **0x3C / 0x25 container** | items keyed by `container` + `ItemView.layer` | "do I have ore/ingots/gold?"; find pickaxe; looting; banking | ✅ |
| corpse (0x2E + container) | loot view | hunt loop | ⏳ |

### A3 — bridge (anima-net) additions
- ✅ new actions in `apply_action` + `json.rs`; `pending_target` in observation JSON.
- ⏳ **`navigate` command** — delegate to anima-core A\* `navigate_to` (routes around
  buildings). Needs a UO data path → **use `~/dev/uo/uo-resource`** (full `.mul`/`.uop`
  set) via `anima-assets`.
- ⏳ expose skills / gump / container in the observation JSON as A2 lands.

---

## Workstream B — anima2 cognition + memory

Proceed once A has enough surface (A1 TargetObject + A2 skills unblock the first
real "work" loop).

### B1 — memory
- ✅ **Episodic memory** — `memory.py::EpisodicMemory` (bounded log of `Episode`s with
  rewards); the agent loop records terminal/rewarded skill outcomes; recent episodes flow
  into `SkillContext.episodes` and the `LLMCognition` situation prompt.
- ✅ **Reflection loop** — `memory.py::Insight`/`ReflectionMemory` (bounded insight
  log, tracks which episodes each insight covered) + `cognition.py::ReflectingCognition`
  (wraps any cognition; fires from *inside* `reconsider()` on a tunable cadence — every
  N reconsiders or M new episodes since the last reflection — so it rides the same
  background thread as `ThreadedCognition` and never touches the fast loop). Two
  producers behind one interface: `HeuristicReflection` (reward-per-skill + notable
  failures, offline default) and `LLMReflection` (one call → JSON insight array,
  falls back to the heuristic on a bad/unparseable response). Insights flow into
  `SkillContext.insights` and the `LLMCognition` situation prompt's new "Lessons
  learned" line. **LIVE-VERIFIED**: mining at the Minoc ridge with `LLMReflection`
  wired to the Replicate qwen3 client, reflection fired after 5 skill-gain episodes
  and produced the insight *"mine has paid off: +0.5 reward over 5 turns."* (qwen
  didn't return valid JSON for the reflection call, so it went through the
  heuristic fallback — same resilience `LLMCognition` already relies on for bare
  prose); confirmed the insight reaching the next goal prompt's "Lessons learned:"
  line. Run: `python -m anima2.live_reflect`.
- ⏳ **Semantic memory = uowiki** — `wiki_search`/`wiki_read_page`; consult before betting
  on a mechanic, file discrepancy reports when reality differs.

### B2 — new skills (need A actions)
- ✅ **Mine/Gather** — `skills/harvest.py::Mine`: find tool (open pack if needed) →
  `Use(pickaxe)` → answer cursor with a probed neighbour tile (round-robin 8 dirs) →
  **reward on Mining skill-base gain** (the real signal & v1's fitness backbone; dig
  result clilocs like #1007072 "You dig some iron ore" now also reach the journal via the
  0xC1 parser — available for richer logic). **LIVE-VERIFIED**: staged at the Minoc
  ridge via the Control plane, the brain mined Mining 35.0 → 35.2 (reward in episodic
  memory). Run: `python -m anima2.live_mine`.
- ✅ **Smelt** — `skills/smelt.py::MineAndSmelt` (subclasses `Mine`): mines until the
  backpack holds `ore_threshold` total ore, then `Use(ore)` → answer the cursor with
  `TargetObject(forge)` → repeats until the pack has no smeltable ore, then resumes
  mining — one skill, so `profession.py` only swaps `work_skill` and adds a
  `structures=[("Forge", dx, dy)]` staged within reach (`Mine` never walks, so the
  forge stays reachable all shift). **Reward on ingots gained.** **LIVE-VERIFIED**:
  at the Minoc ridge, the miner mined ore, smelted it into `IronIngot` (cliloc
  501988), and cycled back to mining automatically; Mining 35.0 → 37.0 over 250
  ticks, 68 ingots produced. Run: `python -m anima2.live_smelt`. Also fixed a
  latent bug in `Harvest._backpack` (matched *any* nearby mobile's backpack by
  layer alone — now filters by `container == player.serial`, since a mobile's own
  contained items share the same placeholder ground position and can tie on
  distance).
- ⏳ Craft (gump MAKE loop) · Eat/Heal (bandage→self, or Heal spell) · Bank ·
  Loot (open corpse → PickUp).

### B3 — richer cognition
- ⏳ context-aware speech using episodic memory + wiki; respond to journal lines aimed at us.
- ⏳ expand goal vocabulary: mine / craft / bank / hunt / socialize / explore.

---

## Critical path (recommended order)
1. ✅ **TargetObject/Ground + 0x6C cursor** — unblocks targeted actions.
2. ✅ **0x3A skills parse + `Observation.skills[]`** — reward signal. (Body requests
   stats/skills on login via `build_status_request` 0x34; gains then push via 0xDF.
   Live-verified: 58 skills populate.)
3. ✅ **Mining loop end-to-end (LIVE)** — `Mine` skill + `control.py::GmControl` (the
   Control plane, GM `[` commands via the bridge). GM stages pickaxe + Mining 35 +
   teleport to the Minoc ridge; the brain then mines, gaining Mining 35.0 → 35.2.
   First "production" loop: **a character works and a skill rises, autonomously.**
4. ⏳ **Gump support** (crafting, banking).
5. ⏳ **Memory + wiki + reflection** (workstream B core).

→ Land 1–3 live first: "the character works and a skill rises" is the foundation the
Phase 3 curriculum/Director builds on.

---

## The 4-lockstep checklist (every contract change)
1. **anima-core** — `agent.rs` (Action variant / Observation field) + `world` state +
   `net/game.rs` (incoming parse) and/or `net/outgoing.rs` (builder).
2. **anima-net** — `lib.rs` `apply_action` (+ helper) and `json.rs` (serialize obs field /
   parse action). Add Rust unit tests.
3. **anima2** — `contract.py` (dataclass / Action + `action_from_dict`). Add Python tests.
4. **Verify** — `cargo test` + `pytest` green both sides (the JSON round-trip tests on each
   side are what keep the wire format in lockstep), then a live no-regression run.

## Test/data notes
- Local ServUO on `127.0.0.1:2594`, account `animatest/animatest` (auto-created).
- UO data files for `navigate`/tile checks: **`~/dev/uo/uo-resource`**.
- Full mining/crafting live validation needs scenario setup (tools + location) — that's
  the Control plane (Phase 4); until then, set it up manually or with a GM account.
