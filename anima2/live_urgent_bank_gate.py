"""Live gate: the tinker's POCKETS-FULL band — banking that outranks a ready craft.

forge4 showed the disease (a 1500-tick day carrying 210g while `bank_gold` sat ready
and starved: the patient bank branch lives BELOW craft and only fires in a supply gap,
which a healthy miner never opens). The band added above craft — bank when the pack
holds more than `reserve + BANK_TRIP_SURPLUS` — is offline-pinned by the concordance
lattice, but every live deposit so far landed in a state where the PATIENT branch would
also have fired (forge8 at 140g with no craft possible, forge14 at 175g with 3 iron —
below a batch). Neither is exclusive evidence.

So force the discriminating state, the way this project's other gates do: a tinker with
a FULL batch of iron (craft genuinely ready), no tongs, no ground delivery, and gold
above the band. In that state the two orders disagree by construction:

    patient order : tool > sell > fetch > CRAFT > bank > buy   -> craft_tongs
    with the band : tool > sell > fetch > BANK  > craft > buy  -> bank_gold

And the counterfactual runs on the SAME live observation rather than on a hand-copied
rule: re-ask the REAL `decide_mode` with `bank_reserve` raised so that the same gold
still clears the patient threshold but no longer clears the band's
(`reserve < gold <= reserve + BANK_TRIP_SURPLUS`). If that reading says `craft_tongs`
while the wired reading says `bank_gold`, the band — not the reserve, not the gap — is
what sent this tinker to the bank.

Verdict flags (all must hold):
  craft_ready   — the capability layer itself admits `craft_tongs` here (the state is
                  genuinely craft-capable, so the disagreement is real, not vacuous)
  urgent_pick   — the wired rule picks `bank_gold` in that state
  patient_would_craft — the same rule on the same observation, band out of reach, picks
                  `craft_tongs` (the exclusive discriminator)
  admitted_bank — the agent admitted `bank_gold`, so the rule steered behaviour
  deposited     — gold reached the bank BOX and the pack settled at exactly the reserve

Gold is GRANTED here (stated plainly, like the bank gate's own note): this is a
decision-order proof, not an economy claim — provenance is the forge pair's job.
"""

from __future__ import annotations

import sys
import time

from .capabilities import ready_capability_ids
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
from .profession import BANKER_SPOT, PROFESSIONS, TRADE_SMITH_SPOT, VENDOR_SPOT
from .skills.base import SkillContext
from .skills.hunt import GOLD_GRAPHIC
from .skills.tinkering import TinkerTongs
from .tinker_life import BANK_RESERVE, BANK_TRIP_SURPLUS, TinkerLife, decide_mode

HOST, PORT = "127.0.0.1", 2594
#: Above the band (reserve + surplus = 157) — and a full craft batch of iron beside it,
#: so the orders disagree.
GRANT = BANK_RESERVE + BANK_TRIP_SURPLUS + 18
#: Comfortably more than one batch (`craft_material_per_item * craft_batch`).
IRON = TinkerTongs.craft_material_per_item * TinkerTongs.craft_batch * 4
#: The counterfactual reserve: still under GRANT (patient branch eligible) but far
#: enough above it that GRANT <= reserve + surplus (band out of reach).
PATIENT_RESERVE = GRANT - BANK_TRIP_SURPLUS + 5


def main() -> int:
    acct = f"animaurgent{fresh_suffix()}"
    with ResilientIpcBody.spawn(HOST, PORT, acct, acct, pump_ms=400) as body:
        serial = body.ready["player"]["serial"]
        login_throttle(GM_RELOGIN_COOLDOWN_S)
        with GmControl.spawn(HOST, PORT) as gm:
            gm.hide()
            tx, ty, tz = gm.stage(serial, *TRADE_SMITH_SPOT,
                                  skills=PROFESSIONS["tinker"].skills,
                                  items=["TinkerTools 999", f"IronIngot {IRON}"])
            # A clean floor matters twice here: a stray ingot within pickup reach
            # would make `fetch_iron` — which outranks the band on purpose — the
            # honest winner, and the gate would prove nothing.
            wipe_area(gm, tx, ty, radius=8, z=tz)
            enforce_gold_provenance(gm, body, serial)
            gm.command_on(f"[AddToPack Gold {GRANT}", serial)
            routes, _ = stage_shops(
                gm, z=tz, anchor=(tx, ty), exclude=serial,
                spots={"vendor_spot": ("Tinker", VENDOR_SPOT[-1]),
                       "banker_spot": ("Banker", BANKER_SPOT[-1])})
        life = TinkerLife(body=body, persona=Persona(name="Pim"), routes=routes)
        for m in (life.memory, life.econ_agent.memory):
            m["craft_spot"] = (tx, ty)
        life.set_leash((tx, ty), 2)
        print(f"staged: Pim@({tx},{ty}) gold={GRANT} iron={IRON} tongs=0 "
              f"reserve={BANK_RESERVE} band>{BANK_RESERVE + BANK_TRIP_SURPLUS} — "
              f"craft is ready AND the pockets are full\n")

        # Settle one tick so the observation carries the staged pack, then judge the
        # decision on THAT observation before the agent acts on it.
        life.tick()
        obs = getattr(life.body, "last_obs", None)
        econ_mem = life.econ_agent.memory
        ctx = SkillContext(obs=obs, persona=life.persona, memory=econ_mem)
        ready = ready_capability_ids("tinker", ctx)
        wired = decide_mode(obs, econ_mem)
        patient_mem = dict(econ_mem, bank_reserve=PATIENT_RESERVE)
        patient = decide_mode(obs, patient_mem)
        print(f"  ready capabilities: {list(ready)}")
        print(f"  wired rule (reserve={BANK_RESERVE}): {wired}")
        print(f"  counterfactual (reserve={PATIENT_RESERVE}, band out of reach): {patient}")

        last = ""
        banked = 0
        admitted: set[str] = set()
        for tick in range(240):
            life.tick()
            obs = getattr(life.body, "last_obs", None)
            m = life.econ_agent.memory
            cur = life.econ_agent.goal_stack.current
            adm = cur.goal.params.get("capability") if cur else None
            if adm:
                admitted.add(adm)
            banked = banked_amount(obs)
            line = (f"want={life.target_cap} adm={adm} stage={m.get('bank_stage')} "
                    f"banker={m.get('bank_banker')} popups={m.get('bank_popup_total')} "
                    f"gold={pack_amount(obs, GOLD_GRAPHIC)} banked={banked}")
            if line != last:
                print(f"  t{tick:03} {line}")
                last = line
            if banked > 0:
                break
            time.sleep(0.1)

        obs = getattr(life.body, "last_obs", None)
        # Who did the FSM actually talk to? A one-tick popup giveup is the
        # `_NO_ENTRY` signature ("this banker has no Bank entry") — i.e. the
        # market mobile resolver picked the wrong NPC. Name the neighbours so
        # the next reader does not have to guess (staging verification is this
        # project's oldest lesson: identity, not just distance).
        for mob in sorted(getattr(obs, "mobiles", []), key=lambda mo: mo.distance)[:6]:
            print(f"  nearby: {mob.serial:#x} {mob.name!r} at ({mob.pos.x},{mob.pos.y}) "
                  f"d={mob.distance}")
        flags = {
            "craft_ready": "craft_tongs" in ready,
            "urgent_pick": wired == ("economy", "bank_gold"),
            "patient_would_craft": patient == ("economy", "craft_tongs"),
            "admitted_bank": "bank_gold" in admitted,
            "deposited": banked > 0,
            "reserve_kept": pack_amount(obs, GOLD_GRAPHIC) == BANK_RESERVE,
        }
        return 0 if print_gate_verdict(flags, label="URGENT BANK BAND GATE") else 1


if __name__ == "__main__":
    sys.exit(main())
