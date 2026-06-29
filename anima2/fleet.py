"""Fleet — release multiple autonomous agents into the live world at once.

Each agent gets its own account (auto-created on the shard), its own `IpcBody`
(bridge subprocess), a persona, and runs the brain loop in its own thread. Logins
are staggered to dodge ServUO's per-IP login throttle. Optionally a GM gathers them
to one town and names each character after its persona, so they meet and greet —
a first taste of "characters living in Britannia" (DESIGN.md §1).

Usage: python -m anima2.fleet [--n N] [--ticks T] [--gather] [--persona-dir DIR]
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

from .agent import Agent
from .contract import Say, Walk
from .control import GmControl
from .ipc_body import IpcBody
from .persona import Persona
from .planner import Planner
from .skills import Greet, SpeakPending, Wander

V1_PERSONAS = Path.home() / "dev" / "uo" / "anima" / "personas"


def load_personas(n: int, persona_dir: Path | None = None) -> list[Persona]:
    """Load up to `n` personas from v1 YAML, falling back to built-ins."""
    d = persona_dir or V1_PERSONAS
    files = sorted(d.glob("*.yaml")) if d.exists() else []
    personas = [Persona.load(f) for f in files[:n]]
    while len(personas) < n:  # pad with simple built-ins if not enough YAML
        i = len(personas)
        personas.append(Persona(name=f"Wanderer{i}", title="a traveler", talkativeness=0.5))
    return personas


def build_agent(body: IpcBody, persona: Persona) -> Agent:
    """A sociable 'living world' agent: voice queued lines, greet people, wander."""
    return Agent(
        body=body,
        persona=persona,
        planner=Planner([SpeakPending(), Greet(), Wander()]),
    )


def _run_agent(agent: Agent, ticks: int, idx: int, status: dict, lock: threading.Lock) -> None:
    steps = says = 0
    for _ in range(ticks):
        if not agent.body.connected:
            break
        action = agent.tick()
        if isinstance(action, Walk):
            steps += 1
        elif isinstance(action, Say):
            says += 1
        p = agent.body.observe().player.pos
        with lock:
            status[idx] = f"{agent.persona.name:<10} @({p.x},{p.y}) steps={steps} says={says}"


def run_fleet(
    n: int,
    *,
    host: str = "127.0.0.1",
    port: int = 2594,
    ticks: int = 60,
    gather: bool = False,
    persona_dir: Path | None = None,
    stagger: float = 4.0,
) -> None:
    personas = load_personas(n, persona_dir)
    bodies: list[tuple[str, IpcBody, Persona]] = []
    print(f"spawning {n} agents (staggered to dodge login throttle)...")
    for i, persona in enumerate(personas):
        acct = f"anima{i}"
        try:
            body = IpcBody.spawn(host, port, acct, acct, pump_ms=300)
        except Exception as e:  # noqa: BLE001 — one bad login shouldn't sink the fleet
            print(f"  {acct}: login failed ({e}); skipping")
            continue
        bodies.append((acct, body, persona))
        print(f"  {acct}: {persona.name} in world @ {body.ready['player']['pos']}")
        time.sleep(stagger)

    if not bodies:
        print("no agents came online")
        return

    if gather:
        _gather(bodies, host, port)

    # Run each agent in its own thread (independent body → no shared state).
    status: dict[int, str] = {}
    lock = threading.Lock()
    agents = [build_agent(body, persona) for _, body, persona in bodies]
    threads = [
        threading.Thread(target=_run_agent, args=(a, ticks, i, status, lock), daemon=True)
        for i, a in enumerate(agents)
    ]
    for t in threads:
        t.start()

    # Main thread: periodically print a snapshot of the living world.
    while any(t.is_alive() for t in threads):
        time.sleep(2.0)
        with lock:
            snap = [status[i] for i in sorted(status)]
        print("— world —\n  " + "\n  ".join(snap))
    for t in threads:
        t.join()
    print("fleet done.")


def _gather(bodies: list[tuple[str, IpcBody, Persona]], host: str, port: int,
            tx: int = 1416, ty: int = 1683) -> None:
    """GM-teleport every agent to a town crossroads and name each after its persona."""
    with GmControl.spawn(host, port) as gm:
        gm.hide()
        gx, gy, gz = gm.go(tx, ty)
        for i, (_acct, body, persona) in enumerate(bodies):
            serial = body.ready["player"]["serial"]
            # Spread them a few tiles apart so they're in view but not stacked.
            gm.command_on(f"[Set X {gx + (i % 4)} Y {gy + (i // 4)} Z {gz}", serial)
            gm.command_on(f'[Set Name "{persona.name}"', serial)
    print(f"gathered {len(bodies)} agents at ({tx},{ty}) and named them")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--ticks", type=int, default=60)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--gather", action="store_true", help="GM-teleport all to Britain + name them")
    ap.add_argument("--persona-dir")
    args = ap.parse_args()
    run_fleet(
        args.n,
        host=args.host,
        port=args.port,
        ticks=args.ticks,
        gather=args.gather,
        persona_dir=Path(args.persona_dir) if args.persona_dir else None,
    )


if __name__ == "__main__":
    main()
