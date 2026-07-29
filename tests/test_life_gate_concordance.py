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
    return Observation(player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0)),
                       items=[_backpack(), *items], mobiles=list(mobiles))


def _assert_concordance(profession, decide, cases, expected_caps):
    """Forward: rule wants cap => gate ready. Coverage: every cap wanted somewhere."""
    wanted: set[str] = set()
    for obs, memory in cases:
        mode, cap = decide(obs, dict(memory))
        if mode != "economy" or cap is None:
            continue
        wanted.add(cap)
        ctx = SkillContext(obs=obs, persona=Persona(name="T"), memory=dict(memory))
        ready = ready_capability_ids(profession, ctx)
        assert cap in ready, (
            f"{profession}: the rule wants {cap!r} but the gate refuses "
            f"(ready={sorted(ready)}).\nThis is the exact stall shape that cost six "
            f"live runs. Observation items: "
            + ", ".join(f"g=0x{i.graphic:X} c={i.container} l={i.layer} d={i.distance} "
                        f"n={i.amount}" for i in obs.items)
        )
    missing = set(expected_caps) - wanted
    assert not missing, (
        f"{profession}: no lattice point ever WANTS {sorted(missing)} — either the "
        f"lattice lost an axis or the rule has a dead branch (the unreachable "
        f"BANK_ABOVE=300 shape)."
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
    )
    from anima2.skills.carpentry import BuySaw, FetchBoards, SellFurniture

    SAW = sorted(BuySaw.owned_tool_graphics)[0]
    BOARD = sorted(FetchBoards.fetched_graphics)[0]
    FURNITURE = SellFurniture.sold_graphic
    memory = {"vendor_spot": ((10, 10),), "banker_spot": ((10, 10),),
              "bank_reserve": BANK_RESERVE, "craft_spot": (5, 5)}

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

    cases = []
    for (_, saw), packb, (_, groundb), furn, gold in product(
            saw_axis.items(), pack_board_axis, ground_board_axis.items(),
            furniture_axis, gold_axis):
        items = [*saw, *groundb]
        if packb:
            items.append(_item(BOARD, packb))
        if furn:
            items.append(_item(FURNITURE, furn))
        if gold:
            items.append(_item(GOLD_GRAPHIC, gold))
        cases.append((_obs(items), memory))

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
        IRON_BATCH_COST,
        SELL_TONGS_AT,
        TOOL_COST,
        decide_mode as tinker_decide,
    )

    IRON = sorted(INGOT_GRAPHICS)[0]
    TOOL = sorted(TinkerTongs.craft_tool_graphics)[0]
    memory = {"vendor_spot": ((10, 10),), "banker_spot": ((10, 10),),
              "bank_reserve": BANK_RESERVE,
              # The craft gate allows a small drift radius around this; the player
              # fixture stands at (5, 5), inside it — the REAL wiring.
              "craft_spot": (5, 5)}

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
                        IRON_BATCH_COST, BANK_RESERVE, BANK_RESERVE + 1})

    cases = []
    for (_, (tool, mobs)), tongs, iron, (_, ground), gold in product(
            tool_axis.items(), tongs_axis, iron_axis, ground_axis.items(), gold_axis):
        items = [*tool, *ground]
        if tongs:
            items.append(_item(TONGS_GRAPHIC, tongs))
        if iron:
            items.append(_item(IRON, iron))
        if gold:
            items.append(_item(GOLD_GRAPHIC, gold))
        cases.append((_obs(items, mobs), memory))

    _assert_concordance("tinker", tinker_decide, cases,
                        {"buy_tinker_tool", "sell_tongs", "fetch_iron", "craft_tongs",
                         "buy_iron", "bank_gold"})
