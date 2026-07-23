"""Brick 6 board-delivery pair (deliver_boards + fetch_boards) — the goal-scoped
drop/pickup mechanics + readiness gates, on hand-built observations.

Both are thin, board-typed, goal-scoped analogs of proven primitives
(`MineSmeltDeliver._deliver_step` and `Blacksmith._fetch_step`), so these tests
target what is BRICK-6-specific: DeliverBoards lifts a pack pile and Drops it on
the GROUND (container=0xFFFFFFFF) at the drop point then finishes when the pack
empties; FetchBoards lifts a nearby GROUND pile (container is None) and Drops it
INTO the pack (container=backpack) then finishes when none remain; plus the
readiness gates and the carpenter's fetch-before-buy registry preference.
"""

from anima2.capabilities import CAPABILITIES
from anima2.contract import Drop, ItemView, Observation, PickUp, PlayerView, Position
from anima2.persona import Persona
from anima2.skills.base import SkillContext
from anima2.skills.carpentry import FetchBoards
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.woodwork import BOARD_GRAPHIC, DeliverBoards

PLAYER = 1
BACKPACK = 0x50
DROP = (5, 5)


def _item(serial, graphic, *, container=BACKPACK, amount=1, layer=0, distance=0, pos=None):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=pos or Position(),
                    container=container, layer=layer, distance=distance)


def _backpack():
    return _item(BACKPACK, 0x0E75, container=PLAYER, layer=BACKPACK_LAYER)


def _ctx(items, *, memory, goal_id=71, pos=Position(5, 5, 0)):
    obs = Observation(player=PlayerView(serial=PLAYER, pos=pos), items=list(items))
    return SkillContext(obs=obs, persona=Persona(name="Bjorn"), memory=memory, goal_id=goal_id)


# --- config / registry ---------------------------------------------------------


def test_deliver_config_and_fetch_gate():
    assert DeliverBoards.deliver_threshold == 19  # one throne's worth per haul
    from anima2.capabilities import _FETCH_BOARDS_THRESHOLD
    assert _FETCH_BOARDS_THRESHOLD == 19          # fetch only below a craftable stock


def test_carpenter_prefers_free_fetch_over_paid_buy():
    # fetch_boards must come BEFORE buy_boards in the carpenter's registry order so
    # the deterministic cognition picks the FREE delivered boards over buying them.
    order = [c for (p, c) in CAPABILITIES if p == "carpenter"]
    assert order.index("fetch_boards") < order.index("buy_boards")


def test_deliver_and_fetch_are_registered_to_the_right_professions():
    assert ("lumberjack", "deliver_boards") in CAPABILITIES
    assert ("carpenter", "fetch_boards") in CAPABILITIES


# --- DeliverBoards: PickUp a pack pile -> Drop it on the GROUND at the drop point


def test_deliver_boards_drops_pack_pile_on_the_ground_then_finishes():
    skill = DeliverBoards()
    mem = {"carpenter_drop": DROP, "lumber_home": DROP}  # at the drop point already
    actions = []

    # Tick 1: begin (freeze 19) + at drop point + nothing held -> lift the pile.
    r1 = skill.step(_ctx([_backpack(), _item(0x700, BOARD_GRAPHIC, amount=19)], memory=mem))
    actions.append(r1.action)
    assert mem["cap_deliver_needed"] == 19
    assert isinstance(r1.action, PickUp) and r1.action.serial == 0x700

    # Tick 2: the pile is on the cursor (gone from pack) -> Drop it on the GROUND.
    r2 = skill.step(_ctx([_backpack()], memory=mem))
    actions.append(r2.action)
    assert isinstance(r2.action, Drop)
    assert r2.action.container == 0xFFFFFFFF          # ground drop, not into a pack
    assert (r2.action.x, r2.action.y) == DROP

    # Tick 3: pack empty, home == drop point -> haul delivered, goal finished.
    r3 = skill.step(_ctx([_backpack()], memory=mem))
    actions.append(r3.action)
    assert mem["cap_deliver_finished_goal_id"] == 71
    assert mem["cap_deliver_delivered"] == 19
    assert mem["cap_deliver_boards_remaining"] == 0
    assert r3.action is None


def test_deliver_ready_needs_boards_and_a_drop_point():
    skill = DeliverBoards()  # noqa: F841 (readiness reads memory/obs, not the skill)
    from anima2.capabilities import _deliver_ready
    full = [_backpack(), _item(0x700, BOARD_GRAPHIC, amount=19)]
    # Boards + drop point -> ready.
    assert _deliver_ready(_ctx(full, memory={"carpenter_drop": DROP}, goal_id=None))
    # No drop point -> not ready (it would have nowhere to deliver).
    assert not _deliver_ready(_ctx(full, memory={}, goal_id=None))
    # Below one throne's worth -> not ready.
    assert not _deliver_ready(_ctx([_backpack(), _item(0x700, BOARD_GRAPHIC, amount=18)],
                                   memory={"carpenter_drop": DROP}, goal_id=None))


# --- FetchBoards: PickUp a nearby GROUND pile -> Drop it INTO the pack ----------


def test_fetch_boards_lifts_ground_pile_into_the_pack_then_finishes():
    skill = FetchBoards()
    mem: dict = {}
    ground = _item(0x800, BOARD_GRAPHIC, container=None, amount=19, distance=1, pos=Position(5, 5, 0))
    actions = []

    # Tick 1: begin (a ground pile is nearby, pack has 0 boards) -> lift it.
    r1 = skill.step(_ctx([_backpack(), ground], memory=mem))
    actions.append(r1.action)
    assert mem["cap_fetch_start_boards"] == 0
    assert isinstance(r1.action, PickUp) and r1.action.serial == 0x800

    # Tick 2: the pile is on the cursor -> Drop it INTO the pack (container=backpack).
    r2 = skill.step(_ctx([_backpack()], memory=mem))
    actions.append(r2.action)
    assert isinstance(r2.action, Drop) and r2.action.container == BACKPACK

    # Tick 3: the boards are now in the pack, no ground pile remains -> finished.
    r3 = skill.step(_ctx([_backpack(), _item(0x800, BOARD_GRAPHIC, amount=19)], memory=mem))
    actions.append(r3.action)
    assert mem["cap_fetch_finished_goal_id"] == 71
    assert mem["cap_fetch_fetched"] == 19
    assert mem["cap_fetch_ground_remaining"] == 0
    assert r3.action is None


def test_fetch_ready_needs_ground_boards_and_a_starved_pack():
    from anima2.capabilities import _fetch_ready
    ground = _item(0x800, BOARD_GRAPHIC, container=None, amount=19, distance=1, pos=Position(5, 5, 0))
    # Ground boards + pack below a throne's worth -> ready.
    assert _fetch_ready(_ctx([_backpack(), ground], memory={}, goal_id=None))
    # No ground boards -> not ready.
    assert not _fetch_ready(_ctx([_backpack()], memory={}, goal_id=None))
    # Pack already has a craftable stock (>=19) -> not ready (don't over-fetch).
    assert not _fetch_ready(_ctx([_backpack(), ground, _item(0x900, BOARD_GRAPHIC, amount=19)],
                                 memory={}, goal_id=None))
