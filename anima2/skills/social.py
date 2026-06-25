"""Social skills — greet nearby people in character.

Phase 1 uses fixed templates gated by persona sociability. The slow LLM cognition
loop will later generate genuinely in-character, context-aware speech; this is the
deterministic placeholder that proves the seam (Say + journal perception).
"""

from __future__ import annotations

from ..contract import MobileView, Say
from .base import Skill, SkillContext, SkillResult, Status

# Human player/NPC body graphics (male, female, and elf variants).
HUMAN_BODIES = frozenset({0x0190, 0x0191, 0x025D, 0x025E})


class Greet(Skill):
    """Say hello to a nearby human we haven't greeted yet.

    Applicable only for sociable personas (``talkativeness > 0``) when an
    ungreeted human is within `greet_range`. Greeted serials are remembered in
    scratch memory so we don't spam.
    """

    name = "greet"
    description = "Greet a nearby person in character."
    greet_range: int = 4

    def can_run(self, ctx: SkillContext) -> bool:
        return ctx.persona.talkativeness > 0 and self._target(ctx) is not None

    def step(self, ctx: SkillContext) -> SkillResult:
        target = self._target(ctx)
        if target is None:
            return SkillResult(Status.SUCCESS, None)
        ctx.memory.setdefault("greeted", set()).add(target.serial)
        return SkillResult(Status.SUCCESS, Say(text=self._hello(ctx)), reward=0.1)

    def _target(self, ctx: SkillContext) -> MobileView | None:
        greeted: set[int] = ctx.memory.get("greeted", set())
        for m in ctx.obs.mobiles:
            if m.body in HUMAN_BODIES and m.distance <= self.greet_range and m.serial not in greeted:
                return m
        return None

    @staticmethod
    def _hello(ctx: SkillContext) -> str:
        name = ctx.persona.name
        return f"Hail, friend. {name} greets you."


class SpeakPending(Skill):
    """Voice a line the cognition loop queued in ``memory['pending_say']``.

    This is the seam by which the slow LLM loop talks: cognition stashes a line,
    this high-priority skill drains it as a `Say`. One utterance per tick.
    """

    name = "speak_pending"
    description = "Say a line queued by the cognition loop."

    def can_run(self, ctx: SkillContext) -> bool:
        return bool(ctx.memory.get("pending_say"))

    def step(self, ctx: SkillContext) -> SkillResult:
        text = ctx.memory.pop("pending_say", None)
        if not text:
            return SkillResult(Status.SUCCESS, None)
        return SkillResult(Status.SUCCESS, Say(text=text))
