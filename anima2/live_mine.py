"""Live end-to-end mining proof: GM stages the scenario, the brain mines.

1. Spawn the agent body (animatest) and read its serial.
2. Spawn a GM body (hulryung) and stage a miner scenario on the agent: pickaxe in
   pack, Mining 35, teleport to the Minoc ridge.
3. Run the brain's Mine skill and watch the journal for "You dig...".

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_mine [--ticks N]
"""

from __future__ import annotations

import argparse

from .agent import Agent
from .control import GmControl
from .ipc_body import IpcBody
from .persona import Persona
from .planner import Planner
from .skills import Mine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=30)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    args = ap.parse_args()

    with IpcBody.spawn(args.host, args.port, "animatest", "animatest", pump_ms=400) as agent_body:
        serial = agent_body.ready["player"]["serial"]
        print(f"agent: animatest serial={serial} at {agent_body.ready['player']['pos']}")

        with GmControl.spawn(args.host, args.port) as gm:
            spot = gm.setup_miner(serial)
            print(f"GM staged miner scenario at {spot}")

        # Let the teleport + pack grant settle in the agent's world.
        agent_body.observe()
        obs = agent_body.observe()
        tools = [i for i in obs.items if i.graphic in (0x0E85, 0x0E86, 0x0F39, 0x0F3A)]
        mining = next((s for s in obs.skills if s.id == 45), None)
        print(f"agent now at {obs.player.pos}; pickaxes seen={len(tools)}; "
              f"Mining={mining.base if mining else '?'}")

        def mining_base() -> float:
            s = next((s for s in agent_body.observe().skills if s.id == 45), None)
            return s.base if s else 0.0

        agent = Agent(body=agent_body, persona=Persona(name="Grimm"), planner=Planner([Mine()]))
        start_skill = mining_base()
        for t in range(args.ticks):
            agent.tick()
            if t % 6 == 0:
                print(f"  tick {t:2d}: Mining={mining_base():.1f}  "
                      f"reward={agent.episodes.total_reward():.1f}")
        end_skill = mining_base()
        print(f"done. Mining {start_skill:.1f} → {end_skill:.1f} "
              f"(+{end_skill - start_skill:.1f}); episodic reward={agent.episodes.total_reward():.1f}")


if __name__ == "__main__":
    main()
