# Phase 2 — Work Breakdown

Phase 2 = **two workstreams**. The DESIGN.md headline ("cognition + memory") is
workstream B, but the real gate is **workstream A: expanding the contract** in
anima-core — without new Actions/Observation fields, the brain literally cannot
*express* mining, healing, crafting, or banking. Mining and crafting now do
(the target-cursor + gump surface landed); healing and banking still need it.

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
| UseSkill | 0x12 (sub 0x24) | Hiding, Peacemaking, Anatomy, Stealth… | ⏳ (Rust side has `UseSkill` end-to-end; not mirrored in `contract.py` yet) |
| **CastSpell** | 0xBF/0x12 | magery, Heal/Cure | ✅ (all 4 lockstep places + `test_contract.py`; no caster skill uses it yet) |
| **Drop / Equip** | 0x08 / 0x13 | inventory, looting, gear | ✅ (all 4 lockstep places + `test_contract.py`; not yet driven by a skill) |
| **GumpResponse** | 0xB1 | crafting menus (MAKE), banking, quests | ✅ — drives `skills/craft.py::Blacksmith`'s MAKE-button loop; banking not wired |
| ContextMenu req/resp | 0xBF 0x13/0x15 | banker, vendor, many NPCs | ✅ (Phase 3 item 2 — `PopupRequest`/`PopupSelect`/`PopupMenu`; drives `skills/market.py::BlacksmithMarket`'s vendor-sell/bank-open flow) |
| Buy / Sell | 0x3B / 0x9F | economy loop (sell ingots, buy tools) | ✅ (Phase 3 item 2 — `BuyItems`/`SellItems` mirrored into `contract.py`; `SellItems` drives `BlacksmithMarket`, `BuyItems` unused so far) |

### A2 — new incoming parsing + World/Observation fields
| Packet | New state | Why | Status |
|--------|-----------|-----|--------|
| **0x6C target cursor** | `World.pending_target` → `Observation.pending_target` | brain must know the server is asking for a target | ✅ |
| **0x3A UpdateSkills** | `World.skills` → `Observation.skills[]` (value/base/cap/lock) | skill levels + **skill-gain reward signal** | ✅ |
| **0xC1 / 0xCC cliloc** | `JournalEntry.cliloc` + args; resolved brain-side via `anima2/cliloc.py` (Cliloc.enu) | the brain reads localized messages (mining/combat/loot/system) | ✅ |
| **0xB0 OpenGump** | `World.gumps` → `Observation.gumps[]` (`GumpView`: serial, gump_id, layout) | crafting/banking UI | ✅ — the blacksmith craft gump is live (`skills/craft.py`) |
| **0x3C / 0x25 container** | items keyed by `container` + `ItemView.layer` | "do I have ore/ingots/gold?"; find pickaxe; looting; banking | ✅ |
| **0x74 / 0x9E shop windows** | `World.shop_buy`/`shop_sell` → `Observation.shop_buy`/`shop_sell` | vendor buy/sell prices — answer with `BuyItems`/`SellItems` | ✅ (Phase 3 item 2 — `ShopBuy`/`ShopSell`/`ShopBuyEntry`/`ShopSellItem` mirrored into `contract.py`; `shop_sell` drives `BlacksmithMarket`) |
| **0xBF/0x14 popup menu** | `World.popup` → `Observation.popup` (`PopupMenu`: serial, entries) | right-click context menus (vendor sell, open bank box, …) | ✅ (Phase 3 item 2 — `PopupMenu`/`PopupEntry` mirrored into `contract.py`) |
| corpse (0x2E + container) | loot view | hunt loop | ⏳ |

### A3 — bridge (anima-net) additions
- ✅ new actions in `apply_action` + `json.rs`; `pending_target` in observation JSON.
- ⏳ **`navigate` command** — delegate to anima-net's `Session::navigate_to` (A\* from
  anima-core's `path` module; routes around buildings). Needs a UO data path →
  **use `~/dev/uo/uo-resource`** (full `.mul`/`.uop` set) via `anima-assets`.
  Now a Phase 3 item (DESIGN.md §10).
- ✅ skills / gump / container are all exposed in the observation JSON (0x3A →
  `skills[]`, 0xB0 → `gumps[]`, 0x3C/0x25 → `items[]` keyed by `container`).

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
  (wraps any cognition; a reflection becomes *due* from inside `reconsider()` on a
  tunable cadence — every N reconsiders or M new episodes since the last reflection,
  whichever comes first — but runs on its **own dedicated daemon thread**
  (`_reflect_bg`), off the goal-delivery path entirely: `reconsider()` calls
  `inner.reconsider()` and returns that goal immediately, so even a slow LLM-backed
  reflection call can never add latency to goal delivery — including under the usual
  `ThreadedCognition(ReflectingCognition(inner))` composition. A non-overlap guard
  (`_reflecting`, mirroring `ThreadedCognition`'s busy-flag) skips a round rather than
  overlapping if one is already in flight; cadence counters only reset when a pass
  actually starts, so no due round is silently dropped; a reflection exception can
  never wedge the guard or kill cognition. `wait_idle()` (also added to
  `ThreadedCognition`) gives tests a deterministic, sleep-free join point.) Two
  producers behind one interface: `HeuristicReflection` (reward-per-skill + notable
  failures, offline default) and `LLMReflection` (one call → JSON insight array,
  screened/clamped through the same `_clean_model_line()` defense `LLMCognition` uses
  for speech, falls back to the heuristic on a bad/unparseable/character-breaking
  response). Insights flow into `SkillContext.insights` and the `LLMCognition`
  situation prompt's "Lessons learned" line. **LIVE-VERIFIED**: mining at the Minoc
  ridge with `LLMReflection`
  wired to the Replicate qwen3 client, reflection fired after 5 skill-gain episodes
  and produced the insight *"mine has paid off: +0.5 reward over 5 turns."* (qwen
  didn't return valid JSON for the reflection call, so it went through the
  heuristic fallback — same resilience `LLMCognition` already relies on for bare
  prose); confirmed the insight reaching the next goal prompt's "Lessons learned:"
  line. Run: `python -m anima2.live_reflect`.
- ✅ **Semantic memory = uowiki** — `wiki.py::Wiki`: anima2 is a standalone Python
  process (no MCP, no deployed-site access), so this reads `../uowiki/src/content/docs`
  directly — a small, dependency-free keyword index (title/description/heading/body,
  title weighted heavily; `ja`/`ko` locale duplicates and `templates`/`essays`
  build-preset/narrative pages excluded — see `wiki.py`'s module docstring) built
  lazily on first use and cached thereafter, so it never touches the fast loop. Wired
  into `cognition.py::LLMCognition`/`LLMReflection` (both take an optional `wiki=`):
  each derives a short query from context (`_wiki_query`/`_top_skill_name` — the
  most-rewarded recent skill episode's name + job title) and splices at most one
  compact "Wiki — `<title>`: `<excerpt>`" line into its prompt, memoized per query
  so an unchanged query costs one `Wiki.search()` call, not one per reconsider
  (DESIGN.md §7). Indexing the real ~2.4k-file tree (865 pages after exclusions)
  measured ~0.35–0.45s — well under a second, and lazy indexing means it only ever
  runs on a slow-loop thread (`ThreadedCognition`'s worker / `ReflectingCognition`'s
  reflection thread), never the fast tick. Read-only: filing discrepancy reports
  when reality differs is still Phase 4's fuller wiki loop (DESIGN.md §10).
  **LIVE-VERIFIED**: mining at the Minoc ridge with the real `../uowiki` root wired
  in (`python -m anima2.live_reflect`) and the Replicate qwen3 client, the actual
  situation prompt sent to the LLM carried
  `Wiki — Mining: Digging ore and stone from mountains and caves — ore types,
  required skill, vein chances, and training route.` — the real `skills/mining.md`
  page's frontmatter description, reached via the "mine miner" query derived from
  the miner's own rewarded episodes — confirmed on every reconsider across a 90-tick
  run (qwen's usual JSON-format flakiness didn't affect this: the evidence is the
  prompt content, not the reply).

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
- ✅ **Chop / Fish** — `skills/harvest.py::Chop`/`Fish`, both `Harvest` subclasses
  (shared probe/reward machinery with `Mine`): `Chop` works a grove of tree statics
  found by the static-map finder (`uomap.py::find_tree_clusters`), switching trees
  on depletion; `Fish` casts at a calibrated water tile and rewards per catch
  (`CATCH_CLILOC`), since Fishing skill itself gains very slowly. Live in the
  village as the lumberjack and fisher professions.
- ✅ **Craft** — `skills/craft.py::Blacksmith`: drives the ServUO CraftGump via
  `GumpResponse` (category → item → repeated MAKE LAST), reward on Blacksmithing
  skill-base gain. Live in the village as the blacksmith profession, forging
  daggers from iron ingots at a staged forge + anvil.
- ⏳ Eat/Heal (bandage→self, or Heal spell) · Bank · Loot (open corpse → PickUp).

### B3 — richer cognition
- ✅ **In-character chatter + `goal:goto`** — `cognition.py::LLMCognition`: each
  reconsider asks the LLM, in character, for a short spoken line (voiced next tick
  by `SpeakPending`) and an optional `goal: goto` clamped to a short walk
  (`max_excursion`, so a hallucinated far coordinate can't march the agent across
  the map). `Profession.planner()` puts `SpeakPending()`/`GoTo()` ahead of the work
  skill so both get consumed. Live in the village via `village.py --chatter`
  (`ThreadedCognition(LLMCognition(...))` per agent).
- 🚧 partially done: the situation prompt already folds in recent episodic memory
  (`ctx.episodes[-5:]`) and reflection `Insight`s (`ctx.insights[-3:]`, the
  "Lessons learned" line — see B1 above), so speech already reacts to what just
  happened. Still open: wiki-grounded speech (no `uowiki` lookups yet), and
  **responding to journal lines aimed at us** specifically (recent journal text is
  in the prompt, but nothing singles out lines directed at the agent).
- ⏳ wider goal vocabulary: only `goto` exists today; still need craft / bank /
  hunt / socialize / explore goals (Phase 3 items, DESIGN.md §10).

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
4. ✅ **Gump support (crafting)** — `GumpResponse`/`GumpView` (0xB0/0xB1) +
   `skills/craft.py::Blacksmith`; the banking gump is still ⏳ (Phase 3 item).
5. ✅ **Memory + wiki + reflection** — episodic memory, the reflection loop, and
   **wiki semantic memory** (workstream B1) are all done.

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
  what the Control plane (`control.py::GmControl`, landed early — see DESIGN.md §10's
  re-baselining note) does; used by every `live_*.py` script and `village.py`.

## Backlog (from review)
- The reflection-loop adversarial-review follow-up round (four fixes to
  `cognition.py`'s `ReflectingCognition`/`LLMReflection`, plus `agent.py`, `memory.py`,
  `live_reflect.py`, and `tests/test_reflection.py`) intentionally skipped doc updates,
  per its own scope note: *"No changes to docs/DESIGN.md, docs/PHASE2.md, or
  CLAUDE.md's stale '20 tests' line — the task scoped the four fixes to code+tests+live
  verification, and CLAUDE.md's test count was already stale before this change
  (pre-existing, out of scope). Did not commit, per instructions."* — resolved by the
  documentation sync pass that produced the current revision of this file (and
  CLAUDE.md/README.md/DESIGN.md, re-baselined to ground truth: 92 tests).
