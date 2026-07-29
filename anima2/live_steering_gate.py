"""Live gate: a REAL LLM steers a Life's choice, inside the closed vocabulary.

Audit #8's live half. The steered pipeline run proved the plumbing (client built, the
mage lived a full loop) but logged ZERO consults — the 2+ candidate overlap the
steering waits for never arose naturally in that session. This gate does what the
project's own staging discipline says to do: FORCE the state under test instead of
waiting for it. A fresh mage is staged directly into the overlap — reagents below the
reorder line, gold enough for a batch but under the fetch cap, a delivered purse on
the ground within pickup reach — so `decide_candidates` returns BOTH `buy_reagent`
and `fetch_gold` from tick one.

Verdict flags (all must hold):
  consulted      — at least one steering consult reached the real LLM
  in_set         — every consult's choice was inside the candidate list (closed
                   vocabulary: an invalid answer falls back and is logged used=False,
                   so this asserts on the CHOSEN value, not on the model behaving)
  llm_pick       — at least one consult was a genuine LLM pick (used=True), so the
                   gate is not vacuously passed by fallbacks alone
  goal_followed  — the goal actually admitted matches a consult's choice (steering
                   steered the agent, not just a log)

Runs on the calibrated MAGE plaza spots (village.MAGE_*), one agent, ~2-4 minutes.
"""

from __future__ import annotations

import sys
import time

from .control import GmControl
from .ipc_body import ResilientIpcBody
from .life_runner import enforce_gold_provenance, stage_shops
from .live_common import (
    GM_RELOGIN_COOLDOWN_S,
    fresh_suffix,
    login_throttle,
    print_gate_verdict,
    wipe_area,
)
from .mage_life import LOW_REAGENTS, REAGENT_BATCH_COST, MageLife
from .persona import Persona
from .profession import PROFESSIONS
from .village import MAGE_BANKER, MAGE_STAND, MAGE_VENDOR

HOST, PORT = "127.0.0.1", 2594
#: Enough for a reagent batch, comfortably under FETCH_GOLD_PACK_CAP (180).
STAGE_GOLD = REAGENT_BATCH_COST + 40
#: The delivered purse, dropped beside the stand.
PURSE = 140


def main() -> int:
    acct = f"animasteer{fresh_suffix()}"
    with ResilientIpcBody.spawn(HOST, PORT, acct, acct, pump_ms=400) as body:
        serial = body.ready["player"]["serial"]
        login_throttle(GM_RELOGIN_COOLDOWN_S)
        with GmControl.spawn(HOST, PORT) as gm:
            gm.hide()
            mx, my, mz = gm.stage(
                serial, *MAGE_STAND, skills=PROFESSIONS["mage"].skills,
                items=["Spellbook", f"SulfurousAsh {LOW_REAGENTS - 5}"])
            wipe_area(gm, mx, my, radius=8, z=mz)
            enforce_gold_provenance(gm, body, serial)
            gm.command_on(f"[AddToPack Gold {STAGE_GOLD}", serial)
            routes, _ = stage_shops(
                gm, z=mz, anchor=(mx, my), exclude=serial,
                spots={"mage_vendor_spot": ("Mage", MAGE_VENDOR),
                       "banker_spot": ("Banker", MAGE_BANKER)})
            # The overlap's second half: a purse already delivered, in pickup reach.
            gm.command_at(f"[Add Gold {PURSE}", mx + 1, my + 1, mz)
            print(f"staged: mage@({mx},{my}) ash={LOW_REAGENTS - 5} gold={STAGE_GOLD} "
                  f"purse={PURSE} on the ground — both branches admissible")

        life = MageLife(body=body, persona=Persona(name="Elara"), routes=routes,
                        steering="llm")
        life.set_leash((mx, my), 3)

        admitted: list[str] = []
        for _ in range(60):
            life.tick()
            cur = life.econ_agent.goal_stack.current
            if cur is not None:
                cap = cur.goal.params.get("capability")
                if cap and (not admitted or admitted[-1] != cap):
                    admitted.append(cap)
            if life.steering_log and admitted:
                break
            time.sleep(0.2)

        log = list(life.steering_log)
        for i, (cands, chosen, used) in enumerate(log):
            print(f"  consult#{i + 1}: {list(cands)} -> {chosen} "
                  f"({'LLM' if used else 'fallback'})")
        print(f"  goals admitted, in order: {admitted}")

        flags = {
            "consulted": bool(log),
            "in_set": all(chosen in cands for cands, chosen, _ in log) if log else False,
            "llm_pick": any(used for _, _, used in log),
            "goal_followed": bool(log and admitted
                                  and any(chosen == admitted[0]
                                          for _, chosen, _ in log[:3])),
        }
        return 0 if print_gate_verdict(flags, label="STEERING GATE") else 1


if __name__ == "__main__":
    sys.exit(main())
