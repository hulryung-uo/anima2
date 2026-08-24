"""Combat and Greet skills produce the right contract actions."""

from anima2.contract import Attack, MobileView, Position, Say, WarMode
from anima2.persona import Persona
from anima2.skills import Combat, Greet
from anima2.skills.base import SkillContext, Status

from anima2.contract import Observation, PlayerView


def _obs(mobiles: list[MobileView]) -> Observation:
    return Observation(player=PlayerView(serial=1, pos=Position(100, 100, 0)), mobiles=mobiles)


def _ctx(obs: Observation, persona: Persona) -> SkillContext:
    return SkillContext(obs=obs, persona=persona, memory={})


def test_combat_wars_then_attacks_hostile():
    # IN REACH (distance 1). At 2 the skill now closes first — see
    # `test_combat_walks_into_reach_instead_of_swinging_at_nothing`.
    rat = MobileView(0xAA, "rat", Position(101, 100, 0), body=0x10, notoriety=6, hits=10,
                     hits_max=10, distance=1)
    ctx = _ctx(_obs([rat]), Persona(name="Ash", combat_disposition="aggressive"))
    skill = Combat()
    assert skill.can_run(ctx)
    first = skill.step(ctx)
    assert isinstance(first.action, WarMode) and first.action.on is True
    second = skill.step(ctx)
    assert isinstance(second.action, Attack) and second.action.serial == 0xAA


def test_combat_walks_into_reach_instead_of_swinging_at_nothing():
    """The server does not walk us into range, so an `Attack` from two tiles away is a
    packet that does nothing — forever.

    Measured 2026-08-24 (audit §59): a warrior one tile off its stand spent
    `act=Attackx544` — five hundred and forty-four consecutive ticks — swinging at prey
    pinned at `foes=d2`, with `steps=8` for the whole day and `!stalled` up. It had only
    ever worked because the village spawns prey ADJACENT.
    """
    rat = MobileView(0xAA, "rat", Position(103, 100, 0), body=0x10, notoriety=6, hits=10,
                     hits_max=10, distance=3)
    ctx = _ctx(_obs([rat]), Persona(name="Ash", combat_disposition="aggressive"))
    skill = Combat()
    assert isinstance(skill.step(ctx).action, WarMode)

    # It closes, and it closes TOWARD the target (east, +x).
    from anima2.contract import Walk
    from anima2.geometry import DIRECTION_DELTAS
    step = skill.step(ctx)
    assert isinstance(step.action, Walk), step.action
    assert DIRECTION_DELTAS[step.action.dir] == (1, 0), DIRECTION_DELTAS[step.action.dir]

    # A BLOCKED approach is bounded, or a walled-off target becomes an infinite loop.
    # The observation never changes, so the position never changes.
    walks = 1 + sum(isinstance(skill.step(ctx).action, Walk) for _ in range(20))
    assert walks == skill.approach_stall_limit, walks
    assert isinstance(skill.step(ctx).action, Attack), "and then it swings anyway"

    # A BLOCKED APPROACH IS RETRIED, or the budget is a LIFETIME cap: it resets only on
    # arriving or on moving, and a warrior that can do neither never walks again.
    # Measured 2026-08-25 on a three-warrior roster (audit §64): two of three sat at
    # `foes=d2,d2,d2` with `act=Attackx547` and `!stalled` for a whole run, while the
    # third — whose first approach happened to land — banked normally.
    swings = 0
    while not isinstance(skill.step(ctx).action, Walk):
        swings += 1
        assert swings <= skill.approach_retry_ticks + 2, "it never tried again"
    assert swings > skill.approach_stall_limit, (
        f"it retried after only {swings} swings — that is the budget cycling, not a "
        f"cooldown, and a permanently walled-off target becomes a walk every other tick")

    # A NEW TARGET gets a fresh budget — the old one's wall says nothing about this one.
    other = MobileView(0xBB, "rat", Position(103, 100, 0), body=0x10, notoriety=6, hits=10,
                       hits_max=10, distance=3)
    for _ in range(skill.approach_stall_limit):
        skill.step(ctx)                       # spend it on 0xAA
    ctx_other = SkillContext(obs=_obs([other]), persona=ctx.persona, memory=ctx.memory)
    assert isinstance(skill.step(ctx_other).action, Walk), "a new target, a new approach"

    # In reach, it never walks — and arriving RESETS the budget, so a target that runs
    # off again is chased afresh instead of inheriting a spent counter. One tick of the
    # new chase is charged immediately, because we are standing where we already stood
    # and that is exactly what the counter measures.
    close = MobileView(0xAA, "rat", Position(101, 100, 0), body=0x10, notoriety=6, hits=10,
                       hits_max=10, distance=1)
    ctx_close = SkillContext(obs=_obs([close]), persona=ctx.persona, memory=ctx.memory)
    assert isinstance(skill.step(ctx_close).action, Attack)
    assert sum(isinstance(skill.step(ctx).action, Walk)
               for _ in range(20)) == skill.approach_stall_limit - 1


def test_pacifist_never_fights():
    rat = MobileView(0xAA, "rat", Position(101, 100, 0), body=0x10, notoriety=6, hits=10,
                     hits_max=10, distance=1)
    ctx = _ctx(_obs([rat]), Persona(name="Grimm", combat_disposition="pacifist"))
    assert not Combat().can_run(ctx)


def test_combat_ignores_innocents():
    blue = MobileView(0xBB, "townsfolk", Position(101, 100, 0), body=0x190, notoriety=1,
                      hits=50, hits_max=50, distance=1)
    ctx = _ctx(_obs([blue]), Persona(name="Ash", combat_disposition="aggressive"))
    assert not Combat().can_run(ctx)


def test_combat_ignores_observably_dead_hostile():
    corpse_lag = MobileView(0xBC, "rat", Position(101, 100, 0), body=0x10, notoriety=6,
                            hits=0, hits_max=10, distance=1)
    ctx = _ctx(_obs([corpse_lag]), Persona(name="Ash", combat_disposition="aggressive"))
    assert not Combat().can_run(ctx)


def test_greet_says_hello_once_per_person():
    human = MobileView(0xCC, "Bob", Position(102, 100, 0), body=0x190, notoriety=1,
                       hits=50, hits_max=50, distance=2)
    ctx = _ctx(_obs([human]), Persona(name="Sera", talkativeness=0.5))
    skill = Greet()
    assert skill.can_run(ctx)
    res = skill.step(ctx)
    assert isinstance(res.action, Say) and "Sera" in res.action.text
    assert res.status is Status.SUCCESS
    # Already greeted → no longer applicable.
    assert not skill.can_run(ctx)


def test_silent_persona_does_not_greet():
    human = MobileView(0xCC, "Bob", Position(101, 100, 0), body=0x190, notoriety=1,
                       hits=50, hits_max=50, distance=1)
    ctx = _ctx(_obs([human]), Persona(name="Shade", talkativeness=0.0))
    assert not Greet().can_run(ctx)
