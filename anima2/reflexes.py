"""Reflexes — fast, hard-coded survival rules that pre-empt the planner.

These run first in the fast loop. They are deliberately tiny and certain: the
things an agent must do *now* without deliberation (flee at low health, answer a
direct greeting). They return an `Action` to take immediately, or `None` to let
the planner decide. No LLM, no allocation-heavy logic.
"""

from __future__ import annotations

from .contract import Action, Observation
from .persona import Persona


class Reflexes:
    """A small ordered set of survival/etiquette rules."""

    #: Flee below this fraction of max health.
    flee_hp_fraction: float = 0.3

    def check(self, obs: Observation, persona: Persona) -> Action | None:
        p = obs.player
        # Survival comes first. (Phase 1: detection only; flee/heal action TBD once
        # the body exposes the needed primitives. We surface intent via memory.)
        if p.hits_max and p.hits / p.hits_max <= self.flee_hp_fraction:
            return None  # TODO: emit a flee/heal action when those skills exist
        return None
