"""anima2 — an autonomous AI agent that plays Ultima Online (the Brain).

See docs/DESIGN.md. The brain talks to a `Body` (anima-core via IPC in production,
`MockBody` offline) through the Observation/Action contract — it never touches
packets.
"""

from .agent import Agent, Cognition, NullCognition
from .body import Body
from .contract import Action, Observation
from .persona import Persona
from .planner import Planner
from .reflexes import Reflexes

__all__ = [
    "Agent",
    "Cognition",
    "NullCognition",
    "Body",
    "Action",
    "Observation",
    "Persona",
    "Planner",
    "Reflexes",
]
