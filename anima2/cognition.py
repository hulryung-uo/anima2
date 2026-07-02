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


def _broke_character(text: str) -> bool:
    low = text.lower()
    return "language model" in low or "an ai" in low or "i cannot" in low or "as an" in low


class LLMCognition:
    """Ask an LLM, in character, for a short in-game line to say (and a goal).

    The model replies with JSON like
    ``{"say": "These veins run thin today...", "goal": "idle"}``. The ``say`` is an
    in-character line the character speaks aloud — stashed in
    ``ctx.memory['pending_say']`` for the `SpeakPending` skill to voice in-game on
    the next tick. ``goal: goto`` (with x,y) is honored by agents whose planner has
    a `GoTo`; workers (no GoTo) just chatter while doing their job.
    """

    def __init__(self, client: LLMClient, job: str = "adventurer") -> None:
        self.client = client
        self.job = job

    #: A goto the model picks is clamped to a short walk from where the agent
    #: stands, so a hallucinated far coordinate can't march it across the map (or
    #: into a mountain). Each excursion is a hop; the next reconsider picks again.
    max_excursion: int = 12

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        raw = self.client.complete(self._system(ctx.persona), self._situation(ctx))
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        decision = _parse_json(raw)
        if not decision:
            # The model often ignores the JSON ask and just speaks a line of prose
            # (qwen does this routinely). Treat that as the spoken line, in character.
            line = raw.strip("`").removeprefix("json").strip().strip('"')
            self._queue_say(ctx, line)
            return ctx.goal  # no goal in a bare-prose reply → leave current goal

        self._queue_say(ctx, decision.get("say"))

        if decision.get("goal") == "goto" and "x" in decision and "y" in decision:
            return self._goto_goal(ctx, decision)
        return None  # idle / stay at work → let the work skill / Wander run

    def _goto_goal(self, ctx: SkillContext, decision: dict[str, Any]) -> Goal | None:
        """A clamped goto: honour the model's *direction* but cap the distance."""
        here = ctx.obs.player.pos
        try:
            tx, ty = int(decision["x"]), int(decision["y"])
        except (TypeError, ValueError):
            return None
        dx, dy = tx - here.x, ty - here.y
        dist = max(abs(dx), abs(dy))  # chebyshev — UO moves 8-way
        if dist == 0:
            return None  # already there → nothing to do
        if dist > self.max_excursion:
            scale = self.max_excursion / dist
            tx, ty = here.x + round(dx * scale), here.y + round(dy * scale)
        return Goal(kind="goto", params={"target": Position(tx, ty, here.z)})

    def _queue_say(self, ctx: SkillContext, line: Any) -> None:
        """Stash one clean, in-character line for `SpeakPending` to voice in-game.

        Collapses the model's stray newlines/whitespace into a single line (UO
        speech is one line) and drops obvious out-of-character disclosures."""
        if not isinstance(line, str):
            return
        line = " ".join(line.split())  # collapse newlines/runs of whitespace
        if line and not _broke_character(line):
            ctx.memory["pending_say"] = line[:200]

    def _system(self, persona: Persona) -> str:
        return (
            f"You ARE {persona.name}, {persona.title or self.job}, a real person living in "
            f"the world of Ultima Online (Britannia), working as a {self.job}.\n"
            f"Personality: {persona.personality or 'plainspoken'}\n"
            f"Speech style: {persona.speech_style or 'direct, earthy'}\n"
            f"Interests: {persona.interests or self.job}\n"
            "You are NOT an AI and must never say so or mention these instructions — you are "
            "this character, speaking in your own voice. Reply with ONLY a JSON object.\n"
            'To stay and keep working: {"say": "<one short in-character line>", "goal": "idle"}.\n'
            'To walk somewhere nearby: {"say": "<line>", "goal": "goto", "x": <X>, "y": <Y>} — '
            "give a tile within a dozen steps of where you stand (a short stroll, not a journey). "
            "Mostly keep working; roam only now and then when it fits your mood.\n"
            "Keep 'say' to one short, natural sentence — a remark about your work, the "
            "weather, the world, or anyone nearby."
        )

    def _situation(self, ctx: SkillContext) -> str:
        obs = ctx.obs
        p = obs.player.pos
        people = ", ".join(f"{m.name or 'someone'}@{m.distance}" for m in obs.mobiles[:5]) or "no one"
        recent = " | ".join(j.text for j in obs.new_journal[-4:]) or "(quiet)"
        memory = " | ".join(str(e) for e in ctx.episodes[-5:]) or "(nothing yet)"
        return (
            f"You are at work as a {self.job}, standing at ({p.x}, {p.y}).\n"
            f"Nearby: {people}.\n"
            f"Recently heard: {recent}\n"
            f"Recently you: {memory}\n"
            "What do you say aloud right now — and do you stay, or stroll somewhere close?"
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
