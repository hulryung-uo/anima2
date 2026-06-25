"""Cognition — the slow loop that sets the agent's high-level goal.

Runs occasionally (not per tick) and **never in the fast loop's hot path**: wrap
any cognition in `ThreadedCognition` so the LLM call happens on a background
thread and the fast loop reads the most recent result without blocking
(DESIGN.md §3.3).

Implementations:
- `HeuristicCognition` — offline default, no LLM (keeps/forms simple goals).
- `LLMCognition` — prompts an `LLMClient` for a goal (+ optional in-character speech).
- `ThreadedCognition` — non-blocking wrapper around any of the above.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

from .contract import Position
from .llm import LLMClient
from .persona import Persona
from .skills.base import Goal, SkillContext

_UNSET = object()


class HeuristicCognition:
    """No-LLM default: passes the current goal through unchanged."""

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        return ctx.goal


class LLMCognition:
    """Ask an LLM for the next high-level goal, given persona + situation.

    The model replies with a small JSON object, e.g.
    ``{"goal": "goto", "x": 3716, "y": 2204, "say": "Off to the mine."}`` or
    ``{"goal": "idle"}``. A ``say`` is stashed in ``ctx.memory['pending_say']`` for
    the `SpeakPending` skill to voice on the next tick.
    """

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        raw = self.client.complete(self._system(ctx.persona), self._situation(ctx))
        decision = _parse_json(raw)
        if not decision:
            return ctx.goal  # unparseable → don't disturb the current goal

        say = decision.get("say")
        if isinstance(say, str) and say.strip():
            ctx.memory["pending_say"] = say.strip()

        if decision.get("goal") == "goto" and "x" in decision and "y" in decision:
            z = ctx.obs.player.pos.z
            return Goal(kind="goto", params={"target": Position(int(decision["x"]),
                                                                int(decision["y"]), z)})
        return None  # idle / explore → let the Wander fallback run

    @staticmethod
    def _system(persona: Persona) -> str:
        return (
            f"You are {persona.name}, {persona.title}, a character in Ultima Online.\n"
            f"Personality: {persona.personality}\n"
            f"Speech style: {persona.speech_style}\n"
            f"Interests: {persona.interests}\n"
            "Decide your next short-term goal. Reply with ONLY a JSON object: "
            '{"goal": "goto"|"idle", "x": int, "y": int, "say": optional short line}. '
            "Use goto with map coordinates when you have somewhere to be; idle otherwise. "
            "Stay in character; keep any 'say' to one short sentence."
        )

    @staticmethod
    def _situation(ctx: SkillContext) -> str:
        obs = ctx.obs
        p = obs.player
        people = ", ".join(f"{m.name or '?'}@{m.distance}" for m in obs.mobiles[:5]) or "none"
        recent = " | ".join(j.text for j in obs.new_journal[-5:]) or "(quiet)"
        memory = " | ".join(str(e) for e in ctx.episodes[-6:]) or "(nothing yet)"
        return (
            f"You are at ({p.pos.x},{p.pos.y}) with {p.hits}/{p.hits_max} health.\n"
            f"Nearby: {people}.\n"
            f"Recent chatter: {recent}\n"
            f"Recently you: {memory}\n"
            "What is your next goal?"
        )


class ThreadedCognition:
    """Run `inner.reconsider` on a background thread; `reconsider` never blocks.

    Returns the most recently computed goal, or the live `ctx.goal` until the first
    background result lands. A new background pass starts only when the previous one
    has finished, so the LLM is never called re-entrantly.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self._lock = threading.Lock()
        self._result: Any = _UNSET
        self._busy = False

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        with self._lock:
            result = self._result
            busy = self._busy
            if not busy:
                self._busy = True
                start = True
            else:
                start = False
        if start:
            threading.Thread(target=self._work, args=(ctx,), daemon=True).start()
        return ctx.goal if result is _UNSET else result

    def _work(self, ctx: SkillContext) -> None:
        try:
            r = self.inner.reconsider(ctx)
        except Exception:  # a flaky LLM call must not kill the agent
            r = ctx.goal
        with self._lock:
            self._result = r
            self._busy = False


def _parse_json(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from a model response (tolerates code fences)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
