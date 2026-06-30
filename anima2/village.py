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
from .profession import MINING_SPOTS, PROFESSIONS, Profession
from .uomap import Static, find_trees

# Minoc-area woods (map 1), near the mining camp — keeps the village compact.
# Lumberjacks are each assigned a distinct tree scanned from the static map.
FOREST_BASE = (2520, 450)
LUMBER_MAP = 1


def _distinct_trees(map_index: int, cx: int, cy: int, radius: int = 60) -> list[Static]:
    """One tree per tile (a tree stacks several graphics at the same (x, y))."""
    seen: set[tuple[int, int]] = set()
    out: list[Static] = []
    for t in find_trees(map_index, cx, cy, radius):
        if (t.x, t.y) not in seen:
            seen.add((t.x, t.y))
            out.append(t)
    return out


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
                           f"skill+{agent.episodes.total_reward():.1f} steps={steps} says={says}")


def run_village(roster: list[str], *, host: str = "127.0.0.1", port: int = 2594,
                ticks: int = 60, stagger: float = 4.0) -> None:
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

    # 2) Assign each worker a distinct workplace (and, for lumberjacks, an exact
    #    tree node found from the static map — trees can't be probed blindly).
    spots = iter(MINING_SPOTS)
    trees = _distinct_trees(LUMBER_MAP, *FOREST_BASE)
    plan: list[dict] = []
    for body, prof, persona in online:
        workplace, node = None, None
        if prof.key == "lumberjack" and trees:
            t = trees.pop(0)
            workplace, node = (t.x, t.y + 1), (t.x, t.y, t.z, t.graphic)
        elif prof.needs_workplace:
            workplace = prof.workplace or next(spots)
        plan.append({"body": body, "prof": prof, "persona": persona,
                     "workplace": workplace, "node": node})

    # 3) Control plane: stage workers and name everyone.
    with GmControl.spawn(host, port) as gm:
        gm.hide()
        for p in plan:
            serial = p["body"].ready["player"]["serial"]
            if p["workplace"] is not None:
                gm.stage(serial, *p["workplace"], skills=p["prof"].skills, items=p["prof"].items)
            gm.command_on(f'[Set Name "{p["persona"].name}"', serial)
    print("staged & named. work begins.\n")

    # 4) Run every villager concurrently; print a live snapshot of the village.
    status: dict[int, str] = {}
    lock = threading.Lock()
    threads = []
    for i, p in enumerate(plan):
        agent = Agent(body=p["body"], persona=p["persona"], planner=p["prof"].planner())
        if p["node"] is not None:
            agent.memory["harvest_node"] = p["node"]  # the exact tree to chop
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--miners", type=int, default=2)
    ap.add_argument("--lumberjacks", type=int, default=2)
    ap.add_argument("--townsfolk", type=int, default=1)
    ap.add_argument("--ticks", type=int, default=60)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    args = ap.parse_args()
    roster = (["miner"] * args.miners + ["lumberjack"] * args.lumberjacks
              + ["townsfolk"] * args.townsfolk)
    run_village(roster, host=args.host, port=args.port, ticks=args.ticks)


if __name__ == "__main__":
    main()
