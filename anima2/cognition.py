"""Cognition — the slow loop that sets the agent's high-level goal.

Runs occasionally (not per tick) and **never in the fast loop's hot path**: wrap
any cognition in `ThreadedCognition` so the LLM call happens on a background
thread and the fast loop reads the most recent result without blocking
(DESIGN.md §3.3).

Implementations:
- `HeuristicCognition` — offline default, no LLM (keeps/forms simple goals).
- `LLMCognition` — prompts an `LLMClient` for a goal (+ optional in-character speech).
- `ThreadedCognition` — non-blocking wrapper around any of the above.
- `ReflectingCognition` — wraps a cognition; periodically distills recent episodes
  into `Insight`s (Generative Agents-style reflection, PHASE2.md B1) via a
  `HeuristicReflection` or `LLMReflection` producer.
"""

from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from typing import Any, Protocol

from .contract import Position
from .llm import LLMClient
from .memory import Episode, Insight, ReflectionMemory
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
        learned = " | ".join(str(i) for i in ctx.insights[-3:]) or "(nothing yet)"
        return (
            f"You are at work as a {self.job}, standing at ({p.x}, {p.y}).\n"
            f"Nearby: {people}.\n"
            f"Recently heard: {recent}\n"
            f"Recently you: {memory}\n"
            f"Lessons learned: {learned}\n"
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


class ReflectionProducer(Protocol):
    """Distills a run of episodes into 1-3 short natural-language insights."""

    def reflect(self, episodes: list[Episode], persona: Persona) -> list[str]:
        """Return short, in-character takeaways from `episodes` (may be empty)."""
        ...


class HeuristicReflection:
    """No-LLM default: aggregate reward per skill + notable failures into 1-2
    short insight strings. Offline-safe; also `LLMReflection`'s fallback."""

    def reflect(self, episodes: list[Episode], persona: Persona) -> list[str]:
        if not episodes:
            return []
        by_name: dict[str, list[Episode]] = defaultdict(list)
        for ep in episodes:
            name = ep.summary.split(" → ", 1)[0] if " → " in ep.summary else ep.kind
            by_name[name].append(ep)

        insights: list[str] = []
        totals = {name: sum(e.reward for e in eps) for name, eps in by_name.items()}
        best, best_total = max(totals.items(), key=lambda kv: kv[1])
        if best_total > 0:
            insights.append(
                f"{best} has paid off: {best_total:+.1f} reward over {len(by_name[best])} turns."
            )

        failures = [e for e in episodes if e.summary.endswith("failure")]
        if failures:
            worst = failures[-1]
            name = worst.summary.split(" → ", 1)[0]
            insights.append(
                f"Trouble with {name}: {len(failures)} setback(s), most recently at tick {worst.tick}."
            )

        if not insights:
            insights.append(f"A quiet stretch: {len(episodes)} turns, nothing much to report.")
        return insights[:2]


class LLMReflection:
    """LLM-backed reflection: one call summarizing recent episodes into 1-3 short,
    first-person insights (Generative Agents-style). Falls back to
    `HeuristicReflection` on any failure (bad/empty response, parse error, flaky
    client) so reflection never breaks the slow loop."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self._fallback = HeuristicReflection()

    def reflect(self, episodes: list[Episode], persona: Persona) -> list[str]:
        if not episodes:
            return []
        try:
            raw = self.client.complete(self._system(persona), self._situation(episodes))
        except Exception:  # noqa: BLE001 — a flaky LLM must not break reflection
            return self._fallback.reflect(episodes, persona)
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        return _parse_insights(raw) or self._fallback.reflect(episodes, persona)

    def _system(self, persona: Persona) -> str:
        return (
            f"You ARE {persona.name}, quietly taking stock of your recent work in Britannia — "
            "not speaking aloud, just reflecting to yourself.\n"
            "Distill 1-3 short, concrete lessons from what just happened: what worked, what to "
            "avoid, patterns worth remembering. You are NOT an AI and must never say so. "
            'Reply with ONLY a JSON array of short strings, e.g. '
            '["The east vein paid better than the west one.", '
            '"Best to smelt before the pack fills up."]'
        )

    def _situation(self, episodes: list[Episode]) -> str:
        recent = "\n".join(str(e) for e in episodes)
        return f"What just happened:\n{recent}\n\nWhat do you take away from this?"


class ReflectingCognition:
    """Wraps a `Cognition`; periodically distills recent episodes into short
    `Insight`s that persist and feed back into later goal/speech decisions
    (Generative Agents-style reflection, PHASE2.md B1).

    Cadence lives entirely in the **slow loop**: reflection fires from inside
    `reconsider()` when either `every_n_reconsiders` calls have passed, or at
    least `min_new_episodes` new episodes (tracked via `SkillContext.episode_count`,
    which — unlike `len(episodes)` — survives `EpisodicMemory`'s bounded window)
    have accumulated since the last reflection — whichever comes first — *and*
    at least one new episode has landed since then. That last condition keeps
    an idle agent (no new episodes) from re-reflecting over the same unchanged
    window every `every_n_reconsiders` calls, which would otherwise flood the
    bounded `ReflectionMemory` with duplicate insights and, for `LLMReflection`,
    burn a wasted LLM call per cycle. Because it runs inside `reconsider()`, the
    usual `ThreadedCognition(ReflectingCognition(inner))` composition keeps
    reflection off the fast loop's hot path entirely.
    """

    def __init__(
        self,
        inner: Any,
        reflection: ReflectionProducer,
        *,
        every_n_reconsiders: int = 5,
        min_new_episodes: int = 6,
        episode_window: int = 20,
        insights: ReflectionMemory | None = None,
    ) -> None:
        self.inner = inner
        self.reflection = reflection
        self.every_n_reconsiders = every_n_reconsiders
        self.min_new_episodes = min_new_episodes
        self.episode_window = episode_window
        self.insights = insights if insights is not None else ReflectionMemory()
        self._reconsiders_since = 0
        self._episode_count_at_last = 0

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        # Insights from *prior* reflection rounds inform this round's decision —
        # not the reflection this call might itself produce below.
        ctx.insights = self.insights.recent(3)
        goal = self.inner.reconsider(ctx)

        self._reconsiders_since += 1
        new_episodes = ctx.episode_count - self._episode_count_at_last
        due = new_episodes >= 1 and (
            self._reconsiders_since >= self.every_n_reconsiders
            or new_episodes >= self.min_new_episodes
        )
        if due and ctx.episodes:
            self._reflect(ctx)
        return goal

    def _reflect(self, ctx: SkillContext) -> None:
        window = ctx.episodes[-self.episode_window :]
        for text in self.reflection.reflect(window, ctx.persona):
            self.insights.record(
                Insight(
                    text=text,
                    episode_ticks=(window[0].tick, window[-1].tick),
                    episode_count=len(window),
                )
            )
        self._reconsiders_since = 0
        self._episode_count_at_last = ctx.episode_count


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


def _parse_insights(text: str) -> list[str]:
    """Extract a JSON array of short strings from a model response (tolerates
    code fences and surrounding prose). Returns `[]` if nothing usable is found."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        arr = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(arr, list):
        return []
    return [s.strip() for s in arr if isinstance(s, str) and s.strip()][:3]
