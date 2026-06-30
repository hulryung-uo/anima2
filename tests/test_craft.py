"""The Blacksmith skill drives the craft gump with the right button sequence."""

from anima2.contract import GumpResponse, GumpView, ItemView, Observation, PlayerView, Position, Use
from anima2.persona import Persona
from anima2.skills import Blacksmith
from anima2.skills.base import SkillContext
from anima2.skills.craft import CATEGORY_BTN, DAGGER_BTN, MAKE_LAST_BTN

HAMMER = 0x13E3


def _tool():
    return ItemView(serial=0x40, graphic=HAMMER, amount=1, pos=Position(),
                    container=0x99, layer=0, distance=0)


def _ctx(gumps=(), state=None):
    mem = {} if state is None else {"bs_state": state}
    obs = Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)),
                      items=[_tool()], gumps=list(gumps))
    return SkillContext(obs=obs, persona=Persona(name="T"), memory=mem)


def test_button_ids_match_servuo_formula():
    assert (CATEGORY_BTN, DAGGER_BTN, MAKE_LAST_BTN) == (22, 30, 21)


def test_no_tool_means_cannot_smith():
    obs = Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)))
    assert not Blacksmith().can_run(SkillContext(obs=obs, persona=Persona(name="T"), memory={}))


def test_opens_gump_with_the_tool():
    res = Blacksmith().step(_ctx())  # no gump, fresh → use the hammer
    assert isinstance(res.action, Use) and res.action.serial == 0x40


def test_gump_button_sequence():
    g = GumpView(serial=0xAB, gump_id=0xCD, layout="")
    # category → item → make-last loop
    r1 = Blacksmith().step(_ctx(gumps=[g], state="category"))
    assert isinstance(r1.action, GumpResponse) and r1.action.button == CATEGORY_BTN
    r2 = Blacksmith().step(_ctx(gumps=[g], state="item"))
    assert r2.action.button == DAGGER_BTN
    r3 = Blacksmith().step(_ctx(gumps=[g], state="loop"))
    assert r3.action.button == MAKE_LAST_BTN
    assert r3.action.serial == 0xAB and r3.action.gump_id == 0xCD
