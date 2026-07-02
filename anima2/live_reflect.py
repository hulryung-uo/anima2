"""Live reflection-loop proof: LLM cognition + periodic reflection while mining.

1. Spawn the agent body (animatest) and stage a miner scenario via the Control
   plane (pickaxe in pack, Mining 35, teleport to the Minoc ridge) — same setup as
   `live_mine.py`.
2. Run the brain with `ThreadedCognition(ReflectingCognition(LLMCognition(...),
   LLMReflection(...)))`: an LLM sets goals/speech *and* periodically reflects over
   episodic memory into short insights — all off the fast loop (PHASE2.md B1).
   Falls back to `HeuristicCognition` + `HeuristicReflection` (no LLM) if no LLM is
   configured, so the loop still demonstrates reflection offline.
3. Print every insight produced, so the reflection loop's output is directly
   observable (not just "it ran").
4. Wires a real `wiki.Wiki` (default root: `../uowiki/src/content/docs`,
   `--wiki-root`/`--no-wiki` override) into both `LLMCognition` and
   `LLMReflection` — the Phase 2 close-out item (PHASE2.md B1: semantic memory).
   `_TracingClient` prints every "Wiki — <title>: ..." line that actually made it
   into a situation prompt, so a real wiki excerpt reaching cognition is directly
   observable in this script's output (independent of whether the LLM's reply
   itself parses — see `_TracingClient`'s docstring).

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_reflect [--ticks N] [--wiki-root PATH] [--no-wiki]
"""

from __future__ import annotations

import argparse

from .agent import Agent
from .cognition import (
    HeuristicCognition,
    HeuristicReflection,
    LLMCognition,
    LLMReflection,
    ReflectingCognition,
    ThreadedCognition,
)
from .control import GmControl
from .ipc_body import IpcBody
from .llm import LLMClient, ReplicateClient
from .persona import Persona
from .planner import Planner
from .skills import GoTo, Mine, SpeakPending
from .wiki import Wiki


class _TracingClient:
    """Wraps an `LLMClient` to print every "Wiki — <title>: ..." line found in a
    situation prompt before forwarding the call unchanged.

    Live evidence that a real wiki excerpt reaches the cognition prompt
    (PHASE2.md B1) has to be the *prompt content*, not the reply — the Replicate
    qwen3 client's well-known JSON-format flakiness (see PHASE2.md's reflection
    write-up) is irrelevant to what we're proving here. This wrapper makes that
    prompt content directly observable in this script's stdout without changing
    `LLMCognition`/`LLMReflection`'s public API.
    """

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner

    def complete(self, system: str, user: str) -> str:
        for line in user.splitlines():
            if line.startswith("Wiki —"):
                print(f"  [wiki -> prompt] {line}")
        return self.inner.complete(system, user)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=140)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--cognition-interval", type=int, default=6)
    ap.add_argument("--every-n", type=int, default=4, help="reflect every N cognition reconsiders")
    ap.add_argument("--min-episodes", type=int, default=4, help="...or after M new episodes")
    ap.add_argument("--wiki-root", default=None, help="override the wiki docs root (see wiki.py)")
    ap.add_argument("--no-wiki", action="store_true", help="disable wiki-grounded prompts")
    args = ap.parse_args()

    wiki = None if args.no_wiki else Wiki(args.wiki_root)
    if wiki is not None:
        print(f"wiki: root={wiki.root} available={wiki.available}")

    client = ReplicateClient.from_v1_config()
    if client is not None:
        print(f"LLM: {client.model} (live)")
        traced = _TracingClient(client)
        goal_cog = LLMCognition(traced, job="miner", wiki=wiki)
        reflect_producer = LLMReflection(traced, wiki=wiki)
    else:
        print("LLM: none configured — offline heuristic demo")
        goal_cog, reflect_producer = HeuristicCognition(), HeuristicReflection()

    reflecting = ReflectingCognition(
        goal_cog, reflect_producer,
        every_n_reconsiders=args.every_n, min_new_episodes=args.min_episodes,
    )
    cognition = ThreadedCognition(reflecting)

    with IpcBody.spawn(args.host, args.port, "animatest", "animatest", pump_ms=400) as agent_body:
        serial = agent_body.ready["player"]["serial"]
        print(f"agent: animatest serial={serial} at {agent_body.ready['player']['pos']}")

        with GmControl.spawn(args.host, args.port) as gm:
            spot = gm.setup_miner(serial)
            print(f"GM staged miner scenario at {spot}")

        agent_body.observe()  # let the teleport + pack grant settle
        # SpeakPending/GoTo ahead of Mine (as `Profession.planner()` composes worker
        # planners, profession.py): without them LLMCognition's queued speech is
        # never voiced and an LLM-set goto goal has no skill to consume it.
        agent = Agent(
            body=agent_body, persona=Persona(name="Grimm", title="a miner"),
            planner=Planner([SpeakPending(), GoTo(), Mine()]), cognition=cognition,
            cognition_interval=args.cognition_interval,
        )

        seen = 0
        for t in range(args.ticks):
            agent.tick()
            if len(reflecting.insights) > seen:
                for ins in reflecting.insights.recent(len(reflecting.insights) - seen):
                    print(f"  [reflect @tick {t}] {ins.text}  "
                          f"(episodes tick {ins.episode_ticks[0]}-{ins.episode_ticks[1]}, n={ins.episode_count})")
                seen = len(reflecting.insights)
            if t % 20 == 0:
                print(f"  tick {t:3d}: reward={agent.episodes.total_reward():.1f} "
                      f"episodes={agent.episodes.total_recorded} insights={len(reflecting.insights)}")

        print(f"\ndone. {len(reflecting.insights)} insight(s) produced from "
              f"{agent.episodes.total_recorded} episodes; episodic reward={agent.episodes.total_reward():.1f}")
        for ins in reflecting.insights.recent(10):
            print(f"  - {ins.text}")


if __name__ == "__main__":
    main()
