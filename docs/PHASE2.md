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
| 0xB0 OpenGump | `pending_gump` (serial, buttons) | crafting/banking UI | ⏳ |
| 0x3C / 0x24 / 0x25 container | items keyed by container | "do I have ore/ingots/gold?"; looting | ⏳ |
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
- ⏳ **Episodic memory** — store events + outcomes + rewards (mine v1 `memory/`).
- ⏳ **Reflection loop** — periodic LLM summary of episodes → updates goals/strategy
  (Generative Agents). Runs in the slow loop (already non-blocking via `ThreadedCognition`).
- ⏳ **Semantic memory = uowiki** — `wiki_search`/`wiki_read_page`; consult before betting
  on a mechanic, file discrepancy reports when reality differs.

### B2 — new skills (need A actions)
- ⏳ **Mine/Gather** — `Use(pickaxe)` → target cursor → `TargetGround(mountain)` → read
  journal result. (TargetGround is ✅, so this is the first loop to build.)
- ⏳ Smelt · Craft (gump MAKE loop) · Eat/Heal (bandage→self, or Heal spell) · Bank ·
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
3. ⏳ **Mining loop end-to-end** (Use pickaxe → TargetGround → journal "You dig…") —
   first "production"; needs a pickaxe + a mineable tile (scenario setup, ideally GM /
   Control plane, or stand the test char near rock manually).
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
