"""IpcBody must speak the NDJSON protocol correctly (tested via a fake bridge)."""

import subprocess
import sys
from pathlib import Path

from anima2.agent import Agent
from anima2.contract import Walk
from anima2.ipc_body import IpcBody
from anima2.persona import Persona
from anima2.planner import Planner
from anima2.skills import Wander

FAKE = Path(__file__).parent / "fake_agent.py"


def spawn_fake() -> IpcBody:
    proc = subprocess.Popen(
        [sys.executable, str(FAKE)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    return IpcBody(proc)


def test_ready_and_observe():
    with spawn_fake() as body:
        assert body.ready["event"] == "ready"
        obs = body.observe()
        assert obs.player.name == "Fake"
        assert obs.player.pos.x == 100


def test_act_walk_moves_player():
    with spawn_fake() as body:
        body.act(Walk(dir=2))  # East: +1 x
        obs = body.observe()
        assert obs.player.pos.x == 101


def test_agent_drives_ipc_body():
    """The real Agent loop should run unchanged against the IPC body."""
    with spawn_fake() as body:
        agent = Agent(body=body, persona=Persona(name="Fake"), planner=Planner([Wander()]))
        for _ in range(3):
            agent.tick()
        obs = body.observe()
        # Wander emitted Walk actions, so the fake player moved from the origin.
        assert (obs.player.pos.x, obs.player.pos.y) != (100, 100)
