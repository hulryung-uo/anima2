"""Live gate for BOUND 1 — the give-up ladder — on the flagship economy path.

Audit follow-up 24. Bound 1 of `WarriorLife`'s exit-edge hold is the only one of the
three that has never been exercised on a shard, and `docs/AUDIT-2026-07-29.md` records
that absence in five separate sections (§6.3, §7.4, §8.3, §16.3, §17.5, §18.3). Two
1200-tick forge runs failed to produce it by luck: the first admitted `buy_iron` on 60
samples and retired three frames on their DEADLINE instead, the second never needed to buy
at all. So this follows §7's conclusion for bound 3 — **reaching a bound needs forced
state, not patience** — and forces it.

**The forced state is one substitution: a vendor that cannot sell what we came for.**
`live_buy_goal.py` stages a Blacksmith, whose stock includes iron ingots, and the trip
succeeds. This stages a HEALER at the same spot: a real vendor with a real `Buy` context
entry and a real shop window, stocking bandages and no iron. Nothing is injected and no
memory is hand-written — the FSM meets a world in which its offer genuinely does not
exist, which is the condition `OFFER_REOPEN_ATTEMPTS` was written for.

What that single change is expected to walk, in order:

  1. the trip opens the window and finds no iron offer;
  2. `_buy_step` re-rolls — closing the window with an empty-list `BuyItems` (ServUO's
     EndVendorBuy) and marking `buy_closing_window` so the popup pre-check does not
     re-adopt the snapshot it just cancelled;
  3. after `OFFER_REOPEN_ATTEMPTS` it gives up and walks home;
  4. the return phase writes the neutral `cap_run_finished_goal_id`;
  5. `CapabilityGoalComplete` closes the frame FAILURE — `-> giveup`, bound 1 — instead of
     the frame riding 180 ticks to its deadline.

Steps 2 and 4 have also never run on a shard. The re-roll path and the
`{ns}_closing_window` marker are §13's and §15's entire subject matter and both live
sessions left them untouched (`cancels=0`, `buy_stage` only ever `popup`); the marker at
step 4 is follow-up 19, applied on 2026-08-09 and unvalidated live because the re-run
never entered the path. One gate, four gaps.

Usage::

    python -m anima2.live_buy_giveup_gate --suffix f24
"""

from __future__ import annotations

import argparse

from .agent import Agent
from .capabilities import CapabilityPolicy, capability_goal
from .capability_cognition import CapabilityCognition
from .cognition import ThreadedCognition
from .contract import BuyItems
from .control import GmControl
from .ipc_body import ResilientIpcBody
from .life_runner import frame_retirements
from .live_buy_goal import (
    _RecordingBody,
    _SequencedClient,
    _pack_items,
)
from .live_common import (
    GM_RELOGIN_COOLDOWN_S,
    login_throttle,
    print_gate_verdict,
    wipe_area,
)
from .persona import Persona
from .profession import PROFESSIONS
from .skills.craft import DAGGER_GRAPHIC
from .skills.market import GOLD_GRAPHIC, OFFER_REOPEN_ATTEMPTS, is_vendor_cancel
from .skills.smelt import INGOT_GRAPHICS

_PROFESSION = "blacksmith"
_CAPABILITY = "buy_ingots"
#: Same provenance-clean balance `live_buy_goal` uses. It must be ENOUGH — an unaffordable
#: batch would keep the gate refused and the trip would never start, which proves nothing
#: about the give-up ladder and everything about the affordability clause.
_STARTING_GOLD = 200
#: The substitution that forces the state. A Healer has a `Buy` entry and a shop window and
#: stocks bandages; it has never stocked an iron ingot. Anything else with a Buy menu and
#: no iron would do — the vendor's identity is not load-bearing, its STOCK is.
_VENDOR_NPC = "Healer"


def _run(args: argparse.Namespace) -> tuple[dict[str, bool], str]:
    account = args.account or f"animaf24{args.suffix}"
    password = args.password or account
    smith_x, smith_y = args.smith
    vendor_x, vendor_y = args.vendor

    with ResilientIpcBody.spawn(args.host, args.port, account, password,
                                bridge=args.bridge, pump_ms=args.pump_ms) as ipc:
        serial = ipc.ready["player"]["serial"]
        login_throttle(GM_RELOGIN_COOLDOWN_S)
        with GmControl.spawn(args.host, args.port, bridge=args.bridge) as gm:
            gm.hide()
            wipe_area(gm, smith_x, smith_y, radius=10, z=20)
            gx, gy, gz = gm.stage(serial, smith_x, smith_y,
                                  skills={"Blacksmith": 35}, items=["SmithHammer 999"])
            staged = [ipc.observe() for _ in range(3)][-1]
            removed = all(
                gm.command_on("[Delete", item.serial)
                for graphic in (GOLD_GRAPHIC, DAGGER_GRAPHIC, *INGOT_GRAPHICS)
                for item in _pack_items(staged, graphic)
            )
            added = gm.command_on(f"[AddToPack Gold {_STARTING_GOLD}", serial)
            vendor = gm.stage_npc(_VENDOR_NPC, vendor_x, vendor_y, gz, exclude=serial)
            print(f"GM staged a smith with NO iron and a {_VENDOR_NPC} that sells none; "
                  f"subject=0x{serial:X} pos=({gx},{gy},{gz}) gold={_STARTING_GOLD} "
                  f"vendor={getattr(vendor, 'serial', None)}; closing GM")

        body = _RecordingBody(ipc)
        for _ in range(4):
            body.observe()
        vendor_serial = vendor.serial if vendor is not None else None

        planner = PROFESSIONS[_PROFESSION].planner(capability_goals=True)
        cognition = ThreadedCognition(CapabilityCognition(_SequencedClient(), _PROFESSION))
        agent = Agent(body=body, persona=Persona(name="Tormund"), planner=planner,
                      cognition=cognition, cognition_interval=1, profession=_PROFESSION,
                      goal_policy=CapabilityPolicy(_PROFESSION))
        # A vendor route and nothing else, so `buy_ingots` is the only branch — same
        # closed fixture `live_buy_goal` uses, for the same reason.
        agent.memory["vendor_spot"] = ((vendor_x, vendor_y),)

        goal = capability_goal(_PROFESSION, _CAPABILITY)
        agent.tick()
        cognition.wait_idle(timeout=5.0)
        agent.tick()
        cognition.wait_idle(timeout=5.0)

        # --- watch, per tick: a retirement is an EDGE and a ~4s sampler would miss it ---
        watch = {
            "window_seen": False,        # the shop window actually opened
            "offer_absent": False,       # ...and genuinely had no iron in it
            "rerolls": 0,                # `buy_offer_reopens` high-water mark
            "closing_marked": False,     # the §15 marker was set at least once
            "cancels": 0,                # empty-list BuyItems (EndVendorBuy)
            "gave_up": False,            # the trip left the buy phase without iron
            "run_marker": False,         # follow-up 19's neutral marker was written
        }
        budget = None
        retired: tuple | None = None
        deadline_ticks = None
        for _ in range(args.ticks):
            agent.tick()
            obs = body.last_obs
            mem = agent.memory
            frame = agent.goal_stack.current
            if frame is not None and frame.goal == goal and budget is None:
                budget = frame.deadline_tick - frame.created_tick
                deadline_ticks = frame.deadline_tick
            if obs is not None and obs.shop_buy is not None:
                watch["window_seen"] = True
                if not any(e.graphic in INGOT_GRAPHICS or e.graphic == 0x1BF2
                           for e in obs.shop_buy.entries):
                    watch["offer_absent"] = True
            watch["rerolls"] = max(watch["rerolls"],
                                   int(mem.get("buy_offer_reopens", 0) or 0))
            if mem.get("buy_closing_window"):
                watch["closing_marked"] = True
            if mem.get("cap_run_finished_goal_id") is not None:
                watch["run_marker"] = True
            if watch["window_seen"] and mem.get("mkt_phase", "craft") == "craft" \
                    and mem.get("buy_stage") is None:
                watch["gave_up"] = True
            rows = [r for r in frame_retirements(agent) if r[1] == _CAPABILITY]
            if rows:
                retired = rows[0]
                break
        watch["cancels"] = sum(1 for rec in body.actions
                               if isinstance(rec.action, BuyItems)
                               and is_vendor_cancel(rec.action))

        iron_after = sum(i.amount for i in (body.last_obs.items if body.last_obs else [])
                         if i.graphic in INGOT_GRAPHICS)
        flags = {
            "schema_ready": bool(ipc.ready),
            "gm_fixture_staged": bool(removed and added and vendor is not None),
            # The forced state itself: a real window, genuinely without our offer. If this
            # is False the Healer stocked iron and the whole gate is meaningless.
            "vendor_window_opened_without_our_offer":
                watch["window_seen"] and watch["offer_absent"],
            # §13/§15's subject matter, never run on a shard until now.
            "reroll_path_walked": watch["rerolls"] > 0,
            "reroll_budget_respected": 0 < watch["rerolls"] <= OFFER_REOPEN_ATTEMPTS,
            "closing_window_marker_set": watch["closing_marked"],
            "window_cancelled_not_purchased": watch["cancels"] > 0,
            "nothing_was_bought": iron_after == 0,
            # Follow-up 19, applied 2026-08-09 and unvalidated live until here.
            "giveup_marker_written": watch["run_marker"],
            # BOUND 1.
            "frame_retired_through_the_giveup_ladder":
                retired is not None and retired[4] == "giveup",
            # The payoff: it must close EARLY, not by outliving its deadline.
            "retired_well_inside_its_deadline":
                retired is not None and budget is not None and retired[2] < budget,
        }
        detail = (f"vendor={vendor_serial} rerolls={watch['rerolls']}/"
                  f"{OFFER_REOPEN_ATTEMPTS} cancels={watch['cancels']} "
                  f"iron={iron_after} deadline_tick={deadline_ticks} "
                  f"retired={retired}")
        return flags, detail


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2594)
    p.add_argument("--account", default=None)
    p.add_argument("--password", default=None)
    p.add_argument("--suffix", default="f24")
    p.add_argument("--bridge", default=None)
    p.add_argument("--pump-ms", type=int, default=400)
    p.add_argument("--ticks", type=int, default=400)
    p.add_argument("--smith", type=int, nargs=2, default=(2609, 474))
    p.add_argument("--vendor", type=int, nargs=2, default=(2612, 474))
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        flags, detail = _run(args)
    except Exception as exc:  # noqa: BLE001 — a gate reports, it does not traceback
        print(f"[FLAG] BUY GIVE-UP GATE FAILED: {type(exc).__name__}: {exc}")
        return 1
    return 0 if print_gate_verdict(flags, label="BUY GIVE-UP GATE (bound 1)",
                                   detail=detail) else 1


if __name__ == "__main__":
    raise SystemExit(main())
