"""Closed, immutable execution capabilities for autonomous Goal proposals.

This registry is intentionally separate from ``skill_library.REGISTRY``.  The
skill library is descriptive/retrieval metadata; this module is an authority
boundary.  A Goal can name only an opaque, hand-written capability id.  Trusted
code binds that id to one shipped leaf skill plus its readiness, completion,
progress, yield, source, profession, and deadline policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Mapping

from .goals import GoalAdmission, GoalSource
from .skills import Skill
from .skills.base import Goal, SkillContext
from .skills.market import BankGold

_BACKPACK_LAYER = 0x15
_BANKBOX_LAYER = 0x1D
_GOLD_GRAPHIC = 0x0EED
_GOAL_KEYS = frozenset({"schema", "profession", "capability"})
_CAPABILITY_AUTHORITY = object()
_PLANNER_AUTHORITY = object()


@dataclass(frozen=True)
class CapabilityBinding:
    """One auditable profession capability; never constructed from model text."""

    capability_id: str
    profession: str
    skill_type: type[Skill]
    allowed_sources: frozenset[GoalSource]
    ready: Callable[[SkillContext], bool]
    achieved: Callable[[SkillContext], bool]
    progress: Callable[[SkillContext], float]
    can_yield: Callable[[SkillContext], bool]
    default_deadline_ticks: int


@dataclass(frozen=True)
class ResolvedCapability:
    """A canonical sealed Goal and the sole trusted binding that may serve it."""

    goal: Goal
    binding: CapabilityBinding


@dataclass(frozen=True)
class CapabilityPlannerLease:
    """Proof that the capability planner came from the profession factory."""

    profession: str
    capability_ids: frozenset[str]
    installed_skills: tuple[Skill, ...] = field(repr=False, compare=False)
    _authority: object = field(repr=False, compare=False)


def _backpack_serial(ctx: SkillContext) -> int | None:
    item = next(
        (
            item
            for item in ctx.obs.items
            if item.layer == _BACKPACK_LAYER and item.container == ctx.obs.player.serial
        ),
        None,
    )
    return item.serial if item is not None else None


def _bankbox_serial(ctx: SkillContext) -> int | None:
    item = next(
        (
            item
            for item in ctx.obs.items
            if item.layer == _BANKBOX_LAYER and item.container == ctx.obs.player.serial
        ),
        None,
    )
    return item.serial if item is not None else None


def _container_gold(ctx: SkillContext, container: int | None) -> int:
    if container is None:
        return 0
    return sum(
        item.amount
        for item in ctx.obs.items
        if item.graphic == _GOLD_GRAPHIC and item.container == container
    )


def _pack_gold(ctx: SkillContext) -> int:
    return _container_gold(ctx, _backpack_serial(ctx))


def _bank_gold(ctx: SkillContext) -> int:
    return _container_gold(ctx, _bankbox_serial(ctx))


def _valid_spot(value: object) -> bool:
    if not isinstance(value, (tuple, list)) or not value:
        return False
    points = value if isinstance(value[0], (tuple, list)) else (value,)
    return all(
        isinstance(point, (tuple, list))
        and len(point) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in point)
        for point in points
    )


def _bank_ready(ctx: SkillContext) -> bool:
    obs = ctx.obs
    return bool(
        _valid_spot(ctx.memory.get("banker_spot"))
        and _backpack_serial(ctx) is not None
        and _pack_gold(ctx) >= 100
        and _bank_gold(ctx) < 100
        and obs.pending_target is None
        and not obs.gumps
        and obs.popup is None
        and obs.shop_buy is None
        and obs.shop_sell is None
    )


def _bank_can_yield(ctx: SkillContext) -> bool:
    obs = ctx.obs
    return bool(
        ctx.memory.get("mkt_phase", "craft") == "craft"
        and ctx.memory.get("bank_held") is None
        and ctx.memory.get("cap_bank_release_pending") is None
        and obs.pending_target is None
        and not obs.gumps
        and obs.popup is None
        and obs.shop_buy is None
        and obs.shop_sell is None
    )


def _bank_achieved(ctx: SkillContext) -> bool:
    return _bank_gold(ctx) >= 100 and _bank_can_yield(ctx)


def _bank_progress(ctx: SkillContext) -> float:
    return max(0.0, min(1.0, _bank_gold(ctx) / 100.0))


_BANK_GOLD = CapabilityBinding(
    capability_id="bank_gold",
    profession="blacksmith",
    skill_type=BankGold,
    allowed_sources=frozenset({GoalSource.COGNITION, GoalSource.USER, GoalSource.SYSTEM}),
    ready=_bank_ready,
    achieved=_bank_achieved,
    progress=_bank_progress,
    can_yield=_bank_can_yield,
    default_deadline_ticks=120,
)

CAPABILITIES: Mapping[tuple[str, str], CapabilityBinding] = MappingProxyType(
    {(_BANK_GOLD.profession, _BANK_GOLD.capability_id): _BANK_GOLD}
)


def _valid_capability_skill_manifest(
    profession: str,
    skills: tuple[Skill, ...],
) -> bool:
    """Match the exact shipped factory order, types, bindings, and defaults."""

    # Local imports avoid a module cycle: Profession calls the issuer only
    # after this module and its own class definitions have finished loading.
    from .profession import CapabilityBoundSkill, CapabilityGoalComplete, CapabilityWait
    from .skills import GoTo, Greet, RecoverDeath, SpeakPending, Survive, Wander

    bindings = tuple(
        binding
        for (bound_profession, _capability), binding in CAPABILITIES.items()
        if bound_profession == profession
    )
    expected_length = 8 + len(bindings)
    if len(skills) != expected_length:
        return False
    prefix = skills[:4]
    goal_complete = skills[4]
    bound_skills = skills[5 : 5 + len(bindings)]
    wait, greet, wander = skills[-3:]
    if tuple(type(skill) for skill in prefix) != (
        Survive,
        RecoverDeath,
        SpeakPending,
        GoTo,
    ):
        return False
    if (
        vars(prefix[0]) != {}
        or vars(prefix[1]) != {"resurrection_target": None}
        or vars(prefix[2]) != {}
        or vars(prefix[3]) != {}
        or type(goal_complete) is not CapabilityGoalComplete
        or vars(goal_complete) != {"profession": profession}
        or type(wait) is not CapabilityWait
        or vars(wait) != {"profession": profession}
        or type(greet) is not Greet
        or vars(greet) != {}
        or type(wander) is not Wander
        or vars(wander) != {}
    ):
        return False
    for wrapper, binding in zip(bound_skills, bindings, strict=True):
        if type(wrapper) is not CapabilityBoundSkill:
            return False
        inner = getattr(wrapper, "inner", None)
        if type(inner) is not binding.skill_type or vars(inner) != {}:
            return False
        if vars(wrapper) != {
            "profession": profession,
            "inner": inner,
            "name": inner.name,
            "description": inner.description,
        }:
            return False
    return True


def issue_capability_planner_lease(
    profession: str,
    skills: tuple[Skill, ...],
) -> CapabilityPlannerLease:
    """Bind a lease to one exact, validated factory skill manifest."""

    capability_ids = frozenset(
        capability
        for bound_profession, capability in CAPABILITIES
        if bound_profession == profession
    )
    if not capability_ids:
        raise ValueError(f"profession {profession!r} has no installed capabilities")
    if not _valid_capability_skill_manifest(profession, skills):
        raise ValueError("capability planner does not match the shipped factory manifest")
    return CapabilityPlannerLease(
        profession,
        capability_ids,
        skills,
        _PLANNER_AUTHORITY,
    )


def valid_capability_planner_lease(
    value: object,
    skills: tuple[Skill, ...],
) -> bool:
    """Validate planner provenance without trusting mutable marker attributes."""

    return bool(
        type(value) is CapabilityPlannerLease
        and value._authority is _PLANNER_AUTHORITY
        and value.capability_ids
        and len(value.installed_skills) == len(skills)
        and all(
            installed is current
            for installed, current in zip(value.installed_skills, skills, strict=True)
        )
        and _valid_capability_skill_manifest(value.profession, skills)
        and value.capability_ids
        == frozenset(
            capability
            for bound_profession, capability in CAPABILITIES
            if bound_profession == value.profession
        )
    )


def capability_goal(profession: str, capability: str) -> Goal:
    """Construct an unsealed request; admission returns a separate sealed copy."""

    return Goal(
        kind="capability",
        params={"schema": 1, "profession": profession, "capability": capability},
    )


def binding_for_goal(
    goal: Goal,
    profession: str,
    source: GoalSource,
) -> CapabilityBinding | None:
    """Resolve structure and authority only, with exact keys and types."""

    binding = _structural_binding_for_goal(goal, profession)
    if (
        binding is None
        or not isinstance(source, GoalSource)
        or source not in binding.allowed_sources
    ):
        return None
    return binding


def installed_binding_for_goal(
    goal: Goal,
    profession: str,
) -> CapabilityBinding | None:
    """Resolve an already-admitted frame against installed profession hands."""

    if not isinstance(goal, Goal) or not goal.sealed_by(_CAPABILITY_AUTHORITY):
        return None
    return _structural_binding_for_goal(goal, profession)


def execution_goal_copy(goal: Goal, profession: str) -> Goal | None:
    """Return a fresh authority-sealed copy for deterministic SkillContext use."""

    binding = installed_binding_for_goal(goal, profession)
    if binding is None:
        return None
    return capability_goal(binding.profession, binding.capability_id).seal(
        _CAPABILITY_AUTHORITY
    )


def policy_binding_for_context(
    ctx: SkillContext,
    profession: str,
) -> CapabilityBinding | None:
    """Require both a canonical Goal and the exact Agent-installed policy."""

    policy = ctx.goal_policy
    if type(policy) is not CapabilityPolicy or policy.profession != profession:
        return None
    if ctx.goal is None:
        return None
    binding = installed_binding_for_goal(ctx.goal, profession)
    if binding is None or binding.capability_id not in policy.capability_ids:
        return None
    return binding


def _structural_binding_for_goal(
    goal: Goal,
    profession: str,
) -> CapabilityBinding | None:
    if not isinstance(goal, Goal) or goal.kind != "capability":
        return None
    params = goal.params
    if not isinstance(params, Mapping) or set(params) != _GOAL_KEYS:
        return None
    schema = params.get("schema")
    goal_profession = params.get("profession")
    capability_id = params.get("capability")
    if type(schema) is not int or schema != 1:
        return None
    if type(goal_profession) is not str or goal_profession != profession:
        return None
    if type(capability_id) is not str or len(capability_id) > 80:
        return None
    binding = CAPABILITIES.get((profession, capability_id))
    if binding is None or binding.profession != profession:
        return None
    return binding


def resolve_capability(
    goal: Goal,
    profession: str,
    source: GoalSource,
    ctx: SkillContext,
) -> ResolvedCapability | None:
    """Admit a ready request and detach it from producer-owned mutable state."""

    binding = binding_for_goal(goal, profession, source)
    if binding is None:
        return None
    try:
        ready = bool(binding.ready(ctx))
    except Exception:  # noqa: BLE001 — authority callbacks fail closed
        ready = False
    if not ready:
        return None
    canonical = capability_goal(binding.profession, binding.capability_id).seal(
        _CAPABILITY_AUTHORITY
    )
    return ResolvedCapability(goal=canonical, binding=binding)


@dataclass(frozen=True)
class CapabilityPolicy:
    """Agent/planner policy view over the immutable registry for one profession."""

    profession: str
    capability_ids: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        capability_ids = frozenset(
            capability
            for (bound_profession, capability) in CAPABILITIES
            if bound_profession == self.profession
        )
        if not capability_ids:
            raise ValueError(f"profession {self.profession!r} has no installed capabilities")
        object.__setattr__(self, "capability_ids", capability_ids)

    def admit_goal(
        self,
        goal: Goal,
        ctx: SkillContext,
        source: GoalSource,
    ) -> GoalAdmission | None:
        resolved = resolve_capability(goal, self.profession, source, ctx)
        if resolved is None:
            return None
        return GoalAdmission(
            goal=resolved.goal,
            deadline_ticks=resolved.binding.default_deadline_ticks,
        )

    def binding(self, goal: Goal, source: GoalSource = GoalSource.COGNITION) -> CapabilityBinding | None:
        return binding_for_goal(goal, self.profession, source)

    def goal_progress(self, goal: Goal, ctx: SkillContext) -> float | None:
        binding = installed_binding_for_goal(goal, self.profession)
        if binding is None:
            return None
        try:
            return float(binding.progress(ctx))
        except Exception:  # noqa: BLE001 — telemetry callbacks fail closed
            return None

    def deadline_can_expire(self, goal: Goal, ctx: SkillContext) -> bool:
        """Expire only outside a transaction and never race observed success."""

        binding = installed_binding_for_goal(goal, self.profession)
        if binding is None:
            return False
        try:
            if binding.achieved(ctx):
                return False  # let the terminal skill archive SUCCESS this tick
            return bool(binding.can_yield(ctx))
        except Exception:  # noqa: BLE001 — deadline safety fails closed
            return False

    def can_preempt(self, goal: Goal, ctx: SkillContext) -> bool:
        """Allow direct Goal APIs only when no capability transaction owns hands."""

        binding = installed_binding_for_goal(goal, self.profession)
        if binding is None:
            return False
        try:
            return bool(binding.can_yield(ctx))
        except Exception:  # noqa: BLE001 — pre-emption safety fails closed
            return False
