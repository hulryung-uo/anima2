# Phase 3 — Work Breakdown

Phase 3 = the economy & interaction loop (DESIGN.md §10). Item 1 needed **no
contract expansion at all**: `Drop`, `PickUp`, `TargetObject`, `GumpResponse`
all already existed (Workstream A closed out in Phase 2), so it's pure
brain-side work — new skill logic plus live geometry calibration. Item 2
needed only a `contract.py` **mirror** of a shop/popup surface anima-net had
already fully implemented Rust-side (see its own "Update" note below) — not a
genuine expansion, but still `contract.py` work, unlike item 1. The remaining
items (hunt/loot, A* navigate) do need real contract work and are still ⏳.

Status legend: ✅ done · 🚧 in progress · ⏳ todo

---

## Item 1 — Inter-agent trade loop (miner → blacksmith) ✅

**A miner hauls its smelted ingots to a nearby smithy and drops them; the
blacksmith picks ground ingots up when its stock runs low and keeps
crafting.** Goods flow between two live agents with no GM gifting sustaining
the loop past the initial stage — live-verified end to end.

### What landed

- **`skills/smelt.py::MineSmeltDeliver(MineAndSmelt)`** — the miner's *one*
  work skill now has a third phase pair (`deliver`/`return`) on top of
  `mine`/`smelt`: once the backpack holds `deliver_threshold` ingots, walk
  greedily (stepwise, `geometry.direction_toward` — same technique
  `skills/movement.py::GoTo` uses, no A*) to a configured smithy drop point,
  lift and ground-drop every ingot pile there (a UO ground drop is two
  packets — `PickUp` then `Drop`, exactly like a pickup; a bare `Drop` with no
  prior `PickUp` is illegal and the server silently ignores it — live-caught,
  see "Bugs found live" below), walk back to where mining started, and
  resume. **Opt-in** via `ctx.memory["smithy_drop"]` (an `(x, y)` tuple the
  village wiring plumbs in exactly like a lumberjack's grove or a fisher's
  water tile — see `village.py`); with no drop point configured, `step()`
  defers straight to `MineAndSmelt.step()`, so the offline demo and every
  pre-Phase-3 test are unaffected. Reward is earned only for ingots
  **confirmed gone from the pack** by the next observation, not merely for
  issuing the `Drop` — same "reward on the observed outcome" discipline
  `_smelt_step` already used for smelting.
- **`skills/craft.py::Blacksmith`** — extended to notice starvation (pack
  ingots below `MIN_INGOTS` — a dagger costs 3 — or the server's own "not
  enough metal" cliloc, 1044037) and, **only when no gump is answerable**,
  walk to a nearby dropped ingot pile and `PickUp` it (lift → `Drop` into the
  backpack, mirroring `Harvest`'s tool-equip two-step) before resuming the
  MAKE loop. Never fights the gump state machine — including the one specific
  case that looks like "no gump open" but isn't: see "Bugs found live" below.
- **`village.py`** — a roster with *both* a miner and a blacksmith now
  co-locates the first of each at a calibrated trade spot
  (`profession.TRADE_MINE_SPOT`/`TRADE_SMITH_SPOT`) and sets the miner's
  `smithy_drop`; any further miners/blacksmiths and rosters without the
  pairing are untouched (draw from the existing separate pools exactly as
  before).
- **`live_trade.py`** — a focused 2-agent live proof (see below), mirroring
  `live_smelt.py`'s style: GM stages a miner (pickaxes, Mining 35, forge) and
  a blacksmith (durable hammer, forge+anvil, Blacksmith 35, deliberately only
  ~15 ingots) co-located, then ticks both agents round-robin and prints a
  timeline of phase/ingot-count/journal evidence for each.

### Geometry calibration (the "GEOMETRY RISK" item)

`MINING_SPOTS` (the Minoc ridge) and `BLACKSMITH_SPOTS` (the Britain plains)
are ~1000 tiles apart — far beyond a greedy (no A*) walk, and real
inter-workplace commutes are explicitly a separate, later Phase 3 item (A*
`navigate`, below). The MVP instead co-locates a smithy right at the ridge's
foot, calibrated **live** via `GmControl` (not just offline map data — see
"Bugs found live" for why that distinction mattered):

1. Z-mapped candidate areas near the ridge by teleporting a GM body across a
   grid (`[Go` — fast, but doesn't check collision) and reading back the
   settled Z, looking for a flat pocket (a mismatch between the smith's stand
   Z and the forge/anvil's Z was a documented Phase 2 failure mode: "a steep
   Minoc slope put forge z=20, anvil z=38").
2. Verified the pocket was actually **walkable** with real, collision-checked
   `Walk` actions (not `[Go` teleports) — a promising-looking flat z=0 pocket
   near `MINING_SPOTS[4]` turned out to be a tiny walled-in alcove barely
   bigger than the stand tile itself once real movement was tried, and (worse
   — see below) had no live ore in reach at all.
3. Landed on `MINING_SPOTS[1]` (2611, 474): confirmed live ore ("You dig some
   iron ore...", Mining 35.0 → 35.1) *and* real open room — due west reaches
   2 tiles without a hitch, unlike most of the ridge (e.g.
   `MINING_SPOTS[0]`, walled in on every side but one — a single `Walk`
   there moves exactly one tile before hitting rock).
4. `TRADE_SMITH_SPOT = (2609, 474)`, forge/anvil placed north/south of the
   smith's own stand tile (not east/west — see below), all three at the same
   Z (20) — confirmed craftable live (Blacksmithing 35.0 → 35.3 over one
   `live_trade.py` run).

The result is recorded as data in `profession.py` (`TRADE_MINE_SPOT`,
`TRADE_SMITH_SPOT`, both with provenance comments), matching the existing
`MINING_SPOTS`/`FISHING_SPOTS` style.

### Bugs found live (all fixed; none were hypothetical)

Live testing surfaced several real bugs — some in the new Phase 3 code,
some latent in Phase 2 code that a bounded, adversarial scenario (a smith
that must run dry and recover) finally exercised:

1. **A bare `Drop` with no prior `PickUp` is illegal** — the miner's first
   delivery implementation issued `Drop` directly from the backpack; the
   server silently ignored it (ingots never left the pack). Fixed by making
   delivery (and the blacksmith's fetch) a proper two-step lift-then-place,
   matching `anima-core`'s actual `PickUp`(0x07)/`Drop`(0x08) packet pair.
2. **`DAGGER_BTN` pointed at the wrong CraftGump item** — decoding the actual
   gump layout live (`{xmfhtmlgumpcolor ... 1023921 ...}`, cliloc 1023921 =
   "dagger") showed Dagger sits at index 2 within the Bladed group on this
   ServUO, not index 4 (a **Kryss** — cliloc 1025121 — a much pricier item).
   This bug predates Phase 3; it was invisible before because *any*
   successful craft still pays the Blacksmithing skill-gain reward, so
   crafting the wrong item was silent until a bounded ingot supply actually
   ran out against it.
3. **A truly-out-of-metal MAKE LAST press reopens the *same* gump forever**
   — `CraftGump.CanCraft` fails synchronously (no craft to animate) and
   `SendGump`s the failure message baked into the reshown gump's own layout,
   not as a separate journal line. `gump is None` — the fetch logic's
   original gate — never comes true again once truly starved, so the smith
   could never notice a delivered pile. Fixed by also recognizing that one
   specific "stuck" gump (its layout contains the 1044037 cliloc) as
   fetch-eligible.
4. **`SmithHammer` breaks silently** — a freshly `[AddToPack SmithHammer`
   tool gets a *random* 25–75 uses and deletes itself on depletion (no
   error). `SmithHammer <uses>` invokes its `[Constructable] SmithHammer(int
   uses)` overload for a durable tool instead — now used everywhere a smith
   is staged (`profession.py`, `live_trade.py`).
5. **An anvil is a solid, blocking static** — the blacksmith profession's
   original forge/anvil offsets were east/west of the stand tile; for a
   miner approaching from the east, the anvil sat squarely on the direct
   path and sealed a 1-tile-wide corridor entirely. Reoriented to north/south
   (`profession.py`'s `Blacksmith` `structures`) — clear for any horizontal
   approach, and just as valid for a solo, unpaired smith.
6. **Repeated live testing itself pollutes the shard** — every `[Add Forge`/
   `[Add Anvil` from an earlier debug run leaves the item behind; enough
   accumulated debris on the delivery corridor reproduced bug 5 with no code
   change involved. `[WipeItems` (bounding-box GM command, same shape as the
   existing `[WipeNPCs` `command_area` helper) clears it; worth remembering
   for future live iteration on this shard.
7. **A fetch that leaves the smith out of forge/anvil range wedges on a dead
   button forever** — `Blacksmith._fetch_step` can pull the smith several
   tiles from its stand (a pile up to `PICKUP_RADIUS` out), and once back the
   next MAKE press fails `DefBlacksmithy.CanCraft`'s `CheckAnvilAndForge`
   (2-tile range), which — exactly like bug 3's "not enough metal" case —
   re-`SendGump`s the *same* gump with the failure (cliloc **1044267**,
   "You must be near an anvil and a forge to smith items.") baked into its
   layout, not a separate journal line. Neither `stuck_gump`
   (`NOT_ENOUGH_METAL_CLILOC`) nor `starved` (plenty of metal — this isn't a
   supply problem) ever recognized it, so the smith pressed MAKE LAST on a
   button that could never succeed — caught live, mid-verification, as a
   smith wedged off-anvil with a full pack. Fixed in the same commit as the
   rest of this item (`01d78e8`) by recognizing that gump
   (`craft.py::PROXIMITY_CLILOC`) unconditionally — before `starved` is even
   computed — and routing it to the same walk-back-to-stand step the ingot
   fetch already had (`_fetch_return_step`), self-healing if the walk itself
   wedges (retries the craft anyway rather than freezing). This bug (and its
   fix) predates this document's original write-up but was missing from this
   list — added on the Phase 3 item 2 documentation sweep.

### Live proof

`python -m anima2.live_trade` (needs ServUO on :2594 + the built bridge).
Verified run (`--deliver-threshold 8 --smith-ingots 15`, fresh accounts, area
wiped of prior debris first):

```
GM staged miner at (2611,474,20) + forge
GM staged blacksmith at (2609,474,20) + forge/anvil, 15 starting ingots
  tick    0: SNAPSHOT miner phase=mine  pack_ingots=0 | smith pack_ingots=15 ground_ingots=0 Blacksmithing=35.0
  tick   12: [smith] pack ingots dropped below 3 (3 -> 0) — about to stall
  tick   25: SNAPSHOT ... smith pack_ingots=0 ... Blacksmithing=35.3        # 5 daggers crafted, then stalled
  tick   40: [miner] phase mine -> smelt (pack ingots=0)
  tick   46: [miner journal] You smelt the ore removing the impurities and put the metal in your backpack.
  ...                                                                       # mine/smelt cycles, ore is scarce
  tick  150: [miner] phase smelt -> mine (pack ingots=9)
  tick  152: [miner] phase mine -> deliver (pack ingots=9)
  tick  157: [miner] phase deliver -> return (pack ingots=0)
  tick  157: [miner] DELIVERED — dropped ingots at the smithy
  tick  159: [smith] pack ingots 0 -> 9 (picked up delivered ore; 0 still on the ground nearby)
  tick  160: [miner] phase return -> mine (pack ingots=0)
  tick  162: [smith] CONSUMED delivered metal on a new craft (pack ingots 9 -> 6)

full loop demonstrated by tick 162 — stopping early.

--- result ---
smith stalled (ran low on metal):     True
miner delivered ingots to the smithy:  True
smith picked delivered ingots up:      True
smith crafted again from that metal:   True
miner episodic reward: 12.3  smith episodic reward: 0.3

TRADE LOOP CONFIRMED: goods flowed miner -> ground -> blacksmith -> craft.
```

Every ingot the smith crafted with *after* the stall (tick 12 onward) traces
back to the miner's own delivery at tick 157 — no GM re-gifted anything after
the initial staging.

### Offline tests

`tests/test_smelt.py` — `MineSmeltDeliver`: threshold trigger, no-trigger
mid-mining-swing, walk-until-arrival, two-step pickup-then-drop on arrival,
reward only on pack-confirmed loss (not on issuing the action), phase
transitions (`deliver` → `return` → `mine`), a wedged leg still advances the
phase, and full backwards-compatibility (no `smithy_drop` ⇒ byte-for-byte
`MineAndSmelt`). `tests/test_craft.py` — `Blacksmith` fetch: ignores a ground
pile with plenty of metal or a gump still open, walks/picks-up/drops when
starved and a pile is in range, the "stuck" not-enough-metal gump still
triggers a fetch, a wedged pile falls back to crafting, the server's cliloc
is an alternate trigger. 148 tests green (up from 118), `ruff check .` clean
(HEAD reality after later items landed: 205 — see item 2 below for what grew it).

---

## Item 2 — Bank + buy/sell ✅

**The blacksmith sells its crafted daggers to a vendor and banks the
proceeds: `ore -> ingot -> dagger -> gold -> bank`, live, closing the economy
loop item 1 started.**

### What landed

- **`contract.py`** — the 4-lockstep mirror this item started with, plus one
  more surface the live work below turned up as *also* needed:
  - `ShopBuy`/`ShopBuyEntry`, `ShopSell`/`ShopSellItem` observation
    dataclasses (`Observation.shop_buy`/`.shop_sell`, absent → `None` exactly
    like `pending_target`) and `BuyItems`/`SellItems` actions — mirrors
    anima-net's `json.rs` (`shop_buy_json`/`shop_sell_json`/
    `shop_items_from_json`), which — per this item's original ground truth —
    already had the 0x74/0x9E parsing and the shop observation surface
    landed; only the `contract.py` mirror was missing (see "Update" note
    below on a doc-drift this corrects).
  - `PopupMenu`/`PopupEntry` observation (`Observation.popup`) and
    `PopupRequest`/`PopupSelect` actions — **not in this item's original
    scope**, added after live testing proved the speech-keyword design in
    the ground truth doesn't work on ServUO at all (bug 1 below). Also
    already fully implemented Rust-side (0xBF/0x13 request → 0xBF/0x14
    `PopupMenu` → 0xBF/0x15 select, with its own Rust unit tests) — the same
    "done in Rust, missing only the Python mirror" shape as shop_buy/sell,
    just not flagged as needed until live testing forced the redesign.
  - Round-trip tests for both surfaces in `tests/test_contract.py`
    (`test_action_json_roundtrip` extended, plus dedicated shop/popup tests);
    absent-key backwards compatibility covered explicitly.
- **`skills/market.py::BlacksmithMarket(Blacksmith)`** — adds `sell`/
  `sell_return` and `bank`/`bank_return` phases on top of `Blacksmith`'s own
  MAKE-loop/fetch state machine, following `MineSmeltDeliver`'s composed-phase
  pattern: **opt-in** via `ctx.memory["vendor_spot"]`/`["banker_spot"]` — with
  neither configured, byte-for-byte `Blacksmith`. A trip only starts between
  craft cycles (no gump open, not mid an ingot-fetch trip). Selling walks to
  the vendor, opens its **right-click context menu** and selects "Sell"
  (cliloc-matched, not text-matched — see bug 2), answers the resulting 0x9E
  SellList with `SellItems` for **dagger entries only** (never the smith's own
  tongs/hammer/remaining ingots, even though the vendor's `InternalSellInfo`
  buys those too), and rewards on gold **confirmed gained** in the pack.
  Banking opens the banker's context menu and selects "Bank" (calls
  `BankBox.Open()`), waits a settle period, then lift-then-places the pack's
  gold pile into the bank box — same two-step `PickUp`→`Drop` shape delivery
  and fetch already established — rewarding on gold **confirmed gone**.
  `vendor_spot`/`banker_spot` accept either a plain `(x, y)` point or a
  `[(x, y), ...]` **route** (walked leg by leg, intermediate waypoints
  requiring exact arrival) — needed because of the trade smithy's own
  geometry (bug 6). Every wait/walk step is stall- or timeout-bounded
  (`_walk_route`/`_market_walk_toward`, `FIND_MOBILE_TIMEOUT`, `ASK_RETRY`,
  `SELL_CONFIRM_TIMEOUT`), so a missing/wandered-off/dead vendor or banker
  gives up and resumes crafting rather than freezing the MAKE loop.
- **`profession.py`** — `TRADE_HUB`, `VENDOR_SPOT`, `BANKER_SPOT`: live-
  calibrated two-leg routes through the trade corridor's middle-tile hub (see
  Geometry below), with the same kind of provenance comment as
  `TRADE_MINE_SPOT`/`TRADE_SMITH_SPOT`. The `blacksmith` profession's work
  skill swaps from `Blacksmith` to `BlacksmithMarket` unconditionally (it's a
  strict superset when unconfigured — mirrors `MineSmeltDeliver` becoming the
  miner's *one* work skill in item 1) — solo blacksmiths on `BLACKSMITH_SPOTS`
  are unaffected, byte for byte.
- **`village.py`** — when the miner+blacksmith trade pairing is active (same
  condition as item 1), also stages a `Blacksmith` vendor and a `Banker` near
  the trade smithy (`VENDOR_SPOT`/`BANKER_SPOT`'s final waypoints), pins each
  with `[Set CantWalk true` right after identifying it (bug 5), and sets the
  paired blacksmith's `vendor_spot`/`banker_spot` memory. Non-paired rosters
  are untouched.
- **`control.py`** — two `find_mobile_near` fixes this item's live testing
  forced (bug 4): sorts by distance to the *queried* tile, not to the
  observing GM's own position; a new `exclude` parameter drops one serial
  (e.g. the character being staged) from consideration.
- **`live_market.py`** — the live proof driver (see below), mirroring
  `live_trade.py`'s style: wipes the area, stages one blacksmith + a vendor +
  a banker (add → find → pin **one NPC at a time** — bug 5), then ticks the
  agent and prints a phase/dagger/gold/bank evidence timeline.

### Geometry calibration

`TRADE_SMITH_SPOT` (item 1) turned out to sit at the closed end of a
single-tile-wide corridor with **exactly one** real (collision-checked
`Walk`, not `[Go` teleports — the same distinction item 1's own geometry
notes flagged) open exit: due east, through `(2610, 474)` to
`TRADE_MINE_SPOT`. Every other direction from the smith's own stand tile is
walled rock (confirmed live by probing all 8 directions). But `(2610, 474)`
— the corridor's middle tile — turned out to be a small open **hub**, not
just a pass-through: real single-`Walk`-step probes from it found
N/NE/E/SE/S/W all open (only SW/NW blocked). A vendor/banker placed on one of
those extra exits is unreachable by a single straight-line `direction_toward`
walk from the smith's own stand tile (the very first computed direction —
e.g. NE for a tile north of the mine spot — is exactly the rock the corridor
is walled in by), which is why `vendor_spot`/`banker_spot` became **routes**:
`VENDOR_SPOT = [TRADE_HUB, (2610, 473)]` (hub, then due north — both
confirmed-open single steps) and `BANKER_SPOT = [TRADE_HUB, (2610, 475)]`
(hub, then due south), landing on distinct tiles so the two staged NPCs don't
overlap. Both `z=20`, matching the smith/forge/anvil/mine spot.

### Bugs found live (all fixed; none were hypothetical)

This item's live testing found the ground truth's central assumption
(speech-keyword vendor/bank interaction) doesn't work at all, plus several
more issues once the redesign was live-tested against the real corridor
geometry and a wandering NPC:

1. **Speech keywords are unreachable from `Say` — the ground truth's whole
   design didn't work.** ServUO's keyword matching
   (`SpeechEventArgs.HasKeyword`, what `VendorAI.cs`'s "*vendor sell*" and
   `Banker.cs`'s "*bank*" cases check) only ever sees a non-empty `Keywords`
   array when the **client** sends an **encoded** speech packet with
   pre-matched keyword IDs embedded. Real UO clients do this transparently —
   confirmed by reading ClassicUO's `Send_UnicodeSpeechRequest`/
   `Send_ASCIISpeechRequest`, both of which call `Speeches.GetKeywords(text)`
   (a `speech.mul` lookup) before sending *any* line, encoding it if it
   matches a known phrase. `anima-core`'s `build_say`/`build_unicode_say` do
   no such lookup (by design — no `speech.mul` parsing), so
   `AsciiSpeech`/`UnicodeSpeech` (`Server/Network/PacketHandlers.cs`) always
   receive an **empty** keyword array from us — `HasKeyword` can never be
   true, for any phrase. Live-caught directly: an early version said "vendor
   sell"/"bank" (visible in the smith's own journal — unmatched speech is
   still echoed back) but the vendor/banker never responded, ever. Fixed by
   switching to the **right-click context menu** (`PopupRequest`/
   `PopupSelect`, 0xBF/0x13→0x14→0x15) instead — `BaseVendor`'s
   `VendorSellEntry` and `Banker`'s `OpenBankEntry` are ordinary context-menu
   entries, unaffected by the speech-encoding gap, and the Rust side already
   had the full round trip implemented (just not mirrored into `contract.py`
   — the same lockstep gap as shop_buy/shop_sell).
2. **This ServUO negotiates the *legacy* popup cliloc layout.** ServUO's
   `ContextMenuEntry` constants (`VendorSellEntry`: `6104`; `OpenBankEntry`:
   `6105`) are the *modern*-format ids; `anima-core`'s `parse_popup` correctly
   reconstructs the *legacy* format's cliloc (`+3,000,000`) when the server
   sends that layout — confirmed live by decoding an actual 0xBF/0x14 reply
   (`PopupEntry.cliloc`), not assumed: this shard's negotiated entries read
   `3006103`/`3006104`/`3006105`, not `6103`/`6104`/`6105`. `market.py`'s
   `SELL_CLILOC`/`BANK_CLILOC` are the live-decoded values — same
   "live-verified against this ServUO" discipline as `craft.py`'s
   `DAGGER_BTN`, not a guess from the C# source alone.
3. **The deployed bridge binary was stale.** `default_bridge_path()`
   (`ipc_body.py`) prefers `target/release/anima-agent` over `debug` when
   both exist; the release binary on disk predated the anima-net commit that
   added `shop_buy`/`shop_sell`/`popup`/`opl` to the observation JSON (its
   `observe()` reply was silently missing those keys entirely — no error, just
   absent fields, which read as `None` and looked exactly like "the feature
   doesn't work" until traced back to the binary's own build timestamp vs.
   the commit log). Not a Rust *code* bug — a build/deployment staleness gap
   this item's live testing happened to catch. Fixed with `cargo build -p
   anima-net --release` (no source changes).
4. **`GmControl.find_mobile_near` sorted by the wrong distance.** It compared
   `m.distance` (relative to the *observing* body's own position) instead of
   distance to the *queried* `(x, y)` tile, so a `[Add`-ed vendor's search
   could silently resolve to some other mobile that merely happened to be
   close to *us* — including, worst case, the character being staged itself
   (its own stand tile sits within the default 1-tile search radius of the
   nearby banker spot too). Fixed to sort by chebyshev distance to the query
   point; added an `exclude` parameter for exactly that self-collision case.
5. **A freshly-added vendor wanders — and staging both NPCs before pinning
   either compounds it.** `BaseVendor` runs `VendorAI`, whose
   `DoActionWander` roams it when idle (real UO shopkeepers pace their shop)
   — enough to drift a freshly `[Add`-ed NPC out of the market skill's fixed
   route/search radius within the handful of ticks before a script gets
   around to identifying and freezing it. `[Set CantWalk true`
   (`Server/Mobile.cs`) pins it without touching its ability to trade/bank.
   The *first* attempt at this — add both NPCs, *then* find-and-pin both —
   still failed intermittently: the window between adding the vendor and
   pinning it was long enough (each `[Add`/`find_mobile_near` round trip is
   several 400ms pumps) for it to wander into the *banker*'s search radius,
   so `find_mobile_near(banker_spot)` (even with the bug-4 fix) sometimes
   resolved to the vendor. Fixed by adding, finding, and pinning **one NPC at
   a time** (`live_market.py`, `village.py`) — by the time the second NPC is
   searched for, the first is already frozen and out of the way.
6. **The trade corridor's own geometry blocks a straight-line walk to
   either NPC.** See "Geometry calibration" above — `direction_toward`
   (`_market_walk_toward`, same technique `GoTo`/`MineSmeltDeliver` use)
   picks one straight-line direction toward the *final* target and has no
   fallback when that's blocked, even if a different route would work (no
   A* — DESIGN.md §10 item 4 is the eventual real fix). Fixed by giving
   `vendor_spot`/`banker_spot` a **route** shape (`_walk_route`, `_ARRIVED`
   sentinel) — a manually curated `[hub, final]` waypoint list walked leg by
   leg, with exact-arrival required at every waypoint but the last, so each
   leg's `direction_toward` computation lands on a real confirmed-open edge.
   The return trip walks the same route in reverse.

### Update — item 1's own note corrected

Item 1's write-up above said the miner→blacksmith loop needed "no contract
changes" — still true for item 1. But this item's own original scope note
(now superseded by the section above) understated what already existed
Rust-side: it said only `BuyItems`/`SellItems` existed; in fact the 0x74/0x9E
*parsing* and the full `shop_buy`/`shop_sell` **observation** surface
(`json.rs`) were also already implemented, just — like the actions — not yet
mirrored into `contract.py`. That mirror (plus the popup surface bug 1 forced)
is exactly what this item's `contract.py` work closed.

### Live proof

`python -m anima2.live_market` (needs ServUO on :2594 + the built-and-**fresh**
bridge — see bug 3). Verified run (fresh account, area wiped, default
thresholds — `--sell-threshold 3 --bank-threshold 20`):

```
smith at (2609, 474), vendor at (2610, 473), banker at (2610, 475)
smith: anima11 serial=16706
GM staged smith at (2609,474,20) + forge/anvil, 30 starting ingots
GM staged vendor at (2610,473,20) serial=16769; banker at (2610,475,20) serial=16770 (both pinned)
  tick    0: phase craft -> sell  (pack daggers=5 gold=1000)
  tick    0: SNAPSHOT phase=sell       daggers= 5 gold=1000 Blacksmithing=35.0
  tick    5: phase sell -> sell_return  (pack daggers=1 gold=1040)
  tick    5: SOLD — 4 dagger(s) for 40 gold (pack gold 1000 -> 1040)
  tick    7: phase sell_return -> craft  (pack daggers=1 gold=1040)
  tick   10: phase craft -> bank  (pack daggers=1 gold=1040)
  tick   13: CRAFTED — first dagger(s) in the pack (daggers=2)
  tick   13: Blacksmithing 35.0 -> 35.1
  tick   14: [journal] Bank container has 1 items, 1 stones
  tick   18: phase bank -> bank_return  (pack daggers=2 gold=0)
  tick   18: BANKED — 1040 gold deposited (pack gold 1040 -> 0; bank box now shows 1040 gold)

full chain demonstrated by tick 18 — stopping early.

--- result ---
crafted daggers:  True
sold to vendor:   True
banked the gold:  True
episodic reward: 1080.0

ECONOMY CLOSED: ore -> ingot -> dagger -> gold -> bank, live.
```

Every stage is independently confirmed: **crafted** (Blacksmithing 35.0 →
35.1, a real MAKE-loop success, not just the account's starting daggers sold
at tick 5), **sold** (`SellItems` answered with dagger-only entries; pack
gold rose by exactly the sale price, daggers dropped), **banked** (pack gold
fell to 0; the bank box's own container contents show the deposit). Reran
multiple times across the CantWalk/ordering fixes (bugs 4–5) landing —
several earlier runs demonstrated crafting + one of sell/bank but stalled on
the other until those fixes landed; post-fix runs (this one included)
consistently complete the full chain within ~20–30 ticks.

### Offline tests

`tests/test_contract.py` — `ShopBuy`/`ShopSellItem`/`PopupMenu` round-trips
both directions, `BuyItems`/`SellItems`/`PopupRequest`/`PopupSelect` action
round-trips, and backwards compatibility (an observation dict with none of
these keys still parses, all four fields `None`). `tests/test_market.py` (43
tests) — `BlacksmithMarket`: opt-in backwards compatibility (byte-for-byte
`Blacksmith` unconfigured); sell/bank threshold triggers and the never-hijack-
a-gump/never-abandon-a-fetch guards; vendor/banker mobile discovery (found,
waits, gives up); the popup request/select cycle (asks, waits, re-asks, picks
the right cliloc, bails if the entry's missing); the SellList answer
(dagger-only filtering, never tools/ingots); confirm/timeout/wedge-give-up on
every phase; reward paid only on the confirmed outcome (gold gained for a
sale, gold gone for a deposit) including no-double-pay-on-bounce; and the
multi-leg route mechanism (heads for the first waypoint not the final target,
requires exact arrival on intermediate legs, only the usual reach radius on
the last one, return trip reverses the route, a plain-tuple route is
byte-for-byte unchanged, the route's own leg index stays pinned for the whole
trip rather than resetting mid-interaction, and a wedged return trip doesn't
leak its leg index into the next trip's); and the sell/bank retrigger backoff
that stops a permanently missing vendor/banker from turning into a permanent
commute. 205 tests green (up from 148), `ruff check .` clean.

## ⏳ Item 3 — Hunt/loot

Needs corpse container observation (PHASE2.md A2: "corpse (0x2E + container)
loot view") plus a loot skill (open corpse → `PickUp`, reusing the same
lift/place two-step this phase's delivery/fetch logic established).

## ⏳ Item 4 — A* navigate

Delegate `GoTo` (and `MineSmeltDeliver`'s own greedy walker) to anima-net's
`Session::navigate_to` (A* from anima-core's `path` module — already landed,
just not wired to a bridge command). Would remove the "co-located workplaces
only" constraint this phase's trade loop lives under, and turn the Britain
↔ Minoc commute (or any real inter-workplace trip) into a real possibility
instead of an out-of-reach distance.

---

## References

- DESIGN.md §10 — Phase 3 definition and the re-baselining note.
- PHASE2.md — the 4-lockstep contract-change checklist (item 3 above will
  still need it); the workstream-A/B split this phase didn't need.
- `anima2/skills/smelt.py`, `anima2/skills/craft.py`, `anima2/skills/market.py`
  — the three skills this phase extended, each carrying its own detailed
  module/class docstrings (`market.py`'s covers the speech-vs-context-menu
  finding in full).
- `anima2/profession.py` — `TRADE_MINE_SPOT`/`TRADE_SMITH_SPOT`/`TRADE_HUB`/
  `VENDOR_SPOT`/`BANKER_SPOT` provenance comments; the corrected `blacksmith`
  profession's `items`/`structures`/`work_skill`.
- `anima2/control.py` — `GmControl.find_mobile_near`'s distance-to-query-point
  fix and `exclude` parameter.
- `anima2/live_trade.py`, `anima2/live_market.py` — the live proof drivers.
