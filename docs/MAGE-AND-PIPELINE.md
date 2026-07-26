# The Mage, and the production pipeline that funds it

A `/goal`: *make things with a production skill, sell them for money, and use that money to
raise a character who can fight — and if there can be a magic-using character besides the
warrior, build that too.*

Both halves are live-verified. The magic character is `mage`; the pipeline joins the
tinker's existing craft-and-sell loop to it with a gold hand-off.

## The mage — fighting with Magery instead of a blade

The sword-warrior is strong because of what it **wears** (ServUO picks the combat skill
from the equipped weapon). A mage's damage instead scales with **Magery / Evaluating
Intelligence**, so nothing needs to be worn — what it needs is **mana** and **reagents**,
which is exactly what makes its economy leg load-bearing.

Everything else a hunter needs was reused **unchanged**: `Hunt` engages and loots corpses,
`Survive` bandages through the fight, and the market/bank capabilities buy the supplies.
The one genuinely new fast-loop piece is the cast — and the contract already carried every
packet for it.

**`skills/mage.py::CastAttack`** — a UO attack spell is two steps, and both already
existed: `CastSpell(spell)` opens the incantation, then the server's **target cursor** is
answered with `TargetObject(victim)` — the same `pending_target` idiom `Survive` uses for
bandages. It is gated on mana AND reagents (a cast the server would refuse only burns
ticks), times out if no cursor ever opens, and re-aims if the victim dies mid-incantation.

**Spell data is ServUO's own:** Magic Arrow is registry index `04` and
`PacketHandlers.CastSpell` does `ReadInt16() - 1`, so the **wire id is 5**; its reagent is
**SulfurousAsh** (art `0xF8C`, sold by the Mage vendor @3g).

**The `mage` profession** — Magery/EvalInt 90, Meditation 80, a spellbook + reagent pouch +
bandages, with `pre_work_skills=(CastAttack,)` so spells sit directly above the work skill:
`Survive > RecoverDeath > SpeakPending > GoTo > CastAttack > Hunt > Greet > Wander`.

### Live gate (`scratchpad/live_mage.py`) — PASSED, all five flags
14 casts, 14 cursor answers, ash **100 → 86** consumed, an Ettin's HP driven **22 → 1 by
magic**, and the kill registered.

### Three bugs the live run caught — none visible offline
1. **Targeting required `hits > 0`.** A creature reports `hits == 0` until the server sends
   its status, so the mage stood beside a pinned Ettin for 200 ticks without casting. Fixed
   to hostility + range only, exactly like `Combat._target`.
2. **A staged spellbook arrives EMPTY**, and ServUO refuses a cast whose spell is not in
   the caster's book — 16 casts went out and no cursor ever opened. The proof now fills it
   with the `[AllSpells` GM command.
3. **Spell kills were unattributed.** `Hunt` decides a corpse is ours from `hunt_attacked`
   — the serials `Combat` sent an **`Attack`** for. A mage never sends Attack, so it bolted
   creatures down and walked away from every corpse it made. `CastAttack` now records its
   victims in that same ledger.

## The production pipeline — a crafter's earnings become a mage's spells

The crafting half already existed and is untouched: the tinker's live-verified
`craft_tongs → sell_tongs → bank_gold` loop. What was missing was the **hand-off** and the
fighter's own economy — and both are config subclasses of the ground drop/pickup machinery
the lumberjack and carpenter already use for boards, pointed at gold:

- `skills/tinkering.py::DeliverGold` — carry the craft-and-sell proceeds to the funded
  fighter's `mage_drop` spot (threshold 120g, so it walks over with a real purse).
- `skills/mage.py::FetchGold` — the fighter picks that purse up.
- `capabilities.py` — `deliver_gold` (tinker) plus the mage's whole economy: `fetch_gold`,
  `buy_reagent`, `bank_gold`. Wired through the **existing** skill-class deliver factories
  (they already read a skill's own `delivered_graphics`/`drop_key`/`deliver_threshold`)
  plus one new generic `_make_fetch_ready`. No existing binding changed.

### The full arc, every link live-verified

| step | capability | proof |
|---|---|---|
| MAKE | tinker `craft_tongs` | previously live-verified |
| SELL | tinker `sell_tongs` | previously live-verified |
| FUND | tinker `deliver_gold` | `live_pipeline.py` |
| COLLECT | mage `fetch_gold` | `live_pipeline.py` |
| ARM | mage `buy_reagent` | `live_pipeline.py` |
| FIGHT | mage `cast_attack` | `live_mage.py` |

### Live gate (`scratchpad/live_pipeline.py`) — PASSED, 4/4
A crafter carrying a 300g purse walks to the mage's funding spot and drops it (tick 3); the
mage picks up all **300g** off the ground (tick 3) and turns it into **20 SulfurousAsh for
exactly 60g** (tick 4). Provenance is airtight — the mage was staged with **zero gold and
zero reagents**, so every coin it spent came from the crafter and every reagent it holds
was bought with that money.

## A new tactic: kiting

The first behaviour that makes a caster genuinely *play* differently from a swordsman. A
warrior **wants** contact — its damage happens in melee. A mage's damage happens at range,
so every tile a creature closes is pure loss: it takes hits while its own output is
unchanged. Nothing in the planner expressed that, because `Survive` only retreats once the
mage is **already** below 40% HP — far too late for a frail caster.

`skills/mage.py::KeepDistance` steps away from a hostile that has closed to melee, reusing
`Survive`'s retreat geometry for a *tactical* (not desperate) step. The band is deliberately
narrow: it fires only within `too_close` (2) and stops the moment the gap is open, so the
mage alternates "step back, cast, step back, cast" rather than fleeing; the budget is capped
(3 steps) and recovers once the gap has been held; and it yields while a target cursor is up
so a half-finished cast is never abandoned. Planner order: `KeepDistance > CastAttack > Hunt`.

### Live gate (`scratchpad/live_kite_mech.py`) — PASSED

| arm | melee_frac | max_dist |
|---|---|---|
| NO-KITE | **1.00** (melee every tick) | 1 |
| KITE | **0.03** (melee on 1 of 37 ticks) | **3** |

The mage steps off within ten ticks and holds distance 3 — a ~33× cut in melee exposure.

**Why this gate and not the first one.** A first A/B against a free-roaming Ettin was
*inconclusive*, and the reason is worth recording: the two runs differed mostly in what the
**monster** did (one fled when wounded, the other charged), so its "pass" rested on
melee_frac 0.57 → 0.50 while HP loss got *worse*, 0 → 36 — a verdict about the creature, not
the tactic. Re-running against a **pinned, adjacent** Ettin that can neither chase nor flee
makes the mage's own stepping the only thing that can change the gap, which is what turns a
noisy comparison into a decisive one.

## MageLife — the mage lives on its own

`mage_life.py::MageLife` is the mage's counterpart to `WarriorLife`, and deliberately the
**same orchestrator**: two agents over one body, separate memories coordinating only through
the world, a caching body so the mode decision costs no extra pump, and the switch
hysteresis. Each of those was live-caught the hard way building the warrior's version, so
the mage inherits the fixes rather than rediscovering them — `warrior_life.py` gained one
pluggable `decide` hook (defaulting to the warrior's own rule, so its behaviour is
unchanged) and `MageLife` is a thin subclass.

What differs is the **decision**, because a mage stops being able to fight for a different
reason: a warrior loses its blade or armor, a mage runs out of **reagents** (without ash
`CastAttack` is inert and a frail caster is left swinging its fists):

    reagents  >  collect a delivered purse  >  bank surplus  >  hunt

That second line is where the production pipeline **closes into the mage's own life**: a
crafter drops its earnings at the mage's funding spot, and the mage — unprompted — walks
over, picks them up (`fetch_gold`), and turns them into the ability to cast.

### Live gate (`scratchpad/live_mage_life.py`) — PASSED, 5/5
A mage hunting with a full pouch has its reagents stripped at tick 20 (the caster's
equivalent of losing its blade). With no manual driver it waits out the grace,
**auto-switches to the economy leg at tick 26** targeting `buy_reagent`, **restocks 20 ash
for 60 gold at tick 29** (200 → 140), and is **back to hunting able to cast again at tick
30**.

## The artisan+mage roster — and what running it unattended revealed

`village.py::run_artisan_mage_village` (CLI `--pipeline`) turns the pipeline from a scripted
proof into a standing roster: it stages an artisan and a mage side by side with their
vendors, prey, and provenance (the mage starts broke, its spellbook filled), then runs both
unattended in one process through the unchanged `_run_worker` — the artisan on its own
capability planner under a `CapabilityCognition` with **no client** (it picks the first
*observation-ready* capability, so the readiness gates **are** the policy), and the mage
under `MageLife`.

Both halves run autonomously, and the mage side works: across a 900-tick unattended run it
hunts, casts, and greets on its own with no driver.

### Finding: crafting is far more contention-sensitive than hunting

In the shared village the artisan produced **nothing** in 900 ticks. Two live checks pin the
cause, and it is **not** the craft logic:

- a readiness diagnostic with the **exact** village staging reports its capabilities ready:
  `('craft_tongs', 'deliver_gold')`;
- the **same** artisan — same staging, same autonomous cognition — running **alone** crafts
  immediately: **5 tongs from 6 iron by tick 25**, already advancing to `sell_tongs`.

So the craft loop is sound; sharing the single-threaded shard with a hunting mage starves
it. This is the same ceiling measured for the warrior village, but much sharper here,
because a **gump-driven craft FSM needs many server round-trips per item** while hunting is
mostly local decisions. (The solo run's sell then hit the separately-documented intermittent
vendor stall, so its reward read 0.0 even though the tongs were made.)

Lifting it needs the shard-side lever already identified for the warriors — a second port,
or a shard that services connections in parallel — not more agent tuning.

## Next (not built)

More spells (a heal, a stronger bolt as Magery grows); the crafter deciding *when* to fund
the fighter rather than being driven to it; and a second shard port so the artisan and the
mage stop competing for one server thread.
