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
