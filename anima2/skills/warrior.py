"""Warrior — a sword-wielding hunter: equip a blade, fight with Swordsmanship,
heal through bandages, loot the corpses, bank the gold, and buy better swords.

The combat/heal/loot machinery already exists and needs NO change: `Hunt(Combat)`
(`skills/hunt.py`) engages hostiles and loots their corpses, and `Survive`
(`skills/survival.py`) bandages through the fight — both weapon-agnostic. On
ServUO the server picks the combat skill from what the mobile is WEARING (bare
hands -> Wrestling; a sword in the one-handed layer -> Swordsmanship), so the only
new fast-loop piece a swordsman needs is to actually WEAR its blade: `EquipWeapon`
below. Once worn, `Hunt`'s existing WarMode+Attack fights with Swordsmanship, and
the warrior's Swordsmanship/Tactics/Anatomy rise from live swings (ServUO
`SkillCheck` on-use gain) toward a stronger warrior.

The economy loop reuses the generalized capability machinery: `bank_gold`
(`market.py::BankGold`) banks looted gold; `BuyWeapon` (below, a config subclass
of `market.py::BuyToolCapability`, exactly like the lumberjack's `BuyHatchet`)
buys a fresh sword from the WeaponSmith with earned gold.

Weapon data (ServUO `Scripts/VendorInfo/SBWeaponSmith.cs` + `Scripts/Items/
Weapons/`): every sword below is a one-handed Swordsmanship weapon (equip LAYER 1);
prices are the WeaponSmith's for-sale gold.
"""

from __future__ import annotations

from ..contract import Drop, Equip, PickUp
from .base import Skill, SkillContext, SkillResult, Status
from .harvest import BACKPACK_LAYER
from .market import BuyToolCapability
from .survival import Survive


class WarriorSurvive(Survive):
    """A warrior's survival reflex: identical to `Survive`, but once a heal starts it
    keeps bandaging until HP recovers to a SAFE margin (75%) before returning to the
    fight. The stock `Survive` stops the instant HP crosses back above 40%, so a
    warrior re-engages Ettins at ~41% HP and is bursted down again — the death-loop a
    living-endurance test surfaced. Plate soaks the damage; this makes the fighter
    wait behind it until it is actually safe to swing again."""

    heal_until_fraction = 0.75

# The equip layer for a ONE-HANDED weapon (ServUO Layer.OneHanded == 1). Two-handed
# weapons use layer 2 (mirrors harvest.py's axe), but the buyable swords are all
# one-handed, so the warrior wields at layer 1.
WEAPON_LAYER = 1

# One-handed swords the WeaponSmith sells (ServUO SBWeaponSmith.cs), each keyed to
# its in-pack/worn ItemID and ranked by base damage (higher = stronger blade) so
# EquipWeapon always wields the BEST sword the warrior owns and an upgrade buy
# actually upgrades. (Damage order Longsword/VikingSword > Broadsword/Scimitar >
# Katana > Cutlass; refined against Scripts/Items/Weapons/ on this shard.)
CUTLASS_GRAPHIC = 0x1441
KATANA_GRAPHIC = 0x13FF
BROADSWORD_GRAPHIC = 0x0F5E
SCIMITAR_GRAPHIC = 0x13B6
LONGSWORD_GRAPHIC = 0x0F61
VIKINGSWORD_GRAPHIC = 0x13B9

SWORD_GRAPHICS = frozenset({
    CUTLASS_GRAPHIC, KATANA_GRAPHIC, BROADSWORD_GRAPHIC,
    SCIMITAR_GRAPHIC, LONGSWORD_GRAPHIC, VIKINGSWORD_GRAPHIC,
})

# Higher = better blade for FARMING — EquipWeapon wields the highest-ranked owned
# sword. Ranked by sustained DPS on this T2A shard (pre-AOS Old* damage/speed,
# swing = 75/Speed s @100 stam, dmg ×2.25 @GM): Katana ~27 DPS is best (fast +
# one-handed + StrReq 10), then Cutlass/Broadsword ~23, Scimitar ~22, Longsword
# ~20, VikingSword ~18 (biggest one-handed HIT though, 6-34). Katana is the
# research-recommended default (best DPS + skill-gain rate + shield-compatible).
SWORD_RANK: dict[int, int] = {
    VIKINGSWORD_GRAPHIC: 1,
    LONGSWORD_GRAPHIC: 2,
    SCIMITAR_GRAPHIC: 3,
    BROADSWORD_GRAPHIC: 4,
    CUTLASS_GRAPHIC: 4,
    KATANA_GRAPHIC: 5,
}


class EquipWeapon(Skill):
    """Wield the best sword the warrior owns (so the server fights it with
    Swordsmanship, not bare-handed Wrestling), and re-wield after buying/looting a
    stronger one. A UO equip is two packets — `PickUp` to the cursor, then `Equip`
    to the hand — driven off a remembered serial the way `Harvest` equips its axe
    (`harvest.py`). Inert once the best owned sword is already worn, so it costs
    nothing between upgrades and never steals a cursor from the work skill.
    """

    name = "equip_weapon"
    description = "Wield the best owned sword so the warrior fights with Swordsmanship."

    _STEP = "equip_weapon_step"
    _SERIAL = "equip_weapon_serial"

    def can_run(self, ctx: SkillContext) -> bool:
        # Never contend for an open cursor (a bandage/loot cursor owns it).
        if ctx.obs.pending_target is not None:
            return False
        # Mid-equip: the sword was PickUp'd to the cursor last tick and has vanished
        # from `items`, so `_best_owned_sword` sees nothing — keep running to send the
        # second (Equip) packet off the remembered serial (the harvest-equip idiom).
        if ctx.memory.get(self._SERIAL) is not None and ctx.memory.get(self._STEP) == 1:
            return True
        worn = self._worn_sword(ctx)
        best = self._best_owned_sword(ctx)
        if best is None:
            return False  # no sword at all -> Hunt fights bare-handed (Wrestling)
        # Ready iff the best owned sword is NOT the one currently worn (unworn, or a
        # stronger blade sitting in the pack while a weaker one is in hand).
        return worn is None or best.serial != worn.serial

    def step(self, ctx: SkillContext) -> SkillResult:
        if ctx.obs.pending_target is not None:
            return SkillResult(Status.FAILURE, None)
        held = ctx.memory.get(self._SERIAL)
        # Mid-equip: the sword was PickUp'd to the cursor last tick (and has vanished
        # from `items`). Commit the second (Equip) packet off the REMEMBERED serial —
        # never re-derive `best` to decide whether to send it. During an UPGRADE the
        # still-worn weaker sword is the only blade left visible, so a re-derived best
        # would flip away from the held sword and the Equip would be skipped, stranding
        # the picked-up blade on the cursor forever (the exact bug an armored-review
        # trace caught). EquipArmor commits the same way, purely off held + step.
        if held is not None and ctx.memory.get(self._STEP) == 1:
            ctx.memory[self._STEP] = 0
            ctx.memory.pop(self._SERIAL, None)
            return SkillResult(Status.RUNNING, Equip(serial=held, layer=WEAPON_LAYER))
        best = self._best_owned_sword(ctx)
        if best is None:
            self._reset(ctx)
            return SkillResult(Status.SUCCESS, None)
        worn = self._worn_sword(ctx)
        if worn is not None and worn.serial == best.serial:
            self._reset(ctx)
            return SkillResult(Status.SUCCESS, None)  # best already worn
        # Begin the two-step equip of the best sword: PickUp to cursor, then Equip.
        ctx.memory[self._SERIAL] = best.serial
        ctx.memory[self._STEP] = 1
        return SkillResult(Status.RUNNING, PickUp(serial=best.serial, amount=1))

    def _reset(self, ctx: SkillContext) -> None:
        ctx.memory.pop(self._STEP, None)
        ctx.memory.pop(self._SERIAL, None)

    @staticmethod
    def _worn_sword(ctx: SkillContext):
        return next(
            (i for i in ctx.obs.items
             if i.graphic in SWORD_GRAPHICS
             and i.layer == WEAPON_LAYER and i.container == ctx.obs.player.serial),
            None,
        )

    def _best_owned_sword(self, ctx: SkillContext):
        """The highest-ranked sword the warrior owns — worn OR in the pack — so the
        warrior always ends up wielding its strongest blade."""
        bp = next(
            (i for i in ctx.obs.items
             if i.layer == BACKPACK_LAYER and i.container == ctx.obs.player.serial),
            None,
        )
        owned = [
            i for i in ctx.obs.items
            if i.graphic in SWORD_GRAPHICS
            and (
                (i.layer == WEAPON_LAYER and i.container == ctx.obs.player.serial)
                or (bp is not None and i.container == bp.serial)
            )
        ]
        if not owned:
            return None
        return max(owned, key=lambda i: SWORD_RANK.get(i.graphic, 0))


# A full plate suit — each piece's in-pack/worn ItemID mapped to the body LAYER it
# wears on (ServUO `Server/Item.cs` Layer enum; the layer is the piece's own
# tiledata layer, `BaseArmor` sets `Layer = (Layer)ItemData.Quality`). A warrior in
# a full suit soaks a large fraction of incoming melee — the difference between
# soloing an Ettin and being alpha-struck dead by three (proven live). Plate is the
# heaviest, highest-AR mundane armor the smith/vendor supplies.
PLATE_CHEST_GRAPHIC = 0x1415
PLATE_LEGS_GRAPHIC = 0x1411
PLATE_ARMS_GRAPHIC = 0x1410
PLATE_GLOVES_GRAPHIC = 0x1414
PLATE_GORGET_GRAPHIC = 0x1413
PLATE_HELM_GRAPHIC = 0x1412

PLATE_ARMOR_LAYERS: dict[int, int] = {
    PLATE_CHEST_GRAPHIC: 0x0D,   # InnerTorso
    PLATE_LEGS_GRAPHIC: 0x04,    # Pants
    PLATE_ARMS_GRAPHIC: 0x13,    # Arms
    PLATE_GLOVES_GRAPHIC: 0x07,  # Gloves
    PLATE_GORGET_GRAPHIC: 0x0A,  # Neck
    PLATE_HELM_GRAPHIC: 0x06,    # Helm
}
ARMOR_GRAPHICS = frozenset(PLATE_ARMOR_LAYERS)


# Give up on a plate piece after this many equip attempts. A layer the server
# won't let us fill (e.g. an undroppable starter garment) must never wedge the
# warrior on the suit forever and starve Hunt — it wears what it can and fights.
_MAX_EQUIP_TRIES = 3


class EquipArmor(Skill):
    """Wear every owned piece of a plate suit, each at its own body layer, so the
    warrior soaks melee instead of being alpha-struck (an unarmored warrior is
    provably alpha-struck dead by three Ettins). Same two-packet (`PickUp` ->
    `Equip`) idiom as `EquipWeapon`, one piece per tick, driven off a remembered
    serial.

    ServUO places each piece at ITS OWN tiledata layer and rejects the equip if
    that layer is already occupied — and a fresh character wears default clothing
    (notably pants at the Pants layer, which PlateLegs wants). So before equipping a
    piece whose layer is blocked by a NON-plate garment, EquipArmor first strips
    that garment into the pack. A piece the server keeps refusing (an undroppable
    layer) is abandoned after `_MAX_EQUIP_TRIES` so it can never starve Hunt.

    Inert once the whole owned suit is worn, so like `EquipWeapon` it costs nothing
    between top-ups. Sits just after `EquipWeapon` in the swordsman's pre-work
    reflexes: first-`can_run`-wins ordering means `EquipWeapon` finishes wielding
    the blade before `EquipArmor` starts on the suit, so the two never contend for
    the cursor.
    """

    name = "equip_armor"
    description = "Wear all owned plate armor so the warrior soaks melee damage."

    # _STEP: 0 idle · 1 mid plate-equip (Equip next) · 2 mid clothing-strip (Drop next)
    _STEP = "equip_armor_step"
    _SERIAL = "equip_armor_serial"
    _LAYER = "equip_armor_layer"
    _TRIES = "equip_armor_tries"

    def can_run(self, ctx: SkillContext) -> bool:
        if ctx.obs.pending_target is not None:
            return False
        # Mid-op: an item is on the cursor (plate to equip, or a garment to stow) —
        # keep running to send the second packet off the remembered serial.
        if ctx.memory.get(self._SERIAL) is not None and ctx.memory.get(self._STEP) in (1, 2):
            return True
        return self._plan(ctx) is not None

    def step(self, ctx: SkillContext) -> SkillResult:
        if ctx.obs.pending_target is not None:
            return SkillResult(Status.FAILURE, None)
        held = ctx.memory.get(self._SERIAL)
        step = ctx.memory.get(self._STEP, 0)
        if held is not None and step == 1:  # finish the plate equip
            layer = ctx.memory.get(self._LAYER)
            self._clear_cursor(ctx)
            return SkillResult(Status.RUNNING, Equip(serial=held, layer=layer))
        if held is not None and step == 2:  # finish stowing the stripped garment
            backpack = self._backpack_serial(ctx)
            self._clear_cursor(ctx)
            return SkillResult(Status.RUNNING, Drop(serial=held, container=backpack))
        plan = self._plan(ctx)
        if plan is None:
            self._reset(ctx)
            return SkillResult(Status.SUCCESS, None)
        kind, item, layer = plan
        tries = ctx.memory.setdefault(self._TRIES, {})
        tries[item.serial] = tries.get(item.serial, 0) + 1
        if kind == "strip":  # a non-plate garment blocks this layer — stow it first
            ctx.memory[self._SERIAL] = item.serial
            ctx.memory[self._STEP] = 2
            return SkillResult(Status.RUNNING, PickUp(serial=item.serial, amount=1))
        ctx.memory[self._SERIAL] = item.serial
        ctx.memory[self._STEP] = 1
        ctx.memory[self._LAYER] = layer
        return SkillResult(Status.RUNNING, PickUp(serial=item.serial, amount=1))

    def _clear_cursor(self, ctx: SkillContext) -> None:
        ctx.memory[self._STEP] = 0
        ctx.memory.pop(self._SERIAL, None)
        ctx.memory.pop(self._LAYER, None)

    def _reset(self, ctx: SkillContext) -> None:
        for key in (self._STEP, self._SERIAL, self._LAYER):
            ctx.memory.pop(key, None)

    @staticmethod
    def _backpack_serial(ctx: SkillContext):
        return next(
            (i.serial for i in ctx.obs.items
             if i.layer == BACKPACK_LAYER and i.container == ctx.obs.player.serial),
            None,
        )

    def _plan(self, ctx: SkillContext):
        """The next armor action, or `None` when the owned suit is fully worn.
        Returns ``("strip", garment, layer)`` to stow a non-plate garment blocking a
        plate layer, or ``("equip", piece, layer)`` to put a plate piece on. Pieces
        (or their blockers) refused `_MAX_EQUIP_TRIES` times are skipped so a
        stubborn layer never wedges the warrior."""
        backpack = self._backpack_serial(ctx)
        if backpack is None:
            return None
        player = ctx.obs.player.serial
        tries = ctx.memory.get(self._TRIES, {})
        worn_by_layer = {
            i.layer: i for i in ctx.obs.items if i.container == player
        }
        for item in ctx.obs.items:
            if item.graphic not in ARMOR_GRAPHICS or item.container != backpack:
                continue
            layer = PLATE_ARMOR_LAYERS[item.graphic]
            worn = worn_by_layer.get(layer)
            if worn is not None and worn.graphic == item.graphic:
                continue  # this very piece is already worn
            if tries.get(item.serial, 0) >= _MAX_EQUIP_TRIES:
                continue  # abandoned this piece — never starve Hunt on it
            if worn is not None and worn.graphic not in ARMOR_GRAPHICS:
                # A starter garment holds this layer: stow it so the plate can go on
                # (unless it too keeps refusing to move — then skip the piece). The
                # strip attempt is counted in `step`, keeping `_plan` side-effect-free
                # (it is also called from `can_run`).
                if tries.get(worn.serial, 0) >= _MAX_EQUIP_TRIES:
                    continue
                return "strip", worn, layer
            return "equip", item, layer
        return None


class BuyWeapon(BuyToolCapability):
    """Warrior config: buy a Katana (the best-DPS purchasable sword) from the
    `weapon_vendor_spot` WeaponSmith/sword vendor with earned gold when the warrior
    owns no sword. Only the weapon config differs from the lumberjack's `BuyHatchet`;
    the buy machinery is `BuyToolCapability`'s, unchanged. Its readiness
    (`capabilities.py::_make_weapon_buy_ready`) uses a WORN-aware "owned" check — a
    swordsman WEARS its blade at layer 1, not in the pack, so the stock pack-only
    tool check would buy endless swords.
    """

    name = "buy_weapon"
    description = "Buy a katana from the configured weapon vendor and return."
    #: Any sword counts as "already armed" (the buy trigger is owning NONE).
    owned_tool_graphics = SWORD_GRAPHICS
    #: The exact for-sale Katana art the sword vendor stocks (0x13FF @ 33g) —
    #: the best sustained-DPS one-handed sword, resolved off the enriched offer.
    offer_graphic = KATANA_GRAPHIC
    #: This shard's live Katana price (SBSwordWeapon) — the affordability estimate.
    tool_price_estimate = 33
    #: The warrior's weapon vendor (a SEPARATE key from any sell/bank vendor).
    vendor_spot_key = "weapon_vendor_spot"
