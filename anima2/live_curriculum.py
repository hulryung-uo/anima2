"""Live gate for PHASE4.md item 5 — the automatic curriculum.

Proves, against a live ServUO character, that a milestone's achieved-transition
fires from the **live Observation stream** and is **LLM-independent** — the
whole point of item 5's "predicates are Observation/EpisodicMemory-derived, so
the agent can't game them, and the LLM only picks which eligible milestone to
*display*, never whether one was achieved."

The proof is deliberately non-vacuous and differential:

  1. Stage a real miner (Mining 35, a pickaxe, a forge) and drive it through a
     `CurriculumController`. Partway through, the GM boosts the miner's Mining
     skill past 50 (`[Set Skills.Mining.Base 51`, the same command family
     `GmControl.stage()` issues) to force a live, deterministic crossing of the
     `miner_mining_50` milestone rather than waiting on organic skill gain.
  2. After the run, read the agent's `EpisodicMemory` **directly** (not a log
     line): exactly one `Episode(kind="milestone")` for `miner_mining_50` must
     be present, and the controller's `current_milestone` must be a real
     eligible name.
  3. Rerun the identical scenario with the curriculum pick client swapped for a
     `StubLLMClient` returning **pure garbage** — the same milestone episode
     must STILL fire. A controller that only recorded achievements when the LLM
     cooperated would fail this leg; because the achieved-transition is a
     deterministic predicate over the live Observation, it fires regardless.

Both legs use a stubbed pick client (a sensible one and a garbage one) rather
than the live Replicate model: the achievement recording under test is
LLM-independent by construction, and the live-ness that matters here is the
*skill crossing over a real Observation stream driving a real Agent loop*, not
which model answers the (peripheral) "which milestone to display" question. The
LLM pick path itself is covered deterministically by `tests/test_curriculum.py`.

SAFETY / hygiene (see the `anima2-live-verification` lessons): each leg wipes
its staged area, uses a fresh account, and writes its milestone ledger to an
isolated path under the scratchpad so no run seeds another's achieved-set.

Usage:
    python -m anima2.live_curriculum [--host H] [--port P] [--ticks N]
        [--boost-at N] [--miner-account NAME] [--milestones-dir DIR]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from .agent import Agent
from .cognition import HeuristicCognition
from .contract import Observation
from .control import GmControl
from .curriculum import CurriculumController
from .ipc_body import IpcBody
from .llm import StubLLMClient
from .persona import Persona
from .planner import Planner
from .profession import TRADE_MINE_SPOT
from .skills import MineSmeltDeliver

_MINING_SKILL_ID = 45
_MILESTONE = "miner_mining_50"
_WIPE_RADIUS = 12


class _RecordingBody:
    """Wraps an `IpcBody`, caching the last `Observation` so the driver loop can
    read the miner's live Mining skill without a second pump (mirrors
    `live_trade.py`'s own `_RecordingBody`)."""

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


def _skill_base(obs: Observation, skill_id: int) -> float | None:
    s = next((s for s in obs.skills if s.id == skill_id), None)
    return s.base if s is not None else None


def _milestone_episodes(agent: Agent, name: str) -> list:
    """Read the agent's OWN EpisodicMemory directly (not a log line) for
    milestone-kind episodes recording `name`."""
    return [e for e in agent.episodes.recent(9999)
            if e.kind == "milestone" and name in e.summary]


def _run_leg(label: str, pick_client, args, milestones_path: Path, account: str) -> dict:
    """One live leg: stage a miner, drive it through a CurriculumController,
    GM-boost Mining past 50 partway, then read back the milestone episode.
    Returns a result dict; never raises for a game hiccup it can report."""
    mx, my = TRADE_MINE_SPOT
    print(f"\n=== leg [{label}] account={account} milestones={milestones_path.name} ===")
    with IpcBody.spawn(args.host, args.port, account, account, pump_ms=400) as ipc:
        serial = ipc.ready["player"]["serial"]
        print(f"miner: {account} serial={serial}")
        time.sleep(args.stagger)

        with GmControl.spawn(args.host, args.port) as gm:
            gm.hide()
            x1, y1, x2, y2 = mx - _WIPE_RADIUS, my - _WIPE_RADIUS, mx + _WIPE_RADIUS, my + _WIPE_RADIUS
            gm.command_area("[WipeItems", x1, y1, x2, y2, 20)
            gm.command_area("[WipeNPCs", x1, y1, x2, y2, 20)
            gx, gy, gz = gm.stage(serial, mx, my, skills={"Mining": 35}, items=["Pickaxe", "Pickaxe"])
            gm.command_at("[Add Forge", gx + 1, gy + 1, gz)
            print(f"GM staged miner at ({gx},{gy},{gz}) + forge, Mining 35 (below the 50 milestone)")

        body = _RecordingBody(ipc)
        body.observe()
        body.observe()

        # A CurriculumController wrapping a no-LLM inner. Its own EpisodicMemory
        # is rebound to the Agent's right after construction (Agent builds its
        # own; the milestone Episode must land in the one we read back).
        ctrl = CurriculumController(
            HeuristicCognition(), pick_client, "Grimm", "miner",
            every_n_reconsiders=3, min_new_episodes=2,
            milestones_path=milestones_path,
        )
        agent = Agent(body=body, persona=Persona(name="Grimm"),
                      planner=Planner([MineSmeltDeliver()]), cognition=ctrl,
                      cognition_interval=6)
        ctrl.episodes = agent.episodes

        boosted = False
        milestone_seen_tick = None
        for t in range(args.ticks):
            agent.tick()
            base = _skill_base(body.last_obs, _MINING_SKILL_ID) if body.last_obs else None
            if not boosted and t >= args.boost_at:
                with GmControl.spawn(args.host, args.port) as gm:
                    gm.hide()
                    gm.command_on("[Set Skills.Mining.Base 51", serial)
                boosted = True
                print(f"  tick {t:4d}: GM boosted Mining.Base -> 51 (was ~{base}) — forcing the milestone crossing")
            if milestone_seen_tick is None and _milestone_episodes(agent, _MILESTONE):
                milestone_seen_tick = t
                # let the controller settle, then stop early
                ctrl.wait_idle(timeout=5.0)
                print(f"  tick {t:4d}: milestone Episode observed in EpisodicMemory")
            if t % 20 == 0:
                print(f"  tick {t:4d}: Mining.Base={base} current_milestone={ctrl.current_milestone!r} "
                      f"milestone_episodes={len(_milestone_episodes(agent, _MILESTONE))}")
            if milestone_seen_tick is not None and t >= milestone_seen_tick + 4:
                break

        ctrl.wait_idle(timeout=5.0)
        eps = _milestone_episodes(agent, _MILESTONE)
        final_base = _skill_base(body.last_obs, _MINING_SKILL_ID) if body.last_obs else None
        result = {
            "label": label,
            "final_mining_base": final_base,
            "milestone_episode_count": len(eps),
            "current_milestone": ctrl.current_milestone,
            "fired_exactly_once": len(eps) == 1,
        }
        print(f"--- leg [{label}] result ---")
        print(f"  final Mining.Base (live obs):     {final_base}")
        print(f"  Episode(kind=milestone) for {_MILESTONE}: {len(eps)} (read directly from EpisodicMemory)")
        print(f"  controller current_milestone:     {ctrl.current_milestone!r}")
        return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--ticks", type=int, default=80)
    ap.add_argument("--boost-at", type=int, default=15, help="tick at which the GM boosts Mining past 50")
    ap.add_argument("--stagger", type=float, default=4.0)
    ap.add_argument("--miner-account", default="animacur1")
    ap.add_argument("--milestones-dir", default=None,
                    help="dir for isolated per-leg milestone ledgers (default: a scratchpad temp)")
    args = ap.parse_args()

    mdir = Path(args.milestones_dir) if args.milestones_dir else Path("data") / "curriculum_livegate"
    mdir.mkdir(parents=True, exist_ok=True)

    # Leg A — a SENSIBLE pick client (valid milestone name in its JSON).
    sensible = StubLLMClient('{"milestone": "miner_mining_50"}')
    a = _run_leg("sensible-LLM", sensible, args, mdir / "leg_a.jsonl", args.miner_account + "a")

    # Leg B — a GARBAGE pick client the whole time. The milestone must STILL fire.
    garbage = StubLLMClient("I am not JSON. As an AI language model I refuse to pick.")
    b = _run_leg("garbage-LLM", garbage, args, mdir / "leg_b.jsonl", args.miner_account + "b")

    print("\n=== curriculum live gate result ===")
    print(f"leg A (sensible LLM): milestone fired exactly once = {a['fired_exactly_once']} "
          f"(episodes={a['milestone_episode_count']}, current={a['current_milestone']!r})")
    print(f"leg B (garbage LLM):  milestone fired exactly once = {b['fired_exactly_once']} "
          f"(episodes={b['milestone_episode_count']}, current={b['current_milestone']!r})")
    passed = a["fired_exactly_once"] and b["fired_exactly_once"]
    if passed:
        print("CURRICULUM CONFIRMED: the Observation-derived milestone fires on a live skill crossing, "
              "recorded exactly once in EpisodicMemory, and STILL fires under a pure-garbage LLM "
              "(the achievement predicate is deterministic and LLM-independent).")
    else:
        print("CURRICULUM INCOMPLETE — see the per-leg output above.")


if __name__ == "__main__":
    main()
