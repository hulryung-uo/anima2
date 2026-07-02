# Phase 3 — Work Breakdown

Phase 3 = the economy & interaction loop (DESIGN.md §10). Unlike Phase 2, this
phase needed **no contract expansion**: `Drop`, `PickUp`, `TargetObject`,
`GumpResponse` all already existed (Workstream A closed out in Phase 2), so
item 1 below is pure brain-side work — new skill logic plus live geometry
calibration. The remaining items (bank/buy-sell, hunt/loot, A* navigate) do
need contract work and are still ⏳.

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
is an alternate trigger. 137 tests green (up from 118), `ruff check .` clean.

---

## ⏳ Item 2 — Bank + buy/sell

Needs contract expansion — `Buy`/`Sell`/`ContextMenu`/a banker gump — follow
the 4-lockstep checklist in PHASE2.md (anima-core → anima-net → anima2
`contract.py` → tests, live no-regression run). Rust side already has
`BuyItems`/`SellItems` (PHASE2.md workstream A1); not yet mirrored in
`contract.py`.

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
- PHASE2.md — the 4-lockstep contract-change checklist (items 2–3 above will
  need it); the workstream-A/B split this phase didn't need.
- `anima2/skills/smelt.py`, `anima2/skills/craft.py` — the two skills this
  phase extended, each carrying its own detailed module/class docstrings.
- `anima2/profession.py` — `TRADE_MINE_SPOT`/`TRADE_SMITH_SPOT` provenance
  comment; the corrected `blacksmith` profession's `items`/`structures`.
- `anima2/live_trade.py` — the live 2-agent proof driver.
