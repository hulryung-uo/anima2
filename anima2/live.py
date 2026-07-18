"""Live runner: the anima2 brain drives a real UO character via the bridge.

Spawns the anima-net `anima-agent` bridge (which logs into the server), then runs
the two-rate brain loop against it. Requires a running UO server and a built
bridge binary (`cargo build -p anima-net` in ../anima-client).

Usage:
    python -m anima2.live [host] [port] [user] [pass] [--ticks N] [--goto X Y]
"""

from __future__ import annotations

import argparse

from .agent import Agent
from .cognition import HeuristicCognition, LLMCognition, ThreadedCognition
from .contract import Position
from .ipc_body import ResilientIpcBody
from .persona import Persona
from .planner import Planner
from .skills import Combat, GoTo, Greet, SpeakPending, Wander
from .skills.base import Goal


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("host", nargs="?", default="127.0.0.1")
    ap.add_argument("port", nargs="?", type=int, default=2594)
    ap.add_argument("user", nargs="?", default="animatest")
    ap.add_argument("password", nargs="?", default="animatest")
    ap.add_argument("--ticks", type=int, default=15)
    ap.add_argument("--goto", nargs=2, type=int, metavar=("X", "Y"))
    ap.add_argument("--persona", help="path to a persona YAML")
    ap.add_argument("--llm", action="store_true", help="use Claude cognition (needs ANTHROPIC_API_KEY)")
    args = ap.parse_args()

    persona = Persona.load(args.persona) if args.persona else Persona(name="Anima", title="a wanderer")
    goal = None
    if args.llm:
        from .llm import AnthropicClient

        cognition = ThreadedCognition(LLMCognition(AnthropicClient()))
    else:
        cognition = HeuristicCognition()
    if args.goto:
        goal = Goal(kind="goto", params={"target": Position(args.goto[0], args.goto[1], 0)})

    with ResilientIpcBody.spawn(
        args.host, args.port, args.user, args.password, pump_ms=400
    ) as body:
        print(f"bridge ready: {body.ready.get('player')}")
        agent = Agent(
            body=body,
            persona=persona,
            # Priority: voice queued speech → fight → greet → travel → wander.
            planner=Planner([SpeakPending(), Combat(), Greet(), GoTo(), Wander()]),
            cognition=cognition,
            goal=goal,
        )
        for t in range(args.ticks):
            action = agent.tick()
            p = body.observe().player.pos
            print(f"tick {t:2d}: at ({p.x},{p.y},{p.z})  action={type(action).__name__ if action else None}")
        print("done.")


if __name__ == "__main__":
    main()
