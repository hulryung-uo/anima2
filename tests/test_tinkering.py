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
