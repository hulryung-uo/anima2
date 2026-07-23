"""Carpenter capability skills (Bricks 4-5) — config wiring + the genuinely new
no-material-submenu craft path, on hand-built observations.

The carpenter's five skills are thin config subclasses of the already-verified
craft/market machinery, so these tests target what is CARPENTER-specific: the
config attrs the leaf-func factories read (the single source of truth), the craft
gump FSM's open->category->item path with the resource submenu SKIPPED (the first
profession to exercise it), and the sell/buy offer resolution by the carpenter's
own graphics. The shared machinery itself is exhaustively covered by
`test_craft.py`/`test_market.py`/`test_woodwork.py` and stays byte-identical.
"""

from anima2.capabilities import CAPABILITIES
from anima2.contract import (
    BuyItems,
    GumpResponse,
    GumpView,
    ItemView,
    MobileView,
    Observation,
    PlayerView,
    PopupEntry,
    PopupMenu,
    PopupRequest,
    PopupSelect,
    Position,
    SellItems,
    ShopBuy,
    ShopBuyEntry,
    ShopSell,
    ShopSellItem,
    Use,
)
from anima2.persona import Persona
from anima2.skills.base import SkillContext
from anima2.skills.carpentry import (
    BARRELSTAVE_CATEGORY_BTN,
    BARRELSTAVE_ITEM_BTN,
    SAW_GRAPHIC,
    SAW_GRAPHICS,
    THRONE_BOARDS_PER,
    THRONE_CATEGORY_BTN,
    THRONE_GRAPHIC,
    THRONE_ITEM_BTN,
    THRONE_NAME_CLILOC,
    BuyBoards,
    BuySaw,
    CarpenterCraft,
    SellFurniture,
)
from anima2.skills.carpentry import CARPENTRY_TITLE_CLILOC
from anima2.skills.craft import IRON_RESOURCE_BTN, RESOURCE_MENU_BTN
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.market import BankGold, BuyTool, GOLD_GRAPHIC, SELL_CLILOC
from anima2.skills.woodwork import BOARD_GRAPHIC, BOARD_GRAPHICS

BUY_CLILOC = 3_006_103
BACKPACK = 0x50
SAW_SERIAL = 0x40
VENDOR = (10, 0)
VENDOR_SERIAL = 0xAAA1
VENDOR_MOBILE = 0xBBB1
BOARD_CONTAINER = 0xCCC1
THRONE_SERIAL = 0x0B33_00
BOARD_OFFER_SERIAL = 0xDD01
SAW_OFFER_SERIAL = 0xDD34


def _item(serial, graphic, *, container=BACKPACK, amount=1, layer=0, distance=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=distance)


def _backpack():
    return _item(BACKPACK, 0x0E75, container=1, layer=BACKPACK_LAYER)


def _saw():
    return _item(SAW_SERIAL, SAW_GRAPHIC)


def _boards(serial, amount):
    return _item(serial, BOARD_GRAPHIC, amount=amount)


def _throne(serial=THRONE_SERIAL):
    return _item(serial, THRONE_GRAPHIC)


def _gold(serial, amount):
    return _item(serial, GOLD_GRAPHIC, amount=amount)


def _mobile(serial, x, y):
    return MobileView(serial=serial, name="", pos=Position(x, y, 0), body=0x190,
                      notoriety=1, hits=10, hits_max=10, distance=0)


def _popup(serial, clilocs):
    return PopupMenu(serial=serial,
                     entries=[PopupEntry(index=i, cliloc=c) for i, c in enumerate(clilocs)])


def _mctx(items, *, memory, pos=Position(0, 0, 0), goal_id=None, gumps=(),
          mobiles=(), popup=None, shop_sell=None, shop_buy=None, pending=None):
    obs = Observation(player=PlayerView(serial=1, pos=pos), items=list(items),
                      gumps=list(gumps), mobiles=list(mobiles), popup=popup,
                      shop_sell=shop_sell, shop_buy=shop_buy, pending_target=pending)
    return SkillContext(obs=obs, persona=Persona(name="Sten"), memory=memory, goal_id=goal_id)


# --- config: the single source of truth the leaf-func factories read ---------


def test_carpenter_graphics_and_buttons_match_servuo_and_live_calibration():
    # ServUO: Saw base(0x1034) [Flipable(0x1034,0x1035)]; Throne base(0xB33); Board 0x1BD7.
    assert SAW_GRAPHIC == 0x1034                      # base craft/vendor art
    assert SAW_GRAPHICS == frozenset({0x1034, 0x1035})  # flip-safe identity set
    assert THRONE_GRAPHIC == 0x0B33
    assert BOARD_GRAPHIC == 0x1BD7
    assert THRONE_BOARDS_PER == 19
    assert THRONE_NAME_CLILOC == 1044305  # "magincia-style throne"
    # Live-calibrated CraftGump buttons (`1 + type + index*7`): Furniture == 8,
    # throne == 58; barrel staves' Other == 1, item == 2.
    assert (THRONE_CATEGORY_BTN, THRONE_ITEM_BTN) == (8, 58)
    assert (BARRELSTAVE_CATEGORY_BTN, BARRELSTAVE_ITEM_BTN) == (1, 2)


def test_craft_carpentry_config_has_no_material_submenu():
    assert CarpenterCraft.craft_tool_graphics == SAW_GRAPHICS
    assert CarpenterCraft.craft_category_btn == THRONE_CATEGORY_BTN
    assert CarpenterCraft.craft_item_btn == THRONE_ITEM_BTN
    assert CarpenterCraft.craft_material_graphics == BOARD_GRAPHICS
    assert CarpenterCraft.craft_material_per_item == THRONE_BOARDS_PER
    assert CarpenterCraft.craft_output_graphic == THRONE_GRAPHIC
    assert CarpenterCraft.craft_item_name_cliloc == THRONE_NAME_CLILOC
    assert CarpenterCraft.craft_batch == 1  # one big item, not a fill-to-5 batch
    # The new path: carpentry has NO material submenu (both None skip the stages).
    assert CarpenterCraft.craft_resource_menu_btn is None
    assert CarpenterCraft.craft_material_resource_btn is None


def test_sell_furniture_config_targets_the_throne_at_the_carpenter():
    assert SellFurniture.sold_graphic == THRONE_GRAPHIC
    assert SellFurniture.sell_threshold == 1  # one finished piece is worth a trip
    assert SellFurniture.vendor_spot_key == "vendor_spot"  # the one Carpenter NPC


def test_buy_boards_config_targets_boards_at_the_carpenter():
    assert BuyBoards.buy_material_graphics == BOARD_GRAPHICS
    assert BuyBoards.buy_offer_graphic == BOARD_GRAPHIC
    assert BuyBoards.buy_amount == 38            # two thrones' worth (a live tunable)
    assert BuyBoards.buy_reorder == THRONE_BOARDS_PER  # below one throne's boards
    assert BuyBoards.buy_price_estimate == 3     # SBCarpenter Board @3g
    assert BuyBoards.vendor_spot_key == "vendor_spot"


def test_buy_saw_config_targets_the_saw_at_the_carpenter():
    assert BuySaw.owned_tool_graphics == SAW_GRAPHICS  # single-tool set, not 8 axes
    assert BuySaw.offer_graphic == SAW_GRAPHIC
    assert BuySaw.tool_price_estimate == 15      # SBCarpenter Saw @15g
    assert BuySaw.vendor_spot_key == "vendor_spot"


# --- craft_carpentry: the gump FSM with the resource submenu SKIPPED ---------

# The carpentry gump carries ITS OWN title cliloc (1044004), not blacksmithy's
# 1044002 — the mock must match reality or it re-encodes the very bug that stalled
# the live loop (the FSM keys `_craft_gump` on this recipe's own title).
_CRAFT_LAYOUT = (
    f"{{ xmfhtmlgumpcolor 0 0 0 0 {CARPENTRY_TITLE_CLILOC} 0 0 0 }}"
    f"{{ xmfhtmlgumpcolor 0 0 0 0 {THRONE_NAME_CLILOC} 0 0 0 }}"
)


def _craft_gump(serial=0xAB):
    return GumpView(
        serial=serial,
        gump_id=0xCD,
        layout=_CRAFT_LAYOUT,
        elements=[
            {"type": "button", "pageflag": 1, "reply_id": THRONE_CATEGORY_BTN},
            {"type": "button", "pageflag": 1, "reply_id": THRONE_ITEM_BTN},
        ],
    )


def _cctx(items, *, memory, goal_id=17, gumps=()):
    return _mctx([_saw(), *items], memory=memory, goal_id=goal_id, gumps=list(gumps))


def test_craft_carpentry_skips_the_resource_menu_and_builds_one_throne():
    skill = CarpenterCraft()
    mem: dict = {}
    actions = []

    # Tick 1: begin (freeze 19 boards, needed=1) + open the gump with the saw.
    open_res = skill.step(_cctx([_backpack(), _boards(0x700, 19)], memory=mem))
    actions.append(open_res.action)
    assert mem["cap_craft_needed"] == 1

    # Ticks 2-3: category (button 8) then item (button 58) — NO resource submenu.
    for _ in range(2):
        res = skill.step(_cctx([_backpack(), _boards(0x700, 19)], memory=mem,
                               gumps=[_craft_gump()]))
        actions.append(res.action)

    # Tick 4: the craft landed — one throne arrived, 19 boards consumed. The
    # result gump has a DIFFERENT serial (the reshown craft gump) -> confirm+close.
    after = [_backpack(), _throne()]
    close = skill.step(_cctx(after, memory=mem, gumps=[_craft_gump(serial=0xAC)]))
    actions.append(close.action)

    # Tick 5: gump gone -> finished.
    finished = skill.step(_cctx(after, memory=mem))

    assert isinstance(actions[0], Use) and actions[0].serial == SAW_SERIAL
    buttons = [a.button for a in actions[1:] if isinstance(a, GumpResponse)]
    assert buttons == [THRONE_CATEGORY_BTN, THRONE_ITEM_BTN, 0]
    # The genuinely new path: the resource-menu buttons NEVER appear.
    assert RESOURCE_MENU_BTN not in buttons
    assert IRON_RESOURCE_BTN not in buttons
    assert finished.action is None
    assert mem["cap_craft_confirmed"] == 1
    assert mem["cap_craft_ingots_used"] == THRONE_BOARDS_PER  # 19 boards
    assert sum(amount for _serial, amount in mem["cap_craft_produced"]) == 1
    assert mem["cap_craft_finished_goal_id"] == 17


def test_craft_carpentry_ignores_a_blacksmith_titled_gump():
    """Regression for the live stall: the carpenter's saw opens a gump titled
    1044004; a gump carrying blacksmithy's 1044002 (a sibling profession's, or
    the pre-fix hardcoded constant) must be INVISIBLE to `_craft_gump` so the FSM
    never mis-drives it — it waits, emitting no category button, not stalls-as-
    success. Before the `craft_title_cliloc` fix this test could not exist: the
    FSM keyed every craft on 1044002, so a blacksmith-titled gump WOULD have been
    navigated (and the real 1044004 carpentry gump was silently ignored)."""
    from anima2.skills.craft import CRAFT_TITLE_CLILOC as SMITH_TITLE

    smith_layout = (
        f"{{ xmfhtmlgumpcolor 0 0 0 0 {SMITH_TITLE} 0 0 0 }}"
        f"{{ xmfhtmlgumpcolor 0 0 0 0 {THRONE_NAME_CLILOC} 0 0 0 }}"
    )
    smith_gump = GumpView(
        serial=0xAB, gump_id=0xCD, layout=smith_layout,
        elements=[
            {"type": "button", "pageflag": 1, "reply_id": THRONE_CATEGORY_BTN},
            {"type": "button", "pageflag": 1, "reply_id": THRONE_ITEM_BTN},
        ],
    )
    skill = CarpenterCraft()
    mem: dict = {}
    # Tick 1: begin + Use(saw).
    skill.step(_cctx([_backpack(), _boards(0x700, 19)], memory=mem))
    # Ticks 2-5: the ONLY gump on screen is blacksmith-titled. The FSM must keep
    # waiting at "category" — never emit the category button against it.
    for _ in range(4):
        res = skill.step(_cctx([_backpack(), _boards(0x700, 19)], memory=mem,
                               gumps=[smith_gump]))
        assert not isinstance(res.action, GumpResponse), (
            "carpenter drove a blacksmith-titled gump — title isolation broken"
        )
    assert mem["cap_craft_stage"] == "category"  # still waiting, not advanced/closed


# --- craft_carpentry readiness: batch=1 over the throne's 19-board threshold --


def _ready_ctx(items, *, memory):
    obs = Observation(player=PlayerView(serial=1, pos=Position(5, 5, 0)), items=list(items))
    return SkillContext(obs=obs, persona=Persona(name="Sten"), memory=memory, goal_id=None)


def test_craft_carpentry_ready_needs_a_full_throne_of_boards_at_the_stand():
    ready = CAPABILITIES[("carpenter", "craft_carpentry")].ready
    mem = {"craft_spot": (5, 5)}
    # 19 boards + saw at the stand: ready.
    assert ready(_ready_ctx([_backpack(), _saw(), _boards(0x700, 19)], memory=mem)) is True
    # 18 boards: one short of a throne -> not ready.
    assert ready(_ready_ctx([_backpack(), _saw(), _boards(0x700, 18)], memory=mem)) is False
    # A throne already in the pack (made == batch) -> not ready (nothing to add).
    assert ready(_ready_ctx([_backpack(), _saw(), _boards(0x700, 40), _throne()], memory=mem)) is False
    # Off the configured stand tile -> not ready.
    assert ready(_ready_ctx([_backpack(), _saw(), _boards(0x700, 19)],
                            memory={"craft_spot": (9, 9)})) is False


# --- sell_furniture: offers the throne (not distractors) via sold_graphic -----


def test_sell_furniture_offers_only_the_throne_from_the_shop_sell_window():
    vendor = _mobile(VENDOR_MOBILE, *VENDOR)
    sell = ShopSell(vendor=VENDOR_SERIAL, items=[
        ShopSellItem(serial=THRONE_SERIAL, graphic=THRONE_GRAPHIC, hue=0, amount=1,
                     price=24, name="throne"),
        ShopSellItem(serial=0x41, graphic=BOARD_GRAPHIC, hue=0, amount=5, price=2, name="board"),
    ])
    mem = {"vendor_spot": VENDOR, "bs_stand": (0, 0)}
    before = [_backpack(), _throne()]
    skill = SellFurniture()

    skill.step(_mctx(before, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor], goal_id=17))
    popup = _popup(VENDOR_MOBILE, [SELL_CLILOC])
    skill.step(_mctx(before, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor],
                     popup=popup, goal_id=17))
    offer = skill.step(_mctx(before, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor],
                             shop_sell=sell, goal_id=17))

    assert isinstance(offer.action, SellItems)
    assert offer.action.items == [(THRONE_SERIAL, 1)]  # the throne only, never the board
    assert mem["cap_sell_sent_goal_id"] == 17
    assert mem["cap_sell_expected_gold"] == 24
    assert mem["cap_sell_offered_items"] == ((THRONE_SERIAL, 1, 24),)


# --- buy_boards: resolves boards by graphic and clamps to buy_amount ----------


def _board_buy_window(board_stock=100):
    # The Carpenter's BUY window: boards + tool distractors also on 0x1BD7-free
    # graphics. buy_boards must resolve ONLY the single board offer by graphic.
    return ShopBuy(vendor=VENDOR_SERIAL, container=BOARD_CONTAINER, entries=[
        ShopBuyEntry(price=3, name="nails", serial=0xDD00, graphic=0x102E, amount=20),
        ShopBuyEntry(price=3, name="board", serial=BOARD_OFFER_SERIAL,
                     graphic=BOARD_GRAPHIC, amount=board_stock),
        ShopBuyEntry(price=15, name="saw", serial=SAW_OFFER_SERIAL, graphic=SAW_GRAPHIC, amount=20),
    ])


def test_buy_boards_orders_the_board_offer_by_graphic_clamped_to_buy_amount():
    vendor = _mobile(VENDOR_MOBILE, *VENDOR)
    mem = {"vendor_spot": VENDOR, "bs_stand": (0, 0)}
    skill = BuyBoards()
    before = [_backpack(), _gold(0x900, amount=200)]  # gold, 0 boards

    request = skill.step(_mctx(before, memory=mem, pos=Position(*VENDOR, 0),
                               mobiles=[vendor], goal_id=17))
    popup = _popup(VENDOR_MOBILE, [BUY_CLILOC, SELL_CLILOC])
    select = skill.step(_mctx(before, memory=mem, pos=Position(*VENDOR, 0),
                              mobiles=[vendor], popup=popup, goal_id=17))
    order = skill.step(_mctx(before, memory=mem, pos=Position(*VENDOR, 0),
                             mobiles=[vendor], shop_buy=_board_buy_window(), goal_id=17))

    assert isinstance(request.action, PopupRequest)
    assert isinstance(select.action, PopupSelect) and select.action.index == 0  # Buy
    assert isinstance(order.action, BuyItems)
    assert order.action.vendor == VENDOR_SERIAL
    # 38 boards (buy_amount, under the 100-stock), resolved by graphic among nails
    # + saw distractors — never the saw or the nails.
    assert order.action.items == [(BOARD_OFFER_SERIAL, 38)]
    assert mem["cap_buy_bought_ingots"] == 38
    assert mem["cap_buy_expected_cost"] == 38 * 3
    assert mem["cap_buy_offer"] == (BOARD_OFFER_SERIAL, 38, 3)


def test_buy_boards_clamps_the_order_to_the_vendors_live_stock():
    vendor = _mobile(VENDOR_MOBILE, *VENDOR)
    mem = {"vendor_spot": VENDOR, "bs_stand": (0, 0)}
    skill = BuyBoards()
    before = [_backpack(), _gold(0x900, amount=200)]
    skill.step(_mctx(before, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor], goal_id=17))
    popup = _popup(VENDOR_MOBILE, [BUY_CLILOC, SELL_CLILOC])
    skill.step(_mctx(before, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor],
                     popup=popup, goal_id=17))
    # Only 10 boards in stock -> order 10, never the full 38.
    order = skill.step(_mctx(before, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor],
                             shop_buy=_board_buy_window(board_stock=10), goal_id=17))
    assert isinstance(order.action, BuyItems)
    assert order.action.items == [(BOARD_OFFER_SERIAL, 10)]
    assert mem["cap_buy_offer"] == (BOARD_OFFER_SERIAL, 10, 3)


# --- buy_saw: resolves the saw by scalar offer_graphic, reads vendor_spot -----


def _tool_buy_window():
    return ShopBuy(vendor=VENDOR_SERIAL, container=BOARD_CONTAINER, entries=[
        ShopBuyEntry(price=12, name="dovetail saw", serial=0xDD28, graphic=0x1028, amount=20),
        ShopBuyEntry(price=15, name="saw", serial=SAW_OFFER_SERIAL, graphic=SAW_GRAPHIC, amount=20),
        ShopBuyEntry(price=3, name="board", serial=BOARD_OFFER_SERIAL, graphic=BOARD_GRAPHIC, amount=50),
    ])


def test_buy_saw_reads_the_carpenter_vendor_spot_route():
    # A single Carpenter NPC sells the saw too, so buy_saw reads `vendor_spot`
    # (NOT a separate tool vendor like the lumberjack's `tool_vendor_spot`).
    skill = BuySaw()
    items = [_backpack(), _gold(0x900, amount=100)]  # gold, no saw
    mem = {"vendor_spot": VENDOR}
    skill.step(_mctx(items, memory=mem, pos=Position(0, 0, 0), goal_id=17))
    assert mem["cap_toolbuy_route"] == (VENDOR,)


def test_buy_saw_buys_the_saw_offer_by_graphic_among_tool_distractors():
    vendor = _mobile(VENDOR_MOBILE, *VENDOR)
    mem = {
        "vendor_spot": VENDOR, "bs_stand": (0, 0), "mkt_phase": "toolbuy",
        "toolbuy_stage": "window", "toolbuy_vendor": VENDOR_MOBILE,
        "cap_toolbuy_goal_id": 17, "cap_toolbuy_route": (VENDOR,),
        "cap_toolbuy_start_tools": 0, "cap_toolbuy_start_gold": 100,
    }
    items = [_backpack(), _gold(0x900, amount=100)]  # gold, no saw
    res = BuySaw().step(_mctx(items, memory=mem, pos=Position(*VENDOR, 0), mobiles=[vendor],
                              shop_buy=_tool_buy_window(), goal_id=17))
    assert isinstance(res.action, BuyItems)
    # Exactly the 0x1034 saw, by the scalar offer_graphic — never the dovetail saw.
    assert res.action.items == [(SAW_OFFER_SERIAL, 1)]
    assert mem["cap_toolbuy_offer"] == (SAW_OFFER_SERIAL, 1, 15)


# --- bank_gold: the profession-agnostic gold leaf funcs, reused verbatim ------


def test_carpenter_bank_gold_reuses_the_shared_binding_and_skill():
    smith = CAPABILITIES[("blacksmith", "bank_gold")]
    carp = CAPABILITIES[("carpenter", "bank_gold")]
    assert carp.skill_type is BankGold
    # Gold is profession-agnostic: the exact same leaf funcs the smith uses.
    assert carp.ready is smith.ready
    assert carp.achieved is smith.achieved
    assert carp.progress is smith.progress
    assert carp.can_yield is smith.can_yield


def test_carpenter_skills_are_distinct_from_the_smith_configs():
    # Siblings (not smith subclasses) so `isinstance` disjointness holds for any
    # `_contains_*` test helper the registry suite relies on.
    assert not issubclass(BuyBoards, BuyTool)
    assert not issubclass(SellFurniture, BankGold)
