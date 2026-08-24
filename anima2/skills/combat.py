"""Combat skill — engage the nearest hostile creature."""

from __future__ import annotations

from ..contract import Attack, MobileView, Walk, WarMode
from ..geometry import direction_toward
from .base import Skill, SkillContext, SkillResult, Status

# UO notoriety bytes that are valid/likely attack targets:
#   3 gray (attackable), 4 criminal, 5 enemy (orange), 6 murderer (red).
HOSTILE_NOTORIETY = frozenset({3, 4, 5, 6})


def is_hostile(mobile: MobileView) -> bool:
    """Shared target/flee population: attackable and not observably dead."""
    return (
        mobile.notoriety in HOSTILE_NOTORIETY
        and not (mobile.hits_max > 0 and mobile.hits <= 0)
    )


class Combat(Skill):
    """Toggle war mode and attack the nearest hostile within `engage_range`.

    Disabled for pacifist personas. Emits `WarMode(on=True)` once, then `Attack`.
    """

    name = "combat"
    description = "Attack the nearest hostile creature (war mode + attack)."
    engage_range: int = 10
    #: Distance at which our attacks actually land. Beyond it, CLOSE — the server does
    #: not walk us into range, so an `Attack` sent from two tiles away is a packet that
    #: does nothing, forever. Measured 2026-08-24 (audit §59): a warrior that had stepped
    #: one tile off its stand spent `act=Attackx544` — five hundred and forty-four
    #: consecutive ticks — swinging at prey pinned at `foes=d2`, `steps=8` for the whole
    #: day. It only ever worked because the village spawns its prey ADJACENT.
    #: A ranged profession raises this to its own reach; 1 is melee.
    melee_reach: int = 1
    #: Consecutive approach ticks without the position changing before the approach is
    #: abandoned for this tick (the planner's next skill gets the hands). Bounded for the
    #: same reason every other walk in this codebase is: a blocked tile must not become an
    #: infinite loop. `Attack` still goes out, so a target that wanders back into reach is
    #: still engaged.
    approach_stall_limit: int = 6
    #: Ticks to spend attacking before a stalled approach is tried again. Without this the
    #: budget above is a LIFETIME cap — the same shape `max_flee_steps` had in §55.3 —
    #: because it resets only on arriving or on moving, and a warrior that can do neither
    #: never retries. Measured 2026-08-25 on a three-warrior roster (audit §64): Bram1 and
    #: Bram2 sat at `foes=d2,d2,d2` with `act=Attackx547` and `!stalled` for the whole run
    #: while Bram0, whose first approach happened to land, banked normally.
    #:
    #: A transient block — a creature in the doorway, a tile a neighbour is crossing —
    #: must not end the day, and a permanent one must not become a walk every tick.
    approach_retry_ticks: int = 30
    _APPROACH_POS = "combat_approach_pos"
    _APPROACH_STALL = "combat_approach_stall"
    _APPROACH_TARGET = "combat_approach_target"

    def can_run(self, ctx: SkillContext) -> bool:
        return ctx.persona.combat_disposition != "pacifist" and self._target(ctx) is not None

    def step(self, ctx: SkillContext) -> SkillResult:
        target = self._target(ctx)
        if target is None:
            ctx.memory["in_war"] = False
            return SkillResult(Status.SUCCESS, None)  # nothing left to fight
        if not ctx.memory.get("in_war"):
            ctx.memory["in_war"] = True
            return SkillResult(Status.RUNNING, WarMode(on=True))
        approach = self._approach(ctx, target)
        if approach is not None:
            return approach
        return SkillResult(Status.RUNNING, Attack(serial=target.serial), reward=0.05)

    def _approach(self, ctx: SkillContext, target: MobileView) -> SkillResult | None:
        """One step toward `target` when it is out of reach, or `None` to attack.

        Stall-bounded: if the position has not changed for `approach_stall_limit`
        consecutive approach ticks the walk is abandoned and the ordinary `Attack` goes
        out instead, exactly as before this method existed. The counter resets the moment
        we move OR the target comes into reach, so a long legitimate chase never
        exhausts it.
        """
        here = ctx.obs.player.pos
        if target.distance <= self.melee_reach:
            # ONE place resets the budget. Clearing the remembered position as well would
            # reset it a second way (a missing `last` reads as "moved"), and two resets
            # for one fact means a mutant can delete either and the tests stay green.
            ctx.memory[self._APPROACH_STALL] = 0
            return None
        # A NEW TARGET IS A NEW APPROACH. The old one's blocked tile says nothing about
        # this one's, and carrying the count over means one wall spends the budget for
        # every creature that follows.
        if ctx.memory.get(self._APPROACH_TARGET) != target.serial:
            ctx.memory[self._APPROACH_TARGET] = target.serial
            ctx.memory[self._APPROACH_STALL] = 0
        last = ctx.memory.get(self._APPROACH_POS)
        moved = last != (here.x, here.y)
        stall = 0 if moved else int(ctx.memory.get(self._APPROACH_STALL, 0)) + 1
        ctx.memory[self._APPROACH_POS] = (here.x, here.y)
        if stall >= self.approach_stall_limit + self.approach_retry_ticks:
            stall = 0  # the cooldown is up: try the walk again rather than swing forever
        ctx.memory[self._APPROACH_STALL] = stall
        if stall >= self.approach_stall_limit:
            return None
        return SkillResult(Status.RUNNING,
                           Walk(direction_toward(here, target.pos), run=False))

    def _target(self, ctx: SkillContext) -> MobileView | None:
        # obs.mobiles is sorted by distance, so the first match is the nearest.
        for m in ctx.obs.mobiles:
            if is_hostile(m) and m.distance <= self.engage_range:
                return m
        return None
