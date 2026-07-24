"""Warrior (sword-hunter) — EquipWeapon's two-step wield + best-blade selection,
and the BuyWeapon config, on hand-built observations. The combat/heal/loot loop
itself is Hunt/Survive (covered by their own tests); this targets what is
WARRIOR-specific: wearing the blade so the server fights with Swordsmanship.
"""

from anima2.contract import Drop, Equip, ItemView, Observation, PickUp, PlayerView, Position
from anima2.persona import Persona
from anima2.skills.base import SkillContext, Status
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.warrior import (
    ARMOR_GRAPHICS,
    CUTLASS_GRAPHIC,
    KATANA_GRAPHIC,
    PLATE_ARMOR_LAYERS,
    PLATE_ARMS_GRAPHIC,
    PLATE_CHEST_GRAPHIC,
    PLATE_GLOVES_GRAPHIC,
    PLATE_GORGET_GRAPHIC,
    PLATE_HELM_GRAPHIC,
    PLATE_LEGS_GRAPHIC,
    SWORD_GRAPHICS,
    SWORD_RANK,
    WEAPON_LAYER,
    BuyWeapon,
    EquipArmor,
    EquipWeapon,
    _MAX_EQUIP_TRIES,
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


def test_equip_weapon_upgrades_by_actually_equipping_the_stronger_pack_blade():
    # Regression (armored-review trace): during an UPGRADE the second (Equip) packet
    # must fire off the REMEMBERED serial. The old code re-derived `best` mid-equip,
    # which flipped to the still-worn weaker sword and stranded the picked-up blade on
    # the cursor forever — the upgrade silently failed and the cursor could stall Hunt.
    skill = EquipWeapon()
    mem: dict = {}
    worn_cutlass = _item(0x700, CUTLASS_GRAPHIC, container=PLAYER, layer=WEAPON_LAYER)
    pack_katana = _item(0x701, KATANA_GRAPHIC)  # stronger blade sitting in the pack

    ctx1 = _ctx([_backpack(), worn_cutlass, pack_katana], memory=mem)
    assert skill.can_run(ctx1) is True  # weaker worn, stronger owned -> re-wield
    r1 = skill.step(ctx1)
    assert isinstance(r1.action, PickUp) and r1.action.serial == 0x701  # grab the katana

    # Tick 2: the Katana is on the cursor (gone from `items`) while the Cutlass is
    # STILL worn — the exact state that used to flip `best` away from the held blade.
    ctx2 = _ctx([_backpack(), worn_cutlass], memory=mem)
    assert skill.can_run(ctx2) is True  # mid-equip -> keep running
    r2 = skill.step(ctx2)
    assert isinstance(r2.action, Equip)
    assert r2.action.serial == 0x701 and r2.action.layer == WEAPON_LAYER  # equips the katana
    assert mem.get(skill._SERIAL) is None  # cursor state cleared — nothing stranded


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


def test_owned_weapon_is_worn_aware_so_the_warrior_never_double_buys():
    from anima2.capabilities import _owned_weapon

    graphics = SWORD_GRAPHICS
    # A sword WORN at layer 1 (the normal warrior state) counts as owned -> the
    # buy trigger stays off (else the warrior buys blades forever, the whole reason
    # a pack-only check won't do).
    worn = _item(0x700, KATANA_GRAPHIC, container=PLAYER, layer=WEAPON_LAYER)
    assert _owned_weapon(_ctx([_backpack(), worn], memory={}), graphics) is not None
    # A sword in the pack also counts (just bought, not yet wielded).
    packed = _item(0x701, KATANA_GRAPHIC)
    assert _owned_weapon(_ctx([_backpack(), packed], memory={}), graphics) is not None
    # Truly unarmed (no sword worn or packed) -> None -> buy_weapon may fire.
    assert _owned_weapon(_ctx([_backpack()], memory={}), graphics) is None


def test_swordsman_wires_both_hunt_and_the_economy_capabilities():
    from anima2.capabilities import CAPABILITIES
    from anima2.profession import PROFESSIONS
    from anima2.skills.hunt import Hunt

    sword = PROFESSIONS["swordsman"]
    # The work-skill (hunting) planner carries both pre-work reflexes just above
    # Hunt, in order: EquipWeapon (wield) then EquipArmor (suit up), then Hunt.
    work = [type(s).__name__ for s in sword.planner().skills]
    assert work[work.index("Hunt") - 2 : work.index("Hunt")] == ["EquipWeapon", "EquipArmor"]
    assert sword.work_skill is Hunt
    # The economy planner (capability mode) builds — its manifest must pass, which
    # it only does because pre-work reflexes are excluded from capability mode.
    econ = sword.planner(capability_goals=True)
    assert set(econ.capability_ids) == {"bank_gold", "buy_weapon"}
    econ_names = [type(s).__name__ for s in econ.skills]
    assert "EquipWeapon" not in econ_names and "EquipArmor" not in econ_names
    assert {cid for (p, cid) in CAPABILITIES if p == "swordsman"} == {"bank_gold", "buy_weapon"}


def test_all_six_plate_layers_match_the_live_verified_values():
    # Every piece's layer was empirically verified live (a wrong value is rejected by
    # ServUO and the piece silently won't wear). Pin ALL six so a bad layer can never
    # pass offline while failing live (the project's known offline-mock hazard).
    assert PLATE_ARMOR_LAYERS == {
        PLATE_CHEST_GRAPHIC: 0x0D,   # InnerTorso
        PLATE_LEGS_GRAPHIC: 0x04,    # Pants
        PLATE_ARMS_GRAPHIC: 0x13,    # Arms
        PLATE_GLOVES_GRAPHIC: 0x07,  # Gloves
        PLATE_GORGET_GRAPHIC: 0x0A,  # Neck
        PLATE_HELM_GRAPHIC: 0x06,    # Helm
    }
    assert ARMOR_GRAPHICS == frozenset(PLATE_ARMOR_LAYERS)


def test_buy_weapon_binding_readiness_is_worn_aware_end_to_end():
    # The binding-level check the worn-aware trigger exists FOR: exercise the actual
    # registered `ready` gate, not just `_owned_weapon`. A swordsman WEARS its blade
    # (0 swords in the pack) yet must count as armed, or it buys Katanas forever.
    from anima2.capabilities import CAPABILITIES
    from anima2.skills.market import GOLD_GRAPHIC

    ready = CAPABILITIES[("swordsman", "buy_weapon")].ready

    def _gold(amount, serial):
        return ItemView(serial=serial, graphic=GOLD_GRAPHIC, amount=amount,
                        pos=Position(), container=BACKPACK, layer=0, distance=0)

    def _ctx_vendor(items):
        return _ctx(items, memory={"weapon_vendor_spot": ((100, 100),)})

    # Unarmed + enough gold (Katana 33g) + a vendor route -> buy_weapon IS ready.
    assert ready(_ctx_vendor([_backpack(), _gold(50, 0xA00)])) is True
    # A Katana WORN at layer 1 (pack has 0 swords) -> NOT ready: the worn-aware gate
    # must see the wielded blade and refuse a redundant buy.
    worn = _item(0x700, KATANA_GRAPHIC, container=PLAYER, layer=WEAPON_LAYER)
    assert ready(_ctx_vendor([_backpack(), _gold(50, 0xA00), worn])) is False
    # Unarmed but too poor -> not ready.
    assert ready(_ctx_vendor([_backpack(), _gold(10, 0xA01)])) is False


def test_equip_armor_wears_each_owned_plate_piece_at_its_layer():
    assert ARMOR_GRAPHICS == frozenset(PLATE_ARMOR_LAYERS)
    assert PLATE_ARMOR_LAYERS[PLATE_CHEST_GRAPHIC] == 0x0D  # InnerTorso
    assert PLATE_ARMOR_LAYERS[PLATE_HELM_GRAPHIC] == 0x06   # Helm

    skill = EquipArmor()
    mem: dict = {}
    chest = _item(0x800, PLATE_CHEST_GRAPHIC)
    helm = _item(0x801, PLATE_HELM_GRAPHIC)
    items = [_backpack(), chest, helm]

    # Tick 1: PickUp the first unworn piece (the chest).
    r1 = skill.step(_ctx(items, memory=mem))
    assert isinstance(r1.action, PickUp) and r1.action.serial == 0x800

    # Tick 2: mid-equip — the chest is on the cursor (gone from items). can_run must
    # stay true so the Equip packet fires at the chest's InnerTorso layer.
    mid = _ctx([_backpack(), helm], memory=mem)
    assert skill.can_run(mid) is True
    r2 = skill.step(mid)
    assert isinstance(r2.action, Equip)
    assert r2.action.serial == 0x800 and r2.action.layer == 0x0D

    # Chest now worn; only the helm remains in the pack -> next PickUp is the helm.
    worn_chest = _item(0x800, PLATE_CHEST_GRAPHIC, container=PLAYER, layer=0x0D)
    r3 = skill.step(_ctx([_backpack(), worn_chest, helm], memory=mem))
    assert isinstance(r3.action, PickUp) and r3.action.serial == 0x801

    # Whole owned suit worn -> inert (nothing left to put on).
    worn_helm = _item(0x801, PLATE_HELM_GRAPHIC, container=PLAYER, layer=0x06)
    done = _ctx([_backpack(), worn_chest, worn_helm], memory={})
    assert skill.can_run(done) is False
    assert skill.step(done).status is Status.SUCCESS


def test_equip_armor_never_steals_a_cursor():
    skill = EquipArmor()
    ctx = _ctx([_backpack(), _item(0x800, PLATE_CHEST_GRAPHIC)],
               memory={}, pending=object())
    assert skill.can_run(ctx) is False  # a target/loot cursor is open — yield


def test_equip_armor_strips_a_blocking_starter_garment_before_the_plate():
    # ServUO rejects a plate piece whose body layer is already occupied. A fresh
    # char wears default pants at PlateLegs' layer (0x04) — EquipArmor must stow
    # that garment into the pack FIRST, then equip the plate legs (the live 5/6 bug).
    skill = EquipArmor()
    mem: dict = {}
    pants = _item(0x900, 0x152E, container=PLAYER, layer=0x04)  # starter pants, worn
    legs = _item(0x901, PLATE_LEGS_GRAPHIC)                     # plate legs in pack

    r1 = skill.step(_ctx([_backpack(), pants, legs], memory=mem))
    assert isinstance(r1.action, PickUp) and r1.action.serial == 0x900  # grab the garment

    mid = _ctx([_backpack(), legs], memory=mem)  # pants now on the cursor
    assert skill.can_run(mid) is True
    r2 = skill.step(mid)
    assert isinstance(r2.action, Drop) and r2.action.serial == 0x900  # stow it in the pack

    # Layer 0x04 is free now -> equip the plate legs.
    r3 = skill.step(_ctx([_backpack(), legs], memory=mem))
    assert isinstance(r3.action, PickUp) and r3.action.serial == 0x901


def test_equip_armor_abandons_a_stubborn_piece_so_hunt_is_never_wedged():
    # A layer the server keeps refusing must NOT loop forever and starve Hunt.
    skill = EquipArmor()
    mem: dict = {}
    legs = _item(0x901, PLATE_LEGS_GRAPHIC)  # never "sticks" (stays in pack)
    for _ in range(_MAX_EQUIP_TRIES):
        assert skill.can_run(_ctx([_backpack(), legs], memory=mem)) is True
        skill.step(_ctx([_backpack(), legs], memory=mem))  # PickUp (counts an attempt)
        skill.step(_ctx([_backpack()], memory=mem))        # Equip off the cursor
    # After _MAX_EQUIP_TRIES failed attempts the piece is abandoned -> inert, so the
    # planner falls through to Hunt.
    assert skill.can_run(_ctx([_backpack(), legs], memory=mem)) is False
