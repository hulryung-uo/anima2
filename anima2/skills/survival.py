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
SKILL_ANATOMY = 1
SKILL_HEALING = 17
MIN_CURE_SKILL = 60.0

# ServUO Bandage.cs result messages. A completion line only proves that the
# attempt resolved: 500968 can be a failed skill roll, and 500969 is also sent
# before a poison-cure roll whose state is not exposed by the A1 contract. HP
# recovery remains the success signal.
_BANDAGE_RESOLVED = frozenset({500967, 500968, 500969})
_BANDAGE_REFUSED = frozenset({500955})
_CURE_FAILED = frozenset({1010060})
_RESOLVED_TEXT = ("finish applying", "barely help", "little damage")
_REFUSED_TEXT = ("not damaged",)
_CURE_FAILED_TEXT = ("failed to cure",)


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
    cure_retry_cooldown_ticks: int = 30

    _PHASE = "survival_bandage_phase"
    _WAIT = "survival_bandage_wait"
    _HP_BEFORE = "survival_bandage_hp_before"
    _POISON_BEFORE = "survival_bandage_poison_before"
    _LAST_HP = "survival_bandage_last_hp"
    _LAST_POISON = "survival_bandage_last_poison"
    _CONFIRM_HP = "survival_bandage_confirm_hp"
    _CONFIRM_POISON = "survival_bandage_confirm_poison"
    _RECOVERY_DELTA_AGE = "survival_bandage_recovery_delta_age"
    _BANDAGE_SERIAL = "survival_bandage_serial"
    _FLEE_STEPS = "survival_flee_steps"
    _CURE_COOLDOWN = "survival_cure_cooldown"

    def can_run(self, ctx: SkillContext) -> bool:
        if self._observably_dead(ctx):
            if ctx.memory.get(self._PHASE) is not None:
                self._abort_bandage(ctx)
            return False
        if ctx.memory.get("death_waiting_resurrection"):
            # The death interrupt gets one living tick to stop its healer route
            # and confirm resurrection before low-HP bandaging can preempt it.
            return False
        if not ctx.obs.player.poisoned:
            ctx.memory.pop(self._CURE_COOLDOWN, None)
        elif ctx.memory.get(self._PHASE) is None:
            cooldown = int(ctx.memory.get(self._CURE_COOLDOWN, 0))
            if cooldown > 0:
                ctx.memory[self._CURE_COOLDOWN] = cooldown - 1
        phase = ctx.memory.get(self._PHASE)
        if phase is not None:
            if phase == "cursor" and ctx.obs.pending_target is None:
                serial = ctx.memory.get(self._BANDAGE_SERIAL)
                if serial is not None and not any(item.serial == serial for item in ctx.obs.items):
                    self._abort_bandage(ctx)
                    return False
            # Once a bandage has been targeted, keep its server-side context
            # alive until a result message or a bounded timeout.  T2A natural
            # regeneration can add one HP during the roughly 16-second apply
            # time; treating that uncorrelated delta as success resets this FSM
            # and the next Use cancels the still-running BandageContext.
            return True
        if not self._needs_recovery(ctx):
            ctx.memory[self._FLEE_STEPS] = 0
            return False
        hostiles = self._hostiles(ctx)
        bandage = self._usable_bandage(ctx)
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
        if self._observably_dead(ctx):
            return self._abort_bandage(ctx)
        phase = ctx.memory.get(self._PHASE)
        if phase is not None:
            return self._bandage_step(ctx, phase)
        if not self._needs_recovery(ctx):
            ctx.memory[self._FLEE_STEPS] = 0
            return SkillResult(Status.FAILURE, None)

        bandage = self._usable_bandage(ctx)
        hostiles = self._hostiles(ctx)
        flee_steps = int(ctx.memory.get(self._FLEE_STEPS, 0))
        should_flee = (
            bool(hostiles)
            and flee_steps < self.max_flee_steps
            and (
                bandage is None
                or ctx.obs.player.poisoned
                or len(hostiles) >= self.flee_hostile_count
            )
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
        ctx.memory[self._POISON_BEFORE] = ctx.obs.player.poisoned
        ctx.memory[self._LAST_HP] = ctx.obs.player.hits
        ctx.memory[self._LAST_POISON] = ctx.obs.player.poisoned
        ctx.memory[self._BANDAGE_SERIAL] = bandage.serial
        return SkillResult(Status.RUNNING, Use(bandage.serial))

    def _bandage_step(self, ctx: SkillContext, phase: str) -> SkillResult:
        if ctx.obs.player.dead:
            return self._abort_bandage(ctx)

        journal = [(entry.cliloc, entry.text.lower()) for entry in ctx.obs.new_journal]
        for cliloc, text in journal:
            if cliloc in _BANDAGE_REFUSED or any(part in text for part in _REFUSED_TEXT):
                return self._abort_bandage(ctx)
        for cliloc, text in journal:
            if cliloc in _CURE_FAILED or any(part in text for part in _CURE_FAILED_TEXT):
                return self._abort_bandage(ctx)

        if phase == "cursor":
            if not self._needs_recovery(ctx):
                return self._abort_bandage(ctx)
            cursor = ctx.obs.pending_target
            if cursor is not None and cursor.target_type == 0 and cursor.cursor_flag == 2:
                ctx.memory[self._PHASE] = "applying"
                ctx.memory[self._WAIT] = 0
                self._remember_observation(ctx)
                return SkillResult(Status.RUNNING, TargetObject(ctx.obs.player.serial))
            if cursor is not None:
                # A delayed work cursor can arrive after the clean observation on
                # which Use(bandage) was sent. Only an object/helpful cursor is
                # compatible with self-bandaging; leave every other cursor intact
                # so its owning skill can answer it on the next planner tick.
                return self._abort_bandage(ctx)
            wait = int(ctx.memory.get(self._WAIT, 0)) + 1
            ctx.memory[self._WAIT] = wait
            if wait > self.cursor_timeout_ticks:
                return self._abort_bandage(ctx)
            return SkillResult(Status.RUNNING, None)

        if phase == "confirming":
            confirm_hp = int(ctx.memory.get(self._CONFIRM_HP, ctx.obs.player.hits))
            confirm_poison = bool(ctx.memory.get(self._CONFIRM_POISON, False))
            if self._observed_recovery(ctx, confirm_hp, confirm_poison):
                self._reset_bandage(ctx)
                return SkillResult(Status.SUCCESS, None)
            wait = int(ctx.memory.get(self._WAIT, 0)) + 1
            ctx.memory[self._WAIT] = wait
            if wait > self.hp_confirmation_ticks:
                return self._abort_bandage(ctx)
            return SkillResult(Status.RUNNING, None)

        for cliloc, text in journal:
            if cliloc in _BANDAGE_RESOLVED or any(part in text for part in _RESOLVED_TEXT):
                last_hp = int(ctx.memory.get(self._LAST_HP, ctx.obs.player.hits))
                last_poison = bool(ctx.memory.get(self._LAST_POISON, ctx.obs.player.poisoned))
                delta_age = int(
                    ctx.memory.get(self._RECOVERY_DELTA_AGE, self.hp_confirmation_ticks + 1)
                )
                if (
                    self._observed_recovery(ctx, last_hp, last_poison)
                    or delta_age <= self.hp_confirmation_ticks
                ):
                    self._reset_bandage(ctx)
                    return SkillResult(Status.SUCCESS, None)
                # Give the world snapshot a few pumps to catch up with the
                # journal packet.  The confirmation baseline is the observation
                # carrying the resolved line, not HP at bandage start: an older
                # natural-regeneration delta is not evidence for this attempt.
                ctx.memory[self._PHASE] = "confirming"
                ctx.memory[self._WAIT] = 0
                ctx.memory[self._CONFIRM_HP] = ctx.obs.player.hits
                ctx.memory[self._CONFIRM_POISON] = ctx.obs.player.poisoned
                return SkillResult(Status.RUNNING, None)

        wait = int(ctx.memory.get(self._WAIT, 0)) + 1
        ctx.memory[self._WAIT] = wait
        if wait > self.apply_timeout_ticks:
            return self._abort_bandage(ctx)
        self._track_unresolved_observation(ctx)
        return SkillResult(Status.RUNNING, None)

    def _wounded(self, ctx: SkillContext) -> bool:
        p = ctx.obs.player
        return p.hits > 0 and p.hits_max > 0 and p.hits / p.hits_max < self.heal_below_fraction

    def _needs_recovery(self, ctx: SkillContext) -> bool:
        if self._observably_dead(ctx):
            return False
        return self._wounded(ctx) or (
            ctx.obs.player.poisoned and self._can_cure_with_bandages(ctx)
        )

    @staticmethod
    def _can_cure_with_bandages(ctx: SkillContext) -> bool:
        values = {skill.id: skill.value for skill in ctx.obs.skills}
        return (
            values.get(SKILL_HEALING, 0.0) >= MIN_CURE_SKILL
            and values.get(SKILL_ANATOMY, 0.0) >= MIN_CURE_SKILL
        )

    @staticmethod
    def _observably_dead(ctx: SkillContext) -> bool:
        player = ctx.obs.player
        return player.dead or (player.hits_max > 0 and player.hits <= 0)

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

    def _usable_bandage(self, ctx: SkillContext):
        if ctx.obs.player.poisoned:
            if not self._can_cure_with_bandages(ctx):
                return None
            if int(ctx.memory.get(self._CURE_COOLDOWN, 0)) > 0:
                return None
        return self._bandage(ctx)

    @staticmethod
    def _observed_recovery(ctx: SkillContext, hp: int, poisoned: bool) -> bool:
        return ctx.obs.player.hits > hp or (poisoned and not ctx.obs.player.poisoned)

    def _remember_observation(self, ctx: SkillContext) -> None:
        ctx.memory[self._LAST_HP] = ctx.obs.player.hits
        ctx.memory[self._LAST_POISON] = ctx.obs.player.poisoned

    def _track_unresolved_observation(self, ctx: SkillContext) -> None:
        last_hp = int(ctx.memory.get(self._LAST_HP, ctx.obs.player.hits))
        last_poison = bool(ctx.memory.get(self._LAST_POISON, ctx.obs.player.poisoned))
        if self._observed_recovery(ctx, last_hp, last_poison):
            ctx.memory[self._RECOVERY_DELTA_AGE] = 0
        elif self._RECOVERY_DELTA_AGE in ctx.memory:
            ctx.memory[self._RECOVERY_DELTA_AGE] = (
                int(ctx.memory[self._RECOVERY_DELTA_AGE]) + 1
            )
        self._remember_observation(ctx)

    def _abort_bandage(self, ctx: SkillContext) -> SkillResult:
        poison_attempt = bool(ctx.memory.get(self._POISON_BEFORE, False))
        self._reset_bandage(ctx)
        if poison_attempt:
            ctx.memory[self._CURE_COOLDOWN] = self.cure_retry_cooldown_ticks
        return SkillResult(Status.FAILURE, None)

    def _reset_bandage(self, ctx: SkillContext) -> None:
        ctx.memory.pop(self._PHASE, None)
        ctx.memory.pop(self._WAIT, None)
        ctx.memory.pop(self._HP_BEFORE, None)
        ctx.memory.pop(self._POISON_BEFORE, None)
        ctx.memory.pop(self._LAST_HP, None)
        ctx.memory.pop(self._LAST_POISON, None)
        ctx.memory.pop(self._CONFIRM_HP, None)
        ctx.memory.pop(self._CONFIRM_POISON, None)
        ctx.memory.pop(self._RECOVERY_DELTA_AGE, None)
        ctx.memory.pop(self._BANDAGE_SERIAL, None)
        ctx.memory[self._FLEE_STEPS] = 0
