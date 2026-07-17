"""Autonomy slice A1: wounded agents flee danger, then bandage themselves."""

from anima2.contract import (
    ItemView,
    JournalEntry,
    MobileView,
    Observation,
    PlayerView,
    Position,
    TargetCursor,
    TargetObject,
    Use,
    Walk,
)
from anima2.persona import Persona
from anima2.skills import Survive
from anima2.skills.base import SkillContext, Status


def _ctx(*, hp=30, hostiles=(), bandages=10, pending=None, journal=(), memory=None):
    player = PlayerView(serial=1, pos=Position(100, 100, 0), hits=hp, hits_max=100)
    backpack = ItemView(2, 0x0E75, 1, player.pos, player.serial, 0x15, 0)
    items = [backpack]
    if bandages:
        items.append(ItemView(3, 0x0E21, bandages, player.pos, backpack.serial, 0, 0))
    obs = Observation(
        player=player,
        mobiles=list(hostiles),
        items=items,
        pending_target=pending,
        new_journal=list(journal),
    )
    return SkillContext(
        obs=obs,
        persona=Persona(name="Ragnar", combat_disposition="aggressive"),
        memory=memory if memory is not None else {},
    )


def _hostile(serial, x, y, *, hits=10):
    return MobileView(serial, "mongbat", Position(x, y, 0), 0x27, 6, hits, 10, 1)


def test_survive_is_inert_when_healthy():
    ctx = _ctx(hp=80, hostiles=[_hostile(10, 101, 100)])
    skill = Survive()
    assert not skill.can_run(ctx)
    result = skill.step(ctx)  # defensive even when misconfigured as planner fallback
    assert result.status is Status.FAILURE and result.action is None


def test_wounded_agent_runs_away_from_hostile_centroid_before_bandaging():
    hostiles = [_hostile(10, 101, 99), _hostile(11, 101, 100), _hostile(12, 101, 101)]
    ctx = _ctx(hostiles=hostiles)
    result = Survive().step(ctx)
    assert isinstance(result.action, Walk)
    assert result.action.dir == 6  # hostile centroid east -> flee west
    assert result.action.run is True


def test_centroid_tie_flees_north_instead_of_standing_still():
    hostiles = [_hostile(10, 99, 100), _hostile(11, 101, 100), _hostile(12, 100, 100)]
    result = Survive().step(_ctx(hostiles=hostiles))
    assert isinstance(result.action, Walk) and result.action.dir == 0


def test_fractional_centroid_uses_away_sign_instead_of_rounding_to_north():
    hostiles = [_hostile(10, 101, 99), _hostile(11, 100, 100), _hostile(12, 100, 101)]
    result = Survive().step(_ctx(hostiles=hostiles))
    assert isinstance(result.action, Walk) and result.action.dir == 6


def test_flee_is_bounded_then_bandage_starts():
    hostiles = [_hostile(10, 101, 99), _hostile(11, 101, 100), _hostile(12, 101, 101)]
    ctx = _ctx(hostiles=hostiles)
    skill = Survive()
    for _ in range(skill.max_flee_steps):
        assert isinstance(skill.step(ctx).action, Walk)
    result = skill.step(ctx)
    assert isinstance(result.action, Use) and result.action.serial == 3


def test_bandage_targets_self_once_and_waits_for_confirmed_heal():
    memory = {}
    skill = Survive()
    first = skill.step(_ctx(memory=memory))
    assert isinstance(first.action, Use) and first.action.serial == 3

    cursor = TargetCursor(target_type=0, cursor_id=7, cursor_flag=2)
    second = skill.step(_ctx(memory=memory, pending=cursor))
    assert isinstance(second.action, TargetObject) and second.action.serial == 1

    waiting = skill.step(_ctx(memory=memory))
    assert waiting.status is Status.RUNNING and waiting.action is None
    assert memory[skill._PHASE] == "applying"  # no repeated Use while the bandage resolves

    healed = skill.step(_ctx(hp=55, memory=memory))
    assert healed.status is Status.SUCCESS and healed.action is None
    assert skill._PHASE not in memory


def test_bandage_finish_without_hp_delta_waits_then_fails_instead_of_false_success():
    memory = {Survive._PHASE: "applying", Survive._HP_BEFORE: 30, Survive._WAIT: 3}
    finish = JournalEntry(0, "System", "", 0, 0, cliloc=500969)
    skill = Survive()
    result = skill.step(_ctx(memory=memory, journal=[finish]))
    assert result.status is Status.RUNNING
    for _ in range(skill.hp_confirmation_ticks):
        assert skill.step(_ctx(memory=memory)).status is Status.RUNNING
    result = skill.step(_ctx(memory=memory))
    assert result.status is Status.FAILURE
    assert Survive._PHASE not in memory


def test_barely_help_without_hp_delta_is_a_failed_attempt():
    memory = {Survive._PHASE: "applying", Survive._HP_BEFORE: 30, Survive._WAIT: 3}
    resolved = JournalEntry(0, "System", "", 0, 0, cliloc=500968)
    skill = Survive()
    result = skill.step(_ctx(memory=memory, journal=[resolved]))
    assert result.status is Status.RUNNING
    for _ in range(skill.hp_confirmation_ticks + 1):
        result = skill.step(_ctx(memory=memory))
    assert result.status is Status.FAILURE
    assert Survive._PHASE not in memory


def test_hp_update_after_finish_journal_records_real_success():
    memory = {Survive._PHASE: "applying", Survive._HP_BEFORE: 30, Survive._WAIT: 3}
    finish = JournalEntry(0, "System", "", 0, 0, cliloc=500969)
    skill = Survive()
    assert skill.step(_ctx(memory=memory, journal=[finish])).status is Status.RUNNING
    assert skill.step(_ctx(hp=55, memory=memory)).status is Status.SUCCESS


def test_death_aborts_inflight_bandage_immediately():
    memory = {Survive._PHASE: "applying", Survive._HP_BEFORE: 30, Survive._WAIT: 3}
    skill = Survive()
    assert not skill.can_run(_ctx(hp=0, memory=memory))
    assert skill._PHASE not in memory


def test_external_recovery_completes_inflight_bandage():
    memory = {Survive._PHASE: "applying", Survive._HP_BEFORE: 30, Survive._WAIT: 3}
    skill = Survive()
    ctx = _ctx(hp=80, memory=memory)
    assert skill.can_run(ctx)
    assert skill.step(ctx).status is Status.SUCCESS


def test_unrelated_open_cursor_is_not_hijacked():
    cursor = TargetCursor(target_type=1, cursor_id=99, cursor_flag=0)
    assert not Survive().can_run(_ctx(pending=cursor))


def test_without_bandages_even_one_hostile_triggers_flee():
    ctx = _ctx(hostiles=[_hostile(10, 101, 100)], bandages=0)
    assert Survive().can_run(ctx)
    result = Survive().step(ctx)
    assert isinstance(result.action, Walk) and result.action.run


def test_without_bandages_flee_attempts_are_bounded_and_then_yield():
    ctx = _ctx(hostiles=[_hostile(10, 101, 100)], bandages=0)
    skill = Survive()
    for _ in range(skill.max_flee_steps):
        assert skill.can_run(ctx)
        assert isinstance(skill.step(ctx).action, Walk)
    assert not skill.can_run(ctx)


def test_flee_budget_resets_after_recovery():
    memory = {Survive._FLEE_STEPS: Survive.max_flee_steps}
    skill = Survive()
    assert not skill.can_run(_ctx(hp=80, memory=memory))
    assert memory[skill._FLEE_STEPS] == 0


def test_delayed_incompatible_cursor_is_left_for_its_owner():
    memory = {}
    skill = Survive()
    assert isinstance(skill.step(_ctx(memory=memory)).action, Use)

    delayed = TargetCursor(target_type=1, cursor_id=99, cursor_flag=0)
    result = skill.step(_ctx(memory=memory, pending=delayed))
    assert result.status is Status.FAILURE and result.action is None
    assert skill._PHASE not in memory


def test_observably_dead_mobile_is_not_a_survival_threat():
    dead = _hostile(10, 101, 100, hits=0)
    assert not Survive().can_run(_ctx(hostiles=[dead], bandages=0))
