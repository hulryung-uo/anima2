# The Sword-Warrior — a strong armored hunter that earns and buys

A post-Phase-6 `/goal`: *raise a warrior with Swordsmanship / Healing / Tactics
that hunts creatures, earns money, and buys weapons with it — bandages included —
the strongest possible warrior earning as much money as possible.*

Unlike the lumberjack/carpenter/tinker (capability-driven crafters), the warrior
is a **work-skill** profession (`work_skill=Hunt`) whose economy runs as a separate
capability leg between hunts. Almost everything it needs already existed and was
reused **unchanged**; the warrior is a thin layer of new pieces on proven machinery.

## What already existed (reused unchanged)

- **`Combat` / `Hunt`** (`skills/hunt.py`) — engage hostiles (WarMode + Attack),
  loot their corpses (gold-only whitelist), corpse→kill attribution. Weapon-agnostic.
- **`Survive`** (`skills/survival.py`) — a bandage-heal reflex (interrupts the goal,
  flees + bandages below 40% HP). Weapon-agnostic.
- **`BuyToolCapability` / `BankGold`** (`skills/market.py`) — the generalized
  vendor-buy FSM (PopupRequest → PopupSelect(Buy) → BuyItems) and gold banking,
  already live-verified for the smith/lumberjack/tinker.

On ServUO the server picks the **combat skill from what is WORN**: bare hands →
Wrestling; a sword in the one-handed layer → **Swordsmanship**. So the only new
fast-loop piece a swordsman fundamentally needs is to *wear its blade*.

## What's new (`skills/warrior.py`, `profession.py`, `capabilities.py`)

1. **`EquipWeapon`** — a pre-work reflex that wields the best owned sword (Katana =
   best sustained DPS on this T2A shard) via the two-packet `PickUp`→`Equip` idiom
   at `WEAPON_LAYER=1`. Inert once the best blade is worn.

2. **`EquipArmor`** — wears a full plate suit, each piece at its own body layer.
   ServUO places a piece at *its own* tiledata layer and **rejects the equip if that
   layer is occupied** — and a fresh char wears starter clothing (pants at the Pants
   layer, which `PlateLegs` wants). So `EquipArmor` first **strips** a blocking
   non-plate garment into the pack, then equips the plate. A layer the server keeps
   refusing is **abandoned after a few tries** so a stubborn piece can never starve
   `Hunt`. Empirically verified plate layers: Chest `0x0D`, Legs `0x04`, Arms `0x13`,
   Gloves `0x07`, Gorget `0x0A`, Helm `0x06`.

3. **`swordsman` profession** — Swordsmanship/Tactics/Anatomy/Healing 100, a full
   plate suit + Katana + 200 bandages, `combat_disposition="aggressive"`,
   `pre_work_skills=(EquipWeapon, EquipArmor)`. The planner order (work-skill mode):
   `Survive > RecoverDeath > SpeakPending > GoTo > EquipWeapon > EquipArmor > Hunt >
   Greet > Wander` (first `can_run` wins; Survive/RecoverDeath pre-checked).

4. **Economy** — two capabilities registered for `swordsman`: `bank_gold` (the
   profession-agnostic bank machinery, verbatim) and `buy_weapon` (`BuyWeapon` on the
   generalized toolbuy FSM, buying a Katana from the Weaponsmith @33g). `buy_weapon`
   needs a **worn-aware** readiness (`_make_weapon_buy_ready` off a new `_owned_weapon`
   that checks the pack **or** the layer-1 hand) — a swordsman *wears* its blade, so
   the stock pack-only trigger would buy swords forever. `pre_work_skills` are excluded
   from capability (economy) mode, whose planner manifest is a fixed
   `[reflexes]+[capabilities]` shape a pre-work reflex would break — so the swordsman
   cleanly builds **both** a work-skill planner and an economy planner.

## Live verification (all GM-free after staging, starting gold deleted → all gold is loot)

- **Core loop** (`scratchpad/live_swordsman.py`) — a staged swordsman equips a Katana
  (layer 1 → Swordsmanship, not Wrestling), kills Mongbats/Orc with it, loots gold,
  and `Survive` bandages it through the fight. 4 kills, 34 gold, survived.

- **Strong armored / rich prey** (`scratchpad/live_swordsman_rich.py`) — the
  capstone. An **unarmored** warrior is provably alpha-struck dead by three Ettins
  (HP 125→0 by tick 40, 0 kills). The **armored** warrior (full plate, GM combat
  skills, 150 HP) equips **6/6 plate** (stripping the starter pants first), **tanks**
  3 Ettins + an Orc (**min HP 64/125** — never near death), kills them, and banks
  **208 gold** (~5× a Mongbat run). Armor is so effective it needed **zero bandages**.

- **Buy a weapon with earned money** (`scratchpad/live_swordsman_buy.py`) — a
  weaponless swordsman with 100 gold drives the closed capability planner to pick
  `buy_weapon`, walks to the Weaponsmith, buys **exactly one Katana for 33 gold**
  (100→67), the goal reaches SUCCESS, and then `EquipWeapon` (work-skill mode) wields
  the freshly bought blade at layer 1. Full `돈 벌고 → 무기 산다` loop, live-verified.

## Bugs the live proofs / diagnostics caught (and fixed)

- **`EquipWeapon.can_run` returned False mid-equip.** After `PickUp` the sword is on
  the cursor and gone from `items`, so the "best owned sword" lookup saw nothing and
  the second (`Equip`) packet never fired → the warrior fought bare-handed with
  Wrestling. Fixed by keeping `can_run` true mid-equip off the remembered serial.

- **Unarmored warrior alpha-struck dead by three Ettins.** The missing capstone was
  armor → built `EquipArmor`.

- **Only 5/6 plate equipped, and it wedged `Hunt`.** ServUO rejects an equip whose
  layer is occupied; a fresh char's starter pants hold `PlateLegs`' layer. Diagnostics
  (`scratchpad/diag_armor*.py`) pinned it by brute-forcing the layers and inspecting
  worn clothing. Fixed by stripping the blocking garment first **and** a give-up guard
  so a refused layer never loops forever and starves `Hunt`.

## Economics / strength notes

- **Katana** is the research-recommended blade (best sustained DPS + skill-gain rate +
  shield-compatible; 33g at the Weaponsmith). All buyable swords are one-handed (layer 1).
- **Armor is the multiplier for "많은 돈".** Rich prey (Ettin ~75g, vs Mongbat ~13g)
  is only farmable safely *with* a plate suit — the difference between dying at tick 40
  and cruising above 50% HP while banking 208 gold. Skills (Swords/Tactics/Anatomy/
  Healing) also rise from live swinging + bandaging (ServUO on-use gain), so the warrior
  gets stronger by fighting.

## Living-test iteration (post-hoc hardening)

A `/goal` to "make a good test character, run a LIVING test, and improve from what it
reveals" was run against the swordsman. A 4-lens design workflow chose it (deliberately
under-provisioned) as the richest test subject, and an endurance run
(`scratchpad/live_warrior_life.py`, then `live_warrior_thrive.py`) surfaced a real
robustness cliff plus one shipped improvement. Full write-up:
`scratchpad/LIVING_TEST_FINDINGS.md`.

- **Finding — remote-death naked loop.** An under-provisioned warrior overwhelmed by 3
  Ettins (their DPS outpaces a single ~50-HP bandage) dies, drops all its plate onto its
  corpse, resurrects ~134 tiles away at a distant healer, and — the corpse now sitting in
  the prey zone — death-loops naked. `RecoverDeath`'s corpse recovery works mechanically
  (it navigates the 134 tiles back) but is defeated by prey guarding the corpse.
- **Improvement shipped — heal hysteresis.** `Survive` gained `heal_until_fraction`:
  once a heal starts it recovers to a safe ceiling before re-engaging, instead of
  stopping the instant HP crosses back above 40%. Default equals the trigger, so every
  existing profession is byte-identical; the warrior installs `WarriorSurvive` (0.75) via
  `Profession.survive_factory`. Honestly, a heal-ceiling demo showed a single bandage on
  this shard already overshoots to ~89%, so the hysteresis is a modest buffer, not the
  main lever — a fact the re-test, not the hypothesis, established.
- **Positive result.** A properly-provisioned warrior LIVES WELL: `live_warrior_thrive.py`
  (kills-driven respawn) ran ~500 ticks with **0 deaths, full plate kept, ~646 gold
  banked, HP healthy** — the shipped combat/heal/loot code is sound.
- **Re-arm after death (shipped).** The resupply leg that breaks the naked death-loop:
  when the corpse can't be recovered, buy a replacement blade + bandages with looted gold
  instead of fighting on naked and dry. `skills/warrior.py::BuyBandage` (a
  `BuyMaterialCapability` buying a 20-batch of bandages from the `healer_spot` Healer @5g,
  mirroring the tinker's `BuyIron`) joins `bank_gold` + `buy_weapon` in the swordsman's
  economy set. Live-verified (`scratchpad/live_warrior_rearm.py`, GATE PASSED): a
  post-death warrior — no sword, a near-empty bandage stack, looted gold — RE-ARMS via the
  economy (`buy_weapon` → Katana, gold 300→267; `buy_bandage` → bandages 8→28, 267→167) and
  then the work-skill planner's `EquipWeapon` re-wields the fresh blade. Two live gotchas:
  drive the capability goals with a SYNCHRONOUS `CapabilityCognition` (the async
  `ThreadedCognition` raced and intermittently never proposed the goal), and stage one
  vendor at a time on one calibrated tile (two nearby wandering NPCs confuse the
  closest-mobile vendor pick).
- **Autonomous orchestrator (shipped).** `warrior_life.py::WarriorLife` is the piece that
  decides *when* to leave the hunt loop to re-arm and resumes on its own. `decide_mode`
  (a pure, testable function over the observation + vendor routes) picks hunt vs economy —
  re-arm a lost blade first, then restock bandages, then bank surplus, else hunt; a ghost
  yields to `RecoverDeath`. `WarriorLife` runs two agents over one body (hunt + economy)
  with SEPARATE memories that coordinate only through the world (the hunt agent wields the
  blade the economy agent bought because it SEES it in the pack — sharing one memory was
  live-caught corrupting the buy FSM with the hunt agent's leftover state), plus a
  hysteresis (`ECON_GRACE`) so a 1-2 tick mid-equip "weaponless" blip doesn't interrupt
  `EquipWeapon`/`EquipArmor` and strand the gear. Live-verified
  (`scratchpad/live_warrior_orchestrator.py`, GATE PASSED): a warrior hunting with Katana +
  6/6 plate has its blade stripped mid-life, and with NO manual driver it auto-switches to
  economy, re-buys a Katana (gold 120→87), and auto-switches back to hunt with the blade
  re-wielded — a death turned into a self-recovered setback.
  **The mode rule gained an EXIT edge on 2026-08-03, and it is not the hysteresis
  mirrored.** `decide_mode` is pure over `(obs, memory)`, so it cannot see the goal stack
  and answers "hunt" on the very tick a transaction's own world-fact flips — the coin
  moves, the goods leave the pack — leaving the frame stranded mid-transaction, since only
  the economy agent's ticks can retire one and exactly ONE inner agent is ticked per
  orchestrator tick. So `tick` now HOLDS the economy mode while a frame is live, bounded
  three ways (the FSM's give-up ladder; the frame's own deadline; and, because neither of
  those is general, an OVERDUE frame releasing the hold outright), with a death episode —
  ghost window AND corpse run — overriding it so `RecoverDeath` always has the body. Found
  live, fixed and proven **offline only**: `docs/AUDIT-2026-07-29.md`, 2026-08-03 §3 and
  §5, and follow-up 12 for what a live run must check.
- **Village integration (wired).** `village.py::run_warrior_village` + `--warriors N` run
  N swordsmen living the loop via `WarriorLife` in the standing village: each is staged at
  its own pocket with a Weaponsmith/Healer/Banker (pushed well out so they don't distract
  Greet/Wander), full plate + Katana + bandages, starter gold deleted, and a kills-driven
  prey supply; each runs through the unchanged `_run_worker` (WarriorLife duck-types as an
  Agent). The load-bearing fix here: WarriorLife wraps the body in a `_CachingBody` and
  decides the mode from the last CACHED observation — issuing its own extra `observe()`
  around the inner agent's tick breaks that agent's non-blocking route/reflex cadence
  (live-caught: with the extra pump the warrior never equipped or engaged; with the cache
  it equips and enters hunt mode).   Three fixes were needed to make village warriors genuinely hunt, each found by a live
  trace rather than guessed:
  1. **Caching body** (above) — the orchestrator must not add its own `observe()` around
     the inner agent's tick, or the warrior never equips or engages.
  2. **Live-position prey** — spawn prey adjacent to the warrior's *current* position (read
     from the cached `last_obs`), not a fixed stand it has drifted away from. A `mindist`
     trace confirmed prey then reach melee (mindist=1) instead of idling out of reach.
  3. **Pinned prey** — an action-stream trace proved combat itself works (war mode + real
     `Attack`s, the Ettin's HP falling 9→5→3), but a wounded ServUO creature **flees** at
     low HP and outruns the warrior (distance 1→13), so an almost-won fight never landed.
     Pinning each spawned creature with `[Set CantWalk true` — the same pin `stage_npc`
     already applies to wandering vendors — makes it stand and fight.

  Live result: `--warriors 1 --ticks 200` now finishes with a real kill and **out+160.0**
  (looted gold), where every run before these fixes reported 0 kills and 0.0 reward. The
  swordsman lives the full autonomous loop in the standing village.
- **Multi-warrior scale-out (measured).** `--warriors N` runs several swordsmen at once,
  each in its own pocket 25 tiles apart. A 3-warrior run exposed a real staging bug: each
  pocket is cleared with a `[WipeNPCs` radius of 20 and the wipe ran INLINE with that
  warrior's staging, so staging warrior N+1 deleted warrior N's just-staged vendors (at
  ±12) and its adjacent prey — every warrior but the last sat with nothing to fight. Fixed
  with **two-pass staging**: pass 1 places every warrior and clears every pocket (before
  any vendor/prey exists), pass 2 dresses each warrior and stages its own furniture, with
  nothing wiping afterwards. Measured throughput on this single-port ServUO:

  | warriors | result |
  |---|---|
  | 1 | out+316.0, 2 kills |
  | 2 | out+181.0 + out+158.0, 2 kills — both hunting in parallel |
  | 3 (before the GM budget) | out+0.0 ×3, 0 kills — all frozen at steps=0 |
  | 3 (after) | out+301.0, 2 kills — all three alive and moving |
  | 5 | out+151.0, 1 kill — only 3 of 5 get enough cadence |

- **Raising the ceiling — budget the GM work.** The 3-warrior wall was measured, not
  guessed: every GM call costs a full pump on the ONE shared control connection, served by
  the same single-threaded shard the warriors play on, and the monitor's restocking was the
  choke point — `_spawn_pinned` = `[Add` + `find_mobile_near` (up to **3** pumps at the
  default retries) + `[Set CantWalk` ≈ 2s, so 3 warriors × 2 prey ≈ **12s of GM work inside
  a 3s cycle**, monopolizing the server until every warrior bridge starved. Three fixes keep
  GM traffic flat as the roster grows: `find_mobile_near(retries=1)` (the creature was just
  `[Add`-ed at a known tile — ~2s → ~0.8s), a **per-cycle GM budget** (2 spawns) applied
  **round-robin** over the roster so every warrior is still served regularly, and a monitor
  interval that grows with the roster plus a 0.7s stagger between worker starts so the
  bridges' pump windows interleave. Result: 3 warriors went from *all frozen, 0 kills* to
  *all active, 2 kills, out+301*.
- **The 5-warrior limit (measured, not guessed).** One more lever was found and applied:
  an agent runner observes **twice** per tick (the agent's own observe, then the runner's
  for status), and on a shared single-threaded shard those pumps are the scarce resource —
  `_CachingBody` now serves the second observe of a tick from that tick's cached state
  (`WarriorLife.tick()` calls `begin_tick()` so each tick still starts fresh), halving each
  warrior's pump load and taking 5 warriors from 2-of-5 active to 3-of-5. But the ceiling
  held at ~3: a liveness check added at staging confirms **every body reports
  `connected=True`** right before the threads start, so the starved warriors are alive and
  simply not being served. That is a single-threaded-shard limit — pushing past it needs a
  second shard port (or a shard that services connections in parallel), not more tuning.
- **Armor replacement (shipped).** The last piece of the re-arm story: death drops the
  whole suit on the corpse, and a corpse guarded by prey often can't be reclaimed, so
  `skills/warrior.py::BuyArmor` buys a replacement **PlateChest** (0x1415 @ 243g — the
  biggest slice of armor rating) from an `armorer_spot` Armorer. It mirrors `BuyWeapon`,
  including a worn-aware ownership check (`capabilities.py::_make_armor_buy_ready` reads
  `PLATE_ARMOR_LAYERS`, since armor is worn at per-piece layers and a pack-only check would
  re-buy every trip). `decide_mode` prices it into the priority by what actually stops a
  warrior living: **blade > bandages > chest plate > bank surplus > hunt**. `village.py`
  stages an Armorer in each warrior's pocket and wires `armorer_spot`. Live-verified
  (`scratchpad/live_armor_direct.py`): a chestless warrior with 400 gold buys a PlateChest
  for exactly its 243g price (gold 400 → 157); the orchestrator half auto-switched to
  `buy_armor` after the grace with the chest stripped mid-hunt (that run then hit the known
  intermittent vendor-buy stall, so the fully-orchestrated purchase is the piece still to
  re-confirm).
- **Blade upgrade (shipped).** `buy_weapon` only fires when the warrior is *completely*
  unarmed, so one that lost its Katana and looted a Cutlass kept the worse blade forever.
  The documented blocker — the arrival proof's `start_tools == 0` — turned out not to be
  one: that gate counts **pack** swords, and an upgrading warrior *wears* its weaker blade
  with an empty pack, so the stock proof applies unchanged and only a readiness was needed.
  `UpgradeWeapon(BuyWeapon)` makes the same purchase with a different trigger, and
  `_make_weapon_upgrade_ready` fires when the best WORN blade ranks below the offer
  (`SWORD_RANK`), the pack holds no sword (keeping that premise true), and the warrior can
  afford it **while keeping a chest plate's worth in reserve** — growth never spends the
  coin a life-critical re-arm needs. Live-verified
  (`scratchpad/live_upgrade_direct.py`): a Cutlass-wearing warrior with 400 gold buys a
  Katana for exactly 33g (400 → 367), the case `buy_weapon` cannot serve.

  **Full economy priority:** blade → bandages → chest plate → blade upgrade → bank → hunt.
- **Known live flakiness.** Vendor buys stall intermittently (~50% of runs, across every
  buy capability, seen since the re-arm proof). The same script passes on a retry and the
  offline gates pin the logic, but an end-to-end orchestrated purchase can need a rerun.
- **Next (not built).** Replacing the other five plate pieces, chasing the intermittent
  vendor-buy stall, and a second shard port if a bigger roster is ever wanted.
