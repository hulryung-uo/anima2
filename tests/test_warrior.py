"""Warrior (sword-hunter) — EquipWeapon's two-step wield + best-blade selection,
and the BuyWeapon config, on hand-built observations. The combat/heal/loot loop
itself is Hunt/Survive (covered by their own tests); this targets what is
WARRIOR-specific: wearing the blade so the server fights with Swordsmanship.
"""

from anima2.contract import Equip, ItemView, Observation, PickUp, PlayerView, Position
from anima2.persona import Persona
from anima2.skills.base import SkillContext, Status
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.warrior import (
    CUTLASS_GRAPHIC,
    KATANA_GRAPHIC,
    SWORD_GRAPHICS,
    SWORD_RANK,
    WEAPON_LAYER,
    BuyWeapon,
    EquipWeapon,
)

PLAYER = 1
BACKPACK = 0x50


def _item(serial, graphic, *, container=BACKPACK, layer=0):
    return ItemView(serial=serial, graphic=graphic, amount=1, pos=Position(),
                    container=container, layer=layer, distance=0)


def _backpack():
    return _item(BACKPACK, 0x0E75, container=PLAYER, layer=BACKPACK_LAYER)


def _ctx(items, *, memory, pending=None):
    obs = Observation(player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0)),
                      items=list(items), pending_target=pending)
    return SkillContext(obs=obs, persona=Persona(name="Bram"), memory=memory)


def test_katana_is_the_top_ranked_farming_blade():
    assert KATANA_GRAPHIC == 0x13FF
    assert SWORD_RANK[KATANA_GRAPHIC] == max(SWORD_RANK.values())
    assert all(g in SWORD_RANK for g in SWORD_GRAPHICS)


def test_equip_weapon_wields_the_pack_sword_in_two_steps():
    skill = EquipWeapon()
    mem: dict = {}
    katana = _item(0x700, KATANA_GRAPHIC)  # a katana in the pack, not worn

    # Tick 1: PickUp the sword to the cursor.
    r1 = skill.step(_ctx([_backpack(), katana], memory=mem))
    assert isinstance(r1.action, PickUp) and r1.action.serial == 0x700

    # Tick 2: the sword is on the cursor (gone from `items`) — can_run must STILL be
    # true (the mid-equip fix) so the second packet, Equip, fires off the remembered
    # serial. This is the exact bug the live proof caught (sword never got worn).
    mid = _ctx([_backpack()], memory=mem)
    assert skill.can_run(mid) is True
    r2 = skill.step(mid)
    assert isinstance(r2.action, Equip)
    assert r2.action.serial == 0x700 and r2.action.layer == WEAPON_LAYER  # one-handed

    # Tick 3: the katana is now worn at layer 1 -> inert (best blade already wielded).
    worn = _item(0x700, KATANA_GRAPHIC, container=PLAYER, layer=WEAPON_LAYER)
    done_ctx = _ctx([_backpack(), worn], memory=mem)
    assert skill.can_run(done_ctx) is False
    assert skill.step(done_ctx).status is Status.SUCCESS


def test_equip_weapon_prefers_a_stronger_blade_in_the_pack_over_a_worn_weaker_one():
    skill = EquipWeapon()
    worn_cutlass = _item(0x700, CUTLASS_GRAPHIC, container=PLAYER, layer=WEAPON_LAYER)
    pack_katana = _item(0x701, KATANA_GRAPHIC)  # stronger blade sitting in the pack
    ctx = _ctx([_backpack(), worn_cutlass, pack_katana], memory={})
    # A weaker sword is worn but a stronger one is owned -> re-wield (upgrade).
    assert skill.can_run(ctx) is True
    r = skill.step(ctx)
    assert isinstance(r.action, PickUp) and r.action.serial == 0x701  # picks the katana


def test_equip_weapon_never_steals_a_cursor_and_is_inert_bare_handed():
    skill = EquipWeapon()
    # An open cursor (a bandage/loot cursor) -> never contend for it.
    assert skill.can_run(_ctx([_backpack(), _item(0x700, KATANA_GRAPHIC)],
                              memory={}, pending=object())) is False
    # No sword owned at all -> inert (Hunt fights bare-handed with Wrestling).
    assert skill.can_run(_ctx([_backpack()], memory={})) is False


def test_buy_weapon_config_targets_a_katana_at_the_weapon_vendor():
    assert BuyWeapon.owned_tool_graphics == SWORD_GRAPHICS   # any sword = armed
    assert BuyWeapon.offer_graphic == KATANA_GRAPHIC          # buy the best-DPS blade
    assert BuyWeapon.tool_price_estimate == 33                # Katana @33g
    assert BuyWeapon.vendor_spot_key == "weapon_vendor_spot"  # its own vendor key
