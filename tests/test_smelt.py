"""The MineAndSmelt/MineSmeltDeliver skills' decision logic, hand-built observations."""

from anima2.contract import (
    Drop,
    ItemView,
    Observation,
    PickUp,
    PlayerView,
    Position,
    TargetCursor,
    TargetGround,
    TargetObject,
    Use,
    Walk,
)
from anima2.persona import Persona
from anima2.skills import MineAndSmelt, MineSmeltDeliver
from anima2.skills.base import SkillContext
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.smelt import FORGE_GRAPHICS, INGOT_GRAPHICS, ORE_GRAPHICS, SMALL_ORE_GRAPHIC

PICKAXE = 0x0E86
BACKPACK = 0x40001453
FORGE_SERIAL = 0x900
PICKAXE_SERIAL = 0x300
ORE_GRAPHIC = 0x19B9
INGOT_GRAPHIC = 0x1BF2
SMITHY = (200, 200)


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


def _ingot(serial, amount=1):
    return _item(serial, INGOT_GRAPHIC, container=BACKPACK, amount=amount)


def _forge(distance=1):
    return _item(FORGE_SERIAL, 0x0FB1, distance=distance)


def _ctx(items, pending=None, memory=None, pos=Position(100, 100, 0)):
    obs = Observation(player=PlayerView(serial=1, pos=pos),
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


def test_smelt_final_batch_reward_survives_ore_running_out_same_tick():
    # One tick of observation lag: the ingot gain from the *last* Use and the ore
    # pile scan coming up empty can land on the same observation. The reward must
    # still reach this tick's result (it used to be discarded by the bare
    # `return None` that resumes mining) rather than getting silently dropped.
    forge = _forge()
    mem = {"smelt_phase": "smelt", "smelt_ingots": 0}
    items = [_backpack(), _pickaxe(), forge, _item(0x777, INGOT_GRAPHIC, container=BACKPACK, amount=3)]
    res = MineAndSmelt().step(_ctx(items, memory=mem))
    assert mem["smelt_phase"] == "mine"  # ore's gone — resumed mining, same tick
    assert isinstance(res.action, Use) and res.action.serial == PICKAXE_SERIAL
    assert res.reward == 3.0  # the last batch's ingots are still credited


# --- MineSmeltDeliver: the deliver phase (Phase 3 trade loop) --------------------


def test_deliver_backwards_compatible_without_a_smithy():
    # No `smithy_drop` in memory → byte-for-byte MineAndSmelt behaviour, even
    # with a fat ingot haul that would trigger delivery if configured.
    items = [_backpack(), _pickaxe(), _forge(), _ingot(0x777, amount=99)]
    ctx = _ctx(items)
    res = MineSmeltDeliver().step(ctx)
    assert isinstance(res.action, Use) and res.action.serial == PICKAXE_SERIAL
    assert "smithy_drop" not in ctx.memory and "deliver_ingots_prev" not in ctx.memory
    assert ctx.memory.get("smelt_phase", "mine") == "mine"  # never touched deliver machinery


def test_deliver_threshold_triggers_a_delivery_walk():
    items = [_backpack(), _pickaxe(), _forge(), _ingot(0x777, amount=10)]
    mem = {"smithy_drop": SMITHY}
    ctx = _ctx(items, memory=mem)
    res = MineSmeltDeliver().step(ctx)
    assert mem["smelt_phase"] == "deliver"
    assert isinstance(res.action, Walk)  # (100,100) -> (200,200): walk, don't teleport
    assert mem["miner_home"] == (100, 100)  # captured before leaving


def test_deliver_does_not_trigger_mid_mining_swing():
    # A mining cursor is open — reaching the deliver threshold must not hijack it
    # (mirrors MineAndSmelt's own mine->smelt guard).
    items = [_backpack(), _pickaxe(), _forge(), _ingot(0x777, amount=10)]
    mem = {"smithy_drop": SMITHY}
    ctx = _ctx(items, pending=TargetCursor(target_type=1, cursor_id=1, cursor_flag=0), memory=mem)
    res = MineSmeltDeliver().step(ctx)
    assert isinstance(res.action, TargetGround)  # Mine's own probe answer
    assert mem.get("smelt_phase", "mine") == "mine"


def test_deliver_walks_toward_the_smithy_until_arrival():
    items = [_backpack(), _pickaxe(), _ingot(0x777, amount=10)]
    mem = {"smithy_drop": SMITHY, "smelt_phase": "deliver", "miner_home": (100, 100)}
    ctx = _ctx(items, memory=mem, pos=Position(150, 150, 0))
    res = MineSmeltDeliver().step(ctx)
    assert mem["smelt_phase"] == "deliver"  # still en route
    assert isinstance(res.action, Walk) and res.action.dir == 3  # SE toward (200,200)


def test_deliver_picks_up_from_an_adjacent_tile_without_stepping_onto_the_smithy():
    # The drop point is typically the blacksmith's own permanently-occupied
    # stand tile; requiring chebyshev == 0 to it would make every delivery
    # depend on ServUO's shove mechanic. A ground Drop reaches 2 tiles, so
    # stopping one tile short (chebyshev == 1) must be enough to drop.
    pile = _ingot(0x777, amount=10)
    items = [_backpack(), _pickaxe(), pile]
    mem = {"smithy_drop": SMITHY, "smelt_phase": "deliver", "miner_home": (100, 100)}
    adjacent = Position(SMITHY[0] - 1, SMITHY[1], 0)  # chebyshev == 1, not on the tile itself

    res = MineSmeltDeliver().step(_ctx(items, memory=mem, pos=adjacent))
    assert isinstance(res.action, PickUp)  # close enough — no more walking
    assert mem["smelt_phase"] == "deliver"


def test_deliver_still_walks_when_two_tiles_from_the_smithy():
    items = [_backpack(), _pickaxe(), _ingot(0x777, amount=10)]
    mem = {"smithy_drop": SMITHY, "smelt_phase": "deliver", "miner_home": (100, 100)}
    two_away = Position(SMITHY[0] - 2, SMITHY[1], 0)  # chebyshev == 2 — still short

    res = MineSmeltDeliver().step(_ctx(items, memory=mem, pos=two_away))
    assert isinstance(res.action, Walk)


def test_deliver_picks_up_then_drops_a_pack_pile_on_arrival():
    # A UO ground drop is two packets: `PickUp` lifts the pile out of the pack
    # first (a bare `Drop` with no prior `PickUp` is illegal and gets silently
    # ignored server-side — live-observed: nothing ever left the pack until
    # this was two actions), *then* `Drop` places it.
    pile = _ingot(0x777, amount=10)
    items = [_backpack(), _pickaxe(), pile]
    mem = {"smithy_drop": SMITHY, "smelt_phase": "deliver", "miner_home": (100, 100)}

    res1 = MineSmeltDeliver().step(_ctx(items, memory=mem, pos=Position(*SMITHY, 0)))
    assert mem["smelt_phase"] == "deliver"  # still delivering (or more piles next tick)
    assert isinstance(res1.action, PickUp)
    assert res1.action.serial == pile.serial and res1.action.amount == 10
    assert mem["deliver_held"] == pile.serial

    # Next tick: the lift succeeded (the pile is gone from the pack) — place it.
    items2 = [_backpack(), _pickaxe()]
    res2 = MineSmeltDeliver().step(_ctx(items2, memory=mem, pos=Position(*SMITHY, 0)))
    assert isinstance(res2.action, Drop)
    assert res2.action.serial == pile.serial
    assert (res2.action.x, res2.action.y) == SMITHY
    assert res2.action.container == 0xFFFFFFFF  # ground, not a container
    assert "deliver_held" not in mem


def test_deliver_rewards_only_ingots_confirmed_gone_from_the_pack():
    mem = {"smithy_drop": SMITHY, "smelt_phase": "deliver", "miner_home": (100, 100)}
    pos = Position(*SMITHY, 0)

    items1 = [_backpack(), _pickaxe(), _ingot(0x777, amount=10)]
    res1 = MineSmeltDeliver().step(_ctx(items1, memory=mem, pos=pos))  # seeds baseline, lifts the pile
    assert res1.reward == 0.0
    assert isinstance(res1.action, PickUp)

    # Simulate the server having applied that lift: the pile is gone from the pack.
    items2 = [_backpack(), _pickaxe()]
    res2 = MineSmeltDeliver().step(_ctx(items2, memory=mem, pos=pos))
    assert res2.reward == 10.0
    assert isinstance(res2.action, Drop)


def test_deliver_finishes_and_switches_to_return_once_pack_is_empty():
    items = [_backpack(), _pickaxe()]  # no ingots left — the haul is delivered
    mem = {"smithy_drop": SMITHY, "smelt_phase": "deliver", "miner_home": (100, 100),
          "deliver_ingots_start": 0}
    ctx = _ctx(items, memory=mem, pos=Position(*SMITHY, 0))
    res = MineSmeltDeliver().step(ctx)
    assert mem["smelt_phase"] == "return"
    assert "deliver_ingots_start" not in mem and "deliver_paid" not in mem
    assert isinstance(res.action, Walk) and res.action.dir == 7  # NW: (200,200) -> (100,100)


def test_deliver_final_confirmed_drop_reward_survives_the_lagged_empty_scan():
    # One tick of observation lag: the pack-decrease from the final `Drop` and the
    # pile scan coming up empty land on the *same* observation — `deliver_ingots_start`
    # (10) is stale from a tick or two back, but this tick's pack already shows 0
    # ingots *and* no pile left to pick up. That confirmed 10-ingot reward must
    # still reach this tick's result (the same-tick fallthrough into the `return`
    # leg), not get silently dropped by the bare `return None` that ends delivery.
    items = [_backpack(), _pickaxe()]  # no ingots left — the haul is delivered
    mem = {"smithy_drop": SMITHY, "smelt_phase": "deliver", "miner_home": (100, 100),
          "deliver_ingots_start": 10}
    ctx = _ctx(items, memory=mem, pos=Position(*SMITHY, 0))
    res = MineSmeltDeliver().step(ctx)
    assert mem["smelt_phase"] == "return"
    assert "deliver_ingots_start" not in mem and "deliver_paid" not in mem
    assert isinstance(res.action, Walk) and res.action.dir == 7  # same-tick fallthrough
    assert res.reward == 10.0  # the confirmed delivery is credited, not discarded


def test_deliver_reward_does_not_double_pay_when_a_drop_bounces_back_into_the_pack():
    # ServUO bounces a server-rejected ground Drop straight back into the source
    # container (Item.Bounce): the pile that was just lifted reappears in the
    # pack with no corresponding "increase" penalty in the old prev-vs-now
    # accounting, so a naive "pay on every decrease" scheme pays the same
    # ingots again on the retry lift. Reward must track net confirmed loss
    # across the whole delivery phase instead.
    pile = _ingot(0x777, amount=10)
    items_at_arrival = [_backpack(), _pickaxe(), pile]
    items_lifted = [_backpack(), _pickaxe()]
    mem = {"smithy_drop": SMITHY, "smelt_phase": "deliver", "miner_home": (100, 100)}
    pos = Position(*SMITHY, 0)

    res1 = MineSmeltDeliver().step(_ctx(items_at_arrival, memory=mem, pos=pos))  # lift
    assert res1.reward == 0.0
    assert isinstance(res1.action, PickUp)

    res2 = MineSmeltDeliver().step(_ctx(items_lifted, memory=mem, pos=pos))  # lift confirmed -> drop
    assert res2.reward == 10.0
    assert isinstance(res2.action, Drop)

    # The drop bounces: the server rejects it and the pile lands back in the pack.
    res3 = MineSmeltDeliver().step(_ctx(items_at_arrival, memory=mem, pos=pos))
    assert res3.reward == 0.0  # no negative reward for the bounce either
    assert isinstance(res3.action, PickUp)  # re-lifts the bounced pile

    res4 = MineSmeltDeliver().step(_ctx(items_lifted, memory=mem, pos=pos))  # re-lift confirmed -> drop
    assert res4.reward == 0.0  # already paid on res2 — not paid again
    assert isinstance(res4.action, Drop)


def test_return_walks_home_then_resumes_mining_on_arrival():
    items = [_backpack(), _pickaxe(), _forge()]  # no ore — a plain mining swing awaits
    mem = {"smithy_drop": SMITHY, "smelt_phase": "return", "miner_home": (100, 100)}

    # Not home yet → keep walking.
    en_route = MineSmeltDeliver().step(_ctx(items, memory=mem, pos=Position(150, 150, 0)))
    assert mem["smelt_phase"] == "return"
    assert isinstance(en_route.action, Walk)

    # Home → the phase resets and MineAndSmelt's own step (Mine, here) takes over.
    home = MineSmeltDeliver().step(_ctx(items, memory=mem, pos=Position(100, 100, 0)))
    assert mem["smelt_phase"] == "mine"
    assert isinstance(home.action, Use) and home.action.serial == PICKAXE_SERIAL


def test_deliver_gives_up_a_wedged_leg_and_still_advances_the_phase():
    # `deliver_stall` already one short of the limit, same tile as last tick →
    # this tick trips the limit; the greedy (no-A*) mover gives up this leg
    # rather than retrying into the same obstruction forever.
    items = [_backpack(), _pickaxe(), _ingot(0x777, amount=10)]
    mem = {
        "smithy_drop": SMITHY, "smelt_phase": "deliver", "miner_home": (50, 50),
        "deliver_stall": 5, "deliver_last_pos": (100, 100),
    }
    ctx = _ctx(items, memory=mem, pos=Position(100, 100, 0))
    res = MineSmeltDeliver().step(ctx)
    assert mem["smelt_phase"] == "return"  # gave up delivering, moved on to the return leg
    assert "deliver_stall" not in mem and "deliver_last_pos" not in mem
    assert isinstance(res.action, Walk)  # the (fresh) return leg starts walking toward home
    assert mem["deliver_giveup_ingots"] == 10  # haul still in the pack — recorded for the backoff


def test_deliver_giveup_suppresses_immediate_retrigger_until_more_ingots_accumulate():
    # A wedged delivery leg must not send the miner right back into the same
    # obstruction on the very next mine-phase tick with the same haul — that's
    # the endless deliver<->return commute livelock. It should wait until the
    # pack holds *more* ingots than it did at give-up (real mining/smelting
    # progress), not just retry instantly.
    items = [_backpack(), _pickaxe(), _ingot(0x777, amount=10)]
    mem = {
        "smithy_drop": SMITHY, "smelt_phase": "deliver", "miner_home": (100, 100),
        "deliver_stall": 5, "deliver_last_pos": (150, 150),
    }
    gave_up = MineSmeltDeliver().step(_ctx(items, memory=mem, pos=Position(150, 150, 0)))
    assert mem["smelt_phase"] == "return"  # (falls through into the return leg this same tick)
    assert isinstance(gave_up.action, Walk)
    assert mem["deliver_giveup_ingots"] == 10

    # Arrive home — the phase resets to "mine" (still same tick, via MineAndSmelt.step).
    home_items = [_backpack(), _pickaxe(), _forge(), _ingot(0x777, amount=10)]
    MineSmeltDeliver().step(_ctx(home_items, memory=mem, pos=Position(100, 100, 0)))
    assert mem["smelt_phase"] == "mine"

    # Same ingot count as the failed trip — must NOT re-trigger delivery.
    MineSmeltDeliver().step(_ctx(home_items, memory=mem, pos=Position(100, 100, 0)))
    assert mem["smelt_phase"] == "mine"

    # More ore gets smelted into ingots — now a fresh delivery attempt is allowed.
    more_items = [_backpack(), _pickaxe(), _forge(), _ingot(0x777, amount=11)]
    MineSmeltDeliver().step(_ctx(more_items, memory=mem, pos=Position(100, 100, 0)))
    assert mem["smelt_phase"] == "deliver"
    assert "deliver_giveup_ingots" not in mem
