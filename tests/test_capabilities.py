"""Closed-world capability admission and profession-planner wiring."""

from dataclasses import FrozenInstanceError

import pytest

from anima2.agent import Agent, NullCognition
from anima2.capabilities import (
    CAPABILITIES,
    CapabilityPolicy,
    capability_goal,
    issue_capability_planner_lease,
    resolve_capability,
)
from anima2.contract import (
    Drop,
    ItemView,
    MobileView,
    Observation,
    PlayerView,
    PopupRequest,
    Position,
    Use,
)
from anima2.goals import GoalOutcome, GoalSource
from anima2.persona import Persona
from anima2.planner import Planner
from anima2.profession import PROFESSIONS
from anima2.skills import SpeakPending
from anima2.skills.base import Goal, Skill, SkillContext, SkillResult, Status
from anima2.skills.market import BankGold, SellDaggers


def _ctx(
    goal: Goal | None = None,
    *,
    gold: int = 0,
    bank_gold: int = 0,
    daggers: int = 0,
    goal_id: int | None = None,
    goal_policy: CapabilityPolicy | None = None,
) -> SkillContext:
    player = PlayerView(
        serial=1,
        pos=Position(100, 100, 0),
        hits=100,
        hits_max=100,
        mana=100,
        mana_max=100,
        stam=100,
        stam_max=100,
    )
    backpack = ItemView(
        serial=2,
        graphic=0x0E75,
        amount=1,
        pos=player.pos,
        container=player.serial,
        layer=0x15,
        distance=0,
    )
    bank_box = ItemView(
        serial=3,
        graphic=0x0E7C,
        amount=1,
        pos=player.pos,
        container=player.serial,
        layer=0x1D,
        distance=0,
    )
    items = [backpack, bank_box]
    if gold:
        items.append(
            ItemView(
                serial=4,
                graphic=0x0EED,
                amount=gold,
                pos=player.pos,
                container=backpack.serial,
                layer=0,
                distance=0,
            )
        )
    if bank_gold:
        items.append(
            ItemView(
                serial=5,
                graphic=0x0EED,
                amount=bank_gold,
                pos=player.pos,
                container=bank_box.serial,
                layer=0,
                distance=0,
            )
        )
    if daggers:
        items.append(
            ItemView(
                serial=6,
                graphic=0x0F52,
                amount=daggers,
                pos=player.pos,
                container=backpack.serial,
                layer=0,
                distance=0,
            )
        )
    return SkillContext(
        obs=Observation(player=player, items=items),
        persona=Persona(name="Tormund"),
        goal=goal,
        memory={"banker_spot": (100, 100), "vendor_spot": (100, 100)},
        goal_id=goal_id,
        goal_policy=goal_policy,
    )


def _registry_bindings() -> tuple[object, ...]:
    values = getattr(CAPABILITIES, "values", None)
    return tuple(values()) if callable(values) else tuple(CAPABILITIES)


def _contains_bank_gold(skill: Skill) -> bool:
    current: object = skill
    seen: set[int] = set()
    while isinstance(current, Skill) and id(current) not in seen:
        if isinstance(current, BankGold):
            return True
        seen.add(id(current))
        current = getattr(current, "inner", None)
    return False


def _contains_sell_daggers(skill: Skill) -> bool:
    current: object = skill
    seen: set[int] = set()
    while isinstance(current, Skill) and id(current) not in seen:
        if isinstance(current, SellDaggers):
            return True
        seen.add(id(current))
        current = getattr(current, "inner", None)
    return False


def test_capability_registry_is_unique_and_deeply_immutable():
    bindings = _registry_bindings()
    keys = [(binding.profession, binding.capability_id) for binding in bindings]

    assert keys == [
        ("blacksmith", "sell_daggers"),
        ("blacksmith", "bank_gold"),
    ]
    assert len(keys) == len(set(keys))
    assert not isinstance(CAPABILITIES, list)

    with pytest.raises((AttributeError, TypeError)):
        CAPABILITIES.append(bindings[0])  # type: ignore[attr-defined]
    with pytest.raises((AttributeError, FrozenInstanceError, TypeError)):
        bindings[0].profession = "miner"  # type: ignore[attr-defined]


def test_capability_goal_has_one_exact_versioned_wire_shape():
    goal = capability_goal("blacksmith", "bank_gold")

    assert goal == Goal(
        kind="capability",
        params={
            "schema": 1,
            "profession": "blacksmith",
            "capability": "bank_gold",
        },
    )


@pytest.mark.parametrize("source", [GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM])
def test_installed_capability_is_admitted_for_trusted_goal_sources(source: GoalSource):
    goal = capability_goal("blacksmith", "bank_gold")

    resolved = resolve_capability(goal, "blacksmith", source, _ctx(goal, gold=250))

    assert resolved is not None
    assert resolved.goal == capability_goal("blacksmith", "bank_gold")
    assert resolved.binding.profession == "blacksmith"
    assert resolved.binding.capability_id == "bank_gold"


def test_skill_generated_capability_goal_is_not_an_admission_authority():
    goal = capability_goal("blacksmith", "bank_gold")

    assert resolve_capability(goal, "blacksmith", GoalSource.SKILL, _ctx(goal, gold=250)) is None


@pytest.mark.parametrize(
    "goal",
    [
        Goal(
            kind="goto", params={"schema": 1, "profession": "blacksmith", "capability": "bank_gold"}
        ),
        Goal(kind="capability", params={}),
        Goal(
            kind="capability",
            params={"schema": True, "profession": "blacksmith", "capability": "bank_gold"},
        ),
        Goal(
            kind="capability",
            params={"schema": 2, "profession": "blacksmith", "capability": "bank_gold"},
        ),
        Goal(
            kind="capability",
            params={"schema": 1.0, "profession": "blacksmith", "capability": "bank_gold"},
        ),
        Goal(
            kind="capability",
            params={
                "schema": 1 + 0j,
                "profession": "blacksmith",
                "capability": "bank_gold",
            },
        ),
        Goal(kind="capability", params={"schema": 1, "profession": "blacksmith"}),
        Goal(kind="capability", params={"schema": 1, "capability": "bank_gold"}),
        Goal(
            kind="capability",
            params={"schema": 1, "profession": "blacksmith", "capability": "unknown"},
        ),
        Goal(
            kind="capability",
            params={"schema": 1, "profession": "blacksmith", "capability": "bank_gold", "args": {}},
        ),
        Goal(
            kind="capability",
            params={"schema": 1, "profession": "blacksmith", "capability": "bank_gold; wander"},
        ),
        Goal(kind="capability", params=None),  # type: ignore[arg-type]
    ],
)
def test_malformed_or_unknown_capability_goal_fails_closed(goal: Goal):
    assert (
        resolve_capability(goal, "blacksmith", GoalSource.COGNITION, _ctx(goal, gold=250)) is None
    )


def test_capability_cannot_cross_profession_policy_boundary():
    smith_goal = capability_goal("blacksmith", "bank_gold")
    forged_miner_goal = Goal(
        kind="capability",
        params={"schema": 1, "profession": "miner", "capability": "bank_gold"},
    )

    assert resolve_capability(smith_goal, "miner", GoalSource.COGNITION, _ctx(smith_goal)) is None
    assert (
        resolve_capability(
            forged_miner_goal, "miner", GoalSource.COGNITION, _ctx(forged_miner_goal)
        )
        is None
    )


def test_resolved_capability_owns_a_canonical_copy_of_the_untrusted_goal():
    proposal = capability_goal("blacksmith", "bank_gold")
    resolved = resolve_capability(
        proposal, "blacksmith", GoalSource.COGNITION, _ctx(proposal, gold=250)
    )
    assert resolved is not None
    assert resolved.goal is not proposal
    assert resolved.goal.params is not proposal.params

    proposal.kind = "goto"
    proposal.params.clear()
    proposal.params["capability"] = "wander"

    assert resolved.goal == Goal(
        kind="capability",
        params={"schema": 1, "profession": "blacksmith", "capability": "bank_gold"},
    )
    with pytest.raises(TypeError):
        resolved.goal.params["capability"] = "wander"  # type: ignore[index]
    with pytest.raises(AttributeError):
        resolved.goal.kind = "goto"


def test_capability_planner_opt_out_preserves_the_legacy_skill_order():
    for profession in PROFESSIONS.values():
        default = profession.planner()
        opted_out = profession.planner(capability_goals=False)

        assert [type(skill) for skill in opted_out.skills] == [
            type(skill) for skill in default.skills
        ]
        assert not any(_contains_bank_gold(skill) for skill in opted_out.skills)
        assert not any(_contains_sell_daggers(skill) for skill in opted_out.skills)


def test_registry_and_profession_planners_expose_the_same_closed_capability_set():
    planner_keys: list[tuple[str, str]] = []
    smith_goal = capability_goal("blacksmith", "bank_gold")
    resolved = resolve_capability(
        smith_goal,
        "blacksmith",
        GoalSource.COGNITION,
        _ctx(smith_goal, gold=250),
    )
    assert resolved is not None

    for profession in PROFESSIONS.values():
        if profession.key != "blacksmith":
            with pytest.raises(ValueError, match="no installed capabilities"):
                profession.planner(capability_goals=True)
            continue

        planner = profession.planner(capability_goals=True)
        sell_skills = [skill for skill in planner.skills if _contains_sell_daggers(skill)]
        bank_skills = [skill for skill in planner.skills if _contains_bank_gold(skill)]
        planner_keys.extend((profession.key, "sell_daggers") for _ in sell_skills)
        planner_keys.extend((profession.key, "bank_gold") for _ in bank_skills)
        assert len(sell_skills) == 1
        assert len(bank_skills) == 1
        assert type(sell_skills[0].inner) is SellDaggers
        assert type(bank_skills[0].inner) is resolved.binding.skill_type
        names = {skill.name for skill in planner.skills}
        assert "capability_complete" in names
        assert "capability_wait" in names

    registry_keys = [
        (binding.profession, binding.capability_id) for binding in _registry_bindings()
    ]
    assert planner_keys == registry_keys


def test_valid_blacksmith_bank_goal_selects_the_bound_bank_skill():
    goal = capability_goal("blacksmith", "bank_gold")
    resolved = resolve_capability(
        goal,
        "blacksmith",
        GoalSource.COGNITION,
        _ctx(goal, gold=250),
    )
    assert resolved is not None
    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)

    selected = planner.select(
        _ctx(
            resolved.goal,
            gold=250,
            goal_policy=CapabilityPolicy("blacksmith"),
        )
    )

    assert _contains_bank_gold(selected)


def test_valid_blacksmith_sell_goal_selects_only_the_bound_sell_skill():
    goal = capability_goal("blacksmith", "sell_daggers")
    resolved = resolve_capability(
        goal,
        "blacksmith",
        GoalSource.COGNITION,
        _ctx(goal, daggers=5),
    )
    assert resolved is not None
    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)

    selected = planner.select(
        _ctx(
            resolved.goal,
            daggers=5,
            goal_id=17,
            goal_policy=CapabilityPolicy("blacksmith"),
        )
    )

    assert _contains_sell_daggers(selected)
    assert not _contains_bank_gold(selected)


def test_sell_completion_evidence_is_exactly_goal_scoped() -> None:
    binding = CAPABILITIES[("blacksmith", "sell_daggers")]
    ctx = _ctx(daggers=0, gold=50, goal_id=17)
    ctx.memory.update(
        {
            "cap_sell_goal_id": 17,
            "cap_sell_sent_goal_id": 17,
            "cap_sell_finished_goal_id": 17,
            "cap_sell_returned_goal_id": 17,
            "cap_sell_sent_daggers": 5,
            "cap_sell_expected_gold": 50,
            "cap_sell_offered_items": ((6, 5, 10),),
            "cap_sell_offered_removed": 5,
            "cap_sell_offered_cleared": True,
            "cap_sell_dagger_delta": 5,
            "cap_sell_gold_delta": 50,
            "mkt_phase": "craft",
        }
    )

    assert binding.achieved(ctx)
    assert binding.progress(ctx) == 1.0

    ctx.goal_id = 18
    assert not binding.achieved(ctx)
    assert binding.progress(ctx) == 0.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cap_sell_sent_goal_id", None),
        ("cap_sell_finished_goal_id", None),
        ("cap_sell_returned_goal_id", None),
        ("cap_sell_offered_removed", 4),
        ("cap_sell_offered_cleared", False),
        ("cap_sell_dagger_delta", 4),
        ("cap_sell_gold_delta", 49),
        ("mkt_phase", "sell_return"),
    ],
)
def test_sell_completion_requires_send_both_deltas_and_safe_return(
    field: str, value: object
) -> None:
    binding = CAPABILITIES[("blacksmith", "sell_daggers")]
    ctx = _ctx(daggers=0, gold=50, goal_id=17)
    ctx.memory.update(
        {
            "cap_sell_goal_id": 17,
            "cap_sell_sent_goal_id": 17,
            "cap_sell_finished_goal_id": 17,
            "cap_sell_returned_goal_id": 17,
            "cap_sell_sent_daggers": 5,
            "cap_sell_expected_gold": 50,
            "cap_sell_offered_items": ((6, 5, 10),),
            "cap_sell_offered_removed": 5,
            "cap_sell_offered_cleared": True,
            "cap_sell_dagger_delta": 5,
            "cap_sell_gold_delta": 50,
            "mkt_phase": "craft",
            field: value,
        }
    )

    assert not binding.achieved(ctx)


def test_sell_deadline_and_preemption_wait_for_vendor_transaction_yield() -> None:
    proposal = capability_goal("blacksmith", "sell_daggers")
    policy = CapabilityPolicy("blacksmith")
    resolved = resolve_capability(
        proposal,
        "blacksmith",
        GoalSource.COGNITION,
        _ctx(proposal, daggers=5),
    )
    assert resolved is not None
    ctx = _ctx(
        resolved.goal,
        daggers=5,
        goal_id=17,
        goal_policy=policy,
    )
    ctx.memory.update({"mkt_phase": "sell", "sell_stage": "confirm"})

    assert not policy.deadline_can_expire(resolved.goal, ctx)
    assert not policy.can_preempt(resolved.goal, ctx)

    ctx.memory["mkt_phase"] = "craft"
    ctx.memory.pop("sell_stage")

    assert policy.deadline_can_expire(resolved.goal, ctx)
    assert policy.can_preempt(resolved.goal, ctx)

    # A completed/given-up operation owns no cursor resource. A stale vendor
    # UI must not retain the lease forever, though it still cannot be SUCCESS.
    ctx.memory["cap_sell_finished_goal_id"] = 17
    ctx.obs.popup = object()  # type: ignore[assignment]

    assert policy.deadline_can_expire(resolved.goal, ctx)
    assert policy.can_preempt(resolved.goal, ctx)
    assert not CAPABILITIES[("blacksmith", "sell_daggers")].achieved(ctx)


@pytest.mark.parametrize(
    "goal",
    [
        capability_goal("blacksmith", "bank_gold"),
        Goal(
            kind="capability",
            params={"schema": 1, "profession": "miner", "capability": "bank_gold"},
        ),
        Goal(
            kind="capability",
            params={"schema": 1, "profession": "blacksmith", "capability": "wander"},
        ),
    ],
)
def test_invalid_capability_goal_waits_without_emitting_an_action(goal: Goal):
    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)

    selected = planner.select(_ctx(goal, gold=250))
    result = selected.step(_ctx(goal, gold=250))

    assert selected.name == "capability_wait"
    assert result.action is None


class _FixedCognition:
    def __init__(self, goal: Goal) -> None:
        self.goal = goal

    def reconsider(self, ctx: SkillContext) -> Goal:
        return self.goal


class _StaticBody:
    connected = True

    def __init__(self, observation: Observation) -> None:
        self.observation = observation
        self.actions: list[object] = []

    def observe(self) -> Observation:
        return self.observation

    def act(self, action: object) -> None:
        self.actions.append(action)


def _capability_agent(proposal: Goal) -> Agent:
    ctx = _ctx(gold=100)
    policy = CapabilityPolicy("blacksmith")
    agent = Agent(
        body=_StaticBody(ctx.obs),  # type: ignore[arg-type]
        persona=ctx.persona,
        planner=PROFESSIONS["blacksmith"].planner(capability_goals=True),
        cognition=_FixedCognition(proposal),
        cognition_interval=1,
        profession="blacksmith",
        goal_policy=policy,
    )
    agent.memory["banker_spot"] = (100, 100)
    return agent


def _sell_capability_agent(proposal: Goal) -> Agent:
    ctx = _ctx(daggers=5)
    policy = CapabilityPolicy("blacksmith")
    agent = Agent(
        body=_StaticBody(ctx.obs),  # type: ignore[arg-type]
        persona=ctx.persona,
        planner=PROFESSIONS["blacksmith"].planner(capability_goals=True),
        cognition=_FixedCognition(proposal),
        cognition_interval=1,
        profession="blacksmith",
        goal_policy=policy,
    )
    agent.memory["vendor_spot"] = (100, 100)
    return agent


def test_agent_admits_a_sealed_copy_with_registry_deadline() -> None:
    proposal = capability_goal("blacksmith", "bank_gold")
    agent = _capability_agent(proposal)

    agent.tick()

    assert agent.goal is not None and agent.goal is not proposal and agent.goal.sealed
    assert agent.goal_stack.current is not None
    assert agent.goal_stack.current.deadline_tick == 120
    proposal.kind = "goto"
    proposal.params.clear()
    assert agent.goal.kind == "capability"
    assert agent.goal.params["capability"] == "bank_gold"


def test_capability_safety_preselection_commits_live_normalization() -> None:
    agent = _capability_agent(capability_goal("blacksmith", "bank_gold"))
    agent.memory["survival_flee_steps"] = 1

    agent.tick()

    assert agent.memory["survival_flee_steps"] == 0
    assert agent.goal is not None


def test_agent_rejects_policy_planner_profession_mismatch_before_tick() -> None:
    ctx = _ctx(gold=100)
    policy = CapabilityPolicy("blacksmith")

    with pytest.raises(ValueError, match="matching capability planner"):
        Agent(
            body=_StaticBody(ctx.obs),  # type: ignore[arg-type]
            persona=ctx.persona,
            planner=PROFESSIONS["blacksmith"].planner(),
            profession="blacksmith",
            goal_policy=policy,
        )


def test_capability_planner_rejects_raw_or_wrapped_policy_callbacks() -> None:
    ctx = _ctx(gold=100)
    policy = CapabilityPolicy("blacksmith")
    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)

    with pytest.raises(ValueError, match="requires its CapabilityPolicy object"):
        Agent(
            body=_StaticBody(ctx.obs),  # type: ignore[arg-type]
            persona=ctx.persona,
            planner=planner,
            profession="blacksmith",
            goal_admitter=lambda goal, skill_ctx, source: policy.admit_goal(
                goal, skill_ctx, source
            ),
        )


def test_capability_planner_rejects_a_duck_typed_policy_object() -> None:
    ctx = _ctx(gold=100)

    class _FakePolicy:
        profession = "blacksmith"
        capability_ids = frozenset({"bank_gold"})

        def admit_goal(self, goal, skill_ctx, source):  # noqa: ANN001, ANN201
            return None

        def goal_progress(self, goal, skill_ctx):  # noqa: ANN001, ANN201
            return None

        def deadline_can_expire(self, goal, skill_ctx):  # noqa: ANN001, ANN201
            return True

    with pytest.raises(TypeError, match="exact CapabilityPolicy"):
        Agent(
            body=_StaticBody(ctx.obs),  # type: ignore[arg-type]
            persona=ctx.persona,
            planner=PROFESSIONS["blacksmith"].planner(capability_goals=True),
            profession="blacksmith",
            goal_policy=_FakePolicy(),
        )


def test_capability_policy_identity_fields_are_immutable() -> None:
    policy = CapabilityPolicy("blacksmith")

    with pytest.raises(FrozenInstanceError):
        policy.profession = "miner"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        policy.capability_ids = frozenset()  # type: ignore[misc]


def test_unserviceable_admitted_capability_expires_in_bounded_time() -> None:
    agent = _capability_agent(capability_goal("blacksmith", "bank_gold"))

    agent.tick()
    agent.cognition = NullCognition()
    for _ in range(240):
        agent.tick()
        if agent.goal is None:
            break

    matches = [
        frame
        for frame in agent.goal_stack.history
        if frame.goal.kind == "capability" and frame.outcome is GoalOutcome.EXPIRED
    ]
    assert len(matches) == 1
    assert agent.goal is None


def test_due_capability_drains_held_gold_before_expiring() -> None:
    agent = _capability_agent(capability_goal("blacksmith", "bank_gold"))
    agent.tick()
    agent.cognition = NullCognition()
    assert agent.goal_stack.current is not None
    agent.ticks = agent.goal_stack.current.deadline_tick or 0
    agent.memory.update(
        {"mkt_phase": "bank", "bank_stage": "deposit", "bank_held": 4}
    )
    agent.body.observation = _ctx().obs  # type: ignore[attr-defined]

    action = agent.tick()

    assert action == Drop(serial=4, container=3)
    assert agent.goal is not None
    assert not any(frame.outcome is GoalOutcome.EXPIRED for frame in agent.goal_stack.history)


def test_due_capability_expires_immediately_at_a_safe_unachieved_yield_point() -> None:
    agent = _capability_agent(capability_goal("blacksmith", "bank_gold"))
    agent.tick()
    agent.cognition = NullCognition()
    assert agent.goal_stack.current is not None
    agent.ticks = agent.goal_stack.current.deadline_tick or 0
    agent.memory.update({"mkt_phase": "craft", "bank_held": None})

    agent.tick()

    assert agent.goal is None
    assert agent.goal_stack.history[-1].outcome is GoalOutcome.EXPIRED


def test_due_sell_capability_defers_and_rejects_cancel_until_safe_yield() -> None:
    agent = _sell_capability_agent(
        capability_goal("blacksmith", "sell_daggers")
    )
    agent.tick()
    assert agent.goal_stack.current is not None
    assert agent.goal_stack.current.deadline_tick == 180
    agent.cognition = NullCognition()
    agent.ticks = agent.goal_stack.current.deadline_tick or 0
    agent.memory.update({"mkt_phase": "sell", "sell_stage": "confirm"})

    agent.tick()

    assert agent.goal is not None
    assert not any(
        frame.outcome is GoalOutcome.EXPIRED for frame in agent.goal_stack.history
    )
    with pytest.raises(RuntimeError, match="safe-yield"):
        agent.cancel_goal()

    agent.memory["mkt_phase"] = "craft"
    agent.memory.pop("sell_stage", None)
    agent.memory.pop("sell_find_wait", None)
    agent.memory.pop("sell_confirm_wait", None)
    agent.tick()

    assert agent.goal is None
    assert agent.goal_stack.history[-1].outcome is GoalOutcome.EXPIRED


def test_observed_success_wins_when_it_arrives_at_the_deadline() -> None:
    agent = _capability_agent(capability_goal("blacksmith", "bank_gold"))
    agent.tick()
    agent.cognition = NullCognition()
    assert agent.goal_stack.current is not None
    agent.ticks = agent.goal_stack.current.deadline_tick or 0
    completed_ctx = _ctx(bank_gold=100)
    agent.body.observation = completed_ctx.obs  # type: ignore[attr-defined]
    agent.memory.update({"mkt_phase": "craft", "bank_held": None})

    agent.tick()

    assert agent.goal is None
    assert agent.goal_stack.history[-1].outcome is GoalOutcome.SUCCESS


def test_expired_cognition_proposal_cannot_refresh_its_deadline() -> None:
    proposal = capability_goal("blacksmith", "bank_gold")
    agent = _capability_agent(proposal)
    agent.tick()
    assert agent.goal_stack.current is not None
    agent.ticks = agent.goal_stack.current.deadline_tick or 0
    agent.memory.update({"mkt_phase": "craft", "bank_held": None})

    for _ in range(5):
        agent.tick()

    expired = [
        frame for frame in agent.goal_stack.history if frame.outcome is GoalOutcome.EXPIRED
    ]
    assert len(expired) == 1
    assert agent.goal is None
    assert agent.memory["cognition_expired_replay_rejections"] >= 1


def test_fresh_equal_capability_decision_can_retry_after_expiry() -> None:
    expired_proposal = capability_goal("blacksmith", "bank_gold")
    agent = _capability_agent(expired_proposal)
    agent.tick()
    assert agent.goal_stack.current is not None
    agent.ticks = agent.goal_stack.current.deadline_tick or 0
    agent.memory.update({"mkt_phase": "craft", "bank_held": None})
    agent.tick()
    assert agent.goal is None

    fresh_proposal = capability_goal("blacksmith", "bank_gold")
    assert fresh_proposal == expired_proposal
    assert fresh_proposal is not expired_proposal
    agent.cognition = _FixedCognition(fresh_proposal)

    agent.tick()

    assert agent.goal is not None
    assert agent.goal == fresh_proposal
    assert agent.goal_stack.current is not None
    assert agent.goal_stack.current.outcome is None


def test_goal_seal_is_deep_and_its_metadata_cannot_be_reopened() -> None:
    nested = {"items": [{"name": "gold"}], "tags": {"bank"}}
    goal = Goal("test", nested).seal(object())

    with pytest.raises(TypeError):
        goal.params["items"][0]["name"] = "ore"  # type: ignore[index]
    with pytest.raises(AttributeError, match="metadata"):
        goal._sealed = False  # noqa: SLF001
    with pytest.raises(AttributeError, match="metadata"):
        goal._seal_authority = object()  # noqa: SLF001
    with pytest.raises(AttributeError, match="cannot be deleted"):
        del goal._sealed  # noqa: SLF001


def test_goal_seal_rejects_aliased_custom_mutable_values() -> None:
    class _MutableBox:
        value = "gold"

    with pytest.raises(TypeError, match="unsupported mutable Goal parameter"):
        Goal("test", {"nested": _MutableBox()}).seal(object())


def test_direct_capability_goal_apis_cannot_bypass_policy_admission() -> None:
    agent = _capability_agent(capability_goal("blacksmith", "bank_gold"))
    proposal = capability_goal("blacksmith", "bank_gold")
    resolved = resolve_capability(
        proposal, "blacksmith", GoalSource.USER, _ctx(proposal, gold=100)
    )
    assert resolved is not None

    with pytest.raises(ValueError, match="Policy admission"):
        agent.replace_goal(resolved.goal)
    with pytest.raises(ValueError, match="Policy admission"):
        agent.interrupt_goal(resolved.goal)


@pytest.mark.parametrize("operation", ["cancel", "replace", "interrupt"])
def test_direct_goal_apis_cannot_preempt_an_unsafe_capability(operation: str) -> None:
    agent = _capability_agent(capability_goal("blacksmith", "bank_gold"))
    agent.tick()
    agent.memory.update(
        {"mkt_phase": "bank", "bank_stage": "deposit", "bank_held": 4}
    )

    with pytest.raises(RuntimeError, match="safe-yield"):
        if operation == "cancel":
            agent.cancel_goal()
        elif operation == "replace":
            agent.replace_goal(Goal("goto"))
        else:
            agent.interrupt_goal(Goal("recover"))

    assert agent.goal is not None
    assert agent.memory["bank_held"] == 4


def test_missing_banker_after_admission_drains_and_expires() -> None:
    agent = _capability_agent(capability_goal("blacksmith", "bank_gold"))
    agent.tick()
    agent.cognition = NullCognition()
    assert agent.goal_stack.current is not None
    agent.ticks = agent.goal_stack.current.deadline_tick or 0
    agent.memory.pop("banker_spot")

    for _ in range(80):
        agent.tick()
        if agent.goal is None:
            break

    assert agent.goal is None
    assert agent.goal_stack.history[-1].outcome is GoalOutcome.EXPIRED


def test_missing_bankbox_after_pickup_releases_gold_to_backpack() -> None:
    agent = _capability_agent(capability_goal("blacksmith", "bank_gold"))
    agent.tick()
    assert agent.goal_stack.current is not None
    agent.ticks = agent.goal_stack.current.deadline_tick or 0
    agent.memory.update(
        {"mkt_phase": "bank", "bank_stage": "deposit", "bank_held": 4}
    )
    ctx = _ctx()
    without_bankbox = [item for item in ctx.obs.items if item.layer != 0x1D]
    agent.body.observation = Observation(  # type: ignore[attr-defined]
        player=ctx.obs.player,
        items=without_bankbox,
    )

    action = agent.tick()

    assert action == Drop(serial=4, container=2)
    assert agent.goal is not None
    assert agent.memory["cap_bank_release_pending"] == (4, 2, 0, 0)


def test_ambiguous_drop_keeps_transaction_lease_and_retries() -> None:
    agent = _capability_agent(capability_goal("blacksmith", "bank_gold"))
    agent.tick()
    agent.cognition = NullCognition()
    assert agent.goal_stack.current is not None
    agent.ticks = agent.goal_stack.current.deadline_tick or 0
    agent.memory.update(
        {"mkt_phase": "bank", "bank_stage": "deposit", "bank_held": 4}
    )
    agent.body.observation = _ctx().obs  # type: ignore[attr-defined]

    first = agent.tick()
    agent.body.observation = _ctx().obs  # type: ignore[attr-defined]
    second = agent.tick()

    assert first == Drop(serial=4, container=3)
    assert second == Drop(serial=4, container=3)
    assert agent.memory["cap_bank_release_pending"] == (4, 3, 0, 0)
    assert agent.goal is not None
    assert not any(frame.outcome is GoalOutcome.EXPIRED for frame in agent.goal_stack.history)


def test_expired_capability_tombstone_survives_goal_history_eviction() -> None:
    proposal = capability_goal("blacksmith", "bank_gold")
    agent = _capability_agent(proposal)
    agent.tick()
    assert agent.goal_stack.current is not None
    agent.ticks = agent.goal_stack.current.deadline_tick or 0
    agent.memory.update({"mkt_phase": "craft", "bank_held": None})
    agent.tick()
    assert agent.goal is None

    agent.cognition = NullCognition()
    for index in range(agent.goal_stack.history_limit + 1):
        agent.replace_goal(Goal("ordinary", {"index": index}))
        agent.cancel_goal()
    assert not any(frame.outcome is GoalOutcome.EXPIRED for frame in agent.goal_stack.history)

    agent.cognition = _FixedCognition(proposal)
    agent.tick()

    assert agent.goal is None
    assert agent.memory["cognition_expired_replay_rejections"] >= 1


def test_markerless_planner_proxy_cannot_execute_capability_without_policy() -> None:
    ctx = _ctx(gold=100)
    inner = PROFESSIONS["blacksmith"].planner(capability_goals=True)

    class _MarkerlessProxy:
        def __init__(self) -> None:
            self.skills = inner.skills

        def preselect_interrupt(self, skill_ctx):  # noqa: ANN001, ANN201
            return inner.preselect_interrupt(skill_ctx)

        def select_cached(self, skill_ctx, applicability):  # noqa: ANN001, ANN201
            return inner.select_cached(skill_ctx, applicability)

        def select(self, skill_ctx):  # noqa: ANN001, ANN201
            return inner.select(skill_ctx)

    proposal = capability_goal("blacksmith", "bank_gold")
    resolved = resolve_capability(
        proposal, "blacksmith", GoalSource.COGNITION, _ctx(proposal, gold=100)
    )
    assert resolved is not None
    body = _StaticBody(ctx.obs)
    class _PolicyInjectingCognition:
        def reconsider(self, skill_ctx):  # noqa: ANN001, ANN201
            skill_ctx.goal_policy = CapabilityPolicy("blacksmith")
            return resolved.goal

    agent = Agent(
        body=body,  # type: ignore[arg-type]
        persona=ctx.persona,
        planner=_MarkerlessProxy(),  # type: ignore[arg-type]
        cognition=_PolicyInjectingCognition(),
        cognition_interval=1,
        profession="blacksmith",
    )
    agent.memory["banker_spot"] = (100, 100)

    action = agent.tick()

    assert action is None
    assert body.actions == []
    assert agent.goal_stack.current is None
    assert agent.memory["cognition_admission_rejections"] == 1


def test_cognition_cannot_inject_execution_goal_through_mutable_context() -> None:
    ctx = _ctx(gold=100)
    proposal = capability_goal("blacksmith", "bank_gold")
    resolved = resolve_capability(
        proposal, "blacksmith", GoalSource.COGNITION, _ctx(proposal, gold=100)
    )
    assert resolved is not None

    class _ContextInjectingCognition:
        def reconsider(self, skill_ctx):  # noqa: ANN001, ANN201
            skill_ctx.goal = resolved.goal
            skill_ctx.goal_id = 999
            skill_ctx.goal_policy = CapabilityPolicy("blacksmith")
            return None

    body = _StaticBody(ctx.obs)
    agent = Agent(
        body=body,  # type: ignore[arg-type]
        persona=ctx.persona,
        planner=PROFESSIONS["blacksmith"].planner(capability_goals=True),
        cognition=_ContextInjectingCognition(),
        cognition_interval=1,
        profession="blacksmith",
        goal_policy=CapabilityPolicy("blacksmith"),
    )
    agent.memory["banker_spot"] = (100, 100)

    action = agent.tick()

    assert action is None
    assert body.actions == []
    assert agent.goal is None
    assert len(agent.goal_stack.history) == 0


def test_capability_cognition_cannot_mutate_admission_memory_in_place() -> None:
    ctx = _ctx(gold=100)

    class _MemoryInjectingCognition:
        def reconsider(self, skill_ctx):  # noqa: ANN001, ANN201
            skill_ctx.memory["banker_spot"] = (999, 999)
            return capability_goal("blacksmith", "bank_gold")

    body = _StaticBody(ctx.obs)
    agent = Agent(
        body=body,  # type: ignore[arg-type]
        persona=ctx.persona,
        planner=PROFESSIONS["blacksmith"].planner(capability_goals=True),
        cognition=_MemoryInjectingCognition(),
        cognition_interval=1,
        profession="blacksmith",
        goal_policy=CapabilityPolicy("blacksmith"),
    )

    action = agent.tick()

    assert action is None
    assert body.actions == []
    assert "banker_spot" not in agent.memory
    assert agent.goal is None
    assert agent.memory["cognition_admission_rejections"] == 1


def test_capability_cognition_never_receives_live_canonical_goal_reference() -> None:
    proposal = capability_goal("blacksmith", "bank_gold")

    class _CanonicalMutatingCognition:
        def __init__(self) -> None:
            self.calls = 0

        def reconsider(self, skill_ctx):  # noqa: ANN001, ANN201
            self.calls += 1
            if self.calls == 1:
                return proposal
            assert skill_ctx.goal is not None
            object.__setattr__(skill_ctx.goal, "kind", "goto")
            object.__setattr__(
                skill_ctx.goal,
                "params",
                {"target": Position(112, 100, 0)},
            )
            return None

    ctx = _ctx(gold=100)
    body = _StaticBody(ctx.obs)
    cognition = _CanonicalMutatingCognition()
    agent = Agent(
        body=body,  # type: ignore[arg-type]
        persona=ctx.persona,
        planner=PROFESSIONS["blacksmith"].planner(capability_goals=True),
        cognition=cognition,
        cognition_interval=1,
        profession="blacksmith",
        goal_policy=CapabilityPolicy("blacksmith"),
    )
    agent.memory["banker_spot"] = (100, 100)

    agent.tick()
    agent.tick()

    assert agent.goal is not None
    assert agent.goal.kind == "capability"
    assert agent.goal.params["capability"] == "bank_gold"


def test_spoofed_capability_planner_metadata_cannot_run_an_unbound_skill() -> None:
    ctx = _ctx(gold=100)

    class _DangerSkill(Skill):
        def step(self, skill_ctx):  # noqa: ANN001, ANN201
            return type("Result", (), {"action": Use(serial=999), "status": None})()

    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)
    danger = _DangerSkill()
    body = _StaticBody(ctx.obs)
    agent = Agent(
        body=body,  # type: ignore[arg-type]
        persona=ctx.persona,
        planner=planner,
        cognition=_FixedCognition(capability_goal("blacksmith", "bank_gold")),
        cognition_interval=1,
        profession="blacksmith",
        goal_policy=CapabilityPolicy("blacksmith"),
    )
    planner.select_cached = lambda skill_ctx, applicability: danger  # type: ignore[method-assign]
    agent.memory["banker_spot"] = (100, 100)

    action = agent.tick()

    assert action is None
    assert body.actions == []
    assert agent.goal is not None
    assert agent.memory["capability_skill_rejections"] == 1


def test_planner_selection_cannot_inject_live_transaction_memory() -> None:
    ctx = _ctx(gold=100)
    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)
    bound = next(skill for skill in planner.skills if _contains_bank_gold(skill))

    def _inject_and_select(skill_ctx, applicability):  # noqa: ANN001, ANN201
        skill_ctx.memory.update(
            {"mkt_phase": "bank", "bank_stage": "deposit", "bank_held": 999}
        )
        assert skill_ctx.goal is not None
        object.__setattr__(skill_ctx.goal, "kind", "goto")
        return bound

    body = _StaticBody(ctx.obs)
    agent = Agent(
        body=body,  # type: ignore[arg-type]
        persona=ctx.persona,
        planner=planner,
        cognition=_FixedCognition(capability_goal("blacksmith", "bank_gold")),
        cognition_interval=1,
        profession="blacksmith",
        goal_policy=CapabilityPolicy("blacksmith"),
    )
    planner.select_cached = _inject_and_select  # type: ignore[method-assign]
    agent.memory["banker_spot"] = (100, 100)

    action = agent.tick()

    assert action is None
    assert "bank_held" not in agent.memory
    assert agent.goal is not None and agent.goal.kind == "capability"
    assert agent.memory["capability_skill_rejections"] == 1
    assert not any(
        isinstance(recorded, Drop) and recorded.serial == 999
        for recorded in body.actions
    )


def test_capability_mode_rejects_external_skill_even_without_active_goal() -> None:
    ctx = _ctx(gold=100)

    class _DangerSkill(Skill):
        def step(self, skill_ctx):  # noqa: ANN001, ANN201
            return Use(serial=999)

    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)
    danger = _DangerSkill()
    invalid = Goal(
        "capability",
        {"schema": 1, "profession": "miner", "capability": "bank_gold"},
    )
    body = _StaticBody(ctx.obs)
    agent = Agent(
        body=body,  # type: ignore[arg-type]
        persona=ctx.persona,
        planner=planner,
        cognition=_FixedCognition(invalid),
        cognition_interval=1,
        profession="blacksmith",
        goal_policy=CapabilityPolicy("blacksmith"),
    )
    planner.select_cached = lambda skill_ctx, applicability: danger  # type: ignore[method-assign]
    agent.memory["banker_spot"] = (100, 100)

    action = agent.tick()

    assert action is None
    assert agent.goal is None
    assert body.actions == []
    assert agent.memory["capability_skill_rejections"] == 1


def test_capability_planner_requires_factory_issued_provenance_lease() -> None:
    ctx = _ctx(gold=100)
    planner = Planner([BankGold()])
    planner.capability_profession = "blacksmith"
    planner.capability_ids = frozenset({"bank_gold"})

    with pytest.raises(ValueError, match="do not match"):
        Agent(
            body=_StaticBody(ctx.obs),  # type: ignore[arg-type]
            persona=ctx.persona,
            planner=planner,
            profession="blacksmith",
            goal_policy=CapabilityPolicy("blacksmith"),
        )


def test_copied_lease_cannot_authorize_a_different_skill_manifest() -> None:
    ctx = _ctx(gold=100)
    installed = PROFESSIONS["blacksmith"].planner(capability_goals=True)

    class _DangerSkill(Skill):
        def step(self, skill_ctx):  # noqa: ANN001, ANN201
            return SkillResult(Status.RUNNING, Use(serial=999))

    planner = Planner([_DangerSkill()])
    planner.capability_profession = installed.capability_profession
    planner.capability_ids = installed.capability_ids
    planner.capability_lease = installed.capability_lease

    with pytest.raises(ValueError, match="do not match"):
        Agent(
            body=_StaticBody(ctx.obs),  # type: ignore[arg-type]
            persona=ctx.persona,
            planner=planner,
            profession="blacksmith",
            goal_policy=CapabilityPolicy("blacksmith"),
        )


def test_lease_issuer_rejects_a_nonfactory_skill_manifest() -> None:
    class _DangerSkill(Skill):
        def step(self, skill_ctx):  # noqa: ANN001, ANN201
            return SkillResult(Status.RUNNING, Use(serial=999))

    with pytest.raises(ValueError, match="factory manifest"):
        issue_capability_planner_lease("blacksmith", (_DangerSkill(),))


def test_post_init_skill_class_change_is_rejected_before_selection() -> None:
    ctx = _ctx(gold=100)
    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)
    pending = next(skill for skill in planner.skills if type(skill) is SpeakPending)
    body = _StaticBody(ctx.obs)
    agent = Agent(
        body=body,  # type: ignore[arg-type]
        persona=ctx.persona,
        planner=planner,
        cognition=NullCognition(),
        cognition_interval=1,
        profession="blacksmith",
        goal_policy=CapabilityPolicy("blacksmith"),
    )

    class _ChangedSpeakPending(SpeakPending):
        def can_run(self, skill_ctx):  # noqa: ANN001, ANN201
            return True

        def step(self, skill_ctx):  # noqa: ANN001, ANN201
            return SkillResult(Status.RUNNING, Use(serial=999))

    pending.__class__ = _ChangedSpeakPending

    action = agent.tick()

    assert action is None
    assert body.actions == []
    assert agent.memory["capability_skill_rejections"] == 1


def test_planner_owned_capability_instance_rejects_step_override() -> None:
    ctx = _ctx(gold=100)
    installed = PROFESSIONS["blacksmith"].planner(capability_goals=True)
    bound = next(skill for skill in installed.skills if _contains_bank_gold(skill))
    bound.step = lambda skill_ctx: Use(serial=999)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="modified skill code"):
        Agent(
            body=_StaticBody(ctx.obs),  # type: ignore[arg-type]
            persona=ctx.persona,
            planner=installed,
            cognition=_FixedCognition(capability_goal("blacksmith", "bank_gold")),
            cognition_interval=1,
            profession="blacksmith",
            goal_policy=CapabilityPolicy("blacksmith"),
        )


def test_post_init_capability_helper_override_is_rejected_before_step() -> None:
    ctx = _ctx(gold=100)
    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)
    bound = next(skill for skill in planner.skills if _contains_bank_gold(skill))
    body = _StaticBody(ctx.obs)
    agent = Agent(
        body=body,  # type: ignore[arg-type]
        persona=ctx.persona,
        planner=planner,
        cognition=_FixedCognition(capability_goal("blacksmith", "bank_gold")),
        cognition_interval=1,
        profession="blacksmith",
        goal_policy=CapabilityPolicy("blacksmith"),
    )
    agent.memory["banker_spot"] = (100, 100)
    bound.inner._bank_step = lambda skill_ctx, route: Use(serial=999)  # type: ignore[method-assign]

    action = agent.tick()

    assert action is None
    assert body.actions == []
    assert agent.memory["capability_skill_rejections"] == 1


def test_merged_gold_stack_confirms_pending_release_by_bank_delta() -> None:
    ctx = _ctx(bank_gold=150)
    ctx.memory.update(
        {
            "mkt_phase": "bank",
            "bank_stage": "deposit",
            "bank_box_start": 50,
            "cap_bank_release_pending": (4, 3, 0, 50),
        }
    )

    result = BankGold().step(ctx)

    assert result.action is None
    assert "cap_bank_release_pending" not in ctx.memory
    assert ctx.memory["mkt_phase"] == "craft"


def test_hidden_bank_after_uncertain_drop_reopens_instead_of_retrying_forever() -> None:
    ctx = _ctx()
    backpack_only = [item for item in ctx.obs.items if item.layer != 0x1D]
    banker = MobileView(
        serial=9,
        name="banker",
        pos=ctx.obs.player.pos,
        body=0x190,
        notoriety=1,
        hits=100,
        hits_max=100,
        distance=0,
    )
    ctx.obs = Observation(
        player=ctx.obs.player,
        mobiles=[banker],
        items=backpack_only,
    )
    ctx.memory.update(
        {
            "banker_spot": (100, 100),
            "mkt_phase": "bank",
            "bank_stage": "deposit",
            "cap_bank_release_pending": (4, 3, 0, 50),
        }
    )
    skill = BankGold()

    first = skill.step(ctx)
    second = skill.step(ctx)

    assert first.action == Drop(serial=4, container=2)
    assert second.action == PopupRequest(serial=9)
    assert ctx.memory["cap_bank_reopen_started"] is True
    assert ctx.memory["cap_bank_release_pending"] == (4, 2, 0, 50)
