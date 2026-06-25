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
    rat = MobileView(0xAA, "rat", Position(102, 100, 0), body=0x10, notoriety=6, hits=10,
                     hits_max=10, distance=2)
    ctx = _ctx(_obs([rat]), Persona(name="Ash", combat_disposition="aggressive"))
    skill = Combat()
    assert skill.can_run(ctx)
    first = skill.step(ctx)
    assert isinstance(first.action, WarMode) and first.action.on is True
    second = skill.step(ctx)
    assert isinstance(second.action, Attack) and second.action.serial == 0xAA


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
