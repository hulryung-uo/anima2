"""Rule<->gate concordance: every Life's decide_mode must never want what admission refuses.

Six live failures in one week shared a single shape — two components each individually
correct, disagreeing about a definition (worn vs packed tools, a fetch rule ignoring the
gate's PICKUP_RADIUS, a threshold that was not a reserve). Each was caught ONE AT A TIME
with a 10-20 minute live run and bespoke telemetry. This suite converts the whole class
into an offline assertion: for every profession that has a Life, walk a lattice of
observations across the axes those failures actually varied on (tool placement, stack
sizes at threshold edges, gold at cost/reserve edges, ground items at PICKUP_RADIUS and
one tile beyond) and assert that whenever the Life's rule wants an economy capability,
the capability layer's own readiness gate agrees.

Scope, stated honestly: this is the STEADY-STATE lattice — idle UI (no cursor, no gumps,
no shop windows), `mkt_phase == "craft"`, `bs_state == "open"`. Transient mid-transaction
disagreement (gates deliberately de-assert while a buy/sell is in flight) is the runtime
disagreement detector's domain, not this suite's. And no offline test can prove a TILE is
walkable — terrain stays live-probe territory (see docs/WOODSMAN.md).

The memory handed to both sides is the REAL memory the orchestrators wire (routes plus
`bank_reserve` where the Life sets it) — the gates read `ctx.memory` with defaults, so a
fixture that omits what the orchestrator writes would test a configuration that never
runs.

**Every lattice point is also walked at every KNOB setting**, which is the half this
suite was missing. It used to pin each knob to its module default, and at the default
both sides agree BY CONSTRUCTION — so a knob converted on the rule side only would have
sailed straight through (proved with a probe, not assumed: a one-line rule-side-only
`sell_boards_at` knob passed the whole suite untouched, and failed it the moment the
fixture carried a tuned value). The assertion was always the right one; it simply was
not pointed at the knobs. `_knob_sweep` points it: tuned off the constant in both
directions, never written at all, and malformed four ways — because the drift class this
suite exists for is now reachable THROUGH a knob (see `anima2/knobs.py`), and a knob
whose two readers disagree about a malformed value is the same six live failures wearing
a tuning axis.

**And WHERE the agent is standing is an axis too** (`_craft_spot_axis`), which is the
other half this suite was missing. Both craft lattices used to PIN `craft_spot` to the
player's own tile, so neither craft Life was ever walked off its stand — and a
differential probe over 150,000 randomized states found exactly one forward-concordance
violation in the whole codebase hiding in that blind spot: the carpenter's terminal craft
branch, unguarded against the two clauses `capabilities._make_craft_ready` applies
(`_craft_at_spot` and `0 <= made < batch`). 308 disagreeing states, and the shape is a
PERMANENT want-but-never-ready — nothing else in a craft chain fires with material
already in the pack, so `_clear_stale_ui` then closes a UI surface every tick on a
healthy agent. A pinned fixture is a fixture that tests one point of an axis.

A coverage assertion rides along: every capability in a Life's economy set must be WANTED
somewhere in its lattice. A capability no lattice point can want is a dead branch — the
shape of the woodsman's original unreachable BANK_ABOVE=300.
"""

from itertools import product

from anima2.capabilities import ready_capability_ids
from anima2.carpenter_life import decide_mode as carpenter_decide
from anima2.contract import ItemView, MobileView, Observation, PlayerView, Position
from anima2.mage_life import decide_mode as mage_decide
from anima2.persona import Persona
from anima2.skills.base import SkillContext
from anima2.skills.craft import PICKUP_RADIUS
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.hunt import GOLD_GRAPHIC
from anima2.warrior_life import decide_mode as warrior_decide
from anima2.woodsman_life import decide_mode as woodsman_decide

PLAYER = 1
BP = 0x50
OTHER = 0x99        # a vendor standing beside the agent
OTHER_PACK = 0x9A   # ...and its backpack
#: Where `_obs` stands the player. Named because the craft-spot axis below is defined
#: RELATIVE to it — a lattice whose spot and player tile drift apart tests nothing.
PLAYER_TILE = (5, 5)

_serial = [0x1000]


def _item(graphic, amount=1, *, container=BP, layer=0, distance=0):
    _serial[0] += 1
    return ItemView(serial=_serial[0], graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=distance)


def _backpack():
    return ItemView(serial=BP, graphic=0x0E75, amount=1, pos=Position(),
                    container=PLAYER, layer=BACKPACK_LAYER, distance=0)


def _other_mobile():
    return MobileView(serial=OTHER, name="Vendor", pos=Position(6, 5, 0), body=400,
                      notoriety=1, hits=100, hits_max=100, distance=1)


def _other_pack():
    return ItemView(serial=OTHER_PACK, graphic=0x0E75, amount=1, pos=Position(),
                    container=OTHER, layer=BACKPACK_LAYER, distance=1)


def _obs(items, mobiles=()):
    return Observation(player=PlayerView(serial=PLAYER, pos=Position(*PLAYER_TILE, 0)),
                       items=[_backpack(), *items], mobiles=list(mobiles))


def _craft_spot_axis(radius: int) -> dict:
    """`(label, memory patch)` for every `craft_spot` a live run actually produces.

    The axis this suite was missing, and it is the reason it could not catch the one
    forward-concordance violation a differential probe later found: both craft lattices
    pinned `craft_spot` to the player's own tile, so NEITHER craft Life was ever walked
    at an out-of-radius or missing spot. Every craft gate applies `_craft_at_spot`
    (`capabilities._make_craft_ready`), so a rule that does not ask the same question
    wants a craft admission refuses — forever, since nothing else in a craft chain fires
    with material already in the pack.

    `WANDERED` is the live-shaped one, not the exotic one: `FetchBoards` walks up to
    `PICKUP_RADIUS` (6) to a delivered pile, twice the carpenter's craft radius, and has
    no walk-home leg. `ABSENT` and `MALFORMED` are the tuning/staging shapes — a spot
    key is a memory write like any other.
    """
    px, py = PLAYER_TILE
    return {
        "at": {"craft_spot": (px, py)},
        "edge": {"craft_spot": (px + radius, py + radius)},
        "wandered": {"craft_spot": (px + radius + 1, py)},
        "absent": {},
        "malformed": {"craft_spot": (px, "5")},
    }


def _knob_sweep(memory, key):
    """`(label, memory)` at every setting a tuning write can actually produce for `key`.

    SET (both directions off the module constant — a genome axis explores, it does not
    stop at the shipped value), ABSENT (the bare-dict shape: the two sides deliberately
    take DIFFERENT defaults, the Life's own constant on the rule side and 0 on the gate
    side, and forward safety must survive that asymmetry), and MALFORMED four ways.

    The malformed row is the one with a scar: negative, float, bool and string all land
    on the clamp floor in `anima2/knobs.py`, and they must land there on EVERY reader.
    A rule reading raw while a gate read the clamp made the rule want `bank_gold` at any
    gold while the gate refused at 0g — the rule-vs-gate drift class, recreated through
    the very knob that was supposed to be safe (review-caught on the tinker's urgent
    band). `True` is in the list on purpose: `bool` is an `int` subclass, so an
    `isinstance` clamp would honour it as `1` instead of rejecting it.
    """
    wired = memory[key]
    return [
        ("wired", dict(memory)),
        ("tuned_down", {**memory, key: 1}),
        ("tuned_up", {**memory, key: wired * 3 + 7}),
        ("absent", {k: v for k, v in memory.items() if k != key}),
        ("malformed_negative", {**memory, key: -100}),
        ("malformed_float", {**memory, key: 12.5}),
        ("malformed_bool", {**memory, key: True}),
        ("malformed_string", {**memory, key: "80"}),
    ]


#: Registry capabilities a Life's own rule DELIBERATELY never proposes, per profession.
#: Checked for equality, not membership, by `_assert_concordance` — see it for why.
#:
#: The tinker's five are the whole reason this allow-list exists (review-caught): its
#: registry has 11 entries and `TinkerLife.decide_mode` can return 6, so `craft_saw`,
#: `craft_hatchet`, `deliver_saw`, `deliver_hatchet` and `deliver_gold` go READY in
#: randomized states and are then unreachable — `WarriorLife.tick` sets `self.candidates`
#: from `decide`/`decide_all`, and `_LifeClient._pick` may only return a member of that
#: list, so the CapabilityCognition attached to a TinkerLife cannot propose anything else.
#: That makes the Brick-10 tinker→carpenter/lumberjack tool-supply link and the
#: tinker→mage gold hand-off unreachable THROUGH A LIFE. Inert rather than harmful today
#: only because no shipped runner wires a TinkerLife's drop keys: `run_forge_pair` (the
#: only TinkerLife runner) stages `vendor_spot` and `banker_spot` alone, and the one
#: production `mage_drop` write belongs to `run_artisan_mage_village`'s artisan, which is
#: a plain Agent under `CapabilityCognition(None)`. Deliberately NOT closed by adding
#: branches here: that is a live-behaviour change to the flagship positive-margin Life,
#: and nothing offline can tell us what it does to a real forge day. Recorded, enforced,
#: and left — if a runner ever wires those drops, this list is where the bill arrives.
_NOT_DRIVEN_BY_THE_LIFE = {
    "tinker": frozenset({"craft_saw", "craft_hatchet", "deliver_saw", "deliver_hatchet",
                         "deliver_gold"}),
}


def _registry_ids(profession: str) -> frozenset[str]:
    """Every capability id the immutable registry binds for `profession`."""
    from anima2.capabilities import CAPABILITIES

    return frozenset(b.capability_id for (prof, _c), b in CAPABILITIES.items()
                     if prof == profession)


#: Which professions' rules actually READ a vendor-dry marker. An EQUALITY, not a
#: floor, for the same reason `_NOT_DRIVEN_BY_THE_LIFE` is one: a Life that quietly
#: stops reading its marker turns this axis back into the no-op it used to be, and
#: an axis nobody can see fire is an axis that is not testing anything. The
#: lumberjack is absent because `woodsman_life` has no `vendor_dry` read at all.
_DRY_CHANGES_A_DECISION = frozenset({"swordsman", "mage", "carpenter", "tinker"})


def _assert_dry_axis(profession, decide, cases):
    """Walk the lattice again with a vendor-dry marker planted.

    This suite made **3316** `vendor_dry` calls and planted a marker on exactly
    ZERO of them, so `vendor_dry` returned `False` every single time and the whole
    axis was untested — the suite was comparing the rule with itself. The vacuity
    is not cosmetic: a dry read added to the tool-buy GATE (a textbook rule-vs-gate
    standoff, and precisely what this file exists to catch) passes all 1578 tests
    as they stand, and is caught by four of the five the moment a marker is planted.

    BOTH keys production writes are planted. `buy_dry_until` is read by four Life
    rules; `toolbuy_dry_until` (`market.py`) is written and read NOWHERE — planting
    it is what makes a future reader's disagreement fail here, on the day it is
    written, instead of on a shard.
    """
    changed = False
    for obs, memory in cases:
        base = decide(obs, dict(memory))
        for marker in ("buy_dry_until", "toolbuy_dry_until"):
            # 1 beats `market_clock`'s 0 default, so the stand-down reads as live.
            tuned = {**memory, marker: 1}
            mode, cap = decide(obs, dict(tuned))
            if (mode, cap) != base:
                changed = True
            if mode != "economy" or cap is None:
                continue
            ctx = SkillContext(obs=obs, persona=Persona(name="T"),
                               memory=dict(tuned))
            ready = ready_capability_ids(profession, ctx)
            assert cap in ready, (
                f"{profession}: with {marker} planted the rule wants {cap!r} but "
                f"the gate refuses (ready={sorted(ready)}). A gate that reads a "
                f"vendor-dry marker its rule does not read is the rule-vs-gate "
                f"standoff this axis exists to catch."
            )
    assert changed == (profession in _DRY_CHANGES_A_DECISION), (
        f"{profession}: planting a vendor-dry marker "
        f"{'changed nothing' if not changed else 'changed a decision'}, which "
        f"_DRY_CHANGES_A_DECISION contradicts. Either a Life gained or lost a "
        f"`vendor_dry` read, or the lattice stopped reaching the branch that has "
        f"one — and either way this axis has gone back to testing nothing."
    )


def _assert_concordance(profession, decide, cases, expected_caps,
                        knobs=("bank_reserve",)):
    """Forward: rule wants cap => gate ready. Coverage: every cap wanted somewhere.

    Each lattice point is walked once per knob per `_knob_sweep` setting (one knob moved
    at a time, the rest as wired — a cross product would buy combinations no tuner
    produces at the price of a suite nobody runs). Coverage is over the UNION, so the
    dead-branch check still only needs one reachable point.

    Coverage is checked TWICE, against two different sources, because the hand-written
    one cannot see the defect the registry one exists for. `expected_caps` is a list
    written here, so it can only catch a branch that USED to be reachable and stopped;
    a capability the Life's rule never proposed in the first place is simply absent from
    both sides and passes. The tinker shipped exactly that way — 5 of its 11 registry
    capabilities go ready and are never wanted — and the 6-id `expected_caps` beside it
    was structurally incapable of noticing. So the second check is REGISTRY-DERIVED and
    is an equality: whatever the lattice never wants must be spelled out in
    `_NOT_DRIVEN_BY_THE_LIFE` with a reason, which turns "dead capacity" from something
    you have to go looking for into something you have to write down.
    """
    wanted: set[str] = set()
    for obs, memory in cases:
        for knob in knobs:
            for label, tuned in _knob_sweep(memory, knob):
                mode, cap = decide(obs, dict(tuned))
                if mode != "economy" or cap is None:
                    continue
                wanted.add(cap)
                ctx = SkillContext(obs=obs, persona=Persona(name="T"),
                                   memory=dict(tuned))
                ready = ready_capability_ids(profession, ctx)
                assert cap in ready, (
                    f"{profession}: the rule wants {cap!r} but the gate refuses "
                    f"(ready={sorted(ready)}) with {knob} {label} "
                    f"({tuned.get(knob, '<unset>')!r}).\nThis is the exact stall shape "
                    f"that cost six live runs — here reached through a tuning knob, "
                    f"which is why every reader must clamp through anima2/knobs.py. "
                    f"Observation items: "
                    + ", ".join(f"g=0x{i.graphic:X} c={i.container} l={i.layer} "
                                f"d={i.distance} n={i.amount}" for i in obs.items)
                )
    missing = set(expected_caps) - wanted
    assert not missing, (
        f"{profession}: no lattice point ever WANTS {sorted(missing)} — either the "
        f"lattice lost an axis or the rule has a dead branch (the unreachable "
        f"BANK_ABOVE=300 shape)."
    )
    _assert_dry_axis(profession, decide, cases)
    undriven = _registry_ids(profession) - wanted
    assert undriven == _NOT_DRIVEN_BY_THE_LIFE.get(profession, frozenset()), (
        f"{profession}: the registry binds {sorted(undriven)} which no lattice point "
        f"ever wants, and that set is not the one _NOT_DRIVEN_BY_THE_LIFE declares "
        f"({sorted(_NOT_DRIVEN_BY_THE_LIFE.get(profession, ()))}). Either the Life's "
        f"rule gained/lost a branch, the lattice lost an axis, or a capability is DEAD "
        f"CAPACITY — ready in the gate, unreachable through the rule, so the "
        f"CapabilityCognition on this Life can never propose it. Whichever it is, it "
        f"gets written down there with a reason rather than discovered by a sweep."
    )


# --- warrior ------------------------------------------------------------------------

def test_warrior_rule_never_wants_what_its_gates_refuse():
    from anima2.skills.warrior import (
        BANDAGE_GRAPHIC,
        PLATE_ARMOR_LAYERS,
        PLATE_CHEST_GRAPHIC,
        SWORD_RANK,
        WEAPON_LAYER,
        UpgradeWeapon,
    )
    from anima2.warrior_life import (
        ARMOR_PRICE,
        BANDAGE_BATCH_COST,
        LOW_BANDAGES,
        UPGRADE_RESERVE,
        WEAPON_PRICE,
    )

    top = UpgradeWeapon.offer_graphic
    low = min(SWORD_RANK, key=SWORD_RANK.get)
    from anima2.warrior_life import BANK_RESERVE

    # The REAL econ memory: routes plus the bank_reserve the constructor writes
    # (audit #5) - the gate reads this key with default 0, so omitting what the
    # orchestrator wires would test a configuration that never runs.
    routes = {"weapon_vendor_spot": ((10, 10),), "healer_spot": ((10, 10),),
              "armorer_spot": ((10, 10),), "banker_spot": ((10, 10),),
              "bank_reserve": BANK_RESERVE}

    weapon_axis = {
        "none": [],
        "worn_low": [_item(low, container=PLAYER, layer=WEAPON_LAYER)],
        "worn_top": [_item(top, container=PLAYER, layer=WEAPON_LAYER)],
        "packed": [_item(top)],
    }
    chest_axis = {
        "none": [],
        "worn": [_item(PLATE_CHEST_GRAPHIC, container=PLAYER,
                       layer=PLATE_ARMOR_LAYERS[PLATE_CHEST_GRAPHIC])],
        "packed": [_item(PLATE_CHEST_GRAPHIC)],
    }
    bandage_axis = [0, LOW_BANDAGES - 1, LOW_BANDAGES]
    gold_axis = sorted({0, WEAPON_PRICE - 1, WEAPON_PRICE, BANDAGE_BATCH_COST,
                        ARMOR_PRICE, WEAPON_PRICE + UPGRADE_RESERVE - 1,
                        WEAPON_PRICE + UPGRADE_RESERVE, BANK_RESERVE, BANK_RESERVE + 1})

    cases = []
    for (_, weapon), (_, chest), bandages, gold in product(
            weapon_axis.items(), chest_axis.items(), bandage_axis, gold_axis):
        items = [*weapon, *chest]
        if bandages:
            items.append(_item(BANDAGE_GRAPHIC, bandages))
        if gold:
            items.append(_item(GOLD_GRAPHIC, gold))
        cases.append((_obs(items), routes))

    _assert_concordance("swordsman", warrior_decide, cases,
                        {"buy_weapon", "buy_bandage", "buy_armor", "upgrade_weapon",
                         "bank_gold"})


# --- mage ---------------------------------------------------------------------------

def test_mage_rule_never_wants_what_its_gates_refuse():
    from anima2.mage_life import LOW_REAGENTS, REAGENT_BATCH_COST
    from anima2.skills.mage import FETCH_GOLD_PACK_CAP, SULFUROUS_ASH_GRAPHIC

    from anima2.mage_life import BANK_RESERVE

    routes = {"mage_vendor_spot": ((10, 10),), "banker_spot": ((10, 10),),
              "bank_reserve": BANK_RESERVE}

    ash_axis = [0, LOW_REAGENTS - 1, LOW_REAGENTS]
    gold_axis = sorted({0, REAGENT_BATCH_COST - 1, REAGENT_BATCH_COST,
                        FETCH_GOLD_PACK_CAP - 1, FETCH_GOLD_PACK_CAP,
                        BANK_RESERVE, BANK_RESERVE + 1})
    purse_axis = {
        "none": [],
        "in_reach": [_item(GOLD_GRAPHIC, 140, container=None, distance=PICKUP_RADIUS)],
        "out_of_reach": [_item(GOLD_GRAPHIC, 140, container=None,
                               distance=PICKUP_RADIUS + 1)],
    }

    cases = []
    for ash, gold, (_, purse) in product(ash_axis, gold_axis, purse_axis.items()):
        items = [*purse]
        if ash:
            items.append(_item(SULFUROUS_ASH_GRAPHIC, ash))
        if gold:
            items.append(_item(GOLD_GRAPHIC, gold))
        cases.append((_obs(items), routes))

    _assert_concordance("mage", mage_decide, cases,
                        {"fetch_gold", "buy_reagent", "bank_gold"})


# --- woodsman -----------------------------------------------------------------------

def test_woodsman_rule_never_wants_what_its_gates_refuse():
    from anima2.skills.woodwork import BOARD_GRAPHIC, LOG_GRAPHIC
    from anima2.woodsman_life import (
        BANK_RESERVE,
        DELIVER_BOARDS_AT,
        HATCHET_COST,
        SELL_BOARDS_AT,
    )

    HATCHET = 0x0F43
    base_memory = {"vendor_spot": ((10, 10),), "tool_vendor_spot": ((10, 10),),
                   "banker_spot": ((10, 10),), "bank_reserve": BANK_RESERVE}

    axe_axis = {
        "none": ([], []),
        "packed": ([_item(HATCHET)], []),
        "worn": ([_item(HATCHET, container=PLAYER, layer=1)], []),
        "ground_in_reach": ([_item(HATCHET, container=None, distance=PICKUP_RADIUS)], []),
        "ground_out_of_reach": ([_item(HATCHET, container=None,
                                       distance=PICKUP_RADIUS + 1)], []),
        "foreign": ([_other_pack(), _item(HATCHET, container=OTHER_PACK, distance=1)],
                    [_other_mobile()]),
    }
    log_axis = [0, 5]
    board_axis = sorted({0, SELL_BOARDS_AT - 1, SELL_BOARDS_AT, DELIVER_BOARDS_AT})
    gold_axis = sorted({0, HATCHET_COST - 1, HATCHET_COST, BANK_RESERVE,
                        BANK_RESERVE + 1})
    drop_axis = [None, ((12, 12),)]

    cases = []
    for (_, (axe, mobs)), logs, boards, gold, drop in product(
            axe_axis.items(), log_axis, board_axis, gold_axis, drop_axis):
        items = [*axe]
        if logs:
            items.append(_item(LOG_GRAPHIC, logs))
        if boards:
            items.append(_item(BOARD_GRAPHIC, boards))
        if gold:
            items.append(_item(GOLD_GRAPHIC, gold))
        memory = dict(base_memory)
        if drop is not None:
            memory["carpenter_drop"] = drop
        cases.append((_obs(items, mobs), memory))

    _assert_concordance("lumberjack", woodsman_decide, cases,
                        {"process_logs", "sell_boards", "deliver_boards", "bank_gold",
                         "fetch_hatchet", "buy_hatchet"})


# --- carpenter ----------------------------------------------------------------------

def test_carpenter_rule_never_wants_what_its_gates_refuse():
    from anima2.carpenter_life import (
        BANK_RESERVE,
        BOARD_BATCH_COST,
        BOARDS_PER_ITEM,
        SAW_COST,
        _CRAFT_RADIUS,
    )
    from anima2.skills.carpentry import BuySaw, FetchBoards, SellFurniture

    SAW = sorted(BuySaw.owned_tool_graphics)[0]
    BOARD = sorted(FetchBoards.fetched_graphics)[0]
    FURNITURE = SellFurniture.sold_graphic
    # No `craft_spot` here: it is an AXIS now (see `_craft_spot_axis`), because pinning
    # it to the player's own tile is what let the carpenter's craft branch ship for a
    # release wanting a craft the gate refuses whenever the agent had walked off its
    # stand — 308 disagreeing states in a 30,000-state differential probe.
    # `vendor_spot` is likewise an axis: `run_supply_pair` stages with `strict=False`,
    # so an UNSET vendor route is a tolerated, documented live outcome, and it is the
    # state in which a finished throne can never be cleared.
    base_memory = {"banker_spot": ((10, 10),), "bank_reserve": BANK_RESERVE}

    saw_axis = {
        "none": [],
        "packed": [_item(SAW)],
        "worn": [_item(SAW, container=PLAYER, layer=1)],
        "ground_in_reach": [_item(SAW, container=None, distance=PICKUP_RADIUS)],
        "ground_out_of_reach": [_item(SAW, container=None, distance=PICKUP_RADIUS + 1)],
    }
    pack_board_axis = [0, BOARDS_PER_ITEM - 1, BOARDS_PER_ITEM]
    ground_board_axis = {
        "none": [],
        "in_reach": [_item(BOARD, 40, container=None, distance=PICKUP_RADIUS)],
        "out_of_reach": [_item(BOARD, 40, container=None, distance=PICKUP_RADIUS + 1)],
    }
    furniture_axis = [0, 1]
    gold_axis = sorted({0, SAW_COST - 1, SAW_COST, BOARD_BATCH_COST - 1,
                        BOARD_BATCH_COST, BANK_RESERVE, BANK_RESERVE + 1})

    vendor_axis = {"routed": {"vendor_spot": ((10, 10),)}, "unrouted": {}}

    cases = []
    for (_, saw), packb, (_, groundb), furn, gold, (_, spot), (_, vendor) in product(
            saw_axis.items(), pack_board_axis, ground_board_axis.items(),
            furniture_axis, gold_axis, _craft_spot_axis(_CRAFT_RADIUS).items(),
            vendor_axis.items()):
        items = [*saw, *groundb]
        if packb:
            items.append(_item(BOARD, packb))
        if furn:
            items.append(_item(FURNITURE, furn))
        if gold:
            items.append(_item(GOLD_GRAPHIC, gold))
        cases.append((_obs(items), {**base_memory, **spot, **vendor}))

    _assert_concordance("carpenter", carpenter_decide, cases,
                        {"craft_carpentry", "sell_furniture", "fetch_boards",
                         "buy_boards", "fetch_saw", "buy_saw", "bank_gold"})


# --- tinker (the flagship positive chain) -------------------------------------------

def test_tinker_rule_never_wants_what_its_gates_refuse():
    from anima2.skills.smelt import INGOT_GRAPHICS
    from anima2.skills.tinkering import (
        FETCH_IRON_PACK_CAP,
        TONGS_GRAPHIC,
        BuyIron,
        TinkerTongs,
    )
    from anima2.tinker_life import (
        BANK_RESERVE,
        BANK_TRIP_SURPLUS,
        IRON_BATCH_COST,
        SELL_TONGS_AT,
        TOOL_COST,
        _CRAFT_RADIUS,
        decide_mode as tinker_decide,
    )

    IRON = sorted(INGOT_GRAPHICS)[0]
    TOOL = sorted(TinkerTongs.craft_tool_graphics)[0]
    base_memory = {"vendor_spot": ((10, 10),), "banker_spot": ((10, 10),),
                   "bank_reserve": BANK_RESERVE,
                   # The second knob `TinkerLife.__init__` writes. Both are swept below:
                   # this Life is the only one with two, so it is the only place a knob
                   # can be tuned while ANOTHER knob is what the gate compares against.
                   "bank_trip_surplus": BANK_TRIP_SURPLUS}
    # `craft_spot` is an AXIS here too (`_craft_spot_axis`), not a pin. The tinker's rule
    # has mirrored the gate's radius since birth, so this walks a guard that already
    # holds — which is the point: it is the assertion that keeps holding it.

    tool_axis = {
        "none": ([], []),
        "packed": ([_item(TOOL)], []),
        "foreign": ([_other_pack(), _item(TOOL, container=OTHER_PACK, distance=1)],
                    [_other_mobile()]),
    }
    tongs_axis = [0, SELL_TONGS_AT - 1, SELL_TONGS_AT]
    iron_axis = sorted({0, TinkerTongs.craft_material_per_item,
                        BuyIron.buy_reorder - 1, BuyIron.buy_reorder,
                        TinkerTongs.craft_material_per_item * TinkerTongs.craft_batch,
                        FETCH_IRON_PACK_CAP - 1, FETCH_IRON_PACK_CAP})
    ground_axis = {
        "none": [],
        "in_reach": [_item(IRON, 20, container=None, distance=PICKUP_RADIUS)],
        "out_of_reach": [_item(IRON, 20, container=None,
                               distance=PICKUP_RADIUS + 1)],
    }
    gold_axis = sorted({0, TOOL_COST - 1, TOOL_COST, IRON_BATCH_COST - 1,
                        IRON_BATCH_COST, BANK_RESERVE, BANK_RESERVE + 1,
                        # The pockets-full band: banking preempts craft above it
                        # (forge4's starved patient branch) — both edges walked.
                        BANK_RESERVE + BANK_TRIP_SURPLUS,
                        BANK_RESERVE + BANK_TRIP_SURPLUS + 1})

    cases = []
    for (_, (tool, mobs)), tongs, iron, (_, ground), gold, (_, spot) in product(
            tool_axis.items(), tongs_axis, iron_axis, ground_axis.items(), gold_axis,
            _craft_spot_axis(_CRAFT_RADIUS).items()):
        items = [*tool, *ground]
        if tongs:
            items.append(_item(TONGS_GRAPHIC, tongs))
        if iron:
            items.append(_item(IRON, iron))
        if gold:
            items.append(_item(GOLD_GRAPHIC, gold))
        cases.append((_obs(items, mobs), {**base_memory, **spot}))

    _assert_concordance("tinker", tinker_decide, cases,
                        {"buy_tinker_tool", "sell_tongs", "fetch_iron", "craft_tongs",
                         "buy_iron", "bank_gold"},
                        knobs=("bank_reserve", "bank_trip_surplus"))


# --- the state every lattice above hides: OUR OWN PACK out of the observation --------
#
# `_obs` injects a backpack into every fixture in this file, and that is exactly why the
# suite could not see the missing-backpack drift (docs/AUDIT-2026-07-29.md, follow-up 1).
# The state is real, not hypothetical: docs/WOODSMAN.md records a run "with our own
# backpack out of view". It has TWO halves and both are asserted below, because fixing
# one alone only renames the disagreement:
#
#   - the OWNED half: a ground item's container is `None`, so an unguarded
#     `i.container in (bp, player)` matched it whenever `bp` was `None` too — the rule
#     claimed to own a saw the gate said we did not have;
#   - the GROUND half: every fetch gate opens with `_backpack_serial(ctx) is not None`,
#     so a rule that fetched without asking the same question wanted a pickup admission
#     always refuses. Guarding only `owns` moved the woodsman from wanting nothing to
#     wanting `fetch_hatchet` against `ready=[]` — a NEW disagreement, review-caught.


def _obs_no_pack(items, mobiles=()):
    """The same observation shape as `_obs`, minus our own backpack."""
    return Observation(player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0)),
                       items=list(items), mobiles=list(mobiles))


def test_a_ground_tool_is_not_owned_and_not_fetchable_without_our_pack():
    from anima2.obsview import ground_amount, on_ground, owns, pack_amount
    from anima2.skills.woodwork import AXE_GRAPHICS

    axe = sorted(AXE_GRAPHICS)[0]
    obs = _obs_no_pack([_item(axe, container=None, distance=0),
                        _item(GOLD_GRAPHIC, 500, container=None, distance=0)])
    assert owns(obs, AXE_GRAPHICS) is False, (
        "a hatchet lying on the ground is not a hatchet we own — the guard "
        "`warrior._has_weapon` had from the start and three later Lives did not"
    )
    assert on_ground(obs, AXE_GRAPHICS) is False, (
        "with no pack in the observation there is nowhere to put it, which is the "
        "fetch gate's own first clause"
    )
    assert ground_amount(obs, GOLD_GRAPHIC) == 0
    assert pack_amount(obs, GOLD_GRAPHIC) == 0
    # ...and with our pack back in view both answers flip, so the clause is a guard and
    # not a blanket refusal.
    with_pack = _obs([_item(axe, container=None, distance=0),
                      _item(GOLD_GRAPHIC, 500, container=None, distance=0)])
    assert owns(with_pack, AXE_GRAPHICS) is False   # still ground, still not ours
    assert on_ground(with_pack, AXE_GRAPHICS) is True
    assert ground_amount(with_pack, GOLD_GRAPHIC) == 500


def test_no_life_wants_anything_the_gate_refuses_with_our_pack_out_of_view():
    from anima2.skills.carpentry import BuySaw, FetchBoards
    from anima2.skills.smelt import INGOT_GRAPHICS
    from anima2.skills.tinkering import TONGS_GRAPHIC
    from anima2.skills.woodwork import AXE_GRAPHICS, BOARD_GRAPHIC
    from anima2.tinker_life import decide_mode as tinker_decide

    # Every art a Life can want off the ground, all at our feet at once — the state that
    # made four of the five rules ask for a fetch admission can never grant.
    ground = [_item(art, 20, container=None, distance=0) for art in sorted({
        GOLD_GRAPHIC, BOARD_GRAPHIC, TONGS_GRAPHIC,
        *AXE_GRAPHICS, *INGOT_GRAPHICS,
        *BuySaw.owned_tool_graphics, *FetchBoards.fetched_graphics,
    })]
    obs = _obs_no_pack(ground, [_other_mobile(), _other_pack()])
    # Every route wired, so nothing is declined for the WRONG reason (an unset spot).
    #
    # The keys are READ OFF THE SKILL CLASSES, never spelled out. Spelled out is how this
    # fixture shipped, and eight of its fourteen names did not exist: `reagent_vendor_spot`
    # / `saw_vendor_spot` / `carpentry_vendor_spot` / `board_vendor_spot` /
    # `lumber_vendor_spot` / `hatchet_vendor_spot` / `tinker_vendor_spot` /
    # `iron_vendor_spot` against the real `mage_vendor_spot` (BuyReagent),
    # `tool_vendor_spot` (BuyHatchet) and a plain shared `vendor_spot` (everything else).
    # So every buy/sell branch except the warrior's was declined for exactly the unset-spot
    # reason this comment claimed it was not, and the fetch concordance the test is named
    # for was only ever reached because the fetch branches sit ABOVE those. Review-caught.
    from anima2.skills.mage import BuyReagent
    from anima2.skills.tinkering import BuyIron, BuyTinkerTool, SellTongs
    from anima2.skills.woodwork import BuyHatchet, DeliverBoards, SellBoards

    route_keys = {c.vendor_spot_key for c in (
        BuyReagent, BuyHatchet, SellBoards, BuySaw, BuyTinkerTool, SellTongs, BuyIron)}
    route_keys |= {DeliverBoards.drop_key,
                   "weapon_vendor_spot", "healer_spot", "armorer_spot", "banker_spot"}
    memory = {k: ((10, 10),) for k in route_keys}
    for profession, decide in (("swordsman", warrior_decide), ("mage", mage_decide),
                               ("lumberjack", woodsman_decide),
                               ("carpenter", carpenter_decide),
                               ("tinker", tinker_decide)):
        mode, cap = decide(obs, dict(memory))
        if mode != "economy" or cap is None:
            continue
        ctx = SkillContext(obs=obs, persona=Persona(name="T"), memory=dict(memory))
        ready = ready_capability_ids(profession, ctx)
        assert cap in ready, (
            f"{profession}: with our own pack out of the observation the rule wants "
            f"{cap!r} but the gate refuses (ready={sorted(ready)}). Every fetch gate "
            f"requires a backpack; the rule side must ask the same question."
        )


# --- the same class in CONSTANT form (audit follow-up 6) -----------------------------
#
# The lattice above catches a rule and a gate disagreeing about a VALUE. It cannot catch
# them disagreeing about a DEFINITION, because a constant written twice is numerically
# locked until somebody edits one copy — and the edit is the failure. Follow-up 6 named
# three such pairs and called them "the audit's own headline defect class, in constant
# form". These pin the merge, and they are identity assertions on purpose: equality would
# pass again the moment a second definition happened to compute the same number.


def test_the_upgrade_reserve_is_ONE_decision_not_two_that_agree():
    """`warrior_life.UPGRADE_RESERVE` (the decide rule) and `capabilities._UPGRADE_RESERVE`
    (the `upgrade_weapon` gate) were separate module constants with near-identical
    comments and the same right-hand side. They could not diverge numerically — both read
    `BuyArmor.tool_price_estimate` — but "the reserve is one chest plate's worth" is a
    DECISION, and it was recorded in two places. A rule and a gate disagreeing about a
    threshold does not care whether the disagreement arrived through a bad value or an
    edit."""
    import anima2.capabilities as caps
    import anima2.warrior_life as wl
    from anima2.skills.warrior import UPGRADE_RESERVE

    assert wl.UPGRADE_RESERVE is UPGRADE_RESERVE
    assert caps.UPGRADE_RESERVE is UPGRADE_RESERVE
    # The gate-private second name is gone, not merely equal to the first.
    assert not hasattr(caps, "_UPGRADE_RESERVE")


def test_boards_per_item_is_ONE_name_not_two():
    """`carpenter_life.BOARDS_PER_ITEM` and `capabilities._FETCH_BOARDS_THRESHOLD`: two
    names for one fact. The gate's own comment claimed it read the class attribute "so it
    stays in lockstep with the craft gate that consumes it" — which is a property no
    comment can hold."""
    import anima2.capabilities as caps
    import anima2.carpenter_life as cl
    from anima2.skills.carpentry import BOARDS_PER_ITEM

    assert cl.BOARDS_PER_ITEM is BOARDS_PER_ITEM
    assert caps.BOARDS_PER_ITEM is BOARDS_PER_ITEM
    assert not hasattr(caps, "_FETCH_BOARDS_THRESHOLD")


def test_the_bandage_family_is_never_restated_as_a_literal():
    """The third pair, and the ONLY one of the three that could actually drift: the other
    two derive from a shared class attribute, while `skills/warrior.BANDAGE_GRAPHIC` was
    the literal `0x0E21` typed out again three lines below an import of the frozenset that
    already held it.

    It had already produced a live-shaped hazard. `WarriorLife.decide` counted the SINGLE
    graphic while `buy_bandage`'s gate counts `BuyBandage.buy_material_graphics` — the
    whole FAMILY. Identical today because the family is a singleton; the moment it grew,
    the rule would undercount, want `buy_bandage`, and the gate would refuse: the exact
    want-vs-refuse standoff this suite exists for, reachable by adding one graphic."""
    import ast
    from pathlib import Path

    import anima2.warrior_life as wl
    from anima2.skills.survival import BANDAGE_GRAPHICS
    from anima2.skills.warrior import BANDAGE_GRAPHIC, BuyBandage

    # The rule and the gate now read the SAME OBJECT, so they cannot disagree at all.
    assert BuyBandage.buy_material_graphics is BANDAGE_GRAPHICS
    assert wl.BANDAGE_GRAPHICS is BANDAGE_GRAPHICS
    # ...and the rule can no longer reach the single-graphic name even by accident.
    assert not hasattr(wl, "BANDAGE_GRAPHIC")
    # The one offer graphic is a MEMBER of the family, derived and not restated. The AST
    # check is the part that bites: `BANDAGE_GRAPHIC = 0x0E21` satisfies `in` too.
    assert BANDAGE_GRAPHIC in BANDAGE_GRAPHICS
    tree = ast.parse((Path(__file__).resolve().parent.parent
                      / "anima2" / "skills" / "warrior.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "BANDAGE_GRAPHIC" for t in node.targets):
            assert not isinstance(node.value, ast.Constant), (
                "BANDAGE_GRAPHIC is a literal again; derive it from BANDAGE_GRAPHICS")
            break
    else:
        raise AssertionError("BANDAGE_GRAPHIC is no longer assigned at module level")
