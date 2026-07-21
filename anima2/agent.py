"""The Agent — the two-rate control loop that makes a persona *live*.

- **Fast loop** (`tick`): perceive → reflexes → planner → run a skill → act. Pure,
  deterministic, no LLM. The agent is always alive here.
- **Slow loop** (`Cognition`, async/occasional): sets the high-level `Goal` the
  planner serves, handles social/novelty, reflects. Phase 1 ships a stub; the real
  LLM cognition drops in behind this interface without touching the fast loop
  (DESIGN.md §3.3).
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Callable, Protocol

from .body import Body
from .cognition import CognitionDecision
from .contract import Action, Observation, Walk, WalkTo
from .goals import GoalAdmission, GoalFrame, GoalOutcome, GoalSource, GoalStack
from .memory import Episode, EpisodicMemory
from .persona import Persona
from .planner import Planner
from .reflexes import Reflexes
from .skill_library import SkillLibrary
from .skills.base import Goal, Skill, SkillContext, Status


_BACKPACK_LAYER = 0x15
_GOTO_TRANSIENT_KEYS = (
    "goto_target",
    "goto_walkto_last_pos",
    "goto_walkto_stall",
    "goto_walkto_retries",
    "goto_stall",
    "goto_last_pos",
)


class Cognition(Protocol):
    """The slow, goal-setting layer (LLM in production)."""

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        """Return an updated goal given the current situation (may be `None`)."""
        ...


class NullCognition:
    """Phase-1 stub: never changes the goal. Replace with an LLM-backed cognition."""

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        return ctx.goal


class Agent:
    def __init__(
        self,
        body: Body,
        persona: Persona,
        planner: Planner,
        reflexes: Reflexes | None = None,
        cognition: Cognition | None = None,
        *,
        goal: Goal | None = None,
        cognition_interval: int = 20,
        episodes_window: int = 20,
        skill_library: SkillLibrary | None = None,
        profession: str = "",
        goal_validator: Callable[[Goal, SkillContext], bool] | None = None,
        goal_progress: Callable[[Goal, SkillContext], float | None] | None = None,
        goal_admitter: (
            Callable[[Goal, SkillContext, GoalSource], GoalAdmission | None] | None
        ) = None,
        goal_policy: object | None = None,
    ) -> None:
        self.body = body
        self.persona = persona
        self.planner = planner
        self.reflexes = reflexes or Reflexes()
        self.cognition = cognition or NullCognition()
        self.cognition_interval = cognition_interval
        #: Optional collaborator (PHASE4.md item 3, `skill_library.py`) — `None`
        #: (the default) makes `tick()`'s outcome-ledger call below a byte-for-
        #: byte no-op, so every existing caller is unaffected (see
        #: `test_agent_skill_library_none_is_byte_for_byte_noop`).
        self.skill_library = skill_library
        #: The profession key (`profession.py::Profession.key`, e.g. "hunter",
        #: "miner") this agent is playing — `Agent` itself has no other notion
        #: of "job" today (`Persona` carries identity/voice, not a profession
        #: string). Only consulted by the `skill_library` ledger call below, as
        #: half of its `(skill_name, profession)` key — the same pairing
        #: `SkillLibrary.stats()` looks up by. Defaults to `""` (an explicit,
        #: harmless "unset" key) so passing `skill_library=` alone still works.
        self.profession = profession
        # Optional fail-closed admission boundary for autonomous cognition.
        # User/system APIs retain their explicit authority; only proposals
        # entering through the slow loop are constrained here.
        planner_profession = getattr(planner, "capability_profession", None)
        capability_skills: tuple[Skill, ...] = ()
        if planner_profession is not None:
            from .capabilities import (
                CapabilityPolicy,
                valid_capability_planner_lease,
            )

            if goal_policy is None:
                raise ValueError("capability planner requires its CapabilityPolicy object")
            if type(planner) is not Planner:
                raise TypeError("capability policy requires the exact factory Planner")
            if any(
                name in vars(planner)
                for name in ("preselect_interrupt", "select", "select_cached", "_select")
            ):
                raise ValueError("capability planner contains modified selection code")
            if type(goal_policy) is not CapabilityPolicy:
                raise TypeError("capability planner requires an exact CapabilityPolicy")
            if goal_validator is not None or goal_admitter is not None or goal_progress is not None:
                raise ValueError("capability policy owns admission, progress, and deadline hooks")
            policy_profession = getattr(goal_policy, "profession", None)
            planner_ids = getattr(planner, "capability_ids", frozenset())
            policy_ids = getattr(goal_policy, "capability_ids", frozenset())
            planner_lease = getattr(planner, "capability_lease", None)
            planner_skills = tuple(planner.skills)
            for skill in planner_skills:
                inner = getattr(skill, "inner", None)
                if any(callable(value) for value in vars(skill).values()) or (
                    inner is not None
                    and any(callable(value) for value in vars(inner).values())
                ):
                    raise ValueError("capability planner contains modified skill code")
            if (
                profession != policy_profession
                or planner_profession != policy_profession
                or planner_ids != policy_ids
                or not valid_capability_planner_lease(planner_lease, planner_skills)
                or planner_lease.profession != planner_profession
                or planner_lease.capability_ids != planner_ids
            ):
                raise ValueError("capability policy, profession, and planner do not match")
            capability_skills = planner_skills
            if not capability_skills:
                raise ValueError("capability planner must expose its installed skill instances")
            goal_admitter = getattr(goal_policy, "admit_goal", None)
            goal_progress = getattr(goal_policy, "goal_progress", None)
            goal_deadline_guard = getattr(goal_policy, "deadline_can_expire", None)
            goal_preemption_guard = getattr(goal_policy, "can_preempt", None)
            if not all(
                callable(hook)
                for hook in (
                    goal_admitter,
                    goal_progress,
                    goal_deadline_guard,
                    goal_preemption_guard,
                )
            ):
                raise TypeError("CapabilityPolicy is missing a required hook")
        else:
            if goal_policy is not None:
                raise ValueError("CapabilityPolicy requires a matching capability planner")
            goal_deadline_guard = None
            goal_preemption_guard = None

        self.goal_validator = goal_validator
        self.goal_progress = goal_progress
        self.goal_admitter = goal_admitter
        self.goal_deadline_guard = goal_deadline_guard
        self.goal_preemption_guard = goal_preemption_guard
        self.goal_policy = goal_policy
        self._capability_skills = capability_skills
        self._capability_skill_states = {
            id(skill): self._skill_instance_state(skill)
            for skill in capability_skills
        }
        if goal_validator is not None and goal_admitter is not None:
            raise ValueError("pass either goal_validator or goal_admitter, not both")
        #: How many recent episodes `SkillContext.episodes` carries each tick. This
        #: is the ceiling every cognition layer sees — notably `ReflectingCognition`,
        #: whose own `episode_window` can never look further back than this (it
        #: slices `ctx.episodes`, so a bigger `episode_window` than `episodes_window`
        #: is silently capped). Defaults to `ReflectingCognition`'s default
        #: (`episode_window=20`) so the common `ThreadedCognition(ReflectingCognition(
        #: inner))` composition gets its full window out of the box; bump both
        #: together if you want reflection to see further back.
        self.episodes_window = episodes_window
        self.memory: dict = {}
        self.episodes = EpisodicMemory()
        self.ticks = 0
        self.goal_stack = GoalStack()
        self._pending_route_stop = False
        self._route_in_flight = False
        self._intention_epoch = object()
        self._goal_safety_active = False
        self._last_observation: Observation | None = None
        # Capability tombstones are independent of GoalStack's bounded
        # telemetry history. The installed registry is finite, so retaining
        # one immutable canonical Goal per expired capability is bounded by
        # the policy vocabulary rather than by runtime duration.
        self._expired_capability_replays: list[Goal] = []
        if goal is not None and goal.kind == "capability":
            raise ValueError("capability Goals must enter through CapabilityPolicy admission")
        if goal is not None:
            self.goal_stack.push(goal, source=GoalSource.USER, tick=self.ticks)

    @property
    def goal(self) -> Goal | None:
        """The active goal, preserving the original single-slot public API."""

        return self.goal_stack.current_goal

    @goal.setter
    def goal(self, goal: Goal | None) -> None:
        """Authoritatively replace the live intention tree.

        Direct assignment is retained for existing launchers and live probes.
        Autonomous temporary work should use :meth:`interrupt_goal` so its
        parent is suspended and later resumed instead of discarded.
        """

        self.replace_goal(goal, source=GoalSource.USER)

    def replace_goal(
        self,
        goal: Goal | None,
        *,
        source: GoalSource = GoalSource.USER,
        deadline_ticks: int | None = None,
    ) -> GoalFrame | None:
        """Replace the entire live stack with one authoritative goal."""

        if goal is not None and goal.kind == "capability":
            raise ValueError("capability Goals must enter through CapabilityPolicy admission")

        if goal is self.goal and len(self.goal_stack) == 1 and deadline_ticks is None:
            return self.goal_stack.current
        self._require_capability_yield("replace")
        old = self.goal
        while self.goal_stack.current is not None:
            self.goal_stack.finish(GoalOutcome.REPLACED, tick=self.ticks)
        self._on_goal_transition(old, stop_route=goal is not old)
        if goal is None:
            return None
        deadline = self._deadline_from_budget(deadline_ticks)
        return self.goal_stack.push(
            goal,
            source=source,
            tick=self.ticks,
            deadline_tick=deadline,
        )

    def interrupt_goal(
        self,
        goal: Goal,
        *,
        deadline_ticks: int | None = None,
        source: GoalSource = GoalSource.SYSTEM,
    ) -> GoalFrame:
        """Push temporary work and suspend the exact current parent frame."""

        if goal.kind == "capability":
            raise ValueError("capability Goals cannot bypass CapabilityPolicy admission")

        self._require_capability_yield("interrupt")
        old = self.goal
        frame = self.goal_stack.push(
            goal,
            source=source,
            tick=self.ticks,
            deadline_tick=self._deadline_from_budget(deadline_ticks),
        )
        self._on_goal_transition(old, stop_route=old is not None)
        return frame

    def cancel_goal(self, *, expected_id: int | None = None) -> GoalFrame | None:
        """Cancel the active frame and resume its parent, if any."""

        frame = self.goal_stack.current
        if frame is None:
            return None
        self._require_capability_yield("cancel")
        old = frame.goal
        finished = self.goal_stack.finish(
            GoalOutcome.CANCELLED,
            tick=self.ticks,
            expected_id=expected_id,
        )
        self._on_goal_transition(old, stop_route=True)
        return finished

    def _deadline_from_budget(self, deadline_ticks: int | None) -> int | None:
        if deadline_ticks is None:
            return None
        if (
            not isinstance(deadline_ticks, int)
            or isinstance(deadline_ticks, bool)
            or deadline_ticks < 0
        ):
            raise ValueError("deadline_ticks must be a non-negative integer")
        return self.ticks + deadline_ticks

    @staticmethod
    def _skill_instance_state(
        skill: Skill,
    ) -> tuple[type, dict, object | None, type | None, dict | None]:
        inner = getattr(skill, "inner", None)
        return (
            type(skill),
            dict(vars(skill)),
            inner,
            type(inner) if inner is not None else None,
            dict(vars(inner)) if inner is not None else None,
        )

    def _context_goal(self, goal: Goal | None) -> Goal | None:
        """Never expose the GoalStack's canonical capability object to collaborators."""

        if goal is None or goal.kind != "capability":
            return goal
        from .capabilities import execution_goal_copy

        return execution_goal_copy(goal, self.profession)

    def _isolated_planner_context(self, ctx: SkillContext) -> SkillContext:
        """Give selection code values, then run the chosen skill on live state."""

        return replace(
            ctx,
            obs=deepcopy(ctx.obs),
            persona=deepcopy(ctx.persona),
            goal=self._context_goal(self.goal),
            memory=deepcopy(ctx.memory),
            episodes=deepcopy(ctx.episodes),
            insights=deepcopy(ctx.insights),
            goal_policy=self.goal_policy,
        )

    def _capability_planner_intact(self) -> bool:
        if self.goal_policy is None:
            return True
        if type(self.planner) is not Planner or any(
            name in vars(self.planner)
            for name in ("preselect_interrupt", "select", "select_cached", "_select")
        ):
            return False
        live = tuple(self.planner.skills)
        return bool(
            len(live) == len(self._capability_skills)
            and all(
                current is installed
                for current, installed in zip(live, self._capability_skills, strict=True)
            )
            and all(
                self._skill_instance_state(skill)
                == self._capability_skill_states.get(id(skill))
                for skill in self._capability_skills
            )
        )

    def _require_capability_yield(self, operation: str) -> None:
        """Reject external pre-emption while verified hands own a transaction."""

        frame = self.goal_stack.current
        if (
            frame is None
            or frame.goal.kind != "capability"
            or self.goal_preemption_guard is None
        ):
            return
        obs = self._last_observation
        if obs is None:
            raise RuntimeError(f"cannot {operation} capability before a safe observation")
        ctx = SkillContext(
            obs=obs,
            persona=self.persona,
            goal=frame.goal,
            goal_id=frame.id,
            goal_revision=self.goal_stack.revision,
            goal_progress=frame.progress,
            memory=self.memory,
        )
        try:
            can_preempt = bool(self.goal_preemption_guard(frame.goal, ctx))
        except Exception:  # noqa: BLE001 — transaction safety fails closed
            can_preempt = False
        if not can_preempt:
            raise RuntimeError(
                f"cannot {operation} capability before its verified safe-yield point"
            )

    def _remember_expired_capabilities(
        self, expired: tuple[GoalFrame, ...]
    ) -> None:
        for frame in expired:
            goal = frame.goal
            if goal.kind != "capability" or not goal.sealed:
                continue
            if not any(goal == prior for prior in self._expired_capability_replays):
                self._expired_capability_replays.append(goal)

    def _on_goal_transition(self, old: Goal | None, *, stop_route: bool) -> None:
        for key in _GOTO_TRANSIENT_KEYS:
            self.memory.pop(key, None)
        if stop_route and self._route_in_flight:
            self._pending_route_stop = True

    @property
    def _intention_token(self) -> tuple[object, int]:
        return (self._intention_epoch, self.goal_stack.revision)

    def _apply_cognition(
        self,
        proposal: Goal | None,
        based_on_token: object,
        ctx: SkillContext,
    ) -> bool:
        """CAS-apply a cognition proposal only while the stack is idle.

        Active and suspended goals are durable transactions.  Cognition may
        observe them, but cannot clear or replace them; it proposes the next
        goal after the current stack reaches a terminal state.
        """

        if based_on_token != self._intention_token:
            self.memory["cognition_stale_rejections"] = (
                int(self.memory.get("cognition_stale_rejections", 0)) + 1
            )
            return False
        current = self.goal
        if current is not None:
            if proposal is not current and proposal != current:
                self.memory["cognition_overwrite_rejections"] = (
                    int(self.memory.get("cognition_overwrite_rejections", 0)) + 1
                )
            return False
        if proposal is None:
            return False
        if proposal.kind == "capability" and self.goal_policy is None:
            self.memory["cognition_admission_rejections"] = (
                int(self.memory.get("cognition_admission_rejections", 0)) + 1
            )
            return False
        deadline_tick = None
        if self.goal_admitter is not None:
            try:
                admission = self.goal_admitter(proposal, ctx, GoalSource.COGNITION)
            except Exception:  # noqa: BLE001 — policy failure must fail closed
                admission = None
            if not isinstance(admission, GoalAdmission):
                self.memory["cognition_admission_rejections"] = (
                    int(self.memory.get("cognition_admission_rejections", 0)) + 1
                )
                return False
            proposal = admission.goal
            if not isinstance(proposal, Goal) or not proposal.sealed:
                self.memory["cognition_admission_rejections"] = (
                    int(self.memory.get("cognition_admission_rejections", 0)) + 1
                )
                return False
            replayed_capability = proposal.kind == "capability" and any(
                proposal == prior for prior in self._expired_capability_replays
            )
            replayed_other = proposal.kind != "capability" and any(
                frame.outcome is GoalOutcome.EXPIRED and frame.goal == proposal
                for frame in self.goal_stack.history
            )
            if replayed_capability or replayed_other:
                self.memory["cognition_expired_replay_rejections"] = (
                    int(self.memory.get("cognition_expired_replay_rejections", 0)) + 1
                )
                return False
            deadline_tick = self._deadline_from_budget(admission.deadline_ticks)
        elif self.goal_validator is not None:
            try:
                admitted = bool(self.goal_validator(proposal, ctx))
            except Exception:  # noqa: BLE001 — policy failure must fail closed
                admitted = False
            if not admitted:
                self.memory["cognition_admission_rejections"] = (
                    int(self.memory.get("cognition_admission_rejections", 0)) + 1
                )
                return False
        self.goal_stack.push(
            proposal,
            source=GoalSource.COGNITION,
            tick=self.ticks,
            deadline_tick=deadline_tick,
        )
        return True

    def _record_route_action(self, action: Action, obs: Observation) -> None:
        if isinstance(action, WalkTo):
            self._route_in_flight = (action.x, action.y) != (
                obs.player.pos.x,
                obs.player.pos.y,
            )
        elif isinstance(action, Walk):
            # A manual movement command replaces the native async route.
            self._route_in_flight = False

    def _safety_interrupt_active(self, obs: Observation) -> bool:
        """Whether deterministic survival/recovery currently owns the hands."""

        player = getattr(obs, "player", None)
        observably_dead = bool(
            player is not None
            and (
                getattr(player, "dead", False)
                or (getattr(player, "hits_max", 0) > 0 and getattr(player, "hits", 0) <= 0)
            )
        )
        return bool(
            observably_dead
            or self.memory.get("death_waiting_resurrection")
            or self.memory.get("death_corpse_pending")
            or self.memory.get("survival_bandage_phase") is not None
            or int(self.memory.get("survival_flee_steps", 0)) > 0
        )

    def tick(self) -> Action | None:
        """Run one fast-loop iteration. Returns the action taken (or `None`)."""
        obs = self.body.observe()
        self._last_observation = obs
        # Preserve the B1 deadline semantics for every legacy planner. A
        # capability policy gets one stricter path below: an owned transaction
        # must reach a verified yield point before its due frame can disappear.
        if self.goal_deadline_guard is None:
            active_before_expiry = self.goal_stack.current
            expired = self.goal_stack.expire_due(self.ticks)
            if expired:
                self._remember_expired_capabilities(expired)
                top_expired = active_before_expiry is not None and any(
                    frame.id == active_before_expiry.id for frame in expired
                )
                if top_expired:
                    self._on_goal_transition(active_before_expiry.goal, stop_route=True)
        # Keep a rolling *living* snapshot even during a corpse run. A second
        # death is a new recovery episode and must use the state immediately
        # before that death, not evidence frozen for the previous corpse.
        if not obs.player.dead and obs.player.body:
            backpacks = {
                item.serial
                for item in obs.items
                if item.container == obs.player.serial and item.layer == _BACKPACK_LAYER
            }
            equipped = {
                item.serial
                for item in obs.items
                if item.container == obs.player.serial
                and item.layer > 0
                and item.layer != _BACKPACK_LAYER
            }

            # Include nested backpack contents as ownership evidence. ItemView
            # does not expose "is container", so treating every discovered item
            # as a possible parent is the safe generic traversal: only actual
            # parent serials can pull another item into the set.
            pack_owned: set[int] = set()
            frontier = set(backpacks)
            while frontier:
                discovered = {
                    item.serial
                    for item in obs.items
                    if item.container in frontier and item.serial not in pack_owned
                }
                pack_owned.update(discovered)
                frontier = discovered

            self.memory["death_rolling_alive_body"] = obs.player.body
            self.memory["death_rolling_alive_pos"] = (
                obs.player.pos.x,
                obs.player.pos.y,
                obs.player.pos.z,
            )
            self.memory["death_rolling_equipped"] = equipped
            self.memory["death_rolling_pack_owned"] = pack_owned

        was_dead = bool(self.memory.get("death_observed_dead", False))
        if obs.player.dead and not was_dead:
            # Freeze attribution evidence exactly once on the alive->dead edge.
            # Clearing first prevents a dead-on-login/new-episode observation
            # without usable rolling evidence from inheriting an older corpse.
            for key in (
                "death_last_alive_body",
                "death_last_alive_pos",
                "death_last_equipped",
                "death_last_pack_owned",
            ):
                self.memory.pop(key, None)
            body = int(self.memory.get("death_rolling_alive_body", 0))
            pos = self.memory.get("death_rolling_alive_pos")
            if body > 0 and isinstance(pos, tuple) and len(pos) == 3:
                self.memory["death_last_alive_body"] = body
                self.memory["death_last_alive_pos"] = pos
                self.memory["death_last_equipped"] = set(
                    self.memory.get("death_rolling_equipped", set())
                )
                self.memory["death_last_pack_owned"] = set(
                    self.memory.get("death_rolling_pack_owned", set())
                )
            self.memory["death_episode"] = int(self.memory.get("death_episode", 0)) + 1
        self.memory["death_observed_dead"] = obs.player.dead
        frame = self.goal_stack.current
        ctx = SkillContext(
            obs=obs,
            persona=self.persona,
            goal=self._context_goal(self.goal),
            goal_id=frame.id if frame is not None else None,
            goal_revision=self.goal_stack.revision,
            goal_progress=frame.progress if frame is not None else None,
            memory=self.memory,
            episodes=self.episodes.recent(self.episodes_window),
            episode_count=self.episodes.total_recorded,
        )
        if self.goal_deadline_guard is not None:
            active_before_expiry = self.goal_stack.current
            defer_ids: frozenset[int] = frozenset()
            if (
                active_before_expiry is not None
                and active_before_expiry.goal.kind == "capability"
                and active_before_expiry.deadline_tick is not None
                and self.ticks >= active_before_expiry.deadline_tick
            ):
                try:
                    can_expire = bool(
                        self.goal_deadline_guard(active_before_expiry.goal, ctx)
                    )
                except Exception:  # noqa: BLE001 — transaction safety fails closed
                    can_expire = False
                if not can_expire:
                    defer_ids = frozenset({active_before_expiry.id})
            expired = self.goal_stack.expire_due(self.ticks, defer_ids=defer_ids)
            if expired:
                self._remember_expired_capabilities(expired)
                top_expired = active_before_expiry is not None and any(
                    frame.id == active_before_expiry.id for frame in expired
                )
                if top_expired:
                    self._on_goal_transition(active_before_expiry.goal, stop_route=True)
                frame = self.goal_stack.current
                ctx.goal = self._context_goal(self.goal)
                ctx.goal_id = frame.id if frame is not None else None
                ctx.goal_revision = self.goal_stack.revision
                ctx.goal_progress = frame.progress if frame is not None else None
        planner_intact = self._capability_planner_intact()
        if not planner_intact:
            self.memory["capability_skill_rejections"] = (
                int(self.memory.get("capability_skill_rejections", 0)) + 1
            )
            interrupt_skill, interrupt_checks = None, {}
        elif self.goal_policy is not None:
            # Exact shipped safety predicates intentionally normalize their
            # own live FSM memory (cooldowns, stale flee state, aborted
            # cursors). Invoke the known Planner implementation directly;
            # ordinary capability selection below still receives a copy.
            interrupt_skill, interrupt_checks = Planner.preselect_interrupt(
                self.planner, ctx
            )
        else:
            interrupt_skill, interrupt_checks = self.planner.preselect_interrupt(ctx)
        safety_interrupt = interrupt_skill is not None or self._safety_interrupt_active(obs)
        if safety_interrupt != self._goal_safety_active:
            self._goal_safety_active = safety_interrupt
            self.goal_stack.invalidate_proposals()
            if safety_interrupt:
                self._on_goal_transition(self.goal, stop_route=True)

        # Only observations can advance durable progress. Skip every safety or
        # route-ownership transition: its movement belongs to the interrupting
        # transaction, not the durable parent frame.
        if not self._pending_route_stop and not safety_interrupt:
            self.goal_stack.observe(obs, self.ticks)
            progress_goal = self.goal
            if progress_goal is not None and self.goal_progress is not None:
                try:
                    policy_progress = self.goal_progress(progress_goal, ctx)
                except Exception:  # noqa: BLE001 — progress policy is advisory
                    policy_progress = None
                if policy_progress is not None:
                    self.goal_stack.set_progress(policy_progress, tick=self.ticks)
            frame = self.goal_stack.current
            ctx.goal_revision = self.goal_stack.revision
            ctx.goal_progress = frame.progress if frame is not None else None

        # Slow loop, sampled: cognition proposes work.  A version-aware wrapper
        # (ThreadedCognition) returns the revision of the snapshot it used, so a
        # late answer cannot win an ABA race after interrupt/resume.
        if not safety_interrupt and self.ticks % self.cognition_interval == 0:
            token = self._intention_token
            cognition_ctx: SkillContext | None = ctx
            if self.goal_policy is not None:
                # Capability cognition receives values, never the live mutable
                # world/memory objects that admission and deterministic hands
                # trust. The sole supported slow-loop side effect is a pending
                # spoken line, merged explicitly below under the same CAS.
                try:
                    cognition_ctx = replace(
                        ctx,
                        obs=deepcopy(ctx.obs),
                        persona=deepcopy(ctx.persona),
                        goal=(
                            None
                            if ctx.goal is None
                            else (
                                Goal(ctx.goal.kind, dict(ctx.goal.params))
                                if ctx.goal.sealed
                                else deepcopy(ctx.goal)
                            )
                        ),
                        memory=deepcopy(ctx.memory),
                        episodes=deepcopy(ctx.episodes),
                        insights=deepcopy(ctx.insights),
                        goal_policy=None,
                    )
                except Exception:  # noqa: BLE001 — isolation must fail closed
                    cognition_ctx = None
                    self.memory["cognition_isolation_rejections"] = (
                        int(self.memory.get("cognition_isolation_rejections", 0)) + 1
                    )
            if cognition_ctx is None:
                proposal = None
                based_on_token = token
            else:
                pending_before = cognition_ctx.memory.get("pending_say")
                reconsider_versioned = getattr(
                    self.cognition, "reconsider_versioned", None
                )
                if reconsider_versioned is not None:
                    decision = reconsider_versioned(cognition_ctx, token)
                    if not isinstance(decision, CognitionDecision):
                        raise TypeError("reconsider_versioned must return CognitionDecision")
                    proposal = decision.goal
                    based_on_token = decision.based_on_token
                    if (
                        decision.pending_say is not None
                        and based_on_token == self._intention_token
                    ):
                        self.memory["pending_say"] = decision.pending_say
                else:
                    proposal = self.cognition.reconsider(cognition_ctx)
                    based_on_token = token
                    pending_after = cognition_ctx.memory.get("pending_say")
                    if (
                        isinstance(pending_after, str)
                        and pending_after != pending_before
                        and based_on_token == self._intention_token
                    ):
                        self.memory["pending_say"] = pending_after
            if self._apply_cognition(proposal, based_on_token, ctx):
                frame = self.goal_stack.current
                ctx.goal = self._context_goal(self.goal)
                ctx.goal_id = frame.id if frame is not None else None
                ctx.goal_revision = self.goal_stack.revision
                ctx.goal_progress = frame.progress if frame is not None else None

            # SkillContext is a convenient cognition snapshot, never an
            # execution-authority channel. Restore every planner-relevant
            # reference from Agent/GoalStack truth even when admission did not
            # apply, so a buggy or adversarial Cognition cannot smuggle a Goal,
            # frame id, observation, memory object, or execution lease through
            # the mutable snapshot it received.
            frame = self.goal_stack.current
            ctx.obs = obs
            ctx.persona = self.persona
            ctx.goal = self._context_goal(self.goal)
            ctx.goal_id = frame.id if frame is not None else None
            ctx.goal_revision = self.goal_stack.revision
            ctx.goal_progress = frame.progress if frame is not None else None
            ctx.memory = self.memory
            ctx.goal_policy = None

        self.ticks += 1

        # 1) Reflexes pre-empt everything.
        action = self.reflexes.check(obs, self.persona)
        if action is not None:
            self._record_route_action(action, obs)
            self.body.act(action)
            return action

        # `WalkTo` continues inside the native body after Python stops emitting
        # actions.  Goal pre-emption must therefore cancel that route explicitly
        # before the child (or resumed parent) is allowed to act.
        if self._pending_route_stop:
            self._pending_route_stop = False
            action = WalkTo(x=obs.player.pos.x, y=obs.player.pos.y)
            self._record_route_action(action, obs)
            self.body.act(action)
            return action

        # 2) Planner picks a skill; the skill produces an action.
        # Cognition sees no execution lease. Install the Agent-owned policy
        # only at the last handoff to deterministic planner/skill code, and
        # overwrite any value an adversarial Cognition placed on the shared
        # context while proposing a Goal.
        ctx.goal = self._context_goal(self.goal)
        ctx.goal_policy = self.goal_policy
        if interrupt_skill is not None:
            skill = interrupt_skill
        elif self.goal_policy is not None:
            if not planner_intact:
                return None
            try:
                selection_ctx = self._isolated_planner_context(ctx)
            except Exception:  # noqa: BLE001 — planner isolation fails closed
                self.memory["planner_isolation_rejections"] = (
                    int(self.memory.get("planner_isolation_rejections", 0)) + 1
                )
                return None
            skill = Planner._select(self.planner, selection_ctx, interrupt_checks)
            observer = self.planner.selection_observer
            if observer is not None:
                observer(skill, selection_ctx)
        else:
            skill = self.planner.select_cached(ctx, interrupt_checks)

        active_frame = self.goal_stack.current
        active_capability = bool(
            active_frame is not None and active_frame.goal.kind == "capability"
        )
        if self.goal_policy is not None:
            installed = next(
                (candidate for candidate in self._capability_skills if skill is candidate),
                None,
            )
            state_matches = bool(
                installed is not None
                and self._capability_planner_intact()
                and self._skill_instance_state(installed)
                == self._capability_skill_states.get(id(installed))
            )
            if not state_matches:
                self.memory["capability_skill_rejections"] = (
                    int(self.memory.get("capability_skill_rejections", 0)) + 1
                )
                return None

        if active_capability:
            from .profession import (
                CapabilityBoundSkill,
                CapabilityGoalComplete,
                CapabilityWait,
            )
            from .skills import RecoverDeath, SpeakPending, Survive

            allowed_types = {
                CapabilityBoundSkill,
                CapabilityGoalComplete,
                CapabilityWait,
                RecoverDeath,
                SpeakPending,
                Survive,
            }
            if type(skill) not in allowed_types or ctx.goal is None:
                self.memory["capability_skill_rejections"] = (
                    int(self.memory.get("capability_skill_rejections", 0)) + 1
                )
                return None
        result = skill.step(ctx)

        # Record terminal/rewarded outcomes to episodic memory (the learning signal
        # + cognition context). RUNNING-with-no-reward steps are too noisy to log.
        if result.reward or result.status is not Status.RUNNING:
            self.episodes.record(
                Episode(
                    tick=self.ticks,
                    kind="skill",
                    summary=f"{skill.name} → {result.status.name.lower()}",
                    reward=result.reward,
                    pos=(obs.player.pos.x, obs.player.pos.y),
                )
            )
            # PHASE4.md item 3: the exact same filter as the episodic record
            # above (not a separate, looser one) — a skill_library=None agent
            # never reaches this branch at all, and one wired in only ever
            # ledgers what episodic memory itself would have kept.
            if self.skill_library is not None:
                self.skill_library.record_outcome(
                    skill.name, self.profession, result.reward, result.status
                )

        # A terminal goal-serving skill finishes only the exact frame it ran
        # against.  Its suspended parent then resumes with the same Goal object
        # and accumulated progress; SUCCESS and FAILURE remain distinguishable
        # in bounded history.
        if result.status is not Status.RUNNING and ctx.goal_id is not None and skill.consumes_goal:
            outcome = (
                GoalOutcome.SUCCESS if result.status is Status.SUCCESS else GoalOutcome.FAILURE
            )
            current = self.goal_stack.current
            if current is not None and current.id == ctx.goal_id:
                if result.status is Status.SUCCESS:
                    current.progress = replace(current.progress, value=1.0)
                old = current.goal
                self.goal_stack.finish(outcome, tick=self.ticks, expected_id=ctx.goal_id)
                self._on_goal_transition(old, stop_route=True)
        if result.action is not None:
            self._record_route_action(result.action, obs)
            # A terminal skill is no longer allowed to own a native route,
            # including a WalkTo emitted by its terminal result itself.
            if result.status is not Status.RUNNING and skill.consumes_goal:
                if self._route_in_flight:
                    self._pending_route_stop = True
            self.body.act(result.action)
        return result.action

    def run(self, ticks: int) -> None:
        """Run the fast loop for a fixed number of ticks (synchronous demo driver)."""
        for _ in range(ticks):
            if not self.body.connected:
                break
            self.tick()
