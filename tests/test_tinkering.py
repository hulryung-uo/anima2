"""Tinker capability skills (Bricks 7-10) — config wiring + the no-material-submenu
tinkering craft path, on hand-built observations.

The tinker's five skills are thin config subclasses of the already-verified
craft/market machinery, so these tests target what is TINKER-specific: the config
attrs the leaf-func factories read (the single source of truth), the craft gump
FSM's open->category->item path keyed on the TINKERING title cliloc (1044007, not
blacksmithy's 1044002 nor carpentry's 1044004 — the regression that stalled the
carpenter live), with the resource submenu SKIPPED, and the sell/buy offer
resolution by the tinker's own graphics. The shared machinery itself is
exhaustively covered by `test_craft.py`/`test_market.py`/`test_carpentry.py` and
stays byte-identical.
"""

from anima2.contract import GumpResponse, GumpView, ItemView, Position, Use
from anima2.persona import Persona
from anima2.skills.base import SkillContext
from anima2.skills.craft import IRON_RESOURCE_BTN, RESOURCE_MENU_BTN
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.market import GOLD_GRAPHIC, IRON_INGOT_GRAPHIC
from anima2.skills.smelt import INGOT_GRAPHICS
from anima2.skills.tinkering import (
    SCISSORS_GRAPHIC,
    SCISSORS_ITEM_BTN,
    SCISSORS_IRON_PER,
    SCISSORS_NAME_CLILOC,
    TINKERING_TITLE_CLILOC,
    TINKERTOOLS_FORSALE_GRAPHIC,
    TINKERTOOLS_GRAPHIC,
    TINKERTOOLS_GRAPHICS,
    TONGS_GRAPHIC,
    TONGS_IRON_PER,
    TONGS_ITEM_BTN,
    TONGS_NAME_CLILOC,
    TOOLS_CATEGORY_BTN,
    BuyIron,
    BuyTinkerTool,
    SellTongs,
    TinkerScissors,
    TinkerTongs,
)

BACKPACK = 0x50
TOOLS_SERIAL = 0x41


def _item(serial, graphic, *, container=BACKPACK, amount=1, layer=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=0)


def _backpack():
    return _item(BACKPACK, 0x0E75, container=1, layer=BACKPACK_LAYER)


def _tools():
    return _item(TOOLS_SERIAL, TINKERTOOLS_GRAPHIC)


def _iron(serial, amount):
    return _item(serial, IRON_INGOT_GRAPHIC, amount=amount)


def _tctx(items, *, memory, goal_id=41, gumps=()):
    from anima2.contract import Observation, PlayerView

    obs = Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)),
                      items=[_tools(), *items], gumps=list(gumps))
    return SkillContext(obs=obs, persona=Persona(name="Pim"), memory=memory, goal_id=goal_id)


# --- config: the single source of truth the leaf-func factories read ---------


def test_tinker_graphics_and_buttons_match_servuo_and_live_calibration():
    # ServUO: TinkerTools base(0x1EB8) [Flipable 0x1EB8,0x1EBC]; Tongs base(0xFBB);
    # Scissors base(0xF9F); IronIngot 0x1BF2.
    assert TINKERTOOLS_GRAPHIC == 0x1EB8
    assert TINKERTOOLS_GRAPHICS == frozenset({0x1EB8, 0x1EBC})
    assert TONGS_GRAPHIC == 0x0FBB
    assert SCISSORS_GRAPHIC == 0x0F9F
    assert IRON_INGOT_GRAPHIC in INGOT_GRAPHICS
    assert TONGS_IRON_PER == 1
    assert SCISSORS_IRON_PER == 2
    assert TONGS_NAME_CLILOC == 1024028      # "tongs"
    assert SCISSORS_NAME_CLILOC == 1023998   # "scissors"
    # The tinkering gump's own title (NOT 1044002 / 1044004).
    assert TINKERING_TITLE_CLILOC == 1044007
    # Live-calibrated CraftGump buttons (`1 + type + index*7`): Tools == 15, tongs
    # (Tools index 12) == 86; scissors (Tools index 0) == 2.
    assert TOOLS_CATEGORY_BTN == 15
    assert TONGS_ITEM_BTN == 86
    assert SCISSORS_ITEM_BTN == 2


def test_craft_tongs_config_has_no_material_submenu_and_its_own_title():
    assert TinkerTongs.craft_tool_graphics == TINKERTOOLS_GRAPHICS
    assert TinkerTongs.craft_title_cliloc == TINKERING_TITLE_CLILOC
    assert TinkerTongs.craft_category_btn == TOOLS_CATEGORY_BTN
    assert TinkerTongs.craft_item_btn == TONGS_ITEM_BTN
    assert TinkerTongs.craft_material_graphics == INGOT_GRAPHICS  # inherited iron
    assert TinkerTongs.craft_material_per_item == TONGS_IRON_PER
    assert TinkerTongs.craft_output_graphic == TONGS_GRAPHIC
    assert TinkerTongs.craft_item_name_cliloc == TONGS_NAME_CLILOC
    assert TinkerTongs.craft_batch == 5  # a sale-sized batch (5 iron -> 5 tongs)
    # NO material submenu (both None skip the resource stages, like carpentry).
    assert TinkerTongs.craft_resource_menu_btn is None
    assert TinkerTongs.craft_material_resource_btn is None


def test_tinker_scissors_smoke_shares_the_tongs_path_only_the_item_differs():
    assert issubclass(TinkerScissors, TinkerTongs)
    assert TinkerScissors.craft_title_cliloc == TINKERING_TITLE_CLILOC
    assert TinkerScissors.craft_category_btn == TOOLS_CATEGORY_BTN  # both in Tools
    assert TinkerScissors.craft_item_btn == SCISSORS_ITEM_BTN
    assert TinkerScissors.craft_output_graphic == SCISSORS_GRAPHIC
    assert TinkerScissors.craft_material_per_item == SCISSORS_IRON_PER


def test_sell_tongs_config_targets_tongs_at_the_tinker():
    assert SellTongs.sold_graphic == TONGS_GRAPHIC
    assert SellTongs.sell_threshold == 5  # sell a full craft batch per trip
    assert SellTongs.vendor_spot_key == "vendor_spot"  # the one Tinker NPC


def test_buy_iron_config_targets_iron_at_the_tinker():
    assert BuyIron.buy_material_graphics == INGOT_GRAPHICS
    assert BuyIron.buy_offer_graphic == IRON_INGOT_GRAPHIC
    assert BuyIron.buy_reorder == 5           # below one craft batch's iron
    assert BuyIron.buy_price_estimate == 5    # SBTinker sells IronIngot @5g
    assert BuyIron.vendor_spot_key == "vendor_spot"


def test_buy_tinker_tool_config_targets_the_tinker_tool_at_the_tinker():
    assert BuyTinkerTool.owned_tool_graphics == TINKERTOOLS_GRAPHICS
    assert BuyTinkerTool.offer_graphic == TINKERTOOLS_FORSALE_GRAPHIC  # 0x1EBC for-sale art
    assert TINKERTOOLS_FORSALE_GRAPHIC == 0x1EBC and TINKERTOOLS_GRAPHIC == 0x1EB8
    assert BuyTinkerTool.tool_price_estimate == 7  # SBTinker TinkersTools @7g
    assert BuyTinkerTool.vendor_spot_key == "vendor_spot"


# --- craft_tongs: the gump FSM keyed on the TINKERING title, submenu SKIPPED ---

# The tinkering gump carries ITS OWN title cliloc (1044007) — the mock must match
# reality or it re-encodes the bug that stalled the carpenter live.
def _tinker_gump(serial=0xAB):
    layout = (
        f"{{ xmfhtmlgumpcolor 0 0 0 0 {TINKERING_TITLE_CLILOC} 0 0 0 }}"
        f"{{ xmfhtmlgumpcolor 0 0 0 0 {TONGS_NAME_CLILOC} 0 0 0 }}"
    )
    return GumpView(
        serial=serial, gump_id=0xCD, layout=layout,
        elements=[
            {"type": "button", "pageflag": 1, "reply_id": TOOLS_CATEGORY_BTN},
            {"type": "button", "pageflag": 1, "reply_id": TONGS_ITEM_BTN},
        ],
    )


def test_craft_tongs_navigates_the_tinkering_gump_with_no_submenu():
    skill = TinkerTongs()
    mem: dict = {}
    actions = []

    # Tick 1: begin (freeze 0 tongs, needed=5 batch) + open with tinker tools.
    open_res = skill.step(_tctx([_backpack(), _iron(0x800, 20)], memory=mem))
    actions.append(open_res.action)
    assert mem["cap_craft_needed"] == 5
    assert isinstance(open_res.action, Use) and open_res.action.serial == TOOLS_SERIAL

    # Ticks 2-3: category (15) then item (86) — NO resource submenu in between.
    for _ in range(2):
        res = skill.step(_tctx([_backpack(), _iron(0x800, 20)], memory=mem,
                               gumps=[_tinker_gump()]))
        actions.append(res.action)

    buttons = [a.button for a in actions[1:] if isinstance(a, GumpResponse)]
    assert buttons == [TOOLS_CATEGORY_BTN, TONGS_ITEM_BTN]  # [15, 86]
    assert RESOURCE_MENU_BTN not in buttons
    assert IRON_RESOURCE_BTN not in buttons
    # The first make attempt was snapshotted and sent (item button pressed).
    assert mem["cap_craft_stage"] == "pending"


def test_craft_tongs_ignores_a_carpentry_titled_gump():
    """Regression / cross-profession isolation: the tinker's tool opens a gump
    titled 1044007; a gump carrying carpentry's 1044004 (or blacksmithy's 1044002)
    must be INVISIBLE to `_craft_gump` so the FSM never mis-drives it — it waits,
    emitting no category button. Proves `craft_title_cliloc` keys per craft SYSTEM."""
    from anima2.skills.carpentry import CARPENTRY_TITLE_CLILOC

    wrong_layout = (
        f"{{ xmfhtmlgumpcolor 0 0 0 0 {CARPENTRY_TITLE_CLILOC} 0 0 0 }}"
        f"{{ xmfhtmlgumpcolor 0 0 0 0 {TONGS_NAME_CLILOC} 0 0 0 }}"
    )
    wrong_gump = GumpView(
        serial=0xAB, gump_id=0xCD, layout=wrong_layout,
        elements=[
            {"type": "button", "pageflag": 1, "reply_id": TOOLS_CATEGORY_BTN},
            {"type": "button", "pageflag": 1, "reply_id": TONGS_ITEM_BTN},
        ],
    )
    skill = TinkerTongs()
    mem: dict = {}
    skill.step(_tctx([_backpack(), _iron(0x800, 20)], memory=mem))  # Use(tools)
    for _ in range(4):
        res = skill.step(_tctx([_backpack(), _iron(0x800, 20)], memory=mem,
                               gumps=[wrong_gump]))
        assert not isinstance(res.action, GumpResponse), (
            "tinker drove a carpentry-titled gump — title isolation broken"
        )
    assert mem["cap_craft_stage"] == "category"  # still waiting, not advanced


def test_gold_graphic_is_shared_bank_currency():
    # bank_gold is profession-agnostic; the tinker banks the same GOLD_GRAPHIC.
    assert GOLD_GRAPHIC == 0x0EED


# --- the pockets-full band: banking preempts craft (forge4, 2026-07-30) --------------
#
# The patient bank branch sits below craft and only fires in a supply GAP — and a
# HEALTHY miner never opens one: Pim finished a live 1500-tick day carrying 210g with
# bank_gold in the ready set throughout. Above reserve + one restock of spare gold,
# banking must outrank a ready craft; free ground iron still outranks the trip
# (drops decay, pack gold does not).

def _decide_obs(items):
    from anima2.contract import Observation, PlayerView

    return Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)),
                       items=[_backpack(), _tools(), *items])


def _decide_memory():
    from anima2.tinker_life import BANK_RESERVE

    return {"vendor_spot": ((10, 10),), "banker_spot": ((10, 10),),
            "bank_reserve": BANK_RESERVE, "craft_spot": (0, 0)}


def test_pockets_full_banking_preempts_a_ready_craft():
    from anima2.tinker_life import BANK_RESERVE, BANK_TRIP_SURPLUS, decide_mode

    gold = BANK_RESERVE + BANK_TRIP_SURPLUS + 1
    obs = _decide_obs([_iron(0x800, 20), _item(0x801, GOLD_GRAPHIC, amount=gold)])
    assert decide_mode(obs, _decide_memory()) == ("economy", "bank_gold")


def test_at_the_pockets_full_edge_craft_still_wins():
    from anima2.tinker_life import BANK_RESERVE, BANK_TRIP_SURPLUS, decide_mode

    gold = BANK_RESERVE + BANK_TRIP_SURPLUS  # not ABOVE the band -> patient order
    obs = _decide_obs([_iron(0x800, 20), _item(0x801, GOLD_GRAPHIC, amount=gold)])
    assert decide_mode(obs, _decide_memory()) == ("economy", "craft_tongs")


def test_free_ground_iron_still_outranks_the_urgent_bank_trip():
    from anima2.contract import ItemView
    from anima2.skills.craft import PICKUP_RADIUS
    from anima2.tinker_life import BANK_RESERVE, BANK_TRIP_SURPLUS, decide_mode

    gold = BANK_RESERVE + BANK_TRIP_SURPLUS + 1
    ground = ItemView(serial=0x900, graphic=IRON_INGOT_GRAPHIC, amount=10,
                      pos=Position(1, 1, 0), container=None, layer=0,
                      distance=PICKUP_RADIUS)
    obs = _decide_obs([ground, _item(0x801, GOLD_GRAPHIC, amount=gold)])
    assert decide_mode(obs, _decide_memory()) == ("economy", "fetch_iron")


def test_malformed_bank_reserve_clamps_identically_for_rule_and_gate():
    # Review-caught: decide_mode used to read bank_reserve RAW while the gate and
    # the BankGold FSM read it through market._bank_reserve's clamp — a negative
    # knob value (a genome/bandit axis exploring, a bad steering write) made the
    # urgent branch want bank_gold at ANY gold while the gate refused at 0g:
    # the rule-vs-gate drift class, recreated through the tuning knob itself.
    from anima2.tinker_life import decide_mode

    memory = _decide_memory()
    memory["bank_reserve"] = -100  # malformed -> every reader clamps to 0
    gold = 50  # above the clamped reserve (0), below clamp + surplus (75)
    obs = _decide_obs([_iron(0x800, 20), _item(0x801, GOLD_GRAPHIC, amount=gold)])
    # Urgent branch must NOT fire (50 <= 0 + 75); craft outranks the patient bank.
    assert decide_mode(obs, memory) == ("economy", "craft_tongs")


# --- the urgent band as a tuning knob (audit #5, the second knob) --------------------
#
# `bank_trip_surplus` is the tinker's own §E "priority band" axis and the first knob
# added AFTER the clamp was generalized into `anima2/knobs.py`. It is RULE-ONLY: the
# only bank gate is `gold > bank_reserve`, and the urgent band `gold > reserve +
# surplus` is strictly stricter for any surplus >= 0 — which is exactly why the read
# is clamped, since at a NEGATIVE value the band goes LOOSER than the gate and the
# rule wants a deposit admission refuses. Same drift class, second knob.

def _gate_ready(obs, memory):
    from anima2.capabilities import ready_capability_ids
    from anima2.skills.base import SkillContext

    return ready_capability_ids(
        "tinker", SkillContext(obs=obs, persona=Persona(name="Pim"),
                               memory=dict(memory)))


def test_a_tuned_bank_trip_surplus_moves_the_urgent_band():
    from anima2.tinker_life import BANK_RESERVE, decide_mode

    memory = dict(_decide_memory(), bank_trip_surplus=10)
    gold = BANK_RESERVE + 11  # inside the DEFAULT band, above the tuned one
    obs = _decide_obs([_iron(0x800, 20), _item(0x801, GOLD_GRAPHIC, amount=gold)])
    assert decide_mode(obs, memory) == ("economy", "bank_gold")
    # ...and untuned, the same gold leaves craft on top — the knob moved the rule,
    # not the fixture.
    assert decide_mode(obs, _decide_memory()) == ("economy", "craft_tongs")
    # The gate agrees at both settings: it reads `bank_reserve`, which the band only
    # ever narrows.
    assert "bank_gold" in _gate_ready(obs, memory)


def test_bank_trip_surplus_clamps_identically_for_rule_and_gate():
    """Set, absent and MALFORMED — the three states a tuning write can leave behind.

    The malformed row is the whole reason this knob is read through `knob_int`: at
    `-100` an unclamped band would fire at 1g of surplus while `_bank_ready` still
    refuses below the reserve, which is the stall shape six live runs paid for.
    """
    from anima2.tinker_life import BANK_RESERVE, BANK_TRIP_SURPLUS, decide_mode

    gold = BANK_RESERVE + 1  # one coin of surplus: inside every non-zero band
    obs = _decide_obs([_iron(0x800, 20), _item(0x801, GOLD_GRAPHIC, amount=gold)])
    for label, value in [("set", BANK_TRIP_SURPLUS), ("absent", None),
                         ("negative", -100), ("float", 2.5), ("bool", True),
                         ("string", "0")]:
        memory = _decide_memory()
        if value is not None:
            memory["bank_trip_surplus"] = value
        mode, cap = decide_mode(obs, memory)
        if cap is not None:
            assert cap in _gate_ready(obs, memory), (
                f"{label}: the rule wants {cap!r} the gate refuses — the knob pried "
                f"the two apart"
            )
        # Every malformed value collapses to the same clamped band (surplus 0), so
        # one coin above the reserve is already urgent; a healthy value keeps craft.
        expected = "bank_gold" if label not in {"set", "absent"} else "craft_tongs"
        assert cap == expected, f"{label}: wanted {cap!r}"


def test_the_tinker_writes_its_own_urgent_band_at_construction():
    # The knob is a MEMORY key because `decide_mode` is a staticmethod over
    # `(obs, memory)` — a rule can only see a knob that lives where it looks.
    from anima2.mock_body import MockBody
    from anima2.tinker_life import BANK_TRIP_SURPLUS, TinkerLife

    life = TinkerLife(body=MockBody(), persona=Persona(name="Pim"))
    assert life.econ_agent.memory["bank_trip_surplus"] == BANK_TRIP_SURPLUS
    tuned = TinkerLife(body=MockBody(), persona=Persona(name="Pim"),
                       bank_trip_surplus=5)
    assert tuned.econ_agent.memory["bank_trip_surplus"] == 5


def test_a_given_up_bank_goal_closes_its_frame_instead_of_zombieing():
    # forge1 (audit #6's one anomaly) and forge13 live, root-caused offline: a
    # bank run whose FSM gives up (banker not found — here structurally, the
    # mock stages no banker) marks its goal id finished and walks home, but
    # nothing CLOSED the frame: `CapabilityGoalComplete` requires achievement,
    # so the frame sat admitted until its deadline while the inner skill
    # no-opped on the finished id — 20+ live minutes of a Life doing nothing,
    # with a delivery rotting on the ground beside it. The wrapper's neutral
    # `cap_run_finished_goal_id` marker now lets the frame close as FAILURE,
    # and the Life must simply move on to the work it wants.
    from anima2.contract import ItemView, PlayerView, Position
    from anima2.mock_body import MockBody
    from anima2.skills.harvest import BACKPACK_LAYER
    from anima2.skills.smelt import INGOT_GRAPHICS
    from anima2.tinker_life import TinkerLife

    IRON = sorted(INGOT_GRAPHICS)[0]
    PIM, PACK = 0x1, 0xB1
    items = {
        PACK: ItemView(serial=PACK, graphic=0x0E75, amount=1, pos=Position(),
                       container=PIM, layer=BACKPACK_LAYER, distance=0),
        0x100: ItemView(serial=0x100, graphic=TINKERTOOLS_GRAPHIC, amount=1,
                        pos=Position(), container=PACK, layer=0, distance=0),
        0x101: ItemView(serial=0x101, graphic=GOLD_GRAPHIC, amount=140,
                        pos=Position(), container=PACK, layer=0, distance=0),
    }
    body = MockBody(player=PlayerView(serial=PIM, name="Pim",
                                      pos=Position(10, 10, 0)), items=items)
    life = TinkerLife(body=body, persona=Persona(name="Pim"),
                      routes={"vendor_spot": ((12, 10),),
                              "banker_spot": ((10, 12),)})
    life.set_leash((10, 10), 3)
    for m in (life.memory, life.econ_agent.memory):
        m["craft_spot"] = (10, 10)

    def admitted():
        cur = life.econ_agent.goal_stack.current
        return cur.goal.params.get("capability") if cur else None

    for _ in range(30):  # supply gap: 140g > reserve -> bank_gold admitted
        life.tick()
        if admitted() == "bank_gold":
            break
    assert admitted() == "bank_gold"

    # The mid-goal delivery that turned forge13's wedge visible.
    items[0x200] = ItemView(serial=0x200, graphic=IRON, amount=17,
                            pos=Position(11, 10, 0), container=None, layer=0,
                            distance=1)
    for t in range(40):
        life.tick()
        if admitted() != "bank_gold":
            break
    assert admitted() != "bank_gold", (
        "the given-up bank frame never closed — the forge13 zombie is back")
    # And the Life actually moved on: give it the rest of the errand and the
    # delivered iron must reach the pack (fetch executed, not just re-wanted).
    for _ in range(60):
        life.tick()
        pack_iron = sum(i.amount for i in items.values()
                        if i.graphic == IRON and i.container == PACK)
        if pack_iron > 0:
            break
    assert pack_iron > 0
