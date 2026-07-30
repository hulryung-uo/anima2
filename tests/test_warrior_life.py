"""WarriorLife.decide_mode — the autonomous hunt <-> re-arm switch, on hand-built
observations. The mode decision is a pure function so its priority and gates can be
pinned without a live body."""

from anima2.contract import ItemView, Observation, PlayerView, Position
from anima2.persona import Persona
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.hunt import GOLD_GRAPHIC
from anima2.skills.warrior import (
    BANDAGE_GRAPHIC,
    KATANA_GRAPHIC,
    PLATE_ARMOR_LAYERS,
    PLATE_CHEST_GRAPHIC,
    WEAPON_LAYER,
)
from anima2.warrior_life import (
    ARMOR_PRICE,
    BANDAGE_BATCH_COST,
    BANK_ABOVE,
    LOW_BANDAGES,
    WEAPON_PRICE,
    decide_mode,
)

PLAYER = 1
BP = 0x50
ROUTES = {"weapon_vendor_spot": ((10, 10),), "healer_spot": ((10, 10),),
          "banker_spot": ((10, 10),), "armorer_spot": ((10, 10),)}


def _item(serial, graphic, amount=1, *, container=BP, layer=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=0)


def _obs(items, *, dead=False):
    return Observation(player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0), dead=dead),
                       items=list(items))


def _backpack():
    return _item(BP, 0x0E75, container=PLAYER, layer=BACKPACK_LAYER)


def _worn_chest():
    return _item(0x800, PLATE_CHEST_GRAPHIC, container=PLAYER,
                 layer=PLATE_ARMOR_LAYERS[PLATE_CHEST_GRAPHIC])


def _worn_katana():
    return _item(0x700, KATANA_GRAPHIC, container=PLAYER, layer=WEAPON_LAYER)


def _gold(n):
    return _item(0x900, GOLD_GRAPHIC, n)


def _bandages(n):
    return _item(0x901, BANDAGE_GRAPHIC, n)


def test_armed_and_supplied_warrior_hunts():
    obs = _obs([_backpack(), _worn_katana(), _worn_chest(), _bandages(50), _gold(100)])
    assert decide_mode(obs, dict(ROUTES)) == ("hunt", None)


def test_weaponless_with_gold_and_vendor_rearms_first():
    obs = _obs([_backpack(), _bandages(50), _gold(100)])  # lost the blade
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "buy_weapon")


def test_weaponless_but_no_vendor_route_keeps_hunting():
    # No vendor configured -> can't shop -> keep hunting (fight bare-handed) rather
    # than stall at a shop it can't reach.
    obs = _obs([_backpack(), _bandages(50), _gold(100)])
    assert decide_mode(obs, {}) == ("hunt", None)


def test_weaponless_but_broke_keeps_hunting():
    obs = _obs([_backpack(), _bandages(50), _gold(WEAPON_PRICE - 1)])
    assert decide_mode(obs, dict(ROUTES)) == ("hunt", None)


def test_low_bandages_restocks():
    obs = _obs([_backpack(), _worn_katana(), _worn_chest(),
                _bandages(LOW_BANDAGES - 1), _gold(BANDAGE_BATCH_COST)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "buy_bandage")


def test_surplus_gold_banks():
    obs = _obs([_backpack(), _worn_katana(), _worn_chest(), _bandages(50), _gold(BANK_ABOVE + 1)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "bank_gold")


def test_weaponless_takes_priority_over_low_bandages():
    obs = _obs([_backpack(), _bandages(1), _gold(200)])  # both weaponless AND dry
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "buy_weapon")  # blade first


def test_a_packed_katana_counts_as_armed():
    # A just-bought Katana in the pack (not yet worn) means armed -> don't re-buy.
    obs = _obs([_backpack(), _item(0x701, KATANA_GRAPHIC), _worn_chest(),
                _bandages(50), _gold(100)])
    assert decide_mode(obs, dict(ROUTES)) == ("hunt", None)


def test_lost_chest_plate_triggers_an_armor_rebuy():
    # Died, corpse unreclaimed -> no chest. Fighting rich prey unarmored is fatal (a
    # living test proved it), so with gold + an armorer the warrior goes and replaces it.
    obs = _obs([_backpack(), _worn_katana(), _bandages(50), _gold(ARMOR_PRICE)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "buy_armor")


def test_chestless_but_broke_or_unrouted_keeps_hunting():
    # Can't afford a chest -> keep hunting and earn toward one, don't stall at the shop.
    poor = _obs([_backpack(), _worn_katana(), _bandages(50), _gold(ARMOR_PRICE - 1)])
    assert decide_mode(poor, dict(ROUTES)) == ("hunt", None)
    # No armorer route configured -> nothing to divert to.
    routes = {k: v for k, v in ROUTES.items() if k != "armorer_spot"}
    rich = _obs([_backpack(), _worn_katana(), _bandages(50), _gold(ARMOR_PRICE)])
    assert decide_mode(rich, routes) == ("hunt", None)


def test_blade_and_bandages_outrank_armor():
    # Priority: you cannot hunt without a blade, nor survive without bandages; armor is
    # the third need, ahead of banking.
    no_blade = _obs([_backpack(), _bandages(50), _gold(500)])          # also chestless
    assert decide_mode(no_blade, dict(ROUTES)) == ("economy", "buy_weapon")
    dry = _obs([_backpack(), _worn_katana(), _bandages(1), _gold(500)])  # also chestless
    assert decide_mode(dry, dict(ROUTES)) == ("economy", "buy_bandage")
    # ...and armor outranks banking a surplus.
    rich = _obs([_backpack(), _worn_katana(), _bandages(50), _gold(BANK_ABOVE + 1)])
    assert decide_mode(rich, dict(ROUTES)) == ("economy", "buy_armor")


def test_a_weaker_worn_blade_is_traded_up_once_there_is_surplus():
    from anima2.skills.warrior import CUTLASS_GRAPHIC
    from anima2.warrior_life import UPGRADE_RESERVE

    def _worn_cutlass():
        return _item(0x702, CUTLASS_GRAPHIC, container=PLAYER, layer=WEAPON_LAYER)

    surplus = WEAPON_PRICE + UPGRADE_RESERVE
    # Armed with a weaker blade, fed, armored, and rich enough to keep a re-arm reserve.
    obs = _obs([_backpack(), _worn_cutlass(), _worn_chest(), _bandages(50), _gold(surplus)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "upgrade_weapon")
    # One coin short of the reserve -> growth waits; keep hunting.
    lean = _obs([_backpack(), _worn_cutlass(), _worn_chest(), _bandages(50), _gold(surplus - 1)])
    assert decide_mode(lean, dict(ROUTES)) == ("hunt", None)
    # Already wielding the best blade -> no upgrade (banks the surplus instead).
    best = _obs([_backpack(), _worn_katana(), _worn_chest(), _bandages(50), _gold(BANK_ABOVE + 1)])
    assert decide_mode(best, dict(ROUTES)) == ("economy", "bank_gold")


def test_survival_needs_outrank_a_blade_upgrade():
    from anima2.skills.warrior import CUTLASS_GRAPHIC
    from anima2.warrior_life import UPGRADE_RESERVE

    worn_cutlass = _item(0x702, CUTLASS_GRAPHIC, container=PLAYER, layer=WEAPON_LAYER)
    rich = _gold(WEAPON_PRICE + UPGRADE_RESERVE + BANK_ABOVE)
    # Dry on bandages while holding a weaker blade -> restock first, upgrade later.
    dry = _obs([_backpack(), worn_cutlass, _worn_chest(), _bandages(1), rich])
    assert decide_mode(dry, dict(ROUTES)) == ("economy", "buy_bandage")
    # Chestless -> armor first, upgrade later.
    bare = _obs([_backpack(), worn_cutlass, _bandages(50), rich])
    assert decide_mode(bare, dict(ROUTES)) == ("economy", "buy_armor")


def test_a_dead_warrior_yields_to_recover_death():
    # Dead + weaponless (gear dropped): the hunt planner's RecoverDeath reflex owns the
    # death window; do NOT divert to the economy while a ghost.
    obs = _obs([_backpack(), _gold(100)], dead=True)
    assert decide_mode(obs, dict(ROUTES)) == ("hunt", None)


class _MockBody:
    connected = True

    def __init__(self, obs_seq):
        self._it = iter(obs_seq)
        self._last = None

    def observe(self):
        try:
            self._last = next(self._it)
        except StopIteration:
            pass
        return self._last

    def act(self, action):
        pass


def test_econ_switch_has_hysteresis_against_a_transient_weaponless_blip():
    # The mid-equip window (blade on the cursor, gone from `items`) reads "weaponless"
    # for a tick or two — the orchestrator must NOT divert to the economy then, or it
    # would interrupt EquipWeapon and strand the blade. It commits only after the
    # condition persists past ECON_GRACE.
    from anima2.warrior_life import ECON_GRACE, WarriorLife

    weaponless = _obs([_backpack(), _bandages(50), _gold(100)])
    life = WarriorLife(body=_MockBody([weaponless] * (ECON_GRACE + 2)),
                       persona=Persona(name="Bram"),
                       routes={"weapon_vendor_spot": ((10, 10),)})
    for i in range(ECON_GRACE - 1):
        life.tick()
        assert life.mode == "hunt", f"tick {i}: within grace must stay hunt"
    life.tick()  # grace elapsed -> commit
    assert life.mode == "economy" and life.target_cap == "buy_weapon"


def test_hysteresis_resets_when_the_blade_is_wielded_within_grace():
    # A real mid-equip resolves inside the grace: a couple weaponless ticks, then the
    # blade is worn -> the streak resets and the warrior never diverts.
    from anima2.warrior_life import WarriorLife

    seq = ([_obs([_backpack(), _gold(100)])] * 2                      # mid-equip blip
           + [_obs([_backpack(), _worn_katana(), _gold(100)])] * 6)   # equipped
    life = WarriorLife(body=_MockBody(seq), persona=Persona(name="Bram"),
                       routes={"weapon_vendor_spot": ((10, 10),)})
    for _ in range(6):
        life.tick()
        assert life.mode == "hunt"  # equip resolved within grace -> never diverts


def test_a_leash_binds_both_agents_not_just_the_one_currently_ticking():
    # Each agent owns its own memory and BOTH planners end in `Wander`, which reads
    # whichever agent is ticking. Live-caught with the carpenter — a profession with no
    # work skill, so its ECONOMY agent runs nearly every tick — whose leash sat on the
    # hunt agent's memory while the economy agent wandered three tiles off its supply
    # drop, onto ground it could not walk back from.
    from anima2.persona import Persona
    from anima2.warrior_life import WarriorLife

    class _Body:
        connected = True
        ready = {"player": {"serial": 1}}

        def observe(self):
            return _obs([])

        def act(self, action):
            pass

    life = WarriorLife(body=_Body(), persona=Persona(name="Bram"))
    life.set_leash((10, 20), 3)
    for memory in (life.hunt_agent.memory, life.econ_agent.memory):
        assert memory["wander_home"] == (10, 20)
        assert memory["wander_leash"] == 3
    # A home with no explicit leash leaves `Wander`'s own default in charge.
    life2 = WarriorLife(body=_Body(), persona=Persona(name="Bram"))
    life2.set_leash((1, 2))
    for memory in (life2.hunt_agent.memory, life2.econ_agent.memory):
        assert memory["wander_home"] == (1, 2) and "wander_leash" not in memory


# --- the rule-vs-gate disagreement detector ------------------------------------------
#
# Six live failures shared one state: the rule WANTED an economy capability, no goal was
# admitted, and the gate refused — for whole runs, with no outward signature. The
# orchestrator now detects that state itself instead of leaving it to bespoke telemetry.

def _rich_unarmed_obs():
    # A warrior with no weapon and plenty of gold: the rule wants buy_weapon.
    return _obs([_backpack(), _gold(500)])


def test_a_persistent_disagreement_is_flagged_after_the_grace_window():
    # The rule and the gate now literally read one dict (audit #5), so the old drift
    # avenue — a route the rule sees and the gate does not — is structurally closed.
    # What can STILL diverge is state the rule does not model: a market-FSM key
    # (`bs_state="fetch"`) left behind by a wedged trip blocks every buy gate while the
    # rule keeps wanting the purchase. That is the live shape this detector exists for.
    from anima2.warrior_life import DISAGREEMENT_TICKS, ECON_GRACE, WarriorLife

    obs = _rich_unarmed_obs()
    life = WarriorLife(body=_MockBody([obs] * (ECON_GRACE + DISAGREEMENT_TICKS + 4)),
                       persona=Persona(name="Bram"),
                       routes={"weapon_vendor_spot": ((10, 10),)})
    life.econ_agent.memory["bs_state"] = "fetch"  # a stale mid-fetch marker
    for _ in range(ECON_GRACE + DISAGREEMENT_TICKS + 2):
        life.tick()
    assert life.rule_gate_disagreement is not None
    cap, streak = life.rule_gate_disagreement
    assert cap == "buy_weapon" and streak >= DISAGREEMENT_TICKS


def test_a_healthy_admitted_goal_never_trips_the_detector():
    # Gates de-assert MID-transaction by design (a buy in flight holds a goal and shows
    # not-ready) — the no-goal guard must make that invisible to the detector.
    from anima2.warrior_life import DISAGREEMENT_TICKS, ECON_GRACE, WarriorLife

    obs = _rich_unarmed_obs()
    life = WarriorLife(body=_MockBody([obs] * (ECON_GRACE + DISAGREEMENT_TICKS + 4)),
                       persona=Persona(name="Bram"),
                       routes={"weapon_vendor_spot": ((10, 10),)})
    for _ in range(ECON_GRACE + DISAGREEMENT_TICKS + 2):
        life.tick()
    # The gate agrees here (route present in econ memory, gold in pack), so whether or
    # not a goal is admitted, no disagreement may be reported.
    assert life.rule_gate_disagreement is None


def test_the_flag_clears_when_the_disagreement_resolves():
    from anima2.warrior_life import DISAGREEMENT_TICKS, ECON_GRACE, WarriorLife

    obs = _rich_unarmed_obs()
    life = WarriorLife(body=_MockBody([obs] * (ECON_GRACE + DISAGREEMENT_TICKS * 2 + 6)),
                       persona=Persona(name="Bram"),
                       routes={"weapon_vendor_spot": ((10, 10),)})
    life.econ_agent.memory["bs_state"] = "fetch"
    for _ in range(ECON_GRACE + DISAGREEMENT_TICKS + 2):
        life.tick()
    assert life.rule_gate_disagreement is not None
    # Heal the drift: the wedged market state clears, the gate opens again.
    life.econ_agent.memory["bs_state"] = "open"
    life.tick()
    assert life.rule_gate_disagreement is None


# --- the tuning knobs (audit #5) -----------------------------------------------------
#
# Thresholds became constructor parameters so genome axes / bandit tuning / slow-loop
# steering can touch them — but each knob routes through its SINGLE source (the
# `bank_reserve` memory key the rule, the gate, and BankGold's FSM all read), so tuning
# one side cannot recreate the rule-vs-gate drift class.

def test_a_tuned_bank_reserve_moves_the_rule_and_the_gate_together():
    from anima2.capabilities import ready_capability_ids
    from anima2.skills.base import SkillContext
    from anima2.warrior_life import WarriorLife

    life = WarriorLife(body=_MockBody([]), persona=Persona(name="Bram"),
                       routes={"banker_spot": ((10, 10),)}, bank_reserve=77)
    mem = life.econ_agent.memory
    assert mem["bank_reserve"] == 77

    at = _obs([_backpack(), _worn_katana(), _worn_chest(), _bandages(50), _gold(77)])
    above = _obs([_backpack(), _worn_katana(), _worn_chest(), _bandages(50), _gold(78)])
    # The RULE flips exactly at the knob...
    assert WarriorLife.decide(at, mem) == ("hunt", None)
    assert WarriorLife.decide(above, mem) == ("economy", "bank_gold")
    # ...and the GATE flips at the same coin, reading the same key.
    assert "bank_gold" not in ready_capability_ids(
        "swordsman", SkillContext(obs=at, persona=life.persona, memory=mem))
    assert "bank_gold" in ready_capability_ids(
        "swordsman", SkillContext(obs=above, persona=life.persona, memory=mem))


def test_a_tuned_econ_grace_changes_the_hysteresis_window():
    from anima2.warrior_life import WarriorLife

    weaponless = _obs([_backpack(), _bandages(50), _gold(100)])
    life = WarriorLife(body=_MockBody([weaponless] * 6),
                       persona=Persona(name="Bram"),
                       routes={"weapon_vendor_spot": ((10, 10),)}, econ_grace=2)
    life.tick()
    assert life.mode == "hunt"          # tick 1: within the tuned grace
    life.tick()
    assert life.mode == "economy"       # tick 2: tuned grace elapsed


def test_every_life_writes_its_own_derived_reserve_at_construction():
    from anima2.carpenter_life import CarpenterLife
    from anima2.mage_life import MageLife
    from anima2.warrior_life import WarriorLife
    from anima2.woodsman_life import WoodsmanLife

    for cls in (WarriorLife, MageLife, WoodsmanLife, CarpenterLife):
        life = cls(body=_MockBody([]), persona=Persona(name="T"))
        assert life.econ_agent.memory["bank_reserve"] == cls.DEFAULT_BANK_RESERVE > 0, (
            f"{cls.__name__}: a zero reserve banks the working capital too — the "
            f"threshold-vs-reserve conflation this knob exists to prevent"
        )


# --- the detector as a repair: an unowned gump refuses every capability ---------------

def _stale_ui_life():
    """A TinkerLife wanting `fetch_iron` with delivered iron at its feet, and a gump
    left open by nobody — the forge15 wedge, offline."""
    from anima2.contract import GumpView, ItemView, PlayerView, Position
    from anima2.mock_body import MockBody
    from anima2.persona import Persona
    from anima2.skills.harvest import BACKPACK_LAYER
    from anima2.skills.smelt import INGOT_GRAPHICS
    from anima2.skills.tinkering import TINKERTOOLS_GRAPHIC
    from anima2.tinker_life import TinkerLife

    IRON = sorted(INGOT_GRAPHICS)[0]
    PIM, PACK = 0x1, 0xB1
    items = {
        PACK: ItemView(serial=PACK, graphic=0x0E75, amount=1, pos=Position(),
                       container=PIM, layer=BACKPACK_LAYER, distance=0),
        0x100: ItemView(serial=0x100, graphic=TINKERTOOLS_GRAPHIC, amount=1,
                        pos=Position(), container=PACK, layer=0, distance=0),
        0x200: ItemView(serial=0x200, graphic=IRON, amount=38, pos=Position(11, 10, 0),
                        container=None, layer=0, distance=1),
    }
    body = MockBody(player=PlayerView(serial=PIM, name="Pim",
                                      pos=Position(10, 10, 0)), items=items)
    stale = GumpView(serial=PIM, gump_id=0x5AFE, elements=[])
    body.gumps.append(stale)  # nobody's gump: no goal ever admitted it
    life = TinkerLife(body=body, persona=Persona(name="Pim"),
                      routes={"vendor_spot": ((12, 10),), "banker_spot": ((10, 12),)})
    life.set_leash((10, 10), 3)
    for m in (life.memory, life.econ_agent.memory):
        m["craft_spot"] = (10, 10)
    return life, body, stale


def test_an_unowned_gump_is_closed_once_the_detector_fires():
    from anima2.contract import GumpResponse
    from anima2.warrior_life import DISAGREEMENT_TICKS

    life, body, stale = _stale_ui_life()
    fired = False
    # ECON_GRACE has to elapse before the mode even becomes "economy", then
    # DISAGREEMENT_TICKS of streak on top — the detector is deliberately slow.
    for _ in range(DISAGREEMENT_TICKS * 4):
        life.tick()
        fired = fired or life.rule_gate_disagreement is not None
        if not body.gumps:
            break
    assert fired, "the wedge never reported itself"  # the detector saw it
    closes = [a for a in body.actions
              if isinstance(a, GumpResponse) and a.button == 0
              and a.gump_id == stale.gump_id]
    assert closes, "the detector reported the wedge but never repaired it"
    assert not body.gumps, "the gump survived its own close"
    # And the repair RESTORES the economy: with the surface gone the gates admit
    # again and the delivered iron finally reaches the pack.
    for _ in range(60):
        life.tick()
    pack = sum(i.amount for i in body.items.values()
               if i.container == 0xB1 and i.graphic in
               __import__("anima2.skills.smelt", fromlist=["x"]).INGOT_GRAPHICS)
    assert pack > 0, "unwedged, but the Life never picked the iron up"


def test_a_stale_vendor_window_is_cancelled_with_an_empty_buy_list():
    # forge16 live: 200+ disagreement ticks with `ui=shopbuy` after a finished
    # buy_iron trip. ServUO answers anything that is not flag 0x02 with
    # EndVendorBuy, and the bridge encodes an empty list as 0x00 — so "buy
    # nothing" IS the close.
    from anima2.contract import BuyItems, ShopBuy
    from anima2.warrior_life import DISAGREEMENT_TICKS

    life, body, stale = _stale_ui_life()
    body.gumps.clear()
    body.shop_buy = ShopBuy(vendor=0xABCD, container=0x999, entries=[])
    for _ in range(DISAGREEMENT_TICKS * 4):
        life.tick()
        if any(isinstance(a, BuyItems) for a in body.actions):
            break
    cancels = [a for a in body.actions if isinstance(a, BuyItems) and not a.items]
    assert cancels and cancels[0].vendor == 0xABCD


def test_a_healthy_life_never_closes_a_gump_out_from_under_a_goal():
    # The no-goal guard is what makes the repair safe: a gump mid-transaction
    # belongs to a live capability goal and must never be closed by the Life.
    from anima2.contract import GumpResponse

    life, body, _ = _stale_ui_life()
    for _ in range(4):  # short of DISAGREEMENT_TICKS
        life.tick()
    assert life.rule_gate_disagreement is None
    assert not [a for a in body.actions if isinstance(a, GumpResponse)]
