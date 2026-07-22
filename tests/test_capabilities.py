"""Closed-world capability admission and profession-planner wiring."""

from dataclasses import FrozenInstanceError

import pytest

from anima2.agent import Agent, NullCognition
from anima2.capabilities import (
    CAPABILITIES,
    CapabilityPolicy,
    capability_goal,
    issue_capability_planner_lease,
    ready_capability_ids,
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
from anima2.skills.market import BankGold, BuyIngots, BuyTool, SellDaggers
from anima2.skills.craft import DAGGER_GRAPHIC, CraftDaggers
from anima2.skills.smelt import INGOT_GRAPHICS


def _ctx(
    goal: Goal | None = None,
    *,
    gold: int = 0,
    bank_gold: int = 0,
    daggers: int = 0,
    ingots: int = 0,
    smith_tool: bool = False,
    craft_spot: object | None = None,
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
                graphic=DAGGER_GRAPHIC,
                amount=daggers,
                pos=player.pos,
                container=backpack.serial,
                layer=0,
                distance=0,
            )
        )
    if ingots:
        items.append(
            ItemView(
                serial=7,
                graphic=next(iter(INGOT_GRAPHICS)),
                amount=ingots,
                pos=player.pos,
                container=backpack.serial,
                layer=0,
                distance=0,
            )
        )
    if smith_tool:
        items.append(
            ItemView(
                serial=8,
                graphic=0x13E3,
                amount=1,
                pos=player.pos,
                container=backpack.serial,
                layer=0,
                distance=0,
            )
        )
    memory = {"banker_spot": (100, 100), "vendor_spot": (100, 100)}
    if craft_spot is not None:
        memory["craft_spot"] = craft_spot
    return SkillContext(
        obs=Observation(player=player, items=items),
        persona=Persona(name="Tormund"),
        goal=goal,
        memory=memory,
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


def _contains_craft_daggers(skill: Skill) -> bool:
    current: object = skill
    seen: set[int] = set()
    while isinstance(current, Skill) and id(current) not in seen:
        if isinstance(current, CraftDaggers):
            return True
        seen.add(id(current))
        current = getattr(current, "inner", None)
    return False


def _contains_buy_ingots(skill: Skill) -> bool:
    current: object = skill
    seen: set[int] = set()
    while isinstance(current, Skill) and id(current) not in seen:
        if isinstance(current, BuyIngots):
            return True
        seen.add(id(current))
        current = getattr(current, "inner", None)
    return False


def _contains_buy_tool(skill: Skill) -> bool:
    current: object = skill
    seen: set[int] = set()
    while isinstance(current, Skill) and id(current) not in seen:
        if isinstance(current, BuyTool):
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
        ("blacksmith", "craft_daggers"),
        ("blacksmith", "buy_ingots"),
        ("blacksmith", "buy_smith_tool"),
        ("lumberjack", "process_logs"),
        ("lumberjack", "sell_boards"),
        ("lumberjack", "bank_gold"),
        ("lumberjack", "buy_hatchet"),
        ("carpenter", "craft_carpentry"),
        ("carpenter", "sell_furniture"),
        ("carpenter", "bank_gold"),
        ("carpenter", "buy_boards"),
        ("carpenter", "buy_saw"),
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
        assert not any(_contains_craft_daggers(skill) for skill in opted_out.skills)
        assert not any(_contains_buy_ingots(skill) for skill in opted_out.skills)
        assert not any(_contains_buy_tool(skill) for skill in opted_out.skills)


def test_registry_and_profession_planners_expose_the_same_closed_capability_set():
    from anima2.profession import CapabilityBoundSkill

    # Exactly the professions with installed capabilities can build a capability
    # planner; the rest fail closed.
    capability_professions = {
        profession for profession, _capability in CAPABILITIES
    }
    assert capability_professions == {"blacksmith", "lumberjack", "carpenter"}

    planner_keys: list[tuple[str, str]] = []
    for profession in PROFESSIONS.values():
        if profession.key not in capability_professions:
            with pytest.raises(ValueError, match="no installed capabilities"):
                profession.planner(capability_goals=True)
            continue

        planner = profession.planner(capability_goals=True)
        # The planner's bound capability skills, in order, must be exactly this
        # profession's registry bindings (same order), each an untouched instance
        # of its own binding's skill_type.
        expected = [
            (prof, capability, binding.skill_type)
            for (prof, capability), binding in CAPABILITIES.items()
            if prof == profession.key
        ]
        bound = [
            skill for skill in planner.skills if isinstance(skill, CapabilityBoundSkill)
        ]
        assert len(bound) == len(expected)
        for skill, (prof, capability, skill_type) in zip(bound, expected, strict=True):
            assert type(skill.inner) is skill_type
            assert vars(skill.inner) == {}
            planner_keys.append((prof, capability))
        names = {skill.name for skill in planner.skills}
        assert "capability_complete" in names
        assert "capability_wait" in names

    registry_keys = [
        (binding.profession, binding.capability_id) for binding in _registry_bindings()
    ]
    assert planner_keys == registry_keys


def test_blacksmith_capability_manifest_includes_buy_ingots_in_registry_order():
    # Adding each new blacksmith binding must keep the shipped skill manifest
    # valid: 8 fixed scaffold skills + one bound skill per installed binding,
    # in registry order (now buy_ingots then buy_smith_tool).
    # `planner(capability_goals=True)`
    # runs `issue_capability_planner_lease`, which rejects any manifest that
    # doesn't match `_valid_capability_skill_manifest` exactly — so a successful
    # build with a non-None lease is itself the manifest-validity proof.
    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)

    assert len(planner.skills) == 8 + 5
    assert [skill.name for skill in planner.skills] == [
        "survive",
        "recover_death",
        "speak_pending",
        "goto",
        "capability_complete",
        "sell_daggers",
        "bank_gold",
        "craft_daggers",
        "buy_ingots",
        "buy_smith_tool",
        "capability_wait",
        "greet",
        "wander",
    ]
    assert planner.capability_lease is not None
    assert "buy_ingots" in planner.capability_ids
    assert "buy_smith_tool" in planner.capability_ids


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


def test_bank_goal_is_ready_for_new_pack_gold_with_a_preexisting_bank_balance():
    goal = capability_goal("blacksmith", "bank_gold")
    ctx = _ctx(goal, gold=40, bank_gold=1_000)

    resolved = resolve_capability(
        goal,
        "blacksmith",
        GoalSource.COGNITION,
        ctx,
    )

    assert resolved is not None


def test_preexisting_bank_gold_alone_neither_admits_nor_achieves_a_new_goal():
    goal = capability_goal("blacksmith", "bank_gold")
    ctx = _ctx(goal, bank_gold=1_000, goal_id=17)
    binding = CAPABILITIES[("blacksmith", "bank_gold")]

    assert resolve_capability(goal, "blacksmith", GoalSource.COGNITION, ctx) is None
    assert not binding.achieved(ctx)
    assert binding.progress(ctx) == 0.0


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


def test_valid_blacksmith_craft_goal_selects_only_the_bound_craft_skill():
    goal = capability_goal("blacksmith", "craft_daggers")
    ready = _ctx(
        goal,
        ingots=15,
        smith_tool=True,
        craft_spot=(100, 100),
    )
    resolved = resolve_capability(goal, "blacksmith", GoalSource.COGNITION, ready)
    assert resolved is not None
    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)

    selected = planner.select(
        _ctx(
            resolved.goal,
            ingots=15,
            smith_tool=True,
            craft_spot=(100, 100),
            goal_id=17,
            goal_policy=CapabilityPolicy("blacksmith"),
        )
    )

    assert _contains_craft_daggers(selected)
    assert not _contains_sell_daggers(selected)
    assert not _contains_bank_gold(selected)


def _completed_craft_ctx(*, goal_id: int = 17) -> SkillContext:
    ctx = _ctx(
        daggers=5,
        ingots=0,
        smith_tool=True,
        craft_spot=(100, 100),
        goal_id=goal_id,
    )
    ctx.obs.items = [item for item in ctx.obs.items if item.graphic != DAGGER_GRAPHIC]
    backpack = next(item for item in ctx.obs.items if item.layer == 0x15)
    ctx.obs.items.extend(
        ItemView(
            serial=0x800 + index,
            graphic=DAGGER_GRAPHIC,
            amount=1,
            pos=ctx.obs.player.pos,
            container=backpack.serial,
            layer=0,
            distance=0,
        )
        for index in range(5)
    )
    ctx.memory.update(
        {
            "cap_craft_goal_id": 17,
            "cap_craft_dagger_button_goal_id": 17,
            "cap_craft_finished_goal_id": 17,
            "cap_craft_returned_goal_id": 17,
            "cap_craft_start_ingots": 15,
            "cap_craft_start_daggers": (),
            "cap_craft_needed": 5,
            "cap_craft_confirmed": 5,
            "cap_craft_produced": tuple((0x800 + index, 1) for index in range(5)),
            "cap_craft_failed_attempts": 0,
            "cap_craft_failed_ingots": 0,
            "cap_craft_failure_costs": (),
            "cap_craft_ingots_used": 15,
            "cap_craft_close_sent": True,
            "cap_craft_stage": "finished",
        }
    )
    return ctx


def _completed_bank_ctx(
    *,
    goal_id: int = 17,
    start_bank_gold: int = 250,
    piles: tuple[tuple[int, int], ...] = ((4, 100),),
    reserve: int = 0,
) -> SkillContext:
    # `piles` are the BANKED surplus; `reserve` (default 0 == B7) is retained in
    # the pack, so the final pack gold equals the reserve, not 0.
    expected = sum(amount for _serial, amount in piles)
    ctx = _ctx(bank_gold=start_bank_gold + expected, goal_id=goal_id)
    ctx.memory.update(
        {
            "cap_bank_goal_id": goal_id,
            "cap_bank_baseline_goal_id": goal_id,
            "cap_bank_sent_goal_id": goal_id,
            "cap_bank_finished_goal_id": goal_id,
            "cap_bank_returned_goal_id": goal_id,
            "cap_bank_route": ((100, 100),),
            "cap_bank_start_piles": piles,
            "cap_bank_expected_gold": expected,
            "cap_bank_start_pack_gold": expected,
            "cap_bank_start_full_pack": expected + reserve,
            "cap_bank_start_bank_gold": start_bank_gold,
            "cap_bank_box_serial": 3,
            "cap_bank_lifted_items": piles,
            "cap_bank_dropped_items": tuple(
                (serial, amount, 3) for serial, amount in piles
            ),
            "cap_bank_pack_delta": expected,
            "cap_bank_bank_delta": expected,
            "cap_bank_confirmed": expected,
            "cap_bank_final_pack_gold": reserve,
            "cap_bank_start_piles_removed": expected,
            "cap_bank_start_piles_cleared": True,
            "mkt_phase": "craft",
        }
    )
    if reserve:
        ctx.memory["bank_reserve"] = reserve
    return ctx


def test_bank_completion_evidence_is_exactly_goal_scoped() -> None:
    binding = CAPABILITIES[("blacksmith", "bank_gold")]
    ctx = _completed_bank_ctx(start_bank_gold=1_000)

    assert binding.achieved(ctx)
    assert binding.progress(ctx) == 1.0
    assert binding.can_yield(ctx)

    ctx.goal_id = 18
    assert not binding.achieved(ctx)
    assert binding.progress(ctx) == 0.0
    assert binding.can_yield(ctx)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cap_bank_pack_delta", 99),
        ("cap_bank_pack_delta", 101),
        ("cap_bank_bank_delta", 99),
        ("cap_bank_bank_delta", 101),
        ("cap_bank_confirmed", 99),
        ("cap_bank_confirmed", 101),
        ("cap_bank_final_pack_gold", 1),
        ("cap_bank_start_piles_removed", 99),
        ("cap_bank_start_piles_cleared", False),
    ],
)
def test_bank_completion_rejects_under_over_and_one_sided_delta_evidence(
    field: str, value: object
) -> None:
    binding = CAPABILITIES[("blacksmith", "bank_gold")]
    ctx = _completed_bank_ctx()
    ctx.memory[field] = value

    assert not binding.achieved(ctx)


def test_bank_reserve_gates_readiness_on_surplus_above_the_reserve() -> None:
    goal = capability_goal("blacksmith", "bank_gold")

    # Pack gold at or below the reserve -> no surplus -> not ready.
    at_reserve = _ctx(goal, gold=88)
    at_reserve.memory["bank_reserve"] = 88
    assert resolve_capability(goal, "blacksmith", GoalSource.COGNITION, at_reserve) is None

    below_reserve = _ctx(goal, gold=50)
    below_reserve.memory["bank_reserve"] = 88
    assert resolve_capability(goal, "blacksmith", GoalSource.COGNITION, below_reserve) is None

    # A surplus above the reserve -> ready.
    above_reserve = _ctx(goal, gold=200)
    above_reserve.memory["bank_reserve"] = 88
    assert resolve_capability(goal, "blacksmith", GoalSource.COGNITION, above_reserve) is not None


def test_bank_reserve_completion_binds_deltas_to_the_surplus_and_retains_the_reserve() -> None:
    binding = CAPABILITIES[("blacksmith", "bank_gold")]
    # 200 pack gold, reserve 88 -> bank the 112 surplus, retain 88.
    ctx = _completed_bank_ctx(piles=((4, 112),), reserve=88)

    assert binding.achieved(ctx)
    assert binding.progress(ctx) == 1.0
    assert binding.can_yield(ctx)

    # The retained reserve is not surplus: the final pack gold must equal it
    # exactly — neither 0 (over-banked) nor anything else.
    ctx.memory["cap_bank_final_pack_gold"] = 0
    assert not binding.achieved(ctx)
    ctx.memory["cap_bank_final_pack_gold"] = 88
    assert binding.achieved(ctx)

    # Every delta binds to the banked surplus (112), never the full 200.
    for field in ("cap_bank_pack_delta", "cap_bank_bank_delta", "cap_bank_confirmed"):
        original = ctx.memory[field]
        ctx.memory[field] = 200
        assert not binding.achieved(ctx)
        ctx.memory[field] = original
    assert binding.achieved(ctx)


def test_bank_negative_reserve_is_clamped_and_never_makes_readiness_always_true() -> None:
    goal = capability_goal("blacksmith", "bank_gold")

    # 0 gold with a negative reserve must NOT be ready (clamped to 0 -> 0 > 0).
    broke = _ctx(goal, gold=0)
    broke.memory["bank_reserve"] = -50
    assert resolve_capability(goal, "blacksmith", GoalSource.COGNITION, broke) is None

    # Any positive gold with a negative reserve is ready (clamped to 0).
    has_gold = _ctx(goal, gold=10)
    has_gold.memory["bank_reserve"] = -50
    assert resolve_capability(goal, "blacksmith", GoalSource.COGNITION, has_gold) is not None


def test_bank_negative_reserve_clamps_achieved_final_pack_to_zero() -> None:
    binding = CAPABILITIES[("blacksmith", "bank_gold")]
    # A whole-pack completion (final pack gold 0) with a negative reserve stays
    # achieved — the reserve clamps to 0, so the required final pack gold is 0.
    ctx = _completed_bank_ctx()
    ctx.memory["bank_reserve"] = -50

    assert binding.achieved(ctx)
    ctx.memory["cap_bank_final_pack_gold"] = 50
    assert not binding.achieved(ctx)


def test_craft_completion_evidence_is_exactly_goal_scoped() -> None:
    binding = CAPABILITIES[("blacksmith", "craft_daggers")]
    ctx = _completed_craft_ctx()

    assert binding.achieved(ctx)
    assert binding.progress(ctx) == 1.0
    assert binding.can_yield(ctx)

    ctx.goal_id = 18
    assert not binding.achieved(ctx)
    assert binding.progress(ctx) == 0.0
    # Goal 18 has not emitted an action; stale completed evidence from goal 17
    # cannot achieve it, but it also cannot make this clean pre-start state
    # unsafe to cancel.
    assert binding.can_yield(ctx)


def test_craft_completion_accepts_only_fully_attributed_failed_ingot_costs() -> None:
    binding = CAPABILITIES[("blacksmith", "craft_daggers")]
    ctx = _completed_craft_ctx()
    ctx.memory.update(
        {
            "cap_craft_start_ingots": 21,
            "cap_craft_failed_attempts": 2,
            "cap_craft_failed_ingots": 6,
            "cap_craft_failure_costs": (3, 3),
            "cap_craft_ingots_used": 21,
        }
    )

    assert binding.achieved(ctx)

    ctx.memory["cap_craft_failed_ingots"] = 3
    assert not binding.achieved(ctx)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cap_craft_dagger_button_goal_id", None),
        ("cap_craft_finished_goal_id", None),
        ("cap_craft_returned_goal_id", None),
        ("cap_craft_needed", 4),
        ("cap_craft_confirmed", 4),
        ("cap_craft_produced", ((0x800, 4),)),
        ("cap_craft_ingots_used", 14),
        ("cap_craft_failed_attempts", 1),
        ("cap_craft_failed_ingots", 3),
        ("cap_craft_failure_costs", (3,)),
        ("cap_craft_start_ingots", 14),
        ("cap_craft_close_sent", False),
        ("cap_craft_attempt_gump_serial", 0xABC),
        ("cap_craft_attempt_wait", 1),
        ("cap_craft_stage", "close_wait"),
    ],
)
def test_craft_completion_requires_exact_batch_evidence_and_safe_close(
    field: str, value: object
) -> None:
    binding = CAPABILITIES[("blacksmith", "craft_daggers")]
    ctx = _completed_craft_ctx()
    ctx.memory[field] = value

    assert not binding.achieved(ctx)


def test_craft_deadline_and_preemption_wait_for_owned_gump_cleanup() -> None:
    proposal = capability_goal("blacksmith", "craft_daggers")
    policy = CapabilityPolicy("blacksmith")
    ready = _ctx(
        proposal,
        ingots=15,
        smith_tool=True,
        craft_spot=(100, 100),
    )
    resolved = resolve_capability(
        proposal, "blacksmith", GoalSource.COGNITION, ready
    )
    assert resolved is not None
    ctx = _ctx(
        resolved.goal,
        ingots=15,
        smith_tool=True,
        craft_spot=(100, 100),
        goal_id=17,
        goal_policy=policy,
    )
    ctx.memory.update(
        {
            "cap_craft_goal_id": 17,
            "cap_craft_stage": "pending",
            "cap_craft_attempt_daggers": (),
            "cap_craft_attempt_ingots": 15,
        }
    )

    assert not policy.deadline_can_expire(resolved.goal, ctx)
    assert not policy.can_preempt(resolved.goal, ctx)

    ctx.memory.update(
        {
            "cap_craft_finished_goal_id": 17,
            "cap_craft_stage": "finished",
        }
    )
    ctx.memory.pop("cap_craft_attempt_daggers")
    ctx.memory.pop("cap_craft_attempt_ingots")

    assert policy.deadline_can_expire(resolved.goal, ctx)
    assert policy.can_preempt(resolved.goal, ctx)


def test_craft_goal_can_be_cancelled_before_it_emits_its_first_action() -> None:
    proposal = capability_goal("blacksmith", "craft_daggers")
    policy = CapabilityPolicy("blacksmith")
    ready = _ctx(
        proposal,
        ingots=15,
        smith_tool=True,
        craft_spot=(100, 100),
    )
    resolved = resolve_capability(
        proposal, "blacksmith", GoalSource.COGNITION, ready
    )
    assert resolved is not None
    ctx = _ctx(
        resolved.goal,
        ingots=15,
        smith_tool=True,
        craft_spot=(100, 100),
        goal_id=17,
        goal_policy=policy,
    )

    assert policy.deadline_can_expire(resolved.goal, ctx)
    assert policy.can_preempt(resolved.goal, ctx)


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


# --- buy_ingots (B8): the sell side inverted — gold leaves, iron ingots arrive ----


def _completed_buy_ctx(
    *,
    goal_id: int = 17,
    amount: int = 15,
    unit_price: int = 5,
    iron_serial: int = 0xDD01,
) -> SkillContext:
    cost = amount * unit_price
    ctx = _ctx(gold=200, ingots=amount, goal_id=goal_id)
    ctx.memory.update(
        {
            "cap_buy_goal_id": goal_id,
            "cap_buy_sent_goal_id": goal_id,
            "cap_buy_finished_goal_id": goal_id,
            "cap_buy_returned_goal_id": goal_id,
            "cap_buy_bought_ingots": amount,
            "cap_buy_expected_cost": cost,
            "cap_buy_offer": (iron_serial, amount, unit_price),
            "cap_buy_ingot_delta": amount,
            "cap_buy_gold_delta": cost,
            "mkt_phase": "craft",
        }
    )
    return ctx


def test_valid_blacksmith_buy_goal_selects_only_the_bound_buy_skill() -> None:
    goal = capability_goal("blacksmith", "buy_ingots")
    ready = _ctx(goal, gold=100, ingots=0)
    resolved = resolve_capability(goal, "blacksmith", GoalSource.COGNITION, ready)
    assert resolved is not None
    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)

    selected = planner.select(
        _ctx(
            resolved.goal,
            gold=100,
            ingots=0,
            goal_id=17,
            goal_policy=CapabilityPolicy("blacksmith"),
        )
    )

    assert _contains_buy_ingots(selected)
    assert not _contains_sell_daggers(selected)
    assert not _contains_bank_gold(selected)
    assert not _contains_craft_daggers(selected)


def test_buy_goal_is_ready_only_below_reorder_and_when_affordable() -> None:
    goal = capability_goal("blacksmith", "buy_ingots")

    # Below the reorder point (15) and able to afford the fixed batch
    # (BUY_AMOUNT * 5 == 75 gold) — ready.
    assert (
        resolve_capability(goal, "blacksmith", GoalSource.COGNITION, _ctx(goal, gold=100, ingots=0))
        is not None
    )
    assert (
        resolve_capability(goal, "blacksmith", GoalSource.COGNITION, _ctx(goal, gold=100, ingots=14))
        is not None
    )
    # At or above the reorder point — a full craft batch still fits, nothing to buy.
    assert (
        resolve_capability(goal, "blacksmith", GoalSource.COGNITION, _ctx(goal, gold=100, ingots=15))
        is None
    )
    # Below reorder but can't afford the fixed batch.
    assert (
        resolve_capability(goal, "blacksmith", GoalSource.COGNITION, _ctx(goal, gold=74, ingots=0))
        is None
    )


def test_buy_goal_requires_a_vendor_route() -> None:
    goal = capability_goal("blacksmith", "buy_ingots")
    ctx = _ctx(goal, gold=100, ingots=0)
    ctx.memory.pop("vendor_spot")

    assert resolve_capability(goal, "blacksmith", GoalSource.COGNITION, ctx) is None


def test_preexisting_low_ingots_alone_neither_admits_nor_achieves_a_buy_goal() -> None:
    goal = capability_goal("blacksmith", "buy_ingots")
    # Low ingots but no gold: replenishment is needed yet unaffordable.
    ctx = _ctx(goal, gold=0, ingots=0, goal_id=17)
    binding = CAPABILITIES[("blacksmith", "buy_ingots")]

    assert resolve_capability(goal, "blacksmith", GoalSource.COGNITION, ctx) is None
    assert not binding.achieved(ctx)
    assert binding.progress(ctx) == 0.0


def test_buy_completion_evidence_is_exactly_goal_scoped() -> None:
    binding = CAPABILITIES[("blacksmith", "buy_ingots")]
    ctx = _completed_buy_ctx()

    assert binding.achieved(ctx)
    assert binding.progress(ctx) == 1.0
    assert binding.can_yield(ctx)

    ctx.goal_id = 18
    assert not binding.achieved(ctx)
    assert binding.progress(ctx) == 0.0
    # Goal 18 has emitted nothing; stale goal-17 evidence cannot achieve it, but
    # this clean pre-start state is still safe to cancel.
    assert binding.can_yield(ctx)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cap_buy_sent_goal_id", None),
        ("cap_buy_finished_goal_id", None),
        ("cap_buy_returned_goal_id", None),
        # Short arrival: fewer ingots than the batch bought.
        ("cap_buy_ingot_delta", 14),
        # No arrival at all (gold spent on nothing / a non-iron item).
        ("cap_buy_ingot_delta", 0),
        # Underspend and overspend must both fail the exact-cost check.
        ("cap_buy_gold_delta", 74),
        ("cap_buy_gold_delta", 76),
        # No gold left the pack at all.
        ("cap_buy_gold_delta", 0),
        # The observed offer must account for the whole batch at the quote.
        ("cap_buy_offer", (0xDD01, 10, 5)),
        ("cap_buy_offer", (0xDD01, 15, 4)),
        ("cap_buy_bought_ingots", 10),
        ("cap_buy_expected_cost", 74),
        # A live vendor UI or an in-flight transaction is never a yield point.
        ("mkt_phase", "buy_return"),
        ("buy_stage", "window"),
    ],
)
def test_buy_completion_requires_exact_offer_spend_and_arrival(
    field: str, value: object
) -> None:
    binding = CAPABILITIES[("blacksmith", "buy_ingots")]
    ctx = _completed_buy_ctx()
    ctx.memory[field] = value

    assert not binding.achieved(ctx)


def test_buy_completion_rejects_a_live_buy_window_even_with_full_evidence() -> None:
    binding = CAPABILITIES[("blacksmith", "buy_ingots")]
    ctx = _completed_buy_ctx()
    ctx.obs.shop_buy = object()  # type: ignore[assignment]

    assert not binding.achieved(ctx)


def test_buy_deadline_and_preemption_wait_for_vendor_transaction_yield() -> None:
    proposal = capability_goal("blacksmith", "buy_ingots")
    policy = CapabilityPolicy("blacksmith")
    resolved = resolve_capability(
        proposal,
        "blacksmith",
        GoalSource.COGNITION,
        _ctx(proposal, gold=100, ingots=0),
    )
    assert resolved is not None
    ctx = _ctx(
        resolved.goal,
        gold=100,
        ingots=0,
        goal_id=17,
        goal_policy=policy,
    )
    ctx.memory.update({"mkt_phase": "buy", "buy_stage": "window"})

    assert not policy.deadline_can_expire(resolved.goal, ctx)
    assert not policy.can_preempt(resolved.goal, ctx)

    ctx.memory["mkt_phase"] = "craft"
    ctx.memory.pop("buy_stage")

    assert policy.deadline_can_expire(resolved.goal, ctx)
    assert policy.can_preempt(resolved.goal, ctx)


# --- buy_smith_tool (B8): replace a broken tool — the acquisition set's other half -


def _completed_toolbuy_ctx(
    *,
    goal_id: int = 17,
    unit_price: int = 13,
    tongs_serial: int = 0xDD02,
) -> SkillContext:
    # A bought tool is present in the pack (smith_tool=True), but the recorded
    # start count was 0 — a genuine 0->1 acquisition.
    ctx = _ctx(gold=200, smith_tool=True, goal_id=goal_id)
    ctx.memory.update(
        {
            "cap_toolbuy_goal_id": goal_id,
            "cap_toolbuy_sent_goal_id": goal_id,
            "cap_toolbuy_finished_goal_id": goal_id,
            "cap_toolbuy_returned_goal_id": goal_id,
            "cap_toolbuy_bought_tools": 1,
            "cap_toolbuy_expected_cost": unit_price,
            "cap_toolbuy_offer": (tongs_serial, 1, unit_price),
            "cap_toolbuy_start_tools": 0,
            "cap_toolbuy_tool_delta": 1,
            "cap_toolbuy_gold_delta": unit_price,
            "mkt_phase": "craft",
        }
    )
    return ctx


def test_valid_blacksmith_toolbuy_goal_selects_only_the_bound_tool_skill() -> None:
    goal = capability_goal("blacksmith", "buy_smith_tool")
    ready = _ctx(goal, gold=100)  # no smith tool present
    resolved = resolve_capability(goal, "blacksmith", GoalSource.COGNITION, ready)
    assert resolved is not None
    planner = PROFESSIONS["blacksmith"].planner(capability_goals=True)

    selected = planner.select(
        _ctx(
            resolved.goal,
            gold=100,
            goal_id=17,
            goal_policy=CapabilityPolicy("blacksmith"),
        )
    )

    assert _contains_buy_tool(selected)
    assert not _contains_buy_ingots(selected)
    assert not _contains_sell_daggers(selected)
    assert not _contains_bank_gold(selected)
    assert not _contains_craft_daggers(selected)


def test_toolbuy_goal_is_ready_only_without_a_tool_and_when_affordable() -> None:
    goal = capability_goal("blacksmith", "buy_smith_tool")

    # No working tool and able to afford one (TOOL_BUY_AMOUNT * 13 == 13) — ready.
    assert (
        resolve_capability(goal, "blacksmith", GoalSource.COGNITION, _ctx(goal, gold=100))
        is not None
    )
    assert (
        resolve_capability(goal, "blacksmith", GoalSource.COGNITION, _ctx(goal, gold=13))
        is not None
    )
    # A working tool is present — nothing to replace.
    assert (
        resolve_capability(
            goal, "blacksmith", GoalSource.COGNITION, _ctx(goal, gold=100, smith_tool=True)
        )
        is None
    )
    # No tool but can't afford one.
    assert (
        resolve_capability(goal, "blacksmith", GoalSource.COGNITION, _ctx(goal, gold=12))
        is None
    )


def test_toolbuy_goal_requires_a_vendor_route() -> None:
    goal = capability_goal("blacksmith", "buy_smith_tool")
    ctx = _ctx(goal, gold=100)
    ctx.memory.pop("vendor_spot")

    assert resolve_capability(goal, "blacksmith", GoalSource.COGNITION, ctx) is None


def test_preexisting_no_tool_alone_neither_admits_nor_achieves_a_toolbuy_goal() -> None:
    goal = capability_goal("blacksmith", "buy_smith_tool")
    # No tool but no gold: replacement is needed yet unaffordable.
    ctx = _ctx(goal, gold=0, goal_id=17)
    binding = CAPABILITIES[("blacksmith", "buy_smith_tool")]

    assert resolve_capability(goal, "blacksmith", GoalSource.COGNITION, ctx) is None
    assert not binding.achieved(ctx)
    assert binding.progress(ctx) == 0.0


def test_toolbuy_completion_evidence_is_exactly_goal_scoped() -> None:
    binding = CAPABILITIES[("blacksmith", "buy_smith_tool")]
    ctx = _completed_toolbuy_ctx()

    assert binding.achieved(ctx)
    assert binding.progress(ctx) == 1.0
    assert binding.can_yield(ctx)

    ctx.goal_id = 18
    assert not binding.achieved(ctx)
    assert binding.progress(ctx) == 0.0
    assert binding.can_yield(ctx)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cap_toolbuy_sent_goal_id", None),
        ("cap_toolbuy_finished_goal_id", None),
        ("cap_toolbuy_returned_goal_id", None),
        # No tool arrived (gold spent on nothing / a non-tongs item).
        ("cap_toolbuy_tool_delta", 0),
        # Underspend and overspend must both fail the exact-cost check.
        ("cap_toolbuy_gold_delta", 12),
        ("cap_toolbuy_gold_delta", 14),
        ("cap_toolbuy_gold_delta", 0),
        # Not a genuine acquisition: a tool was already in the pack at the start.
        ("cap_toolbuy_start_tools", 1),
        # The observed offer must account for the one tool at the quote.
        ("cap_toolbuy_offer", (0xDD02, 2, 13)),
        ("cap_toolbuy_offer", (0xDD02, 1, 12)),
        ("cap_toolbuy_bought_tools", 2),
        ("cap_toolbuy_expected_cost", 12),
        # A live vendor UI or an in-flight transaction is never a yield point.
        ("mkt_phase", "toolbuy_return"),
        ("toolbuy_stage", "window"),
    ],
)
def test_toolbuy_completion_requires_exact_offer_spend_and_arrival(
    field: str, value: object
) -> None:
    binding = CAPABILITIES[("blacksmith", "buy_smith_tool")]
    ctx = _completed_toolbuy_ctx()
    ctx.memory[field] = value

    assert not binding.achieved(ctx)


def test_toolbuy_completion_requires_a_tool_present_in_the_pack() -> None:
    binding = CAPABILITIES[("blacksmith", "buy_smith_tool")]
    ctx = _completed_toolbuy_ctx()
    # The recorded delta says one arrived, but the live observation shows none —
    # completion must fail closed on the current observed pack, not stale memory.
    ctx.obs.items = [item for item in ctx.obs.items if item.graphic != 0x13E3]

    assert not binding.achieved(ctx)


def test_toolbuy_completion_rejects_a_live_buy_window_even_with_full_evidence() -> None:
    binding = CAPABILITIES[("blacksmith", "buy_smith_tool")]
    ctx = _completed_toolbuy_ctx()
    ctx.obs.shop_buy = object()  # type: ignore[assignment]

    assert not binding.achieved(ctx)


def test_toolbuy_deadline_and_preemption_wait_for_vendor_transaction_yield() -> None:
    proposal = capability_goal("blacksmith", "buy_smith_tool")
    policy = CapabilityPolicy("blacksmith")
    resolved = resolve_capability(
        proposal,
        "blacksmith",
        GoalSource.COGNITION,
        _ctx(proposal, gold=100),
    )
    assert resolved is not None
    ctx = _ctx(
        resolved.goal,
        gold=100,
        goal_id=17,
        goal_policy=policy,
    )
    ctx.memory.update({"mkt_phase": "toolbuy", "toolbuy_stage": "window"})

    assert not policy.deadline_can_expire(resolved.goal, ctx)
    assert not policy.can_preempt(resolved.goal, ctx)

    ctx.memory["mkt_phase"] = "craft"
    ctx.memory.pop("toolbuy_stage")

    assert policy.deadline_can_expire(resolved.goal, ctx)
    assert policy.can_preempt(resolved.goal, ctx)


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


def _mark_active_bank_goal_terminal(agent: Agent, *, achieved: bool = False) -> None:
    frame = agent.goal_stack.current
    assert frame is not None
    goal_id = frame.id
    for key in (
        "bank_leg",
        "bank_stage",
        "bank_banker",
        "bank_find_wait",
        "bank_popup_wait",
        "bank_popup_total",
        "bank_settle",
        "bank_deposit_attempts",
        "bank_return_leg",
        "bank_held",
        "cap_bank_release_pending",
        "cap_bank_recovery_drop_sent",
        "cap_bank_reopen_started",
    ):
        agent.memory.pop(key, None)
    agent.memory.update(
        {
            "mkt_phase": "craft",
            "cap_bank_goal_id": goal_id,
            "cap_bank_finished_goal_id": goal_id,
        }
    )
    if not achieved:
        return

    completed = _completed_bank_ctx(goal_id=goal_id, start_bank_gold=0)
    agent.body.observation = completed.obs  # type: ignore[attr-defined]
    agent.memory.update(completed.memory)


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
    _mark_active_bank_goal_terminal(agent)

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
    _mark_active_bank_goal_terminal(agent, achieved=True)

    agent.tick()

    assert agent.goal is None
    assert agent.goal_stack.history[-1].outcome is GoalOutcome.SUCCESS


def test_expired_cognition_proposal_cannot_refresh_its_deadline() -> None:
    proposal = capability_goal("blacksmith", "bank_gold")
    agent = _capability_agent(proposal)
    agent.tick()
    assert agent.goal_stack.current is not None
    agent.ticks = agent.goal_stack.current.deadline_tick or 0
    _mark_active_bank_goal_terminal(agent)

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
    _mark_active_bank_goal_terminal(agent)
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
    _mark_active_bank_goal_terminal(agent)
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


def test_merged_gold_stack_releases_pending_cursor_by_bank_delta() -> None:
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
    # Without a goal id this recovery-only call must not start or complete a
    # fresh deposit merely because the bank already contains enough gold.
    assert ctx.memory["mkt_phase"] == "bank"


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


# --- lumberjack capabilities (Brick 2): the generalized market machinery for a new
# profession + items — process_logs / sell_boards / bank_gold / buy_hatchet -------

from anima2.skills.woodwork import (  # noqa: E402
    BOARD_GRAPHIC as _BOARD_GRAPHIC,
    LOG_GRAPHIC as _LOG_GRAPHIC,
    BuyHatchet as _BuyHatchet,
    ProcessLogsGoal as _ProcessLogsGoal,
    SellBoards as _SellBoards,
)

_HATCHET = 0x0F43  # a pack hatchet (an AXE_GRAPHICS member)


def _lumber_ctx(
    goal: Goal | None = None,
    *,
    gold: int = 0,
    boards: int = 0,
    logs: int = 0,
    axe: bool = False,
    goal_id: int | None = None,
    goal_policy: CapabilityPolicy | None = None,
) -> SkillContext:
    player = PlayerView(serial=1, pos=Position(100, 100, 0), hits=100, hits_max=100)
    backpack = ItemView(serial=2, graphic=0x0E75, amount=1, pos=player.pos,
                        container=player.serial, layer=0x15, distance=0)
    bank_box = ItemView(serial=3, graphic=0x0E7C, amount=1, pos=player.pos,
                        container=player.serial, layer=0x1D, distance=0)
    items = [backpack, bank_box]
    if gold:
        items.append(ItemView(serial=4, graphic=0x0EED, amount=gold, pos=player.pos,
                              container=backpack.serial, layer=0, distance=0))
    if boards:
        items.append(ItemView(serial=5, graphic=_BOARD_GRAPHIC, amount=boards, pos=player.pos,
                              container=backpack.serial, layer=0, distance=0))
    if logs:
        items.append(ItemView(serial=6, graphic=_LOG_GRAPHIC, amount=logs, pos=player.pos,
                              container=backpack.serial, layer=0, distance=0))
    if axe:
        items.append(ItemView(serial=7, graphic=_HATCHET, amount=1, pos=player.pos,
                              container=backpack.serial, layer=0, distance=0))
    # Two vendors (Carpenter=vendor_spot, WeaponSmith=tool_vendor_spot) + banker.
    memory = {
        "vendor_spot": (100, 100),
        "tool_vendor_spot": (100, 100),
        "banker_spot": (100, 100),
    }
    return SkillContext(
        obs=Observation(player=player, items=items),
        persona=Persona(name="Bjorn"),
        goal=goal,
        memory=memory,
        goal_id=goal_id,
        goal_policy=goal_policy,
    )


def test_lumberjack_manifest_includes_all_four_capabilities_in_registry_order():
    planner = PROFESSIONS["lumberjack"].planner(capability_goals=True)
    assert len(planner.skills) == 8 + 4
    names = [skill.name for skill in planner.skills]
    assert names == [
        "survive", "recover_death", "speak_pending", "goto", "capability_complete",
        "process_logs", "sell_boards", "bank_gold", "buy_hatchet",
        "capability_wait", "greet", "wander",
    ]
    assert planner.capability_lease is not None
    assert planner.capability_ids == frozenset(
        {"process_logs", "sell_boards", "bank_gold", "buy_hatchet"}
    )


def test_lumberjack_sell_boards_ready_only_at_threshold_from_the_carpenter():
    goal = capability_goal("lumberjack", "sell_boards")
    # 20+ boards + a carpenter (vendor_spot) route -> ready.
    assert resolve_capability(goal, "lumberjack", GoalSource.COGNITION, _lumber_ctx(goal, boards=20)) is not None
    # Below the 20-board threshold -> not ready.
    assert resolve_capability(goal, "lumberjack", GoalSource.COGNITION, _lumber_ctx(goal, boards=19)) is None
    # No carpenter route -> not ready.
    no_vendor = _lumber_ctx(goal, boards=20)
    no_vendor.memory.pop("vendor_spot")
    assert resolve_capability(goal, "lumberjack", GoalSource.COGNITION, no_vendor) is None


def test_lumberjack_buy_hatchet_ready_only_without_an_axe_at_the_tool_vendor():
    goal = capability_goal("lumberjack", "buy_hatchet")
    # No axe + affordable + a WeaponSmith (tool_vendor_spot) route -> ready.
    assert resolve_capability(goal, "lumberjack", GoalSource.COGNITION, _lumber_ctx(goal, gold=100)) is not None
    # An axe present -> nothing to replace.
    assert resolve_capability(goal, "lumberjack", GoalSource.COGNITION, _lumber_ctx(goal, gold=100, axe=True)) is None
    # Can't afford a hatchet (25g estimate).
    assert resolve_capability(goal, "lumberjack", GoalSource.COGNITION, _lumber_ctx(goal, gold=24)) is None
    # No tool vendor route -> not ready (even though a Carpenter vendor_spot exists).
    no_tool_vendor = _lumber_ctx(goal, gold=100)
    no_tool_vendor.memory.pop("tool_vendor_spot")
    assert resolve_capability(goal, "lumberjack", GoalSource.COGNITION, no_tool_vendor) is None


def test_lumberjack_process_logs_ready_only_with_logs_and_an_axe():
    goal = capability_goal("lumberjack", "process_logs")
    assert resolve_capability(goal, "lumberjack", GoalSource.COGNITION, _lumber_ctx(goal, logs=18, axe=True)) is not None
    # No logs -> nothing to process.
    assert resolve_capability(goal, "lumberjack", GoalSource.COGNITION, _lumber_ctx(goal, logs=0, axe=True)) is None
    # No axe -> can't convert.
    assert resolve_capability(goal, "lumberjack", GoalSource.COGNITION, _lumber_ctx(goal, logs=18, axe=False)) is None


def test_lumberjack_process_completion_binds_boards_to_the_frozen_logs():
    binding = CAPABILITIES[("lumberjack", "process_logs")]
    ctx = _lumber_ctx(boards=18, goal_id=17)
    ctx.memory.update({
        "cap_process_goal_id": 17,
        "cap_process_finished_goal_id": 17,
        "cap_process_needed": 18,
        "cap_process_board_delta": 18,
        "cap_process_logs_remaining": 0,
    })
    assert binding.achieved(ctx)
    assert binding.progress(ctx) == 1.0

    # Short conversion (fewer boards than the frozen logs) fails.
    ctx.memory["cap_process_board_delta"] = 17
    assert not binding.achieved(ctx)
    ctx.memory["cap_process_board_delta"] = 18
    # Logs still remaining (not all converted) fails.
    ctx.memory["cap_process_logs_remaining"] = 1
    assert not binding.achieved(ctx)
    ctx.memory["cap_process_logs_remaining"] = 0
    # A stale goal_id can't complete it.
    ctx.goal_id = 18
    assert not binding.achieved(ctx)


def test_lumberjack_ready_capability_ids_surfaces_its_own_closed_set():
    # A lumberjack with logs+axe, 20 boards, and gold: process/sell/bank ready in
    # registry order (buy_hatchet excluded — an axe is present). The cognition-
    # facing list is scoped to the profession's own bindings.
    ctx = _lumber_ctx(gold=100, boards=20, logs=18, axe=True)
    ids = ready_capability_ids("lumberjack", ctx)
    assert ids == ("process_logs", "sell_boards", "bank_gold")
    # The list is scoped to lumberjack bindings only — no blacksmith ids appear.
    assert "sell_daggers" not in ids
    assert "buy_ingots" not in ids
    assert "craft_daggers" not in ids


def test_lumberjack_bank_gold_reuses_the_blacksmith_leaf_funcs():
    goal = capability_goal("lumberjack", "bank_gold")
    # Reuses the profession-agnostic bank machinery: ready with pack gold + a banker.
    assert resolve_capability(goal, "lumberjack", GoalSource.COGNITION, _lumber_ctx(goal, gold=250)) is not None
    from anima2.skills.market import BankGold
    assert CAPABILITIES[("lumberjack", "bank_gold")].skill_type is BankGold
    assert CAPABILITIES[("lumberjack", "sell_boards")].skill_type is _SellBoards
    assert CAPABILITIES[("lumberjack", "buy_hatchet")].skill_type is _BuyHatchet
    assert CAPABILITIES[("lumberjack", "process_logs")].skill_type is _ProcessLogsGoal
