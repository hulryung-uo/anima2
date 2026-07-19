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
    WAYPOINT_CORPSE,
    WAYPOINT_RESURRECTION,
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
    interrupts_goal = True
    description = "Recover from death through a safe resurrection and reclaim an attributed corpse."

    corpse_find_timeout_ticks: int = 15
    route_stall_timeout_ticks: int = 20
    corpse_open_settle_ticks: int = 3
    item_verify_timeout_ticks: int = 4
    max_recovered_items: int = 64
    resurrection_reentry_attempts: int = 3
    resurrection_route_stall_ticks: int = 8
    resurrection_route_attempts: int = 32
    resurrection_retry_cooldown_ticks: int = 30
    corpse_route_attempts: int = 32

    _WAITING = "death_waiting_resurrection"
    _ROUTE_STOPPED = "death_route_stopped"
    _GUMP_RESPONDED = "death_gump_responded"
    _RES_ROUTE_SENT = "death_resurrection_route_sent"
    _RES_REENTRY_PHASE = "death_resurrection_reentry_phase"
    _RES_REENTRY_ATTEMPTS = "death_resurrection_reentry_attempts"
    _RES_INSIDE_WAIT = "death_resurrection_inside_wait"
    _RES_TARGET = "death_resurrection_target"
    _RES_FAILED = "death_resurrection_failed"
    _RES_CLOCK = "death_resurrection_clock"
    _RES_LAST_POS = "death_resurrection_last_pos"
    _RES_STALL = "death_resurrection_stall"
    _RES_ROUTE_ATTEMPTS = "death_resurrection_route_attempts"
    _RES_LEG_TARGET = "death_resurrection_leg_target"
    _RES_EXIT_TARGET = "death_resurrection_exit_target"
    _POST_RES_STOPPED = "death_post_resurrection_route_stopped"
    _CORPSE_PENDING = "death_corpse_pending"
    _CORPSE_SERIAL = "death_corpse_serial"
    _CORPSE_PHASE = "death_corpse_phase"
    _WAIT = "death_recovery_wait"
    _ROUTE_SENT = "death_corpse_route_sent"
    _ROUTE_ATTEMPTS = "death_corpse_route_attempts"
    _LAST_POS = "death_corpse_last_pos"
    _STALL = "death_corpse_stall"
    _HELD = "death_corpse_held"
    _HELD_GRAPHIC = "death_corpse_held_graphic"
    _HELD_AMOUNT = "death_corpse_held_amount"
    _PACK_AMOUNT_BEFORE = "death_corpse_pack_amount_before"
    _RECOVERED = "death_corpse_recovered"
    _CORPSE_HINT = "death_corpse_waypoint_hint"
    _EPISODE = "death_episode"
    _ACTIVE_EPISODE = "death_recovery_episode"

    def __init__(self, resurrection_target: tuple[int, int] | None = None) -> None:
        # Explicit targets remain useful for deterministic fixtures. Production
        # planners pass None and discover ServUO 0xE5 resurrection waypoints.
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
        self._remember_corpse_waypoint(ctx)

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

        ctx.memory[self._RES_CLOCK] = int(ctx.memory.get(self._RES_CLOCK, 0)) + 1
        target_info = self._resurrection_target(ctx)
        if target_info is not None:
            _serial, tx, ty, _map = target_info
            target = (tx, ty)
            target_pos = type(ctx.obs.player.pos)(target[0], target[1], ctx.obs.player.pos.z)
            distance = chebyshev(ctx.obs.player.pos, target_pos)
            reentry_phase = ctx.memory.get(self._RES_REENTRY_PHASE)
            if reentry_phase == "exit":
                if distance >= 4:
                    ctx.memory[self._RES_REENTRY_PHASE] = "reenter"
                    return self._walk_resurrection_leg(ctx, target_info, target)
                exit_target = ctx.memory.get(self._RES_EXIT_TARGET)
                if not isinstance(exit_target, tuple) or len(exit_target) != 2:
                    dx = -6 if ctx.obs.player.pos.x <= target[0] else 6
                    exit_target = (target[0] + dx, target[1])
                    ctx.memory[self._RES_EXIT_TARGET] = exit_target
                return self._walk_resurrection_leg(ctx, target_info, exit_target)
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
                    exit_target = (target[0] + dx, target[1])
                    ctx.memory[self._RES_EXIT_TARGET] = exit_target
                    return self._walk_resurrection_leg(
                        ctx,
                        target_info,
                        exit_target,
                    )
                self._reject_resurrection_target(ctx, target_info)
                return SkillResult(Status.RUNNING, None)
            if reentry_phase == "reenter":
                return self._walk_resurrection_leg(ctx, target_info, target)
            return self._walk_to_resurrection(ctx, target_info)

        # No usable healer: remain quarantined without packet spam rather than
        # run ordinary work as a ghost. Failed candidates become eligible again
        # only after their bounded cooldown.
        return SkillResult(Status.RUNNING, None)

    def _resurrection_target(self, ctx: SkillContext) -> tuple[int, int, int, int] | None:
        """Select/carry one healer target as ``(serial, x, y, map)``."""
        map_index = int(ctx.obs.map_index)
        if self.resurrection_target is not None:
            selected = (0, self.resurrection_target[0], self.resurrection_target[1], map_index)
            self._set_resurrection_target(ctx, selected)
            return selected

        now = int(ctx.memory.get(self._RES_CLOCK, 0))
        failed = dict(ctx.memory.get(self._RES_FAILED, {}))
        candidates: list[tuple[int, int, int, int]] = []
        mobiles = {mobile.serial: mobile for mobile in ctx.obs.mobiles}
        for waypoint in ctx.obs.waypoints:
            if waypoint.kind != WAYPOINT_RESURRECTION or waypoint.map != map_index:
                continue
            if int(failed.get(waypoint.serial, 0)) > now:
                continue
            mobile = None if waypoint.ignore_object else mobiles.get(waypoint.serial)
            pos = mobile.pos if mobile is not None else waypoint.pos
            candidates.append((waypoint.serial, pos.x, pos.y, waypoint.map))
        candidates.sort(
            key=lambda candidate: (
                chebyshev(
                    ctx.obs.player.pos,
                    type(ctx.obs.player.pos)(candidate[1], candidate[2], ctx.obs.player.pos.z),
                ),
                candidate[0],
            )
        )

        current = ctx.memory.get(self._RES_TARGET)
        if isinstance(current, tuple) and len(current) == 4:
            current = tuple(int(value) for value in current)
            if current[3] != map_index:
                self._clear_resurrection_target(ctx)
                current = None
            else:
                refreshed = next((c for c in candidates if c[0] == current[0]), None)
                if refreshed is not None:
                    self._set_resurrection_target(ctx, refreshed)
                    return refreshed
                # ServUO does not resend death waypoints after an A3 bridge
                # replacement. Preserve the episode cache while the fresh
                # world has no usable healer evidence; unrelated quest/corpse
                # markers do not prove that this selected healer disappeared.
                if not candidates:
                    return current
                self._clear_resurrection_target(ctx)

        if not candidates:
            return None
        self._set_resurrection_target(ctx, candidates[0])
        return candidates[0]

    def _set_resurrection_target(
        self, ctx: SkillContext, target: tuple[int, int, int, int]
    ) -> None:
        current = ctx.memory.get(self._RES_TARGET)
        if current == target:
            return
        # A live WanderingHealer can refresh its coordinates every tick. Keep
        # the same serial/facet candidate's total route and re-entry budgets;
        # `_walk_resurrection_leg` will restart only the changed destination.
        if (
            isinstance(current, tuple)
            and len(current) == 4
            and int(current[0]) == target[0]
            and int(current[3]) == target[3]
        ):
            ctx.memory[self._RES_TARGET] = target
            return
        self._clear_resurrection_route(ctx)
        ctx.memory[self._RES_TARGET] = target

    def _clear_resurrection_target(self, ctx: SkillContext) -> None:
        ctx.memory.pop(self._RES_TARGET, None)
        self._clear_resurrection_route(ctx)

    def _clear_resurrection_route(self, ctx: SkillContext) -> None:
        for key in (
            self._RES_ROUTE_SENT,
            self._RES_REENTRY_PHASE,
            self._RES_REENTRY_ATTEMPTS,
            self._RES_INSIDE_WAIT,
            self._RES_LAST_POS,
            self._RES_STALL,
            self._RES_ROUTE_ATTEMPTS,
            self._RES_LEG_TARGET,
            self._RES_EXIT_TARGET,
        ):
            ctx.memory.pop(key, None)

    def _walk_to_resurrection(
        self, ctx: SkillContext, target: tuple[int, int, int, int]
    ) -> SkillResult:
        return self._walk_resurrection_leg(ctx, target, (target[1], target[2]))

    def _walk_resurrection_leg(
        self,
        ctx: SkillContext,
        target: tuple[int, int, int, int],
        destination: tuple[int, int],
    ) -> SkillResult:
        """Drive one approach/exit/re-entry leg under one candidate budget."""
        if ctx.memory.get(self._RES_LEG_TARGET) != destination:
            ctx.memory[self._RES_LEG_TARGET] = destination
            for key in (self._RES_ROUTE_SENT, self._RES_LAST_POS, self._RES_STALL):
                ctx.memory.pop(key, None)
        current = (ctx.obs.player.pos.x, ctx.obs.player.pos.y)
        last = ctx.memory.get(self._RES_LAST_POS)
        stall = int(ctx.memory.get(self._RES_STALL, 0)) + 1 if last == current else 0
        ctx.memory[self._RES_LAST_POS] = current
        ctx.memory[self._RES_STALL] = stall
        attempts = int(ctx.memory.get(self._RES_ROUTE_ATTEMPTS, 0))
        should_send = not ctx.memory.get(self._RES_ROUTE_SENT)
        if stall >= self.resurrection_route_stall_ticks:
            should_send = True
            ctx.memory[self._RES_STALL] = 0
        if not should_send:
            return SkillResult(Status.RUNNING, None)
        if attempts >= self.resurrection_route_attempts:
            self._reject_resurrection_target(ctx, target)
            return SkillResult(Status.RUNNING, None)
        ctx.memory[self._RES_ROUTE_SENT] = True
        ctx.memory[self._RES_ROUTE_ATTEMPTS] = attempts + 1
        return SkillResult(Status.RUNNING, WalkTo(*destination))

    def _reject_resurrection_target(
        self, ctx: SkillContext, target: tuple[int, int, int, int]
    ) -> None:
        if target[0] == 0:
            return  # explicit fixture target keeps the pre-A4 quarantine behavior
        failed = dict(ctx.memory.get(self._RES_FAILED, {}))
        failed[target[0]] = (
            int(ctx.memory.get(self._RES_CLOCK, 0)) + self.resurrection_retry_cooldown_ticks
        )
        ctx.memory[self._RES_FAILED] = failed
        self._clear_resurrection_target(ctx)

    def _remember_corpse_waypoint(self, ctx: SkillContext) -> None:
        death_pos = ctx.memory.get("death_last_alive_pos")
        if not isinstance(death_pos, tuple) or len(death_pos) != 3:
            return
        expected = type(ctx.obs.player.pos)(*death_pos)
        candidates = [
            waypoint
            for waypoint in ctx.obs.waypoints
            if waypoint.kind == WAYPOINT_CORPSE
            and waypoint.map == ctx.obs.map_index
            and chebyshev(waypoint.pos, expected) <= 1
        ]
        if not candidates:
            return
        waypoint = min(candidates, key=lambda candidate: candidate.serial)
        ctx.memory[self._CORPSE_HINT] = (
            waypoint.serial,
            waypoint.pos.x,
            waypoint.pos.y,
            waypoint.map,
        )

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
            self._RES_TARGET,
            self._RES_FAILED,
            self._RES_CLOCK,
            self._RES_LAST_POS,
            self._RES_STALL,
            self._RES_ROUTE_ATTEMPTS,
            self._RES_LEG_TARGET,
            self._RES_EXIT_TARGET,
            self._POST_RES_STOPPED,
            self._CORPSE_PENDING,
            self._CORPSE_SERIAL,
            self._CORPSE_PHASE,
            self._WAIT,
            self._ROUTE_SENT,
            self._ROUTE_ATTEMPTS,
            self._LAST_POS,
            self._STALL,
            self._HELD,
            self._HELD_GRAPHIC,
            self._HELD_AMOUNT,
            self._PACK_AMOUNT_BEFORE,
            self._RECOVERED,
            self._CORPSE_HINT,
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
        return RESURRECTION_TITLE_CLILOC in clilocs and RESURRECTION_CONTINUE_BUTTON in replies

    def _finish_resurrection(self, ctx: SkillContext) -> None:
        ctx.memory.pop(self._WAITING, None)
        ctx.memory.pop(self._ROUTE_STOPPED, None)
        ctx.memory.pop(self._GUMP_RESPONDED, None)
        self._clear_resurrection_target(ctx)
        ctx.memory.pop(self._RES_FAILED, None)
        ctx.memory.pop(self._RES_CLOCK, None)
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
                target = self._corpse_navigation_target(ctx)
                if target is not None:
                    target_pos = type(ctx.obs.player.pos)(
                        target[0], target[1], ctx.obs.player.pos.z
                    )
                    if chebyshev(ctx.obs.player.pos, target_pos) > 2:
                        return self._walk_to_corpse(ctx, target[0], target[1])
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
        attempts = int(ctx.memory.get(self._ROUTE_ATTEMPTS, 0))
        should_send = not ctx.memory.get(self._ROUTE_SENT)
        if stall > self.route_stall_timeout_ticks:
            should_send = True
            ctx.memory[self._STALL] = 0
        if should_send:
            if attempts >= self.corpse_route_attempts:
                return self._finish_corpse(ctx, Status.FAILURE)
            ctx.memory[self._ROUTE_SENT] = True
            ctx.memory[self._ROUTE_ATTEMPTS] = attempts + 1
            return SkillResult(Status.RUNNING, WalkTo(x, y))
        return SkillResult(Status.RUNNING, None)

    def _corpse_navigation_target(self, ctx: SkillContext) -> tuple[int, int] | None:
        """Return only a location hint; ownership still needs strong evidence."""
        death_pos = ctx.memory.get("death_last_alive_pos")
        if not isinstance(death_pos, tuple) or len(death_pos) != 3:
            return None
        hint = ctx.memory.get(self._CORPSE_HINT)
        if isinstance(hint, tuple) and len(hint) == 4 and int(hint[3]) == ctx.obs.map_index:
            hinted = type(ctx.obs.player.pos)(int(hint[1]), int(hint[2]), death_pos[2])
            expected = type(ctx.obs.player.pos)(*death_pos)
            if chebyshev(hinted, expected) <= 1:
                return hinted.x, hinted.y
        return int(death_pos[0]), int(death_pos[1])

    def _loot_step(self, ctx: SkillContext, corpse_serial: int) -> SkillResult:
        backpack = next(
            (
                item
                for item in ctx.obs.items
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
                if candidate.container == backpack.serial and candidate.graphic == held_graphic
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
            item
            for item in ctx.obs.items
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
            entry.corpse: {item.serial for item in entry.entries} for entry in ctx.obs.corpse_equip
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
            self._ROUTE_ATTEMPTS,
            self._LAST_POS,
            self._STALL,
            self._HELD,
            self._HELD_GRAPHIC,
            self._HELD_AMOUNT,
            self._PACK_AMOUNT_BEFORE,
            self._RECOVERED,
            self._CORPSE_HINT,
        ):
            ctx.memory.pop(key, None)
        return SkillResult(status, None)
