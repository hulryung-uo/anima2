"""The Agent — the two-rate control loop that makes a persona *live*.

- **Fast loop** (`tick`): perceive → reflexes → planner → run a skill → act. Pure,
  deterministic, no LLM. The agent is always alive here.
- **Slow loop** (`Cognition`, async/occasional): sets the high-level `Goal` the
  planner serves, handles social/novelty, reflects. Phase 1 ships a stub; the real
  LLM cognition drops in behind this interface without touching the fast loop
  (DESIGN.md §3.3).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Protocol

from .body import Body
from .cognition import CognitionDecision
from .contract import Action, Observation, Walk, WalkTo
from .goals import GoalFrame, GoalOutcome, GoalSource, GoalStack
from .memory import Episode, EpisodicMemory
from .persona import Persona
from .planner import Planner
from .reflexes import Reflexes
from .skill_library import SkillLibrary
from .skills.base import Goal, SkillContext, Status


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
        self.goal_validator = goal_validator
        self.goal_progress = goal_progress
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

        if goal is self.goal and len(self.goal_stack) == 1 and deadline_ticks is None:
            return self.goal_stack.current
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
        if self.goal_validator is not None:
            try:
                admitted = bool(self.goal_validator(proposal, ctx))
            except Exception:  # noqa: BLE001 — policy failure must fail closed
                admitted = False
            if not admitted:
                self.memory["cognition_admission_rejections"] = (
                    int(self.memory.get("cognition_admission_rejections", 0)) + 1
                )
                return False
        self.goal_stack.push(proposal, source=GoalSource.COGNITION, tick=self.ticks)
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
        active_before_expiry = self.goal_stack.current
        expired = self.goal_stack.expire_due(self.ticks)
        if expired:
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
            goal=self.goal,
            goal_id=frame.id if frame is not None else None,
            goal_revision=self.goal_stack.revision,
            goal_progress=frame.progress if frame is not None else None,
            memory=self.memory,
            episodes=self.episodes.recent(self.episodes_window),
            episode_count=self.episodes.total_recorded,
        )
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
            reconsider_versioned = getattr(self.cognition, "reconsider_versioned", None)
            if reconsider_versioned is not None:
                decision = reconsider_versioned(ctx, token)
                if not isinstance(decision, CognitionDecision):
                    raise TypeError("reconsider_versioned must return CognitionDecision")
                proposal = decision.goal
                based_on_token = decision.based_on_token
                if decision.pending_say is not None and based_on_token == self._intention_token:
                    self.memory["pending_say"] = decision.pending_say
            else:
                proposal = self.cognition.reconsider(ctx)
                based_on_token = token
            if self._apply_cognition(proposal, based_on_token, ctx):
                frame = self.goal_stack.current
                ctx.goal = self.goal
                ctx.goal_id = frame.id if frame is not None else None
                ctx.goal_revision = self.goal_stack.revision
                ctx.goal_progress = frame.progress if frame is not None else None

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
        skill = interrupt_skill or self.planner.select_cached(ctx, interrupt_checks)
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
