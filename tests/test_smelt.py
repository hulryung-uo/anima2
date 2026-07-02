"""The MineAndSmelt skill's decision logic, exercised with hand-built observations."""

from anima2.contract import (
    ItemView,
    Observation,
    PlayerView,
    Position,
    TargetCursor,
    TargetGround,
    TargetObject,
    Use,
)
from anima2.persona import Persona
from anima2.skills import MineAndSmelt
from anima2.skills.base import SkillContext
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.smelt import FORGE_GRAPHICS, INGOT_GRAPHICS, ORE_GRAPHICS, SMALL_ORE_GRAPHIC

PICKAXE = 0x0E86
BACKPACK = 0x40001453
FORGE_SERIAL = 0x900
PICKAXE_SERIAL = 0x300
ORE_GRAPHIC = 0x19B9
INGOT_GRAPHIC = 0x1BF2


def _item(serial, graphic, *, layer=0, container=None, amount=1, distance=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=distance)


def _backpack():
    # container=1 — a worn item's container is its wearer (here, the player, serial 1).
    return _item(BACKPACK, 0x0E75, layer=BACKPACK_LAYER, container=1)


def _pickaxe():
    return _item(PICKAXE_SERIAL, PICKAXE, container=BACKPACK)


def _ore(serial, amount=1):
    return _item(serial, ORE_GRAPHIC, container=BACKPACK, amount=amount)


def _forge(distance=1):
    return _item(FORGE_SERIAL, 0x0FB1, distance=distance)


def _ctx(items, pending=None, memory=None):
    obs = Observation(player=PlayerView(serial=1, pos=Position(100, 100, 0)),
                      items=list(items), pending_target=pending)
    return SkillContext(obs=obs, persona=Persona(name="Grimm"),
                        memory=memory if memory is not None else {})


def test_ore_ingot_and_forge_graphics_match_servuo():
    # ServUO Scripts/Items/Resource/Ore.cs BaseOre.RandomSize / BaseIngot stacks.
    assert ORE_GRAPHICS == {0x19B7, 0x19B8, 0x19B9, 0x19BA}
    assert INGOT_GRAPHICS == {0x1BEF, 0x1BF0, 0x1BF1, 0x1BF2}
    # The `Forge` item class (`[Add Forge`) plus the static forge range Ore.cs's
    # IsForge / DefBlacksmithy.cs's CheckAnvilAndForge both accept.
    assert 0x0FB1 in FORGE_GRAPHICS
    assert 0x197A in FORGE_GRAPHICS and 0x19A9 in FORGE_GRAPHICS
    assert 0x19AA not in FORGE_GRAPHICS


def test_mines_while_below_ore_threshold():
    # ServUO mining stacks each dig onto the same pile (`Mobile.PlaceInBackpack`),
    # so a realistic haul is one growing stack, not several piles — threshold on
    # `amount`, below the default threshold (5) here.
    items = [_backpack(), _pickaxe(), _forge(), _ore(0x400, amount=4)]
    ctx = _ctx(items)
    res = MineAndSmelt().step(ctx)
    assert isinstance(res.action, Use) and res.action.serial == PICKAXE_SERIAL
    assert ctx.memory.get("smelt_phase", "mine") == "mine"


def test_switches_to_smelt_at_threshold():
    items = [_backpack(), _pickaxe(), _forge(), _ore(0x400, amount=5)]
    ctx = _ctx(items)
    res = MineAndSmelt().step(ctx)
    assert ctx.memory["smelt_phase"] == "smelt"
    assert isinstance(res.action, Use) and res.action.serial == 0x400


def test_does_not_switch_mid_mining_swing():
    # A mining cursor is already open — reaching the threshold must not hijack it.
    items = [_backpack(), _pickaxe(), _forge(), _ore(0x400, amount=5)]
    ctx = _ctx(items, pending=TargetCursor(target_type=1, cursor_id=7, cursor_flag=0))
    res = MineAndSmelt().step(ctx)
    assert isinstance(res.action, TargetGround)  # Mine's own probe answer
    assert ctx.memory.get("smelt_phase", "mine") == "mine"


def test_smelt_targets_forge_when_cursor_opens():
    forge = _forge()
    items = [_backpack(), _pickaxe(), forge, _ore(0x400)]
    mem = {"smelt_phase": "smelt"}
    ctx = _ctx(items, pending=TargetCursor(target_type=0, cursor_id=1, cursor_flag=0), memory=mem)
    res = MineAndSmelt().step(ctx)
    assert isinstance(res.action, TargetObject) and res.action.serial == forge.serial


def test_no_forge_in_reach_falls_back_to_mine_probe():
    # Defensive path: a stray smelt cursor with no forge staged is handed off to
    # Mine's own cursor handling rather than hanging forever.
    items = [_backpack(), _pickaxe(), _ore(0x400)]  # no forge item at all
    mem = {"smelt_phase": "smelt"}
    ctx = _ctx(items, pending=TargetCursor(target_type=0, cursor_id=1, cursor_flag=0), memory=mem)
    res = MineAndSmelt().step(ctx)
    assert isinstance(res.action, TargetGround)
    assert mem["smelt_phase"] == "mine"


def test_resumes_mining_once_ore_is_gone():
    items = [_backpack(), _pickaxe(), _forge()]  # no ore piles left
    mem = {"smelt_phase": "smelt", "smelt_ingots": 0}
    ctx = _ctx(items, memory=mem)
    res = MineAndSmelt().step(ctx)
    assert mem["smelt_phase"] == "mine"
    assert isinstance(res.action, Use) and res.action.serial == PICKAXE_SERIAL


def test_skips_unsmeltable_small_pile_and_resumes_mining():
    # ServUO Ore.cs hard-fails smelting the "small pile" graphic (0x19B7) whenever
    # its amount is below 2 ("not enough metal-bearing ore in this pile") — live-
    # observed to loop forever if not skipped, since a failed attempt never
    # consumes or grows the pile. A lone leftover should be left for a later dig
    # to stack onto (same graphic), not hammered every tick.
    items = [_backpack(), _pickaxe(), _forge(), _item(0x400, SMALL_ORE_GRAPHIC, container=BACKPACK, amount=1)]
    mem = {"smelt_phase": "smelt"}
    res = MineAndSmelt().step(_ctx(items, memory=mem))
    assert mem["smelt_phase"] == "mine"
    assert isinstance(res.action, Use) and res.action.serial == PICKAXE_SERIAL


def test_smelts_small_pile_once_it_reaches_two():
    items = [_backpack(), _pickaxe(), _forge(), _item(0x400, SMALL_ORE_GRAPHIC, container=BACKPACK, amount=2)]
    mem = {"smelt_phase": "smelt"}
    res = MineAndSmelt().step(_ctx(items, memory=mem))
    assert isinstance(res.action, Use) and res.action.serial == 0x400


def test_rewards_ingot_gain_while_smelting():
    forge = _forge()
    mem = {"smelt_phase": "smelt"}

    items1 = [_backpack(), _pickaxe(), forge, _ore(0x400)]
    res1 = MineAndSmelt().step(_ctx(items1, memory=mem))  # seeds the ingot-count baseline
    assert res1.reward == 0.0

    items2 = [_backpack(), _pickaxe(), forge, _ore(0x401),
              _item(0x777, INGOT_GRAPHIC, container=BACKPACK, amount=3)]
    res2 = MineAndSmelt().step(_ctx(items2, memory=mem))
    assert res2.reward == 3.0
