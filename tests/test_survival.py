"""Autonomy slice A1: wounded agents flee danger, then bandage themselves."""

from anima2.contract import (
    ItemView,
    JournalEntry,
    MobileView,
    Observation,
    PlayerView,
    Position,
    SkillView,
    TargetCursor,
    TargetObject,
    Use,
    Walk,
)
from anima2.persona import Persona
from anima2.skills import RecoverDeath, Survive
from anima2.skills.base import SkillContext, Status
from anima2.skills.survival import SKILL_ANATOMY, SKILL_HEALING


def _ctx(
    *, hp=30, hostiles=(), bandages=10, pending=None, journal=(), memory=None,
    poisoned=False, healing=0.0, anatomy=0.0,
):
    player = PlayerView(
        serial=1,
        pos=Position(100, 100, 0),
        hits=hp,
        hits_max=100,
        poisoned=poisoned,
    )
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
        skills=[
            SkillView(SKILL_ANATOMY, anatomy, anatomy, 100.0, 0),
            SkillView(SKILL_HEALING, healing, healing, 100.0, 0),
        ],
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

    finish = JournalEntry(0, "System", "", 0, 0, cliloc=500969)
    healed = skill.step(_ctx(hp=55, memory=memory, journal=[finish]))
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


def test_natural_regeneration_during_apply_does_not_complete_or_restart_bandage():
    memory = {Survive._PHASE: "applying", Survive._HP_BEFORE: 30, Survive._WAIT: 3}
    skill = Survive()
    memory[Survive._LAST_HP] = 30
    ctx = _ctx(hp=31, memory=memory)
    assert skill.can_run(ctx)
    result = skill.step(ctx)
    assert result.status is Status.RUNNING and result.action is None
    assert memory[Survive._PHASE] == "applying"
    assert memory[Survive._LAST_HP] == 31

    # The regen happened well before the server resolved this bandage, so it is
    # not accepted as evidence for the attempt.
    for _ in range(skill.hp_confirmation_ticks + 1):
        assert skill.step(_ctx(hp=31, memory=memory)).status is Status.RUNNING
    finish = JournalEntry(0, "System", "", 0, 0, cliloc=500969)
    assert skill.step(_ctx(hp=31, memory=memory, journal=[finish])).status is Status.RUNNING
    assert memory[Survive._PHASE] == "confirming"
    assert skill.step(_ctx(hp=55, memory=memory)).status is Status.SUCCESS


def test_hp_delta_immediately_before_resolved_journal_is_confirmed():
    memory = {
        Survive._PHASE: "applying",
        Survive._HP_BEFORE: 30,
        Survive._LAST_HP: 30,
        Survive._WAIT: 3,
    }
    skill = Survive()
    assert skill.step(_ctx(hp=55, memory=memory)).status is Status.RUNNING

    finish = JournalEntry(0, "System", "", 0, 0, cliloc=500969)
    result = skill.step(_ctx(hp=55, memory=memory, journal=[finish]))
    assert result.status is Status.SUCCESS


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


def test_healthy_poisoned_agent_with_cure_skills_bandages_immediately():
    ctx = _ctx(hp=100, poisoned=True, healing=60, anatomy=60)
    skill = Survive()
    assert skill.can_run(ctx)
    assert isinstance(skill.step(ctx).action, Use)


def test_poisoned_agent_flees_even_one_hostile_before_cure_attempt():
    ctx = _ctx(
        hp=100,
        poisoned=True,
        healing=60,
        anatomy=60,
        hostiles=[_hostile(10, 101, 100)],
    )
    result = Survive().step(ctx)
    assert isinstance(result.action, Walk) and result.action.run


def test_poison_below_cure_skill_floor_does_not_burn_bandages():
    ctx = _ctx(hp=30, poisoned=True, healing=59.9, anatomy=60)
    skill = Survive()
    assert not skill.can_run(ctx)
    result = skill.step(ctx)
    assert result.status is Status.FAILURE and result.action is None


def test_observed_poison_clear_completes_cure_without_hp_delta():
    memory = {
        Survive._PHASE: "applying",
        Survive._HP_BEFORE: 100,
        Survive._POISON_BEFORE: True,
        Survive._LAST_POISON: True,
        Survive._WAIT: 3,
    }
    skill = Survive()
    finish = JournalEntry(0, "System", "", 0, 0, cliloc=500969)
    ctx = _ctx(
        hp=100,
        poisoned=False,
        healing=60,
        anatomy=60,
        memory=memory,
        journal=[finish],
    )
    assert skill.can_run(ctx)
    assert skill.step(ctx).status is Status.SUCCESS


def test_failed_cure_enters_bounded_retry_cooldown():
    memory = {
        Survive._PHASE: "confirming",
        Survive._HP_BEFORE: 100,
        Survive._POISON_BEFORE: True,
        Survive._WAIT: Survive.hp_confirmation_ticks,
    }
    skill = Survive()
    result = skill.step(
        _ctx(hp=100, poisoned=True, healing=60, anatomy=60, memory=memory)
    )
    assert result.status is Status.FAILURE
    assert memory[skill._CURE_COOLDOWN] == skill.cure_retry_cooldown_ticks
    assert not skill.can_run(
        _ctx(hp=100, poisoned=True, healing=60, anatomy=60, memory=memory)
    )


def test_poison_cursor_timeout_uses_common_retry_cooldown():
    memory = {
        Survive._PHASE: "cursor",
        Survive._HP_BEFORE: 100,
        Survive._POISON_BEFORE: True,
        Survive._BANDAGE_SERIAL: 3,
        Survive._WAIT: Survive.cursor_timeout_ticks,
    }
    result = Survive().step(
        _ctx(hp=100, poisoned=True, healing=60, anatomy=60, memory=memory)
    )
    assert result.status is Status.FAILURE
    assert memory[Survive._CURE_COOLDOWN] == Survive.cure_retry_cooldown_ticks


def test_poison_refused_and_apply_timeout_use_common_retry_cooldown():
    refused = JournalEntry(0, "System", "", 0, 0, cliloc=500955)
    cases = (
        ({Survive._PHASE: "applying"}, [refused]),
        (
            {
                Survive._PHASE: "applying",
                Survive._WAIT: Survive.apply_timeout_ticks,
            },
            [],
        ),
    )
    for state, journal in cases:
        memory = {
            Survive._HP_BEFORE: 100,
            Survive._POISON_BEFORE: True,
            **state,
        }
        result = Survive().step(
            _ctx(
                hp=100,
                poisoned=True,
                healing=60,
                anatomy=60,
                memory=memory,
                journal=journal,
            )
        )
        assert result.status is Status.FAILURE
        assert memory[Survive._CURE_COOLDOWN] == Survive.cure_retry_cooldown_ticks


def test_poison_incompatible_cursor_uses_common_retry_cooldown():
    memory = {
        Survive._PHASE: "cursor",
        Survive._HP_BEFORE: 100,
        Survive._POISON_BEFORE: True,
        Survive._BANDAGE_SERIAL: 3,
    }
    delayed = TargetCursor(target_type=1, cursor_id=99, cursor_flag=0)
    result = Survive().step(
        _ctx(
            hp=100,
            poisoned=True,
            healing=60,
            anatomy=60,
            memory=memory,
            pending=delayed,
        )
    )
    assert result.status is Status.FAILURE
    assert memory[Survive._CURE_COOLDOWN] == Survive.cure_retry_cooldown_ticks


def test_explicit_cure_failure_beats_earlier_finish_line_in_same_observation():
    memory = {
        Survive._PHASE: "applying",
        Survive._HP_BEFORE: 100,
        Survive._POISON_BEFORE: True,
    }
    finish = JournalEntry(0, "System", "", 0, 0, cliloc=500969)
    failed = JournalEntry(0, "System", "", 0, 0, cliloc=1010060)
    result = Survive().step(
        _ctx(
            hp=100,
            poisoned=True,
            healing=60,
            anatomy=60,
            memory=memory,
            journal=[finish, failed],
        )
    )
    assert result.status is Status.FAILURE
    assert memory[Survive._CURE_COOLDOWN] == Survive.cure_retry_cooldown_ticks


def test_death_clearing_poison_is_not_misrecorded_as_a_cure():
    memory = {
        Survive._PHASE: "applying",
        Survive._HP_BEFORE: 30,
        Survive._POISON_BEFORE: True,
    }
    skill = Survive()
    ctx = _ctx(hp=0, poisoned=False, memory=memory)
    ctx.obs.player.dead = True
    assert not skill.can_run(ctx)
    assert skill._PHASE not in memory
    assert skill.step(ctx).status is Status.FAILURE


def test_post_resurrection_route_stop_precedes_low_hp_bandaging():
    memory = {RecoverDeath._WAITING: True}
    skill = Survive()
    assert not skill.can_run(_ctx(hp=10, memory=memory))


# --- fleeing to a tile that is actually free (2026-08-24) ----------------------------


def _flee_hostile(serial, x, y):
    from anima2.contract import MobileView, Position
    return MobileView(serial=serial, name="Ettin", body=1, notoriety=6,
                      hits=100, hits_max=100, distance=1, pos=Position(x, y, 0))


def _flee_dir(px, py, hostiles):
    """The direction `Survive` picks to run, given who is standing where."""
    from anima2.contract import Observation, PlayerView, Position
    from anima2.skills.survival import Survive

    obs = Observation(player=PlayerView(serial=0x1, pos=Position(px, py, 0),
                                        hits=40, hits_max=150),
                      mobiles=hostiles)
    ctx = SkillContext(obs=obs, persona=Persona(name="Bram"), memory={})
    return Survive._away_direction(ctx, hostiles)


def test_a_cornered_warrior_steps_somewhere_free_not_into_a_body():
    """The village spawns its pinned prey on the four CARDINAL neighbours, so the obvious
    escape squares are exactly the occupied ones.

    With hostiles east and west the away-vector cancels and the old code committed NORTH
    unconditionally — one of those spawn tiles. Measured live: the warrior walked into the
    same blocked tile for its whole flee budget, never left its spawn square across 203
    samples, and died bandaging inside melee, where ServUO slips every heal to nothing.
    """
    from anima2.geometry import DIRECTION_DELTAS

    # East + west cancel; north is ALSO occupied. It must still find a way out.
    hostiles = [_flee_hostile(0xA, 2588, 408), _flee_hostile(0xB, 2586, 408)]
    blocked = hostiles + [_flee_hostile(0xC, 2587, 407)]
    d = _flee_dir(2587, 408, blocked)
    dx, dy = DIRECTION_DELTAS[d]
    dest = (2587 + dx, 408 + dy)
    assert dest not in {(m.pos.x, m.pos.y) for m in blocked}, (d, dest)


def test_the_flee_still_prefers_running_away_when_it_can():
    """The fan-out is a fallback, not a shuffle: with the direct escape clear, take it."""
    from anima2.geometry import DIRECTION_DELTAS

    # One hostile due south -> the ideal heading is due north, and north is free.
    d = _flee_dir(2587, 408, [_flee_hostile(0xA, 2587, 409)])
    assert DIRECTION_DELTAS[d] == (0, -1), DIRECTION_DELTAS[d]


def test_a_warrior_boxed_in_on_all_eight_sides_still_answers():
    """Surrounded is a real state and must not raise or loop — the give-up ladder owns it."""
    from anima2.geometry import DIRECTION_DELTAS

    ring = [_flee_hostile(0xA0 + i, 2587 + dx, 408 + dy)
            for i, (dx, dy) in enumerate(DIRECTION_DELTAS)]
    d = _flee_dir(2587, 408, ring)
    assert 0 <= d < len(DIRECTION_DELTAS)
