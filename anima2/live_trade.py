"""Live end-to-end trade-loop proof: two agents, one economy (DESIGN.md §10 Phase 3).

GM stages a miner (pickaxes, Mining 35, a forge at its ore bank) and a
blacksmith (tongs/hammer, forge+anvil, Blacksmith 35, deliberately only a
handful of ingots) co-located at the live-calibrated trade spot
(`profession.TRADE_MINE_SPOT`/`TRADE_SMITH_SPOT` — see that module for how it
was found). Both agents' fast loops then run tick-by-tick, round-robin (no
threads — keeps the printed evidence in a single, readable timeline), and the
script watches for, **in order**:

 1. the blacksmith crafts down to its deliberately-thin starting stock and
    stalls (no more Blacksmithing gain / a "not enough metal" cliloc),
 2. the miner mines, smelts, accumulates ingots past `deliver_threshold`, walks
    to the smithy, and `Drop`s them on the ground there,
 3. the blacksmith notices the ground pile and `PickUp`s it into its pack,
 4. the blacksmith **crafts again** — a Blacksmithing skill-base gain (or a
    successful MAKE cycle) *after* the pickup, proving the delivered metal is
    what unstuck it, not a GM top-up. No GM gifting sustains the loop past the
    initial stage: everything the smith crafts *after* the first stall came
    from the miner.

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_trade [--ticks N] [--deliver-threshold N] [--smith-ingots N]
"""

from __future__ import annotations

import argparse
import time

from .agent import Agent
from .cliloc import resolve_entry
from .contract import Observation
from .control import GmControl
from .ipc_body import IpcBody
from .persona import Persona
from .planner import Planner
from .profession import TRADE_MINE_SPOT, TRADE_SMITH_SPOT
from .skills import Blacksmith
from .skills.craft import MIN_INGOTS, NOT_ENOUGH_METAL_CLILOC
from .skills.smelt import INGOT_GRAPHICS, MineSmeltDeliver

MINING_SKILL_ID = 45
BLACKSMITHING_SKILL_ID = 7


class _RecordingBody:
    """Wraps a `Body`, caching the last `Observation` (see `live_smelt.py`) so the
    driver loop below can inspect it without paying for a second `observe()` pump
    on top of the one `Agent.tick()` already does — with *two* agents ticking
    every loop iteration, that doubling matters for wall-clock time."""

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
    bp = next((i for i in obs.items if i.layer == 0x15 and i.container == obs.player.serial), None)
    return bp.serial if bp is not None else None


def _pack_ingots(obs: Observation) -> int:
    bp = _backpack_serial(obs)
    if bp is None:
        return 0
    return sum(i.amount for i in obs.items if i.graphic in INGOT_GRAPHICS and i.container == bp)


def _ground_ingots_near(obs: Observation, radius: int = 8) -> int:
    return sum(i.amount for i in obs.items
              if i.graphic in INGOT_GRAPHICS and i.container is None and i.distance <= radius)


def _skill_base(obs: Observation, skill_id: int) -> float | None:
    s = next((s for s in obs.skills if s.id == skill_id), None)
    return s.base if s is not None else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=600)
    ap.add_argument("--deliver-threshold", type=int, default=8,
                    help="miner pack ingots that trigger a delivery run")
    ap.add_argument("--smith-ingots", type=int, default=15,
                    help="ingots the blacksmith starts with — deliberately thin, so it starves")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--stagger", type=float, default=4.0, help="seconds between logins (ServUO throttle)")
    # Account names auto-create on first login (ServUO), but `[AddToPack` is
    # additive — re-running against the *same* two names on a persistent shard
    # piles more tools/ingots onto whatever a previous run already left, which
    # can quietly undermine "the smith starts nearly dry". Pick fresh names for
    # a clean scenario each time (or reuse on purpose to test a warm start).
    ap.add_argument("--miner-account", default="anima0")
    ap.add_argument("--smith-account", default="anima1")
    args = ap.parse_args()

    mx, my = TRADE_MINE_SPOT
    sx, sy = TRADE_SMITH_SPOT

    print(f"miner at {TRADE_MINE_SPOT}, blacksmith at {TRADE_SMITH_SPOT} "
          f"(dist={max(abs(mx - sx), abs(my - sy))} tiles)")

    with IpcBody.spawn(args.host, args.port, args.miner_account, args.miner_account, pump_ms=400) as miner_ipc:
        miner_serial = miner_ipc.ready["player"]["serial"]
        print(f"miner: {args.miner_account} serial={miner_serial}")
        time.sleep(args.stagger)  # dodge the ServUO login throttle

        with IpcBody.spawn(args.host, args.port, args.smith_account, args.smith_account,
                           pump_ms=400) as smith_ipc:
            smith_serial = smith_ipc.ready["player"]["serial"]
            print(f"blacksmith: {args.smith_account} serial={smith_serial}")

            with GmControl.spawn(args.host, args.port) as gm:
                gm.hide()
                mgx, mgy, mgz = gm.stage(miner_serial, mx, my, skills={"Mining": 35},
                                        items=["Pickaxe", "Pickaxe"])
                gm.command_at("[Add Forge", mgx + 1, mgy + 1, mgz)
                print(f"GM staged miner at ({mgx},{mgy},{mgz}) + forge")

                sgx, sgy, sgz = gm.stage(smith_serial, sx, sy, skills={"Blacksmith": 35},
                                        items=["SmithHammer 999", f"IronIngot {args.smith_ingots}"])
                # North/south of the stand spot, not east/west — matches
                # profession.py's blacksmith `structures`: an anvil is a
                # solid, blocking static, and east/west is the miner's
                # approach corridor (live-observed to seal it off entirely).
                gm.command_at("[Add Forge", sgx, sgy - 1, sgz)
                gm.command_at("[Add Anvil", sgx, sgy + 1, sgz)
                print(f"GM staged blacksmith at ({sgx},{sgy},{sgz}) + forge/anvil, "
                      f"{args.smith_ingots} starting ingots")

            miner_body = _RecordingBody(miner_ipc)
            smith_body = _RecordingBody(smith_ipc)

            # Let the teleports + pack grants + structure spawns settle.
            miner_body.observe()
            miner_body.observe()
            smith_body.observe()
            smith_body.observe()

            miner_skill = MineSmeltDeliver()
            miner_skill.deliver_threshold = args.deliver_threshold
            miner = Agent(body=miner_body, persona=Persona(name="Grimm"), planner=Planner([miner_skill]))
            miner.memory["smithy_drop"] = TRADE_SMITH_SPOT  # the only wiring the deliver phase needs

            smith = Agent(body=smith_body, persona=Persona(name="Tormund"), planner=Planner([Blacksmith()]))

            # Evidence tracking — flip each flag the first time its condition is
            # observed, printing the moment it happens so the transcript reads as
            # a timeline, not just periodic snapshots.
            smith_stalled = False
            miner_delivered = False
            smith_picked_up = False
            smith_recrafted = False
            last_miner_phase = "mine"
            deliver_entry_ingots: int | None = None  # pack count observed on entering "deliver"
            prev_smith_base: float | None = None
            prev_smith_ingots: int | None = None
            post_pickup_ingots: int | None = None  # peak pack count right after a pickup

            for t in range(args.ticks):
                miner.tick()
                assert miner_body.last_obs is not None
                mobs = miner_body.last_obs
                m_phase = miner.memory.get("smelt_phase", "mine")
                if m_phase != last_miner_phase:
                    print(f"  tick {t:4d}: [miner] phase {last_miner_phase} -> {m_phase} "
                          f"(pack ingots={_pack_ingots(mobs)})")
                    if last_miner_phase != "deliver" and m_phase == "deliver":
                        deliver_entry_ingots = _pack_ingots(mobs)
                    if last_miner_phase == "deliver" and m_phase == "return":
                        # A wedged delivery leg also transitions deliver -> return
                        # (`MineSmeltDeliver._walk_toward` gives up rather than
                        # retrying forever) with nothing actually dropped — only
                        # flag DELIVERED if the pack really lost ingots over the
                        # leg, so this evidence line can't lie about a stalled trip.
                        dropped = (deliver_entry_ingots or 0) - _pack_ingots(mobs)
                        if dropped > 0:
                            miner_delivered = True
                            print(f"  tick {t:4d}: [miner] DELIVERED — dropped {dropped} "
                                  f"ingots at the smithy")
                        else:
                            print(f"  tick {t:4d}: [miner] deliver leg gave up wedged — "
                                  f"nothing dropped")
                last_miner_phase = m_phase
                for j in mobs.new_journal:
                    text = resolve_entry(j)
                    if j.cliloc in (501988, 501987, 501986) or "smelt" in text.lower():
                        print(f"  tick {t:4d}: [miner journal] {text}")

                smith.tick()
                assert smith_body.last_obs is not None
                sobs = smith_body.last_obs
                s_base = _skill_base(sobs, BLACKSMITHING_SKILL_ID)
                s_ingots = _pack_ingots(sobs)
                ground = _ground_ingots_near(sobs)

                for j in sobs.new_journal:
                    text = resolve_entry(j)
                    if j.cliloc == NOT_ENOUGH_METAL_CLILOC:
                        if not smith_stalled:
                            print(f"  tick {t:4d}: [smith journal] {text}  <- STALLED (out of metal)")
                        smith_stalled = True
                    elif "metal" in text.lower() or "blacksmith" in text.lower():
                        print(f"  tick {t:4d}: [smith journal] {text}")

                if prev_smith_ingots is not None and s_ingots < MIN_INGOTS and prev_smith_ingots >= MIN_INGOTS:
                    smith_stalled = True
                    print(f"  tick {t:4d}: [smith] pack ingots dropped below {MIN_INGOTS} "
                          f"({prev_smith_ingots} -> {s_ingots}) — about to stall")

                if prev_smith_ingots is not None and s_ingots > prev_smith_ingots:
                    print(f"  tick {t:4d}: [smith] pack ingots {prev_smith_ingots} -> {s_ingots} "
                          f"(picked up delivered ore; {ground} still on the ground nearby)")
                    if smith_stalled:
                        smith_picked_up = True
                        post_pickup_ingots = s_ingots

                # Two independent confirmations that the smith is crafting *from
                # the delivered metal*, not just holding it: a skill-base gain
                # (probabilistic per craft — may not fire on any given attempt)
                # and pack ingots actually being spent back down from the
                # post-pickup peak (deterministic — every successful MAKE costs
                # ingots regardless of the skill-gain roll). Either is proof.
                if prev_smith_base is not None and s_base is not None and s_base > prev_smith_base + 1e-3:
                    if smith_stalled and smith_picked_up and not smith_recrafted:
                        smith_recrafted = True
                        print(f"  tick {t:4d}: [smith] CRAFTED AGAIN from delivered metal "
                              f"(Blacksmithing {prev_smith_base:.1f} -> {s_base:.1f})")
                if (smith_picked_up and not smith_recrafted and post_pickup_ingots is not None
                        and s_ingots < post_pickup_ingots):
                    smith_recrafted = True
                    print(f"  tick {t:4d}: [smith] CONSUMED delivered metal on a new craft "
                          f"(pack ingots {post_pickup_ingots} -> {s_ingots})")

                prev_smith_base, prev_smith_ingots = s_base, s_ingots

                if t % 25 == 0:
                    print(f"  tick {t:4d}: SNAPSHOT miner phase={m_phase:8s} pack_ingots={_pack_ingots(mobs):3d} "
                          f"| smith pack_ingots={s_ingots:3d} ground_ingots={ground:2d} "
                          f"Blacksmithing={s_base if s_base is not None else float('nan'):.1f}")

                if smith_recrafted:
                    print(f"\nfull loop demonstrated by tick {t} — stopping early.")
                    break

            print("\n--- result ---")
            print(f"smith stalled (ran low on metal):     {smith_stalled}")
            print(f"miner delivered ingots to the smithy:  {miner_delivered}")
            print(f"smith picked delivered ingots up:      {smith_picked_up}")
            print(f"smith crafted again from that metal:   {smith_recrafted}")
            print(f"miner episodic reward: {miner.episodes.total_reward():.1f}  "
                  f"smith episodic reward: {smith.episodes.total_reward():.1f}")
            if smith_stalled and miner_delivered and smith_picked_up and smith_recrafted:
                print("\nTRADE LOOP CONFIRMED: goods flowed miner -> ground -> blacksmith -> craft.")
            else:
                print("\nTRADE LOOP INCOMPLETE within --ticks — see the timeline above.")


if __name__ == "__main__":
    main()
