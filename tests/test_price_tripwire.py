"""Price-table tripwire: our economic premises, checked against the shard's own source.

Every deployed loop's viability rests on a handful of vendor prices, and the audit's
economy team verified them by hand once — this test keeps them verified. It parses the
ServUO vendor tables (`Scripts/VendorInfo/SB*.cs`) directly:

    GenericBuyInfo(typeof(X), price, ...)   -> the vendor SELLS X at price
    InternalSellInfo: Add(typeof(X), price) -> the vendor PAYS the player price for X

and asserts (a) that anima2's own `*_price_estimate` constants match what the vendor
actually charges — the tripwire proper, since a silently repriced shard would strand
every affordability gate — and (b) the margin facts the flagship decisions rest on:
tongs are the one positive craft chain (7g per delivered ingot, 1.75x a raw ingot's
4g), while thrones and daggers sell for less than their own inputs (the reason the
lumberjack->carpenter pair is economically FROZEN in DESIGN.md §10).

Scope stated honestly: this reads the checked-out `../servuo` SOURCE, which is what the
local shard is built from; it cannot see runtime price scaling, and it skips entirely
when the sibling checkout is absent (CI without the shard). A type missing from a
present file FAILS — that is the tripwire firing, not a reason to skip.
"""

import re
from pathlib import Path

import pytest

SERVUO = Path(__file__).resolve().parents[2] / "servuo"
VENDOR_DIR = SERVUO / "Scripts" / "VendorInfo"

pytestmark = pytest.mark.skipif(
    not VENDOR_DIR.is_dir(), reason="../servuo checkout not present"
)


def _vendor_tables(sb_file: str) -> tuple[dict, dict[str, int]]:
    """(vendor_sells_at, vendor_pays) parsed from one SB*.cs file.

    `sells` is keyed by (type_name, item_id) because a vendor can stock one TYPE under
    several arts at different prices — SBWeaponSmith sells Hatchet as 0xF44@25 AND
    0xF43@27, and this test's first draft, keyed by type alone, let the second entry
    overwrite the first and fired on a constant that was correct all along. anima2's
    buys match by GRAPHIC (`_offer_by_graphic`), so the test must too.
    """
    text = (VENDOR_DIR / sb_file).read_text(errors="replace")
    sells = {(m.group(1), int(m.group(3), 16)): int(m.group(2)) for m in re.finditer(
        r"GenericBuyInfo\(\s*typeof\(\s*(\w+)\s*\)\s*,\s*(\d+)\s*,"
        r"\s*\d+\s*,\s*0[xX]([0-9A-Fa-f]+)", text)}
    pays: dict[str, int] = {}
    m = re.search(r"class\s+InternalSellInfo(.*)", text, re.S)
    if m:
        pays = {mm.group(1): int(mm.group(2)) for mm in re.finditer(
            r"Add\(\s*typeof\(\s*(\w+)\s*\)\s*,\s*(\d+)\s*\)", m.group(1))}
    return sells, pays


def test_our_price_estimates_match_the_shard():
    # The affordability gates (and every Life rule that derives costs from them) assume
    # these numbers; a repriced shard must fail HERE, offline, not as a live stall.
    from anima2.skills.carpentry import BuyBoards, BuySaw
    from anima2.skills.mage import BuyReagent
    from anima2.skills.tinkering import BuyIron
    from anima2.skills.woodwork import BuyHatchet

    checks = [
        ("SBTinker.cs", "IronIngot", BuyIron.buy_offer_graphic,
         BuyIron.buy_price_estimate),
        ("SBCarpenter.cs", "Board", BuyBoards.buy_offer_graphic,
         BuyBoards.buy_price_estimate),
        ("SBCarpenter.cs", "Saw", BuySaw.offer_graphic, BuySaw.tool_price_estimate),
        ("SBMage.cs", "SulfurousAsh", BuyReagent.buy_offer_graphic,
         BuyReagent.buy_price_estimate),
        ("SBWeaponSmith.cs", "Hatchet", BuyHatchet.offer_graphic,
         BuyHatchet.tool_price_estimate),
    ]
    for sb, type_name, graphic, estimate in checks:
        sells, _ = _vendor_tables(sb)
        key = (type_name, graphic)
        assert key in sells, (
            f"{sb} no longer stocks {type_name} as 0x{graphic:X} — tripwire"
        )
        assert sells[key] == estimate, (
            f"{sb}: {type_name}(0x{graphic:X}) sells at {sells[key]}g but anima2 "
            f"estimates {estimate}g — every affordability gate using it is now wrong"
        )


def test_tongs_are_the_positive_chain_and_stay_positive():
    # The flagship miner->tinker decision rests on exactly these three numbers.
    tk_sells, tk_pays = _vendor_tables("SBTinker.cs")
    _, bs_pays = _vendor_tables("SBBlacksmith.cs")
    from anima2.skills.tinkering import BuyIron

    tongs_pay = tk_pays["Tongs"]
    iron_buy = tk_sells[("IronIngot", BuyIron.buy_offer_graphic)]
    iron_pay = bs_pays["IronIngot"]
    # One ingot per tongs (ServUO DefTinkering; pinned in skills/tinkering.py).
    assert tongs_pay > iron_pay, (
        f"crafting tongs ({tongs_pay}g) no longer beats selling the raw ingot "
        f"({iron_pay}g) — the flagship chain's premise is dead"
    )
    assert tongs_pay > iron_buy, (
        f"tongs ({tongs_pay}g) no longer clear even BOUGHT iron ({iron_buy}g)"
    )


def test_the_frozen_loops_are_still_frozen_for_the_reason_we_wrote_down():
    # DESIGN.md §10 freezes the board->throne chain because it destroys value; if the
    # shard's prices ever change so that stops being true, this test says so and the
    # freeze should be reconsidered — in either direction, the DOC must match the shard.
    from anima2.carpenter_life import BOARDS_PER_ITEM

    cp_sells, cp_pays = _vendor_tables("SBCarpenter.cs")
    throne_pay = cp_pays["Throne"]
    board_pay = cp_pays["Board"]
    assert throne_pay < BOARDS_PER_ITEM * board_pay, (
        "a throne now outsells its own boards — the carpenter freeze in DESIGN.md §10 "
        "is stale and should be lifted"
    )
    _, bs_pays = _vendor_tables("SBBlacksmith.cs")
    # Daggers: 3 iron each (skills/craft.py) — worse than raw even on free mined iron.
    assert bs_pays["Dagger"] / 3 < bs_pays["IronIngot"], (
        "daggers now beat raw ingots per iron — the smith-loop demotion is stale"
    )
