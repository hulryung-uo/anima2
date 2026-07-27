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
    # Engagement + looting are still Hunt's, but gated: a caster that cannot cast must
    # not close to melee (`ArmedHunt`, which killed a live mage before it existed).
    assert issubclass(mage.work_skill, Hunt) and mage.work_skill is not Hunt
    assert mage.skills["Magery"] >= 80      # damage scales with Magery/EvalInt
    names = [type(s).__name__ for s in mage.planner().skills]
    # CastAttack sits directly above Hunt: spells first while a foe is in range.
    assert names[names.index("ArmedHunt") - 1] == "CastAttack"
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


def _kite_ctx(foe_distance, *, memory=None, pending=None, disposition="aggressive"):
    foe = MobileView(serial=FOE, name="Ettin", pos=Position(5 + foe_distance, 5, 0), body=1,
                     notoriety=3, hits=40, hits_max=60, distance=foe_distance)
    return _ctx([_backpack(), _ash()], [foe], memory=memory, pending=pending,
                disposition=disposition)


def test_kiting_steps_away_only_while_a_foe_is_in_melee():
    """A caster's damage happens at range, so a creature in melee must be backed away from
    — unlike a warrior, which wants contact. `Survive` only retreats once already badly
    wounded (below 40% HP), far too late for a frail mage; this is the tactical version."""
    from anima2.contract import Walk
    from anima2.skills.mage import KeepDistance

    kite = KeepDistance()
    mem: dict = {}
    # Adjacent -> back away.
    ctx = _kite_ctx(1, memory=mem)
    assert kite.can_run(ctx) is True
    assert isinstance(kite.step(ctx).action, Walk)
    # Gap opened past the band -> stop kiting (so CastAttack gets the tick).
    assert kite.can_run(_kite_ctx(kite.too_close + 1, memory=mem)) is False


def test_kiting_is_bounded_so_the_mage_never_just_walks_away():
    from anima2.skills.mage import KeepDistance

    kite = KeepDistance()
    mem: dict = {}
    for _ in range(kite.max_steps):
        ctx = _kite_ctx(1, memory=mem)
        assert kite.can_run(ctx) is True
        kite.step(ctx)
    # Budget spent: stand and fight (CastAttack still works in melee; Survive owns danger).
    assert kite.can_run(_kite_ctx(1, memory=mem)) is False


def test_kiting_budget_recovers_once_the_gap_is_held():
    from anima2.skills.mage import KeepDistance

    kite = KeepDistance()
    mem: dict = {}
    for _ in range(kite.max_steps):
        kite.step(_kite_ctx(1, memory=mem))
    assert kite.can_run(_kite_ctx(1, memory=mem)) is False       # spent
    for _ in range(kite.reset_ticks):
        kite.can_run(_kite_ctx(6, memory=mem))                    # gap held open
    assert kite.can_run(_kite_ctx(1, memory=mem)) is True         # ready for the next rush


def test_kiting_never_abandons_a_cast_mid_incantation():
    # A target cursor is up: answer it (CastAttack's job) before moving a tile.
    from anima2.skills.mage import KeepDistance

    cursor = TargetCursor(target_type=0, cursor_id=1, cursor_flag=1)
    assert KeepDistance().can_run(_kite_ctx(1, pending=cursor)) is False


def test_the_mage_kites_before_it_casts():
    from anima2.profession import PROFESSIONS

    names = [type(s).__name__ for s in PROFESSIONS["mage"].planner().skills]
    assert names.index("KeepDistance") < names.index("CastAttack") < names.index("ArmedHunt")
    # Survival still outranks tactics — heal before repositioning.
    assert names.index("Survive") < names.index("KeepDistance")


# --- ArmedHunt: a caster that cannot cast must not close to melee -------------------
#
# Live-caught, with an unusually clean trace: while the mage had ash its HP ROSE across
# the whole fight (82 -> 84) because kiting worked and the pinned prey never landed a
# blow. The tick its pouch hit 0, HP fell 84 -> 78 -> 70 -> 59 -> ... to death — `Hunt`
# kept marching it back into an Ettin it could no longer hurt. Only its ability to
# fight changed at that tick; not the placement, not the creature.

from anima2.skills.mage import ArmedHunt  # noqa: E402


def _hunt_ctx(*, ash: int, mana: int, memory=None, distance: int = 3):
    items = [_backpack()]
    if ash:
        items.append(_ash(ash))
    obs = Observation(
        player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0), mana=mana, hits=80, hits_max=90),
        mobiles=[_foe(distance=distance)],
        items=items,
    )
    return SkillContext(obs=obs, persona=Persona(name="Elara", combat_disposition="aggressive"),
                        memory=dict(memory or {}))


def test_an_armed_mage_engages_exactly_like_hunt():
    assert ArmedHunt().can_run(_hunt_ctx(ash=20, mana=100)) is True


def test_an_empty_pouch_stops_the_mage_closing_to_melee():
    # The bug: this returned True and walked a defenceless caster into an Ettin.
    assert ArmedHunt().can_run(_hunt_ctx(ash=0, mana=100)) is False


def test_no_mana_also_stops_it():
    # Reagents alone are not an attack. Waiting a few ticks for mana is correct for a
    # caster; punching the thing it cannot hurt is not.
    assert ArmedHunt().can_run(_hunt_ctx(ash=20, mana=MAGIC_ARROW_MANA - 1)) is False
    assert ArmedHunt().can_run(_hunt_ctx(ash=20, mana=MAGIC_ARROW_MANA)) is True


def test_looting_is_never_blocked_by_being_disarmed():
    # Retiring a corpse already earned is free, and the gold on it is what buys the next
    # reagent batch — gating that would starve the loop that ENDS the disarmed state.
    for mem in ({"hunt_queue": [0x1234]}, {"hunt_phase": "loot"}):
        assert ArmedHunt().can_run(_hunt_ctx(ash=0, mana=0, memory=mem)) is True


def test_it_says_why_it_will_not_engage():
    reason = ArmedHunt().diagnose(_hunt_ctx(ash=0, mana=100))
    assert reason is not None and "reagents" in reason


def test_a_pacifist_mage_still_never_engages():
    ctx = _hunt_ctx(ash=20, mana=100)
    ctx.persona.combat_disposition = "pacifist"
    assert ArmedHunt().can_run(ctx) is False


def test_the_bare_handed_hunter_keeps_plain_hunt():
    # Fists ARE that profession's attack, so contact is right for it — the fix must not
    # leak across and make it refuse to fight.
    from anima2.profession import PROFESSIONS
    from anima2.skills.hunt import Hunt

    assert PROFESSIONS["hunter"].work_skill is Hunt
    assert PROFESSIONS["mage"].work_skill is ArmedHunt
