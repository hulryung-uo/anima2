"""Mage — `CastAttack`'s two-step cast (CastSpell -> answer the target cursor) and its
mana/reagent gating, on hand-built observations. Engagement + looting are `Hunt`'s (its
own tests cover them); this targets what is MAGE-specific: fighting with a spell."""

from anima2.contract import (
    CastSpell,
    ItemView,
    MobileView,
    Observation,
    PlayerView,
    Position,
    TargetCursor,
    TargetObject,
)
from anima2.persona import Persona
from anima2.skills.base import SkillContext, Status
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.mage import (
    MAGIC_ARROW_MANA,
    MAGIC_ARROW_SPELL,
    REAGENT_GRAPHICS,
    SULFUROUS_ASH_GRAPHIC,
    BuyReagent,
    CastAttack,
)

PLAYER = 1
BACKPACK = 0x50
FOE = 0x300


def _item(serial, graphic, amount=1, *, container=BACKPACK, layer=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=0)


def _backpack():
    return _item(BACKPACK, 0x0E75, container=PLAYER, layer=BACKPACK_LAYER)


def _ash(amount=20):
    return _item(0x900, SULFUROUS_ASH_GRAPHIC, amount)


def _foe(serial=FOE, distance=3, hits=40, hits_max=60):
    # notoriety 3 == "grey"/attackable in the contract's own hostility rule.
    return MobileView(serial=serial, name="Ettin", pos=Position(8, 5, 0), body=1,
                      notoriety=3, hits=hits, hits_max=hits_max, distance=distance)


def _ctx(items, mobiles, *, mana=50, memory=None, pending=None, disposition="aggressive"):
    obs = Observation(
        player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0), hits=60, hits_max=60,
                          mana=mana, mana_max=50),
        items=list(items), mobiles=list(mobiles), pending_target=pending,
    )
    return SkillContext(obs=obs, persona=Persona(name="Elara", combat_disposition=disposition),
                        memory={} if memory is None else memory)


def test_magic_arrow_uses_the_servuo_wire_id_and_its_reagent():
    # ServUO registers MagicArrow at index 04 and the cast handler adds one, so the WIRE
    # id is 5; its reagent is SulfurousAsh (art 0xF8C).
    assert MAGIC_ARROW_SPELL == 5
    assert SULFUROUS_ASH_GRAPHIC == 0x0F8C
    assert REAGENT_GRAPHICS == frozenset({SULFUROUS_ASH_GRAPHIC})
    assert CastAttack.spell == MAGIC_ARROW_SPELL


def test_cast_attack_casts_then_aims_at_the_hostile():
    skill = CastAttack()
    mem: dict = {}

    # Tick 1: a hostile in range, mana and reagents in hand -> begin the incantation.
    ctx1 = _ctx([_backpack(), _ash()], [_foe()], memory=mem)
    assert skill.can_run(ctx1) is True
    r1 = skill.step(ctx1)
    assert isinstance(r1.action, CastSpell) and r1.action.spell == MAGIC_ARROW_SPELL

    # Tick 2: the server opened its target cursor -> aim the bolt at the foe.
    cursor = TargetCursor(target_type=0, cursor_id=1, cursor_flag=1)
    ctx2 = _ctx([_backpack(), _ash()], [_foe()], memory=mem, pending=cursor)
    assert skill.can_run(ctx2) is True  # mid-cast: finish it, don't abandon the cursor
    r2 = skill.step(ctx2)
    assert isinstance(r2.action, TargetObject) and r2.action.serial == FOE
    assert r2.reward > 0  # the confirmation rides the aim, not the (refusable) cast
    assert mem.get(skill._PHASE) is None  # cast state cleared for the next one


def test_cast_attack_is_gated_on_mana_and_reagents():
    skill = CastAttack()
    # Drained -> inert (a cast the server would refuse just burns ticks).
    assert skill.can_run(_ctx([_backpack(), _ash()], [_foe()], mana=MAGIC_ARROW_MANA - 1)) is False
    # No reagent -> inert (this is what makes buy_reagent load-bearing).
    assert skill.can_run(_ctx([_backpack()], [_foe()])) is False
    # Supplied and charged -> ready.
    assert skill.can_run(_ctx([_backpack(), _ash()], [_foe()])) is True


def test_cast_attack_is_inert_without_a_target_or_for_a_pacifist():
    skill = CastAttack()
    assert skill.can_run(_ctx([_backpack(), _ash()], [])) is False              # nothing to fight
    assert skill.can_run(_ctx([_backpack(), _ash()], [_foe(distance=99)])) is False  # out of range
    assert skill.can_run(_ctx([_backpack(), _ash()], [_foe()], disposition="pacifist")) is False


def test_cast_attack_sees_a_foe_whose_hp_is_not_reported_yet():
    # Regression (live-caught): a creature the caster has not attacked yet reports
    # `hits == 0` in the observation until the server sends a status update for it. A
    # health gate therefore makes the mage blind to every fresh foe — it stood beside a
    # pinned Ettin for 200 ticks without casting. Targeting must use hostility + range
    # only, exactly like `Combat._target`.
    skill = CastAttack()
    # Live shape: NEITHER hits nor hits_max reported yet (a reported hits_max with
    # hits<=0 would mean observably DEAD, which is correctly skipped).
    unknown_hp = _foe(hits=0, hits_max=0)
    ctx = _ctx([_backpack(), _ash()], [unknown_hp])
    assert skill.can_run(ctx) is True
    assert isinstance(skill.step(ctx).action, CastSpell)


def test_cast_attack_never_steals_another_skill_s_cursor():
    # A bandage/loot cursor is open and we are NOT mid-cast -> yield to its owner.
    skill = CastAttack()
    cursor = TargetCursor(target_type=0, cursor_id=1, cursor_flag=2)
    assert skill.can_run(_ctx([_backpack(), _ash()], [_foe()], pending=cursor)) is False


def test_a_refused_cast_gives_up_instead_of_waiting_forever():
    # The server never offers a cursor (it judged mana/reagents differently). The skill
    # must time out and yield rather than hang the planner on a dead cast.
    skill = CastAttack()
    mem: dict = {}
    skill.step(_ctx([_backpack(), _ash()], [_foe()], memory=mem))  # CastSpell sent
    for _ in range(skill.cursor_timeout_ticks):
        r = skill.step(_ctx([_backpack(), _ash()], [_foe()], memory=mem))  # no cursor
        assert r.status is Status.RUNNING
    r = skill.step(_ctx([_backpack(), _ash()], [_foe()], memory=mem))
    assert r.status is Status.FAILURE
    assert mem.get(skill._PHASE) is None


def test_cast_attack_re_aims_when_the_remembered_victim_is_gone():
    # The first foe died while the incantation ran; a live cursor must not be wasted.
    skill = CastAttack()
    mem: dict = {}
    skill.step(_ctx([_backpack(), _ash()], [_foe(serial=FOE)], memory=mem))
    cursor = TargetCursor(target_type=0, cursor_id=1, cursor_flag=1)
    other = _foe(serial=0x301)
    r = skill.step(_ctx([_backpack(), _ash()], [other], memory=mem, pending=cursor))
    assert isinstance(r.action, TargetObject) and r.action.serial == 0x301


def test_a_spell_victim_is_recorded_so_hunt_loots_the_corpse():
    # Regression (live-caught): `Hunt` decides a corpse is ours to loot from
    # `hunt_attacked` — the serials `Combat` sent an `Attack` for. A mage kills with
    # SPELLS and never sends Attack, so without recording its victims it bolts a creature
    # down and then walks away from the corpse (observed: prey driven to 4 hp, kills=0,
    # nothing looted). Aiming a bolt must fill the same ledger.
    skill = CastAttack()
    mem: dict = {}
    skill.step(_ctx([_backpack(), _ash()], [_foe()], memory=mem))          # cast
    cursor = TargetCursor(target_type=0, cursor_id=1, cursor_flag=1)
    skill.step(_ctx([_backpack(), _ash()], [_foe()], memory=mem, pending=cursor))  # aim
    assert FOE in mem.get("hunt_attacked", ()), "the spell victim must be attributable"
    # Repeat bolts at the same victim don't grow the ledger.
    skill.step(_ctx([_backpack(), _ash()], [_foe()], memory=mem))
    skill.step(_ctx([_backpack(), _ash()], [_foe()], memory=mem, pending=cursor))
    assert list(mem["hunt_attacked"]).count(FOE) == 1


def test_buy_reagent_config_restocks_ash_from_the_mage_vendor():
    assert BuyReagent.buy_offer_graphic == SULFUROUS_ASH_GRAPHIC
    assert BuyReagent.buy_material_graphics == REAGENT_GRAPHICS
    assert BuyReagent.buy_price_estimate == 3          # SBMage @3g
    assert BuyReagent.buy_amount == 20                 # the vendor's batch size
    assert BuyReagent.vendor_spot_key == "mage_vendor_spot"


def test_mage_profession_casts_above_its_work_skill():
    from anima2.profession import PROFESSIONS
    from anima2.skills.hunt import Hunt

    mage = PROFESSIONS["mage"]
    assert mage.work_skill is Hunt          # engagement + looting are reused unchanged
    assert mage.skills["Magery"] >= 80      # damage scales with Magery/EvalInt
    names = [type(s).__name__ for s in mage.planner().skills]
    # CastAttack sits directly above Hunt: spells first while a foe is in range.
    assert names[names.index("Hunt") - 1] == "CastAttack"
    # Survive still outranks casting — heal before trading blows.
    assert names.index("Survive") < names.index("CastAttack")


def test_the_production_pipeline_is_wired_end_to_end():
    """The goal's arc: a production skill makes wares, they sell for gold, and that gold
    raises a fighter. Each link is a registered capability, and the hand-off reuses the
    lumberjack/carpenter ground drop+pickup machinery pointed at gold."""
    from anima2.capabilities import CAPABILITIES
    from anima2.skills.hunt import GOLD_GRAPHIC
    from anima2.skills.mage import FetchGold
    from anima2.skills.tinkering import DeliverGold

    tinker = {cid for (p, cid) in CAPABILITIES if p == "tinker"}
    mage = {cid for (p, cid) in CAPABILITIES if p == "mage"}
    # MAKE -> SELL -> (bank) is the tinker's proven loop; deliver_gold is the new hand-off.
    assert {"craft_tongs", "sell_tongs", "bank_gold", "deliver_gold"} <= tinker
    # The fighter collects that purse, turns it into reagents, and banks the surplus.
    assert mage == {"fetch_gold", "buy_reagent", "bank_gold"}

    # The hand-off moves GOLD, and the two halves agree on where.
    assert DeliverGold.delivered_graphics == frozenset({GOLD_GRAPHIC})
    assert FetchGold.fetched_graphics == frozenset({GOLD_GRAPHIC})
    assert DeliverGold.drop_key == "mage_drop"
    # Worth a trip: a purse that buys several reagent batches, not a handful of coins.
    from anima2.skills.mage import BuyReagent
    assert DeliverGold.deliver_threshold >= BuyReagent.buy_amount * BuyReagent.buy_price_estimate


def test_the_mage_economy_planner_builds():
    from anima2.profession import PROFESSIONS

    econ = PROFESSIONS["mage"].planner(capability_goals=True)
    assert set(econ.capability_ids) == {"fetch_gold", "buy_reagent", "bank_gold"}
    # Pre-work reflexes stay out of capability mode (the manifest's fixed shape).
    assert "CastAttack" not in [type(s).__name__ for s in econ.skills]
