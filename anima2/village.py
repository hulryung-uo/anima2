"""A working village: agents with per-agent professions, each doing their job.

Releases a roster of agents, stages the workers via the Control plane (tools +
skills + a distinct workplace), names each character, then runs them all
concurrently. Miners mine at their own ore bank and **gain Mining skill** — the
village's "work output" is the skill each agent accrues (recorded as episodic
reward by the work skill).

Usage: python -m anima2.village [--miners N] [--townsfolk M] [--ticks T]
"""

from __future__ import annotations

import argparse
import threading
import time

from .agent import Agent
from .contract import Say, Walk
from .control import GmControl
from .ipc_body import IpcBody
from .persona import Persona
from .profession import (
    BLACKSMITH_SPOTS,
    FISHING_SPOTS,
    MINING_SPOTS,
    PROFESSIONS,
    Profession,
)
from .uomap import find_tree_clusters

# Minoc-area woods (map 1), near the mining camp — keeps the village compact.
# Each lumberjack gets a distinct grove (a stand spot + the trees in reach).
FOREST_BASE = (2520, 450)
LUMBER_MAP = 1


def _persona_for(prof: Profession, idx: int) -> Persona:
    return Persona(name=f"{prof.persona_name}{idx}", title=f"a {prof.key}")


def _run_worker(agent: Agent, ticks: int, idx: int, status: dict, lock: threading.Lock,
                job: str) -> None:
    steps = says = 0
    for _ in range(ticks):
        if not agent.body.connected:
            break
        action = agent.tick()
        steps += isinstance(action, Walk)
        says += isinstance(action, Say)
        p = agent.body.observe().player.pos
        with lock:
            status[idx] = (f"{agent.persona.name:<9} {job:<10} @({p.x},{p.y}) "
                           f"out+{agent.episodes.total_reward():.1f} steps={steps} says={says}")


def run_village(roster: list[str], *, host: str = "127.0.0.1", port: int = 2594,
                ticks: int = 60, stagger: float = 4.0, forum: bool = False) -> None:
    # 1) Bring every agent online (staggered logins dodge the ServUO throttle).
    print(f"releasing {len(roster)} villagers: {roster}")
    online: list[tuple[IpcBody, Profession, Persona]] = []
    for i, key in enumerate(roster):
        prof = PROFESSIONS[key]
        try:
            body = IpcBody.spawn(host, port, f"anima{i}", f"anima{i}", pump_ms=300)
        except Exception as e:  # noqa: BLE001
            print(f"  anima{i} ({key}): login failed ({e})")
            continue
        online.append((body, prof, _persona_for(prof, i)))
        print(f"  anima{i}: {_persona_for(prof, i).name} the {key}")
        time.sleep(stagger)
    if not online:
        print("no villagers came online")
        return

    # 2) Assign each worker a distinct workplace. Miners get an ore bank; each
    #    lumberjack gets a grove (a stand spot + the exact tree statics in reach,
    #    found from the static map — trees can't be probed blindly, and a cluster
    #    lets a worker move tree-to-tree as each one depletes).
    spots = iter(MINING_SPOTS)
    fish_spots = iter(FISHING_SPOTS)
    smith_spots = iter(BLACKSMITH_SPOTS)
    groves = iter(find_tree_clusters(LUMBER_MAP, *FOREST_BASE))
    plan: list[dict] = []
    for body, prof, persona in online:
        workplace, nodes = None, None
        if prof.key == "lumberjack":
            grove = next(groves, None)
            if grove is not None:
                workplace, trees = grove
                nodes = [(t.x, t.y, t.z, t.graphic) for t in trees]
        elif prof.key == "fisher":
            spot = next(fish_spots, None)
            if spot is not None:
                (sx, sy), (wx, wy, wz) = spot
                workplace = (sx, sy)
                nodes = [(wx, wy, wz, 0)]  # cast at the exact water tile (land target)
        elif prof.key == "blacksmith":
            workplace = next(smith_spots, None)
        elif prof.needs_workplace:
            workplace = prof.workplace or next(spots)
        plan.append({"body": body, "prof": prof, "persona": persona,
                     "workplace": workplace, "nodes": nodes})

    # 3) Control plane: stage workers and name everyone.
    with GmControl.spawn(host, port) as gm:
        gm.hide()
        for p in plan:
            serial = p["body"].ready["player"]["serial"]
            if p["workplace"] is not None:
                gx, gy, gz = gm.stage(serial, *p["workplace"],
                                      skills=p["prof"].skills, items=p["prof"].items)
                for stype, dx, dy in p["prof"].structures:
                    gm.command_at(f"[Add {stype}", gx + dx, gy + dy, gz)
            gm.command_on(f'[Set Name "{p["persona"].name}"', serial)
    print("staged & named. work begins.\n")

    # 4) Run every villager concurrently; print a live snapshot of the village.
    status: dict[int, str] = {}
    lock = threading.Lock()
    threads = []
    agents: list[tuple[Agent, str]] = []
    for i, p in enumerate(plan):
        agent = Agent(body=p["body"], persona=p["persona"], planner=p["prof"].planner())
        if p["nodes"]:
            agent.memory["harvest_nodes"] = p["nodes"]  # the grove to work, tree by tree
        agents.append((agent, p["prof"].key))
        t = threading.Thread(target=_run_worker,
                             args=(agent, ticks, i, status, lock, p["prof"].key), daemon=True)
        threads.append(t)
        t.start()

    while any(t.is_alive() for t in threads):
        time.sleep(2.5)
        with lock:
            snap = [status[i] for i in sorted(status)]
        print("— village —\n  " + "\n  ".join(snap))
    for t in threads:
        t.join()
    print("\nday's work done.")

    # 5) End of day: each villager writes about it on the tavern forum.
    if forum:
        from .forum import ForumClient, post_day

        client = ForumClient()
        if not client.configured:
            print("forum: no API key (set ANIMA_FORUM_API_KEY or anima/config.yaml).")
        else:
            print("\n— the tavern board —")
            for agent, job in agents:
                res = post_day(agent, job=job, client=client)
                print(f"  {agent.persona.name} posted about the day: {'ok' if res else 'failed'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--miners", type=int, default=2)
    ap.add_argument("--lumberjacks", type=int, default=1)
    ap.add_argument("--fishers", type=int, default=1)
    ap.add_argument("--blacksmiths", type=int, default=1)
    ap.add_argument("--townsfolk", type=int, default=1)
    ap.add_argument("--ticks", type=int, default=60)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--forum", action="store_true", help="post each villager's day to uotavern")
    args = ap.parse_args()
    roster = (["miner"] * args.miners + ["lumberjack"] * args.lumberjacks
              + ["fisher"] * args.fishers + ["blacksmith"] * args.blacksmiths
              + ["townsfolk"] * args.townsfolk)
    run_village(roster, host=args.host, port=args.port, ticks=args.ticks, forum=args.forum)


if __name__ == "__main__":
    main()
