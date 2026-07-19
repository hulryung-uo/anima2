"""B2 security boundaries for curriculum-driven profession goals.

These tests keep the LLM on the advisory side of the capability boundary:
only an exact, catalog-backed curriculum goal may unlock a profession's work
skill, and wrapping cognition must never launder an arbitrary proposal or a
stale intention token into a fresh decision.
"""

from __future__ import annotations

from typing import Any

import pytest

from anima2.agent import Agent
from anima2.cognition import CognitionDecision
from anima2.contract import GumpView, ItemView, Observation, PlayerView, Position, SkillView
from anima2.curriculum import (
    MILESTONES,
    CurriculumController,
    validate_curriculum_goal,
)
from anima2.goals import GoalStack
from anima2.llm import StubLLMClient
from anima2.mock_body import MockBody
from anima2.persona import Persona
from anima2.profession import PROFESSIONS
from anima2.skills.base import Goal, SkillContext


def _goal(profession: str, milestone: str, **changes: Any) -> Goal:
    params: Any = {
        "schema": 1,
        "profession": profession,
        "milestone": milestone,
    }
    params.update(changes.pop("params", {}))
    return Goal(kind=changes.pop("kind", "curriculum"), params=params, **changes)


def _ctx(goal: Goal | None = None) -> SkillContext:
    player = PlayerView(
        serial=1,
        pos=Position(100, 100, 0),
        hits=100,
        hits_max=100,
    )
    pickaxe = ItemView(
        serial=2,
        graphic=0x0E86,
        amount=1,
        pos=player.pos,
        container=None,
        layer=0,
        distance=0,
    )
    return SkillContext(
        obs=Observation(player=player, items=[pickaxe]),
        persona=Persona(name="Grimm"),
        goal=goal,
    )


class _FixedCognition:
    def __init__(self, goal: Goal | None) -> None:
        self.goal = goal

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        return self.goal


class _VersionedCognition:
    def __init__(
        self,
        goal: Goal | None,
        *,
        based_on_token: object,
        pending_say: str | None = None,
    ) -> None:
        self.goal = goal
        self.based_on_token = based_on_token
        self.pending_say = pending_say
        self.seen_token: object | None = None

    def reconsider_versioned(
        self,
        ctx: SkillContext,
        token: object,
    ) -> CognitionDecision:
        self.seen_token = token
        return CognitionDecision(self.goal, self.based_on_token, self.pending_say)


def _controller(tmp_path, inner: object, *, profession: str = "miner") -> CurriculumController:
    return CurriculumController(
        inner,
        StubLLMClient("(unused)"),
        "Grimm",
        profession,
        drive_goals=True,
        milestones_path=tmp_path / "milestones.jsonl",
    )


def test_every_catalog_entry_has_one_valid_exact_schema_goal() -> None:
    for profession, milestones in MILESTONES.items():
        for milestone in milestones:
            assert milestone.profession == profession
            assert (
                validate_curriculum_goal(
                    _goal(profession, milestone.name),
                    profession,
                )
                is True
            )


@pytest.mark.parametrize(
    "goal",
    [
        Goal(
            kind="work", params={"schema": 1, "profession": "miner", "milestone": "miner_mining_50"}
        ),
        Goal(kind="curriculum", params={"profession": "miner", "milestone": "miner_mining_50"}),
        Goal(
            kind="curriculum",
            params={"schema": True, "profession": "miner", "milestone": "miner_mining_50"},
        ),
        Goal(
            kind="curriculum",
            params={"schema": 2, "profession": "miner", "milestone": "miner_mining_50"},
        ),
        Goal(kind="curriculum", params={"schema": 1, "profession": "miner"}),
        Goal(kind="curriculum", params={"schema": 1, "milestone": "miner_mining_50"}),
        Goal(
            kind="curriculum",
            params={
                "schema": 1,
                "profession": "miner",
                "milestone": "miner_mining_50",
                "skill": "hunt",
            },
        ),
        Goal(kind="curriculum", params=None),  # type: ignore[arg-type]
    ],
    ids=[
        "wrong-kind",
        "missing-schema",
        "bool-schema",
        "future-schema",
        "missing-milestone",
        "missing-profession",
        "extra-capability-key",
        "non-mapping-params",
    ],
)
def test_malformed_curriculum_goal_is_rejected_by_exact_schema(goal: Goal) -> None:
    assert validate_curriculum_goal(goal, "miner") is False


def test_unknown_and_cross_profession_milestones_are_rejected() -> None:
    assert validate_curriculum_goal(_goal("miner", "miner_made_up"), "miner") is False
    assert validate_curriculum_goal(_goal("fisher", "fisher_catch_5"), "miner") is False
    assert validate_curriculum_goal(_goal("miner", "fisher_catch_5"), "miner") is False


def test_controller_validation_is_bound_to_its_profession(tmp_path) -> None:
    controller = _controller(tmp_path, _FixedCognition(None), profession="miner")

    assert controller.validate_goal(_goal("miner", "miner_mining_50")) is True
    assert controller.validate_goal(_goal("fisher", "fisher_catch_5")) is False
    # Nearby goto is the other existing closed autonomous vocabulary.
    ctx = _ctx()
    assert controller.validate_goal(
        Goal(kind="goto", params={"target": Position(101, 100, 0)}), ctx
    ) is False
    assert controller.validate_goal(Goal(kind="goto", params={}), ctx) is False
    assert controller.validate_goal(
        Goal(kind="goto", params={"target": Position(113, 100, 0)}), ctx
    ) is False


def test_default_profession_planner_is_byte_for_byte_opt_out() -> None:
    for profession in PROFESSIONS.values():
        implicit = profession.planner()
        explicit = profession.planner(curriculum_goals=False)

        assert [type(skill) for skill in implicit.skills] == [
            type(skill) for skill in explicit.skills
        ]
        assert [skill.name for skill in implicit.skills] == [
            skill.name for skill in explicit.skills
        ]

    # The legacy miner still works with no goal when the feature is not enabled.
    assert PROFESSIONS["miner"].planner().select(_ctx()).name == "mine_smelt_deliver"


@pytest.mark.parametrize(
    "goal",
    [
        None,
        Goal(kind="goto", params={"target": Position(101, 100, 0)}),
        _goal("miner", "not_a_real_milestone"),
        _goal("fisher", "fisher_catch_5"),
        Goal(
            kind="curriculum",
            params={
                "schema": 1,
                "profession": "miner",
                "milestone": "miner_mining_50",
                "skill": "mine_smelt_deliver",
            },
        ),
    ],
    ids=["none", "arbitrary-goto", "unknown", "cross-profession", "extra-key"],
)
def test_opt_in_profession_work_skill_is_fail_closed(goal: Goal | None) -> None:
    selected = PROFESSIONS["miner"].planner(curriculum_goals=True).select(_ctx(goal))

    assert selected.name != "mine_smelt_deliver"


def test_opt_in_profession_runs_only_for_validated_curriculum_goal() -> None:
    goal = _goal("miner", "miner_mining_50")

    selected = PROFESSIONS["miner"].planner(curriculum_goals=True).select(_ctx(goal))

    assert selected.name == "mine_smelt_deliver"


def test_opt_in_profession_waits_in_place_while_goal_is_not_ready() -> None:
    planner = PROFESSIONS["miner"].planner(curriculum_goals=True)

    selected = planner.select(_ctx())

    assert selected.name == "curriculum_wait"
    assert selected.step(_ctx()).action is None


def test_opt_in_waits_when_valid_goal_work_preconditions_fail() -> None:
    planner = PROFESSIONS["miner"].planner(curriculum_goals=True)
    ctx = _ctx(_goal("miner", "miner_hold_20_ore"))
    ctx.obs.items = []  # no pickaxe: work cannot run

    assert planner.select(ctx).name == "curriculum_wait"


def test_exhausted_curriculum_restores_legacy_work_fallback() -> None:
    planner = PROFESSIONS["miner"].planner(curriculum_goals=True)
    ctx = _ctx()
    ctx.memory["curriculum_exhausted"] = True

    assert planner.select(ctx).name == "mine_smelt_deliver"


@pytest.mark.parametrize(
    ("profession", "milestone", "attribute", "expected"),
    [
        ("miner", "miner_hold_20_ore", "ore_threshold", 20),
        ("blacksmith", "blacksmith_hold_10_daggers", "sell_threshold", 10),
    ],
)
def test_inventory_milestone_applies_trusted_non_consuming_threshold(
    profession: str,
    milestone: str,
    attribute: str,
    expected: int,
) -> None:
    planner = PROFESSIONS[profession].planner(curriculum_goals=True)
    goal = _goal(profession, milestone)

    bound = next(skill for skill in planner.skills if hasattr(skill, "inner"))
    bound.can_run(_ctx(goal))

    assert getattr(bound.inner, attribute) == expected
    bound.can_run(_ctx())
    assert getattr(bound.inner, attribute) == bound._policy_defaults[attribute]


def test_curriculum_completion_waits_for_safe_fsm_yield_point() -> None:
    planner = PROFESSIONS["miner"].planner(curriculum_goals=True)
    complete = next(skill for skill in planner.skills if skill.name == "curriculum_complete")
    ctx = _ctx(_goal("miner", "miner_hold_10_ingots"))
    backpack = ItemView(3, 0x0E75, 1, Position(), 1, 0x15, 0)
    ingots = ItemView(4, 0x1BEF, 10, Position(), 3, 0, 0)
    ctx.obs.items.extend((backpack, ingots))
    ctx.memory["smelt_phase"] = "smelt"

    assert complete.can_run(ctx) is False
    ctx.memory["smelt_phase"] = "mine"
    assert complete.can_run(ctx) is True


def test_curriculum_completion_does_not_abandon_craft_sequence() -> None:
    planner = PROFESSIONS["blacksmith"].planner(curriculum_goals=True)
    complete = next(skill for skill in planner.skills if skill.name == "curriculum_complete")
    ctx = _ctx(_goal("blacksmith", "blacksmith_blacksmithing_50"))
    ctx.obs.skills = [SkillView(id=7, value=50.0, base=50.0, cap=100.0, lock=0)]
    ctx.memory["bs_state"] = "loop"

    assert complete.can_run(ctx) is False
    # The normal MAKE_LAST loop remains `loop`; the safe quiescent boundary is
    # the craft prompt being visibly open again, waiting for our next reply.
    ctx.obs.gumps = [GumpView(serial=10, gump_id=20)]
    assert complete.can_run(ctx) is True


@pytest.mark.parametrize(
    "proposal",
    [
        Goal(kind="goto", params={}),
        Goal(kind="delete_bank", params={}),
        _goal("fisher", "fisher_catch_5"),
        _goal("miner", "made_up"),
    ],
    ids=["malformed-goto", "unknown-kind", "cross-profession", "unknown-milestone"],
)
def test_agent_admission_rejects_arbitrary_cognition_goal_fail_closed(
    tmp_path,
    proposal: Goal,
) -> None:
    controller = _controller(tmp_path, _FixedCognition(proposal))
    body = MockBody()
    body.player.pos = Position(100, 100, 0)
    agent = Agent(
        body=body,
        persona=Persona(name="Grimm"),
        planner=PROFESSIONS["miner"].planner(curriculum_goals=True),
        cognition=_FixedCognition(proposal),
        cognition_interval=1,
        goal_validator=controller.validate_goal,
    )

    agent.tick()

    assert agent.goal is None
    assert agent.goal_stack.frames == ()
    assert agent.memory["cognition_admission_rejections"] == 1


def test_valid_versioned_inner_goal_and_pending_say_preserve_stale_token(tmp_path) -> None:
    call_token = object()
    stale_token = object()
    goal = _goal("miner", "miner_hold_20_ore")
    inner = _VersionedCognition(
        goal,
        based_on_token=stale_token,
        pending_say="Still working.",
    )
    controller = _controller(tmp_path, inner)

    decision = controller.reconsider_versioned(_ctx(), call_token)

    assert inner.seen_token is call_token
    assert decision.goal is goal
    assert decision.based_on_token is stale_token
    assert decision.pending_say == "Still working."


def test_controller_generated_goal_does_not_refresh_inner_stale_token(tmp_path) -> None:
    call_token = object()
    stale_token = object()
    inner = _VersionedCognition(None, based_on_token=stale_token)
    controller = _controller(tmp_path, inner)
    controller.current_milestone = "miner_hold_20_ore"

    decision = controller.reconsider_versioned(_ctx(), call_token)

    # The due background picker may legally choose another eligible miner
    # milestone before this non-blocking call returns. Whichever trusted goal
    # wins that race must retain the inner decision's old CAS token.
    assert decision.goal is not None
    assert validate_curriculum_goal(decision.goal, "miner") is True
    assert decision.based_on_token is stale_token


def test_drive_goals_off_preserves_legacy_arbitrary_inner_goal(tmp_path) -> None:
    legacy = Goal(kind="goto", params={"target": Position(101, 100, 0)})
    controller = CurriculumController(
        _FixedCognition(legacy),
        StubLLMClient("(unused)"),
        "Grimm",
        "miner",
        drive_goals=False,
        milestones_path=tmp_path / "milestones.jsonl",
    )

    assert controller.reconsider(_ctx()) is legacy


def test_agent_rejects_structurally_valid_but_far_goto(tmp_path) -> None:
    far = Goal(kind="goto", params={"target": Position(113, 100, 0)})
    controller = _controller(tmp_path, _FixedCognition(far))
    body = MockBody()
    body.player.pos = Position(100, 100, 0)
    agent = Agent(
        body=body,
        persona=Persona(name="Grimm"),
        planner=PROFESSIONS["miner"].planner(curriculum_goals=True),
        cognition=_FixedCognition(far),
        cognition_interval=1,
        goal_validator=controller.validate_goal,
    )

    agent.tick()

    assert agent.goal is None
    assert agent.memory["cognition_admission_rejections"] == 1


def test_repeated_invalid_inner_cannot_starve_trusted_curriculum(tmp_path) -> None:
    invalid = Goal(kind="goto", params={"target": Position(101, 100, 0)})
    controller = _controller(tmp_path, _FixedCognition(invalid))
    body = MockBody()
    body.player.pos = Position(100, 100, 0)
    agent = Agent(
        body=body,
        persona=Persona(name="Grimm"),
        planner=PROFESSIONS["miner"].planner(curriculum_goals=True),
        cognition=controller,
        cognition_interval=1,
        goal_validator=controller.validate_goal,
    )

    agent.tick()
    assert controller.wait_idle(1.0)
    agent.tick()

    assert agent.goal is not None
    assert validate_curriculum_goal(agent.goal, "miner") is True


class _ObservationBody:
    connected = True

    def __init__(self, observation: Observation) -> None:
        self.observation = observation

    def observe(self) -> Observation:
        return self.observation

    def act(self, action: object) -> None:
        pass


def test_curriculum_catalog_progress_updates_exact_goal_frame(tmp_path) -> None:
    observation = Observation(
        player=PlayerView(
            serial=1,
            pos=Position(100, 100, 0),
            hits=100,
            hits_max=100,
        ),
        skills=[SkillView(id=45, value=25.0, base=25.0, cap=100.0, lock=0)],
    )
    controller = _controller(tmp_path, _FixedCognition(None))
    goal = _goal("miner", "miner_mining_50")
    agent = Agent(
        body=_ObservationBody(observation),  # type: ignore[arg-type]
        persona=Persona(name="Grimm"),
        planner=PROFESSIONS["miner"].planner(curriculum_goals=True),
        cognition=controller,
        cognition_interval=99,
        goal=goal,
        goal_progress=controller.goal_progress,
    )

    agent.tick()

    assert agent.goal_stack.current is not None
    assert agent.goal_stack.current.goal is goal
    assert agent.goal_stack.current.progress.value == 0.5
    assert agent.goal_stack.current.progress.note == "policy"
    revision = agent.goal_stack.revision
    observation.skills = [SkillView(id=45, value=10.0, base=10.0, cap=100.0, lock=0)]
    agent.tick()
    assert agent.goal_stack.current is not None
    assert agent.goal_stack.current.progress.value == 0.5
    assert agent.goal_stack.revision == revision


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_goal_progress_rejects_non_finite_values_without_mutation(value: float) -> None:
    stack = GoalStack()
    stack.push(_goal("miner", "miner_mining_50"), tick=0)
    revision = stack.revision

    with pytest.raises(ValueError):
        stack.set_progress(value, tick=0)

    assert stack.current is not None
    assert stack.current.progress.value == 0.0
    assert stack.revision == revision
