"""PHASE5.md item 1's live verification gate: the gaming agent vs the honest
worker — the non-vacuous proof that `foundry/fitness.py` is independent of
self-report.

Two miners, identical GM staging (Mining 35, two pickaxes, staged on
different **viable** `profession.MINING_SPOTS` entries — `[0]`/`[3]`, both
live-confirmed to have reachable ore, see the `anima2-live-verification`
memory note; distinct spots so they don't share a draining `HarvestBank`).

- **Agent A** (honest): a bare `Mine()` session — probes and swings at real
  ore, gains real Mining skill.
- **Agent B** (rigged): a scripted `_RiggedSkill` whose `SkillResult.reward`
  is enormous every tick while it does a little harmless busy-work (alternates
  a `Walk`/`Say` so it isn't a trivial freeze) and **never touches its
  pickaxe** — no real mining, so its independently GM-verified Mining skill
  never moves.

Both run a **fixed, equal window** (`--ticks`, default 300; no early stop).
Then three rankings are printed, with the raw `[Get` readbacks as evidence:

 1. **self-reported** — `Agent.episodes.total_reward()` and each agent's own
    `SkillLibrary` ledger sum. Expected: B > A (the gameable metric).
 2. **independent fitness** — `foundry/fitness.py::compute_fitness` over the
    two `TrajectorySummary`s the `TrajectoryRecorder`/`TappedBody` pair
    recorded live. Expected: A > B (the server's own skill-gain readback,
    which B's own code can't forge).
 3. **channel-(a)-only** — the same computation with `channel_b=False`
    (channel (b) excluded entirely). Expected: A > B, UNCHANGED — proving the
    load-bearing signal doesn't secretly depend on the in-process tap
    (PHASE5.md item 1's own decisive requirement).

Post-run, a **fresh** `GmControl` connection (never the one used during the
run) re-reads both subjects' final `Skills.Mining.Base`/`TotalGold`/`Hits` and
cross-checks them against what the in-run recorder captured — the same
"fresh channel, never the live process's own memory" discipline PHASE4.md
items 3-5 established.

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_fitness_gate [--ticks N] [--suffix TAG]
"""

from __future__ import annotations

import argparse
import time

from .agent import Agent
from .contract import Say, Walk
from .control import GmControl
from .foundry.fitness import compute_fitness
from .foundry.trajectory import TrajectoryRecorder, TappedBody
from .ipc_body import IpcBody
from .persona import Persona
from .planner import Planner
from .profession import MINING_SPOTS
from .skill_library import SkillLibrary
from .skills import Mine
from .skills.base import Skill, SkillContext, SkillResult, Status

SPOT_A = MINING_SPOTS[0]  # (2567, 493) — confirmed-live ore
SPOT_B = MINING_SPOTS[3]  # (2551, 420) — confirmed-live ore, distinct resource bank

#: Generous enough to catch either spot's own debris; small enough to stay
#: clear of every other calibrated spot on the ridge (mirrors
#: `live_trade.py::WIPE_RADIUS`).
WIPE_RADIUS = 10

#: Rigged reward, deliberately absurd — dwarfs anything an honest Mine()
#: session's own skill-gain reward could produce over the same window.
RIGGED_REWARD = 1000.0


class _RiggedSkill(Skill):
    """Live-gate adversarial test double ONLY (PHASE5.md item 1) — not
    exported from `anima2/skills`, not a reusable skill. Reports an enormous
    `SkillResult.reward` every tick while doing deliberately harmless
    busy-work: alternates a `Walk` and a `Say` (two distinct action groups,
    so it isn't a trivial freeze the liveness gate alone would catch — the
    proof this live gate makes is about `skill_term`, not about liveness)
    and never opens its pack or swings its pickaxe, so its independently
    GM-verified Mining skill never moves.
    """

    name = "rigged"
    description = "Adversarial fixture: inflates its own SkillResult.reward without doing real work."

    def step(self, ctx: SkillContext) -> SkillResult:
        tick = ctx.memory.get("rigged_tick", 0)
        ctx.memory["rigged_tick"] = tick + 1
        if tick % 4 == 3:
            action = Say(text="mining sure is hard work!")
        else:
            action = Walk(dir=tick % 8, run=False)
        return SkillResult(Status.RUNNING, action, reward=RIGGED_REWARD)


def _run_agent(
    args: argparse.Namespace, *, label: str, ipc: IpcBody, spot: tuple[int, int],
    skill, skill_lib: SkillLibrary, profession: str, gm: GmControl,
) -> dict:
    x, y = spot
    print(f"\n=== staging {label} at {spot} ===")
    x1, y1, x2, y2 = x - WIPE_RADIUS, y - WIPE_RADIUS, x + WIPE_RADIUS, y + WIPE_RADIUS
    gm.command_area("[WipeItems", x1, y1, x2, y2, 20)
    gm.command_area("[WipeNPCs", x1, y1, x2, y2, 20)

    # Body lifetime is owned by main() so the post-run cross-check can
    # still `[Get` the subject — a logged-out mobile can't be read, which
    # is exactly what the first gate run's N/A cross-check hit.
    serial = ipc.ready["player"]["serial"]
    print(f"{label}: serial={serial}")
    gx, gy, gz = gm.stage(serial, x, y, skills={"Mining": 35}, items=["Pickaxe", "Pickaxe"])
    print(f"{label} staged at ({gx},{gy},{gz})")

    # Let the teleport + pack grant settle before the window starts.
    ipc.observe()
    ipc.observe()

    recorder = TrajectoryRecorder(gm, subject_serial=serial, skill_names=("Mining",))
    recorder.start()
    pre_mining = recorder.summary.skills[45].first
    pre_gold = recorder.summary.gold_start
    pre_alive = recorder.summary.alive_start
    print(f"{label} channel (a) WINDOW-START [Get: Skills.Mining.Base={pre_mining} "
          f"TotalGold={pre_gold} alive={pre_alive}")

    tapped = TappedBody(ipc, recorder)
    agent = Agent(
        body=tapped, persona=Persona(name=label), planner=Planner([skill]),
        skill_library=skill_lib, profession=profession,
    )
    for t in range(args.ticks):
        agent.tick()
        if t % 50 == 0:
            print(f"  {label} tick {t:4d}: episodes_reward={agent.episodes.total_reward():.1f}")

    summary = recorder.finish()
    post_mining = summary.skills[45].last
    post_gold = summary.gold_end
    post_alive = summary.alive_end
    print(f"{label} channel (a) WINDOW-END   [Get: Skills.Mining.Base={post_mining} "
          f"TotalGold={post_gold} alive={post_alive}")
    print(f"{label} channel (a) Mining gain = {summary.skill_gain_total:.2f}  "
          f"gold delta = {summary.gold_delta}")
    print(f"{label} channel (b) taps: actions={dict(summary.action_counts)} "
          f"confirmed={summary.steps_confirmed} denied={summary.steps_denied} "
          f"items_into_pack={len(summary.items_into_pack)}")

    return {
        "label": label,
        "serial": serial,
        "summary": summary,
        "episodes_total_reward": agent.episodes.total_reward(),
        "episodes_recorded": agent.episodes.total_recorded,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=300)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--suffix", default=None, help="account-name suffix (default: unix time, for freshness)")
    ap.add_argument("--ledger-path", default=None)
    args = ap.parse_args()

    suffix = args.suffix or str(int(time.time()) % 1_000_000)
    account_a = f"fitgatea{suffix}"
    account_b = f"fitgateb{suffix}"

    ledger_path = args.ledger_path or f"data/fitness_gate_ledger_{suffix}.jsonl"
    skill_lib = SkillLibrary(ledger_path=ledger_path)

    # Subject bodies are owned HERE (not inside _run_agent) so both are still
    # ONLINE for the post-run cross-check — a logged-out mobile can't be
    # `[Get`-read (the first gate run hit exactly that: N/A cross-checks).
    # The shard has exactly one GM account, so the fresh cross-check
    # connection is opened only AFTER the run's own GM logs out.
    with IpcBody.spawn(args.host, args.port, account_a, account_a, pump_ms=400) as ipc_a:
        time.sleep(4)  # ServUO login throttle
        with IpcBody.spawn(args.host, args.port, account_b, account_b, pump_ms=400) as ipc_b:
            time.sleep(4)
            with GmControl.spawn(args.host, args.port) as gm:
                gm.hide()
                result_a = _run_agent(
                    args, label="A(honest)", ipc=ipc_a, spot=SPOT_A, skill=Mine(),
                    skill_lib=skill_lib, profession="miner_a", gm=gm,
                )
                result_b = _run_agent(
                    args, label="B(rigged)", ipc=ipc_b, spot=SPOT_B, skill=_RiggedSkill(),
                    skill_lib=skill_lib, profession="miner_b", gm=gm,
                )
            # Run GM logged out; subjects still online. Keep them pumped so the
            # server keeps their sessions alive, then read them back through a
            # FRESH GM connection (the independent post-run channel).
            time.sleep(8)  # let the GM account clear the login throttle
            ipc_a.observe()
            ipc_b.observe()
            print("\n=== post-run cross-check: FRESH GmControl connection (subjects still online) ===")
            with GmControl.spawn(args.host, args.port) as fresh_gm:
                fresh_gm.hide()
                for result in (result_a, result_b):
                    serial = result["serial"]
                    mining = fresh_gm.get_property_value("Skills.Mining.Base", serial)
                    gold = fresh_gm.get_property_value("TotalGold", serial)
                    recorded_last = result["summary"].skills[45].last
                    match = (mining == recorded_last) if mining is not None else "N/A"
                    result["xcheck"] = {"mining": mining, "gold": gold, "match": match}
                    print(f"{result['label']} FRESH [Get: Skills.Mining.Base={mining} TotalGold={gold} "
                          f"| in-run recorder last={recorded_last} (match={match})")

    summary_a, summary_b = result_a["summary"], result_b["summary"]

    # --- ranking 1: self-reported --------------------------------------------
    stats_a = skill_lib.stats("mine", "miner_a")
    stats_b = skill_lib.stats("rigged", "miner_b")
    self_a = result_a["episodes_total_reward"]
    self_b = result_b["episodes_total_reward"]
    ledger_sum_a = stats_a.mean_reward * stats_a.count
    ledger_sum_b = stats_b.mean_reward * stats_b.count

    print("\n=== RANKING 1: self-reported (episodes.total_reward() / skill_ledger sum) ===")
    print(f"A(honest): episodes_total_reward={self_a:.1f}  ledger_sum={ledger_sum_a:.1f} (n={stats_a.count})")
    print(f"B(rigged): episodes_total_reward={self_b:.1f}  ledger_sum={ledger_sum_b:.1f} (n={stats_b.count})")
    self_report_ranks_b_above_a = self_b > self_a
    print(f"self-report ranks B > A: {self_report_ranks_b_above_a}  (the gameable metric)")

    # --- ranking 2: independent fitness (full, both channels) ----------------
    fitness_a_full = compute_fitness(summary_a, channel_b=True)
    fitness_b_full = compute_fitness(summary_b, channel_b=True)
    print("\n=== RANKING 2: independent fitness (foundry/fitness.py, full — both channels) ===")
    print(f"A(honest): total={fitness_a_full.total:.4f}  skill_term={fitness_a_full.skill_term:.4f}  "
          f"gate={fitness_a_full.viability_gate:.4f}  skill_gain_rate={fitness_a_full.skill_gain_rate:.4f}/hr")
    print(f"B(rigged): total={fitness_b_full.total:.4f}  skill_term={fitness_b_full.skill_term:.4f}  "
          f"gate={fitness_b_full.viability_gate:.4f}  skill_gain_rate={fitness_b_full.skill_gain_rate:.4f}/hr")
    independent_ranks_a_above_b = fitness_a_full.total > fitness_b_full.total
    print(f"independent fitness ranks A > B: {independent_ranks_a_above_b}")

    # --- ranking 3: channel-(a)-only ------------------------------------------
    fitness_a_chan_a = compute_fitness(summary_a, channel_b=False)
    fitness_b_chan_a = compute_fitness(summary_b, channel_b=False)
    print("\n=== RANKING 3: channel-(a)-only fitness (channel (b) EXCLUDED) ===")
    print(f"A(honest): total={fitness_a_chan_a.total:.4f}  skill_term={fitness_a_chan_a.skill_term:.4f}  "
          f"(Mining {summary_a.skills[45].first:.1f} -> {summary_a.skills[45].last:.1f})")
    print(f"B(rigged): total={fitness_b_chan_a.total:.4f}  skill_term={fitness_b_chan_a.skill_term:.4f}  "
          f"(Mining {summary_b.skills[45].first:.1f} -> {summary_b.skills[45].last:.1f})")
    channel_a_only_ranks_a_above_b = fitness_a_chan_a.total > fitness_b_chan_a.total

    print(f"channel-(a)-only fitness ranks A > B: {channel_a_only_ranks_a_above_b}")

    print("\n=== GATE VERDICT ===")
    print(f"[FLAG] self_report_ranks_b_above_a = {self_report_ranks_b_above_a}")
    print(f"[FLAG] independent_fitness_ranks_a_above_b = {independent_ranks_a_above_b}")
    print(f"[FLAG] channel_a_only_ranks_a_above_b = {channel_a_only_ranks_a_above_b}")
    passed = self_report_ranks_b_above_a and independent_ranks_a_above_b and channel_a_only_ranks_a_above_b
    print(f"[FLAG] GATE {'PASSED' if passed else 'FAILED'}: divergence between self-report and "
          f"independent fitness, surviving channel-(b) exclusion: {passed}")


if __name__ == "__main__":
    main()
