"""Carpentry — the carpenter profession: saw boards into furniture, sell it,
bank the gold, and self-provision boards + a replacement saw.

The third profession built on the generalized craft/market machinery (Bricks 4-5
of `docs/LUMBER-CARPENTER-TINKER.md`), and the first to exercise the CraftGump
FSM's **no-material-submenu** path: a blacksmith resets the remembered metal to
iron before each craft (RESOURCE_MENU then IRON), but carpentry has no material
submenu — a `Saw` `Use` opens the gump and the category is pressed directly, so
`craft_resource_menu_btn`/`craft_material_resource_btn` are both `None` here
(`craft.py::CraftItemCapability.step` reads that and skips straight to category).

Every skill below is a thin **config** subclass of an already-verified,
live-proven base: `CarpenterCraft` of `craft.py::CraftItemCapability` (the gump
MAKE FSM), `SellFurniture`/`BuyBoards`/`BuySaw` of `market.py`'s
`SellItemCapability`/`BuyMaterialCapability`/`BuyToolCapability`. Only the
`craft_*`/`sold_*`/`buy_*` config attrs differ; the provenance-checked machinery
is inherited unchanged, so the blacksmith stays byte-identical.

Unlike the lumberjack (two vendors — a Carpenter to sell to, a WeaponSmith for
hatchets), the carpenter uses ONE `vendor_spot` Carpenter NPC for everything:
SBCarpenter both buys furniture AND sells boards + saws, so sell_furniture,
buy_boards, and buy_saw all read the default `vendor_spot` key.

Live-calibration source (team lead, against this ServUO): a `Saw` `Use` → the
CraftGump → category "Furniture" (button 8) → item "magincia-style throne"
(button 58, cliloc 1044305) consumes 19 Boards and yields a Throne (ServUO
`Scripts/Items/Decorative/Thrones.cs`: `Throne : base(0xB33)`). The Throne needs
73.6 Carpentry min. `BarrelStaves` (`Scripts/Items/Resource/BarrelParts.cs`:
`base(0x1EB1)`) is the low-skill alt/smoke recipe.
"""

from __future__ import annotations

from .craft import CraftItemCapability
from .market import BuyMaterialCapability, BuyToolCapability, SellItemCapability
from .woodwork import BOARD_GRAPHIC, BOARD_GRAPHICS

# --- Carpentry tool + item graphics (ServUO-confirmed) -----------------------
# The Saw (ServUO `Scripts/Items/Tools/Tools.cs`: `Saw : base(0x1034)`) — the
# carpentry craft tool AND the tool the Carpenter vendor sells; `Use(saw)` opens
# the CraftGump from anywhere (no forge/anvil, unlike smithing).
SAW_GRAPHIC = 0x1034
SAW_GRAPHICS = frozenset({SAW_GRAPHIC})
# The Throne's pack graphic (ServUO Thrones.cs `Throne : base(0xB33)`,
# `[Flipable(0xB32, 0xB33)]` — base 0xB33 in a container) — what the sell phase
# matches pack/SellList items against, and the craft output the FSM confirms.
THRONE_GRAPHIC = 0x0B33
# Cliloc 1044305 = "magincia-style throne" — the item-page name safety-check
# (the throne's own button among the Furniture list), live-decoded from the gump.
THRONE_NAME_CLILOC = 1044305
# A Throne costs 19 Boards (live-calibrated; ServUO DefCarpentry AddCraft amount).
THRONE_BOARDS_PER = 19
# The carpentry CraftGump's title cliloc (ServUO DefCarpentry `GumpTitleNumber`
# == 1044004 "<CENTER>CARPENTRY MENU</CENTER>"), distinct from blacksmithy's
# 1044002 — the FSM keys `_craft_gump` on THIS so the saw's gump is recognized.
CARPENTRY_TITLE_CLILOC = 1044004

# CraftGump button ids (ServUO CraftGump `1 + type + index*7`), taken from the
# team lead's LIVE calibration of this shard's Saw gump — NOT re-derived from
# DefCarpentry.cs source (its AddCraft order shifts with expansion flags, the
# same footgun the dagger button hit in Phase 3). Furniture is `_button(0, 1)`
# == 8; the throne is `_button(1, 8)` == 58.
THRONE_CATEGORY_BTN = 8   # Furniture category (live-calibrated)
THRONE_ITEM_BTN = 58      # magincia-style throne (live-calibrated, cliloc 1044305)


class CarpenterCraft(CraftItemCapability):
    """Carpenter config: saw one Throne (19 boards) from pack boards with a Saw.

    Overrides `CraftItemCapability`'s smith defaults with the carpentry recipe:
    a Saw tool, the Furniture/throne buttons, boards (not ingots) at 19/item, the
    Throne output, batch 1 (one big item, not a 5-dagger fill batch), and — the
    new path — NO material submenu (both resource buttons `None`, so the FSM goes
    open -> category directly). The whole gump FSM + goal-scoped provenance is
    inherited unchanged (`cap_craft_*` memory keys keep their legacy names).
    """

    name = "craft_carpentry"
    description = "Craft one observation-confirmed throne from pack boards with a saw."
    #: The Saw opens the carpentry gump (no forge/anvil needed).
    craft_tool_graphics = SAW_GRAPHICS
    #: Furniture category -> throne item (live-calibrated button ids).
    craft_category_btn = THRONE_CATEGORY_BTN
    craft_item_btn = THRONE_ITEM_BTN
    #: The carpentry gump's own title cliloc (NOT blacksmithy's 1044002) — without
    #: this the saw's gump opens invisibly to the FSM and it stalls forever.
    craft_title_cliloc = CARPENTRY_TITLE_CLILOC
    #: Carpentry has NO material submenu — both `None` skips the resource stages.
    craft_resource_menu_btn = None
    craft_material_resource_btn = None
    #: Consume boards (19 per throne); confirm the Throne's arrival.
    craft_material_graphics = BOARD_GRAPHICS
    craft_material_per_item = THRONE_BOARDS_PER
    craft_output_graphic = THRONE_GRAPHIC
    craft_item_name_cliloc = THRONE_NAME_CLILOC
    #: One Throne per goal (a single large item, not a fill-to-5 sale batch).
    craft_batch = 1


# --- BarrelStaves: the low-skill alt/smoke carpentry recipe -------------------
# For a quick live gump-navigation shakeout that avoids the Throne's 73.6
# Carpentry floor. Live-calibrated: category "Other" (`_button(0, 0)` == 1) ->
# item (`_button(1, 0)` == 2), cliloc 1027857, 5 Boards each.
#
# The in-pack OUTPUT graphic: the team lead's live calibration left it as "None"
# (uncalibrated) — ServUO `BarrelParts.cs` has `BarrelStaves : base(0x1EB1)`
# `[FlipableAttribute(0x1EB1..0x1EB4)]`, so a freshly-crafted stack could show
# any of four flip variants. `BARRELSTAVE_GRAPHIC` is set to the ServUO base for
# OFFLINE testability (the mock controls the graphic); a LIVE smoke should trust
# board CONSUMPTION, not staff arrival, and confirm the base id before relying on
# `_observe_evidence`'s success delta. `BarrelStavesCraft` is intentionally NOT a
# registered capability — it's a navigation smoke, not carpenter income.
BARRELSTAVE_GRAPHIC = 0x1EB1
BARRELSTAVE_NAME_CLILOC = 1027857
BARRELSTAVE_BOARDS_PER = 5
BARRELSTAVE_CATEGORY_BTN = 1   # "Other" category (live-calibrated)
BARRELSTAVE_ITEM_BTN = 2       # barrel staves item (live-calibrated, cliloc 1027857)


class BarrelStavesCraft(CarpenterCraft):
    """Alt/smoke config: saw barrel staves (5 boards) — the low-Carpentry-skill
    recipe for a quick live gump-navigation shakeout below the Throne's 73.6
    floor. Same Saw tool + no-material-submenu path as `CarpenterCraft`; only the
    category/item buttons, per-item board cost, name cliloc, and output graphic
    differ. See the module note on `BARRELSTAVE_GRAPHIC`'s live-unconfirmed
    caveat — not wired as a capability.
    """

    name = "craft_barrel_staves"
    description = "Craft one set of barrel staves from pack boards with a saw (smoke)."
    craft_category_btn = BARRELSTAVE_CATEGORY_BTN
    craft_item_btn = BARRELSTAVE_ITEM_BTN
    craft_material_per_item = BARRELSTAVE_BOARDS_PER
    craft_output_graphic = BARRELSTAVE_GRAPHIC
    craft_item_name_cliloc = BARRELSTAVE_NAME_CLILOC


class SellFurniture(SellItemCapability):
    """Carpenter config: sell finished furniture (the Throne, 0x0B33) to the
    `vendor_spot` Carpenter NPC (SBCarpenter buys furniture). Boards -> throne ->
    gold is the carpenter's income. Only `sold_graphic`/`sell_threshold` differ
    from `SellDaggers`; the provenance machinery is `SellItemCapability`'s.
    """

    name = "sell_furniture"
    description = "Sell observed backpack furniture to the configured carpenter vendor and return."
    #: The Throne — a single art id, not a stack set (furniture is non-stackable).
    sold_graphic = THRONE_GRAPHIC
    #: One finished piece is worth a sale trip (a throne is 19 boards of work).
    sell_threshold = 1
    # vendor_spot_key = "vendor_spot" (inherited): the carpenter's ONE Carpenter
    # NPC both buys furniture and sells boards + saws — no separate tool vendor.


class BuyBoards(BuyMaterialCapability):
    """Carpenter config: buy boards (0x1BD7) from the `vendor_spot` Carpenter NPC
    when pack boards fall below one throne's worth. Closes the carpenter's supply
    loop (craft -> sell -> buy boards) the way `buy_ingots` closes the smith's.
    Only the material/offer/amount/price config differs from `BuyIngots`; the buy
    machinery is `BuyMaterialCapability`'s, unchanged.
    """

    name = "buy_boards"
    description = "Buy a batch of boards from the configured carpenter vendor and return."
    #: Boards counted in the pack (single art id, kept a set for parity).
    buy_material_graphics = BOARD_GRAPHICS
    #: The exact for-sale Board art the Carpenter stocks (SBCarpenter sells Board
    #: @3g) — resolved off the enriched `ShopBuyEntry` by this graphic.
    buy_offer_graphic = BOARD_GRAPHIC
    #: A refill batch — two thrones' worth (38), the high end of the calibrated
    #: 19-38 range, so a single buy trip funds more than one craft (an expensive
    #: 19-board item, so fewer trips is worth the gold buffer). A live tunable.
    buy_amount = 38
    #: Reorder below one throne's boards (19) — at/above 19 a throne can still be
    #: crafted from stock, below it the next craft would starve (mirrors the
    #: smith's `MIN_INGOTS * 5` == one-batch reorder line).
    buy_reorder = THRONE_BOARDS_PER
    #: This shard's live board price (SBCarpenter GenericBuyInfo) — the readiness
    #: affordability estimate only; the buy reads the live entry price.
    buy_price_estimate = 3


class BuySaw(BuyToolCapability):
    """Carpenter config: buy a replacement Saw (0x1034) from the `vendor_spot`
    Carpenter NPC when no saw is in the pack. Only the tool config differs from
    `BuyTool`; the buy machinery is `BuyToolCapability`'s, unchanged.
    """

    name = "buy_saw"
    description = "Buy one replacement saw from the configured carpenter vendor and return."
    #: The trigger is "no saw in the pack" — a single-tool set (unlike the
    #: lumberjack's 8-axe AXE_GRAPHICS, carpentry uses the one Saw graphic).
    owned_tool_graphics = SAW_GRAPHICS
    #: The exact for-sale Saw art the Carpenter stocks — the same 0x1034 that is
    #: both the craft tool and the "already have a tool" trigger graphic.
    offer_graphic = SAW_GRAPHIC
    #: This shard's live-calibrated Saw price (15g); the readiness affordability
    #: estimate only — the buy reads the live entry price.
    tool_price_estimate = 15
    # vendor_spot_key = "vendor_spot" (inherited): the Carpenter sells saws too.
