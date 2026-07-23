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

from ..contract import Equip, PickUp
from .base import Skill, SkillContext, SkillResult, Status
from .harvest import BACKPACK_LAYER
from .market import BuyToolCapability

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
        best = self._best_owned_sword(ctx)
        held = ctx.memory.get(self._SERIAL)
        # Mid-equip: the sword is on the cursor (vanished from `items`); drive the
        # second packet off the remembered serial.
        if held is not None and (best is None or best.serial == held):
            step = ctx.memory.get(self._STEP, 0)
            if step == 1:
                ctx.memory[self._STEP] = 0
                ctx.memory.pop(self._SERIAL, None)
                return SkillResult(Status.RUNNING, Equip(serial=held, layer=WEAPON_LAYER))
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
