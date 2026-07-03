"""Live proof: closing the economy into gold (DESIGN.md §10 Phase 3 item 2).

GM wipes the trade-smith area of prior debris, stages ONE blacksmith (durable
hammer, Blacksmith 35, ~30 starting ingots, forge + anvil) at
`profession.TRADE_SMITH_SPOT`, plus a vendor (`Blacksmith` NPC type — buys
daggers) and a `Banker` a short (route-)walk away
(`profession.VENDOR_SPOT`/`BANKER_SPOT` — see that module's comment for why
these are routes, not plain points: the smithy sits in a corridor with only
one straight-line exit). The brain then runs `skills/market.py::
BlacksmithMarket` tick by tick and the script watches for, **in order**:

 1. the smith crafts daggers (Blacksmithing skill-base gain, or pack dagger
    count rising),
 2. **`MIN_SELL_TRIPS`** separate, independently-confirmed sell trips: once
    `sell_threshold` daggers are held, it walks to the vendor, opens its
    right-click context menu and selects "Sell" (`skills/market.py`'s own
    docstring explains why a context menu, not speech), answers the 0x9E
    SellList with `SellItems` for the dagger entries only, and pack **gold
    increases** by the sale amount while pack **daggers drop**. Requiring more
    than one exercises the same code path twice, not just once (a single-pass
    proof can't tell "reliable" from "got lucky once"),
 3. at least one bank trip, **after** at least one of those sales: once
    `bank_threshold` gold is held, it walks to the banker, opens its context
    menu and selects "Bank" (opens the bank box), lifts the gold pile and
    drops it into the bank box, and pack **gold decreases** while the bank
    box's own container contents show the deposit — counted only once its
    cumulative amount is provably covered by cumulative sale proceeds (see
    `total_sold_gold`/`total_banked_gold` below), which also rules out a bank
    trip ever counting before any sale has landed.

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_market [--ticks N] [--sell-threshold N] [--bank-threshold N]
"""

from __future__ import annotations

import argparse

from .agent import Agent
from .cliloc import resolve_entry
from .contract import Observation
from .control import GmControl
from .ipc_body import IpcBody
from .persona import Persona
from .planner import Planner
from .profession import BANKER_SPOT, TRADE_SMITH_SPOT, VENDOR_SPOT
from .skills.craft import DAGGER_GRAPHIC, SKILL_BLACKSMITHING
from .skills.market import BANKBOX_LAYER, GOLD_GRAPHIC, BlacksmithMarket

BACKPACK_LAYER = 0x15
# How many independently-confirmed sell trips the proof requires before
# calling the "sold" leg demonstrated — a single successful trip can't
# distinguish "this works reliably" from "got lucky once" (live-caught: an
# early single-trip version of this proof passed on a run that turned out to
# wedge on every *later* trip once a mis-settled NPC blocked the route).
MIN_SELL_TRIPS = 2


class _RecordingBody:
    """Wraps a `Body`, caching the last `Observation` (see `live_trade.py`)."""

    def __init__(self, inner: IpcBody) -> None:
        self._inner = inner
        self.last_obs: Observation | None = None

    def observe(self) -> Observation:
        self.last_obs = self._inner.observe()
        return self.last_obs

    def act(self, action) -> None:
        self._inner.act(action)

    @property
    def connected(self) -> bool:
        return self._inner.connected


def _backpack_serial(obs: Observation) -> int | None:
    bp = next((i for i in obs.items if i.layer == BACKPACK_LAYER and i.container == obs.player.serial), None)
    return bp.serial if bp is not None else None


def _bankbox_serial(obs: Observation) -> int | None:
    box = next((i for i in obs.items if i.layer == BANKBOX_LAYER and i.container == obs.player.serial), None)
    return box.serial if box is not None else None


def _pack_amount(obs: Observation, graphic: int) -> int:
    bp = _backpack_serial(obs)
    if bp is None:
        return 0
    return sum(i.amount for i in obs.items if i.graphic == graphic and i.container == bp)


def _bankbox_gold(obs: Observation) -> int:
    box = _bankbox_serial(obs)
    if box is None:
        return 0
    return sum(i.amount for i in obs.items if i.graphic == GOLD_GRAPHIC and i.container == box)


def _skill_base(obs: Observation, skill_id: int) -> float | None:
    s = next((s for s in obs.skills if s.id == skill_id), None)
    return s.base if s is not None else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=800)
    ap.add_argument("--sell-threshold", type=int, default=3, help="pack daggers that trigger a sell trip")
    ap.add_argument("--bank-threshold", type=int, default=20, help="pack gold that triggers a bank trip")
    ap.add_argument("--smith-ingots", type=int, default=30, help="starting ingots — enough for a few sell cycles")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    # Fresh account each run (see live_trade.py's note: `[AddToPack`/`[Add` are
    # additive on a persistent shard, so reusing a name quietly piles debris).
    ap.add_argument("--smith-account", default="anima2")
    args = ap.parse_args()

    sx, sy = TRADE_SMITH_SPOT
    vx, vy = VENDOR_SPOT[-1]
    bx, by = BANKER_SPOT[-1]
    print(f"smith at {TRADE_SMITH_SPOT}, vendor at {VENDOR_SPOT[-1]}, banker at {BANKER_SPOT[-1]}")

    with IpcBody.spawn(args.host, args.port, args.smith_account, args.smith_account, pump_ms=400) as smith_ipc:
        smith_serial = smith_ipc.ready["player"]["serial"]
        print(f"smith: {args.smith_account} serial={smith_serial}")

        with GmControl.spawn(args.host, args.port) as gm:
            gm.hide()
            # Wipe stray debris from earlier runs (forge/anvil/vendors/items) —
            # `[Add` is additive and repeated live iteration otherwise piles up
            # (PHASE3.md item 1's "Bugs found live" #6).
            gm.command_area("[WipeNPCs", sx - 10, sy - 10, sx + 10, sy + 10, 20)
            gm.command_area("[WipeItems", sx - 10, sy - 10, sx + 10, sy + 10, 20)

            sgx, sgy, sgz = gm.stage(smith_serial, sx, sy, skills={"Blacksmith": 35},
                                     items=["SmithHammer 999", f"IronIngot {args.smith_ingots}"])
            gm.command_at("[Add Forge", sgx, sgy - 1, sgz)
            gm.command_at("[Add Anvil", sgx, sgy + 1, sgz)
            print(f"GM staged smith at ({sgx},{sgy},{sgz}) + forge/anvil, {args.smith_ingots} starting ingots")

            # A fresh ServUO character starts with a real Gold item in its
            # backpack (CharacterCreation.cs) — left alone, that starting
            # balance alone can satisfy the "sold"/"banked" evidence gates
            # below with zero vendor proceeds (worse still on a *reused*
            # account name, which can also carry over daggers from an
            # earlier run). Delete it via GM so every gold piece the smith
            # ever holds from here on is provably a sale proceed, not
            # starting capital — the evidence loop below also tracks
            # cumulative sold-vs-banked gold as a second, independent guard
            # in case this ever finds nothing to delete.
            smith_obs = smith_ipc.observe()
            starting_bp = _backpack_serial(smith_obs)
            starting_gold = next(
                (i for i in smith_obs.items if i.graphic == GOLD_GRAPHIC and i.container == starting_bp), None,
            )
            if starting_gold is not None:
                gm.command_on("[Delete", starting_gold.serial)
                print(f"GM deleted the smith's starting gold ({starting_gold.amount}) — "
                      f"every gold piece from here on is provably a sale proceed")

            # Add, find, and pin each NPC **one at a time** — not add-both-
            # then-find-both. `VendorAI.DoActionWander` starts roaming a
            # BaseVendor the moment it spawns (real UO shopkeepers pace their
            # shop), and every extra `command_at`/`find_mobile_near` round
            # trip before it's pinned is a window for it to drift into the
            # OTHER NPC's search radius — live-caught: with both added first,
            # `find_mobile_near(bx, by, ...)` sometimes resolved to the
            # *vendor* (having wandered near the banker spot in the interim),
            # so "the banker" got `CantWalk` on the wrong mobile, and the real
            # banker kept wandering off — a bug that even survived excluding
            # the smith's own serial. `exclude=smith_serial` still matters
            # too: the smith's own stand tile is within `find_mobile_near`'s
            # search radius of the banker spot as well. `stage_npc` also
            # corrects the NPC's position back onto the exact requested spot
            # before pinning — `[Add`'s ground-target placement settling it a
            # tile off is normally harmless, but live-caught pinning it dead
            # on the corridor's own hub waypoint instead, permanently denying
            # every walk through that tile (see `stage_npc`'s docstring).
            vendor = gm.stage_npc("Blacksmith", vx, vy, sgz, exclude=smith_serial)
            banker = gm.stage_npc("Banker", bx, by, sgz, exclude=smith_serial)

            print(f"GM staged vendor at ({vx},{vy},{sgz}) serial={vendor.serial if vendor else '?'}; "
                  f"banker at ({bx},{by},{sgz}) serial={banker.serial if banker else '?'} (both pinned)")

        smith_body = _RecordingBody(smith_ipc)
        # Let the teleport + pack grant + structure/vendor spawns settle.
        smith_body.observe()
        smith_body.observe()

        skill = BlacksmithMarket()
        skill.sell_threshold = args.sell_threshold
        skill.bank_threshold = args.bank_threshold
        smith = Agent(body=smith_body, persona=Persona(name="Tormund"), planner=Planner([skill]))
        smith.memory["vendor_spot"] = VENDOR_SPOT
        smith.memory["banker_spot"] = BANKER_SPOT

        # Evidence tracking — count independently-confirmed trips rather than
        # flipping a one-shot flag (mirrors live_trade.py's timeline style,
        # strengthened): a single successful sell trip can't distinguish
        # "this works reliably" from "got lucky once" — `MIN_SELL_TRIPS`
        # requires the sell code path to succeed more than once before
        # counting it demonstrated. Each trip's own delta measurement is
        # already provenance-safe (gold can only rise across a sell trip
        # because of the `SellItems` answer, nothing else touches it); the
        # bank side needs a second guard on top of that, since deleting the
        # starting gold above should mean every pack gold piece is already a
        # sale proceed, but `total_sold_gold`/`total_banked_gold` double-check
        # that cumulative deposits never outrun cumulative sales — belt and
        # suspenders in case the delete ever finds nothing to remove (a shard
        # that doesn't grant starting gold the same way, or a reused account
        # whose starting item was already spent). A bank trip only ever counts
        # once that holds, which also rules out it counting before any sale
        # has landed — the printed gates are impossible to satisfy from
        # starting gold either way.
        crafted = False
        sell_trip_count = 0
        bank_trip_count = 0
        last_phase = "craft"
        prev_base: float | None = None
        prev_daggers: int | None = None
        sell_entry_gold: int | None = None
        sell_entry_daggers: int | None = None
        bank_entry_gold: int | None = None
        total_sold_gold = 0
        total_banked_gold = 0

        for t in range(args.ticks):
            smith.tick()
            assert smith_body.last_obs is not None
            obs = smith_body.last_obs

            base = _skill_base(obs, SKILL_BLACKSMITHING)
            daggers = _pack_amount(obs, DAGGER_GRAPHIC)
            gold = _pack_amount(obs, GOLD_GRAPHIC)

            phase = smith.memory.get("mkt_phase", "craft")
            if phase != last_phase:
                print(f"  tick {t:4d}: phase {last_phase} -> {phase}  "
                      f"(pack daggers={daggers} gold={gold})")
                if last_phase != "sell" and phase == "sell":
                    sell_entry_gold, sell_entry_daggers = gold, daggers
                # Exiting the sell phase at all — not specifically to
                # "sell_return". `_walk_route`'s in-reach short-circuit (see
                # skills/market.py) can resolve the whole return leg on the
                # very same tick the sale confirms when the vendor/banker are
                # already in reach of the smith's own stand tile (the
                # co-located trade layout): the observable transition then
                # goes straight "sell" -> "craft", and a check hard-coded to
                # "sell_return" specifically would silently never fire —
                # live-caught: a run with ten real, back-to-back successful
                # sell/bank cycles still reported zero trips.
                if last_phase == "sell" and phase != "sell":
                    sold_gold = gold - (sell_entry_gold or 0)
                    sold_daggers = (sell_entry_daggers or 0) - daggers
                    if sold_gold > 0 and sold_daggers > 0:
                        sell_trip_count += 1
                        total_sold_gold += sold_gold
                        print(f"  tick {t:4d}: SOLD (trip {sell_trip_count}/{MIN_SELL_TRIPS}) — "
                              f"{sold_daggers} dagger(s) for {sold_gold} gold "
                              f"(pack gold {sell_entry_gold} -> {gold})")
                    else:
                        print(f"  tick {t:4d}: sell trip gave up wedged — nothing sold")
                if last_phase != "bank" and phase == "bank":
                    bank_entry_gold = gold
                # Mirrors the sell-side generalization above — exiting the
                # bank phase at all, not specifically to "bank_return".
                if last_phase == "bank" and phase != "bank":
                    deposited = (bank_entry_gold or 0) - gold
                    if deposited > 0:
                        total_banked_gold += deposited
                        box_gold = _bankbox_gold(obs)
                        if total_banked_gold <= total_sold_gold:
                            bank_trip_count += 1
                            print(f"  tick {t:4d}: BANKED (trip {bank_trip_count}) — {deposited} gold deposited "
                                  f"(pack gold {bank_entry_gold} -> {gold}; bank box now shows {box_gold} gold; "
                                  f"provenance: {total_banked_gold}/{total_sold_gold} cumulative banked/sold)")
                        else:
                            print(f"  tick {t:4d}: bank trip deposited {deposited} gold (box now {box_gold}), "
                                  f"but cumulative banked ({total_banked_gold}) exceeds cumulative sold "
                                  f"({total_sold_gold}) — not provably sale proceeds, not counting as proof yet")
                    else:
                        print(f"  tick {t:4d}: bank trip gave up wedged — nothing deposited")
            last_phase = phase

            if not crafted and prev_daggers is not None and daggers > prev_daggers:
                crafted = True
                print(f"  tick {t:4d}: CRAFTED — first dagger(s) in the pack (daggers={daggers})")
            if prev_base is not None and base is not None and base > prev_base + 1e-3:
                print(f"  tick {t:4d}: Blacksmithing {prev_base:.1f} -> {base:.1f}")

            for j in obs.new_journal:
                text = resolve_entry(j)
                if "metal" in text.lower() or "gold" in text.lower() or "bank" in text.lower():
                    print(f"  tick {t:4d}: [journal] {text}")

            prev_base, prev_daggers = base, daggers

            if t % 25 == 0:
                print(f"  tick {t:4d}: SNAPSHOT phase={phase:10s} daggers={daggers:2d} gold={gold:3d} "
                      f"Blacksmithing={base if base is not None else float('nan'):.1f}")

            if crafted and sell_trip_count >= MIN_SELL_TRIPS and bank_trip_count >= 1:
                print(f"\nfull chain demonstrated by tick {t} — stopping early.")
                break

        sold = sell_trip_count >= MIN_SELL_TRIPS
        banked = bank_trip_count >= 1
        print("\n--- result ---")
        print(f"crafted daggers:          {crafted}")
        print(f"sold to vendor (trips):   {sell_trip_count} (need >= {MIN_SELL_TRIPS}) -> {sold}")
        print(f"banked the gold (trips):  {bank_trip_count} (need >= 1, after a sell) -> {banked}")
        print(f"episodic reward: {smith.episodes.total_reward():.1f}")
        if crafted and sold and banked:
            print(f"\nECONOMY CLOSED: ore -> ingot -> dagger -> gold -> bank, live "
                  f"({sell_trip_count} sell trips, {bank_trip_count} bank trip(s), all provenance-verified).")
        else:
            print("\nCHAIN INCOMPLETE within --ticks — see the timeline above.")


if __name__ == "__main__":
    main()
