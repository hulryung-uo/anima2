"""Immediate survival: retreat from danger, then bandage wounds.

This is a fast-loop state machine, not LLM cognition. It uses only the existing
Observation/Action contract: HP, nearby mobiles, backpack items, target cursor,
and journal deltas in; Walk/Use/TargetObject out. Poison/death recovery follow
after those states are exposed by the body contract (AUTONOMY-ROADMAP.md).
"""

from __future__ import annotations

import math

from ..contract import MobileView, TargetObject, Use, Walk
from ..geometry import direction_toward
from .base import Skill, SkillContext, SkillResult, Status
from .combat import is_hostile
from .harvest import BACKPACK_LAYER

BANDAGE_GRAPHICS = frozenset({0x0E21})

# ServUO Bandage.cs result messages. A completion line only proves that the
# attempt resolved: 500968 can be a failed skill roll, and 500969 is also sent
# before a poison-cure roll whose state is not exposed by the A1 contract. HP
# recovery remains the success signal.
_BANDAGE_RESOLVED = frozenset({500967, 500968, 500969})
_BANDAGE_REFUSED = frozenset({500955})
_RESOLVED_TEXT = ("finish applying", "barely help", "little damage")
_REFUSED_TEXT = ("not damaged",)


class Survive(Skill):
    """Pre-empt ordinary work when badly wounded; flee, then bandage self."""

    name = "survive"
    description = "Retreat from nearby hostiles when badly wounded, then bandage self."

    heal_below_fraction: float = 0.40
    hostile_scan_range: int = 6
    flee_hostile_count: int = 3
    max_flee_steps: int = 5
    cursor_timeout_ticks: int = 8
    apply_timeout_ticks: int = 50
    hp_confirmation_ticks: int = 3

    _PHASE = "survival_bandage_phase"
    _WAIT = "survival_bandage_wait"
    _HP_BEFORE = "survival_bandage_hp_before"
    _BANDAGE_SERIAL = "survival_bandage_serial"
    _FLEE_STEPS = "survival_flee_steps"

    def can_run(self, ctx: SkillContext) -> bool:
        phase = ctx.memory.get(self._PHASE)
        if phase is not None:
            hp_before = int(ctx.memory.get(self._HP_BEFORE, ctx.obs.player.hits))
            if ctx.obs.player.hits > hp_before:
                return True  # let step ledger the completed recovery
            if not self._wounded(ctx):
                self._reset_bandage(ctx)
                return False
            if phase == "cursor" and ctx.obs.pending_target is None:
                serial = ctx.memory.get(self._BANDAGE_SERIAL)
                if serial is not None and not any(item.serial == serial for item in ctx.obs.items):
                    self._reset_bandage(ctx)
                    return False
            return True
        if not self._wounded(ctx):
            ctx.memory[self._FLEE_STEPS] = 0
            return False
        hostiles = self._hostiles(ctx)
        bandage = self._bandage(ctx)
        if bandage is None:
            if not hostiles:
                ctx.memory[self._FLEE_STEPS] = 0
                return False
            # A bandage-less agent cannot make the planner useful by issuing the
            # same blocked move forever. Give retreat a finite opportunity, then
            # yield to combat/work/fallback until danger clears or supplies arrive.
            return int(ctx.memory.get(self._FLEE_STEPS, 0)) < self.max_flee_steps
        # Never steal a cursor opened by the work skill. Let that skill answer it;
        # survival becomes runnable on the next clean observation.
        return ctx.obs.pending_target is None

    def step(self, ctx: SkillContext) -> SkillResult:
        phase = ctx.memory.get(self._PHASE)
        if phase is not None:
            return self._bandage_step(ctx, phase)
        if not self._wounded(ctx):
            ctx.memory[self._FLEE_STEPS] = 0
            return SkillResult(Status.FAILURE, None)

        bandage = self._bandage(ctx)
        hostiles = self._hostiles(ctx)
        flee_steps = int(ctx.memory.get(self._FLEE_STEPS, 0))
        should_flee = (
            bool(hostiles)
            and flee_steps < self.max_flee_steps
            and (bandage is None or len(hostiles) >= self.flee_hostile_count)
        )
        if should_flee:
            ctx.memory[self._FLEE_STEPS] = flee_steps + 1
            return SkillResult(Status.RUNNING, Walk(self._away_direction(ctx, hostiles), run=True))

        if not hostiles:
            ctx.memory[self._FLEE_STEPS] = 0
        if bandage is None:
            return SkillResult(Status.FAILURE, None)

        ctx.memory[self._PHASE] = "cursor"
        ctx.memory[self._WAIT] = 0
        ctx.memory[self._HP_BEFORE] = ctx.obs.player.hits
        ctx.memory[self._BANDAGE_SERIAL] = bandage.serial
        return SkillResult(Status.RUNNING, Use(bandage.serial))

    def _bandage_step(self, ctx: SkillContext, phase: str) -> SkillResult:
        hp_before = int(ctx.memory.get(self._HP_BEFORE, ctx.obs.player.hits))
        if ctx.obs.player.hits > hp_before:
            self._reset_bandage(ctx)
            return SkillResult(Status.SUCCESS, None)
        if not self._wounded(ctx):
            self._reset_bandage(ctx)
            return SkillResult(Status.FAILURE, None)

        if phase == "cursor":
            cursor = ctx.obs.pending_target
            if cursor is not None and cursor.target_type == 0 and cursor.cursor_flag == 2:
                ctx.memory[self._PHASE] = "applying"
                ctx.memory[self._WAIT] = 0
                return SkillResult(Status.RUNNING, TargetObject(ctx.obs.player.serial))
            if cursor is not None:
                # A delayed work cursor can arrive after the clean observation on
                # which Use(bandage) was sent. Only an object/helpful cursor is
                # compatible with self-bandaging; leave every other cursor intact
                # so its owning skill can answer it on the next planner tick.
                self._reset_bandage(ctx)
                return SkillResult(Status.FAILURE, None)
            wait = int(ctx.memory.get(self._WAIT, 0)) + 1
            ctx.memory[self._WAIT] = wait
            if wait > self.cursor_timeout_ticks:
                self._reset_bandage(ctx)
                return SkillResult(Status.FAILURE, None)
            return SkillResult(Status.RUNNING, None)

        if phase == "confirming":
            wait = int(ctx.memory.get(self._WAIT, 0)) + 1
            ctx.memory[self._WAIT] = wait
            if wait > self.hp_confirmation_ticks:
                self._reset_bandage(ctx)
                return SkillResult(Status.FAILURE, None)
            return SkillResult(Status.RUNNING, None)

        for entry in ctx.obs.new_journal:
            text = entry.text.lower()
            if entry.cliloc in _BANDAGE_REFUSED or any(part in text for part in _REFUSED_TEXT):
                self._reset_bandage(ctx)
                return SkillResult(Status.FAILURE, None)
            if entry.cliloc in _BANDAGE_RESOLVED or any(part in text for part in _RESOLVED_TEXT):
                # Give the world snapshot a few pumps to catch up with the
                # journal packet. Without an HP delta, a resolved attempt is a
                # failure—not a positive learning signal.
                ctx.memory[self._PHASE] = "confirming"
                ctx.memory[self._WAIT] = 0
                return SkillResult(Status.RUNNING, None)

        wait = int(ctx.memory.get(self._WAIT, 0)) + 1
        ctx.memory[self._WAIT] = wait
        if wait > self.apply_timeout_ticks:
            self._reset_bandage(ctx)
            return SkillResult(Status.FAILURE, None)
        return SkillResult(Status.RUNNING, None)

    def _wounded(self, ctx: SkillContext) -> bool:
        p = ctx.obs.player
        return p.hits > 0 and p.hits_max > 0 and p.hits / p.hits_max < self.heal_below_fraction

    def _hostiles(self, ctx: SkillContext) -> list[MobileView]:
        return [
            mobile for mobile in ctx.obs.mobiles
            if mobile.distance <= self.hostile_scan_range and is_hostile(mobile)
        ]

    @staticmethod
    def _away_direction(ctx: SkillContext, hostiles: list[MobileView]) -> int:
        here = ctx.obs.player.pos
        cx = sum(m.pos.x for m in hostiles) / len(hostiles)
        cy = sum(m.pos.y for m in hostiles) / len(hostiles)
        dx, dy = here.x - cx, here.y - cy
        norm = math.hypot(dx, dy)
        if norm < 1e-6:
            dx, dy = 0.0, -1.0  # surrounded at the centroid: commit north
        step_x = 1 if dx > 0 else -1 if dx < 0 else 0
        step_y = 1 if dy > 0 else -1 if dy < 0 else 0
        target = type(here)(here.x + step_x, here.y + step_y, here.z)
        return direction_toward(here, target)

    def _bandage(self, ctx: SkillContext):
        backpack = next(
            (item for item in ctx.obs.items
             if item.layer == BACKPACK_LAYER and item.container == ctx.obs.player.serial),
            None,
        )
        if backpack is None:
            return None
        return next(
            (item for item in ctx.obs.items
             if item.graphic in BANDAGE_GRAPHICS and item.container == backpack.serial),
            None,
        )

    def _reset_bandage(self, ctx: SkillContext) -> None:
        ctx.memory.pop(self._PHASE, None)
        ctx.memory.pop(self._WAIT, None)
        ctx.memory.pop(self._HP_BEFORE, None)
        ctx.memory.pop(self._BANDAGE_SERIAL, None)
        ctx.memory[self._FLEE_STEPS] = 0
