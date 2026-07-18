"""Death interrupt: quarantine ghost actions, resurrect safely, recover own corpse."""

from __future__ import annotations

from ..contract import (
    Drop,
    GumpResponse,
    GumpView,
    PickUp,
    TargetCancel,
    Use,
    WalkTo,
)
from ..geometry import chebyshev
from .base import Skill, SkillContext, SkillResult, Status
from .harvest import BACKPACK_LAYER

CORPSE_GRAPHIC = 0x2006
RESURRECTION_TITLE_CLILOC = 1011022
RESURRECTION_CONTINUE_BUTTON = 1


class RecoverDeath(Skill):
    """Stop stale work, accept only a verified free resurrection, reclaim gear."""

    name = "recover_death"
    description = "Recover from death through a safe resurrection and reclaim an attributed corpse."

    corpse_find_timeout_ticks: int = 15
    route_stall_timeout_ticks: int = 20
    corpse_open_settle_ticks: int = 3
    item_verify_timeout_ticks: int = 4
    max_recovered_items: int = 64
    resurrection_reentry_attempts: int = 3

    _WAITING = "death_waiting_resurrection"
    _ROUTE_STOPPED = "death_route_stopped"
    _GUMP_RESPONDED = "death_gump_responded"
    _RES_ROUTE_SENT = "death_resurrection_route_sent"
    _RES_REENTRY_PHASE = "death_resurrection_reentry_phase"
    _RES_REENTRY_ATTEMPTS = "death_resurrection_reentry_attempts"
    _RES_INSIDE_WAIT = "death_resurrection_inside_wait"
    _POST_RES_STOPPED = "death_post_resurrection_route_stopped"
    _CORPSE_PENDING = "death_corpse_pending"
    _CORPSE_SERIAL = "death_corpse_serial"
    _CORPSE_PHASE = "death_corpse_phase"
    _WAIT = "death_recovery_wait"
    _ROUTE_SENT = "death_corpse_route_sent"
    _LAST_POS = "death_corpse_last_pos"
    _STALL = "death_corpse_stall"
    _HELD = "death_corpse_held"
    _HELD_GRAPHIC = "death_corpse_held_graphic"
    _HELD_AMOUNT = "death_corpse_held_amount"
    _PACK_AMOUNT_BEFORE = "death_corpse_pack_amount_before"
    _RECOVERED = "death_corpse_recovered"
    _EPISODE = "death_episode"
    _ACTIVE_EPISODE = "death_recovery_episode"

    def __init__(self, resurrection_target: tuple[int, int] | None = None) -> None:
        # A staged/live-fixture escape hatch until A4 exposes ServUO 0xE5 healer
        # waypoints. Production profession planners intentionally pass None.
        self.resurrection_target = resurrection_target

    def can_run(self, ctx: SkillContext) -> bool:
        return bool(
            ctx.obs.player.dead
            or ctx.memory.get(self._WAITING)
            or ctx.memory.get(self._CORPSE_PENDING)
        )

    def step(self, ctx: SkillContext) -> SkillResult:
        if ctx.obs.player.dead:
            return self._dead_step(ctx)
        if ctx.memory.get(self._WAITING):
            if not ctx.memory.get(self._POST_RES_STOPPED):
                ctx.memory[self._POST_RES_STOPPED] = True
                pos = ctx.obs.player.pos
                return SkillResult(Status.RUNNING, WalkTo(pos.x, pos.y))
            self._finish_resurrection(ctx)
            return SkillResult(Status.SUCCESS, None)
        if ctx.memory.get(self._CORPSE_PENDING):
            return self._corpse_step(ctx)
        return SkillResult(Status.FAILURE, None)

    def _dead_step(self, ctx: SkillContext) -> SkillResult:
        episode = ctx.memory.get(self._EPISODE)
        if episode is not None and ctx.memory.get(self._ACTIVE_EPISODE) != episode:
            self._start_death_episode(ctx, int(episode))
        ctx.memory[self._WAITING] = True
        ctx.memory.pop(self._CORPSE_PENDING, None)

        # WalkTo is driven asynchronously by the bridge. Replacing its route
        # with the current tile prevents a pre-death work route from dragging a
        # newly resurrected character away on the next pump.
        if not ctx.memory.get(self._ROUTE_STOPPED):
            ctx.memory[self._ROUTE_STOPPED] = True
            pos = ctx.obs.player.pos
            return SkillResult(Status.RUNNING, WalkTo(pos.x, pos.y))

        if ctx.obs.pending_target is not None:
            return SkillResult(Status.RUNNING, TargetCancel())

        gump = next((g for g in ctx.obs.gumps if self._is_free_resurrection(g)), None)
        if gump is not None:
            identity = (gump.serial, gump.gump_id)
            if ctx.memory.get(self._GUMP_RESPONDED) != identity:
                ctx.memory[self._GUMP_RESPONDED] = identity
                return SkillResult(
                    Status.RUNNING,
                    GumpResponse(gump.serial, gump.gump_id, RESURRECTION_CONTINUE_BUTTON),
                )

        target = self.resurrection_target
        if target is not None:
            target_pos = type(ctx.obs.player.pos)(target[0], target[1], ctx.obs.player.pos.z)
            distance = chebyshev(ctx.obs.player.pos, target_pos)
            reentry_phase = ctx.memory.get(self._RES_REENTRY_PHASE)
            if reentry_phase == "exit":
                if distance >= 4:
                    ctx.memory[self._RES_REENTRY_PHASE] = "reenter"
                    return SkillResult(Status.RUNNING, WalkTo(*target))
                return SkillResult(Status.RUNNING, None)
            if distance <= 2:
                inside_wait = int(ctx.memory.get(self._RES_INSIDE_WAIT, 0)) + 1
                ctx.memory[self._RES_INSIDE_WAIT] = inside_wait
                if inside_wait <= 3:
                    return SkillResult(Status.RUNNING, None)
                ctx.memory[self._RES_INSIDE_WAIT] = 0
                attempts = int(ctx.memory.get(self._RES_REENTRY_ATTEMPTS, 0))
                if attempts < self.resurrection_reentry_attempts:
                    ctx.memory[self._RES_REENTRY_ATTEMPTS] = attempts + 1
                    ctx.memory[self._RES_REENTRY_PHASE] = "exit"
                    dx = -6 if ctx.obs.player.pos.x <= target[0] else 6
                    return SkillResult(
                        Status.RUNNING,
                        WalkTo(target[0] + dx, target[1]),
                    )
                return SkillResult(Status.RUNNING, None)
            if not ctx.memory.get(self._RES_ROUTE_SENT):
                ctx.memory[self._RES_ROUTE_SENT] = True
                return SkillResult(Status.RUNNING, WalkTo(*target))
            if reentry_phase == "reenter":
                return SkillResult(Status.RUNNING, None)

        # No healer discovery until waypoint support lands: remain quarantined
        # without packet spam rather than run ordinary work as a ghost.
        return SkillResult(Status.RUNNING, None)

    def _start_death_episode(self, ctx: SkillContext, episode: int) -> None:
        """Atomically discard every transient belonging to an earlier death."""
        for key in (
            self._WAITING,
            self._ROUTE_STOPPED,
            self._GUMP_RESPONDED,
            self._RES_ROUTE_SENT,
            self._RES_REENTRY_PHASE,
            self._RES_REENTRY_ATTEMPTS,
            self._RES_INSIDE_WAIT,
            self._POST_RES_STOPPED,
            self._CORPSE_PENDING,
            self._CORPSE_SERIAL,
            self._CORPSE_PHASE,
            self._WAIT,
            self._ROUTE_SENT,
            self._LAST_POS,
            self._STALL,
            self._HELD,
            self._HELD_GRAPHIC,
            self._HELD_AMOUNT,
            self._PACK_AMOUNT_BEFORE,
            self._RECOVERED,
        ):
            ctx.memory.pop(key, None)
        ctx.memory[self._ACTIVE_EPISODE] = episode

    @staticmethod
    def _is_free_resurrection(gump: GumpView) -> bool:
        clilocs: set[int] = set()
        replies: set[int] = set()
        for element in gump.elements:
            if element.get("type") == "button" and element.get("pageflag") == 1:
                replies.add(int(element.get("reply_id", -1)))
            text = element.get("text")
            if isinstance(text, dict):
                cliloc = text.get("cliloc")
                if isinstance(cliloc, dict) and isinstance(cliloc.get("id"), int):
                    clilocs.add(cliloc["id"])
        return (
            RESURRECTION_TITLE_CLILOC in clilocs
            and RESURRECTION_CONTINUE_BUTTON in replies
        )

    def _finish_resurrection(self, ctx: SkillContext) -> None:
        ctx.memory.pop(self._WAITING, None)
        ctx.memory.pop(self._ROUTE_STOPPED, None)
        ctx.memory.pop(self._GUMP_RESPONDED, None)
        ctx.memory.pop(self._RES_ROUTE_SENT, None)
        ctx.memory.pop(self._RES_REENTRY_PHASE, None)
        ctx.memory.pop(self._RES_REENTRY_ATTEMPTS, None)
        ctx.memory.pop(self._RES_INSIDE_WAIT, None)
        ctx.memory.pop(self._POST_RES_STOPPED, None)
        ctx.memory[self._CORPSE_PENDING] = True
        ctx.memory[self._CORPSE_PHASE] = "find"
        ctx.memory[self._WAIT] = 0

    def _corpse_step(self, ctx: SkillContext) -> SkillResult:
        corpse_serial = ctx.memory.get(self._CORPSE_SERIAL)
        if corpse_serial is None:
            candidates = self._own_corpse_candidates(ctx)
            if len(candidates) > 1:
                return self._finish_corpse(ctx, Status.FAILURE)
            if not candidates:
                wait = int(ctx.memory.get(self._WAIT, 0)) + 1
                ctx.memory[self._WAIT] = wait
                if wait > self.corpse_find_timeout_ticks:
                    # Young-character auto-return and an already-decayed corpse
                    # are valid no-corpse outcomes; do not deadlock the old goal.
                    return self._finish_corpse(ctx, Status.SUCCESS)
                return SkillResult(Status.RUNNING, None)
            corpse_serial = candidates[0]
            ctx.memory[self._CORPSE_SERIAL] = corpse_serial
            ctx.memory[self._WAIT] = 0

        corpse = next((item for item in ctx.obs.items if item.serial == corpse_serial), None)
        if corpse is None:
            return self._finish_corpse(ctx, Status.FAILURE)

        if corpse.distance > 2:
            return self._walk_to_corpse(ctx, corpse.pos.x, corpse.pos.y)

        phase = ctx.memory.get(self._CORPSE_PHASE, "find")
        if phase in {"find", "walk"}:
            ctx.memory[self._CORPSE_PHASE] = "open"
            ctx.memory[self._WAIT] = 0
            return SkillResult(Status.RUNNING, Use(corpse_serial))

        if phase == "open":
            wait = int(ctx.memory.get(self._WAIT, 0)) + 1
            ctx.memory[self._WAIT] = wait
            if wait <= self.corpse_open_settle_ticks:
                return SkillResult(Status.RUNNING, None)
            ctx.memory[self._CORPSE_PHASE] = "loot"

        return self._loot_step(ctx, corpse_serial)

    def _walk_to_corpse(self, ctx: SkillContext, x: int, y: int) -> SkillResult:
        pos = ctx.obs.player.pos
        current = (pos.x, pos.y)
        last = ctx.memory.get(self._LAST_POS)
        stall = int(ctx.memory.get(self._STALL, 0)) + 1 if last == current else 0
        ctx.memory[self._LAST_POS] = current
        ctx.memory[self._STALL] = stall
        ctx.memory[self._CORPSE_PHASE] = "walk"
        if stall > self.route_stall_timeout_ticks:
            return self._finish_corpse(ctx, Status.FAILURE)
        if not ctx.memory.get(self._ROUTE_SENT):
            ctx.memory[self._ROUTE_SENT] = True
            return SkillResult(Status.RUNNING, WalkTo(x, y))
        return SkillResult(Status.RUNNING, None)

    def _loot_step(self, ctx: SkillContext, corpse_serial: int) -> SkillResult:
        backpack = next(
            (
                item for item in ctx.obs.items
                if item.layer == BACKPACK_LAYER and item.container == ctx.obs.player.serial
            ),
            None,
        )
        if backpack is None:
            return self._finish_corpse(ctx, Status.FAILURE)

        recovered = set(ctx.memory.get(self._RECOVERED, set()))
        held = ctx.memory.get(self._HELD)
        phase = ctx.memory.get(self._CORPSE_PHASE)
        if held is not None and phase == "drop":
            ctx.memory[self._CORPSE_PHASE] = "verify"
            ctx.memory[self._WAIT] = 0
            return SkillResult(
                Status.RUNNING,
                Drop(held, 0xFFFF, 0xFFFF, 0, backpack.serial),
            )
        if held is not None and phase == "verify":
            item = next((item for item in ctx.obs.items if item.serial == held), None)
            held_graphic = int(ctx.memory.get(self._HELD_GRAPHIC, -1))
            held_amount = int(ctx.memory.get(self._HELD_AMOUNT, 0))
            pack_amount_before = int(ctx.memory.get(self._PACK_AMOUNT_BEFORE, 0))
            pack_amount_now = sum(
                candidate.amount
                for candidate in ctx.obs.items
                if candidate.container == backpack.serial
                and candidate.graphic == held_graphic
            )
            exact_drop = item is not None and item.container == backpack.serial
            merged_drop = (
                item is None
                and held_graphic >= 0
                and held_amount > 0
                and pack_amount_now >= pack_amount_before + held_amount
            )
            if exact_drop or merged_drop:
                recovered.add(held)
                ctx.memory[self._RECOVERED] = recovered
                ctx.memory.pop(self._HELD, None)
                ctx.memory.pop(self._HELD_GRAPHIC, None)
                ctx.memory.pop(self._HELD_AMOUNT, None)
                ctx.memory.pop(self._PACK_AMOUNT_BEFORE, None)
                ctx.memory[self._CORPSE_PHASE] = "loot"
            else:
                wait = int(ctx.memory.get(self._WAIT, 0)) + 1
                ctx.memory[self._WAIT] = wait
                if wait > self.item_verify_timeout_ticks:
                    return self._finish_corpse(ctx, Status.FAILURE)
                return SkillResult(Status.RUNNING, None)

        contents = [
            item for item in ctx.obs.items
            if item.container == corpse_serial and item.serial not in recovered
        ]
        if not contents or len(recovered) >= self.max_recovered_items:
            return self._finish_corpse(ctx, Status.SUCCESS)

        item = contents[0]
        ctx.memory[self._HELD] = item.serial
        ctx.memory[self._HELD_GRAPHIC] = item.graphic
        ctx.memory[self._HELD_AMOUNT] = max(1, item.amount)
        ctx.memory[self._PACK_AMOUNT_BEFORE] = sum(
            candidate.amount
            for candidate in ctx.obs.items
            if candidate.container == backpack.serial and candidate.graphic == item.graphic
        )
        ctx.memory[self._CORPSE_PHASE] = "drop"
        return SkillResult(Status.RUNNING, PickUp(item.serial, max(1, item.amount)))

    def _own_corpse_candidates(self, ctx: SkillContext) -> list[int]:
        body = int(ctx.memory.get("death_last_alive_body", 0))
        death_pos = ctx.memory.get("death_last_alive_pos")
        equipped_before = set(ctx.memory.get("death_last_equipped", set()))
        pack_owned_before = set(ctx.memory.get("death_last_pack_owned", set()))
        if body <= 0 or not isinstance(death_pos, tuple) or len(death_pos) != 3:
            return []

        exact_links = {
            link.corpse for link in ctx.obs.corpse_of if link.killed == ctx.obs.player.serial
        }
        equipped = {
            entry.corpse: {item.serial for item in entry.entries}
            for entry in ctx.obs.corpse_equip
        }
        contents: dict[int, set[int]] = {}
        for item in ctx.obs.items:
            if item.container is not None:
                contents.setdefault(item.container, set()).add(item.serial)
        candidates: list[int] = []
        for item in ctx.obs.items:
            if item.graphic != CORPSE_GRAPHIC or item.amount != body:
                continue
            if chebyshev(item.pos, type(item.pos)(*death_pos)) > 1:
                continue
            if exact_links and item.serial not in exact_links:
                continue
            equipment_proof = bool(equipped_before & equipped.get(item.serial, set()))
            contents_proof = bool(pack_owned_before & contents.get(item.serial, set()))
            if not (equipment_proof or contents_proof):
                continue
            candidates.append(item.serial)
        return candidates

    def _finish_corpse(self, ctx: SkillContext, status: Status) -> SkillResult:
        for key in (
            self._CORPSE_PENDING,
            self._CORPSE_SERIAL,
            self._CORPSE_PHASE,
            self._WAIT,
            self._ROUTE_SENT,
            self._LAST_POS,
            self._STALL,
            self._HELD,
            self._HELD_GRAPHIC,
            self._HELD_AMOUNT,
            self._PACK_AMOUNT_BEFORE,
            self._RECOVERED,
        ):
            ctx.memory.pop(key, None)
        return SkillResult(status, None)
