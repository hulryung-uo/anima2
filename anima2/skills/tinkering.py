"""Tinkering — the tinker profession: forge iron into small metal goods, sell
them, bank the gold, and self-provision iron + a replacement tinker's tool.

The fourth profession built on the generalized craft/market machinery (Bricks
7-10 of `docs/LUMBER-CARPENTER-TINKER.md`). Like the carpenter it drives the
CraftGump FSM's **no-material-submenu** path: `Use(TinkerTools)` opens the gump
and the category is pressed directly. Carpentry skips the submenu because wood
has no metal type; the tinker skips it because a fresh tinker carries only iron,
so the gump's remembered resource is already iron — LIVE-CONFIRMED on this shard
(a Tools→scissors click consumed 2 iron and produced scissors with no submenu),
so `craft_resource_menu_btn`/`craft_material_resource_btn` are both `None`.

Every skill below is a thin **config** subclass of an already-verified,
live-proven base: `TinkerTongs` of `craft.py::CraftItemCapability` (the gump MAKE
FSM), `SellTongs`/`BuyIron`/`BuyTinkerTool` of `market.py`'s `SellItemCapability`
/`BuyMaterialCapability`/`BuyToolCapability`. Only the `craft_*`/`sold_*`/`buy_*`
config attrs differ; the provenance-checked machinery is inherited unchanged, so
the blacksmith/carpenter stay byte-identical.

Like the carpenter, the tinker uses ONE `vendor_spot` Tinker NPC for everything:
SBTinker both BUYS the finished goods (Tongs @7g, Lockpick @6g, ...) AND SELLS
the raw IronIngot @5g and a replacement TinkerTools @7g — so sell_tongs, buy_iron,
and buy_tinker_tool all read the default `vendor_spot` key.

Economics (live ServUO T2A, `SBTinker.cs`, and the docs' research brick): buying
iron @5g, only **Tongs (+2g)** and Lockpick (+1g) clear a profit at all — every
heavier item loses on bought iron. **Tongs is the most iron-efficient sell item
(1 iron → 7g, 35 skill)**, so it is the tinker's profit good; on FREE (mined)
iron most items turn positive, which is why the robust tinker is paired with an
iron supply (a later brick), exactly as the carpenter is paired with the
lumberjack's boards.

Live-calibration source (against this ServUO, `scratchpad/gump_probe.py`): a
`Use(TinkerTools 0x1EB8)` → the CraftGump (title cliloc **1044007**) → category
"Tools" (button **15**) → item "tongs" (button **86**, cliloc 1024028) consumes
**1 IronIngot** and yields Tongs (ServUO `Scripts/Items/Tools/SmithSmithy.cs`:
`Tongs : base(0xFBB)`). Scissors (Tools button **2**, cliloc 1023998, 2 iron,
0xF9F) is the lowest-skill smoke recipe.
"""

from __future__ import annotations

from .craft import CraftItemCapability
from .market import BuyMaterialCapability, BuyToolCapability, SellItemCapability

# --- Tinkering tool + item graphics (ServUO-confirmed) -----------------------
# TinkerTools (ServUO `Scripts/Items/Tools/Tools.cs`: `TinkerTools : base(0x1EB8)`,
# `[Flipable(0x1EB8, 0x1EBC)]`) — the tinker craft tool AND the tool the Tinker
# vendor sells; `Use(TinkerTools)` opens the CraftGump from anywhere (no forge).
# Both flip orientations count as "a tinker tool in the pack".
TINKERTOOLS_GRAPHIC = 0x1EB8
TINKERTOOLS_GRAPHICS = frozenset({0x1EB8, 0x1EBC})
# The exact art the Tinker vendor stocks for sale (SBTinker.cs:54
# `GenericBuyInfo(typeof(TinkersTools), 7, 20, 0x1EBC, 0)`) — the flip orientation
# 0x1EBC, NOT the 0x1EB8 a `Use` stages. buy_tinker_tool resolves the offer by
# this graphic. (Deferred/unverified-live: the loop stages a durable TinkerTools
# 999 so the tool never breaks; buy_tinker_tool is only the break fallback.)
TINKERTOOLS_FORSALE_GRAPHIC = 0x1EBC
# The tinkering CraftGump's title cliloc (ServUO DefTinkering `GumpTitleNumber`
# == 1044007 "<CENTER>TINKERING MENU</CENTER>"), distinct from blacksmithy's
# 1044002 and carpentry's 1044004 — the FSM keys `_craft_gump` on THIS so the
# tinker tool's gump is recognized (see craft.py::Blacksmith.craft_title_cliloc).
TINKERING_TITLE_CLILOC = 1044007

# CraftGump button ids (ServUO CraftGump `1 + type + index*7`), from the LIVE
# calibration of this shard's TinkerTools gump (scratchpad/gump_probe.py) — NOT
# re-derived from DefTinkering.cs source (its AddCraft order shifts with expansion
# flags, the same footgun the dagger button hit in Phase 3). Category buttons =
# `1 + index*7`: Jewelry=1, Wooden Items=8, Tools=15, Parts=22, Utensils=29, ...
TOOLS_CATEGORY_BTN = 15  # Tools category, index 2 (live-calibrated)

# Tongs (the profit good) — Tools item index 12 → button `2 + 12*7` == 86.
TONGS_ITEM_BTN = 86
# ServUO `Tongs : base(0xFBB)` — the pack/output graphic the craft FSM confirms
# and the sell phase matches. (0xFBB is also a smith craft-tool art, but a tinker
# never carries smith tools, so counting it in the tinker's pack is unambiguous.)
TONGS_GRAPHIC = 0x0FBB
# Cliloc 1024028 = "tongs" — the item-page name safety-check, live-decoded.
TONGS_NAME_CLILOC = 1024028
# A Tongs costs 1 IronIngot (ServUO DefTinkering AddCraft amount, calibrated).
TONGS_IRON_PER = 1

# Scissors (the lowest-skill smoke recipe) — Tools item index 0 → button 2.
SCISSORS_ITEM_BTN = 2
SCISSORS_GRAPHIC = 0x0F9F      # ServUO `Scissors : base(0xF9F)`
SCISSORS_NAME_CLILOC = 1023998  # "scissors"
SCISSORS_IRON_PER = 2


class TinkerTongs(CraftItemCapability):
    """Tinker config: forge one batch of Tongs (1 iron each, 0xFBB) from pack iron
    with the TinkerTools. Overrides `CraftItemCapability`'s smith defaults with the
    tinkering recipe: a TinkerTools tool, the tinkering gump title, the Tools/Tongs
    buttons, iron at 1/item (not 3), the Tongs output, and — like carpentry — NO
    material submenu (both resource buttons `None`). Iron material (`INGOT_GRAPHICS`)
    and the 5-item batch are the inherited smith defaults, unchanged. The whole
    gump FSM + goal-scoped provenance is inherited (`cap_craft_*` keys keep their
    legacy names).
    """

    name = "craft_tongs"
    description = "Forge a batch of observation-confirmed tongs from pack iron with tinker's tools."
    #: TinkerTools opens the tinkering gump (no forge/anvil needed).
    craft_tool_graphics = TINKERTOOLS_GRAPHICS
    #: The tinkering gump's own title cliloc (NOT blacksmithy's 1044002 nor
    #: carpentry's 1044004) — without this the tool's gump opens invisibly to the
    #: FSM and it stalls forever (the bug that stalled the carpenter live).
    craft_title_cliloc = TINKERING_TITLE_CLILOC
    #: Tools category -> tongs item (live-calibrated button ids).
    craft_category_btn = TOOLS_CATEGORY_BTN
    craft_item_btn = TONGS_ITEM_BTN
    #: Tinkering iron items need NO material submenu on a fresh (iron-only) tinker
    #: — both `None` skips the resource stages (live-confirmed direct craft).
    craft_resource_menu_btn = None
    craft_material_resource_btn = None
    #: Consume iron (1 per tongs); confirm the Tongs' arrival. `craft_material_graphics`
    #: stays the inherited iron `INGOT_GRAPHICS`.
    craft_material_per_item = TONGS_IRON_PER
    craft_output_graphic = TONGS_GRAPHIC
    craft_item_name_cliloc = TONGS_NAME_CLILOC
    # craft_batch = 5 (inherited): a sale-sized batch of tongs (5 iron -> 5 tongs).


class TinkerScissors(TinkerTongs):
    """The lowest-skill (5.0) tinkering smoke recipe: Tools -> scissors (2 iron,
    0xF9F). Same TinkerTools + no-submenu path as `TinkerTongs`; only the item
    button, per-item iron cost, name cliloc, and output graphic differ. Not a
    registered capability (a mechanics smoke, like carpentry's `BarrelStavesCraft`)
    — scissors sell for less than 2 bought iron, so tongs is the profit good.
    """

    name = "craft_scissors"
    description = "Forge one set of scissors from pack iron with tinker's tools (smoke)."
    craft_item_btn = SCISSORS_ITEM_BTN
    craft_material_per_item = SCISSORS_IRON_PER
    craft_output_graphic = SCISSORS_GRAPHIC
    craft_item_name_cliloc = SCISSORS_NAME_CLILOC


class SellTongs(SellItemCapability):
    """Tinker config: sell finished tongs (0xFBB) to the `vendor_spot` Tinker NPC
    (SBTinker buys tongs @7g). Iron -> tongs -> gold is the tinker's income. Only
    `sold_graphic` differs from `SellDaggers` (both keep the 5-item batch
    threshold); the provenance machinery is `SellItemCapability`'s.
    """

    name = "sell_tongs"
    description = "Sell observed backpack tongs to the configured tinker vendor and return."
    #: Tongs — a single art id (non-stacking tools).
    sold_graphic = TONGS_GRAPHIC
    # sell_threshold = 5 (inherited): sell a full craft batch per trip.
    # vendor_spot_key = "vendor_spot" (inherited): the tinker's ONE Tinker NPC both
    # buys tongs AND sells iron + tinker's tools — no separate vendor.


class BuyIron(BuyMaterialCapability):
    """Tinker config: buy a batch of iron ingots (0x1BF2) from the `vendor_spot`
    Tinker NPC when metal runs low. Inherits every iron default from
    `BuyMaterialCapability` (`buy_material_graphics=INGOT_GRAPHICS`,
    `buy_offer_graphic=IRON_INGOT_GRAPHIC`, `buy_amount=BUY_AMOUNT`,
    `vendor_spot_key="vendor_spot"`) — SBTinker sells IronIngot @5g. Closes the
    tinker's supply loop (craft -> sell -> buy iron) the way `buy_ingots` closes
    the smith's; only the reorder line + price estimate differ.
    """

    name = "buy_iron"
    description = "Buy a batch of iron ingots from the configured tinker vendor and return."
    #: Reorder below one craft batch's worth of iron (5 tongs x 1 iron = 5) — at/
    #: above 5 a full batch can still be forged, below it the next craft starves.
    buy_reorder = 5
    #: This shard's live iron price (SBTinker sells IronIngot @5g) — the readiness
    #: affordability estimate only; the buy reads the live entry price.
    buy_price_estimate = 5


class BuyTinkerTool(BuyToolCapability):
    """Tinker config: buy a replacement TinkerTools (0x1EB8) from the `vendor_spot`
    Tinker NPC when none is in the pack. Only the tool config differs from
    `BuyTool`; the buy machinery is `BuyToolCapability`'s, unchanged.
    """

    name = "buy_tinker_tool"
    description = "Buy one replacement tinker's tool from the configured tinker vendor and return."
    #: The trigger is "no tinker tool in the pack" — either flip orientation.
    owned_tool_graphics = TINKERTOOLS_GRAPHICS
    #: The exact for-sale TinkerTools art the Tinker stocks (SBTinker sells
    #: TinkersTools @7g with itemID 0x1EBC) — resolved off the enriched offer by
    #: this graphic (the for-sale flip, not the 0x1EB8 a Use stages).
    offer_graphic = TINKERTOOLS_FORSALE_GRAPHIC
    #: This shard's live TinkersTools price (7g); the readiness affordability
    #: estimate only — the buy reads the live entry price.
    tool_price_estimate = 7
    # vendor_spot_key = "vendor_spot" (inherited): the Tinker sells tools too.
