"""Demo: a persona 'lives' in the MockBody — walks toward work, then wanders.

Run: ``python -m anima2`` (optionally ``python -m anima2 <persona.yaml>``).
No server, no Rust core — proves the Brain ⊥ Body loop end to end.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .agent import Agent, NullCognition
from .contract import Position
from .mock_body import MockBody
from .persona import Persona
from .planner import Planner
from .skills import GoTo, Wander
from .skills.base import Goal


def _default_persona() -> Persona:
    # Fall back to the bundled v1 miner persona if present, else a built-in.
    v1 = Path(__file__).resolve().parents[2] / "anima" / "personas" / "miner.yaml"
    if v1.exists():
        return Persona.load(v1)
    return Persona(name="Grimm", title="a dusty miner", talkativeness=0.3)


class GoToWorkThenWander(NullCognition):
    """Tiny scripted cognition: head to the mine, then wander once arrived."""

    def __init__(self, worksite: Position) -> None:
        self.worksite = worksite
        self._arrived = False

    def reconsider(self, ctx):
        if self._arrived:
            return None
        if ctx.obs.player.pos == self.worksite:
            self._arrived = True
            return None
        return Goal(kind="goto", params={"target": self.worksite})


def main(argv: list[str]) -> None:
    persona = Persona.load(argv[1]) if len(argv) > 1 else _default_persona()
    body = MockBody()
    body.player.pos = Position(100, 100, 0)
    body.player.name = persona.name
    body.player.hits = body.player.hits_max = 100
    worksite = Position(108, 104, 0)

    agent = Agent(
        body=body,
        persona=persona,
        planner=Planner([GoTo(), Wander()]),  # GoTo when a goto-goal is set, else Wander
        cognition=GoToWorkThenWander(worksite),
        cognition_interval=1,  # re-check the goal every tick for the demo
    )

    print(f"{persona.name} ({persona.title}) wakes at {body.player.pos}")
    arrived_at = None
    for t in range(40):
        agent.tick()
        p = body.player.pos
        if arrived_at is None and (p.x, p.y) == (worksite.x, worksite.y):
            arrived_at = t
            print(f"  tick {t:2d}: arrived at the worksite {p}")
        elif t % 4 == 0:
            print(f"  tick {t:2d}: at {p}")
    print(f"done (reached worksite at tick {arrived_at}).")


if __name__ == "__main__":
    main(sys.argv)
