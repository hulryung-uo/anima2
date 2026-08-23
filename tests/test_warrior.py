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


def test_buy_bandage_config_restocks_bandages_from_the_healer():
    from anima2.skills.survival import BANDAGE_GRAPHICS
    from anima2.skills.warrior import BANDAGE_GRAPHIC, BuyBandage

    assert BuyBandage.buy_offer_graphic == BANDAGE_GRAPHIC == 0x0E21
    assert BuyBandage.buy_material_graphics == BANDAGE_GRAPHICS  # stacking material
    assert BuyBandage.buy_price_estimate == 5                    # SBHealer @5g
    assert BuyBandage.buy_amount == 20 and BuyBandage.buy_reorder == 10  # Healer's 20-batch
    assert BuyBandage.vendor_spot_key == "healer_spot"           # a Healer, own key


def test_buy_armor_config_replaces_the_chest_plate_at_the_armorer():
    from anima2.skills.warrior import BuyArmor

    assert BuyArmor.offer_graphic == PLATE_CHEST_GRAPHIC == 0x1415
    assert BuyArmor.owned_tool_graphics == frozenset({PLATE_CHEST_GRAPHIC})
    assert BuyArmor.tool_price_estimate == 243          # SBPlateArmor @243g
    assert BuyArmor.vendor_spot_key == "armorer_spot"   # an Armorer, its own key


def test_upgrade_weapon_buys_the_same_katana_as_buy_weapon():
    from anima2.skills.warrior import BuyWeapon as _BW
    from anima2.skills.warrior import UpgradeWeapon

    # Same purchase, different TRIGGER — the config is inherited unchanged.
    assert issubclass(UpgradeWeapon, _BW)
    assert UpgradeWeapon.offer_graphic == KATANA_GRAPHIC
    assert UpgradeWeapon.tool_price_estimate == _BW.tool_price_estimate
    assert UpgradeWeapon.vendor_spot_key == _BW.vendor_spot_key
    assert UpgradeWeapon.name == "upgrade_weapon"


def test_upgrade_readiness_fires_only_for_a_weaker_worn_blade_with_surplus():
    from anima2.capabilities import CAPABILITIES
    from anima2.skills.market import GOLD_GRAPHIC
    from anima2.skills.warrior import UPGRADE_RESERVE, UpgradeWeapon

    ready = CAPABILITIES[("swordsman", "upgrade_weapon")].ready
    rich = UpgradeWeapon.tool_price_estimate + UPGRADE_RESERVE  # afford it AND keep a reserve

    def _gold(amount):
        return ItemView(serial=0xA00, graphic=GOLD_GRAPHIC, amount=amount, pos=Position(),
                        container=BACKPACK, layer=0, distance=0)

    def _ctx_vendor(items):
        return _ctx(items, memory={"weapon_vendor_spot": ((100, 100),)})

    worn_cutlass = _item(0x700, CUTLASS_GRAPHIC, container=PLAYER, layer=WEAPON_LAYER)
    worn_katana = _item(0x701, KATANA_GRAPHIC, container=PLAYER, layer=WEAPON_LAYER)

    # A weaker blade worn + surplus gold -> trade up.
    assert ready(_ctx_vendor([_backpack(), worn_cutlass, _gold(rich)])) is True
    # Already wielding the best blade -> nothing to upgrade to.
    assert ready(_ctx_vendor([_backpack(), worn_katana, _gold(rich)])) is False
    # Bare-handed is `buy_weapon`'s job, never an upgrade.
    assert ready(_ctx_vendor([_backpack(), _gold(rich)])) is False
    # Enough for the blade but NOT the reserve -> don't spend a re-arm's worth on growth.
    assert ready(_ctx_vendor([_backpack(), worn_cutlass,
                              _gold(UpgradeWeapon.tool_price_estimate)])) is False
    # A sword already in the pack breaks the arrival proof's start-empty premise.
    assert ready(_ctx_vendor([_backpack(), worn_cutlass, _item(0x702, CUTLASS_GRAPHIC),
                              _gold(rich)])) is False


def test_armor_buy_readiness_is_worn_aware_so_a_suited_warrior_never_re_buys():
    # Armor is WORN (each piece at its own body layer), so a pack-only check would have
    # the warrior buying a new chest plate every trip. Exercise the registered gate.
    from anima2.capabilities import CAPABILITIES
    from anima2.skills.market import GOLD_GRAPHIC

    ready = CAPABILITIES[("swordsman", "buy_armor")].ready
    chest_layer = PLATE_ARMOR_LAYERS[PLATE_CHEST_GRAPHIC]

    def _gold(amount):
        return ItemView(serial=0xA00, graphic=GOLD_GRAPHIC, amount=amount, pos=Position(),
                        container=BACKPACK, layer=0, distance=0)

    def _ctx_armorer(items):
        return _ctx(items, memory={"armorer_spot": ((100, 100),)})

    # Lost the chest (died, corpse unreclaimed) + can afford one -> ready.
    assert ready(_ctx_armorer([_backpack(), _gold(300)])) is True
    # Wearing a chest -> NOT ready (the whole point of the worn-aware check).
    worn = _item(0x800, PLATE_CHEST_GRAPHIC, container=PLAYER, layer=chest_layer)
    assert ready(_ctx_armorer([_backpack(), _gold(300), worn])) is False
    # A just-bought chest still in the pack also counts as owned -> not ready.
    assert ready(_ctx_armorer([_backpack(), _gold(300), _item(0x801, PLATE_CHEST_GRAPHIC)])) is False
    # Chestless but too poor -> not ready (keep hunting instead of stalling at a shop).
    assert ready(_ctx_armorer([_backpack(), _gold(100)])) is False


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
    from anima2.skills.warrior import WarriorHunt

    sword = PROFESSIONS["swordsman"]
    # The work-skill (hunting) planner carries both pre-work reflexes just above
    # the work skill, in order: EquipWeapon (wield) then EquipArmor (suit up).
    work = [type(s).__name__ for s in sword.planner().skills]
    at = work.index("WarriorHunt")
    assert work[at - 2 : at] == ["EquipWeapon", "EquipArmor"], work
    # `WarriorHunt`, not `Hunt`: it is a strict `Hunt` that refuses to engage bare-handed
    # (audit §61.9 — three deaths at one tile after three resurrections, `blade=NONE`).
    assert sword.work_skill is WarriorHunt
    assert issubclass(WarriorHunt, Hunt)
    # The economy planner (capability mode) builds — its manifest must pass, which
    # it only does because pre-work reflexes are excluded from capability mode.
    econ = sword.planner(capability_goals=True)
    assert set(econ.capability_ids) == {"bank_gold", "buy_weapon", "buy_bandage", "buy_armor", "upgrade_weapon"}
    econ_names = [type(s).__name__ for s in econ.skills]
    assert "EquipWeapon" not in econ_names and "EquipArmor" not in econ_names
    assert {cid for (p, cid) in CAPABILITIES if p == "swordsman"} == {"bank_gold", "buy_weapon", "buy_bandage", "buy_armor", "upgrade_weapon"}


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


def _hp_ctx(hits, *, hits_max=100, memory):
    obs = Observation(player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0),
                                        hits=hits, hits_max=hits_max), items=[])
    return SkillContext(obs=obs, persona=Persona(name="Bram"), memory=memory)


def test_warrior_survive_heals_to_a_safe_margin_before_re_engaging():
    # The living-test death-loop fix: once a heal starts, keep healing until HP
    # recovers to the 75% safe ceiling (hysteresis) rather than stopping at 41%.
    from anima2.skills.warrior import WarriorSurvive

    assert WarriorSurvive.heal_until_fraction == 0.75
    ws = WarriorSurvive()
    mem: dict = {}
    assert ws._wounded(_hp_ctx(30, memory=mem)) is True   # <40% -> heal starts (latch set)
    assert ws._wounded(_hp_ctx(50, memory=mem)) is True   # above trigger, below ceiling -> keep healing
    assert ws._wounded(_hp_ctx(74, memory=mem)) is True   # still under 75% -> keep healing
    assert ws._wounded(_hp_ctx(80, memory=mem)) is False  # >=75% -> safe, stop (latch cleared)
    # Fresh 50% HP with no active heal -> NOT wounded (never dropped below the trigger).
    assert ws._wounded(_hp_ctx(50, memory={})) is False


def test_base_survive_hysteresis_is_a_byte_identical_noop():
    # Default Survive (heal_until == heal_below == 0.40) must behave exactly as before:
    # heal below 40%, stop the instant HP crosses back above it.
    from anima2.skills.survival import Survive

    assert Survive.heal_until_fraction == Survive.heal_below_fraction == 0.40
    s = Survive()
    mem: dict = {}
    assert s._wounded(_hp_ctx(30, memory=mem)) is True    # <40% -> wounded
    assert s._wounded(_hp_ctx(45, memory=mem)) is False   # >=40% -> NOT wounded (no hysteresis)
    assert s._wounded(_hp_ctx(50, memory={})) is False


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


def test_a_warrior_leaves_melee_to_heal_at_all():
    """The other half of the death loop `heal_until_fraction` was raised for.

    ServUO does not interrupt a bandage when the healer is hit, it SLIPS it, and slips are
    charged against the heal (`toHeal -= toHeal * m_Slips * 0.35`). On non-AOS —
    this shard is T2A — `disruptThreshold` is 0, so EVERY point of damage slips. Bandaging
    inside melee is therefore void rather than risky, and the attacker COUNT never enters
    into it: one is enough to spend a bandage for nothing. Tried at two first; the tape
    then burned six bandages standing still against a single attacker and never rose,
    while the one recovery it did manage (54 -> 136) came after stepping three tiles clear.

    Pinned per-profession rather than globally: this is the warrior's melee arithmetic,
    and a caster or a crafter meeting two hostiles is a different question.
    """
    from anima2.skills.survival import Survive
    from anima2.skills.warrior import WarriorSurvive

    assert WarriorSurvive.flee_hostile_count == 1
    assert Survive.flee_hostile_count == 3, "the stock reflex must be unchanged"
    # And the margin it retreats to is still the warrior's, not the stock one.
    assert WarriorSurvive.heal_until_fraction == 0.75
    assert Survive.heal_until_fraction == 0.40

    # Five steps clears a melee reach of 1 with room to spare, which is what makes
    # retreating cheap against the village's PINNED prey.
    assert WarriorSurvive.max_flee_steps >= 2


def test_the_swordsman_profession_actually_uses_the_warrior_reflex():
    """A per-profession tuning that the profession does not install is a comment."""
    from anima2.profession import PROFESSIONS
    from anima2.skills.warrior import WarriorSurvive

    survive = PROFESSIONS["swordsman"].survive_factory()
    assert isinstance(survive, WarriorSurvive), type(survive)
    assert survive.flee_hostile_count == 1


def test_a_swordsman_does_not_engage_bare_handed():
    """Death drops the whole kit on the corpse, the ghost walks to a Healer, and
    `Combat.can_run` is true for anything inside `engage_range = 10` — so the resurrected
    character walks straight back in unarmed.

    Measured 2026-08-24 (audit §61.9): `BACK ALIVE` three times in one run and three
    deaths at the SAME tile `(2583, 408)`, `blade=NONE plate=0/6`.
    `docs/SWORD-WARRIOR.md` had already recorded that an unarmoured warrior is provably
    alpha-struck dead by three Ettins.

    The bar is the BLADE, not the suit — the same bar `village.ready_to_fight` uses.
    """
    from anima2.contract import MobileView, Position
    from anima2.skills.warrior import KATANA_GRAPHIC, WEAPON_LAYER, WarriorHunt

    ettin = MobileView(serial=0xAA, name="Ettin", pos=Position(101, 100, 0), body=1,
                       notoriety=6, hits=100, hits_max=100, distance=1)
    persona = Persona(name="Bram", combat_disposition="aggressive")

    def _hunt_ctx(items):
        obs = Observation(player=PlayerView(serial=PLAYER, pos=Position(100, 100, 0),
                                            hits=150, hits_max=150),
                          items=list(items), mobiles=[ettin])
        return SkillContext(obs=obs, persona=persona, memory={})

    naked = _hunt_ctx([_backpack()])
    assert not WarriorHunt().can_run(naked), "a ghost's leftovers are not a warrior"

    worn = _item(0x700, KATANA_GRAPHIC, container=PLAYER, layer=WEAPON_LAYER)
    assert WarriorHunt().can_run(_hunt_ctx([_backpack(), worn]))

    # A blade in the pack counts: `EquipWeapon` runs above this and will wield it, and
    # refusing here would stall the hunt for the tick between buying and wearing.
    packed = _item(0x701, KATANA_GRAPHIC)  # default container is the backpack
    assert WarriorHunt().can_run(_hunt_ctx([_backpack(), packed]))

    # A sword lying on the GROUND is not owned — container is not us and not our pack.
    loose = _item(0x702, KATANA_GRAPHIC, container=None)
    assert not WarriorHunt().can_run(_hunt_ctx([_backpack(), loose]))

    # ...nor is the one still lying in the CORPSE, which is the exact state this guard
    # exists for: `RecoverDeath` has not fetched it back yet.
    corpse = _item(0x703, KATANA_GRAPHIC, container=0xC0FFEE)
    assert not WarriorHunt().can_run(_hunt_ctx([_backpack(), corpse]))

    # AND IT DOES NOT CHASE OFF THE MAP. `Combat` learned to close (§59) and nothing
    # pulled the warrior back: a day with deaths=0 and six kills ended `@(2591,414)` with
    # `landed=2/36`, thirty-four bank frames given up from a tile the greedy market walk
    # could not leave. The leash is the one `Wander` already obeys.
    from anima2.skills.movement import Wander

    def _at(x, y, memory):
        obs = Observation(player=PlayerView(serial=PLAYER, pos=Position(x, y, 0),
                                            hits=150, hits_max=150),
                          items=[_backpack(), worn],
                          mobiles=[MobileView(serial=0xAA, name="Ettin",
                                              pos=Position(x + 1, y, 0), body=1,
                                              notoriety=6, hits=100, hits_max=100,
                                              distance=1)])
        return SkillContext(obs=obs, persona=persona, memory=memory)

    leashed = {"wander_home": (100, 100), "wander_leash": 4}
    assert WarriorHunt().can_run(_at(104, 100, dict(leashed))), "on the leash, engage"
    assert not WarriorHunt().can_run(_at(105, 100, dict(leashed))), "past it, come home"
    # No home configured = no leash, exactly as before this existed.
    assert WarriorHunt().can_run(_at(900, 900, {}))
    # A malformed home must read as "unleashed", never as "always outside".
    assert WarriorHunt().can_run(_at(900, 900, {"wander_home": "somewhere"}))
    # The default is `Wander`'s own, so the two cannot drift apart.
    far = {"wander_home": (100, 100)}
    assert WarriorHunt().can_run(_at(100 + Wander.leash, 100, dict(far)))
    assert not WarriorHunt().can_run(_at(100 + Wander.leash + 1, 100, dict(far)))

    # It is still a HUNT: armed with nothing to fight, it must yield the hands rather
    # than answer for every tick of a quiet pocket.
    quiet = Observation(player=PlayerView(serial=PLAYER, pos=Position(100, 100, 0),
                                          hits=150, hits_max=150),
                        items=[_backpack(), worn], mobiles=[])
    assert not WarriorHunt().can_run(
        SkillContext(obs=quiet, persona=persona, memory={}))
