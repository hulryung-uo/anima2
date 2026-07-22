"""Closing the economy into gold — sell crafted goods, then bank the proceeds.

Phase 3 item 2 (DESIGN.md §10): the blacksmith side of ``ore -> ingot -> dagger
-> gold -> bank``. Two ServUO NPC interactions, both driven through the
**right-click context menu** (0xBF/0x13 request → 0xBF/0x14 `PopupMenu` →
0xBF/0x15 `PopupSelect`) rather than speech:

- **Selling** is a vendor transaction. `BaseVendor.AddCustomContextEntries`
  (`Scripts/Mobiles/NPCs/BaseVendor.cs`) adds a `VendorSellEntry` — fixed
  cliloc **6104** ("Sell") — to any active-buying vendor's popup menu;
  clicking it calls `VendorSell`, which sends **0x9E SellList** — every pack
  item the vendor recognizes and a price for each
  (`Scripts/VendorInfo/SBBlacksmith.cs`'s `InternalSellInfo` buys Dagger,
  IronIngot, *and* Tongs/SmithHammer — a smith must never sell its own tool or
  remaining metal, so this skill filters the list down to
  `craft.DAGGER_GRAPHIC` entries only). Answering with `Action.SellItems`
  (0x9F `VendorSellReply`, `BaseVendor.cs::OnSellItems`) drops the sold items
  and calls `seller.AddToBackpack(new Gold(...))` — a plain `Gold` item
  (graphic 0x0EED, `Scripts/Items/Consumables/Gold.cs`) lands in the pack
  exactly like any other stackable, so "gold gained" is tracked the same way
  `MineSmeltDeliver` tracks ingots: sum `amount` over pack items with the gold
  graphic, not the `player.gold` status field (a periodic server push, not
  guaranteed to land the same tick as the sale).
- **Banking** deposits that gold. `Scripts/Context Menus/OpenBankEntry.cs`
  adds an entry — cliloc **6105** — to any `Banker`'s popup menu; clicking it
  calls `Owner.From.BankBox.Open()`, which sets `BankBox.Opened = true`. This
  matters because `BankBox.IsAccessibleTo` (`Server/Items/Containers.cs`)
  gates a `Drop` into it on exactly that flag: **a deposit attempted before
  the popup click is silently rejected server-side**, confirmed by reading
  that check directly (the same "an unopened container refuses items" shape
  as a bare `Drop` with no prior `PickUp` elsewhere in this package). Once
  open, the deposit is the same lift-then-place two-step
  `MineSmeltDeliver._deliver_step` / `Blacksmith._fetch_step` already
  established (`PickUp` the pack's gold pile to the cursor, then `Drop` it
  into the bank box's own serial — the bank box is a container item on the
  PLAYER at worn layer 0x1D, `Server/Item.cs`'s `Layer.Bank`, discoverable in
  `Observation.items` exactly the way `Harvest._backpack` finds the backpack
  at layer 0x15: no contract change needed, container packets already carry
  it).

**Why the context menu, not speech.** ServUO's keyword-speech matching
(`SpeechEventArgs.HasKeyword`, what `VendorAI.cs`'s "*vendor sell*"/Banker's
"*bank*" cases check) only ever sees a non-empty `Keywords` array when the
*client* sends an **encoded** speech packet with pre-matched keyword IDs
embedded — real UO clients do this transparently by matching typed text
against `speech.mul` before sending (confirmed against ClassicUO's
`Send_UnicodeSpeechRequest`/`Send_ASCIISpeechRequest`, which always call
`Speeches.GetKeywords(text)` first). Plain text sent through anima-core's
`Say` (`build_say`/`build_unicode_say` — no `speech.mul` parsing, by design;
see their own doc comments) is *never* encoded, so ServUO's own packet
handlers (`AsciiSpeech`/`UnicodeSpeech`, `Server/Network/PacketHandlers.cs`)
always pass an **empty** keyword array for it — `HasKeyword` can never be
true. This was live-caught: an earlier speech-keyword version of this skill
said "vendor sell"/"bank" (visible in its own journal — the server still
*echoes* unmatched speech) but the vendor/banker never responded at all. The
context-menu path sidesteps this entirely — `PopupRequest`/`PopupSelect` and
the 0xBF/0x14 parse were already fully implemented Rust-side (just not yet
mirrored into `contract.py`, the same lockstep gap `ShopBuy`/`ShopSell`/
`BuyItems`/`SellItems` had), so no Rust changes were needed here either.

`BlacksmithMarket(Blacksmith)` adds a `sell`/`sell_return` and `bank`/
`bank_return` phase pair on top of `Blacksmith`'s own MAKE-loop/fetch state
machine, following `MineSmeltDeliver`'s composed-phase pattern exactly:
**opt-in** via `ctx.memory["vendor_spot"]`/`ctx.memory["banker_spot"]` — with
neither configured, `step()` defers straight to `Blacksmith.step()`, so every
existing solo blacksmith (and every pre-Phase-3-item-2 test) is byte-for-byte
unaffected. A market trip only ever starts **between craft cycles** (no gump
open, and not mid an ingot-fetch trip — `Blacksmith`'s own `bs_state`), the
same "never hijack a state machine already waiting on an answer" discipline
`MineAndSmelt`'s mine/smelt guard and `MineSmeltDeliver`'s deliver guard both
use, so it can never answer a craft gump with a walk/click instead. Reward
pays only on the **confirmed** outcome (gold gained in the pack for a sale,
gold confirmed **inside the bank box's own container** for a deposit — not
merely gone from the pack, which a silently-bounced `Drop` can also produce
transiently before the bounce lands it right back — see `_bank_step`) — the
same discipline `MineSmeltDeliver._deliver_step` uses for ingots, not merely
for issuing `SellItems`/`Drop`. Every walk/wait step is stall- or timeout-
bounded (mirrors `MineSmeltDeliver._walk_toward` and `Blacksmith`'s
`_REOPEN_AFTER`) — including the context-menu popup itself (`POPUP_TIMEOUT`)
and the bank deposit's own lift-then-place retries (`BANK_DEPOSIT_ATTEMPTS`),
not just the walk/find/list/confirm stages — so a missing/dead vendor or
banker, or a bank box that never actually opens, gives up and resumes
crafting rather than freezing the MAKE loop forever. A give-up alone isn't
enough, though: resuming crafting with the same over-threshold daggers/gold
still sitting in the pack would otherwise retrigger an identical trip on the
very next craft tick — a permanently missing vendor/banker turning into a
permanent commute. `sell_giveup_daggers`/`bank_giveup_gold` (`step()`) require
the pack to hold *more* than it did at the last give-up before trying again,
mirroring `MineSmeltDeliver`'s own `deliver_giveup_ingots` backoff.

**Routes, not just points** — `vendor_spot`/`banker_spot` accept either a
plain `(x, y)` tuple (walk straight there — fine on open ground, e.g. the
solo `BLACKSMITH_SPOTS`) or a `[(x, y), ...]` list: a manually curated
waypoint route, walked leg by leg. This exists because `direction_toward`
(used by `_market_walk_toward`, same technique as `GoTo`/
`MineSmeltDeliver._walk_toward`) picks *one* straight-line direction toward
the **final** target and keeps retrying it every tick — it has no fallback
when that direction is blocked, even if a completely different route would
work (no A*, by design — DESIGN.md §10 Phase 3 item 4 is the eventual real
fix). `TRADE_SMITH_SPOT` (`profession.py`) sits at the closed end of a
single-tile-wide corridor with exactly one open exit (due east); live real-
walk probing (not `[Go` teleports — see PHASE3.md item 1's own geometry
notes for why that distinction matters) found the corridor's middle tile is
actually a small open hub with several more real exits, but a *straight* line
from the smith's own stand tile to any of them is blocked on the very first
step (it's not due east). A two-leg route through that hub (`[hub, final]`)
lets each leg's `direction_toward` computation land on a real edge instead.
Intermediate waypoints require **exact** arrival (chebyshev 0) before the
route advances to the next leg — direction_toward only reliably finds an open
path when computed from a tile it was already confirmed reachable from, not
some place merely "close enough"; only the final waypoint uses the usual
NPC-interaction reach radius. The return trip (`_market_return_step`) walks
the same route in reverse back to `bs_stand`, for the identical reason.
"""

from __future__ import annotations

from ..contract import BuyItems, Drop, PickUp, PopupRequest, PopupSelect, Position, SellItems, Walk
from ..geometry import chebyshev, direction_toward
from .base import SkillContext, SkillResult, Status
from .craft import DAGGER_GRAPHIC, MIN_INGOTS, SMITH_TOOL_GRAPHICS, Blacksmith
from .smelt import INGOT_GRAPHICS

# ServUO `Scripts/Items/Consumables/Gold.cs`: `base(0xEED)` — the one graphic
# every gold stack uses (no small/large-pile variants the way ore/ingots have).
GOLD_GRAPHIC = 0x0EED
# `Server/Item.cs` `Layer.Bank = 0x1D` — the bank box's own worn layer on the
# player, exactly as `Harvest`/`Blacksmith` find the backpack at `0x15`.
BANKBOX_LAYER = 0x1D

# Fixed context-menu clilocs: `VendorBuyEntry`/`VendorSellEntry`
# (`Scripts/Mobiles/NPCs/BaseVendor.cs`: `ContextMenuEntry(6103, 8)`/
# `ContextMenuEntry(6104, 8)`) and `OpenBankEntry`
# (`Scripts/Context Menus/OpenBankEntry.cs`: `ContextMenuEntry(6105, 12)`).
# **Live-verified against this ServUO** (`PopupEntry.cliloc`, decoded from a
# real 0xBF/0x14 response — not assumed): this shard's client negotiation
# lands on the *legacy* popup layout (`anima-core`'s `parse_popup` doc: "version
# 1 (legacy): [index][cliloc-3000000][flags]" — it adds the 3,000,000 back to
# reconstruct the full id), so the cliloc actually observed is the
# `ContextMenuEntry` constant **+3,000,000**, not the bare constant — matches
# `craft.py`'s `DAGGER_BTN` precedent (a live-decoded value, not a guess).
BUY_CLILOC = 3_006_103  # "Buy" — unused here (this skill only ever sells)
SELL_CLILOC = 3_006_104  # "Sell"
BANK_CLILOC = 3_006_105  # "Bank" (opens the bank box)

# How close (chebyshev) to stand before interacting — comfortably inside
# `VendorSellEntry`'s own context-menu range (8 tiles) and `OpenBankEntry`'s
# (12) — matches the existing `PICKUP_REACH`/`FORGE_REACH` order of magnitude
# elsewhere in this package.
SELL_REACH = 2
BANK_REACH = 2
# How far from the route's final waypoint to look for the vendor/banker
# mobile itself — generous: `[Add`'s ground-target placement can settle a
# tile or two off the exact requested spot (live-observed).
MOBILE_SEARCH_RADIUS = 4
# How many ticks to keep looking for the vendor/banker mobile before giving
# up (it may not have entered view yet, or genuinely isn't there).
FIND_MOBILE_TIMEOUT = 10
# How many ticks to wait for a response (the popup menu, or the 0x9E
# SellList) before re-asking — mirrors `craft.py`'s `_REOPEN_AFTER`.
ASK_RETRY = 10
# Total ticks the `popup` stage will stay in play — across every re-request
# cycle `_popup_click`'s own `ASK_RETRY` wait/re-ask loop makes — before
# giving up on this vendor/banker entirely. `ASK_RETRY` only paces the
# *interval between* re-requests; without a separate total-wait bound, a menu
# that never arrives at all (the vendor/banker killed or wiped after
# `_find_market_mobile` already locked its serial, a menu-less mobile locked
# onto by mistake, or a stale bridge silently omitting the `popup` observation
# key) would re-request forever and never leave this stage. Mirrors the `list`
# stage's own one-way `sell_ask_wait`/`ASK_RETRY` give-up.
POPUP_TIMEOUT = ASK_RETRY * 5
# How many ticks to wait, once `SellItems` has been sent, for the pack to
# confirm the sale (daggers gone) before giving up on this trip anyway —
# wedge-resistant: a rejected/short sale must not freeze the MAKE loop.
SELL_CONFIRM_TIMEOUT = 10
# How many ticks to wait after the bank-popup click before trusting the box
# is open — `BankBox.Open()` (see module docstring) needs a beat to land
# server-side and for its container-content packets to arrive, regardless of
# whether the box's own `ItemView` (layer 0x1D) was already visible beforehand.
BANK_SETTLE_TICKS = 3
# How many lift-then-place attempts the `deposit` stage will retry before
# giving up on this trip — a `Drop` into a bank box that was never actually
# opened server-side (see the module docstring) is silently rejected and
# bounces the gold straight back into the pack, which would otherwise repeat
# the pickup/drop cycle forever. Bounded rather than unbounded because reward
# only ever pays for gold *confirmed inside the bank box itself* (see
# `_bank_step`), so a give-up here never claws back a payment — there's
# nothing to claw back.
BANK_DEPOSIT_ATTEMPTS = 3

# Phase B8 — the buy side, the exact mirror of the sell side inverted: gold
# LEAVES the pack, iron ingots ARRIVE. The blacksmith replenishes its finite
# crafting metal from the same vendor it sells daggers to, closing the supply
# loop so the craft->sell->bank cycle runs indefinitely without a GM re-gifting
# ingots. The buy is a vendor transaction driven through the same right-click
# context menu the sell side uses (`PopupRequest` -> `PopupSelect(BUY_CLILOC)`
# -> the 0x74 BUY window arrives as `obs.shop_buy`), answered with `BuyItems`.
#
# The one iron-ingot art the vendor stocks for sale (ServUO
# `Scripts/VendorInfo/SBBlacksmith.cs`: `GenericBuyInfo(typeof(IronIngot), 5,
# 16, 0x1BF2, 0)`) — the for-sale display graphic, one of the four stack-size
# variants in `smelt.INGOT_GRAPHICS`. The BUY window is now symmetric with the
# SELL window: every `ShopBuyEntry` carries the concrete for-sale item's
# `serial`/`graphic`/`amount`/`price` inline (anima-core pairs each wire price to
# its container item by 0x3C arrival order and ships the identity here), so the
# iron offer is matched by THIS graphic and bought by the entry's own serial —
# never by a fragile `obs.items` index (its other stock — shields, armour,
# weapons, tongs — all differ). Counting iron already IN the pack still uses the
# full `INGOT_GRAPHICS` set, since a bought stack can merge into any pile-size
# variant.
IRON_INGOT_GRAPHIC = 0x1BF2
# The fixed replenishment batch: three full five-ingot craft batches
# (`MIN_INGOTS * 5` == 15) — enough to refill from empty to a whole sale run.
# After one buy, `craft_daggers` becomes ready again, so the loop self-sustains.
# Never chosen by model text; a named constant the `buy_ingots` capability
# verifies against exactly (see `capabilities.py`).
BUY_AMOUNT = MIN_INGOTS * 5
# How close (chebyshev) to stand before opening the vendor's buy window — the
# same NPC-interaction radius the sell side uses (`VendorBuyEntry`'s own
# context-menu range is 8 tiles, comfortably inside 2).
BUY_REACH = 2
# How many ticks to wait, once `BuyItems` has been sent, for the pack iron to
# rise before giving up on this trip anyway — wedge-resistant, mirroring
# `SELL_CONFIRM_TIMEOUT`: a rejected/short buy must not freeze the MAKE loop.
BUY_CONFIRM_TIMEOUT = 10

# The tool-replacement side of B8 — the near-exact mirror of the iron buy for a
# NON-stacking tool bought one at a time. When the smith's hammer/tongs wears out
# and breaks (a finite, GM-gifted supply today), crafting silently stalls; this
# buys a fresh tool with earned gold, closing the last finite-supply dependency
# in the craft loop. The one tongs art the vendor stocks for sale (ServUO
# `Scripts/VendorInfo/SBBlacksmith.cs`: `GenericBuyInfo(typeof(Tongs), 13, 14,
# 0x0FBB, 0)`) — a valid smithing tool (`craft.SMITH_TOOL_GRAPHICS` = {0x13E3,
# 0x0FBB, 0x0FBC}, which `_owned_smith_tool` matches), resolved off the enriched
# `ShopBuyEntry` by THIS graphic exactly like iron by 0x1BF2.
SMITH_TONGS_GRAPHIC = 0x0FBB
# One tool at a time — a tool never stacks, so a replenishment is a single item.
TOOL_BUY_AMOUNT = 1
# How many ticks to wait, once `BuyItems` has been sent, for a smith tool to
# arrive in the pack before giving up — mirrors `BUY_CONFIRM_TIMEOUT`.
TOOL_BUY_CONFIRM_TIMEOUT = 10


def _bank_reserve(memory) -> int:
    """The `bank_gold` working-capital reserve to retain, clamped to a
    non-negative int. The single read point for `bank_reserve` shared by the
    BankGold FSM (`skills/market.py`) and the capability policy
    (`capabilities.py`), so all of them agree.

    A negative or otherwise malformed `bank_reserve` (a stray non-int, a float,
    a bool) is treated as 0 (no reserve): left unclamped, a negative value would
    make `_bank_ready` always true (`gold > -50`, even at 0 gold) and inflate the
    surplus past the pack, wedging the goal — it could never satisfy
    `final_pack_gold == reserve`. Default 0 (unset) stays byte-identical to B7.
    """
    reserve = memory.get("bank_reserve", 0)
    return reserve if type(reserve) is int and reserve > 0 else 0


class BlacksmithMarket(Blacksmith):
    """Craft daggers, sell the surplus to a vendor, and bank the gold.

    A strict superset of `Blacksmith` — see the module docstring for the
    opt-in gate. Village wiring can safely make this the blacksmith
    profession's *one* work skill (mirrors `MineSmeltDeliver` becoming the
    miner's), since an unconfigured `BlacksmithMarket` behaves exactly like
    `Blacksmith`.
    """

    name = "blacksmith_market"
    description = "Forge daggers, sell the surplus to a vendor, and bank the proceeds."
    #: Pack daggers that trigger a sell trip.
    sell_threshold: int = 5
    #: Pack gold that triggers a bank trip.
    bank_threshold: int = 100
    #: Consecutive no-progress walking ticks before a market leg gives up
    #: (mirrors `MineSmeltDeliver.stall_limit` / `Blacksmith.stall_limit`).
    stall_limit: int = 6
    #: How many `step()` calls a give-up backoff (see below) lasts before a
    #: retry is allowed even without new progress. Bounds the *other* failure
    #: mode a pure "needs new progress" backoff has: a transient hiccup (a
    #: wedged walk from a momentarily-blocked tile, a vendor slow to respond)
    #: would otherwise back off exactly as permanently as a genuinely missing
    #: vendor/banker, and if crafting itself later stalls too (e.g. a
    #: `stuck_gump` starvation with nothing more to fetch), the pack never
    #: grows past the give-up floor either — stranding the smith in `craft`
    #: for good. A cooldown keeps the original anti-livelock intent (no
    #: immediate retrigger on the very next tick) while still eventually
    #: trying again on its own.
    giveup_cooldown_ticks: int = 30

    # --- per-capability config (generalize sell/buy_tool to a new profession +
    # item without forking the machinery — carpenter/tinker reuse these). The
    # defaults are the blacksmith's, so every blacksmith path resolves exactly
    # what it did before (byte-identical); a lumberjack skill overrides them.
    #: The pack/vendor item a sell capability trades (blacksmith: daggers).
    sold_graphic: int = DAGGER_GRAPHIC
    #: Which `ctx.memory` key holds this capability's vendor route. The
    #: blacksmith uses one "Blacksmith" NPC for sell+buy (`vendor_spot`); a
    #: profession with a separate tool vendor points buy_tool at another key.
    vendor_spot_key: str = "vendor_spot"
    #: The tools a buy_tool capability treats as "already have a working tool"
    #: (blacksmith: hammer/tongs). Its trigger is "none of these in the pack".
    owned_tool_graphics: frozenset[int] = SMITH_TOOL_GRAPHICS
    #: The exact for-sale tool art a buy_tool capability buys (blacksmith: tongs
    #: 0x0FBB) — resolved off the enriched `ShopBuyEntry` by graphic.
    offer_graphic: int = SMITH_TONGS_GRAPHIC

    def step(self, ctx: SkillContext) -> SkillResult:
        vendor = ctx.memory.get("vendor_spot")
        banker = ctx.memory.get("banker_spot")
        if vendor is None and banker is None:
            return super().step(ctx)  # opt-in: no market configured — plain Blacksmith

        obs = ctx.obs
        # Same stand tile `Blacksmith._fetch_step` walks back to — a market
        # trip and an ingot fetch both resume the MAKE loop from the same spot.
        ctx.memory.setdefault("bs_stand", (obs.player.pos.x, obs.player.pos.y))
        phase = ctx.memory.get("mkt_phase", "craft")
        tick = ctx.memory["mkt_tick"] = ctx.memory.get("mkt_tick", 0) + 1

        # Only break away from crafting between cycles: never while a gump is
        # open (never answer it with a walk/click instead) and never mid an
        # ingot-fetch trip (an item can be sitting lifted on the cursor —
        # abandoning that mid-air would strand it, per `Blacksmith._fetch_step`).
        idle = not obs.gumps and ctx.memory.get("bs_state", "open") not in ("fetch", "fetch_return")
        if phase == "craft" and idle:
            # `..._giveup_...` is the backoff floor a prior give-up left behind
            # (see below) — requiring the pack to hold *more* than it did back
            # then, *or* enough ticks to have passed, before trying again,
            # mirroring `MineSmeltDeliver`'s own `deliver_giveup_ingots` (plus
            # the cooldown `giveup_cooldown_ticks` documents). Without it, a
            # permanently missing/dead vendor or banker would still leave the
            # pack over threshold right after giving up, re-triggering an
            # identical trip on the very next craft tick — a permanent commute
            # in all but name.
            if (vendor is not None and self._pack_daggers(ctx) >= self.sell_threshold
                    and (self._pack_daggers(ctx) > ctx.memory.get("sell_giveup_daggers", -1)
                         or tick - ctx.memory.get("sell_giveup_tick", -10**9) >= self.giveup_cooldown_ticks)):
                ctx.memory.pop("sell_giveup_daggers", None)
                ctx.memory.pop("sell_giveup_tick", None)
                phase = ctx.memory["mkt_phase"] = "sell"
            elif (banker is not None and self._pack_gold(ctx) >= self.bank_threshold
                    and (self._pack_gold(ctx) > ctx.memory.get("bank_giveup_gold", -1)
                         or tick - ctx.memory.get("bank_giveup_tick", -10**9) >= self.giveup_cooldown_ticks)):
                ctx.memory.pop("bank_giveup_gold", None)
                ctx.memory.pop("bank_giveup_tick", None)
                phase = ctx.memory["mkt_phase"] = "bank"

        if phase == "sell":
            result = self._sell_step(ctx, self._route(vendor))
            if result is not None:
                return self._payout(ctx, result)
            # Record the post-trip dagger count (and tick) as the next trip's
            # backoff floor (see above) — a trip that sold nothing leaves the
            # count right where it started, blocking an immediate retrigger;
            # a trip that sold something lowers it too, but harmlessly, since
            # the threshold check above already blocks a retrigger until
            # enough *new* daggers are crafted.
            ctx.memory["sell_giveup_daggers"] = self._pack_daggers(ctx)
            ctx.memory["sell_giveup_tick"] = tick
            for key in ("sell_gold_start", "sell_paid", "sell_daggers_start", "sell_leg",
                        "sell_stage", "sell_vendor", "sell_find_wait", "sell_popup_wait",
                        "sell_popup_total", "sell_ask_wait", "sell_confirm_wait"):
                ctx.memory.pop(key, None)
            phase = ctx.memory["mkt_phase"] = "sell_return"

        if phase == "sell_return":
            result = self._market_return_step(ctx, "sell_return", self._route(vendor))
            if result is not None:
                return self._payout(ctx, result)
            # The route's own leg index (see `_walk_route`'s docstring: it
            # stays pinned for the whole trip, not popped on arrival) is only
            # actually retired here, once the trip is fully over — whichever
            # way it ended. Left behind, the *next* trip's return would
            # resume mid-route (skip the curated hub waypoint) instead of
            # starting over at the first leg.
            ctx.memory.pop("sell_return_leg", None)
            phase = ctx.memory["mkt_phase"] = "craft"

        if phase == "bank":
            result = self._bank_step(ctx, self._route(banker))
            if result is not None:
                return self._payout(ctx, result)
            # Mirrors the sell-side backoff floor above.
            ctx.memory["bank_giveup_gold"] = self._pack_gold(ctx)
            ctx.memory["bank_giveup_tick"] = tick
            for key in ("bank_paid", "bank_box_start", "bank_deposit_attempts", "bank_held",
                        "bank_leg", "bank_stage", "bank_banker", "bank_find_wait",
                        "bank_popup_wait", "bank_popup_total", "bank_settle"):
                ctx.memory.pop(key, None)
            phase = ctx.memory["mkt_phase"] = "bank_return"

        if phase == "bank_return":
            result = self._market_return_step(ctx, "bank_return", self._route(banker))
            if result is not None:
                return self._payout(ctx, result)
            # Mirrors the sell-side leg cleanup above.
            ctx.memory.pop("bank_return_leg", None)
            ctx.memory["mkt_phase"] = "craft"

        return self._payout(ctx, super().step(ctx))

    @staticmethod
    def _route(spot: tuple[int, int] | list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Normalize a configured `vendor_spot`/`banker_spot` into a waypoint
        list — a plain `(x, y)` tuple becomes a single-leg route (unchanged
        behaviour on open ground); an already-list value is a manually
        curated multi-leg route (see the module docstring).
        """
        if isinstance(spot[0], (tuple, list)):
            return [tuple(p) for p in spot]
        return [tuple(spot)]

    # --- sell ------------------------------------------------------------------

    def _sell_step(self, ctx: SkillContext, route: list[tuple[int, int]]) -> SkillResult | None:
        """One sell-phase tick, or `None` once the sale is confirmed (or given
        up on) — the caller then walks back to the forge/anvil.

        State machine (`ctx.memory["sell_stage"]`) once the route is walked:
        `find_vendor` (locate the mobile near the route's final waypoint) →
        `popup` (`PopupRequest`, then `PopupSelect` the `SELL_CLILOC` entry —
        triggers the 0x9E SellList) → `list` (wait for `obs.shop_sell`, answer
        with `SellItems` for the dagger entries only — never tools/ingots,
        even though SBBlacksmith buys those too) → `confirm` (wait for the
        pack to show the daggers gone). Reward pays only for gold **confirmed
        gained** in the pack (mirrors `MineSmeltDeliver._deliver_step`'s
        net-confirmed accounting — a rejected/partial sale can't double-pay
        on a retry).
        """
        obs = ctx.obs
        gold_now = self._pack_gold(ctx)
        start = ctx.memory.get("sell_gold_start")
        if start is None:
            start = ctx.memory["sell_gold_start"] = gold_now
            ctx.memory["sell_daggers_start"] = self._pack_sold(ctx)
        paid = ctx.memory.get("sell_paid", 0.0)
        confirmed_gain = max(0, gold_now - start)
        reward = confirmed_gain - paid
        if reward > 0:
            ctx.memory["sell_paid"] = paid + reward
        else:
            reward = 0.0

        step = self._walk_route(ctx, route, "sell", SELL_REACH, reward)
        if step is None:
            return None  # a leg wedged — abandon the whole trip, walk home
        if step is not self._ARRIVED:
            return step  # still walking the route

        stage = ctx.memory.get("sell_stage", "find_vendor")

        if stage == "find_vendor":
            vendor_serial = self._find_market_mobile(ctx, route[-1], "sell_find_wait")
            if vendor_serial is None:
                if ctx.memory.get("sell_find_wait", 0) >= FIND_MOBILE_TIMEOUT:
                    self._stash_reward(ctx, reward)
                    return None  # no vendor ever showed up near the route's end
                return SkillResult(Status.RUNNING, None, reward)
            ctx.memory["sell_vendor"] = vendor_serial
            ctx.memory.pop("sell_find_wait", None)
            stage = ctx.memory["sell_stage"] = "popup"

        vendor_serial = ctx.memory.get("sell_vendor")

        if stage == "popup":
            # `_popup_click` re-requests the menu every `ASK_RETRY` ticks
            # forever on its own — it has no notion of "total" time spent, so
            # this counts total ticks spent in this stage (across every
            # re-request cycle) and gives up once `POPUP_TIMEOUT` is hit,
            # rather than waiting on a menu that may simply never arrive
            # (dead/wiped vendor, or a menu-less mobile locked onto by
            # mistake) forever.
            total = ctx.memory.get("sell_popup_total", 0) + 1
            ctx.memory["sell_popup_total"] = total
            if total > POPUP_TIMEOUT:
                self._stash_reward(ctx, reward)
                return None  # the menu never arrived at all — give up this trip
            action = self._popup_click(ctx, vendor_serial, SELL_CLILOC, "sell_popup_wait")
            if action is _NO_ENTRY:
                self._stash_reward(ctx, reward)
                return None  # this vendor has no Sell entry (not an active buyer)
            # Only a `PopupSelect` means the menu was actually open and the
            # entry chosen — a fresh/re `PopupRequest` (or the quiet `None`
            # wait) must NOT advance the stage; it's still waiting on the menu.
            if isinstance(action, PopupSelect):
                ctx.memory["sell_stage"] = "list"
            return SkillResult(Status.RUNNING, action, reward)

        if stage == "list":
            sell = obs.shop_sell
            if sell is None:
                wait = ctx.memory.get("sell_ask_wait", 0) + 1
                ctx.memory["sell_ask_wait"] = wait
                if wait >= ASK_RETRY:
                    self._stash_reward(ctx, reward)
                    return None  # the list never arrived — give up this trip
                return SkillResult(Status.RUNNING, None, reward)
            offered = [i for i in sell.items if i.graphic == self.sold_graphic]
            if not offered:
                # The vendor's list doesn't recognize the sold item in the pack
                # (nothing to sell, or a mismatch) — bail rather than loop forever.
                self._stash_reward(ctx, reward)
                return None
            ctx.memory["sell_stage"] = "confirm"
            return SkillResult(
                Status.RUNNING,
                SellItems(vendor=sell.vendor, items=[(i.serial, i.amount) for i in offered]),
                reward,
            )

        # stage == "confirm" — already sent SellItems; wait for the pack to
        # confirm it (the sold items gone), or give up after a bounded wait
        # rather than freezing the MAKE loop over a rejected/short sale.
        if self._pack_sold(ctx) < ctx.memory["sell_daggers_start"]:
            self._stash_reward(ctx, reward)
            return None
        wait = ctx.memory.get("sell_confirm_wait", 0) + 1
        ctx.memory["sell_confirm_wait"] = wait
        if wait >= SELL_CONFIRM_TIMEOUT:
            self._stash_reward(ctx, reward)
            return None
        return SkillResult(Status.RUNNING, None, reward)

    # --- bank --------------------------------------------------------------------

    def _bank_step(self, ctx: SkillContext, route: list[tuple[int, int]]) -> SkillResult | None:
        """One bank-phase tick, or `None` once the deposit is confirmed (or
        given up on) — the caller then walks back to the forge/anvil.

        State machine (`ctx.memory["bank_stage"]`) once the route is walked:
        `find_banker` → `popup` (`PopupRequest`, then `PopupSelect` the
        `BANK_CLILOC` entry — calls `BankBox.Open()`) → `settle` (wait
        `BANK_SETTLE_TICKS` — a `Drop` into the box before this would be
        rejected server-side, see the module docstring) → `deposit` (the
        lift-then-place two-step, bounded to `BANK_DEPOSIT_ATTEMPTS` tries).

        Reward pays only for gold **confirmed inside the bank box's own
        container** (`_bankbox_gold`, baselined at `bank_box_start` once the
        `settle` wait elapses) — *not* merely gone from the pack. A `Drop`
        into a box that never actually opened server-side is silently
        rejected and bounces the gold straight back into the pack (see the
        module docstring): tracking "gone from the pack" alone would pay out
        on the lift, before the drop is even confirmed, and never claw the
        payment back on a bounce. Tracking the box's own contents instead
        means a bounce simply never pays — nothing to claw back — and
        `BANK_DEPOSIT_ATTEMPTS` bounds the retries so an unopened box can't
        wedge the smith in an unbounded pickup/drop cycle.
        """
        box_gold_now = self._bankbox_gold(ctx)
        box_start = ctx.memory.get("bank_box_start")
        paid = ctx.memory.get("bank_paid", 0.0)
        confirmed_deposit = max(0, box_gold_now - box_start) if box_start is not None else 0
        reward = confirmed_deposit - paid
        if reward > 0:
            ctx.memory["bank_paid"] = paid + reward
            # The retry budget belongs to one pile, not the whole trip.  A
            # newly observed bank-box increase proves that the previous pile
            # landed, so the next pack pile receives a fresh bounded budget.
            ctx.memory["bank_deposit_attempts"] = 0
        else:
            reward = 0.0

        step = self._walk_route(ctx, route, "bank", BANK_REACH, reward)
        if step is None:
            return None  # a leg wedged — abandon the whole trip, walk home
        if step is not self._ARRIVED:
            return step  # still walking the route

        stage = ctx.memory.get("bank_stage", "find_banker")

        if stage == "find_banker":
            banker_serial = self._find_market_mobile(ctx, route[-1], "bank_find_wait")
            if banker_serial is None:
                if ctx.memory.get("bank_find_wait", 0) >= FIND_MOBILE_TIMEOUT:
                    self._stash_reward(ctx, reward)
                    return None
                return SkillResult(Status.RUNNING, None, reward)
            ctx.memory["bank_banker"] = banker_serial
            ctx.memory.pop("bank_find_wait", None)
            stage = ctx.memory["bank_stage"] = "popup"

        banker_serial = ctx.memory.get("bank_banker")

        if stage == "popup":
            # See `_sell_step`'s matching popup block — `_popup_click` re-
            # requests forever on its own; this bounds the *total* time spent
            # in this stage across every re-request cycle.
            total = ctx.memory.get("bank_popup_total", 0) + 1
            ctx.memory["bank_popup_total"] = total
            if total > POPUP_TIMEOUT:
                self._stash_reward(ctx, reward)
                return None  # the menu never arrived at all — give up this trip
            action = self._popup_click(ctx, banker_serial, BANK_CLILOC, "bank_popup_wait")
            if action is _NO_ENTRY:
                self._stash_reward(ctx, reward)
                return None  # this "banker" has no Bank entry
            # Only a `PopupSelect` means the menu was actually open and the
            # entry chosen — a fresh/re `PopupRequest` (or the quiet `None`
            # wait) must NOT advance the stage; it's still waiting on the menu.
            if isinstance(action, PopupSelect):
                ctx.memory["bank_stage"] = "settle"
                ctx.memory["bank_settle"] = 0
            return SkillResult(Status.RUNNING, action, reward)

        if stage == "settle":
            settle = ctx.memory.get("bank_settle", 0) + 1
            ctx.memory["bank_settle"] = settle
            if settle < BANK_SETTLE_TICKS:
                return SkillResult(Status.RUNNING, None, reward)
            ctx.memory["bank_stage"] = "deposit"
            # Seed the confirmed-deposit baseline now, not before: the box's
            # own container contents are only trustworthy once the settle
            # wait has actually elapsed (see the module docstring) — seeding
            # any earlier could baseline against a not-yet-synced "0" and
            # misread gold that was already in the box (from an earlier trip)
            # as freshly deposited the moment it becomes visible.
            ctx.memory["bank_box_start"] = self._bankbox_gold(ctx)

        # stage == "deposit"
        box = self._bankbox(ctx)
        if box is None:
            # If visibility disappears after PickUp, explicitly release the
            # cursor back into the backpack before abandoning the trip. Never
            # erase the held marker without emitting a compensating Drop.
            held = ctx.memory.pop("bank_held", None)
            backpack = self._backpack(ctx)
            if held is not None and backpack is not None:
                return SkillResult(
                    Status.RUNNING,
                    Drop(serial=held, container=backpack.serial),
                    reward,
                )
            if held is not None:
                ctx.memory["bank_held"] = held
                return SkillResult(Status.RUNNING, None, reward)
            self._stash_reward(ctx, reward)
            return None

        held = ctx.memory.pop("bank_held", None)
        if held is not None:
            return SkillResult(Status.RUNNING, Drop(serial=held, container=box.serial), reward)

        # Bank only the surplus above the optional working-capital reserve
        # (default 0 == deposit everything, byte-identical to B7): stop once the
        # pack is down to `reserve`, and lift only `pack_gold - reserve` from the
        # last pile so exactly `reserve` stays behind. `_pack_gold_pile` returns
        # the smallest-serial pile, matching `_pack_gold_manifest`'s own
        # sorted-greedy split so the lift amounts line up with the frozen
        # manifest the capability proof checks. With reserve 0 the pack drains to
        # 0 pile-by-pile exactly as before.
        reserve = _bank_reserve(ctx.memory)
        surplus = self._pack_gold(ctx) - reserve
        if surplus <= 0:
            self._stash_reward(ctx, reward)
            return None  # nothing left to bank above the reserve

        pile = self._pack_gold_pile(ctx)
        if pile is None:
            self._stash_reward(ctx, reward)
            return None  # nothing left to deposit

        attempts = ctx.memory.get("bank_deposit_attempts", 0)
        if attempts >= BANK_DEPOSIT_ATTEMPTS:
            # Every lift-then-place attempt so far has bounced (the box never
            # actually opened, see the module docstring) — give up rather
            # than retrying forever. Nothing was ever paid for this gold
            # (reward only fires once it's confirmed *inside* the box), so
            # there's nothing to claw back.
            self._stash_reward(ctx, reward)
            return None
        ctx.memory["bank_deposit_attempts"] = attempts + 1
        ctx.memory["bank_held"] = pile.serial
        return SkillResult(
            Status.RUNNING,
            PickUp(serial=pile.serial, amount=min(pile.amount, surplus)),
            reward,
        )

    # --- buy (mirror of sell, inverted: gold leaves, iron ingots arrive) ----------

    def _buy_step(self, ctx: SkillContext, route: list[tuple[int, int]]) -> SkillResult | None:
        """One buy-phase tick, or `None` once the buy is confirmed (or given up
        on) — the caller then walks back to the forge/anvil.

        State machine (`ctx.memory["buy_stage"]`) once the route is walked:
        `find_vendor` (locate the mobile near the route's final waypoint) →
        `popup` (`PopupRequest`, then `PopupSelect` the `BUY_CLILOC` entry —
        opens the 0x74 BUY window) → `window` (wait for `obs.shop_buy`, find the
        iron-ingot offer by matching `IRON_INGOT_GRAPHIC` against the enriched
        `ShopBuyEntry`s — each now carries serial/graphic/amount/price inline —
        and answer with `BuyItems` for iron only, up to `BUY_AMOUNT`, clamped to
        the entry's available stock; never any other vendor stock) → `confirm`
        (wait for the pack iron count to rise). Reward pays only for iron ingots
        **confirmed gained** in the pack (mirrors `_sell_step`'s net-confirmed
        accounting — a rejected/partial buy can't double-pay on a retry).
        """
        obs = ctx.obs
        iron_now = self._pack_iron(ctx)
        start = ctx.memory.get("buy_iron_start")
        if start is None:
            start = ctx.memory["buy_iron_start"] = iron_now
            ctx.memory["buy_gold_start"] = self._pack_gold(ctx)
        paid = ctx.memory.get("buy_paid", 0.0)
        confirmed_gain = max(0, iron_now - start)
        reward = confirmed_gain - paid
        if reward > 0:
            ctx.memory["buy_paid"] = paid + reward
        else:
            reward = 0.0

        step = self._walk_route(ctx, route, "buy", BUY_REACH, reward)
        if step is None:
            return None  # a leg wedged — abandon the whole trip, walk home
        if step is not self._ARRIVED:
            return step  # still walking the route

        stage = ctx.memory.get("buy_stage", "find_vendor")

        if stage == "find_vendor":
            vendor_serial = self._find_market_mobile(ctx, route[-1], "buy_find_wait")
            if vendor_serial is None:
                if ctx.memory.get("buy_find_wait", 0) >= FIND_MOBILE_TIMEOUT:
                    self._stash_reward(ctx, reward)
                    return None  # no vendor ever showed up near the route's end
                return SkillResult(Status.RUNNING, None, reward)
            ctx.memory["buy_vendor"] = vendor_serial
            ctx.memory.pop("buy_find_wait", None)
            stage = ctx.memory["buy_stage"] = "popup"

        vendor_serial = ctx.memory.get("buy_vendor")

        if stage == "popup":
            # See `_sell_step`'s matching popup block — `_popup_click` re-requests
            # forever on its own; this bounds the *total* time spent in this
            # stage across every re-request cycle, giving up on a window that may
            # simply never arrive (dead/wiped vendor, or a menu-less mobile).
            total = ctx.memory.get("buy_popup_total", 0) + 1
            ctx.memory["buy_popup_total"] = total
            if total > POPUP_TIMEOUT:
                self._stash_reward(ctx, reward)
                return None  # the menu never arrived at all — give up this trip
            action = self._popup_click(ctx, vendor_serial, BUY_CLILOC, "buy_popup_wait")
            if action is _NO_ENTRY:
                self._stash_reward(ctx, reward)
                return None  # this vendor has no Buy entry (not a seller)
            # Only a `PopupSelect` means the menu was actually open and the entry
            # chosen — a fresh/re `PopupRequest` (or the quiet `None` wait) must
            # NOT advance the stage; it's still waiting on the menu.
            if isinstance(action, PopupSelect):
                ctx.memory["buy_stage"] = "window"
            return SkillResult(Status.RUNNING, action, reward)

        if stage == "window":
            buy = obs.shop_buy
            if buy is None:
                wait = ctx.memory.get("buy_ask_wait", 0) + 1
                ctx.memory["buy_ask_wait"] = wait
                if wait >= ASK_RETRY:
                    self._stash_reward(ctx, reward)
                    return None  # the window never arrived — give up this trip
                return SkillResult(Status.RUNNING, None, reward)
            entry = self._iron_offer(buy)
            if entry is None:
                # The vendor's for-sale list doesn't expose a single resolvable
                # iron-ingot offer — bail rather than loop or buy the wrong item.
                self._stash_reward(ctx, reward)
                return None
            # Buy the fixed batch, clamped to what the vendor actually stocks
            # (`entry.amount`), so a thin stock yields a smaller — but still
            # exactly accounted-for — replenishment rather than a rejected order.
            amount = min(BUY_AMOUNT, entry.amount)
            ctx.memory["buy_stage"] = "confirm"
            return SkillResult(
                Status.RUNNING,
                BuyItems(vendor=buy.vendor, items=[(entry.serial, amount)]),
                reward,
            )

        # stage == "confirm" — already sent BuyItems; wait for the pack iron to
        # rise (the buy landed), or give up after a bounded wait rather than
        # freezing the MAKE loop over a rejected/short buy.
        if self._pack_iron(ctx) > ctx.memory["buy_iron_start"]:
            self._stash_reward(ctx, reward)
            return None
        wait = ctx.memory.get("buy_confirm_wait", 0) + 1
        ctx.memory["buy_confirm_wait"] = wait
        if wait >= BUY_CONFIRM_TIMEOUT:
            self._stash_reward(ctx, reward)
            return None
        return SkillResult(Status.RUNNING, None, reward)

    @staticmethod
    def _offer_by_graphic(buy, graphic: int):
        """The vendor's BUY offer for one exact item art — the single
        `ShopBuyEntry` whose `graphic` is `graphic`, or `None`.

        The BUY window is symmetric with the SELL window: every entry carries the
        concrete for-sale item's serial/graphic/amount/price inline (anima-core
        pairs each wire price to its container item and ships the identity here —
        see `contract.py::ShopBuyEntry`), so an offer is matched by graphic and
        bought by the entry's own serial, never by a fragile `obs.items` index.
        The vendor stocks exactly one entry per art (iron 0x1BF2, tongs 0x0FBB,
        etc.), so this fails closed unless exactly one matching entry exists with
        a positive serial, price, and available amount — a malformed/half-filled
        window abandons the trip rather than mis-buying.
        """
        if buy is None:
            return None
        matches = [entry for entry in buy.entries if entry.graphic == graphic]
        if len(matches) != 1:
            return None
        entry = matches[0]
        if (
            type(entry.serial) is not int
            or entry.serial <= 0
            or type(entry.price) is not int
            or entry.price <= 0
            or type(entry.amount) is not int
            or entry.amount <= 0
        ):
            return None
        return entry

    @staticmethod
    def _iron_offer(buy):
        """The vendor's iron-ingot BUY offer (graphic `IRON_INGOT_GRAPHIC`), the
        ONLY 0x1BF2 offer it stocks (its armour/weapons/tongs all differ)."""
        return BlacksmithMarket._offer_by_graphic(buy, IRON_INGOT_GRAPHIC)

    @staticmethod
    def _tool_offer(buy):
        """The vendor's tongs BUY offer (graphic `SMITH_TONGS_GRAPHIC`) — a valid
        smithing tool the `buy_smith_tool` capability replaces a broken one with."""
        return BlacksmithMarket._offer_by_graphic(buy, SMITH_TONGS_GRAPHIC)

    def _buy_offer_for(self, buy, action: BuyItems) -> tuple[int, int, int] | None:
        """Reconstruct the exact `(serial, amount, unit_price)` a `BuyItems`
        action is committing to, straight from the still-open BUY window's iron
        entry — never trusting any model text. `None` unless the action names
        exactly that iron serial for the deterministic clamped order amount
        (`min(BUY_AMOUNT, entry.amount)`) at the entry's own positive price.
        Mirrors how `SellDaggers.step` reconstructs its offered items from the
        open `ShopSell` before recording goal evidence.
        """
        entry = self._iron_offer(buy)
        if entry is None or len(action.items) != 1:
            return None
        serial, amount = action.items[0]
        if (
            serial != entry.serial
            or type(amount) is not int
            or amount != min(BUY_AMOUNT, entry.amount)
        ):
            return None
        return (serial, amount, entry.price)

    def _tool_offer_for(self, buy, action: BuyItems) -> tuple[int, int, int] | None:
        """The tool-buy analogue of `_buy_offer_for`: reconstruct `(serial,
        amount, unit_price)` from the still-open window's tool entry (resolved by
        `self.offer_graphic`). `None` unless the action names exactly that tool
        serial for `min(TOOL_BUY_AMOUNT, entry.amount)` (one tool) at the entry's
        own positive price.
        """
        entry = self._offer_by_graphic(buy, self.offer_graphic)
        if entry is None or len(action.items) != 1:
            return None
        serial, amount = action.items[0]
        if (
            serial != entry.serial
            or type(amount) is not int
            or amount != min(TOOL_BUY_AMOUNT, entry.amount)
        ):
            return None
        return (serial, amount, entry.price)

    # --- buy_smith_tool (B8) — replace a broken tool, one non-stacking tool at a time -

    def _toolbuy_step(self, ctx: SkillContext, route: list[tuple[int, int]]) -> SkillResult | None:
        """One tool-buy tick, or `None` once the buy is confirmed (or given up on).

        The exact mirror of `_buy_step` for a NON-stacking tool: same walk →
        `find_vendor` → `popup`(Buy) → `window` → `confirm` state machine (its own
        `toolbuy_*` memory namespace), but the `window` stage resolves the tongs
        offer by `SMITH_TONGS_GRAPHIC`, orders exactly `TOOL_BUY_AMOUNT` (one),
        and `confirm` waits for the pack's smith-tool COUNT to rise — a tool
        arrives as a distinct item, not a stack, so arrival is a count delta, not
        an amount sum. Reward pays only for a smith tool **confirmed gained**.
        """
        obs = ctx.obs
        tools_now = self._pack_tools(ctx)
        start = ctx.memory.get("toolbuy_tools_start")
        if start is None:
            start = ctx.memory["toolbuy_tools_start"] = tools_now
            ctx.memory["toolbuy_gold_start"] = self._pack_gold(ctx)
        paid = ctx.memory.get("toolbuy_paid", 0.0)
        confirmed_gain = max(0, tools_now - start)
        reward = confirmed_gain - paid
        if reward > 0:
            ctx.memory["toolbuy_paid"] = paid + reward
        else:
            reward = 0.0

        step = self._walk_route(ctx, route, "toolbuy", BUY_REACH, reward)
        if step is None:
            return None  # a leg wedged — abandon the whole trip, walk home
        if step is not self._ARRIVED:
            return step  # still walking the route

        stage = ctx.memory.get("toolbuy_stage", "find_vendor")

        if stage == "find_vendor":
            vendor_serial = self._find_market_mobile(ctx, route[-1], "toolbuy_find_wait")
            if vendor_serial is None:
                if ctx.memory.get("toolbuy_find_wait", 0) >= FIND_MOBILE_TIMEOUT:
                    self._stash_reward(ctx, reward)
                    return None  # no vendor ever showed up near the route's end
                return SkillResult(Status.RUNNING, None, reward)
            ctx.memory["toolbuy_vendor"] = vendor_serial
            ctx.memory.pop("toolbuy_find_wait", None)
            stage = ctx.memory["toolbuy_stage"] = "popup"

        vendor_serial = ctx.memory.get("toolbuy_vendor")

        if stage == "popup":
            total = ctx.memory.get("toolbuy_popup_total", 0) + 1
            ctx.memory["toolbuy_popup_total"] = total
            if total > POPUP_TIMEOUT:
                self._stash_reward(ctx, reward)
                return None  # the menu never arrived at all — give up this trip
            action = self._popup_click(ctx, vendor_serial, BUY_CLILOC, "toolbuy_popup_wait")
            if action is _NO_ENTRY:
                self._stash_reward(ctx, reward)
                return None  # this vendor has no Buy entry (not a seller)
            if isinstance(action, PopupSelect):
                ctx.memory["toolbuy_stage"] = "window"
            return SkillResult(Status.RUNNING, action, reward)

        if stage == "window":
            buy = obs.shop_buy
            if buy is None:
                wait = ctx.memory.get("toolbuy_ask_wait", 0) + 1
                ctx.memory["toolbuy_ask_wait"] = wait
                if wait >= ASK_RETRY:
                    self._stash_reward(ctx, reward)
                    return None  # the window never arrived — give up this trip
                return SkillResult(Status.RUNNING, None, reward)
            entry = self._offer_by_graphic(buy, self.offer_graphic)
            if entry is None:
                # No single resolvable tool offer — bail rather than mis-buy.
                self._stash_reward(ctx, reward)
                return None
            amount = min(TOOL_BUY_AMOUNT, entry.amount)
            ctx.memory["toolbuy_stage"] = "confirm"
            return SkillResult(
                Status.RUNNING,
                BuyItems(vendor=buy.vendor, items=[(entry.serial, amount)]),
                reward,
            )

        # stage == "confirm" — already sent BuyItems; wait for the pack's smith-
        # tool COUNT to rise (the tool landed), or give up after a bounded wait.
        if self._pack_tools(ctx) > ctx.memory["toolbuy_tools_start"]:
            self._stash_reward(ctx, reward)
            return None
        wait = ctx.memory.get("toolbuy_confirm_wait", 0) + 1
        ctx.memory["toolbuy_confirm_wait"] = wait
        if wait >= TOOL_BUY_CONFIRM_TIMEOUT:
            self._stash_reward(ctx, reward)
            return None
        return SkillResult(Status.RUNNING, None, reward)

    # --- context-menu machinery (shared by sell/bank) -----------------------------

    @staticmethod
    def _find_market_mobile(ctx: SkillContext, spot: tuple[int, int], wait_key: str) -> int | None:
        """Find the vendor/banker mobile staged near `spot` (the route's final
        waypoint) — `[Add`'s ground-target placement can settle a tile or two
        off the exact requested spot, so this searches `MOBILE_SEARCH_RADIUS`,
        picking whichever candidate is closest to `spot` itself. Bumps
        `ctx.memory[wait_key]` on a miss so the caller can time out; the
        caller is responsible for actually giving up once that counter reaches
        `FIND_MOBILE_TIMEOUT` (this only searches — it doesn't stash/bail).
        """
        sx, sy = spot
        cands = [m for m in ctx.obs.mobiles if chebyshev(m.pos, Position(sx, sy, m.pos.z)) <= MOBILE_SEARCH_RADIUS]
        if not cands:
            ctx.memory[wait_key] = ctx.memory.get(wait_key, 0) + 1
            return None
        nearest = min(cands, key=lambda m: chebyshev(m.pos, Position(sx, sy, m.pos.z)))
        return nearest.serial

    @staticmethod
    def _popup_click(ctx: SkillContext, serial: int, cliloc: int, wait_key: str):
        """Drive one request/select cycle of a context-menu interaction:
        `PopupRequest` the menu, wait for it, then `PopupSelect` the entry
        matching `cliloc`. Returns the `Action` to take this tick, `None` to
        keep waiting, or the `_NO_ENTRY` sentinel if the open menu doesn't
        have a matching entry at all (the caller should give up — this
        "vendor"/"banker" doesn't offer what we came for).
        """
        popup = ctx.obs.popup
        if popup is None or popup.serial != serial:
            wait = ctx.memory.get(wait_key, ASK_RETRY)
            if wait < ASK_RETRY:
                ctx.memory[wait_key] = wait + 1
                return None
            ctx.memory[wait_key] = 0
            return PopupRequest(serial=serial)
        entry = next((e for e in popup.entries if e.cliloc == cliloc), None)
        if entry is None:
            return _NO_ENTRY
        return PopupSelect(serial=serial, index=entry.index)

    # --- shared walk/return machinery ---------------------------------------------

    #: Sentinel `_walk_route` returns when it has reached the final waypoint
    #: (as opposed to `None`, which means a leg wedged — see `_walk_route`).
    _ARRIVED = object()

    def _walk_route(self, ctx: SkillContext, route: list[tuple[int, int]], tag: str,
                    final_reach: int, reward: float = 0.0):
        """Walk a (possibly multi-leg) waypoint route, one leg at a time (see
        the module docstring for why a route, not always a straight line, is
        needed). All but the last waypoint must be reached **exactly**
        (chebyshev 0) before advancing to the next leg; the last only needs
        `final_reach` (the usual NPC-interaction radius).

        Returns `_ARRIVED` once within reach of the final waypoint, `None` if
        a leg wedged (`_market_walk_toward` already gave up and stashed the
        reward — the caller should abandon the whole trip, same as a
        single-point wedge give-up), or a `SkillResult` for one more walking
        tick. Deliberately does **not** clear `f"{tag}_leg"` on arrival: this
        method runs again on every later tick of the same trip (the
        popup/list/confirm/settle/deposit stages all call it first, same as
        the initial walk), so popping the leg here would reset it to 0 on the
        very next call — sending the smith back toward the *first* waypoint
        mid-interaction instead of re-confirming it's still within reach of
        the last one. The leg stays pinned at wherever the trip left it until
        `step()`'s own end-of-phase cleanup retires it, once the trip is
        actually over.

        Checks the **final** waypoint's reach first, before touching any leg
        at all: on a co-located layout — the trade smithy's vendor/banker
        sit chebyshev 1 from the smith's own stand tile, comfortably within
        `SELL_REACH`/`BANK_REACH` — the curated hub route exists only for
        layouts where a straight line to the final target isn't open (see
        the module docstring); walking through an intermediate waypoint at
        all is then both unnecessary and a needless dependency on that
        waypoint being clear (live-caught: a `[Add`-ed NPC that settled onto
        the hub tile instead of its own spot denied every step through it,
        even though the vendor/banker were already in reach from the trip's
        very first tick).
        """
        here = ctx.obs.player.pos
        fx, fy = route[-1]
        if chebyshev(here, Position(fx, fy, here.z)) <= final_reach:
            return self._ARRIVED

        leg = ctx.memory.get(f"{tag}_leg", 0)
        tx, ty = route[leg]
        last_leg = leg == len(route) - 1
        reach = final_reach if last_leg else 0
        if chebyshev(here, Position(tx, ty, here.z)) <= reach:
            ctx.memory[f"{tag}_leg"] = leg + 1
            ctx.memory.pop(f"{tag}_stall", None)
            ctx.memory.pop(f"{tag}_last_pos", None)
            return self._walk_route(ctx, route, tag, final_reach, reward)  # same tick, next leg
        return self._market_walk_toward(ctx, tx, ty, tag, reward)

    def _market_walk_toward(self, ctx: SkillContext, tx: int, ty: int, tag: str,
                            reward: float = 0.0) -> SkillResult | None:
        """One greedy step toward `(tx, ty)`, `stall_limit`-bounded like `GoTo` /
        `MineSmeltDeliver._walk_toward`. `None` means wedged — the caller treats
        that exactly like "arrived" and moves the phase on, rather than
        retrying into the same obstruction forever.

        Leaves `f"{tag}_leg"` untouched on a give-up, same as on a real
        arrival (see `_walk_route`'s docstring) — `step()`'s own end-of-phase
        cleanup retires it either way, once the trip is actually over.
        """
        here = ctx.obs.player.pos
        cur = (here.x, here.y)
        stall_key, pos_key = f"{tag}_stall", f"{tag}_last_pos"
        stall = ctx.memory.get(stall_key, 0) + 1 if ctx.memory.get(pos_key) == cur else 0
        ctx.memory[stall_key] = stall
        ctx.memory[pos_key] = cur
        if stall >= self.stall_limit:
            ctx.memory.pop(stall_key, None)
            ctx.memory.pop(pos_key, None)
            self._stash_reward(ctx, reward)
            return None
        d = direction_toward(here, Position(tx, ty, here.z))
        return SkillResult(Status.RUNNING, Walk(dir=d, run=False), reward)

    def _market_return_step(self, ctx: SkillContext, tag: str,
                            route: list[tuple[int, int]]) -> SkillResult | None:
        """Walk back to the forge/anvil stand tile (`bs_stand`) via the same
        route (in reverse) the outbound trip used, or `None` once there (or if
        no stand tile was ever recorded) — resume the MAKE loop. Mirrors
        `Blacksmith._fetch_return_step` / `MineSmeltDeliver._return_step`; the
        route-reversal is what the single-point case already did (nothing to
        reverse — see the module docstring on why a route can matter at all).
        """
        stand = ctx.memory.get("bs_stand")
        if stand is None:
            return None
        # The outbound route's waypoints (excluding the final NPC tile itself,
        # already left behind) in reverse, then home — a single-point route
        # reverses to an empty list, so this is exactly `[stand]`, matching
        # the original direct-walk-home behaviour on open ground.
        home_route = list(reversed(route[:-1])) + [tuple(stand)]
        step = self._walk_route(ctx, home_route, tag, 0, 0.0)
        # Both real arrival (`_ARRIVED`) and a wedge give-up (`None` from
        # `_market_walk_toward`, already reward-stashed) resolve to `None`
        # here — either way, the caller resumes the MAKE loop from wherever
        # the smith ended up (mirrors `Blacksmith._fetch_return_step`).
        return None if step is self._ARRIVED else step

    # --- reward carry (mirrors MineSmeltDeliver._bank/_payout) -------------------

    def _stash_reward(self, ctx: SkillContext, reward: float) -> None:
        """Stash a reward this tick's `None` return is about to discard, so the
        very next `SkillResult` this skill returns pays it out instead — closes
        the one-tick observation-lag gap where a confirmed pack change lands on
        exactly the tick a phase's "nothing left to do" scan comes up empty.
        Named distinctly from `MineSmeltDeliver._bank` so it isn't confused
        with this skill's own *banker* deposit phase.
        """
        if reward:
            ctx.memory["mkt_banked_reward"] = ctx.memory.get("mkt_banked_reward", 0.0) + reward

    def _payout(self, ctx: SkillContext, result: SkillResult) -> SkillResult:
        """Fold any reward stashed by `_stash_reward` into `result` before it leaves."""
        banked = ctx.memory.pop("mkt_banked_reward", 0.0)
        if not banked:
            return result
        return SkillResult(result.status, result.action, result.reward + banked)

    # --- pack scans ----------------------------------------------------------------

    def _pack_daggers(self, ctx: SkillContext) -> int:
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(i.amount for i in ctx.obs.items if i.graphic == DAGGER_GRAPHIC and i.container == bp.serial)

    def _pack_sold(self, ctx: SkillContext) -> int:
        """Pack amount of the capability's configured sold item (`self.sold_graphic`
        — daggers for the blacksmith, boards for the lumberjack). The sell
        machinery's start/confirm/give-up counts. For the blacksmith default
        (`sold_graphic == DAGGER_GRAPHIC`) this equals `_pack_daggers` exactly.
        """
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(i.amount for i in ctx.obs.items if i.graphic == self.sold_graphic and i.container == bp.serial)

    def _pack_gold(self, ctx: SkillContext) -> int:
        # Same well-formed-pile filter as `BankGold._pack_gold_manifest` and
        # `_pack_gold_pile`, so all three agree on which piles count (a malformed
        # pile the manifest wouldn't freeze can't inflate the surplus here). Every
        # real ServUO gold pile is a positive-amount int-serial stack, so this is
        # a defensive no-op in practice.
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(
            i.amount
            for i in ctx.obs.items
            if i.graphic == GOLD_GRAPHIC
            and i.container == bp.serial
            and type(i.serial) is int
            and type(i.amount) is int
            and i.amount > 0
        )

    def _pack_iron(self, ctx: SkillContext) -> int:
        """Iron ingots currently in the pack — summed across every pile-size
        variant in `INGOT_GRAPHICS` (a bought stack can merge into any of them),
        unlike `_iron_offer`, which matches the vendor's single for-sale display
        graphic. The confirmed-arrival signal `_buy_step` rewards on and the
        `buy_ingots` capability verifies against.
        """
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(i.amount for i in ctx.obs.items if i.graphic in INGOT_GRAPHICS and i.container == bp.serial)

    def _pack_tools(self, ctx: SkillContext) -> int:
        """COUNT of the capability's owned-tool graphics in the pack (each tool is
        a distinct, non-stacking item, so this counts items, not amounts). The
        confirmed-arrival signal `_toolbuy_step` rewards on and the buy_tool
        capability verifies a 0->1 delta against — consistent with
        `capabilities._owned_tool(ctx, graphics)`. Uses `self.owned_tool_graphics`
        (blacksmith: SMITH_TOOL_GRAPHICS; lumberjack: AXE_GRAPHICS).
        """
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(
            1
            for i in ctx.obs.items
            if i.graphic in self.owned_tool_graphics and i.container == bp.serial
        )

    def _pack_gold_pile(self, ctx: SkillContext):
        # The smallest-serial gold pile, matching `BankGold._pack_gold_manifest`'s
        # own `sorted()`-then-greedy split, so a reserve-bounded deposit lifts the
        # exact per-pile amounts the frozen manifest records (whole piles first,
        # partialing the last). Order is irrelevant when banking everything
        # (reserve 0), so this is byte-identical there.
        bp = self._backpack(ctx)
        if bp is None:
            return None
        piles = [
            i
            for i in ctx.obs.items
            if i.graphic == GOLD_GRAPHIC
            and i.container == bp.serial
            and type(i.serial) is int
            and type(i.amount) is int
            and i.amount > 0
        ]
        return min(piles, key=lambda i: i.serial) if piles else None

    @staticmethod
    def _bankbox(ctx: SkillContext):
        return next(
            (i for i in ctx.obs.items if i.layer == BANKBOX_LAYER and i.container == ctx.obs.player.serial), None,
        )

    def _bankbox_gold(self, ctx: SkillContext) -> int:
        """Gold sitting **inside** the bank box's own container — the
        confirmed-deposit signal `_bank_step` rewards on (see its docstring),
        as opposed to `_pack_gold`, which only shows what's left in the pack.
        `0` both when the box isn't visible at all and when it's visible but
        genuinely empty — `_bank_step` only ever diffs this against a
        same-shape baseline (`bank_box_start`), so the two cases collapsing
        together is harmless.
        """
        box = self._bankbox(ctx)
        if box is None:
            return 0
        return sum(i.amount for i in ctx.obs.items if i.graphic == GOLD_GRAPHIC and i.container == box.serial)


class SellItemCapability(BlacksmithMarket):
    """Operation-specific vendor hands: sell one configured pack item, never craft
    or bank. The generalized base for `SellDaggers` (blacksmith) and `SellBoards`
    (lumberjack) — subclasses only set `sold_graphic`/`sell_threshold`/
    `vendor_spot_key`/`name`/`description`; all the provenance logic below reads
    those class attrs, so nothing forks per item.

    The capability keeps observation evidence scoped to the active goal frame.
    Merely opening a vendor window, sending ``SellItems``, or observing a gold
    change is insufficient on its own: completion requires the exact offered
    item quantity to disappear, at least its quoted value to arrive, and the
    worker to return to the safe idle phase for the same ``goal_id``. (The
    ``cap_sell_*``/``sell_daggers_start`` memory keys keep their legacy names —
    per-agent single-profession means no collision — while now counting
    ``self.sold_graphic``.)
    """

    _CLEANUP_KEYS = (
        "sell_gold_start",
        "sell_paid",
        "sell_daggers_start",
        "sell_leg",
        "sell_stage",
        "sell_vendor",
        "sell_find_wait",
        "sell_popup_wait",
        "sell_popup_total",
        "sell_ask_wait",
        "sell_confirm_wait",
    )

    def _begin_goal(self, ctx: SkillContext) -> bool:
        goal_id = ctx.goal_id
        if type(goal_id) is not int:
            return False
        if ctx.memory.get("cap_sell_goal_id") == goal_id:
            return True

        vendor = ctx.memory.get(self.vendor_spot_key)
        try:
            route = tuple(self._route(vendor))
        except (IndexError, TypeError, ValueError):
            return False
        if not route:
            return False

        ctx.memory["cap_sell_goal_id"] = goal_id
        ctx.memory["cap_sell_route"] = route
        ctx.memory["cap_sell_start_daggers"] = self._pack_sold(ctx)
        ctx.memory["cap_sell_start_gold"] = self._pack_gold(ctx)
        for key in (
            "cap_sell_sent_goal_id",
            "cap_sell_sent_daggers",
            "cap_sell_expected_gold",
            "cap_sell_offered_items",
            "cap_sell_offered_removed",
            "cap_sell_offered_cleared",
            "cap_sell_dagger_delta",
            "cap_sell_gold_delta",
            "cap_sell_finished_goal_id",
            "cap_sell_returned_goal_id",
        ):
            ctx.memory.pop(key, None)
        return True

    def _observe_evidence(self, ctx: SkillContext) -> None:
        goal_id = ctx.goal_id
        if ctx.memory.get("cap_sell_sent_goal_id") != goal_id:
            return
        start_daggers = ctx.memory.get("cap_sell_start_daggers")
        start_gold = ctx.memory.get("cap_sell_start_gold")
        if type(start_daggers) is not int or type(start_gold) is not int:
            return
        ctx.memory["cap_sell_dagger_delta"] = max(
            0, start_daggers - self._pack_sold(ctx)
        )
        ctx.memory["cap_sell_gold_delta"] = max(
            0, self._pack_gold(ctx) - start_gold
        )
        offered = ctx.memory.get("cap_sell_offered_items")
        if (
            not isinstance(offered, tuple)
            or not offered
            or not all(
                isinstance(entry, tuple)
                and len(entry) == 3
                and all(type(value) is int and value > 0 for value in entry)
                for entry in offered
            )
        ):
            return
        backpack = self._backpack(ctx)
        current = {
            item.serial: item.amount
            for item in ctx.obs.items
            if backpack is not None and item.container == backpack.serial
        }
        ctx.memory["cap_sell_offered_removed"] = sum(
            max(0, amount - min(amount, current.get(serial, 0)))
            for serial, amount, _price in offered
        )
        ctx.memory["cap_sell_offered_cleared"] = all(
            current.get(serial, 0) == 0 for serial, _amount, _price in offered
        )

    def step(self, ctx: SkillContext) -> SkillResult:
        if not self._begin_goal(ctx):
            return SkillResult(Status.RUNNING)
        self._observe_evidence(ctx)
        goal_id = ctx.goal_id
        if ctx.memory.get("cap_sell_finished_goal_id") == goal_id:
            return self._payout(ctx, SkillResult(Status.RUNNING))

        obs = ctx.obs
        ctx.memory.setdefault("bs_stand", (obs.player.pos.x, obs.player.pos.y))
        route = [tuple(point) for point in ctx.memory["cap_sell_route"]]
        phase = ctx.memory.get("mkt_phase", "craft")
        tick = ctx.memory["mkt_tick"] = ctx.memory.get("mkt_tick", 0) + 1
        if phase not in {"sell", "sell_return"}:
            phase = ctx.memory["mkt_phase"] = "sell"

        if phase == "sell":
            result = self._sell_step(ctx, route)
            if result is not None:
                if isinstance(result.action, SellItems):
                    offered = {
                        serial: (amount, price)
                        for serial, amount, price in (
                            (item.serial, item.amount, item.price)
                            for item in (obs.shop_sell.items if obs.shop_sell else [])
                            if item.graphic == self.sold_graphic
                        )
                    }
                    sent_daggers = sum(
                        amount
                        for serial, amount in result.action.items
                        if serial in offered and amount == offered[serial][0]
                    )
                    expected_gold = sum(
                        amount * offered[serial][1]
                        for serial, amount in result.action.items
                        if serial in offered and amount == offered[serial][0]
                    )
                    if sent_daggers > 0 and expected_gold > 0:
                        offered_items = tuple(
                            (serial, amount, offered[serial][1])
                            for serial, amount in result.action.items
                            if serial in offered and amount == offered[serial][0]
                        )
                        ctx.memory["cap_sell_sent_goal_id"] = goal_id
                        ctx.memory["cap_sell_sent_daggers"] = sent_daggers
                        ctx.memory["cap_sell_expected_gold"] = expected_gold
                        ctx.memory["cap_sell_offered_items"] = offered_items
                return self._payout(ctx, result)
            ctx.memory["sell_giveup_daggers"] = self._pack_sold(ctx)
            ctx.memory["sell_giveup_tick"] = tick
            for key in self._CLEANUP_KEYS:
                ctx.memory.pop(key, None)
            phase = ctx.memory["mkt_phase"] = "sell_return"

        if phase == "sell_return":
            result = self._market_return_step(ctx, "sell_return", route)
            if result is not None:
                return self._payout(ctx, result)
            ctx.memory.pop("sell_return_leg", None)
            ctx.memory["mkt_phase"] = "craft"
            ctx.memory["cap_sell_finished_goal_id"] = goal_id
            stand = ctx.memory.get("bs_stand")
            if (
                isinstance(stand, (tuple, list))
                and len(stand) == 2
                and (obs.player.pos.x, obs.player.pos.y) == tuple(stand)
            ):
                ctx.memory["cap_sell_returned_goal_id"] = goal_id
            self._observe_evidence(ctx)

        # Deliberately do not fall through to Blacksmith.step(): completion is
        # evaluated from the next Observation before any hammer/bank action.
        return self._payout(ctx, SkillResult(Status.RUNNING))


class SellDaggers(SellItemCapability):
    """Blacksmith config: sell daggers (0x0F52) to the `vendor_spot` Blacksmith
    NPC. Inherits every default (`sold_graphic=DAGGER_GRAPHIC`, `sell_threshold=5`,
    `vendor_spot_key="vendor_spot"`), so it is byte-identical to the B5 skill.
    """

    name = "sell_daggers"
    description = "Sell observed backpack daggers to the configured vendor and return."


class BankGold(BlacksmithMarket):
    """Operation-specific bank hands: deposit gold, never craft or sell.

    ``BlacksmithMarket`` normally chooses among crafting, selling, and banking.
    A closed ``blacksmith.bank_gold`` capability must not unlock those sibling
    powers, so this adapter drives only the already-verified bank and return
    phases while reusing their exact packet/evidence implementation.
    """

    name = "bank_gold"
    description = "Deposit backpack gold into the observed bank box and return."

    _CLEANUP_KEYS = (
        "bank_paid",
        "bank_box_start",
        "bank_deposit_attempts",
        "bank_held",
        "bank_leg",
        "bank_stage",
        "bank_banker",
        "bank_find_wait",
        "bank_popup_wait",
        "bank_popup_total",
        "bank_settle",
    )

    _GOAL_EVIDENCE_KEYS = (
        "cap_bank_baseline_goal_id",
        "cap_bank_start_bank_gold",
        "cap_bank_box_serial",
        "cap_bank_sent_goal_id",
        "cap_bank_lifted_items",
        "cap_bank_dropped_items",
        "cap_bank_pack_delta",
        "cap_bank_bank_delta",
        "cap_bank_confirmed",
        "cap_bank_final_pack_gold",
        "cap_bank_start_piles_removed",
        "cap_bank_start_piles_cleared",
        "cap_bank_finished_goal_id",
        "cap_bank_returned_goal_id",
    )

    def _pack_gold_manifest(self, ctx: SkillContext) -> tuple[tuple[int, int], ...]:
        """The exact `(serial, amount)` piles this goal will BANK — the surplus
        above the optional working-capital reserve (`ctx.memory["bank_reserve"]`,
        default 0). Whole piles are banked greedily in serial order and the last
        one is partialed so exactly `reserve` gold stays in the pack; the piles
        total the surplus, so the capability's frozen-manifest proof binds to
        what actually moves. With reserve 0 this is the whole pack, in the same
        sorted order as before — byte-identical to B7.
        """
        backpack = self._backpack(ctx)
        if backpack is None:
            return ()
        piles = sorted(
            (item.serial, item.amount)
            for item in ctx.obs.items
            if item.graphic == GOLD_GRAPHIC
            and item.container == backpack.serial
            and type(item.serial) is int
            and type(item.amount) is int
            and item.amount > 0
        )
        surplus = sum(amount for _serial, amount in piles) - _bank_reserve(ctx.memory)
        if surplus <= 0:
            return ()
        banked: list[tuple[int, int]] = []
        remaining = surplus
        for serial, amount in piles:
            if remaining <= 0:
                break
            take = min(amount, remaining)
            banked.append((serial, take))
            remaining -= take
        return tuple(banked)

    def _begin_goal(self, ctx: SkillContext) -> bool:
        goal_id = ctx.goal_id
        if type(goal_id) is not int:
            return False
        if ctx.memory.get("cap_bank_goal_id") == goal_id:
            return True

        banker = ctx.memory.get("banker_spot")
        try:
            route = tuple(self._route(banker))
        except (IndexError, TypeError, ValueError):
            return False
        piles = self._pack_gold_manifest(ctx)
        expected = sum(amount for _serial, amount in piles)
        if not route or expected <= 0:
            return False

        for key in self._GOAL_EVIDENCE_KEYS:
            ctx.memory.pop(key, None)
        ctx.memory["cap_bank_goal_id"] = goal_id
        ctx.memory["cap_bank_route"] = route
        ctx.memory["cap_bank_start_piles"] = piles
        ctx.memory["cap_bank_expected_gold"] = expected
        ctx.memory["cap_bank_start_pack_gold"] = expected
        # The FULL pack gold before banking (surplus + retained reserve), so the
        # `pack_delta` proof measures gold that actually LEFT the pack — with
        # reserve 0 this equals `expected`, so the delta math is unchanged.
        ctx.memory["cap_bank_start_full_pack"] = self._pack_gold(ctx)
        ctx.memory["cap_bank_start_pos"] = (
            ctx.obs.player.pos.x,
            ctx.obs.player.pos.y,
        )
        return True

    def _observe_evidence(self, ctx: SkillContext) -> None:
        goal_id = ctx.goal_id
        if type(goal_id) is not int or ctx.memory.get("cap_bank_goal_id") != goal_id:
            return
        expected = ctx.memory.get("cap_bank_expected_gold")
        piles = ctx.memory.get("cap_bank_start_piles")
        if (
            type(expected) is not int
            or expected <= 0
            or not isinstance(piles, tuple)
            or not piles
            or not all(
                isinstance(entry, tuple)
                and len(entry) == 2
                and all(type(value) is int and value > 0 for value in entry)
                for entry in piles
            )
            or len({serial for serial, _amount in piles}) != len(piles)
            or sum(amount for _serial, amount in piles) != expected
        ):
            return

        pack_now = self._pack_gold(ctx)
        ctx.memory["cap_bank_final_pack_gold"] = pack_now
        # Gold that actually LEFT the pack, measured against the full starting
        # pack (surplus + reserve), so a retained reserve isn't miscounted as
        # un-banked surplus. With reserve 0, `start_full == expected`, so this is
        # exactly the prior `expected - pack_now`.
        start_full = ctx.memory.get("cap_bank_start_full_pack", expected)
        ctx.memory["cap_bank_pack_delta"] = max(0, start_full - pack_now)
        backpack = self._backpack(ctx)
        current = {
            item.serial: item.amount
            for item in ctx.obs.items
            if backpack is not None
            and item.graphic == GOLD_GRAPHIC
            and item.container == backpack.serial
        }
        ctx.memory["cap_bank_start_piles_removed"] = sum(
            max(0, amount - min(amount, current.get(serial, 0)))
            for serial, amount in piles
        )
        ctx.memory["cap_bank_start_piles_cleared"] = all(
            current.get(serial, 0) == 0 for serial, _amount in piles
        )

        box_start = ctx.memory.get("bank_box_start")
        box = self._bankbox(ctx)
        if (
            ctx.memory.get("cap_bank_baseline_goal_id") != goal_id
            and type(box_start) is int
            and box_start >= 0
            and box is not None
        ):
            ctx.memory["cap_bank_baseline_goal_id"] = goal_id
            ctx.memory["cap_bank_start_bank_gold"] = box_start
            ctx.memory["cap_bank_box_serial"] = box.serial

        baseline = ctx.memory.get("cap_bank_start_bank_gold")
        if (
            ctx.memory.get("cap_bank_baseline_goal_id") == goal_id
            and type(baseline) is int
            and baseline >= 0
            and box is not None
            and box.serial == ctx.memory.get("cap_bank_box_serial")
        ):
            observed = max(0, self._bankbox_gold(ctx) - baseline)
            prior = ctx.memory.get("cap_bank_confirmed", 0)
            if type(prior) is not int or prior < 0:
                return
            confirmed = max(prior, observed)
            ctx.memory["cap_bank_bank_delta"] = confirmed
            ctx.memory["cap_bank_confirmed"] = confirmed

    def _remember_lift(self, ctx: SkillContext, action: PickUp) -> None:
        goal_id = ctx.goal_id
        manifest = ctx.memory.get("cap_bank_start_piles")
        if (
            type(goal_id) is not int
            or not isinstance(manifest, tuple)
            or (action.serial, action.amount) not in manifest
        ):
            return
        lifted = ctx.memory.get("cap_bank_lifted_items", ())
        if not isinstance(lifted, tuple):
            return
        entry = (action.serial, action.amount)
        if entry not in lifted:
            ctx.memory["cap_bank_lifted_items"] = (*lifted, entry)
        ctx.memory["cap_bank_sent_goal_id"] = goal_id

    def _remember_drop(self, ctx: SkillContext, action: Drop) -> None:
        box_serial = ctx.memory.get("cap_bank_box_serial")
        lifted = ctx.memory.get("cap_bank_lifted_items")
        if type(box_serial) is not int or action.container != box_serial:
            return
        if not isinstance(lifted, tuple):
            return
        amount = next(
            (amount for serial, amount in lifted if serial == action.serial),
            None,
        )
        if type(amount) is not int or amount <= 0:
            return
        dropped = ctx.memory.get("cap_bank_dropped_items", ())
        if not isinstance(dropped, tuple):
            return
        entry = (action.serial, amount, action.container)
        if entry not in dropped:
            ctx.memory["cap_bank_dropped_items"] = (*dropped, entry)

    def step(self, ctx: SkillContext) -> SkillResult:
        obs = ctx.obs
        started = self._begin_goal(ctx)
        if started:
            self._observe_evidence(ctx)
        goal_id = ctx.goal_id
        banker = ctx.memory.get("banker_spot")
        if started:
            route = [tuple(point) for point in ctx.memory["cap_bank_route"]]
        else:
            route = (
                self._route(banker)
                if banker is not None
                else [(obs.player.pos.x, obs.player.pos.y)]
            )
        pending = ctx.memory.get("cap_bank_release_pending")
        if pending is not None:
            if (
                not isinstance(pending, tuple)
                or len(pending) != 4
                or not all(type(value) is int for value in pending)
            ):
                # Corrupt transaction ownership never becomes a yield point.
                return SkillResult(Status.RUNNING)
            serial, target, pack_before, bank_before = pending
            backpack = self._backpack(ctx)
            bankbox = self._bankbox(ctx)
            safe_containers = {
                container.serial
                for container in (backpack, bankbox)
                if container is not None
            }
            observed = next(
                (item for item in obs.items if item.serial == serial),
                None,
            )
            released = bool(
                (observed is not None and observed.container in safe_containers)
                or self._pack_gold(ctx) > pack_before
                or self._bankbox_gold(ctx) > bank_before
            )
            if released:
                ctx.memory.pop("cap_bank_release_pending", None)
                ctx.memory.pop("cap_bank_recovery_drop_sent", None)
                ctx.memory.pop("cap_bank_reopen_started", None)
            else:
                if bankbox is not None:
                    target = bankbox.serial
                    ctx.memory["cap_bank_release_pending"] = (
                        serial,
                        target,
                        pack_before,
                        bank_before,
                    )
                    return SkillResult(
                        Status.RUNNING,
                        Drop(serial=serial, container=target),
                    )
                if backpack is not None and not ctx.memory.get(
                    "cap_bank_recovery_drop_sent"
                ):
                    target = backpack.serial
                    ctx.memory["cap_bank_release_pending"] = (
                        serial,
                        target,
                        pack_before,
                        bank_before,
                    )
                    ctx.memory["cap_bank_recovery_drop_sent"] = True
                    return SkillResult(
                        Status.RUNNING,
                        Drop(serial=serial, container=target),
                    )

                # The Drop may already have reached a now-hidden bank box
                # across a bridge restart. Reopen/synchronize the bank while
                # retaining ownership, then the aggregate delta above can
                # prove either deposit or backpack recovery.
                if not ctx.memory.get("cap_bank_reopen_started"):
                    for key in (
                        "bank_leg",
                        "bank_stage",
                        "bank_banker",
                        "bank_find_wait",
                        "bank_popup_wait",
                        "bank_popup_total",
                        "bank_settle",
                    ):
                        ctx.memory.pop(key, None)
                    ctx.memory["mkt_phase"] = "bank"
                    ctx.memory["cap_bank_reopen_started"] = True
                recovery = self._bank_step(ctx, route)
                if recovery is None:
                    ctx.memory.pop("cap_bank_reopen_started", None)
                    return SkillResult(Status.RUNNING)
                return self._payout(ctx, recovery)
        if not started:
            return SkillResult(Status.RUNNING)
        if ctx.memory.get("cap_bank_finished_goal_id") == goal_id:
            return self._payout(ctx, SkillResult(Status.RUNNING))

        ctx.memory.setdefault("bs_stand", (obs.player.pos.x, obs.player.pos.y))
        phase = ctx.memory.get("mkt_phase", "craft")
        tick = ctx.memory["mkt_tick"] = ctx.memory.get("mkt_tick", 0) + 1
        if phase not in {"bank", "bank_return"}:
            phase = ctx.memory["mkt_phase"] = "bank"

        if phase == "bank":
            result = self._bank_step(ctx, route)
            if result is not None:
                # `_bank_step` establishes its trustworthy bank baseline on
                # the same tick it may emit the first PickUp.
                self._observe_evidence(ctx)
                if isinstance(result.action, PickUp):
                    self._remember_lift(ctx, result.action)
                if isinstance(result.action, Drop):
                    self._remember_drop(ctx, result.action)
                    ctx.memory["cap_bank_release_pending"] = (
                        result.action.serial,
                        result.action.container,
                        self._pack_gold(ctx),
                        self._bankbox_gold(ctx),
                    )
                return self._payout(ctx, result)
            self._observe_evidence(ctx)
            ctx.memory["bank_giveup_gold"] = self._pack_gold(ctx)
            ctx.memory["bank_giveup_tick"] = tick
            for key in self._CLEANUP_KEYS:
                ctx.memory.pop(key, None)
            phase = ctx.memory["mkt_phase"] = "bank_return"

        if phase == "bank_return":
            result = self._market_return_step(ctx, "bank_return", route)
            if result is not None:
                return self._payout(ctx, result)
            ctx.memory.pop("bank_return_leg", None)
            ctx.memory["mkt_phase"] = "craft"
            ctx.memory["cap_bank_finished_goal_id"] = goal_id
            stand = ctx.memory.get("bs_stand")
            if (
                isinstance(stand, (tuple, list))
                and len(stand) == 2
                and (obs.player.pos.x, obs.player.pos.y) == tuple(stand)
            ):
                ctx.memory["cap_bank_returned_goal_id"] = goal_id
            self._observe_evidence(ctx)

        # Deliberately do not fall through to Blacksmith.step(): completion is
        # evaluated from the next Observation before any hammer action can run.
        return self._payout(ctx, SkillResult(Status.RUNNING))


class BuyIngots(BlacksmithMarket):
    """Operation-specific vendor hands: buy iron ingots, never craft or sell.

    The exact mirror of ``SellDaggers`` inverted — gold LEAVES the pack, iron
    ingots ARRIVE — and the self-provisioning keystone: it lets a blacksmith
    replenish its finite crafting metal with earned gold, so the
    craft→sell→bank loop runs indefinitely without a GM re-gifting ingots.

    The capability keeps observation evidence scoped to the active goal frame.
    Merely opening a buy window, sending ``BuyItems``, or observing a gold
    change is insufficient on its own: completion requires the exact vendor
    offer observed, exactly the quoted cost to leave the pack, at least the
    bought amount of iron to arrive in it, the UI to clear, and the smith to
    return to the safe idle phase for the same ``goal_id``. Only iron ingots
    are ever bought — never the vendor's shields, armour, weapons, or tongs.
    """

    name = "buy_ingots"
    description = "Buy a fixed batch of iron ingots from the configured vendor and return."

    _CLEANUP_KEYS = (
        "buy_iron_start",
        "buy_gold_start",
        "buy_paid",
        "buy_leg",
        "buy_stage",
        "buy_vendor",
        "buy_find_wait",
        "buy_popup_wait",
        "buy_popup_total",
        "buy_ask_wait",
        "buy_confirm_wait",
    )

    def _begin_goal(self, ctx: SkillContext) -> bool:
        goal_id = ctx.goal_id
        if type(goal_id) is not int:
            return False
        if ctx.memory.get("cap_buy_goal_id") == goal_id:
            return True

        vendor = ctx.memory.get("vendor_spot")
        try:
            route = tuple(self._route(vendor))
        except (IndexError, TypeError, ValueError):
            return False
        if not route:
            return False

        ctx.memory["cap_buy_goal_id"] = goal_id
        ctx.memory["cap_buy_route"] = route
        ctx.memory["cap_buy_start_ingots"] = self._pack_iron(ctx)
        ctx.memory["cap_buy_start_gold"] = self._pack_gold(ctx)
        for key in (
            "cap_buy_sent_goal_id",
            "cap_buy_bought_ingots",
            "cap_buy_expected_cost",
            "cap_buy_offer",
            "cap_buy_ingot_delta",
            "cap_buy_gold_delta",
            "cap_buy_finished_goal_id",
            "cap_buy_returned_goal_id",
        ):
            ctx.memory.pop(key, None)
        return True

    def _observe_evidence(self, ctx: SkillContext) -> None:
        goal_id = ctx.goal_id
        if ctx.memory.get("cap_buy_sent_goal_id") != goal_id:
            return
        start_ingots = ctx.memory.get("cap_buy_start_ingots")
        start_gold = ctx.memory.get("cap_buy_start_gold")
        if type(start_ingots) is not int or type(start_gold) is not int:
            return
        # Iron arrived (pack rose), gold spent (pack fell) — both floored at 0 so
        # a one-tick observation lag on either side never records a negative.
        ctx.memory["cap_buy_ingot_delta"] = max(0, self._pack_iron(ctx) - start_ingots)
        ctx.memory["cap_buy_gold_delta"] = max(0, start_gold - self._pack_gold(ctx))

    def step(self, ctx: SkillContext) -> SkillResult:
        if not self._begin_goal(ctx):
            return SkillResult(Status.RUNNING)
        self._observe_evidence(ctx)
        goal_id = ctx.goal_id
        if ctx.memory.get("cap_buy_finished_goal_id") == goal_id:
            return self._payout(ctx, SkillResult(Status.RUNNING))

        obs = ctx.obs
        ctx.memory.setdefault("bs_stand", (obs.player.pos.x, obs.player.pos.y))
        route = [tuple(point) for point in ctx.memory["cap_buy_route"]]
        phase = ctx.memory.get("mkt_phase", "craft")
        tick = ctx.memory["mkt_tick"] = ctx.memory.get("mkt_tick", 0) + 1
        if phase not in {"buy", "buy_return"}:
            phase = ctx.memory["mkt_phase"] = "buy"

        if phase == "buy":
            result = self._buy_step(ctx, route)
            if result is not None:
                if isinstance(result.action, BuyItems):
                    # Snapshot the exact vendor offer this buy commits to — the
                    # resolved iron serial, the amount requested, and the live
                    # unit price read from the matching `ShopBuyEntry` — straight
                    # from the still-open window, never from any model text.
                    offer = self._buy_offer_for(obs.shop_buy, result.action)
                    if offer is not None:
                        serial, amount, price = offer
                        ctx.memory["cap_buy_sent_goal_id"] = goal_id
                        ctx.memory["cap_buy_bought_ingots"] = amount
                        ctx.memory["cap_buy_expected_cost"] = amount * price
                        ctx.memory["cap_buy_offer"] = (serial, amount, price)
                return self._payout(ctx, result)
            ctx.memory["buy_giveup_ingots"] = self._pack_iron(ctx)
            ctx.memory["buy_giveup_tick"] = tick
            for key in self._CLEANUP_KEYS:
                ctx.memory.pop(key, None)
            phase = ctx.memory["mkt_phase"] = "buy_return"

        if phase == "buy_return":
            result = self._market_return_step(ctx, "buy_return", route)
            if result is not None:
                return self._payout(ctx, result)
            ctx.memory.pop("buy_return_leg", None)
            ctx.memory["mkt_phase"] = "craft"
            ctx.memory["cap_buy_finished_goal_id"] = goal_id
            stand = ctx.memory.get("bs_stand")
            if (
                isinstance(stand, (tuple, list))
                and len(stand) == 2
                and (obs.player.pos.x, obs.player.pos.y) == tuple(stand)
            ):
                ctx.memory["cap_buy_returned_goal_id"] = goal_id
            self._observe_evidence(ctx)

        # Deliberately do not fall through to Blacksmith.step(): completion is
        # evaluated from the next Observation before any hammer action can run.
        return self._payout(ctx, SkillResult(Status.RUNNING))


class BuyToolCapability(BlacksmithMarket):
    """Operation-specific vendor hands: buy one replacement NON-stacking tool.

    The generalized base for `BuyTool` (blacksmith, buys tongs) and `BuyHatchet`
    (lumberjack, buys a hatchet) — subclasses only set `owned_tool_graphics`
    (the trigger: none of these in the pack), `offer_graphic` (the exact for-sale
    tool bought), `tool_price_estimate`, `vendor_spot_key`, `name`/`description`.

    The near-exact mirror of ``BuyIngots`` for a tool bought one at a time. A
    worker's tool wears out over a long run and breaks; today that silently
    stalls production (its craft/process capability goes unready with no working
    tool). This buys a fresh one with earned gold, closing a finite-supply GM
    dependency.

    The capability keeps observation evidence scoped to the active goal frame.
    Completion requires the exact tool offer observed, exactly the quoted cost to
    leave the pack, and a tool to ARRIVE where there was none (a 0->1 tool-count
    delta — verified by count, since tools don't stack), the UI to clear, and a
    safe return for the same ``goal_id``. Only the configured tool offer is ever
    bought — never the vendor's other stock. (The ``cap_toolbuy_*`` memory keys
    keep their legacy names.)
    """

    _CLEANUP_KEYS = (
        "toolbuy_tools_start",
        "toolbuy_gold_start",
        "toolbuy_paid",
        "toolbuy_leg",
        "toolbuy_stage",
        "toolbuy_vendor",
        "toolbuy_find_wait",
        "toolbuy_popup_wait",
        "toolbuy_popup_total",
        "toolbuy_ask_wait",
        "toolbuy_confirm_wait",
    )

    def _begin_goal(self, ctx: SkillContext) -> bool:
        goal_id = ctx.goal_id
        if type(goal_id) is not int:
            return False
        if ctx.memory.get("cap_toolbuy_goal_id") == goal_id:
            return True

        vendor = ctx.memory.get(self.vendor_spot_key)
        try:
            route = tuple(self._route(vendor))
        except (IndexError, TypeError, ValueError):
            return False
        if not route:
            return False

        ctx.memory["cap_toolbuy_goal_id"] = goal_id
        ctx.memory["cap_toolbuy_route"] = route
        ctx.memory["cap_toolbuy_start_tools"] = self._pack_tools(ctx)
        ctx.memory["cap_toolbuy_start_gold"] = self._pack_gold(ctx)
        for key in (
            "cap_toolbuy_sent_goal_id",
            "cap_toolbuy_bought_tools",
            "cap_toolbuy_expected_cost",
            "cap_toolbuy_offer",
            "cap_toolbuy_tool_delta",
            "cap_toolbuy_gold_delta",
            "cap_toolbuy_finished_goal_id",
            "cap_toolbuy_returned_goal_id",
        ):
            ctx.memory.pop(key, None)
        return True

    def _observe_evidence(self, ctx: SkillContext) -> None:
        goal_id = ctx.goal_id
        if ctx.memory.get("cap_toolbuy_sent_goal_id") != goal_id:
            return
        start_tools = ctx.memory.get("cap_toolbuy_start_tools")
        start_gold = ctx.memory.get("cap_toolbuy_start_gold")
        if type(start_tools) is not int or type(start_gold) is not int:
            return
        # A tool arrived (pack tool count rose), gold spent (pack gold fell) —
        # both floored at 0 so a one-tick observation lag never records negative.
        ctx.memory["cap_toolbuy_tool_delta"] = max(0, self._pack_tools(ctx) - start_tools)
        ctx.memory["cap_toolbuy_gold_delta"] = max(0, start_gold - self._pack_gold(ctx))

    def step(self, ctx: SkillContext) -> SkillResult:
        if not self._begin_goal(ctx):
            return SkillResult(Status.RUNNING)
        self._observe_evidence(ctx)
        goal_id = ctx.goal_id
        if ctx.memory.get("cap_toolbuy_finished_goal_id") == goal_id:
            return self._payout(ctx, SkillResult(Status.RUNNING))

        obs = ctx.obs
        ctx.memory.setdefault("bs_stand", (obs.player.pos.x, obs.player.pos.y))
        route = [tuple(point) for point in ctx.memory["cap_toolbuy_route"]]
        phase = ctx.memory.get("mkt_phase", "craft")
        tick = ctx.memory["mkt_tick"] = ctx.memory.get("mkt_tick", 0) + 1
        if phase not in {"toolbuy", "toolbuy_return"}:
            phase = ctx.memory["mkt_phase"] = "toolbuy"

        if phase == "toolbuy":
            result = self._toolbuy_step(ctx, route)
            if result is not None:
                if isinstance(result.action, BuyItems):
                    # Snapshot the exact vendor offer this buy commits to — the
                    # resolved tongs serial, the amount (one), and the live unit
                    # price read from the matching `ShopBuyEntry` — straight from
                    # the still-open window, never from any model text.
                    offer = self._tool_offer_for(obs.shop_buy, result.action)
                    if offer is not None:
                        serial, amount, price = offer
                        ctx.memory["cap_toolbuy_sent_goal_id"] = goal_id
                        ctx.memory["cap_toolbuy_bought_tools"] = amount
                        ctx.memory["cap_toolbuy_expected_cost"] = amount * price
                        ctx.memory["cap_toolbuy_offer"] = (serial, amount, price)
                return self._payout(ctx, result)
            ctx.memory["toolbuy_giveup_tools"] = self._pack_tools(ctx)
            ctx.memory["toolbuy_giveup_tick"] = tick
            for key in self._CLEANUP_KEYS:
                ctx.memory.pop(key, None)
            phase = ctx.memory["mkt_phase"] = "toolbuy_return"

        if phase == "toolbuy_return":
            result = self._market_return_step(ctx, "toolbuy_return", route)
            if result is not None:
                return self._payout(ctx, result)
            ctx.memory.pop("toolbuy_return_leg", None)
            ctx.memory["mkt_phase"] = "craft"
            ctx.memory["cap_toolbuy_finished_goal_id"] = goal_id
            stand = ctx.memory.get("bs_stand")
            if (
                isinstance(stand, (tuple, list))
                and len(stand) == 2
                and (obs.player.pos.x, obs.player.pos.y) == tuple(stand)
            ):
                ctx.memory["cap_toolbuy_returned_goal_id"] = goal_id
            self._observe_evidence(ctx)

        # Deliberately do not fall through to Blacksmith.step(): completion is
        # evaluated from the next Observation before any hammer action can run.
        return self._payout(ctx, SkillResult(Status.RUNNING))


class BuyTool(BuyToolCapability):
    """Blacksmith config: buy a replacement tongs (0x0FBB) from the `vendor_spot`
    Blacksmith NPC when no smith tool is in the pack. Inherits every default
    (`owned_tool_graphics=SMITH_TOOL_GRAPHICS`, `offer_graphic=SMITH_TONGS_GRAPHIC`,
    `vendor_spot_key="vendor_spot"`), so it is byte-identical to the B8 skill.
    """

    name = "buy_smith_tool"
    description = "Buy one replacement smithing tool from the configured vendor and return."
    #: This shard's tongs price (SBBlacksmith.cs GenericBuyInfo) — the readiness
    #: affordability estimate only; the buy reads the live entry price.
    tool_price_estimate: int = 13


#: Sentinel `_popup_click` returns when the open popup has no entry matching
#: the requested cliloc — module-level (not `BlacksmithMarket._NO_ENTRY`) so a
#: plain `is` check works the same as `Blacksmith._ARRIVED`'s own comparison.
_NO_ENTRY = object()
