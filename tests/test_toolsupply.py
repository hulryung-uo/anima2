"""Brick 10 — the closed-village tool-supply link. The tinker forges the village
tools (Saw for the carpenter, Hatchet for the lumberjack) and DELIVERS one spare
to each counterpart's drop slot; a counterpart whose tool breaks FETCHES the
delivered spare instead of BUYING one from a vendor (closing the village — no
vendor tool purchases).

These target what is BRICK-10-specific: the tinker craft config for the two
tools, the tool-typed deliver drop-on-ground mechanics + the NO-OVERSUPPLY ready
gate (don't stack a second spare at a slot), the tool-typed fetch pickup
mechanics + the "only when the worker's OWN tool broke" ready gate, and the
fetch-before-buy registry preference for BOTH counterparts. The shared
deliver/fetch/craft machinery is exhaustively covered by test_boardtrade.py /
test_tinkering.py / test_craft.py and stays byte-identical for the board case.
"""

from anima2.capabilities import (
    CAPABILITIES,
    _make_deliver_ready,
    _make_tool_craft_ready,
    _make_tool_fetch_achieved,
    _make_tool_fetch_ready,
)
from anima2.contract import Drop, ItemView, Observation, PickUp, PlayerView, Position
from anima2.persona import Persona
from anima2.skills.base import SkillContext
from anima2.skills.carpentry import SAW_GRAPHIC, SAW_GRAPHICS, FetchSaw
from anima2.skills.harvest import AXE_GRAPHICS, BACKPACK_LAYER
from anima2.skills.market import IRON_INGOT_GRAPHIC
from anima2.skills.tinkering import (
    HATCHET_FLIP_GRAPHICS,
    HATCHET_GRAPHIC,
    HATCHET_IRON_PER,
    HATCHET_ITEM_BTN,
    HATCHET_NAME_CLILOC,
    SAW_IRON_PER,
    SAW_ITEM_BTN,
    SAW_NAME_CLILOC,
    TINKERTOOLS_GRAPHIC,
    TOOLS_CATEGORY_BTN,
    DeliverHatchet,
    DeliverSaw,
    TinkerHatchet,
    TinkerSaw,
)
from anima2.skills.woodwork import FetchHatchet

PLAYER = 1
BACKPACK = 0x50
DROP = (5, 5)


def _item(serial, graphic, *, container=BACKPACK, amount=1, layer=0, distance=0, pos=None):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=pos or Position(),
                    container=container, layer=layer, distance=distance)


def _backpack():
    return _item(BACKPACK, 0x0E75, container=PLAYER, layer=BACKPACK_LAYER)


def _ctx(items, *, memory, goal_id=71, pos=Position(5, 5, 0)):
    obs = Observation(player=PlayerView(serial=PLAYER, pos=pos), items=list(items))
    return SkillContext(obs=obs, persona=Persona(name="Pim"), memory=memory, goal_id=goal_id)


# --- tinker craft config: the single source of truth the factories read -------


def test_tinker_saw_config_matches_the_calibrated_servuo_recipe():
    # ServUO: Saw = Tools btn 15 -> item btn 51, 4 IronIngot, out 0x1034, "saw".
    assert TinkerSaw.craft_category_btn == TOOLS_CATEGORY_BTN  # 15, shared Tools cat
    assert TinkerSaw.craft_item_btn == SAW_ITEM_BTN == 51
    assert TinkerSaw.craft_material_per_item == SAW_IRON_PER == 4
    assert TinkerSaw.craft_output_graphic == SAW_GRAPHIC == 0x1034
    assert TinkerSaw.craft_item_name_cliloc == SAW_NAME_CLILOC == 1024148
    assert TinkerSaw.craft_batch == 1  # one durable spare, not a 5-item sale batch
    # Inherits the tinkering tool + gump + no-material-submenu path unchanged.
    assert TinkerSaw.craft_tool_graphics == frozenset({0x1EB8, 0x1EBC})
    assert TinkerSaw.craft_resource_menu_btn is None
    assert TinkerSaw.craft_material_resource_btn is None


def test_tinker_hatchet_config_matches_the_calibrated_servuo_recipe():
    # ServUO: Hatchet = Tools btn 15 -> item btn 30, 4 IronIngot, out 0xF43.
    assert TinkerHatchet.craft_category_btn == TOOLS_CATEGORY_BTN  # 15
    assert TinkerHatchet.craft_item_btn == HATCHET_ITEM_BTN == 30
    assert TinkerHatchet.craft_material_per_item == HATCHET_IRON_PER == 4
    assert TinkerHatchet.craft_output_graphic == HATCHET_GRAPHIC == 0x0F43
    assert TinkerHatchet.craft_item_name_cliloc == HATCHET_NAME_CLILOC == 1023907
    assert TinkerHatchet.craft_batch == 1
    # The Hatchet's flip pair is both in the lumberjack's AXE_GRAPHICS (so the
    # forged 0xF43 and its 0xF44 ground flip both fetch as a working axe).
    assert HATCHET_FLIP_GRAPHICS == frozenset({0x0F43, 0x0F44})
    assert HATCHET_FLIP_GRAPHICS <= AXE_GRAPHICS


def test_deliver_skills_target_distinct_drop_slots_per_counterpart():
    assert DeliverSaw.delivered_graphics == SAW_GRAPHICS
    assert DeliverSaw.deliver_threshold == 1
    assert DeliverSaw.drop_key == "carpenter_tool_drop"
    assert DeliverHatchet.delivered_graphics == HATCHET_FLIP_GRAPHICS
    assert DeliverHatchet.deliver_threshold == 1
    assert DeliverHatchet.drop_key == "lumber_drop"
    # Distinct keys so each tool reaches the right counterpart.
    assert DeliverSaw.drop_key != DeliverHatchet.drop_key


# --- registry: fetch-before-buy for BOTH counterparts + right professions ------


def test_new_tool_capabilities_are_registered_to_the_right_professions():
    assert ("tinker", "craft_saw") in CAPABILITIES
    assert ("tinker", "craft_hatchet") in CAPABILITIES
    assert ("tinker", "deliver_saw") in CAPABILITIES
    assert ("tinker", "deliver_hatchet") in CAPABILITIES
    assert ("lumberjack", "fetch_hatchet") in CAPABILITIES
    assert ("carpenter", "fetch_saw") in CAPABILITIES


def test_counterparts_prefer_free_fetch_over_paid_buy():
    lumber = [c for (p, c) in CAPABILITIES if p == "lumberjack"]
    assert lumber.index("fetch_hatchet") < lumber.index("buy_hatchet")
    carp = [c for (p, c) in CAPABILITIES if p == "carpenter"]
    assert carp.index("fetch_saw") < carp.index("buy_saw")


def test_tinker_forges_demand_gated_tools_before_its_tongs_income():
    tinker = [c for (p, c) in CAPABILITIES if p == "tinker"]
    # Tool forge/deliver come BEFORE the tongs income loop. They are DEMAND-gated
    # (ready only when a counterpart's drop key is wired AND its slot is empty — see
    # the fail-closed test below), so prioritizing them means the tinker fills a
    # real tool shortage first and falls back to Tongs income otherwise. Each craft
    # precedes its own deliver.
    assert tinker.index("craft_saw") < tinker.index("craft_tongs")
    assert tinker.index("craft_hatchet") < tinker.index("craft_tongs")
    assert tinker.index("craft_saw") < tinker.index("deliver_saw")
    assert tinker.index("craft_hatchet") < tinker.index("deliver_hatchet")


def test_craft_tool_fails_closed_without_a_wired_drop_key():
    # Fix: _make_tool_craft_ready requires a VALID drop point (matching the deliver
    # gate) — so a standalone tinker (no drop key) never forges a tool it cannot
    # deliver, and is NOT starved of its Tongs income despite the tools' higher
    # registry priority. With every craft prerequisite met, only the drop key toggles.
    craft_ready = _make_tool_craft_ready(TinkerHatchet, HATCHET_FLIP_GRAPHICS, "lumber_drop")
    ready_items = [_backpack(), _item(0x41, TINKERTOOLS_GRAPHIC),
                   _item(0x42, IRON_INGOT_GRAPHIC, amount=8)]
    # No drop key wired -> fail closed even though the craft prereqs hold.
    assert not craft_ready(_ctx(ready_items, memory={"craft_spot": DROP}, goal_id=None))
    # Drop key wired to an empty far slot -> forge is now allowed.
    assert craft_ready(_ctx(ready_items,
                            memory={"craft_spot": DROP, "lumber_drop": (50, 50)}, goal_id=None))


# --- DeliverHatchet: PickUp a pack tool -> Drop it on the GROUND at the slot ----


def test_deliver_hatchet_drops_the_forged_tool_on_the_ground_then_finishes():
    skill = DeliverHatchet()
    mem = {"lumber_drop": DROP, "lumber_home": DROP}  # already at the slot
    hatchet = _item(0x700, HATCHET_GRAPHIC)  # one forged hatchet in the pack

    # Tick 1: begin (freeze 1) + at the slot + nothing held -> lift the tool.
    r1 = skill.step(_ctx([_backpack(), hatchet], memory=mem))
    assert mem["cap_deliver_needed"] == 1
    assert isinstance(r1.action, PickUp) and r1.action.serial == 0x700

    # Tick 2: the tool is on the cursor -> Drop it on the GROUND at the slot.
    r2 = skill.step(_ctx([_backpack()], memory=mem))
    assert isinstance(r2.action, Drop)
    assert r2.action.container == 0xFFFFFFFF          # ground drop, not into a pack
    assert (r2.action.x, r2.action.y) == DROP

    # Tick 3: pack empty, home == slot -> the spare is delivered, goal finished.
    r3 = skill.step(_ctx([_backpack()], memory=mem))
    assert mem["cap_deliver_finished_goal_id"] == 71
    assert mem["cap_deliver_delivered"] == 1
    assert mem["cap_deliver_boards_remaining"] == 0
    assert r3.action is None
    # Achieved (tool-graphics summed pack count == 0), goal-scoped.
    assert CAPABILITIES[("tinker", "deliver_hatchet")].achieved(_ctx([_backpack()], memory=mem))


def test_deliver_saw_ready_gates_on_the_no_oversupply_slot_check():
    ready = _make_deliver_ready(DeliverSaw)
    saw = _item(0x700, SAW_GRAPHIC)  # a forged saw to carry
    mem = {"carpenter_tool_drop": DROP}
    # A saw to carry + a valid slot + no spare already there -> ready.
    assert ready(_ctx([_backpack(), saw], memory=mem, goal_id=None))
    # A spare saw already on the GROUND at the slot -> NOT ready (keep exactly one).
    spare = _item(0x900, SAW_GRAPHIC, container=None, pos=Position(5, 5, 0))
    assert not ready(_ctx([_backpack(), saw, spare], memory=mem, goal_id=None))
    # No slot configured -> not ready (nowhere to deliver).
    assert not ready(_ctx([_backpack(), saw], memory={}, goal_id=None))
    # Nothing to carry -> not ready.
    assert not ready(_ctx([_backpack()], memory=mem, goal_id=None))


def test_deliver_hatchet_ready_no_oversupply_uses_the_lumber_drop_slot():
    ready = _make_deliver_ready(DeliverHatchet)
    hatchet = _item(0x700, HATCHET_GRAPHIC)
    mem = {"lumber_drop": DROP}
    assert ready(_ctx([_backpack(), hatchet], memory=mem, goal_id=None))
    # The ground flip orientation (0xF44) also counts as an existing spare.
    flip = _item(0x900, 0x0F44, container=None, pos=Position(5, 5, 0))
    assert not ready(_ctx([_backpack(), hatchet, flip], memory=mem, goal_id=None))


# --- FetchHatchet / FetchSaw: PickUp a nearby GROUND tool -> Drop it INTO pack --


def test_fetch_hatchet_lifts_the_delivered_tool_into_the_pack_then_finishes():
    skill = FetchHatchet()
    mem: dict = {}
    ground = _item(0x800, HATCHET_GRAPHIC, container=None, distance=1, pos=Position(5, 5, 0))

    # Tick 1: begin (a ground hatchet is nearby, pack has no axe) -> lift it.
    r1 = skill.step(_ctx([_backpack(), ground], memory=mem))
    assert isinstance(r1.action, PickUp) and r1.action.serial == 0x800

    # Tick 2: the tool is on the cursor -> Drop it INTO the pack.
    r2 = skill.step(_ctx([_backpack()], memory=mem))
    assert isinstance(r2.action, Drop) and r2.action.container == BACKPACK

    # Tick 3: the hatchet is in the pack, no ground tool remains -> finished.
    r3 = skill.step(_ctx([_backpack(), _item(0x800, HATCHET_GRAPHIC)], memory=mem))
    assert mem["cap_fetch_finished_goal_id"] == 71
    assert mem["cap_fetch_fetched"] == 1
    assert r3.action is None


def test_fetch_hatchet_ready_only_when_the_workers_own_axe_broke():
    ready = _make_tool_fetch_ready(FetchHatchet.fetched_graphics)
    ground = _item(0x800, HATCHET_GRAPHIC, container=None, distance=1, pos=Position(5, 5, 0))
    # A delivered hatchet nearby + NO working axe in the pack -> ready (axe broke).
    assert ready(_ctx([_backpack(), ground], memory={}, goal_id=None))
    # A working axe already in the pack -> NOT ready (nothing broke, no fetch).
    own_axe = _item(0x600, HATCHET_GRAPHIC)  # in the pack (container=BACKPACK)
    assert not ready(_ctx([_backpack(), ground, own_axe], memory={}, goal_id=None))
    # No ground tool nearby -> not ready.
    assert not ready(_ctx([_backpack()], memory={}, goal_id=None))


def test_fetch_saw_ready_only_when_the_carpenters_own_saw_broke():
    ready = _make_tool_fetch_ready(FetchSaw.fetched_graphics)
    ground = _item(0x800, SAW_GRAPHIC, container=None, distance=1, pos=Position(5, 5, 0))
    assert FetchSaw.fetched_graphics == SAW_GRAPHICS
    assert ready(_ctx([_backpack(), ground], memory={}, goal_id=None))
    own_saw = _item(0x600, SAW_GRAPHIC)
    assert not ready(_ctx([_backpack(), ground, own_saw], memory={}, goal_id=None))


def test_fetch_tool_achieved_requires_arrival_and_no_ground_remnant():
    achieved = _make_tool_fetch_achieved(FetchHatchet.fetched_graphics)
    mem = {"cap_fetch_goal_id": 71, "cap_fetch_finished_goal_id": 71,
           "cap_fetch_fetched": 1, "cap_fetch_ground_remaining": 0}
    # Tool arrived, nothing left on the ground -> achieved.
    assert achieved(_ctx([_backpack(), _item(0x800, HATCHET_GRAPHIC)], memory=mem))
    # A ground tool still present -> not achieved (fail closed on live obs).
    ground = _item(0x900, HATCHET_GRAPHIC, container=None, distance=1, pos=Position(5, 5, 0))
    assert not achieved(_ctx([_backpack(), _item(0x800, HATCHET_GRAPHIC), ground], memory=mem))
    # Nothing fetched -> not achieved.
    mem["cap_fetch_fetched"] = 0
    assert not achieved(_ctx([_backpack(), _item(0x800, HATCHET_GRAPHIC)], memory=mem))


# --- craft gate: forge a spare ONLY when the slot AND the pack are tool-empty ---


def _tinker_craft_ctx(items, *, memory, pos=Position(100, 100, 0)):
    obs = Observation(player=PlayerView(serial=PLAYER, pos=pos), items=list(items))
    return SkillContext(obs=obs, persona=Persona(name="Pim"), memory=memory, goal_id=71)


def test_craft_saw_ready_gated_by_empty_slot_and_empty_pack():
    ready = _make_tool_craft_ready(TinkerSaw, DeliverSaw.delivered_graphics, DeliverSaw.drop_key)
    base = [
        _backpack(),
        _item(0x41, TINKERTOOLS_GRAPHIC),                 # the tinker tool
        _item(0x42, IRON_INGOT_GRAPHIC, amount=8),        # >= 4 iron for one saw
    ]
    mem = {"craft_spot": (100, 100), "carpenter_tool_drop": DROP}
    # Slot empty, pack tool-empty, iron + tool + at the craft spot -> ready.
    assert ready(_tinker_craft_ctx(base, memory=mem))
    # A saw already sitting on the ground at the slot -> NOT ready (no oversupply).
    at_slot = base + [_item(0x99, SAW_GRAPHIC, container=None, pos=Position(5, 5, 0))]
    assert not ready(_tinker_craft_ctx(at_slot, memory=mem))
    # An undelivered saw still in the pack -> NOT ready (deliver it first).
    in_pack = base + [_item(0x98, SAW_GRAPHIC)]
    assert not ready(_tinker_craft_ctx(in_pack, memory=mem))
    # No iron -> the base craft gate already fails.
    no_iron = [_backpack(), _item(0x41, TINKERTOOLS_GRAPHIC)]
    assert not ready(_tinker_craft_ctx(no_iron, memory=mem))
