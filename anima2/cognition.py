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

`LLMCognition`/`LLMReflection` optionally take a `wiki.Wiki` (PHASE2.md B1's
semantic-memory close-out item): each derives a short, deterministic search
query from context (`_wiki_query`/`_top_skill_name`) and splices at most one
compact "Wiki — <title>: <excerpt>" line into their prompt. Since `Wiki.search`
only ever runs from inside `reconsider()`/`reflect()`, and those only run on a
`ThreadedCognition` worker thread or `ReflectingCognition`'s own reflection
thread in production, wiki file I/O never touches the fast tick — see
`wiki.py`'s module docstring for the "lazy, one-time index" design that makes
this safe regardless of which thread constructs the `Wiki`.
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
from .wiki import Wiki

_UNSET = object()


class HeuristicCognition:
    """No-LLM default: passes the current goal through unchanged."""

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        return ctx.goal


def _broke_character(text: str) -> bool:
    low = text.lower()
    return "language model" in low or "an ai" in low or "i cannot" in low or "as an" in low


def _clean_model_line(text: str, limit: int = 200) -> str | None:
    """Screen one piece of model-produced text before it re-enters a later prompt
    or the game (speech, insights, ...): collapse stray newlines/whitespace into a
    single line, drop it if it breaks character (`_broke_character`), and clamp its
    length. Returns `None` if nothing usable survives. Mirrors
    `LLMCognition._queue_say`'s treatment of `say` — the same defense applies to
    `LLMReflection`'s insights, which are stored verbatim and replayed into every
    later situation prompt via the "Lessons learned" line."""
    line = " ".join(text.split())
    if not line or _broke_character(line):
        return None
    return line[:limit]


def _top_skill_name(episodes: list[Episode]) -> str | None:
    """The name of the most-rewarded recent skill episode, if any — e.g. "mine"
    from an episode summary of "mine → success (+0.5)". The shared building
    block behind both `LLMCognition`'s and `LLMReflection`'s wiki-query
    derivation: what the agent is actually, concretely doing right now (its
    best-paying skill lately) is a short, deterministic, on-topic search term.
    Only looks at the last 5 episodes — recent, not exhaustive."""
    top: str | None = None
    top_reward = 0.0
    for ep in episodes[-5:]:
        if ep.kind == "skill" and ep.reward > top_reward:
            top, top_reward = ep.summary.split(" → ", 1)[0], ep.reward
    return top


def _wiki_query(ctx: SkillContext, job: str) -> str | None:
    """A short, deterministic wiki search query for this `reconsider`: the
    most-rewarded recent skill episode's name plus the job title (e.g.
    "mine miner") — falls back to the job alone before any episode has landed.
    `None` only when neither is available. `Wiki.search`'s own light stemming
    (see `wiki.py::_stem`) bridges the skill-name/job-title vs wiki-page-title
    mismatch (mine/miner -> "Mining", fisher -> "Fishing"); this function's only
    job is picking a couple of on-topic words already on hand in `ctx` — no
    extra I/O, safe to call every `reconsider`."""
    parts = [p for p in (_top_skill_name(ctx.episodes), job) if p]
    return " ".join(parts) or None


def _wiki_line_for(wiki: Wiki | None, query: str | None, cache: dict[str, str | None]) -> str | None:
    """Shared body of `LLMCognition._wiki_line`/`LLMReflection._wiki_line`: at
    most one compact "Wiki — <title>: <excerpt>" line for `query`, or `None` if
    there's no wiki, no query, or no hit. Memoized into the caller's own
    per-instance `cache` dict, keyed on the exact query string — see
    `LLMCognition.__init__`'s `_wiki_cache` docstring for the memoization
    contract this preserves."""
    if wiki is None or not query:
        return None
    if query in cache:
        return cache[query]
    line = None
    hits = wiki.search(query, k=1)
    if hits:
        excerpt = wiki.excerpt(hits[0], limit=200)
        if excerpt:
            line = f"Wiki — {hits[0].title[:60]}: {excerpt}"[:280]
    cache[query] = line
    return line


class LLMCognition:
    """Ask an LLM, in character, for a short in-game line to say (and a goal).

    The model replies with JSON like
    ``{"say": "These veins run thin today...", "goal": "idle"}``. The ``say`` is an
    in-character line the character speaks aloud — stashed in
    ``ctx.memory['pending_say']`` for the `SpeakPending` skill to voice in-game on
    the next tick. ``goal: goto`` (with x,y) is honored by agents whose planner has
    a `GoTo`; workers (no GoTo) just chatter while doing their job.

    An optional `wiki` (PHASE2.md B1) adds at most one "Wiki — <title>:
    <excerpt>" line to the situation prompt, from a query derived from `ctx`
    (`_wiki_query`) — see `_wiki_line`. `None` (the default) keeps the prompt
    exactly as before; wiki lookups never run when there's no wiki to consult.
    """

    def __init__(self, client: LLMClient, job: str = "adventurer", wiki: Wiki | None = None) -> None:
        self.client = client
        self.job = job
        self.wiki = wiki
        #: Memoizes the formatted wiki line per query string (or `None` for a
        #: query that hit nothing) — an unchanged query across many reconsiders
        #: (the common case: same skill, same job) costs one `Wiki.search()`
        #: call total, not one per call (DESIGN.md §7: cache wiki excerpts
        #: aggressively). On top of `Wiki`'s own internal index/result cache.
        self._wiki_cache: dict[str, str | None] = {}

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
        cleaned = _clean_model_line(line)
        if cleaned:
            ctx.memory["pending_say"] = cleaned

    def _wiki_line(self, ctx: SkillContext) -> str | None:
        """At most one compact "Wiki — <title>: <excerpt>" line for the
        situation prompt, or `None` if there's no wiki, no derivable query, or
        no hit. See `_wiki_cache` (`__init__`) for the memoization contract."""
        return _wiki_line_for(self.wiki, _wiki_query(ctx, self.job), self._wiki_cache)

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
        # Only the last few, regardless of how many `ctx.episodes` carries (Agent's
        # `episodes_window`, up to 20+ for reflection) — keeps this prompt short even
        # as that window grows; `ctx.insights` below is the longer-range memory.
        memory = " | ".join(str(e) for e in ctx.episodes[-5:]) or "(nothing yet)"
        learned = " | ".join(str(i) for i in ctx.insights[-3:]) or "(nothing yet)"
        lines = [
            f"You are at work as a {self.job}, standing at ({p.x}, {p.y}).",
            f"Nearby: {people}.",
            f"Recently heard: {recent}",
            f"Recently you: {memory}",
            f"Lessons learned: {learned}",
        ]
        wiki_line = self._wiki_line(ctx)
        if wiki_line:
            lines.append(wiki_line)
        lines.append("What do you say aloud right now — and do you stay, or stroll somewhere close?")
        return "\n".join(lines)


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
        #: Set whenever no background `reconsider` pass is in flight — tests use
        #: `wait_idle()` for a deterministic join point instead of a sleep/poll loop.
        self._idle = threading.Event()
        self._idle.set()

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        with self._lock:
            result = self._result
            busy = self._busy
            if not busy:
                self._busy = True
                self._idle.clear()
                start = True
            else:
                start = False
        if start:
            try:
                threading.Thread(target=self._work, args=(ctx,), daemon=True).start()
            except RuntimeError:  # spawn failed: _work's cleanup will never run —
                # release the guard so a later tick can retry.
                with self._lock:
                    self._busy = False
                    self._idle.set()
        return ctx.goal if result is _UNSET else result

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until any in-flight background `reconsider` pass has finished.
        Never called from `reconsider` itself (that would reintroduce blocking) —
        it's a test-only join point. Returns whether it went idle before `timeout`."""
        return self._idle.wait(timeout)

    def _work(self, ctx: SkillContext) -> None:
        try:
            r = self.inner.reconsider(ctx)
        except Exception:  # a flaky LLM call must not kill the agent
            r = ctx.goal
        with self._lock:
            self._result = r
            self._busy = False
            self._idle.set()


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
    client) so reflection never breaks the slow loop.

    An optional `wiki` (PHASE2.md B1) adds the same compact "Wiki — <title>:
    <excerpt>" line `LLMCognition` uses, derived from the episodes being
    reflected on (`_top_skill_name`) — grounds the reflection prompt in the
    same textbook the goal-setting prompt consults. `None` (the default) keeps
    the prompt exactly as before."""

    def __init__(self, client: LLMClient, wiki: Wiki | None = None) -> None:
        self.client = client
        self.wiki = wiki
        self._fallback = HeuristicReflection()
        #: See `LLMCognition._wiki_cache` — same memoization contract.
        self._wiki_cache: dict[str, str | None] = {}

    def reflect(self, episodes: list[Episode], persona: Persona) -> list[str]:
        if not episodes:
            return []
        try:
            raw = self.client.complete(self._system(persona), self._situation(episodes))
        except Exception:  # noqa: BLE001 — a flaky LLM must not break reflection
            return self._fallback.reflect(episodes, persona)
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        # Insights persist in `ReflectionMemory` and are replayed into every later
        # situation prompt (the "Lessons learned" line) — screen/clamp each one the
        # same way `_queue_say` treats in-game speech before it's stored.
        insights = [c for s in _parse_insights(raw) if (c := _clean_model_line(s)) is not None]
        return insights or self._fallback.reflect(episodes, persona)

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
        parts = [f"What just happened:\n{recent}"]
        wiki_line = self._wiki_line(episodes)
        if wiki_line:
            parts.append(wiki_line)
        parts.append("What do you take away from this?")
        return "\n\n".join(parts)

    def _wiki_line(self, episodes: list[Episode]) -> str | None:
        """Same idea as `LLMCognition._wiki_line`, keyed off the episodes being
        reflected on instead of a live `SkillContext` (reflection has no `ctx`
        or job title on hand — just the episode window and the persona)."""
        return _wiki_line_for(self.wiki, _top_skill_name(episodes), self._wiki_cache)


class ReflectingCognition:
    """Wraps a `Cognition`; periodically distills recent episodes into short
    `Insight`s that persist and feed back into later goal/speech decisions
    (Generative Agents-style reflection, PHASE2.md B1).

    Cadence lives entirely in the **slow loop**: reflection becomes due from inside
    `reconsider()` when either `every_n_reconsiders` calls have passed, or at
    least `min_new_episodes` new episodes (tracked via `SkillContext.episode_count`,
    which — unlike `len(episodes)` — survives `EpisodicMemory`'s bounded window)
    have accumulated since the last reflection — whichever comes first — *and*
    at least one new episode has landed since then. That last condition keeps
    an idle agent (no new episodes) from re-reflecting over the same unchanged
    window every `every_n_reconsiders` calls, which would otherwise flood the
    bounded `ReflectionMemory` with duplicate insights and, for `LLMReflection`,
    burn a wasted LLM call per cycle.

    Reflection itself is **off the goal-delivery path**: `reconsider()` calls
    `inner.reconsider()` (the goal-setting LLM call, if any) and returns that goal
    immediately; a due reflection is handed to its own daemon thread
    (`_reflect_bg`) instead of running inline, so a slow (e.g. LLM-backed)
    `ReflectionProducer` can never add latency to goal delivery — even under the
    usual `ThreadedCognition(ReflectingCognition(inner))` composition, where
    running it inline would keep `ThreadedCognition` "busy" (and the goal stale)
    for both calls instead of one. A non-overlap guard (`_reflecting`, mirroring
    `ThreadedCognition`'s busy-flag) means at most one reflection pass runs at a
    time; if one is already in flight when reflection next becomes due, that round
    is skipped — the next `reconsider()` call will try again (cadence counters are
    only reset when a pass actually starts, so no due round is silently dropped).
    A reflection failure (bad producer, flaky LLM) is caught the same way
    `ThreadedCognition._work` guards `inner.reconsider` — it can never wedge the
    non-overlap guard or kill cognition.
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
        # Non-overlap guard for the background reflection thread (`_reflecting`,
        # under `_reflect_lock`) plus a test-observable idle signal — mirrors
        # `ThreadedCognition`'s `_busy`/`_idle` pattern exactly.
        self._reflect_lock = threading.Lock()
        self._reflecting = False
        self._idle = threading.Event()
        self._idle.set()

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        # Insights from *prior* reflection rounds inform this round's decision —
        # not any reflection this call might itself trigger below.
        ctx.insights = self.insights.recent(3)
        goal = self.inner.reconsider(ctx)

        self._reconsiders_since += 1
        new_episodes = ctx.episode_count - self._episode_count_at_last
        due = new_episodes >= 1 and (
            self._reconsiders_since >= self.every_n_reconsiders
            or new_episodes >= self.min_new_episodes
        )
        if due and ctx.episodes:
            self._start_reflection(ctx)
        return goal

    def _start_reflection(self, ctx: SkillContext) -> None:
        """Claim the non-overlap guard and launch `_reflect_bg`; a no-op (this
        round is skipped, cadence counters untouched) if a pass is already running."""
        with self._reflect_lock:
            if self._reflecting:
                return
            self._reflecting = True
            self._idle.clear()
        # Snapshot what the background thread needs — plain data (a list slice, the
        # persona), safe to hand off. `ctx` itself is reused/mutated by the caller
        # on later ticks, so the thread must not touch it directly.
        window = ctx.episodes[-self.episode_window :]
        reconsiders, count_at_last = self._reconsiders_since, self._episode_count_at_last
        self._reconsiders_since = 0
        self._episode_count_at_last = ctx.episode_count
        try:
            threading.Thread(
                target=self._reflect_bg, args=(window, ctx.persona), daemon=True
            ).start()
        except RuntimeError:  # spawn failed: _reflect_bg's finally will never run —
            # release the guard here and restore the counters so the round stays due.
            self._reconsiders_since, self._episode_count_at_last = reconsiders, count_at_last
            with self._reflect_lock:
                self._reflecting = False
                self._idle.set()

    def _reflect_bg(self, window: list[Episode], persona: Persona) -> None:
        """Runs on its own daemon thread, off the goal-delivery path entirely."""
        try:
            for text in self.reflection.reflect(window, persona):
                self.insights.record(
                    Insight(
                        text=text,
                        episode_ticks=(window[0].tick, window[-1].tick),
                        episode_count=len(window),
                    )
                )
        except Exception:  # noqa: BLE001 — a flaky producer must not wedge or kill cognition
            pass
        finally:
            with self._reflect_lock:
                self._reflecting = False
                self._idle.set()

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until any in-flight background reflection pass has finished. For
        tests — a deterministic join point instead of a sleep/poll loop."""
        return self._idle.wait(timeout)


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
