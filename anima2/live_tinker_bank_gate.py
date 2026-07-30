"""Live gate: the tinker's bank deposit — reproducing forge1's wedge conditions.

The first live forge-pair run ended with an ADMITTED `bank_gold` goal that never
progressed (gold=140 > reserve=82, banked=0, for ~15 minutes) — a state invisible to
both the concordance suite (steady-state only) and the disagreement detector (no-goal
guard, by design). This gate forces exactly that situation and watches the bank FSM's
own stage keys tick by tick: a fresh TinkerLife at the calibrated trade spot, GRANTED
140g (stated plainly: this is an FSM diagnosis, not an economy claim — provenance is
the forge pair's job), a Tinker vendor and a Banker on the calibrated spots.

Verdict: `banked` — gold arrived in the bank BOX (live_bank_goal.py's own proof) and
the pack settled at exactly the reserve. If it wedges instead, the printed stage trace
IS the diagnosis the forge run couldn't give us.
"""

from __future__ import annotations

import sys
import time

from .control import GmControl
from .ipc_body import ResilientIpcBody
from .life_runner import banked_amount, enforce_gold_provenance, pack_amount, stage_shops
from .live_common import (
    GM_RELOGIN_COOLDOWN_S,
    fresh_suffix,
    login_throttle,
    print_gate_verdict,
    wipe_area,
)
from .persona import Persona
from .profession import PROFESSIONS, TRADE_SMITH_SPOT, VENDOR_SPOT, BANKER_SPOT
from .skills.hunt import GOLD_GRAPHIC
from .tinker_life import BANK_RESERVE, TinkerLife

HOST, PORT = "127.0.0.1", 2594
GRANT = 140  # forge1's exact wedge amount


def main() -> int:
    acct = f"animabank{fresh_suffix()}"
    with ResilientIpcBody.spawn(HOST, PORT, acct, acct, pump_ms=400) as body:
        serial = body.ready["player"]["serial"]
        login_throttle(GM_RELOGIN_COOLDOWN_S)
        with GmControl.spawn(HOST, PORT) as gm:
            gm.hide()
            tx, ty, tz = gm.stage(serial, *TRADE_SMITH_SPOT,
                                  skills=PROFESSIONS["tinker"].skills,
                                  items=["TinkerTools 999"])
            wipe_area(gm, tx, ty, radius=8, z=tz)
            enforce_gold_provenance(gm, body, serial)
            gm.command_on(f"[AddToPack Gold {GRANT}", serial)
            shop_serials: dict = {}
            routes, _ = stage_shops(
                gm, z=tz, anchor=(tx, ty), exclude=serial,
                spots={"vendor_spot": ("Tinker", VENDOR_SPOT[-1]),
                       "banker_spot": ("Banker", BANKER_SPOT[-1])},
                serials_out=shop_serials)
        life = TinkerLife(body=body, persona=Persona(name="Pim"), routes=routes)
        for m in (life.memory, life.econ_agent.memory):
            m["craft_spot"] = (tx, ty)
            # Identity pin: this gate's own sibling caught the resolver asking a
            # Tinker to open a bank box when the tie broke the wrong way.
            m["shop_serials"] = shop_serials
        life.set_leash((tx, ty), 2)
        print(f"staged: Pim@({tx},{ty}) gold={GRANT} reserve={BANK_RESERVE} — "
              f"bank_gold should fire and deposit {GRANT - BANK_RESERVE}g\n")

        last = ""
        banked = 0
        for tick in range(240):
            life.tick()
            obs = getattr(life.body, "last_obs", None)
            m = life.econ_agent.memory
            cur = life.econ_agent.goal_stack.current
            adm = cur.goal.params.get("capability") if cur else None
            banked = banked_amount(obs)
            line = (f"want={life.target_cap} adm={adm} stage={m.get('bank_stage')} "
                    f"leg={m.get('bank_leg')} attempts={m.get('bank_deposit_attempts')} "
                    f"held={m.get('bank_held')} gold={pack_amount(obs, GOLD_GRAPHIC)} "
                    f"banked={banked}")
            if line != last:
                print(f"  t{tick:03} {line}")
                last = line
            if banked > 0:
                break
            time.sleep(0.1)

        obs = getattr(life.body, "last_obs", None)
        flags = {
            "banked": banked > 0,
            "reserve_kept": pack_amount(obs, GOLD_GRAPHIC) == BANK_RESERVE,
        }
        return 0 if print_gate_verdict(flags, label="TINKER BANK GATE") else 1


if __name__ == "__main__":
    sys.exit(main())
