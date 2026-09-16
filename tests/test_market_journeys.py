"""Whole transactions at the forge wedge and relocated grove from audit 70/72."""

import pytest

from anima2.contract import ItemView, MobileView, PlayerView, Position, Walk
from anima2.goals import GoalOutcome
from anima2.mock_body import MockBody, MockVendor
from anima2.persona import Persona
from anima2.skills.base import SkillContext
from anima2.skills.harvest import BACKPACK_LAYER, Chop
from anima2.skills.market import BlacksmithMarket, walk_readout
from anima2.skills.tinkering import TINKERTOOLS_GRAPHICS, SellTongs
from anima2.skills.woodwork import AXE_GRAPHICS, BOARD_GRAPHIC
from anima2.tinker_life import TinkerLife
from anima2.woodsman_life import WoodsmanLife


def _seller(cls, home, vendor, graphic, amount, tool):
    body = MockBody(player=PlayerView(serial=1, name="Worker", pos=Position(*home, 0),
                                      hits=80, hits_max=80, body=0x190))
    for serial, art, count, owner, layer in [
        (0x50, 0x0E75, 1, 1, BACKPACK_LAYER),
        (0x51, tool, 1, 0x50, 0),
        (0x52, graphic, amount, 0x50, 0),
    ]:
        body.items[serial] = ItemView(serial=serial, graphic=art, amount=count,
                                      pos=Position(), container=owner, layer=layer, distance=0)
    body.mobiles[0xA0] = MobileView(serial=0xA0, name="Vendor", pos=Position(*vendor, 0),
                                    body=0x190, notoriety=1, hits=50, hits_max=50, distance=0)
    body.vendors[0xA0] = MockVendor(serial=0xA0, buys={graphic: 5})
    life = cls(body, Persona(name="Worker"),
               routes={"vendor_spot": (vendor,), "shop_serials": {vendor: 0xA0}})
    life.set_leash(home, 0)
    return body, life


def _first_sale(life, capability, budget=180):
    for _ in range(budget):
        life.tick()
        for frame in life.econ_agent.goal_stack.history:
            if frame.goal.params.get("capability") == capability:
                assert frame.outcome is GoalOutcome.SUCCESS
                return frame
    pytest.fail("no sale transaction retired")


@pytest.mark.parametrize("turn_before_move", [False, True])
def test_the_forge_seller_detours_around_the_pinned_banker_and_returns(turn_before_move):
    home, vendor, banker = (2610, 476), (2610, 473), (2610, 475)
    body, life = _seller(TinkerLife, home, vendor, SellTongs.sold_graphic, 8,
                         min(TINKERTOOLS_GRAPHICS))
    body.blocked.add(banker)
    body.turn_before_move = turn_before_move
    # The recorded trigger is an economy-to-economy transition after banking,
    # not a fresh login's six hunt ticks (which may wander away from the blocker).
    life.mode, life.target_cap = "economy", "sell_tongs"
    life.candidates = ["sell_tongs"]
    life._econ_streak = life.econ_grace
    frame = _first_sale(life, "sell_tongs")
    assert frame.finished_tick - frame.created_tick < 45
    assert (body.player.pos.x, body.player.pos.y) == home
    assert 0x52 not in body.items
    assert sum(i.amount for i in body.items.values() if i.graphic == 0x0EED) == 40
    walks = [a.dir for a in body.actions if isinstance(a, Walk)]
    assert walks[0] == 0 and walks[1] != 0


@pytest.mark.parametrize("turn_before_move", [False, True])
def test_a_relocated_woodsman_sells_and_returns_to_the_new_grove(turn_before_move):
    old_home, new_home, vendor = (518, 1042), (512, 1045), (519, 1042)
    body, life = _seller(WoodsmanLife, old_home, vendor, BOARD_GRAPHIC, 30,
                         min(AXE_GRAPHICS))
    life.econ_agent.memory["bs_stand"] = old_home
    body.turn_before_move = turn_before_move
    body.player.pos = Position(*new_home, 0)
    # Publish the same arrival event the real Chop relocation FSM emits. No shops
    # or routes are restaged; this is the boundary that previously had no consumer.
    life.memory["harvest_relocate_target"] = new_home
    Chop._clear_relocate(SkillContext(body.observe(), life.persona, memory=life.memory),
                         reached_stand=True)
    body.blocked.add((513, 1044))  # a refused direct approach on the longer journey
    frame = _first_sale(life, "sell_boards")
    assert frame.finished_tick - frame.created_tick < 70
    assert life.econ_agent.memory["bs_stand"] == new_home
    assert life.memory["wander_home"] == life.econ_agent.memory["wander_home"] == new_home
    assert (body.player.pos.x, body.player.pos.y) == new_home
    assert body.mobiles[0xA0].pos == Position(*vendor, 0)


def test_a_failed_relocation_does_not_move_the_workplace():
    home = (518, 1042)
    body, life = _seller(WoodsmanLife, home, (519, 1042), BOARD_GRAPHIC, 30,
                         min(AXE_GRAPHICS))
    body.player.pos = Position(512, 1045, 0)
    life.memory["harvest_relocate_target"] = (500, 1030)
    Chop._clear_relocate(SkillContext(body.observe(), life.persona, memory=life.memory))
    life.tick()
    assert life.memory["wander_home"] == home
    assert "workplace" not in life.econ_agent.memory


def test_a_walk_that_moves_without_approaching_is_bounded():
    skill = BlacksmithMarket()
    memory = {"mkt_phase": "sell", "vendor_spot": (10, 10)}
    result = None
    for tick in range(skill.drift_limit + 1):
        pos = Position(tick % 2, 0, 0)
        body = MockBody(player=PlayerView(serial=1, pos=pos))
        ctx = SkillContext(body.observe(), Persona(name="Worker"), memory=memory)
        result = skill._walk_route(ctx, [(10, 10)], "sell", 2)
        if tick < skill.drift_limit:
            assert result is not None and isinstance(result.action, Walk)
        if tick == skill.drift_limit - 1:
            assert "drift=23/24" in walk_readout(memory, pos)
    assert result is None
    assert "market_walk" not in memory


def test_a_new_goal_does_not_inherit_a_previous_journeys_drift():
    skill = BlacksmithMarket()
    memory = {"market_walk": {"identity": ("sell", (10, 10), 1),
                               "best": 1, "drift": 23}}
    body = MockBody()
    ctx = SkillContext(body.observe(), Persona(name="Worker"), memory=memory, goal_id=2)
    result = skill._walk_route(ctx, [(10, 10)], "sell", 2)
    assert result is not None
    assert memory["market_walk"]["drift"] == 0
