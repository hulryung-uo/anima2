"""A2 death interrupt: safe resurrection and fail-closed own-corpse recovery."""

from anima2.agent import Agent
from anima2.contract import (
    CorpseEquip,
    CorpseEquipEntry,
    Drop,
    GumpResponse,
    GumpView,
    ItemView,
    Observation,
    PickUp,
    PlayerView,
    Position,
    TargetCancel,
    TargetCursor,
    Use,
    WalkTo,
)
from anima2.mock_body import MockBody
from anima2.persona import Persona
from anima2.planner import Planner
from anima2.skills import RecoverDeath
from anima2.skills.base import SkillContext, Status

PLAYER = 1
BACKPACK = 2
CORPSE = 10
WORN = 50
PACK_ITEM = 51
STACK_ITEM = 52


def _item(serial, graphic, amount, *, pos=Position(100, 100, 0), container=None, layer=0):
    return ItemView(serial, graphic, amount, pos, container, layer, 0)


def _free_resurrection_gump():
    return GumpView(
        serial=0x100,
        gump_id=0x200,
        elements=[
            {"type": "html", "text": {"cliloc": {"id": 1011022}}},
            {"type": "button", "reply_id": 1, "pageflag": 1},
        ],
    )


def _ctx(
    *, dead=False, items=(), gumps=(), pending=None, memory=None, corpse_equip=(),
    pos=Position(100, 100, 0),
):
    player = PlayerView(
        serial=PLAYER,
        pos=pos,
        hits=0 if dead else 20,
        hits_max=100,
        body=0x192 if dead else 0x190,
        dead=dead,
    )
    obs = Observation(
        player=player,
        items=list(items),
        gumps=list(gumps),
        pending_target=pending,
        corpse_equip=list(corpse_equip),
    )
    return SkillContext(
        obs=obs,
        persona=Persona(name="Ragnar"),
        memory=memory if memory is not None else {},
    )


def _attribution_memory():
    return {
        "death_corpse_pending": True,
        "death_corpse_phase": "find",
        "death_last_alive_body": 0x190,
        "death_last_alive_pos": (100, 100, 0),
        "death_last_equipped": {WORN},
        "death_last_pack_owned": set(),
    }


def test_death_entry_stops_old_async_route_before_any_other_action():
    result = RecoverDeath().step(_ctx(dead=True))
    assert isinstance(result.action, WalkTo)
    assert (result.action.x, result.action.y) == (100, 100)


def test_dead_recovery_cancels_stale_cursor_before_resurrection_gump():
    memory = {RecoverDeath._ROUTE_STOPPED: True}
    cursor = TargetCursor(target_type=1, cursor_id=7, cursor_flag=0)
    result = RecoverDeath().step(
        _ctx(dead=True, pending=cursor, gumps=[_free_resurrection_gump()], memory=memory)
    )
    assert isinstance(result.action, TargetCancel)


def test_only_structurally_verified_free_resurrection_gump_is_accepted_once():
    memory = {RecoverDeath._ROUTE_STOPPED: True}
    decoy = GumpView(
        serial=3,
        gump_id=4,
        elements=[{"type": "button", "reply_id": 1, "pageflag": 1}],
    )
    skill = RecoverDeath()
    assert skill.step(_ctx(dead=True, gumps=[decoy], memory=memory)).action is None

    safe = _free_resurrection_gump()
    accepted = skill.step(_ctx(dead=True, gumps=[safe], memory=memory))
    assert isinstance(accepted.action, GumpResponse) and accepted.action.button == 1
    assert skill.step(_ctx(dead=True, gumps=[safe], memory=memory)).action is None


def test_priced_or_non_reply_resurrection_gump_is_not_auto_accepted():
    memory = {RecoverDeath._ROUTE_STOPPED: True}
    priced = GumpView(
        serial=3,
        gump_id=4,
        elements=[
            {"type": "html", "text": {"cliloc": {"id": 1011022}}},
            {"type": "button", "reply_id": 2, "pageflag": 1},
            {"type": "button", "reply_id": 1, "pageflag": 0},
        ],
    )
    assert RecoverDeath().step(_ctx(dead=True, gumps=[priced], memory=memory)).action is None


def test_healer_target_reentry_dance_exits_then_crosses_back_in():
    skill = RecoverDeath(resurrection_target=(105, 100))
    memory = {
        RecoverDeath._ROUTE_STOPPED: True,
        RecoverDeath._RES_ROUTE_SENT: True,
        RecoverDeath._RES_INSIDE_WAIT: 3,
    }
    exiting = skill.step(_ctx(dead=True, pos=Position(104, 100, 0), memory=memory))
    assert isinstance(exiting.action, WalkTo)
    assert (exiting.action.x, exiting.action.y) == (99, 100)

    reentering = skill.step(_ctx(dead=True, pos=Position(101, 100, 0), memory=memory))
    assert isinstance(reentering.action, WalkTo)
    assert (reentering.action.x, reentering.action.y) == (105, 100)


def test_alive_observation_confirms_resurrection_and_arms_corpse_recovery():
    memory = {RecoverDeath._WAITING: True, RecoverDeath._ROUTE_STOPPED: True}
    skill = RecoverDeath()
    stopped = skill.step(_ctx(memory=memory))
    assert isinstance(stopped.action, WalkTo)
    result = skill.step(_ctx(memory=memory))
    assert result.status is Status.SUCCESS
    assert memory[skill._CORPSE_PENDING] is True
    assert skill.consumes_goal is False


def test_unique_strongly_attributed_corpse_is_opened():
    memory = _attribution_memory()
    corpse = _item(CORPSE, 0x2006, 0x190)
    evidence = CorpseEquip(CORPSE, [CorpseEquipEntry(layer=2, serial=WORN)])
    result = RecoverDeath().step(
        _ctx(items=[corpse], corpse_equip=[evidence], memory=memory)
    )
    assert isinstance(result.action, Use) and result.action.serial == CORPSE


def test_visible_predeath_backpack_item_strongly_attributes_own_corpse():
    memory = _attribution_memory()
    memory["death_last_equipped"] = set()
    memory["death_last_pack_owned"] = {PACK_ITEM}
    corpse = _item(CORPSE, 0x2006, 0x190)
    pack_item = _item(PACK_ITEM, 0x0F52, 1, container=CORPSE)

    result = RecoverDeath().step(_ctx(items=[corpse, pack_item], memory=memory))
    assert isinstance(result.action, Use) and result.action.serial == CORPSE


def test_body_and_death_position_without_serial_evidence_fail_closed():
    memory = _attribution_memory()
    memory["death_last_equipped"] = set()
    corpse = _item(CORPSE, 0x2006, 0x190)

    result = RecoverDeath().step(_ctx(items=[corpse], memory=memory))
    assert result.status is Status.RUNNING and result.action is None
    assert RecoverDeath._CORPSE_SERIAL not in memory


def test_ambiguous_matching_corpses_fail_closed_without_opening_either():
    memory = _attribution_memory()
    corpses = [
        _item(CORPSE, 0x2006, 0x190),
        _item(CORPSE + 1, 0x2006, 0x190),
    ]
    evidence = [
        CorpseEquip(CORPSE, [CorpseEquipEntry(layer=2, serial=WORN)]),
        CorpseEquip(CORPSE + 1, [CorpseEquipEntry(layer=2, serial=WORN)]),
    ]
    result = RecoverDeath().step(
        _ctx(items=corpses, corpse_equip=evidence, memory=memory)
    )
    assert result.status is Status.FAILURE and result.action is None
    assert RecoverDeath._CORPSE_PENDING not in memory


def test_missing_corpse_wait_is_bounded_and_yields_success_for_auto_return():
    memory = _attribution_memory()
    memory[RecoverDeath._WAIT] = RecoverDeath.corpse_find_timeout_ticks
    result = RecoverDeath().step(_ctx(memory=memory))
    assert result.status is Status.SUCCESS and result.action is None
    assert RecoverDeath._CORPSE_PENDING not in memory


def test_corpse_contents_follow_pickup_drop_verify_sequence():
    memory = _attribution_memory()
    memory.update(
        {
            RecoverDeath._CORPSE_SERIAL: CORPSE,
            RecoverDeath._CORPSE_PHASE: "loot",
        }
    )
    skill = RecoverDeath()
    backpack = _item(BACKPACK, 0x0E75, 1, container=PLAYER, layer=0x15)
    corpse = _item(CORPSE, 0x2006, 0x190)
    loot = _item(WORN, 0x13B9, 1, container=CORPSE)

    picked = skill.step(_ctx(items=[backpack, corpse, loot], memory=memory))
    assert isinstance(picked.action, PickUp) and picked.action.serial == WORN
    dropped = skill.step(_ctx(items=[backpack, corpse, loot], memory=memory))
    assert isinstance(dropped.action, Drop) and dropped.action.container == BACKPACK

    recovered = _item(WORN, 0x13B9, 1, container=BACKPACK)
    done = skill.step(_ctx(items=[backpack, corpse, recovered], memory=memory))
    assert done.status is Status.SUCCESS


def test_missing_item_is_not_misrecorded_as_a_successful_drop():
    memory = _attribution_memory()
    memory.update(
        {
            RecoverDeath._CORPSE_SERIAL: CORPSE,
            RecoverDeath._CORPSE_PHASE: "verify",
            RecoverDeath._HELD: WORN,
            RecoverDeath._HELD_GRAPHIC: 0x13B9,
            RecoverDeath._HELD_AMOUNT: 1,
            RecoverDeath._PACK_AMOUNT_BEFORE: 0,
            RecoverDeath._WAIT: RecoverDeath.item_verify_timeout_ticks,
        }
    )
    backpack = _item(BACKPACK, 0x0E75, 1, container=PLAYER, layer=0x15)
    corpse = _item(CORPSE, 0x2006, 0x190)

    result = RecoverDeath().step(_ctx(items=[backpack, corpse], memory=memory))
    assert result.status is Status.FAILURE and result.action is None
    assert RecoverDeath._CORPSE_PENDING not in memory


def test_stack_merge_is_proven_by_backpack_graphic_amount_delta():
    memory = _attribution_memory()
    memory.update(
        {
            RecoverDeath._CORPSE_SERIAL: CORPSE,
            RecoverDeath._CORPSE_PHASE: "verify",
            RecoverDeath._HELD: STACK_ITEM,
            RecoverDeath._HELD_GRAPHIC: 0x0E21,
            RecoverDeath._HELD_AMOUNT: 5,
            RecoverDeath._PACK_AMOUNT_BEFORE: 10,
        }
    )
    backpack = _item(BACKPACK, 0x0E75, 1, container=PLAYER, layer=0x15)
    corpse = _item(CORPSE, 0x2006, 0x190)
    merged_stack = _item(99, 0x0E21, 15, container=BACKPACK)

    result = RecoverDeath().step(
        _ctx(items=[backpack, corpse, merged_stack], memory=memory)
    )
    assert result.status is Status.SUCCESS and result.action is None


def test_agent_freezes_separate_equipped_and_backpack_evidence_on_death_edge():
    body = MockBody()
    body.player.body = 0x190
    body.player.hits = body.player.hits_max = 100
    body.player.pos = Position(100, 100, 0)
    body.items[BACKPACK] = _item(BACKPACK, 0x0E75, 1, container=PLAYER, layer=0x15)
    body.items[WORN] = _item(WORN, 0x13B9, 1, container=PLAYER, layer=1)
    body.items[PACK_ITEM] = _item(PACK_ITEM, 0x0F52, 1, container=BACKPACK)
    agent = Agent(body, Persona(name="Ragnar"), Planner([RecoverDeath()]))
    agent.tick()

    assert agent.memory["death_rolling_equipped"] == {WORN}
    assert agent.memory["death_rolling_pack_owned"] == {PACK_ITEM}
    assert "death_last_alive_body" not in agent.memory

    body.player.dead = True
    body.player.body = 0x192
    agent.tick()
    assert agent.memory["death_last_alive_body"] == 0x190
    assert agent.memory["death_last_alive_pos"] == (100, 100, 0)
    assert agent.memory["death_last_equipped"] == {WORN}
    assert agent.memory["death_last_pack_owned"] == {PACK_ITEM}
    assert BACKPACK not in agent.memory["death_last_equipped"]


def test_redeath_freezes_fresh_rolling_snapshot_and_resets_prior_episode_state():
    body = MockBody()
    body.player.body = 0x190
    body.player.hits = body.player.hits_max = 100
    body.player.pos = Position(100, 100, 0)
    body.items[BACKPACK] = _item(BACKPACK, 0x0E75, 1, container=PLAYER, layer=0x15)
    body.items[WORN] = _item(WORN, 0x13B9, 1, container=PLAYER, layer=1)
    body.items[PACK_ITEM] = _item(PACK_ITEM, 0x0F52, 1, container=BACKPACK)
    recovery = RecoverDeath()
    agent = Agent(body, Persona(name="Ragnar"), Planner([recovery]))
    agent.tick()

    body.player.dead = True
    body.player.body = 0x192
    agent.tick()
    assert agent.memory["death_episode"] == 1

    # Alive again and already running toward the old corpse: rolling evidence
    # must still follow the living character without overwriting episode 1's
    # frozen attribution evidence.
    body.player.dead = False
    body.player.body = 0x190
    body.player.pos = Position(200, 200, 0)
    body.items.clear()
    body.items[BACKPACK] = _item(
        BACKPACK, 0x0E75, 1, pos=body.player.pos, container=PLAYER, layer=0x15
    )
    body.items[WORN + 1] = _item(
        WORN + 1, 0x13BA, 1, pos=body.player.pos, container=PLAYER, layer=2
    )
    body.items[PACK_ITEM + 1] = _item(
        PACK_ITEM + 1, 0x0F53, 1, pos=body.player.pos, container=BACKPACK
    )
    agent.memory.pop(recovery._WAITING, None)
    agent.memory[recovery._CORPSE_PENDING] = True
    agent.tick()

    assert agent.memory["death_last_alive_pos"] == (100, 100, 0)
    assert agent.memory["death_rolling_alive_pos"] == (200, 200, 0)
    assert agent.memory["death_rolling_equipped"] == {WORN + 1}
    assert agent.memory["death_rolling_pack_owned"] == {PACK_ITEM + 1}

    # Seed stale state from episode 1. Episode 2 must discard all of it before
    # processing its first resurrection action.
    agent.memory.update(
        {
            recovery._GUMP_RESPONDED: (0x100, 0x200),
            recovery._CORPSE_SERIAL: CORPSE,
            recovery._HELD: PACK_ITEM,
            recovery._ROUTE_SENT: True,
        }
    )
    body.player.dead = True
    body.player.body = 0x192
    action = agent.tick()

    assert isinstance(action, WalkTo)
    assert agent.memory["death_episode"] == 2
    assert agent.memory[recovery._ACTIVE_EPISODE] == 2
    assert agent.memory["death_last_alive_pos"] == (200, 200, 0)
    assert agent.memory["death_last_equipped"] == {WORN + 1}
    assert agent.memory["death_last_pack_owned"] == {PACK_ITEM + 1}
    assert recovery._GUMP_RESPONDED not in agent.memory
    assert recovery._CORPSE_SERIAL not in agent.memory
    assert recovery._HELD not in agent.memory
    assert recovery._ROUTE_SENT not in agent.memory
