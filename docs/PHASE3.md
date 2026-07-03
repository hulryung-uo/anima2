# Phase 3 — Work Breakdown

Phase 3 = the economy & interaction loop (DESIGN.md §10). Item 1 needed **no
contract expansion at all**: `Drop`, `PickUp`, `TargetObject`, `GumpResponse`
all already existed (Workstream A closed out in Phase 2), so it's pure
brain-side work — new skill logic plus live geometry calibration. Items 2, 3,
and 4 each needed only a `contract.py` **mirror** of a surface anima-net had
already fully implemented Rust-side (shop/popup for item 2, `corpse_of`/
`corpse_equip` for item 3, `Action::WalkTo`'s non-blocking route driver for
item 4 — see each item's own "Update"/ground-truth note) — not a genuine
expansion, but still `contract.py` work, unlike item 1. **All four items are
now done** — Phase 3 is complete.

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
(HEAD reality after later items landed: 210 — see item 2 below for what grew it).

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
commute. 210 tests green (up from 148), `ruff check .` clean.

## Item 3 — Hunt/loot ✅

**A hunter engages weak creatures, kills them, opens their corpses, and loots
the contents into its pack — repeatedly, with loot provenance.** Closes
PHASE2.md A2's last open row ("corpse (0x2E + container) loot view").

### What landed

- **`contract.py`** — the item's whole contract surface: `CorpseLink`
  (`Observation.corpse_of`, `corpse_serial -> killed_serial`) and
  `CorpseEquip`/`CorpseEquipEntry` (`Observation.corpse_equip`, a corpse's
  worn-item layout) — a straight mirror of `anima-net`'s `json.rs`
  (`corpse_of`/`corpse_equip` observation keys), which — per this item's own
  ground truth, confirmed by reading `anima-core/src/net/game.rs` (0xAF
  `DisplayDeath`, 0x89 `CorpseEquip`) and `world/mod.rs` directly — already
  fully parsed both server packets. **No Rust changes were needed**, the same
  "done in Rust, missing only the Python mirror" shape items 1-2 already hit
  with shop/popup. Unlike `pending_target`/`shop_buy`/`popup` (a single
  "currently open" slot, absent → `None`), `corpse_of`/`corpse_equip` are
  *lists* — several corpses can be tracked at once — so absent/empty is `[]`,
  not `None`; round-trip + backwards-compat tests in `tests/test_contract.py`
  cover both shapes explicitly.
- **`skills/hunt.py::Hunt(Combat)`** — the hunter's one work skill, composed
  on top of `Combat` (reused, not duplicated — mirrors `MineAndSmelt(Mine)`/
  `BlacksmithMarket(Blacksmith)`'s own subclass-and-defer shape) rather than
  re-implementing WarMode/Attack. Phases: **engage** (`Combat.step()`
  unmodified, but this skill's own reward is loot, not combat activity — see
  below) → a kill is detected by scanning `Observation.corpse_of` for a link
  whose `killed` is a serial `Hunt` has ever `Attack`ed (`hunt_attacked`,
  since `Combat._target` always re-targets "nearest hostile" fresh every
  tick — there's no sticky "current target" to key attribution off instead)
  → **locate_corpse** (walk to it, stall-bounded) → **open** (`Use`, then a
  fixed settle wait — a corpse is an ordinary container, **not a gump**, so
  this structurally can't reshow the way a CraftGump can) → **loot**
  (lift-then-place two-step for each item matching a small graphics
  whitelist — Gold plus two verified-but-unexercised gem graphics) →
  **resume** engage. Reward pays only for whitelisted valuables **confirmed
  gained** in the pack, banked across observation lag exactly like
  `MineSmeltDeliver._deliver_step`/`BlacksmithMarket._sell_step`'s own
  confirmed-net-gain accounting — Combat's own per-`Attack` reward (0.05) is
  explicitly zeroed out, never leaking through. **Never** reads
  `Observation.corpse_equip` at all (worn items — a different mechanism,
  out of scope for this MVP per the ground truth) — satisfied simply by not
  touching that field anywhere in the skill. Every stage that can wait on the
  server is bounded (walk stall, a bounded re-`Use` retry if literally
  nothing shows up under the corpse's container, a bounded lift-then-place
  attempt count) — baking in `craft.py::Blacksmith`'s "third dead gump"
  lesson from day one (see the module docstring for why corpse-looting
  structurally can't hit that *specific* failure mode, and what stands in
  for the watchdog instead): a **give-up cooldown** (`hunt_giveup`, 30 ticks
  — the exact constant and mechanism `BlacksmithMarket.giveup_cooldown_ticks`
  uses) stops an abandoned corpse from re-entering the loot queue on the very
  next tick (a livelock in all but name), while a corpse that's genuinely
  fully looted is marked `hunt_looted` **permanently** and never revisited.
- **`profession.py`** — a new `hunter` profession (persona "Ragnar"): bare-
  handed (Wrestling 50, Tactics 50 — live-verified to reliably one-to-two-shot
  a Mongbat, no weapon or healing needed), `work_skill=Hunt`, staged at a
  newly live-calibrated `HUNTING_SPOT`. `Profession` gained a
  `combat_disposition` field (`village.py::_persona_for` now threads it
  through) so the hunter's persona can default to `"aggressive"` — every
  other profession leaves it at `Persona`'s own `"neutral"` default, so this
  is purely additive.
- **`village.py`** — an opt-in `--hunters N` roster knob (default 0): the
  default roster (`--miners 2 --lumberjacks 1 --fishers 1 --blacksmiths 1
  --townsfolk 1`) is untouched unless a caller explicitly asks for hunters.
- **`live_hunt.py`** — the live proof driver (see below), mirroring
  `live_market.py`'s style: wipes the hunting pocket (items *and* mobiles),
  stages one bare-handed hunter on a fresh account with its starting gold
  deleted (so every gold piece it ever holds is provably corpse loot), spawns
  several Mongbats **unpinned** (see "Design decisions" below), then ticks
  the agent and prints a kill/loot-cycle evidence timeline.

### Design decisions

- **Target creature: Mongbat** (`Scripts/Mobiles/Normal/Mongbat.cs`) — 4-6
  hits, `AddLoot(LootPack.Poor)`. Traced the loot-pack chain directly rather
  than assuming: `LootPack.Poor` resolves `Core.SE ? SePoor : Core.AOS ?
  AosPoor : OldPoor` (`Scripts/Misc/LootPack.cs`), and this shard's
  `Config/Expansion.cfg` sets `CurrentExpansion=T2A` — both `Core.SE` and
  `Core.AOS` are false, so it's `OldPoor`: **100% chance, `1d25` gold**, plus
  a 0.02% chance of an unwhitelisted instrument. In practice a Mongbat corpse
  is gold-only, confirmed live (every loot cycle in every proof run below
  yielded a plain gold amount in that range).
- **Not pinned.** Every vendor/banker this package stages elsewhere
  (`VENDOR_SPOT`/`BANKER_SPOT`, `village.py`) gets `[Set CantWalk true` —
  those are *passive* NPCs that need to hold still for a scripted route. A
  Mongbat is the opposite case: `AI_Melee`/`FightMode.Closest` already makes
  it approach and attack on its own once aggroed, and `CantWalk` would
  neuter exactly that (a pinned Mongbat spawned a few tiles off could never
  close the distance at all). Spawned instead within `Combat`'s own
  `engage_range` (10) so the hunter notices them immediately.
- **Bare-handed staging.** Wrestling 50 (+ Tactics 50 for hit chance) proved
  more than sufficient live — every proof run below finished with the
  hunter's HP comfortably above half (worst case 59/80 after fighting 8
  mongbats at once), so no healing skill and no weapon were needed for the
  proof, matching the ground truth's "verify healing is not needed" framing.

### Geometry calibration

`HUNTING_SPOT` needed a check the trade-spot calibration (item 1) didn't:
not just walkable and flat, but **unpopulated** — a spot with pre-existing
wildlife/townsfolk nearby would let `Hunt` (which, via `Combat`, attacks
*any* qualifying-notoriety mobile in range, not specifically mongbats) engage
the wrong thing, muddying `corpse_of` attribution and the "mongbat" proof
narrative alike. Two grid-probed open-Britain-plains candidates (the same
technique `BLACKSMITH_SPOTS` used) both turned out to already have mobiles
within 15-20 tiles — named "innocent"-notoriety NPCs plus unrelated grey
wildlife, evidently inhabited farmland rather than empty ground (live-caught
by adding an explicit `nearby_mobiles` check the trade-spot calibration
never needed). The Minoc-ridge `MINING_SPOTS` pool is all confirmed-**empty**
(mining camps, not settlements) — reused that *area*, but not a pool entry
verbatim: most nooks there are deliberately tight and walled-in (fine for a
stationary miner, bad for a hunter chasing multiple corpses across several
tiles), and reusing an exact `MINING_SPOTS` tuple risked a later collision
with `village.py`'s own miner pool (nothing excludes it there the way
`TRADE_MINE_SPOT` is). Real, collision-checked `Walk` probing (the Z-map +
real-Walk method, item 1) a few tiles past `MINING_SPOTS[2]` (2584, 411 —
itself only 3/8 directions open one step out) found `(2587, 408)` opens into
a genuinely large pocket: 2-4 real tiles in **every** one of the 8
directions before anything blocks, a consistent z=15 throughout (no slope),
and zero mobiles within 20 tiles — ~66 tiles from the trade corridor,
~1100 from `BLACKSMITH_SPOTS`.

### Live proof

`python -m anima2.live_hunt` (needs ServUO on :2594 + the built bridge).
Reran three times (fresh accounts each time, area wiped first) — all three
passed the `MIN_LOOT_CYCLES >= 2` gate, the first two within 50 ticks, a
richer 8-mongbat/4-cycle run (`--mongbats 8 --min-cycles 4`) below chosen as
the primary transcript for its longer, more illustrative timeline:

```
hunter at (2587, 408)
hunter: animahuntfinal serial=16878
GM staged hunter at (2587,408,15): {'Wrestling': 50, 'Tactics': 50}, bare-handed (Wrestling only)
GM deleted the hunter's starting gold (1000) — every gold piece from here on is provably corpse loot
GM spawned 8/8 mongbats around the hunter (unpinned — they aggro in)
  tick    0: SNAPSHOT hunts=0 looted_cycles=0 pack_valuables=0 hp=80/80
  tick   50: SNAPSHOT hunts=0 looted_cycles=0 pack_valuables=0 hp=74/80
  tick   73: KILLED mongbat 0x41f0 -> corpse 0x400174b0
  tick   78: LOOT CYCLE 1/4 complete — corpse 0x400174b0 yielded 23 valuables (pack 0 -> 23); running total looted=23
  tick  100: SNAPSHOT hunts=1 looted_cycles=1 pack_valuables=23 hp=64/80
  tick  113: KILLED mongbat 0x41ef -> corpse 0x400174b3
  tick  118: LOOT CYCLE 2/4 complete — corpse 0x400174b3 yielded 20 valuables (pack 23 -> 43); running total looted=43
  tick  133: KILLED mongbat 0x41f4 -> corpse 0x400174b6
  tick  138: LOOT CYCLE 3/4 complete — corpse 0x400174b6 yielded 9 valuables (pack 43 -> 52); running total looted=52
  tick  150: SNAPSHOT hunts=3 looted_cycles=3 pack_valuables=52 hp=59/80
  tick  184: KILLED mongbat 0x41da -> corpse 0x400174b8
  tick  189: LOOT CYCLE 4/4 complete — corpse 0x400174b8 yielded 14 valuables (pack 52 -> 66); running total looted=66

4 loot cycles demonstrated by tick 189 — stopping early.

--- result ---
mongbats killed:              4
loot cycles (corpse-tied):    4 (need >= 4) -> True
total valuables looted:       66
episodic reward:              66.0

HUNT/LOOT CONFIRMED: engage -> kill -> corpse -> open -> loot, live (4 cycles, all corpse-tied).
```

Each loot cycle is tied to the **specific corpse** being processed when the
pack gain landed (tracking `hunt_queue[0]`'s own turnover tick by tick, not
`hunt_phase`'s coarser engage/loot edges — a single uninterrupted loot run
can drain more than one queued corpse when two mongbats die close together,
and counting by phase transitions alone would under-count that), and every
gold piece is provenance-safe (the fresh account's starting 1000 gold was
GM-deleted before the first kill). Two earlier, smaller reruns
(`--mongbats 6`, default `--min-cycles 2`) both passed at tick 47, confirming
the loop isn't a one-off:

```
tick   22: KILLED mongbat 0x41e1 -> corpse 0x40017470
tick   27: LOOT CYCLE 1/2 complete — corpse 0x40017470 yielded 9 valuables (pack 0 -> 9)
tick   42: KILLED mongbat 0x41e5 -> corpse 0x40017472
tick   47: LOOT CYCLE 2/2 complete — corpse 0x40017472 yielded 25 valuables (pack 9 -> 34)
2 loot cycles demonstrated by tick 47 — stopping early.
```

### Bugs found live

Unlike items 1-2, this item's live testing didn't surface any brain-side
logic bugs in `Hunt` itself (the composed-phase design worked correctly on
the very first live run) — but two real *calibration* mistakes, both caught
before they could taint the proof:

1. **The obvious "open field" candidates weren't actually empty.** The
   first `HUNTING_SPOT` candidates were chosen the same way `BLACKSMITH_SPOTS`
   was — grid-probe for walkable/flat Britain-plains ground — but that
   calibration never checked for *inhabitants*. Both early candidates
   (`(1600, 1700)`, `(1580, 1580)`) turned out to already have nearby
   mobiles (named "innocent" NPCs, unrelated wildlife) within 15-20 tiles,
   live-caught by adding an explicit `nearby_mobiles` check before staging
   anything — worth generalizing: any future "empty pocket" calibration
   should check population, not just terrain.
2. **A reused test account silently carries over state.** An early
   diagnostic run reused an account name from a prior smoke test; the GM's
   "delete starting gold" step printed "deleted 15" instead of "deleted
   1000" — the *character* wasn't fresh, it already held a prior run's loot.
   The safety logic itself was unaffected (it deletes whatever `Gold` items
   it finds, not an assumed amount), but it's why `live_hunt.py` insists on
   a fresh `--hunter-account` per run, same as `live_trade.py`/
   `live_market.py` already document.

### Offline tests

`tests/test_contract.py` — `CorpseLink`/`CorpseEquip` round-trips both
directions; backwards compatibility (an observation dict with neither key at
all still parses, both fields `[]` not `None`, distinguishing this shape
from `pending_target`/`shop_buy`/`popup`'s single-slot `None` convention).
`tests/test_hunt.py` (20 tests) — `Hunt`: pacifist/no-target/no-queue
`can_run` gating, Combat's own attack reward never leaking through (always
0.0), kill attribution (only a corpse whose `killed` we actually attacked
enters the queue), the engage→loot phase switch, walking to a corpse
(stall-bounded, wedge gives up with the cooldown — and does **not**
immediately re-queue during that cooldown, only after it elapses), the
`open` stage's `Use`-once/settle/conditional-retry sequence (including the
"nothing shows up at all" retry-then-accept path), the whitelist (picks
gold, skips a non-whitelisted item, never reads `corpse_equip` even when
populated), the lift-then-place two-step, reward paid only on **confirmed**
pack gain (not on issuing `PickUp`/`Drop`) and banked across the tick a scan
comes up empty, a bounded-attempts give-up on a `Drop` that never lands
(bounces forever), and a multi-corpse queue draining sequentially within one
loot run. `tests/test_profession.py`/`village.py` — the `hunter` profession
is bare-handed and workplace-fixed at `HUNTING_SPOT` (not a `MINING_SPOTS`
member — no pool-collision risk with a real miner), its planner runs `Hunt`,
and `combat_disposition` threads through `_persona_for` without touching any
other profession's default. 245 tests green (up from 210), `ruff check .`
clean.

## Item 4 — A* navigate ✅

**Delegate `GoTo` from greedy tile-by-tile stepping to the bridge's `WalkTo`
route driver**, so agents can reach destinations greedy walking cannot
(around mountains/buildings) — removing the "co-located workplaces only"
constraint this phase's trade loop lives under, in principle. Proving a real
long-commute re-layout (e.g. an actual Britain ↔ Minoc trade route) is
explicitly **out of scope**; the deliverable is `GoTo`-over-`WalkTo` plus a
differential live proof that greedy fails a course A* crosses.

### Update — ground truth's `navigate_to` vs what actually landed

The original scope note (DESIGN.md §10/§11, PHASE2.md A3) pointed at
`Session::navigate_to` — a *blocking* A* helper. What was actually already
built Rust-side, and what this item wires up, is a **different, non-blocking
mechanism**: `Action::WalkTo { x, y }` (`json.rs`) queues a route
(`Session::apply_action`), and `Session::advance_route` — called once per
`pump` by the bridge bin (`anima-net/src/bin/agent.rs`) — advances it at
most one step per call, paced to mirror the play server's own click-to-walk
cadence (400ms/step), with its own deny-detection and `Avoiding` re-route
state (`lib.rs`'s `Route`). This fits the headless brain loop much better
than a blocking call would (the fast loop never blocks on pathfinding) and
needed **no Rust changes** — same "done in Rust, missing only the Python
mirror" shape items 2-3 hit with shop/popup/corpse. Critically, **no route
state is exposed in the observation JSON at all** (confirmed by reading
`lib.rs`/`json.rs` directly: no `route`/`walking`/`arrived` key anywhere) —
the brain has exactly one signal, position deltas across successive
Observations, and had to be designed around that.

### What landed

- **`contract.py`** — `WalkTo(x, y)`, a straight mirror of `json.rs`'s
  `action_from_json` `"WalkTo"` arm: both coordinates required (a missing key
  raises `KeyError`, mirroring `req_u16`'s "must error, not silently default
  to the map origin" discipline), no extra fields (not a single step — a
  route). Round-trip + malformed-input tests in `tests/test_contract.py`.
- **`skills/movement.py::GoTo`** — redesigned around three tiers, `ctx.memory`
  breadcrumbing the active one (`goto_mode`, deliberately *not* wiped on a
  terminal result so it survives as "how did the last attempt finish"):
  1. **`walkto`** (default): emit `WalkTo(target)` **once**, then monitor —
     every tick the tile position is unchanged resets nothing; every tick it
     *does* change resets the stall counter (see "Bugs found live" #1 for why
     this is "did the position change *at all*", not "did distance-to-target
     improve"). No new action is sent while the route is making progress —
     it advances on its own, driven by the body's own `pump` cadence.
  2. **Genuine stall** (`walkto_stall_limit` ticks completely unmoved) is a
     bounded retry: re-issue `WalkTo` (a fresh route has an empty
     deny-blacklist) up to `walkto_max_retries` times — live-calibrated to 3,
     not the original 1 (see "Bugs found live" #2).
  3. **No progress at all** even after every retry falls back to the
     **pre-A* greedy stepping** (`Walk` + `direction_toward`) — this is what
     keeps `GoTo` working under `MockBody`, which has no route driver at all
     (`WalkTo` is silently accepted as a no-op — indistinguishable, from the
     brain's side, from "the route genuinely can't make progress"). Greedy
     fallback is itself `stall_limit`-bounded → FAILURE on a genuine dead end,
     unchanged from the pre-A* skill.
  Arrival is unchanged — an exact tile match (chebyshev 0) — since `WalkTo`'s
  own Rust-side route target is likewise exact; no reason to loosen the
  skill's own contract for this. A new `use_walkto` class attribute
  (default `True`) is an escape hatch: setting it `False` on an instance
  skips the probe entirely and behaves byte-for-byte like the pre-A* skill —
  added so the live differential proof's control run could exercise "pure
  greedy" through the *real* shipped skill rather than a hand-rolled
  reimplementation.
- **`profession.py`** — `NAV_START`/`NAV_DEST`: the calibrated course for
  `live_navigate.py`'s differential proof (see Geometry below).
- **`live_navigate.py`** — the live proof driver (see below): control run
  (greedy `GoTo`) vs. the real `GoTo`, same course, both directions.

The LLM `goal:goto` clamp (`cognition.py::LLMCognition.max_excursion`) is
unchanged — it still caps *distance*, not *mechanism*; a clamped short hop
now simply rides `WalkTo` instead of greedy stepping.

### Bugs found live (both in this item's own new code — none were hypothetical)

1. **"Distance-to-target must improve" is the wrong progress signal — a real
   A* detour needs to move *away* from the target first.** The first cut of
   `GoTo`'s stall detector tracked a best-seen chebyshev-distance watermark
   and treated "no improvement" as the stall condition. Live-caught during
   the geometry-calibration probing that preceded the actual proof script (a
   raw `WalkTo` sent directly, not yet through `GoTo`): routing around the
   calibrated course's spur, distance-to-target *climbed* from 33 to 39 over
   the first ~50 ticks (arcing north around the obstruction) before it
   started closing — a **correct, healthy** route the old detector's own
   arithmetic would have misread as an immediate stall (no improvement
   within a handful of ticks), re-issued into, and abandoned for greedy well
   before it ever got the chance to arrive — defeating the entire point of
   delegating to A*. Fixed by switching the signal to "did the tile position
   change at all since the last tick" — direction-agnostic, tolerates
   backtracking, matches what's actually observable and actually means "the
   route is advancing" (see `GoTo`'s own docstring for the full reasoning).
   Caught and fixed *before* the differential proof script (`GoTo` itself)
   was ever run against real ServUO, purely by reasoning through a probe
   transcript — the "calibrate live first, design the skill around what's
   observable" order this item's own ground truth called for, working
   exactly as intended.
2. **A live-only trap: a real character can enter a tight alcove and then
   get permanently stuck leaving it — even a GM's own body doesn't reproduce
   it.** The differential proof's first calibrated destination reused
   `MINING_SPOTS[2]` (2584, 411) — already documented (item 3's own
   `HUNTING_SPOT` comment) as "only 3/8 directions open one step out". A GM
   character (`GmControl`'s own body) round-tripped through it flawlessly,
   repeatedly — misleadingly reassuring, because **GM movement bypasses
   normal collision denial**, so the GM never exercises the real deny/reroute
   path a normal character needs. The **real, collision-respecting navigator
   character** arrived at that alcove fine (after a lot of wobbling on the
   final approach — itself a hint) but then got **permanently** wedged
   trying to leave: frozen on the exact same tile through 200 ticks and 10
   full fresh-`WalkTo` retries (each with an empty deny-blacklist), never
   moving once — a genuine, reproducible live pathfinding dead end for that
   specific tile, not a transient hiccup `GoTo`'s own retry budget could ever
   paper over (confirmed: even 10 retries, far more than any sane bounded
   skill budget, never helped). Fixed by **not** using that destination —
   swapped to the already-documented, deliberately spacious `HUNTING_SPOT`
   (2587, 408), 3 tiles away ("2-4 real tiles in every one of the 8
   directions before anything blocks") — re-tested with the real navigator
   character and it never stalled more than a single tick either way, both
   directions. General lesson recorded in `profession.py`'s own comment:
   calibrating a destination via the GM body alone doesn't prove it's
   enterable/leaveable by a normal character; a tight single-exit alcove can
   look perfectly fine to a GM and still be a one-way trap for anyone else.
   `GoTo.walkto_max_retries` was separately bumped 1 → 3 while chasing this
   (a real, if smaller, transient stall was also observed once on the *old*
   destination before its alcove nature was understood) — cheap, bounded
   insurance against a genuine transient hiccup, kept even after the real
   fix (the bad destination) landed.

### Geometry calibration

`NAV_START` reuses `MINING_SPOTS[3]` (2551, 420); `NAV_DEST` reuses
`HUNTING_SPOT` (2587, 408) — see `profession.py`'s own `NAV_START`/`NAV_DEST`
comment for the full story (including bug 2 above, which is really a
geometry-calibration lesson as much as a code bug). Both are already
confirmed-empty, already-documented spots reused rather than newly
calibrated ground. `direction_toward` (the greedy technique `GoTo` itself
uses) walked from `NAV_START` toward `NAV_DEST` **never moves at all** —
wedged at the very first attempted step, live-confirmed with real,
collision-checked `Walk` actions — confirming the Minoc-ridge spur between
them has no straight-line line of sight. 36 tiles apart (chebyshev),
comfortably "a few dozen". `WalkTo` crosses it both ways in ~110-121 ticks
(~45-50s at the usual 400ms route cadence), via a real detour arcing north
through ~(2545-2580, 383-420) — distance-to-target climbs well above the
starting distance before it starts closing (the case bug 1 above is built
on).

### Live proof

`python -m anima2.live_navigate` (needs ServUO on :2594 + the built bridge).
A **differential** proof on one course: a control run forces `GoTo` into
pure greedy stepping (`use_walkto = False` — the real shipped skill, not a
reimplementation) and must wedge short of the target; the real, default
`GoTo` must then arrive, and navigate all the way **back** (round trip — the
same multi-cycle lesson every other Phase 3 item's live proof leaned on: a
one-way pass can't tell "works" from "got lucky once", and hides return-leg
state bugs a fresh forward-only run never exercises). Reran twice more after
the `HUNTING_SPOT` fix landed (fresh accounts each time) — both passed
cleanly, `goto_mode` staying `walkto` the entire time (never once falling
back), exact (radius-0) arrival both ways:

```
course: NAV_START=(2551, 420) <-> NAV_DEST=(2587, 408) (chebyshev 36 tiles apart)
navigator: animanav4 serial=16921
GM staged navigator at (2551,420,20)

--- control run: greedy GoTo (use_walkto=False) ---
    [control] tick    0 pos=(2551,420) dist= 36 mode=greedy
    [control] tick    4 pos=(2551,420) dist= 36 mode=greedy
  greedy control result: final=(2551, 420) dist_to_dest=36 goal_cleared=True mode=greedy -> WEDGED (as expected)

--- forward leg: WalkTo-delegated GoTo, NAV_START -> NAV_DEST ---
    [forward] tick    0 pos=(2551,420) dist= 36 mode=walkto
    [forward] tick   50 pos=(2552,393) dist= 35 mode=walkto
    [forward] tick  100 pos=(2579,395) dist= 13 mode=walkto
    [forward] tick  116 pos=(2587,408) dist=  0 mode=walkto
  forward result: final=(2587, 408) dist_to_dest=0 goal_cleared=True mode=walkto -> ARRIVED

--- return leg: WalkTo-delegated GoTo, NAV_DEST -> NAV_START ---
    [return] tick    0 pos=(2587,408) dist= 36 mode=walkto
    [return] tick   50 pos=(2563,384) dist= 36 mode=walkto
    [return] tick  100 pos=(2546,413) dist=  7 mode=walkto
    [return] tick  114 pos=(2551,420) dist=  0 mode=walkto
  return result: final=(2551, 420) dist_to_start=0 goal_cleared=True mode=walkto -> ARRIVED

--- result ---
greedy control wedged short of the target:  True
WalkTo GoTo arrived (forward leg):           True
WalkTo GoTo arrived (return leg, round trip): True

A* NAVIGATE CONFIRMED: greedy GoTo cannot cross this course; WalkTo-delegated GoTo crosses it both ways.
```

(Full per-10-tick timeline in the actual run logs; trimmed here for length —
every sampled tick between forward's tick 0→116 and return's tick 0→114
stayed in `mode=walkto`, confirming the fixed position-changed stall signal
tolerates the real detour without ever triggering the greedy fallback.)

### Offline tests

`tests/test_contract.py` — `WalkTo` round-trip and malformed-input (missing
`x`/`y` → `KeyError`) tests, mirroring `json.rs`'s `req_u16` discipline.
`tests/test_movement.py` (new, 9 tests) — `GoTo`: issues `WalkTo` exactly
once then sends no action while the route is making progress; a detour that
moves *away* from the target before curving back never trips the stall
counter (the bug 1 regression test); bounded stall → retry → (if still no
progress) fall back to greedy, observably via `ctx.memory['goto_mode']`;
`MockBody` compatibility (`WalkTo` silently no-op'd, greedy fallback still
gets there); a changed target mid-flight resets cleanly; greedy-mode
wedge → FAILURE unchanged; `use_walkto=False` behaves byte-for-byte like the
pre-A* skill. `tests/test_agent_loop.py` — the two pre-existing `GoTo`
Agent-level tests (open-terrain arrival, wedged-goal-clears) still pass
under `MockBody`, tick budgets widened to fit the new bounded WalkTo-probe
phase ahead of the greedy fallback, plus explicit `ctx.memory['goto_mode']`
assertions confirming the fallback actually happened rather than just timing
out. 256 tests green (up from 245), `ruff check .` clean.

### Follow-up (not in this item's scope)

`skills/smelt.py::MineSmeltDeliver`'s delivery walk and
`skills/market.py::BlacksmithMarket`'s vendor/banker route walk both still
use their own private greedy `direction_toward` walkers (predating this
item) — migrating them to `WalkTo` would let those trips drop their
manually-curated waypoint routes (`VENDOR_SPOT`/`BANKER_SPOT`) in favor of a
single target point, and is the natural next step toward removing the
"co-located workplaces" constraint for real (a genuine Britain ↔ Minoc
commute). Deliberately out of scope here per the ground truth — this item's
deliverable is `GoTo`-over-`WalkTo` plus the differential proof, not a
re-layout of the village.

---

## References

- DESIGN.md §10 — Phase 3 definition and the re-baselining note.
- PHASE2.md — the 4-lockstep contract-change checklist; the workstream-A/B
  split this phase didn't need. Item 3 above closes A2's last open row; item 4
  closes A3's `navigate` row.
- `anima2/skills/smelt.py`, `anima2/skills/craft.py`, `anima2/skills/market.py`,
  `anima2/skills/hunt.py`, `anima2/skills/movement.py` — the five skills this
  phase extended/added, each carrying its own detailed module/class
  docstrings (`market.py`'s covers the speech-vs-context-menu finding in
  full; `hunt.py`'s covers loot attribution, selection, and the
  bounded-retry discipline in full; `movement.py::GoTo`'s covers the
  distance-vs-position progress-signal finding in full).
- `anima2/profession.py` — `TRADE_MINE_SPOT`/`TRADE_SMITH_SPOT`/`TRADE_HUB`/
  `VENDOR_SPOT`/`BANKER_SPOT`/`HUNTING_SPOT`/`NAV_START`/`NAV_DEST`
  provenance comments; the corrected `blacksmith` profession's
  `items`/`structures`/`work_skill`; the new `hunter` profession.
- `anima2/control.py` — `GmControl.find_mobile_near`'s distance-to-query-point
  fix and `exclude` parameter.
- `anima2/live_trade.py`, `anima2/live_market.py`, `anima2/live_hunt.py`,
  `anima2/live_navigate.py` — the live proof drivers.
