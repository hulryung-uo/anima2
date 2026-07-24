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
- **Next fix (not built).** Re-arm-after-death: when the corpse can't be recovered, buy a
  replacement blade + bandages with banked gold (the `buy_weapon` capability exists),
  composing the hunt and economy loops so a death is a setback, not a terminal loop.
