"""Live end-to-end smelting proof: GM stages the scenario, the brain mines & smelts.

1. Spawn the agent body (animatest) and read its serial.
2. Spawn a GM body (hulryung) and stage a miner scenario: pickaxes + Mining 35,
   teleport to a Minoc ridge ore bank, then `[Add Forge` a couple tiles away (the
   miner never walks, so once staged the forge stays in reach for the whole run).
3. Run the brain's MineAndSmelt skill and watch the journal + backpack for ore
   piling up, then getting smelted into ingots.

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_smelt [--ticks N] [--threshold N]
"""

from __future__ import annotations

import argparse

from .agent import Agent
from .cliloc import resolve_entry
from .contract import Observation
from .control import GmControl
from .ipc_body import IpcBody
from .persona import Persona
from .planner import Planner
from .profession import MINING_SPOTS
from .skills.smelt import FORGE_GRAPHICS, INGOT_GRAPHICS, ORE_GRAPHICS, MineAndSmelt


class _RecordingBody:
    """Wraps a `Body`, caching the last `Observation` so a driver loop can inspect
    it (journal, backpack contents) without paying for an extra `observe()` pump
    on top of the one `Agent.tick()` already does."""

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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=400)
    ap.add_argument("--threshold", type=int, default=3, help="ore piles before a smelt run")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    args = ap.parse_args()

    x, y = MINING_SPOTS[0]

    with IpcBody.spawn(args.host, args.port, "animatest", "animatest", pump_ms=400) as agent_body:
        serial = agent_body.ready["player"]["serial"]
        print(f"agent: animatest serial={serial} at {agent_body.ready['player']['pos']}")

        with GmControl.spawn(args.host, args.port) as gm:
            gx, gy, gz = gm.stage(serial, x, y, skills={"Mining": 35},
                                  items=["Pickaxe", "Pickaxe"])
            print(f"GM staged miner at ({gx},{gy},{gz})")
            gm.command_at("[Add Forge", gx + 1, gy + 1, gz)
            forge = gm.find_item_near(gx + 1, gy + 1, graphic=0x0FB1)
            print(f"GM placed forge: {forge}")

        # Let the teleport + pack grant + forge spawn settle in the agent's world.
        agent_body.observe()
        obs = agent_body.observe()
        forges = [i for i in obs.items if i.graphic in FORGE_GRAPHICS]
        mining = next((s for s in obs.skills if s.id == 45), None)
        print(f"agent now at {obs.player.pos}; forges seen={len(forges)} "
              f"(dist={forges[0].distance if forges else '?'}); Mining={mining.base if mining else '?'}")

        def snapshot(o: Observation) -> tuple[float, int, int]:
            # container == player.serial, not just layer == backpack: another
            # nearby mobile's own backpack shares the layer and (being contained)
            # the same placeholder position, so it can tie on distance with ours.
            bp = next((i for i in o.items if i.layer == 0x15 and i.container == o.player.serial), None)
            ore = sum(i.amount for i in o.items if i.graphic in ORE_GRAPHICS
                      and bp is not None and i.container == bp.serial)
            ingots = sum(i.amount for i in o.items if i.graphic in INGOT_GRAPHICS
                        and bp is not None and i.container == bp.serial)
            s = next((sk for sk in o.skills if sk.id == 45), None)
            return (s.base if s else 0.0, ore, ingots)

        skill = MineAndSmelt()
        skill.ore_threshold = args.threshold
        rec_body = _RecordingBody(agent_body)
        agent = Agent(body=rec_body, persona=Persona(name="Grimm"), planner=Planner([skill]))

        start_skill, _, _ = snapshot(obs)
        max_ingots = 0
        smelt_lines: list[str] = []
        for t in range(args.ticks):
            agent.tick()
            assert rec_body.last_obs is not None
            for j in rec_body.last_obs.new_journal:
                text = resolve_entry(j)
                if j.cliloc in (501988, 501987, 501986, 501990) or "smelt" in text.lower() or "impurities" in text.lower():
                    smelt_lines.append(f"tick {t}: {text}")
                    print(f"  [journal] {text}")
            if t % 20 == 0:
                mining_base, ore, ingots = snapshot(rec_body.last_obs)
                phase = agent.memory.get("smelt_phase", "mine")
                print(f"  tick {t:3d}: phase={phase:5s} Mining={mining_base:.1f} ore={ore} ingots={ingots} "
                      f"reward={agent.episodes.total_reward():.1f}")
                max_ingots = max(max_ingots, ingots)

        end_skill, end_ore, end_ingots = snapshot(agent_body.observe())
        print(f"\ndone. Mining {start_skill:.1f} -> {end_skill:.1f}; "
              f"final ore={end_ore} ingots={end_ingots} (peak seen {max(max_ingots, end_ingots)}); "
              f"episodic reward={agent.episodes.total_reward():.1f}")
        print(f"smelt-related journal lines seen: {len(smelt_lines)}")
        for line in smelt_lines:
            print(" ", line)


if __name__ == "__main__":
    main()
