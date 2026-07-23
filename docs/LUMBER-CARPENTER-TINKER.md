# Lumberjack + Carpenter + Tinker economy — build plan

# Build Plan: Lumberjack + Carpenter + Tinker Economy on anima2 (T2A ServUO)

Ordered bricks, each independently live-verifiable like the blacksmith milestones. Earliest bricks de-risk the biggest unknowns (log→board gesture, gump calibration, tinker's ingot dependency) before any trade-pair wiring is built on top of them.

---

## What generalizes cleanly vs. needs new work

**Reuses the blacksmith template almost verbatim (swap graphics/keys/prices):**
- `harvest.py::Chop` — already produces Log piles (0x1BDD), grove-aware, handles PackFull (500497) / NodeDepleted (500493). Done.
- `smelt.py::MineSmeltDeliver` deliver/return/walk machinery — reuse for board delivery: `INGOT_GRAPHICS→BOARD_GRAPHICS (0x1BD7)`, keys `smithy_drop→carpenter_drop`, `miner_home→lumber_home`, `smelt_phase→wood_phase`.
- `craft.py::Blacksmith` gump MAKE loop — same `CraftGump` button formula `1 + type + index*7` for DefCarpentry (title 1044004) and DefTinkering (title 1044007).
- `craft.py::Blacksmith._fetch_step` + `_nearby_ground_ingots` (container is None, radius 6) — reuse for carpenter picking up delivered boards when starved.
- `village.py::has_trade_pair` (miner+smith co-location, counterpart wiring) — template for `has_wood_pair`.
- sell / bank / buy_material / buy_tool phases + the working-capital reserve — reuse directly with new item types and prices.

**Genuinely new work:**
- **`ProcessLogs` skill** — the one new mechanic: an *inverted* Smelt. `Use(equipped Hatchet)` → cursor (server cliloc 1010018) → `TargetObject(log_pile)` → ServUO `Log.Axe()→TryCreateBoards→ScissorHelper` converts the pile 1:1 to Boards at 0 skill. Log must be in pack (else 1062334). Structurally mirrors `smelt.py::_smelt_step` but target order is axe-then-log instead of ore-then-forge.
- **Carpenter has NO structure** — DefCarpentry.CanCraft checks only tool existence/uses (no forge/anvil/workbench proximity). Simplification: carpenter Profession row is `structures=[]`, `items=['DovetailSaw <uses>']` or `['Saw <uses>']`, `skills={'Carpentry':35}`.
- **Board graphics** — single graphic 0x1BD7 (flip 0x1BDA), not the 4 stack-art variants ore/ingots use. Simpler BOARD_GRAPHICS frozenset.
- **New trade-spot calibration** — grove-adjacent `lumber_home`/`carpenter_drop` pair (collision-checked, short greedy no-A* walk) does not exist; FOREST_BASE=(2520,450) groves aren't paired to any carpenter.
- **Live gump-button calibration** for both DefCarpentry and DefTinkering (see below).
- **Tinker tool-delivery loop** — tinker crafts village tools and delivers them to counterparts' drop points on wear-out (replaces buy_tool-from-vendor). No existing template.

---

## The economics (honest answers)

**Carpenter profitable craft-and-sell = Throne** — SBCarpenter pays **24g** for 19 boards (highest of any vendor-buyable carpentry good). Caveat from Report 4: raw boards sell at **2g each** to the same vendor, which beats every crafted item on gold-per-board (Throne = 1.26 g/board). Crafting only wins per-transaction when the vendor's finite gold pool caps raw-board dumping, and for skill gain. So the lumberjack's simplest honest income is **dumping raw boards at 2g**; the carpenter's is **Thrones at 24g/transaction**. Build both, measure which the live vendor gold pool actually rewards.

**Tinker via vendor-selling is essentially unprofitable when ingots cost 5g.** Only marginal wins: **Tongs (1 iron 5g → 7g, +2g)** and **Lockpick (1 iron → 6g, +1g)**. Jewelry LOSES money on bought ingots: GoldRing needs 3 iron (15g) and sells for 13g (−2g). **Jewelry only profits with a free ingot supply**: with mined ingots, GoldRing = 13g pure. Therefore the tinker cannot "live well" on vendor-bought iron — it **must** be paired with a miner (or the existing mining chain) for ingots. Its real village value is not gold but **tool-making**.

**Tool-supply link — yes, the tinker makes the saws and axes.** DefTinkering `#region Tools` crafts from IronIngot: **Saw (4 iron), Hatchet (4 iron), DovetailSaw (4 iron), Froe (2 iron)**, plus TinkerTools (2), SmithHammer (4), Pickaxe (4), Shovel (4), Tongs (1), Lockpick (1). So one tinker can re-supply the whole village's worn tools — closing the loop the blackshain currently fills via buy_tool-from-vendor. But this makes the tinker dependent on iron, i.e. on the miner.

---

## Riskiest unknowns (ordered by how early the plan attacks them)

1. **Live CraftGump button indices** for carpentry (BarrelStaves, Throne) and tinkering (Spoon, tool crafts, GoldRing) — cannot be computed statically; unconditional modern items (LiquorBarrel, GargishWoodenShield, AcademicBookCase, ThemePack deeds, etc.) shift indices on T2A. The Dagger "index-2-not-4" bug is the precedent. → Attacked in Bricks 3 and 7 as pure calibration bricks before any craft loop.
2. **ProcessLogs / ScissorHelper semantics** — whether a multi-log pile converts to N boards in a single axe→target action (expected 1:1, whole pile) or per-log. Affects cycle count and board_threshold. → Attacked in Brick 1, the very first brick, needing no partner or gump.
3. **Axe-on-log is the actual bot gesture** — double-click axe → target log (BaseAxe.OnDoubleClick→BeginHarvesting), verified in code only. → Brick 1.
4. **Trade-spot calibration** — collision-checked open ground for a grove→carpenter greedy walk. → Brick 6.
5. **Vendor gold-pool exhaustion** — whether raw-board (2g) dumping beats Thrones in practice, and whether tinker items sell at all before the vendor runs dry. → Measured live in Bricks 2, 5, 9.
6. **Tinker economic viability / ingot dependency** — confronted head-on in Bricks 8–9; without a free ingot supply the tinker does not self-sustain.
7. **HarvestersAxe availability** — Report 5 treats it as a design option; Report 2 says it's ML+ (not on T2A). Recommend **Hatchet + ProcessLogs** (faithful to smelt template, no charge-depletion management) and treat HarvestersAxe as likely-absent.

**Brick 0 (pre-flight, cheap):** confirm `Core.Expansion == T2A` at runtime on the live shard. Both Report 2 and 3 flag this as the assumption the entire gated-recipe list rests on. One dump; if false, the whole recipe/index analysis changes.

---

## Ordered bricks

### Phase A — Lumberjack self-sustaining (max reuse, de-risks the one new mechanic)

**Brick 1 — ProcessLogs (log→board conversion).** New skill: equip Hatchet (0xF43, layer 2, requires_equipped), `Use(axe)` → cursor 1010018 → `TargetObject(log_pile 0x1BDD)`. Live-verify: a pack of N logs becomes N boards (0x1BD7). Resolves unknowns #2, #3 and the whole-pile-vs-per-log question with zero dependencies. Prefer Hatchet+ProcessLogs over HarvestersAxe.

**Brick 2 — Lumberjack sell → bank → buy_tool.** Reuse blacksmith sell/bank/buy_tool phases. Sell boards at 2g (and/or logs 1g) to SBCarpenter, deposit gold, buy a replacement Hatchet (25g) from vendor when worn. Live-verify: bank gold rises across a full harvest→process→sell→bank→recover cycle. **Milestone: lumberjack "lives well" standalone.** Also gives the first live read on vendor gold-pool behavior (unknown #5).

### Phase B — Carpenter (de-risk gump calibration)

**Brick 3 — Carpenter gump calibration (LIVE).** Pure calibration brick. Give a bot Saw (0x1034) + boards, open DefCarpentry gump (title 1044004), dump the packet, decode: category button for Other (1044294) + **BarrelStaves** item button (5 boards, 0 skill — the 0-skill smoke-test / Dagger analog), and the **Throne** button (19 boards, profit item). Deliverable: verified button IDs. Attacks unknown #1 before the loop exists.

**Brick 4 — Carpenter craft loop.** Reuse `craft.py::Blacksmith` MAKE loop with the Brick-3 buttons and `structures=[]` (no forge/anvil). Smoke-test with BarrelStaves, then craft Throne. Live-verify: boards consumed, item created, Carpentry skill gains.

**Brick 5 — Carpenter sell → bank → buy_material/buy_tool + reserve.** Reuse blacksmith sell/bank/buy phases + working-capital reserve. Sell Throne at 24g to SBCarpenter, bank, buy boards at 3g when starved, buy Saw (15g) when worn. **Milestone: carpenter self-sustains standalone** (profitable even buying boards at 3g and selling Thrones at 24g). Compare live gold/hour of Throne-crafting vs. Brick-2 raw-board dumping (resolves unknown #5 for wood).

### Phase C — Wood trade pair (lumberjack → carpenter)

**Brick 6 — has_wood_pair + trade-spot calibration.** Reuse `has_trade_pair`. Calibrate grove-adjacent `lumber_home` + `carpenter_drop` (collision-checked, short greedy walk) — the TRADE_MINE_SPOT/TRADE_SMITH_SPOT analog (unknown #4). Lumberjack delivers boards via MineSmeltDeliver (BOARD_GRAPHICS, carpenter_drop); carpenter fetches via `_fetch_step`. Live-verify: full unattended pipeline harvest→process→deliver→fetch→craft→sell→bank. **Milestone: lumberjack+carpenter "live well" as a pair** with no vendor material purchases.

### Phase D — Tinker (economically hardest; needs ingot supply)

**Brick 7 — Tinker gump calibration (LIVE).** Give a bot TinkerTools (0x1EB8) + iron, open DefTinkering gump (title 1044007), decode buttons: **SpoonLeft** (1 iron, 0 skill — smoke test), the **tool crafts** (Saw, Hatchet, DovetailSaw, Froe), and the gold item (**GoldRing** and/or **Tongs/Lockpick**). Attacks unknown #1 for tinkering.

**Brick 8 — Tinker craft loop + ingot-supply resolution.** Reuse craft loop with Brick-7 buttons. Resolve the ingot dependency (unknown #6): recommend **pairing the tinker with the existing miner** (reuse the proven miner→smith ingot delivery, retargeted to a tinker drop) rather than buying ingots at 5g. Live-verify: tinker crafts a Saw/Hatchet from delivered ingots.

**Brick 9 — Tinker gold loop (honest viability).** Sell → bank → recover. On free/mined ingots, sell **GoldRing at 13g** (needs a jeweler NPC / SBJewel outlet — confirm live); otherwise fall back to **Tongs (+2g) / Lockpick (+1g)** thin margins to the tinker NPC. Live-verify tinker banks gold and recovers. This brick makes explicit that the tinker self-sustains **only** with a free ingot supply.

**Brick 10 — Tool-supply link (village-closing loop).** New tool-delivery mechanic: tinker crafts village tools (Hatchet→lumberjack, Saw→carpenter, Pickaxe/SmithHammer→miner/smith) and delivers them to counterparts' drop points when their tool wears out, replacing the buy_tool-from-vendor step. Live-verify: a worn lumberjack Hatchet gets replaced by a tinker-crafted Hatchet delivered to the grove. **Milestone: closed, self-supplying village** (mined iron → tinker tools → all trades; free logs → boards → furniture; gold banked by each).

---

**Sequencing rationale:** Phase A ships a fully self-sustaining lumberjack using only reuse + the single new ProcessLogs gesture, and resolves the three cheapest-to-test unknowns (#2, #3, and first read on #5) before anything depends on them. Phase B isolates the highest-risk unknown (#1, gump indices) into a standalone calibration brick before the craft loop. Phase C only wires the trade pair once both halves work solo. Phase D is last because the tinker is economically hardest and structurally depends on an ingot supply — and its final brick (tool delivery) is what lets the whole village drop its vendor-tool purchases and become closed.
---

## Economics reality (live-verified + ServUO-source research)

The three professions do NOT profit equally at NPC prices on this T2A shard
(`BaseVendor.UseVendorEconomy == Core.AOS && !Siege == FALSE`, so vendors pay the
raw `GenericSellInfo` price — no 0.75× economy repricing; only weapons/armor get
an exceptional ×1.25). Two of the three genuinely "live well"; one is a structural
NPC-economy loser:

- **Lumberjack — profitable & self-sufficient.** Free logs (chopped from trees) →
  boards, sold raw @2g. Every board is +2g on a free input. Lives well.
- **Tinker — profitable & self-sufficient STANDALONE (live-proven,
  `scratchpad/tinker_selfsuf.py`, [PASS]).** Iron bought @5g → Tongs sold @7g =
  **+2g per iron** (the vendor pays MORE for the finished tongs than the raw iron
  it sells — a genuine value-add). Starting BELOW the reorder line it banks its
  surplus above an 88g working-capital reserve, BUYS its own iron, and keeps
  forging — the loop runs indefinitely with no GM re-gift. On free/mined iron it's
  +7g per iron. Tongs is the best gold-per-iron item; only Tongs (+2) and Lockpick
  (+1) profit even on bought iron. Lives well.
- **Carpenter — mechanically works, but structurally value-NEGATIVE at NPC prices
  (ServUO source research).** A Board sells RAW @2g; NO carpentry item beats that
  per board. The Throne is 24g / 19 boards = 1.26 g/board (−14g vs. selling the
  boards raw); the least-bad craftable (Exceptional QuarterStaff) is still only
  1.83 g/board (−1g). So (a) the carpenter CANNOT self-provision — buying boards
  @3g to make a 24g throne LOSES 33g/throne and bankrupts it — and (b) even on
  FREE (lumberjack-delivered) boards it earns LESS than selling those boards raw.
  The carpenter can bank gold only on free boards, and is economically dominated
  by just being a second board-seller. This is a property of the ServUO NPC
  economy (crafted goods have no NPC value-add; real carpentry value comes from
  player-to-player sales, which an autonomous agent on a test shard has none of),
  not a fixable item choice. **Human decision (2026-07-23): keep the carpenter a
  real furniture-maker on the lumberjack's free boards** (accepting the per-board
  suboptimality) — so Brick 6 IS built + live-verified below. The carpenter banks
  gold as a genuine carpenter; it just isn't the globally-optimal use of a board.

## Live calibration findings (verified against the shard)

**Brick 1 mechanic (log→board) — VERIFIED:** `Use(axe)` → `pending_target` → `TargetObject(log_pile 0x1BDD)` converts the WHOLE pile 1:1 to Boards (0x1BD7) at 0 skill. 20 logs → 20 boards in one gesture. (`scratchpad/board_probe.py`.)

**Brick 3 carpentry gump — CALIBRATED & craft-confirmed:**
- Tool: **Saw (0x1034)** — `Use(saw)` opens the carpentry CraftGump (gump_id 0x7B28E708, title cliloc 1044004).
- Category buttons (left panel) = `1 + index*7`: **Other=1**, Furniture=8, Containers=15, Weapons=22, Armor=29, Instruments=36, Misc=43, (…), Anvils&Forges=57.
- Item buttons (right panel, per selected category) = `2 + index*7`: first item=2, second=9, third=16, …
- **BarrelStaves** = Other(**1**) → item **2**, costs **5 Boards** (craft-confirmed: boards 50→45, item made). The 0-skill smoke-test item.
- **Throne** = Furniture(**8**) → item button TBD (needs a Furniture-items dump), 19 Boards, sells 24g.
- MAKE_LAST button = **21** (same fixed id as blacksmithy `_button(6,2)`); exit/cancel = 0.
- Button formula confirmed identical to blacksmithy: `1 + type + index*7`.

**Brick 7 tinkering gump — CALIBRATED (categories + Tools items + craft-confirmed):** Tool: **TinkerTools (0x1EB8)** opens the tinkering CraftGump (title 1044007). Category buttons = `1 + index*7`: Jewelry=**1**, Wooden Items=8, **Tools=15**, Parts=22, **Utensils=29**, Misc=36, Assemblies=43, Traps=50 (all live-confirmed). Item buttons within a category = `2 + index*7`. **Tools category items (live-calibrated, page 1):** scissors=**2**(idx0), mortar&pestle=9, scorp=16, tinker's tools=23, **hatchet=30**(idx4, cliloc 1023907 — the lumberjack's tool), draw knife=37, sewing kit=44, **saw=51**(idx7, cliloc 1024148 — the carpenter's tool), dovetail saw=58, froe=65 (page 2 = NEXT PAGE: SmithHammer/Tongs/Lockpick/Pickaxe). **Craft-confirmed:** Tools(15)→scissors(2) consumed **2 iron** (50→48) and produced scissors (0x0F9F) with **NO resource submenu** — the tinker iron-item craft uses the SAME no-submenu direct category→item path as the carpenter (default-iron on a fresh char), so `craft_resource_menu_btn=None` works; the tinker craft skill is structurally identical to `CarpenterCraft`, only `craft_title_cliloc=1044007` + TinkerTools + iron + the Tools buttons differ. Material = IronIngot (jewelry/tools) or Board (wooden items).

**Lumberjack vendors:** SELL boards @ **Carpenter** NPC (SBCarpenter, Board@2g, Log@1g, Throne@24g; SELLS Board@3g, Saw@15g). BUY Hatchet (0xF44) @ **WeaponSmith** NPC (SBWeaponSmith, @25g) — a SEPARATE vendor. So professions need per-capability vendor spots (sell-vendor ≠ tool-vendor).

**Throne (carpenter profit item):** Furniture category = button 8; Throne = item cliloc 1044305, 19 Boards, sells 24g. Its exact item button (2 + throne_index*7) to be calibrated when building the carpenter craft (Brick 4) — BarrelStaves (Other=1→item=2, 5 boards) is the verified 0-skill smoke-test item.

**Brick 2 (lumberjack self-sustaining) — mechanics all live-confirmed:** ProcessLogs (logs→boards) ✓; sell boards @ Carpenter NPC 2g via context-menu Sell ✓; buy Hatchet @ WeaponSmith NPC (0xF44 @ 25g, one of 8 axes it sells — match the SCALAR 0xF44) ✓; bank reuse. Generalization: `sold_graphic`/`owned_tool_graphics`/`offer_graphic`/`vendor_spot_key` class attrs + leaf-func factories, blacksmith byte-identical. 4 capabilities: process_logs, sell_boards, bank_gold, buy_hatchet (build in progress).

**Throne (carpenter profit item) — CALIBRATED + craft-confirmed:** Saw → category Furniture (button **8**) → item (button **58**) = cliloc 1044305 'magincia-style throne' = ServUO `typeof(Throne)`; consumes **19 Boards** (craft-confirmed: 60→41), sells 24g @ Carpenter. (button 51 = 'wooden throne' 1044304 = the cheaper WoodenThrone @ 6g.) Carpentry button formula confirmed identical to blacksmithy: category `1+idx*7`, item `2+idx*7`, MAKE_LAST=21.

**Brick 2 (lumberjack) — DONE, live-verified, pushed (596c23e).** Lumberjack loop ran process_logs (30 logs→30 boards) → sell_boards (30→60g @ Carpenter) → bank_gold, GM-free. Blacksmith byte-identical (offline 103 tests + live B8 gate). The sell/buy-by-graphic + per-vendor-spot generalization is the reusable foundation.

**Endurance test + the drift-stall fix — DONE, live-verified.** A 1-hour+ endurance test of the lumberjack+carpenter pair (`scratchpad/wood_endurance.py` + `endurance_driver.py`, segmented on fresh servers) ran the pair 74 min: **42 thrones crafted+sold, 23 board deliveries, ~1008 gold banked, 0 tick-errors, 0 goal-failures** while active — the economic loop itself is rock-solid. It surfaced two real issues, both fixed: (1) tools WEAR OUT (a plain Saw died at ~69 crafts → an unrecoverable stall with no tinker/vendor wired; the professions stage durable `Saw 999`, and note the Hatchet does NOT take the `999` uses-arg — it needs plain spares); (2) the **intermittent silent DRIFT STALL** — craft readiness required an EXACT `craft_spot` tile, so a carpenter that drifted a tile or two (fetching a delivered pile that landed off-centre, or returning from a vendor/bank trip) went permanently unready and halted with no error. **Fixed** (`ab95834`) by a per-recipe `craft_spot_radius`: blacksmith stays 0 (exact — forge/anvil proximity is load-bearing, byte-identity guardrailed); carpenter/tinker use 3 (a hand tool crafts anywhere). Live-verified: a carpenter drifted 2 tiles off its stand now crafts (was blocked before), 4 tiles still doesn't. 1192 offline tests green, ruff clean. (An in-character write-up of the endurance run was posted to the uotavern tavern board.)

**Brick 10 (the closed village — tool-supply link) — DONE, live-verified.** The tinker forges the village's wooden-working tools on its own Tools gump (Saw btn 51 / Hatchet btn 30, 4 iron each) and DELIVERS one spare to each counterpart's drop slot; a counterpart whose tool breaks FETCHES that spare instead of BUYING one — closing the village (no vendor tool purchases). Six new capabilities on the generalized deliver/fetch machinery: tinker `craft_saw`/`craft_hatchet` (config subclasses of `TinkerTongs`, batch=1) + `deliver_saw`/`deliver_hatchet` (subclasses of `DeliverBoards`, distinct drop keys `carpenter_tool_drop`/`lumber_drop`); lumberjack `fetch_hatchet` + carpenter `fetch_saw` (subclasses of `FetchBoards`). The tool crafts are **demand-gated** (ready only when a counterpart's drop key is wired AND its ground slot is empty) and sit BEFORE the tinker's Tongs income in the registry, so the tinker fills a real tool shortage first and falls back to Tongs otherwise; `fetch_hatchet`/`fetch_saw` sit BEFORE `buy_hatchet`/`buy_saw` so a delivered spare is always preferred over buying. Live proof (`scratchpad/tool_supply.py`, GM-free): a lumberjack staged with logs but a **broken axe and no vendor spots** (cannot buy) gets a working Hatchet from the tinker — craft_hatchet → deliver_hatchet → fetch_hatchet → process_logs, all SUCCESS, pipeline complete at tick 25, lj_axe 0→1, 15 boards produced. The Saw→carpenter path is the same generalized machinery (offline-verified, `tests/test_toolsupply.py`).

**Built with adversarial verification** (a build+verify workflow): four independent reviewers caught three real issues, all fixed before commit — (1) the Saw's ground-flip art 0x1035 was missing (`SAW_GRAPHICS` made flip-safe `{0x1034,0x1035}` everywhere, matching the Hatchet's 0xF43/0xF44 — a "constant copied from a base that hides a divergence", the same class as the title-cliloc bug); (2) the craft-tool gate now fails closed on a missing drop key (matching deliver, so a forged tool is never stranded and the standalone tinker isn't starved of Tongs by the tools' higher priority); (3) the no-oversupply gate is observation-limited, so the tinker must be co-located within sight of the drop slot (the village toolsmith stands at the hub) — the live harness co-locates. 1192 offline tests, blacksmith byte-identical, ruff clean.

**Brick 6 (the wood pair) — DONE, live-verified.** Two new goal-scoped **capabilities** (not work-skills): lumberjack `deliver_boards` (`woodwork.py::DeliverBoards`, mirrors `MineSmeltDeliver`'s deliver/return: `PickUp` a pack board pile → `Drop` it on the GROUND `container=0xFFFFFFFF` at `carpenter_drop`, walk back to `lumber_home`) and carpenter `fetch_boards` (`carpentry.py::FetchBoards`, mirrors `Blacksmith._fetch_step`: `PickUp` a nearby GROUND board pile `container is None` within `PICKUP_REACH` → `Drop` into pack, goal-scoped). Registry: `fetch_boards` is placed BEFORE `buy_boards` so the carpenter prefers the FREE delivered boards over buying (which loses money). A lumberjack wired with `carpenter_drop` but NO `vendor_spot` DELIVERS instead of selling (the sell gate needs a vendor spot). Live proof (`scratchpad/board_trade.py`, TWO capability-driven agents co-located, GM-free): the carpenter — staged with a Saw and **zero boards** — crafts a Throne from boards the LUMBERJACK physically delivered: process_logs (40 logs→boards) → `deliver_boards` → `fetch_boards` → `craft_carpentry` → `sell_furniture` (@24g) → `bank_gold`, all five SUCCESS, full pipeline complete at tick 36. **Milestone: the lumberjack+carpenter live well as a pair** — the carpenter self-sustains on free boards, no vendor material purchases. 1177 offline tests (+7 board-trade), blacksmith byte-identical. (Note: the two-agent live harness is environmentally fragile — a persistent shard + 3 connections + long idle runs occasionally hit a transient disconnect or a one-off stall; proven across multiple runs.)

**Bricks 7-9 (tinker) — DONE, live-verified.** Fourth profession on the generalized craft/market machinery, mirroring the carpenter: profession "tinker" (persona Pim, Tinkering 80, TinkerTools 999) + `skills/tinkering.py` — craft_tongs(TinkerTongs: Tools=15→tongs=86, 1 iron, no submenu, title **1044007**) / sell_tongs(Tongs 0xFBB @ Tinker 7g) / bank_gold / buy_iron(0x1BF2 @5g) / buy_tinker_tool(0x1EBC @7g, deferred fallback). Live loop (`scratchpad/tinker_loop.py`, GM-free): 4 craft cycles (20 iron → 0, 5 tongs/batch) → 4 sell cycles (@7g → 140g) → banked 140g — all three capability kinds fired SUCCESS. Economics (research brick): iron @5g → Tongs @7g = +2g/iron even BUYING iron, best gold-per-iron of any tinker item; on FREE/mined iron it's +7g/iron (a free-iron supply is the tinker's Brick-6 analog). Blacksmith/carpenter byte-identical; 1170 offline tests green (+9 tinker), ruff clean. The `craft_title_cliloc` fix generalized cleanly (1044007 set on TinkerTongs; a cross-profession title-isolation regression test proves the tinker ignores a carpentry-titled gump). Live-calibrated Tools item buttons (page 1): scissors=2, hatchet=30, saw=51 (the lumberjack's/carpenter's tools — Brick 10 tool-supply), tongs=86.

**Bricks 4-5 (carpenter) — DONE, live-verified.** Craft-capability generalization (config-attr + factory, like sell/buy) + carpenter profession + craft_carpentry(Throne: Furniture8→item58, 19 boards) / sell_furniture(Throne@24g) / bank_gold / buy_boards(0x1BD7@3g) / buy_saw(0x1034@15g), all @ the same Carpenter vendor_spot. Live loop (`scratchpad/carpenter_loop.py`, GM-free): crafted 2 Thrones (45→26→7 boards), sold both (@24g each = 48g), banked 48g — all three capability kinds fired SUCCESS. Blacksmith byte-identical (1161 offline tests green).

**Root-cause fix — the craft-gump title cliloc was hardcoded to blacksmithy's.** `craft.py::_craft_gump`/`_observe_evidence` keyed on a module constant `CRAFT_TITLE_CLILOC = 1044002` (ServUO `DefBlacksmithy.GumpTitleNumber`). ServUO gives **each** craft SYSTEM its own title (carpentry **1044004**, tinkering **1044007**), so the saw's carpentry gump opened but was INVISIBLE to the FSM → it Used the saw, then stalled forever at stage `category` waiting for a gump it never recognized (craft_carpentry admitted as current goal, 0 boards consumed, 0 thrones, indefinitely). Fixed config-drivenly: new `Blacksmith.craft_title_cliloc` class attr (default 1044002; `CarpenterCraft` overrides to 1044004), read via `self.` in both detection sites. The offline test had re-encoded the same bug (its mock carpentry gump carried 1044002), which is why it went green while live stalled — fixed the mock + added a cross-profession title-isolation regression test (`test_craft_carpentry_ignores_a_blacksmith_titled_gump`). Tinkering (Bricks 7-10) inherits the same config attr → set 1044007 there.
