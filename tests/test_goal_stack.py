"""B1 goal-stack acceptance tests through the Agent's public API.

These tests intentionally avoid GoalStack internals beyond its documented
``current_frame``/``history`` views.  Interrupt completion is driven through
normal Agent ticks so the assertions cover planner/cognition integration, not
just a standalone container.
"""

from __future__ import annotations

import threading

import pytest

from anima2.agent import Agent, NullCognition
from anima2.cognition import ThreadedCognition
from anima2.contract import ItemView, Position, Use, WalkTo
from anima2.goals import GoalOutcome, GoalSource, GoalStack, GoalState
from anima2.mock_body import MockBody
from anima2.persona import Persona
from anima2.planner import Planner
from anima2.skills import GoTo, Survive, Wander
from anima2.skills.base import (
    Goal,
    Skill,
    SkillContext,
    SkillResult,
    Status,
)


class _RunningGoalSkill(Skill):
    def __init__(self, *kinds: str) -> None:
        self._kinds = set(kinds)
        self.seen: list[Goal] = []

    def can_run(self, ctx: SkillContext) -> bool:
        return ctx.goal is not None and ctx.goal.kind in self._kinds

    def step(self, ctx: SkillContext) -> SkillResult:
        assert ctx.goal is not None
        self.seen.append(ctx.goal)
        return SkillResult(Status.RUNNING)


class _TerminalGoalSkill(Skill):
    consumes_goal = True

    def __init__(self, kind: str, status: Status) -> None:
        self._kind = kind
        self._status = status

    def can_run(self, ctx: SkillContext) -> bool:
        return ctx.goal is not None and ctx.goal.kind == self._kind

    def step(self, ctx: SkillContext) -> SkillResult:
        return SkillResult(self._status)


class _CountingInactiveInterrupt(Skill):
    interrupts_goal = True

    def __init__(self) -> None:
        self.can_run_calls = 0

    def can_run(self, ctx: SkillContext) -> bool:
        self.can_run_calls += 1
        return False

    def step(self, ctx: SkillContext) -> SkillResult:
        raise AssertionError("inactive interrupt must not run")


class _FixedCognition:
    def __init__(self, answer: Goal | None) -> None:
        self.answer = answer
        self.calls = 0

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        self.calls += 1
        return self.answer


class _CustomPlanner(Planner):
    def __init__(self, skills: list[Skill]) -> None:
        super().__init__(skills)
        self.select_calls = 0

    def select(self, ctx: SkillContext) -> Skill:
        self.select_calls += 1
        return super().select(ctx)


class _SlowFirstCognition:
    """Hold one proposal until the test has completed an interrupt/resume ABA."""

    def __init__(self, answer: Goal) -> None:
        self.answer = answer
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            self.release.wait(timeout=2)
            ctx.memory["pending_say"] = "stale background speech"
            return self.answer
        return ctx.goal


def _agent(
    goal: Goal | None,
    skills: list[Skill],
    *,
    cognition: object | None = None,
) -> tuple[Agent, MockBody]:
    body = MockBody()
    body.player.pos = Position(100, 100, 0)
    agent = Agent(
        body=body,
        persona=Persona(name="GoalTester"),
        planner=Planner(skills),
        cognition=cognition or NullCognition(),
        goal=goal,
        cognition_interval=1,
    )
    return agent, body


def test_nested_interrupts_resume_in_lifo_order_with_goal_identity() -> None:
    parent = Goal(kind="work", params={"token": "parent"})
    child = Goal(kind="recover", params={"token": "child"})
    grandchild = Goal(kind="goto", params={"token": "grandchild"})
    agent, _ = _agent(parent, [_RunningGoalSkill("work", "recover", "goto")])

    agent.interrupt_goal(child, source=GoalSource.SYSTEM)
    agent.interrupt_goal(grandchild, source=GoalSource.SYSTEM)

    assert agent.goal is grandchild
    assert agent.goal_stack.current_frame is not None
    assert agent.goal_stack.current_frame.goal is grandchild

    agent.cancel_goal()
    assert agent.goal is child
    assert agent.goal_stack.current_frame.goal is child

    agent.cancel_goal()
    assert agent.goal is parent
    assert agent.goal_stack.current_frame.goal is parent
    assert [frame.goal for frame in agent.goal_stack.history[-2:]] == [grandchild, child]


@pytest.mark.parametrize("terminal", [Status.SUCCESS, Status.FAILURE])
def test_terminal_interrupt_pops_child_and_resumes_parent(
    terminal: Status,
) -> None:
    parent = Goal(kind="work", params={"token": "same-parent"})
    child = Goal(kind="recover", params={"token": terminal.name})
    parent_skill = _RunningGoalSkill("work")
    agent, _ = _agent(
        parent,
        [_TerminalGoalSkill("recover", terminal), parent_skill],
    )
    agent.interrupt_goal(child)

    agent.tick()
    assert agent.goal is parent
    assert agent.goal_stack.current_frame.goal is parent
    assert agent.goal_stack.history[-1].goal is child

    agent.tick()
    assert parent_skill.seen[-1] is parent


def test_interrupt_deadline_expiry_resumes_parent() -> None:
    parent = Goal(kind="work", params={"token": "parent"})
    child = Goal(kind="recover", params={"token": "deadline"})
    agent, _ = _agent(parent, [_RunningGoalSkill("work", "recover")])
    agent.interrupt_goal(child, deadline_ticks=1)

    agent.tick()
    agent.tick()

    assert agent.goal is parent
    assert agent.goal_stack.current_frame.goal is parent
    assert agent.goal_stack.history[-1].goal is child


@pytest.mark.parametrize(
    "answer",
    [None, Goal(kind="explore", params={"token": "replacement"})],
)
def test_cognition_cannot_overwrite_an_active_interrupt(answer: Goal | None) -> None:
    parent = Goal(kind="work", params={"token": "parent"})
    child = Goal(kind="recover", params={"token": "interrupt"})
    agent, _ = _agent(
        parent,
        [_RunningGoalSkill("recover")],
        cognition=_FixedCognition(answer),
    )
    agent.interrupt_goal(child)

    agent.tick()

    assert agent.goal is child
    assert agent.goal_stack.current_frame.goal is child


def test_goto_progress_is_derived_from_observed_position() -> None:
    goal = Goal(kind="goto", params={"target": Position(110, 100, 0)})
    agent, body = _agent(goal, [_RunningGoalSkill("goto")])

    agent.tick()
    frame = agent.goal_stack.current_frame
    assert frame is not None
    initial = frame.progress.value

    # No skill result claims progress. Only the next Observation moves closer.
    body.player.pos = Position(105, 100, 0)
    agent.tick()

    assert agent.goal_stack.current_frame is frame
    assert frame.progress.value > initial


def test_buried_parent_deadline_expires_while_child_survives() -> None:
    parent = Goal(kind="work", params={"token": "obsolete"})
    child = Goal(kind="recover", params={"token": "still-needed"})
    stack = GoalStack()
    parent_frame = stack.push(parent, tick=0, deadline_tick=2)
    child_frame = stack.push(child, tick=1, deadline_tick=10)

    expired = stack.expire_due(2)

    assert expired == (parent_frame,)
    assert parent_frame.state is GoalState.TERMINAL
    assert parent_frame.outcome is GoalOutcome.EXPIRED
    assert stack.frames == (child_frame,)
    assert stack.current_frame is child_frame
    assert child_frame.state is GoalState.ACTIVE


def test_goal_stack_depth_overflow_is_fail_closed() -> None:
    stack = GoalStack(max_depth=1)
    original = stack.push(Goal(kind="work"), tick=0)
    revision = stack.revision

    with pytest.raises(OverflowError):
        stack.push(Goal(kind="recover"), tick=1)

    assert stack.frames == (original,)
    assert stack.current_frame is original
    assert original.state is GoalState.ACTIVE
    assert original.outcome is None
    assert stack.history == ()
    assert stack.revision == revision


def test_skill_context_legacy_positional_arguments_keep_their_meaning() -> None:
    body = MockBody()
    goal = Goal(kind="work")
    memory = {"legacy": True}
    ctx = SkillContext(
        body.observe(),
        Persona(name="Legacy"),
        goal,
        memory,
        ["episode"],
        7,
        ["insight"],
    )

    assert ctx.goal is goal
    assert ctx.memory is memory
    assert ctx.episodes == ["episode"]
    assert ctx.episode_count == 7
    assert ctx.insights == ["insight"]
    assert ctx.goal_id is None


@pytest.mark.parametrize("operation", ["finish", "replace"])
def test_expected_id_mismatch_is_fail_closed(operation: str) -> None:
    stack = GoalStack()
    original = stack.push(Goal(kind="work"), tick=0)
    revision = stack.revision

    with pytest.raises(LookupError):
        if operation == "finish":
            stack.finish(GoalOutcome.CANCELLED, tick=1, expected_id=original.id + 1)
        else:
            stack.replace(Goal(kind="explore"), tick=1, expected_id=original.id + 1)

    assert stack.frames == (original,)
    assert stack.current_frame is original
    assert original.state is GoalState.ACTIVE
    assert original.outcome is None
    assert stack.history == ()
    assert stack.revision == revision


def test_interrupting_goto_stops_native_route_before_child_runs() -> None:
    parent = Goal(kind="goto", params={"target": Position(130, 100, 0)})
    child = Goal(kind="recover")
    child_skill = _RunningGoalSkill("recover")
    agent, body = _agent(parent, [GoTo(), child_skill])
    assert agent.tick() == WalkTo(x=130, y=100)
    agent.interrupt_goal(child)

    action = agent.tick()

    assert action == WalkTo(x=100, y=100)
    assert body.player.pos == Position(100, 100, 0)
    assert child_skill.seen == []

    agent.tick()
    assert child_skill.seen == [child]


def test_first_survival_tick_freezes_parent_and_stops_native_route() -> None:
    parent = Goal(kind="goto", params={"target": Position(130, 100, 0)})
    adversary = _FixedCognition(Goal(kind="goto", params={"target": Position(1, 1, 0)}))
    agent, body = _agent(
        parent,
        [Survive(), GoTo(), Wander()],
        cognition=adversary,
    )
    body.player.hits = body.player.hits_max = 100
    backpack = ItemView(2, 0x0E75, 1, body.player.pos, body.player.serial, 0x15, 0)
    bandage = ItemView(3, 0x0E21, 10, body.player.pos, backpack.serial, 0, 0)
    body.items = {backpack.serial: backpack, bandage.serial: bandage}

    assert agent.tick() == WalkTo(x=130, y=100)
    frame = agent.goal_stack.current_frame
    assert frame is not None
    progress_before = frame.progress
    cognition_calls_before = adversary.calls

    # Simulate one native-route step and a wound arriving in the same new
    # Observation. Survive has no phase/flee memory yet on this first tick.
    body.player.pos = Position(101, 100, 0)
    body.player.hits = 30
    stop = agent.tick()

    assert stop == WalkTo(x=101, y=100)
    assert frame.progress is progress_before
    assert adversary.calls == cognition_calls_before

    action = agent.tick()
    assert action == Use(serial=bandage.serial)
    assert frame.progress is progress_before
    assert adversary.calls == cognition_calls_before


def test_custom_planner_selection_seam_remains_live() -> None:
    body = MockBody()
    inactive = _CountingInactiveInterrupt()
    planner = _CustomPlanner([inactive, Wander()])
    agent = Agent(body, Persona(name="Planner"), planner)

    agent.tick()

    assert planner.select_calls == 1
    assert inactive.can_run_calls == 1


def test_stale_threaded_cognition_is_rejected_after_parent_aba() -> None:
    parent = Goal(kind="work", params={"token": "same-object"})
    late_proposal = Goal(kind="explore", params={"token": "stale"})
    slow = _SlowFirstCognition(late_proposal)
    cognition = ThreadedCognition(slow)
    agent, _ = _agent(
        parent,
        [_RunningGoalSkill("work")],
        cognition=cognition,
    )

    # This tick starts the slow pass against the original parent revision.
    agent.tick()
    assert slow.started.wait(timeout=1)
    parent_frame = agent.goal_stack.current_frame
    assert parent_frame is not None

    child_frame = agent.interrupt_goal(Goal(kind="recover"))
    agent.cancel_goal(expected_id=child_frame.id)
    assert agent.goal is parent
    assert agent.goal_stack.current_frame is parent_frame

    slow.release.set()
    assert cognition.wait_idle(timeout=2)
    agent.tick()

    assert agent.goal is parent
    assert agent.goal_stack.current_frame is parent_frame
    assert late_proposal not in [frame.goal for frame in agent.goal_stack.frames]
    assert agent.memory["cognition_stale_rejections"] == 1
    assert "pending_say" not in agent.memory


def test_fresh_cognition_proposal_is_accepted_while_idle() -> None:
    proposal = Goal(kind="explore", params={"token": "fresh"})
    agent, _ = _agent(
        None,
        [_RunningGoalSkill("explore")],
        cognition=_FixedCognition(proposal),
    )

    agent.tick()

    assert agent.goal is proposal
    assert agent.goal_stack.current_frame is not None
    assert agent.goal_stack.current_frame.goal is proposal
    assert agent.goal_stack.current_frame.source is GoalSource.COGNITION


def test_threaded_decision_cannot_cross_between_agents() -> None:
    proposal = Goal(kind="explore", params={"token": "agent-a-only"})
    slow = _SlowFirstCognition(proposal)
    shared = ThreadedCognition(slow)
    agent_a, _ = _agent(
        Goal(kind="work"),
        [_RunningGoalSkill("work")],
        cognition=shared,
    )
    agent_b, _ = _agent(
        None,
        [_RunningGoalSkill("explore"), Wander()],
        cognition=shared,
    )

    agent_a.tick()
    assert slow.started.wait(timeout=1)
    slow.release.set()
    assert shared.wait_idle(timeout=2)

    agent_b.tick()

    assert agent_b.goal is None
    assert agent_b.memory["cognition_stale_rejections"] == 1
    assert "pending_say" not in agent_b.memory


@pytest.mark.parametrize("safety_state", ["dead", "corpse_pending"])
def test_safety_movement_does_not_advance_active_goto_progress(
    safety_state: str,
) -> None:
    goal = Goal(kind="goto", params={"target": Position(110, 100, 0)})
    agent, body = _agent(goal, [_RunningGoalSkill("goto")])
    agent.tick()
    frame = agent.goal_stack.current_frame
    assert frame is not None
    progress_before_safety = frame.progress
    revision_before_safety = agent.goal_stack.revision

    body.player.pos = Position(104, 100, 0)
    if safety_state == "dead":
        body.player.dead = True
    else:
        agent.memory["death_corpse_pending"] = True
    agent.tick()

    assert agent.goal_stack.current_frame is frame
    assert frame.progress is progress_before_safety
    # Entering the out-of-stack safety interrupt invalidates in-flight
    # cognition even though the durable parent progress is frozen.
    assert agent.goal_stack.revision > revision_before_safety
    safety_revision = agent.goal_stack.revision

    body.player.dead = False
    agent.memory.pop("death_corpse_pending", None)
    body.player.pos = Position(105, 100, 0)
    agent.tick()

    assert frame.progress is not progress_before_safety
    assert frame.progress.last_position == (105, 100, 0)
    assert frame.progress.value > progress_before_safety.value
    assert agent.goal_stack.revision > safety_revision
