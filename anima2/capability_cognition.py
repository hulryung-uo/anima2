"""Closed-vocabulary cognition for verified operation capabilities.

The model may choose only ``idle`` or one opaque id that trusted code already
found ready in the immutable registry. It never emits a Goal kind, action,
coordinate, parameter, source, binding, or deadline. The returned request is
still advisory: :class:`anima2.agent.Agent` rechecks readiness against its live
context and is the only component that seals and schedules the canonical Goal.

Use this cognition behind ``ThreadedCognition`` whenever a client is supplied;
``complete()`` is deliberately synchronous here so the existing wrapper owns
the one background-call/CAS implementation used throughout the project.
"""

from __future__ import annotations

import json
from typing import Any

from .capabilities import CAPABILITIES, capability_goal, ready_capability_ids
from .llm import LLMClient
from .skills.base import Goal, SkillContext

_MAX_RESPONSE_CHARS = 4096
_CAPABILITY_KEYS = frozenset({"schema", "decision", "capability"})
_IDLE_KEYS = frozenset({"schema", "decision"})


class _DuplicateKey(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def _strict_decision(raw: object) -> tuple[str, str | None] | None:
    """Parse one complete JSON object without coercion or recovery."""

    if type(raw) is not str or not raw or len(raw) > _MAX_RESPONSE_CHARS:
        return None
    try:
        data = json.loads(
            raw,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if type(data) is not dict or type(data.get("schema")) is not int:
        return None
    if data.get("schema") != 1 or type(data.get("decision")) is not str:
        return None
    if data["decision"] == "idle" and frozenset(data) == _IDLE_KEYS:
        return ("idle", None)
    if (
        data["decision"] == "capability"
        and frozenset(data) == _CAPABILITY_KEYS
        and type(data.get("capability")) is str
    ):
        return ("capability", data["capability"])
    return None


class CapabilityCognition:
    """Choose at most one observation-ready id from the installed vocabulary."""

    def __init__(self, client: LLMClient | None, profession: str) -> None:
        capability_ids = tuple(
            capability_id
            for bound_profession, capability_id in CAPABILITIES
            if bound_profession == profession
        )
        if type(profession) is not str or not capability_ids:
            raise ValueError(f"profession {profession!r} has no installed capabilities")
        self.client = client
        self.profession = profession
        self.capability_ids = capability_ids

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        # A durable frame owns cognition until it reaches a terminal state.
        # In particular, do not generate speech or another model call between
        # PickUp and Drop; SpeakPending must never starve transaction hands.
        if ctx.goal is not None:
            return ctx.goal

        ready = ready_capability_ids(self.profession, ctx)
        if not ready:
            return None
        if self.client is None:
            return capability_goal(self.profession, ready[0])

        try:
            raw = self.client.complete(self._system(), self._situation(ready))
        except Exception:  # noqa: BLE001 — transport failure keeps offline autonomy alive
            return capability_goal(self.profession, ready[0])
        parsed = _strict_decision(raw)
        if parsed is None:
            return None
        decision, selected = parsed
        if decision == "idle":
            return None
        if selected not in ready:
            return None
        return capability_goal(self.profession, selected)

    def _system(self) -> str:
        return (
            f"You select work for a {self.profession} in Ultima Online. "
            "Reply with exactly one JSON object and no prose. Choose only an "
            "eligible opaque capability id shown by the user, or idle. "
            'Capability schema: {"schema":1,"decision":"capability",'
            '"capability":"<exact id>"}. Idle schema: '
            '{"schema":1,"decision":"idle"}. Do not add keys.'
        )

    def _situation(self, ready: tuple[str, ...]) -> str:
        lines = ["Eligible capability ids:"]
        for capability_id in ready:
            binding = CAPABILITIES[(self.profession, capability_id)]
            lines.append(f"- {capability_id}: {binding.skill_type.description}")
        lines.append("Choose one exact id or idle.")
        return "\n".join(lines)
