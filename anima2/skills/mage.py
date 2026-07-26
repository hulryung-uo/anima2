"""Mage — a spell-casting hunter: the magic counterpart to the sword-warrior.

Where `warrior.py` makes a fighter strong by WEARING a blade (ServUO picks the combat
skill from the equipped weapon), a mage fights with `Magery`: it casts an attack spell at
a hostile, and the damage scales with Magery/Evaluating Intelligence rather than with what
is in its hands. Everything else a hunter needs already exists and is reused UNCHANGED —
`Hunt` (`skills/hunt.py`) engages and loots corpses, `Survive` (`skills/survival.py`)
bandages through a fight, and the market/bank capabilities fund the supplies.

The one genuinely new fast-loop piece is the CAST, and the contract already carries every
packet it needs: `CastSpell` (the bridge implements it end-to-end) plus the same
`pending_target` -> `TargetObject` cursor answer that `Survive` uses for bandages. A UO
attack spell is a two-step act:

  1. `CastSpell(spell)` — the caster begins the incantation;
  2. the server opens a TARGET CURSOR, answered with `TargetObject(victim_serial)`.

`CastAttack` below drives exactly that, gated on having the mana and the reagents the
spell consumes, so a dry or drained mage yields to the rest of the planner instead of
spamming a cast the server will refuse.

Spell/reagent data is ServUO's own (`Scripts/Spells/First/MagicArrow.cs`,
`Scripts/Spells/Initializer.cs`, `Scripts/VendorInfo/SBMage.cs`): the wire spell id is the
registry index + 1 (`PacketHandlers.CastSpell` does `ReadInt16() - 1`).
"""

from __future__ import annotations

import math

from ..contract import CastSpell, TargetObject, Walk
from ..geometry import direction_toward
from .base import Skill, SkillContext, SkillResult, Status
from .carpentry import FetchBoards
from .combat import is_hostile
from .harvest import BACKPACK_LAYER
from .hunt import GOLD_GRAPHIC
from .market import BuyMaterialCapability

#: Magic Arrow — the first-circle attack spell. ServUO registers it at index 04 and the
#: cast handler adds one, so the WIRE id is 5. One SulfurousAsh, ~4 mana, and it is the
#: cheapest reliable direct-damage spell a young mage can sustain.
MAGIC_ARROW_SPELL = 5
#: Mana the cast costs (ServUO first-circle base). The gate is deliberately conservative:
#: a cast the server refuses for mana would just burn ticks.
MAGIC_ARROW_MANA = 4

#: SulfurousAsh — Magic Arrow's reagent (ServUO `Reagent.SulfurousAsh`, art 0xF8C, sold
#: by the Mage vendor at 3g).
SULFUROUS_ASH_GRAPHIC = 0x0F8C
REAGENT_GRAPHICS = frozenset({SULFUROUS_ASH_GRAPHIC})


class CastAttack(Skill):
    """Cast an attack spell at the nearest hostile — the mage's answer to a sword.

    Two ticks per cast, mirroring `Survive`'s bandage idiom: `CastSpell` opens the
    incantation, then the server's target cursor is answered with `TargetObject(victim)`.
    Inert without a hostile in range, without the mana, or without the reagent, so a
    drained or unsupplied mage falls through to the rest of the planner (flee/heal/restock)
    instead of spamming casts the server refuses.

    Sits ABOVE the work skill in the mage's planner: `Hunt` still owns engagement and
    looting, but while a hostile is in range this spends the mage's ticks on spells.
    """

    name = "cast_attack"
    description = "Cast an attack spell at the nearest hostile creature."

    spell: int = MAGIC_ARROW_SPELL
    mana_cost: int = MAGIC_ARROW_MANA
    reagent_graphics: frozenset[int] = REAGENT_GRAPHICS
    #: Casting range for a first-circle bolt (ServUO checks LOS + ~12 tiles).
    cast_range: int = 10
    #: Give up waiting for the target cursor after this many ticks (a refused cast never
    #: opens one — e.g. the server judged mana/reagents differently than we did).
    cursor_timeout_ticks: int = 6
    #: Cap on remembered spell victims, matching `Hunt.max_tracked` (the ledger is shared).
    max_tracked: int = 64

    _PHASE = "mage_cast_phase"
    _WAIT = "mage_cast_wait"
    _TARGET = "mage_cast_target"

    def can_run(self, ctx: SkillContext) -> bool:
        if ctx.persona.combat_disposition == "pacifist":
            return False
        if ctx.memory.get(self._PHASE) == "cursor":
            return True  # mid-cast: the cursor is (or is about to be) open — finish it
        if ctx.obs.pending_target is not None:
            return False  # someone else's cursor (a bandage/loot) — never steal it
        return bool(
            self._target(ctx) is not None
            and self._has_mana(ctx)
            and self._has_reagents(ctx)
        )

    def step(self, ctx: SkillContext) -> SkillResult:
        phase = ctx.memory.get(self._PHASE)
        if phase == "cursor":
            return self._answer_cursor(ctx)
        target = self._target(ctx)
        if target is None or not self._has_mana(ctx) or not self._has_reagents(ctx):
            self._reset(ctx)
            return SkillResult(Status.FAILURE, None)
        ctx.memory[self._PHASE] = "cursor"
        ctx.memory[self._WAIT] = 0
        ctx.memory[self._TARGET] = target.serial
        return SkillResult(Status.RUNNING, CastSpell(spell=self.spell))

    def _answer_cursor(self, ctx: SkillContext) -> SkillResult:
        cursor = ctx.obs.pending_target
        if cursor is not None:
            serial = ctx.memory.get(self._TARGET)
            # Re-aim at the nearest hostile if the remembered victim is gone (died or
            # fled), so a live cursor is never wasted. Presence, not health — an
            # un-attacked creature reports `hits == 0` (see `_target`).
            if not any(m.serial == serial for m in ctx.obs.mobiles):
                fresh = self._target(ctx)
                serial = fresh.serial if fresh is not None else None
            self._reset(ctx)
            if serial is None:
                return SkillResult(Status.FAILURE, None)
            self._remember_attacked(ctx, serial)
            # The cast is confirmed only when the bolt is actually aimed — the reward
            # rides the TargetObject, never the CastSpell that may still be refused.
            return SkillResult(Status.RUNNING, TargetObject(serial), reward=0.05)
        wait = int(ctx.memory.get(self._WAIT, 0)) + 1
        ctx.memory[self._WAIT] = wait
        if wait > self.cursor_timeout_ticks:
            self._reset(ctx)  # the server never offered a cursor — the cast was refused
            return SkillResult(Status.FAILURE, None)
        return SkillResult(Status.RUNNING, None)

    def _reset(self, ctx: SkillContext) -> None:
        for key in (self._PHASE, self._WAIT, self._TARGET):
            ctx.memory.pop(key, None)

    def _remember_attacked(self, ctx: SkillContext, serial: int) -> None:
        """Record a spell victim in `hunt_attacked` — the SAME ledger `Combat` fills when
        it sends an `Attack`, and the one `Hunt` reads to decide "this corpse is ours to
        loot". A mage kills with spells and never sends `Attack`, so without this its
        kills are unattributed and it walks away from every corpse it makes (live-caught:
        the mage bolted an Ettin down and Hunt recorded no kill and looted nothing).
        Bounded by the same `max_tracked` cap the hunt ledgers use."""
        attacked = list(ctx.memory.get("hunt_attacked", ()))
        if serial in attacked:
            return
        attacked.append(serial)
        ctx.memory["hunt_attacked"] = attacked[-self.max_tracked :]

    def _target(self, ctx: SkillContext):
        # obs.mobiles is distance-sorted, so the first hostile in range is the nearest.
        # Deliberately NOT gated on `hits > 0`, matching `Combat._target`: a freshly seen
        # creature reports `hits == 0` until the server sends a status update for it, so a
        # health gate makes the caster blind to every un-attacked foe (live-caught — the
        # mage stood next to an Ettin for 200 ticks without ever casting).
        for mobile in ctx.obs.mobiles:
            if is_hostile(mobile) and mobile.distance <= self.cast_range:
                return mobile
        return None

    def _has_mana(self, ctx: SkillContext) -> bool:
        return ctx.obs.player.mana >= self.mana_cost

    def _has_reagents(self, ctx: SkillContext) -> bool:
        backpack = next(
            (i.serial for i in ctx.obs.items
             if i.layer == BACKPACK_LAYER and i.container == ctx.obs.player.serial),
            None,
        )
        if backpack is None:
            return False
        return any(
            i.graphic in self.reagent_graphics and i.container == backpack
            for i in ctx.obs.items
        )


class KeepDistance(Skill):
    """Kite: step away from a hostile that has closed to melee, so the mage can keep
    casting from range instead of trading blows it cannot win.

    This is what actually makes a caster play differently from a swordsman. The warrior
    WANTS contact — its damage happens in melee. The mage's damage happens at range, and
    every tile a creature closes is pure loss: it takes hits while its own output stays the
    same. Nothing in the planner expressed that before — `Survive` only retreats once the
    mage is ALREADY badly wounded (below 40% HP), which is far too late for a frail caster.

    The band is deliberately narrow. It only fires when a hostile is within `too_close`,
    and it stops the moment the gap reaches `too_close + 1` — so the mage alternates
    "step back, cast, step back, cast" rather than fleeing the fight outright. Placed
    ABOVE `CastAttack`, so opening the gap wins the tick, and casting resumes as soon as
    the gap is open. It also yields while a target cursor is up, so a half-finished cast is
    never abandoned mid-incantation.
    """

    name = "keep_distance"
    description = "Step away from a hostile that has closed to melee, to keep casting from range."

    #: Retreat while a hostile is this close or closer (melee reach is 1).
    too_close: int = 2
    #: Never back up more than this many steps in a row — a mage that keeps walking is a
    #: mage that never casts, and open ground runs out.
    max_steps: int = 3
    #: The retreat budget resets once the gap has been held open for this many ticks.
    reset_ticks: int = 3

    _STEPS = "mage_kite_steps"
    _CLEAR = "mage_kite_clear"

    def can_run(self, ctx: SkillContext) -> bool:
        if ctx.persona.combat_disposition == "pacifist":
            return False
        if ctx.obs.pending_target is not None:
            return False  # a cast is mid-flight — answer its cursor before moving
        crowding = self._crowding(ctx)
        if not crowding:
            # The gap is open: let the retreat budget recover so the next rush can be
            # backed away from too.
            clear = int(ctx.memory.get(self._CLEAR, 0)) + 1
            ctx.memory[self._CLEAR] = clear
            if clear >= self.reset_ticks:
                ctx.memory[self._STEPS] = 0
                ctx.memory[self._CLEAR] = 0
            return False
        ctx.memory[self._CLEAR] = 0
        return int(ctx.memory.get(self._STEPS, 0)) < self.max_steps

    def step(self, ctx: SkillContext) -> SkillResult:
        crowding = self._crowding(ctx)
        if not crowding:
            return SkillResult(Status.SUCCESS, None)
        steps = int(ctx.memory.get(self._STEPS, 0))
        if steps >= self.max_steps:
            # Out of room to back up — stand and fight; `CastAttack` still works in melee,
            # and `Survive` owns the "actually in danger" case.
            return SkillResult(Status.FAILURE, None)
        ctx.memory[self._STEPS] = steps + 1
        return SkillResult(Status.RUNNING, Walk(self._away_direction(ctx, crowding), run=True))

    def _crowding(self, ctx: SkillContext) -> list:
        return [
            m for m in ctx.obs.mobiles
            if is_hostile(m) and m.distance <= self.too_close
        ]

    @staticmethod
    def _away_direction(ctx: SkillContext, hostiles: list) -> int:
        """Step directly away from the crowd's centroid — the same retreat geometry
        `Survive` uses when it flees, reused here for a tactical (not desperate) step."""
        here = ctx.obs.player.pos
        cx = sum(m.pos.x for m in hostiles) / len(hostiles)
        cy = sum(m.pos.y for m in hostiles) / len(hostiles)
        dx, dy = here.x - cx, here.y - cy
        if math.hypot(dx, dy) < 1e-6:
            dx, dy = 0.0, -1.0  # standing on the centroid: commit north
        step_x = 1 if dx > 0 else -1 if dx < 0 else 0
        step_y = 1 if dy > 0 else -1 if dy < 0 else 0
        target = type(here)(here.x + step_x, here.y + step_y, here.z)
        return direction_toward(here, target)


class BuyReagent(BuyMaterialCapability):
    """Mage config: buy a batch of SulfurousAsh (Magic Arrow's reagent) from the
    `mage_vendor_spot` Mage when the pouch runs low — the mage's equivalent of the
    warrior's bandage restock, and what turns looted (or crafted-and-sold) gold back into
    the ability to fight. Mirrors the tinker's `BuyIron` exactly; only the material,
    price, and vendor key differ.
    """

    name = "buy_reagent"
    description = "Buy a batch of spell reagents from the configured mage vendor and return."
    buy_material_graphics = REAGENT_GRAPHICS
    buy_offer_graphic = SULFUROUS_ASH_GRAPHIC
    #: The Mage vendor stocks reagents in batches of 20 (SBMage `GenericBuyInfo`), and the
    #: buy clamps to `min(buy_amount, entry.amount)`.
    buy_amount = 20
    #: Restock while there is still a fight's worth of casts left in the pouch.
    buy_reorder = 10
    #: This shard's live price (SBMage sells SulfurousAsh @3g).
    buy_price_estimate = 3
    vendor_spot_key = "mage_vendor_spot"


class FetchGold(FetchBoards):
    """Mage config: pick up the gold a CRAFTER dropped at the mage's funding spot.

    This is the receiving half of the production pipeline the goal asks for — a crafter
    makes wares, sells them, and delivers the proceeds; the mage collects that gold and
    turns it into reagents (`buy_reagent`), i.e. into the ability to fight. It is the same
    ground-pickup machinery the carpenter already uses for delivered boards
    (`FetchBoards`), pointed at gold instead: only the art differs.
    """

    name = "fetch_gold"
    description = "Pick up delivered gold from the ground into the pack for one verified goal."
    fetched_graphics = frozenset({GOLD_GRAPHIC})
