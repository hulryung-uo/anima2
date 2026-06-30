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

    # 2) Control plane: stage workers (tools/skills/workplace) and name everyone.
    spots = iter(MINING_SPOTS)
    with GmControl.spawn(host, port) as gm:
        gm.hide()
        for body, prof, persona in online:
            serial = body.ready["player"]["serial"]
            if prof.work_skill is not None and prof.needs_workplace:
                wx, wy = prof.workplace or next(spots)
                gm.stage(serial, wx, wy, skills=prof.skills, items=prof.items)
            gm.command_on(f'[Set Name "{persona.name}"', serial)
    print("staged & named. work begins.\n")

    # 3) Run every villager concurrently; print a live snapshot of the village.
    status: dict[int, str] = {}
    lock = threading.Lock()
    threads = []
    for i, (body, prof, persona) in enumerate(online):
        agent = Agent(body=body, persona=persona, planner=prof.planner())
        t = threading.Thread(target=_run_worker,
                             args=(agent, ticks, i, status, lock, prof.key), daemon=True)
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
    ap.add_argument("--miners", type=int, default=3)
    ap.add_argument("--townsfolk", type=int, default=1)
    ap.add_argument("--ticks", type=int, default=60)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    args = ap.parse_args()
    roster = ["miner"] * args.miners + ["townsfolk"] * args.townsfolk
    run_village(roster, host=args.host, port=args.port, ticks=args.ticks)


if __name__ == "__main__":
    main()
