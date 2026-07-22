"""The Blacksmith skill drives the craft gump with the right button sequence."""

import pytest

from anima2.contract import (
    Drop,
    GumpResponse,
    GumpView,
    ItemView,
    JournalEntry,
    Observation,
    PickUp,
    PlayerView,
    Position,
    Use,
    Walk,
)
from anima2.persona import Persona
from anima2.skills import Blacksmith
from anima2.skills.base import SkillContext
from anima2.skills.craft import (
    CATEGORY_BTN,
    CRAFT_FAILURE_CLILOC,
    CRAFT_FAILURE_NO_LOSS_CLILOC,
    CRAFT_TITLE_CLILOC,
    DAGGER_BTN,
    DAGGER_GRAPHIC,
    DAGGER_NAME_CLILOC,
    IRON_RESOURCE_BTN,
    MAKE_LAST_BTN,
    MIN_INGOTS,
    NOT_ENOUGH_METAL_CLILOC,
    RESOURCE_MENU_BTN,
    CraftDaggers,
)
from anima2.skills.harvest import BACKPACK_LAYER

HAMMER = 0x13E3
INGOT_GRAPHIC = 0x1BF2
BACKPACK = 0x50


def _tool():
    return ItemView(serial=0x40, graphic=HAMMER, amount=1, pos=Position(),
                    container=BACKPACK, layer=0, distance=0)


def _backpack(serial=BACKPACK):
    return ItemView(serial=serial, graphic=0x0E75, amount=1, pos=Position(),
                    container=1, layer=BACKPACK_LAYER, distance=0)


def _pack_ingot(serial, amount, bp=BACKPACK):
    return ItemView(serial=serial, graphic=INGOT_GRAPHIC, amount=amount, pos=Position(),
                    container=bp, layer=0, distance=0)


def _dagger(serial, amount=1, bp=BACKPACK):
    return ItemView(
        serial=serial,
        graphic=DAGGER_GRAPHIC,
        amount=amount,
        pos=Position(),
        container=bp,
        layer=0,
        distance=0,
    )


def _ground_ingot(serial, amount, distance):
    return ItemView(serial=serial, graphic=INGOT_GRAPHIC, amount=amount,
                    pos=Position(10, 10, 0), container=None, layer=0, distance=distance)


def _ctx(
    gumps=(),
    state=None,
    items=(),
    journal=(),
    memory=None,
    pos=Position(0, 0, 0),
    goal_id=None,
):
    mem = memory if memory is not None else ({} if state is None else {"bs_state": state})
    obs = Observation(player=PlayerView(serial=1, pos=pos),
                      items=[_tool(), *items], gumps=list(gumps), new_journal=list(journal))
    return SkillContext(
        obs=obs,
        persona=Persona(name="T"),
        memory=mem,
        goal_id=goal_id,
    )


def test_button_ids_match_servuo_formula():
    # Dagger is index 2 within the Bladed group on this ServUO (live-verified
    # by decoding the CraftGump layout's cliloc — see craft.py's comment);
    # index 4 is a Kryss, a far pricier item a thin ingot stock can't sustain.
    assert (CATEGORY_BTN, DAGGER_BTN, MAKE_LAST_BTN) == (22, 16, 21)


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


# --- Blacksmith fetch: the pickup side of the trade loop (Phase 3) --------------


def test_plenty_of_metal_ignores_a_nearby_ground_pile():
    # A gump is open (mid make-loop) and there's a dropped pile right there, but
    # the pack already has plenty of metal — must not abandon the gump answer.
    g = GumpView(serial=0xAB, gump_id=0xCD, layout="")
    items = [_backpack(), _pack_ingot(0x700, amount=50), _ground_ingot(0x701, amount=5, distance=1)]
    res = Blacksmith().step(_ctx(gumps=[g], state="loop", items=items))
    assert isinstance(res.action, GumpResponse) and res.action.button == MAKE_LAST_BTN


def test_starved_but_gump_open_still_answers_the_gump_not_the_ground_pile():
    # Starved *and* a ground pile in range, but a gump is open — must never
    # fight the gump state machine with a walk/pickup instead of its answer.
    g = GumpView(serial=0xAB, gump_id=0xCD, layout="")
    items = [_backpack(), _ground_ingot(0x701, amount=5, distance=1)]  # 0 pack ingots
    res = Blacksmith().step(_ctx(gumps=[g], state="loop", items=items))
    assert isinstance(res.action, GumpResponse) and res.action.button == MAKE_LAST_BTN


def test_starved_with_no_ground_ingots_in_range_just_tries_to_craft():
    items = [_backpack()]  # 0 pack ingots, nothing on the ground nearby
    res = Blacksmith().step(_ctx(state="open", items=items))
    assert isinstance(res.action, Use) and res.action.serial == 0x40  # falls through to the tool


def test_starved_walks_toward_a_ground_pile_out_of_pickup_reach():
    pile = _ground_ingot(0x701, amount=5, distance=4)
    items = [_backpack(), pile]  # 0 pack ingots
    res = Blacksmith().step(_ctx(state="open", items=items))
    assert isinstance(res.action, Walk)


def test_starved_picks_up_an_adjacent_ground_pile():
    pile = _ground_ingot(0x701, amount=5, distance=1)
    items = [_backpack(), pile]  # 0 pack ingots, pile within PICKUP_REACH
    ctx = _ctx(state="open", items=items)
    res = Blacksmith().step(ctx)
    assert isinstance(res.action, PickUp) and res.action.serial == pile.serial and res.action.amount == 5
    assert ctx.memory["bs_fetch_held"] == pile.serial


def test_picked_up_pile_gets_dropped_into_the_pack_next_tick():
    items = [_backpack()]  # already lifted; nothing left on the ground
    ctx = _ctx(items=items, memory={"bs_state": "fetch", "bs_fetch_held": 0x701})
    res = Blacksmith().step(ctx)
    assert isinstance(res.action, Drop) and res.action.serial == 0x701 and res.action.container == BACKPACK
    assert "bs_fetch_held" not in ctx.memory


def test_resumes_crafting_once_enough_metal_is_fetched():
    # A fetch trip was mid-flight (bs_state == "fetch") but the pack is now
    # over MIN_INGOTS and nothing more is on the ground — resume the MAKE loop
    # (re-open with the tool) instead of idling.
    items = [_backpack(), _pack_ingot(0x700, amount=MIN_INGOTS)]
    ctx = _ctx(items=items, memory={"bs_state": "fetch"})
    res = Blacksmith().step(ctx)
    assert ctx.memory["bs_state"] == "category"  # _fetch_step -> "open" -> pressed Use(tool)
    assert isinstance(res.action, Use) and res.action.serial == 0x40


def test_not_enough_metal_cliloc_also_triggers_a_fetch():
    # Corroborating signal: even if the pack count check somehow missed it, the
    # server's own "not enough metal" message starts a fetch trip.
    pile = _ground_ingot(0x701, amount=5, distance=1)
    items = [_backpack(), _pack_ingot(0x700, amount=99), pile]  # plenty, by count alone
    journal = [JournalEntry(serial=1, name="", text="", msg_type=0, hue=0, cliloc=NOT_ENOUGH_METAL_CLILOC)]
    res = Blacksmith().step(_ctx(state="open", items=items, journal=journal))
    assert isinstance(res.action, PickUp) and res.action.serial == pile.serial


def test_stuck_not_enough_metal_gump_still_fetches_a_ground_pile():
    # A truly-out-of-metal MAKE LAST press fails synchronously and ServUO just
    # re-shows the *same* gump with the failure baked into its layout (no
    # `gump is None` tick ever comes) — live-observed. Pressing that button
    # again can never succeed, so it must not block noticing a delivered pile.
    g = GumpView(serial=0xAB, gump_id=0xCD,
                layout="{ ... }{ xmfhtmlgumpcolor 170 295 350 40 1044037 0 0 32767 }{ ... }")
    pile = _ground_ingot(0x701, amount=5, distance=1)
    items = [_backpack(), pile]  # 0 pack ingots
    ctx = _ctx(gumps=[g], state="loop", items=items)
    res = Blacksmith().step(ctx)
    assert isinstance(res.action, PickUp) and res.action.serial == pile.serial


def test_fetch_gives_up_a_wedged_pile_and_falls_back_to_crafting():
    pile = _ground_ingot(0x701, amount=5, distance=4)  # out of reach — needs a walk
    items = [_backpack(), pile]
    mem = {"bs_state": "fetch", "bs_fetch_stall": 5, "bs_fetch_last_pos": (0, 0)}
    res = Blacksmith().step(_ctx(items=items, memory=mem))
    assert mem["bs_state"] == "category"  # gave up fetching, opened the tool instead
    assert isinstance(res.action, Use) and res.action.serial == 0x40
    assert "bs_fetch_stall" not in mem


def test_fetch_walks_back_to_the_stand_tile_before_resuming_crafting():
    # A pile several tiles out can pull the smith away from its forge/anvil
    # (PICKUP_RADIUS=6 well past CheckAnvilAndForge's 2-tile range). Once
    # there's nothing left to fetch, it must walk back to the stand tile
    # before resuming MAKE — otherwise every craft fails a proximity check
    # that isn't the "not enough metal" gump, with no recovery path.
    stand_ctx = _ctx(items=[_backpack()], memory={}, pos=Position(0, 0, 0))
    Blacksmith().step(stand_ctx)  # first-ever tick — records the stand tile
    assert stand_ctx.memory["bs_stand"] == (0, 0)

    # Mid-fetch, now several tiles from the stand tile, nothing left on the ground.
    mem = dict(stand_ctx.memory)
    mem["bs_state"] = "fetch"
    away = Position(4, 0, 0)
    res = Blacksmith().step(_ctx(items=[_backpack()], memory=mem, pos=away))
    assert mem["bs_state"] == "fetch"  # still mid-trip — walking home, not crafting yet
    assert isinstance(res.action, Walk)

    # Back at the stand tile — resumes the MAKE loop (re-opens with the tool).
    res2 = Blacksmith().step(_ctx(items=[_backpack()], memory=mem, pos=Position(0, 0, 0)))
    assert mem["bs_state"] == "category"
    assert isinstance(res2.action, Use) and res2.action.serial == 0x40


def test_fetch_return_gives_up_if_wedged_and_resumes_crafting_anyway():
    # The walk back can itself get wedged — must not loop forever either;
    # resume crafting from wherever the smith ended up rather than hanging.
    mem = {
        "bs_state": "fetch", "bs_stand": (0, 0),
        "bs_return_stall": 5, "bs_return_last_pos": (4, 0),
    }
    res = Blacksmith().step(_ctx(items=[_backpack()], memory=mem, pos=Position(4, 0, 0)))
    assert mem["bs_state"] == "category"
    assert isinstance(res.action, Use) and res.action.serial == 0x40
    assert "bs_return_stall" not in mem


# --- Proximity-stuck gump: CheckAnvilAndForge failure, cliloc 1044267 ----------
#
# Confirmed against the local ServUO checkout: `DefBlacksmithy.CanCraft`
# (Scripts/Services/Craft/DefBlacksmithy.cs) returns 1044267 when
# CheckAnvilAndForge (2-tile range) fails; CraftGump's "Create item"/"Make
# last" handlers (Core/CraftGump.cs::CraftItem) check it synchronously and
# re-SendGump the same failure baked into the layout — the exact mechanism
# `stuck_gump`/NOT_ENOUGH_METAL_CLILOC already handles, but for a different
# cliloc that `stuck_gump` didn't recognize (a live-caught regression: the
# smith freezes pressing MAKE_LAST forever once a fetch leaves it out of
# forge/anvil range, since neither `stuck_gump` nor `starved` ever fires).

_PROXIMITY_GUMP = GumpView(
    serial=0xAB, gump_id=0xCD,
    layout="{ ... }{ xmfhtmlgumpcolor 170 295 350 40 1044267 0 0 32767 }{ ... }",
)


def test_proximity_stuck_gump_walks_home_instead_of_pressing_a_dead_button():
    # Plenty of metal and mid make-loop state — both would normally mean
    # "press MAKE_LAST" — but a proximity failure can never be fixed by a
    # button press, so this must walk toward the stand tile instead.
    items = [_backpack(), _pack_ingot(0x700, amount=50)]
    mem = {"bs_state": "loop", "bs_stand": (0, 0)}
    away = Position(3, 0, 0)
    res = Blacksmith().step(_ctx(gumps=[_PROXIMITY_GUMP], items=items, memory=mem, pos=away))
    assert isinstance(res.action, Walk)
    assert mem["bs_state"] == "fetch_return"


def test_proximity_stuck_gump_resumes_pressing_buttons_once_back_at_stand():
    items = [_backpack(), _pack_ingot(0x700, amount=50)]
    mem = {"bs_state": "fetch_return", "bs_stand": (0, 0)}
    res = Blacksmith().step(_ctx(gumps=[_PROXIMITY_GUMP], items=items, memory=mem, pos=Position(0, 0, 0)))
    assert isinstance(res.action, GumpResponse) and res.action.button == CATEGORY_BTN
    assert mem["bs_state"] == "item"


def test_proximity_stuck_return_walk_gives_up_and_retries_instead_of_freezing():
    # If the walk home itself wedges, give up and try to craft from wherever
    # we are — that reproduces the same proximity failure next tick and
    # re-enters this same path, a self-healing retry rather than a silent
    # freeze (transient blockers like another agent tend to clear on their own).
    items = [_backpack(), _pack_ingot(0x700, amount=50)]
    mem = {
        "bs_state": "fetch_return", "bs_stand": (0, 0),
        "bs_return_stall": 5, "bs_return_last_pos": (3, 0),
    }
    res = Blacksmith().step(_ctx(gumps=[_PROXIMITY_GUMP], items=items, memory=mem, pos=Position(3, 0, 0)))
    assert mem["bs_state"] == "item"  # gave up, fell through to pressing buttons anyway
    assert isinstance(res.action, GumpResponse) and res.action.button == CATEGORY_BTN
    assert "bs_return_stall" not in mem


# --- Dead-gump watchdog: cliloc-independent, catches the reshow class -----------
#
# `stuck_gump`/`proximity_stuck` above only recognize a reshow by its specific
# baked-in cliloc. A third variant slips past both (live-caught: a truly-
# starved MAKE LAST press with nothing on the ground to fetch reshows a gump
# whose layout matches *neither* NOT_ENOUGH_METAL_CLILOC nor
# PROXIMITY_CLILOC), and `stuck_gump`'s own fetch-and-fail resets `bs_state`
# to "open" every tick with nothing to show for it, so it advances
# open->item->loop->[fails, reshows]->open->... forever — a fresh gump serial
# every single tick, never giving up. The watchdog tracks the *outcome*
# (pack ingots, pack daggers, Blacksmithing base) instead of any gump's text.


def test_dead_gump_watchdog_disengages_when_no_recognized_cliloc_ever_matches():
    items = [_backpack()]  # 0 pack ingots, nothing on the ground — genuinely starved
    mem = {"bs_state": "loop"}
    skill = Blacksmith()
    for i in range(skill.dead_gump_presses):
        g = GumpView(serial=0xA00 + i, gump_id=0xCD, layout="{ some unrecognized layout }")
        res = skill.step(_ctx(gumps=[g], items=items, memory=mem))
        assert isinstance(res.action, GumpResponse)  # still pressing — hasn't tripped yet

    # One more zero-progress tick trips the watchdog: falls through to the
    # "no gump open" path (re-opens with the tool) instead of pressing yet
    # another dead button.
    g_final = GumpView(serial=0xA99, gump_id=0xCD, layout="{ some unrecognized layout }")
    res = skill.step(_ctx(gumps=[g_final], items=items, memory=mem))
    assert isinstance(res.action, Use) and res.action.serial == 0x40
    assert mem["bs_dead_presses"] == 0  # reset once it trips


def test_dead_gump_watchdog_does_not_trip_when_ingots_are_actually_dropping():
    # Real progress (the pack ingot count actually moving, tick to tick) must
    # keep resetting the watchdog counter, not accumulate toward a false trip.
    mem = {"bs_state": "loop"}
    skill = Blacksmith()
    for amount in range(30, 30 - skill.dead_gump_presses - 2, -1):
        items = [_backpack(), _pack_ingot(0x700, amount=amount)]
        g = GumpView(serial=0xB00 + amount, gump_id=0xCD, layout="{ some layout }")
        res = skill.step(_ctx(gumps=[g], items=items, memory=mem))
        assert isinstance(res.action, GumpResponse)
        assert mem["bs_dead_presses"] == 0


# --- closed craft capability ---------------------------------------------------

_CRAFT_LAYOUT = (
    f"{{ xmfhtmlgumpcolor 0 0 0 0 {CRAFT_TITLE_CLILOC} 0 0 0 }}"
    f"{{ xmfhtmlgumpcolor 0 0 0 0 {DAGGER_NAME_CLILOC} 0 0 0 }}"
)


def _craft_gump(serial=0xAB, buttons=None):
    replies = (
        [RESOURCE_MENU_BTN, IRON_RESOURCE_BTN, CATEGORY_BTN, DAGGER_BTN, MAKE_LAST_BTN]
        if buttons is None
        else buttons
    )
    return GumpView(
        serial=serial,
        gump_id=0xCD,
        layout=_CRAFT_LAYOUT,
        elements=[
            {"type": "button", "pageflag": 1, "reply_id": button}
            for button in replies
        ],
    )


def test_craft_capability_builds_exact_five_item_batch_and_closes_gump():
    skill = CraftDaggers()
    mem: dict = {}
    items = [_backpack(), _pack_ingot(0x700, 15)]
    actions = []

    actions.append(skill.step(_ctx(items=items, memory=mem, goal_id=17)).action)
    for _expected in (RESOURCE_MENU_BTN, IRON_RESOURCE_BTN, CATEGORY_BTN, DAGGER_BTN):
        result = skill.step(
            _ctx(gumps=[_craft_gump()], items=items, memory=mem, goal_id=17)
        )
        actions.append(result.action)

    for made in range(1, 5):
        items = [
            _backpack(),
            _pack_ingot(0x700, 15 - made * 3),
            *[_dagger(0x800 + index) for index in range(made)],
        ]
        result = skill.step(
            _ctx(
                gumps=[_craft_gump(serial=0xAB + made)],
                items=items,
                memory=mem,
                goal_id=17,
            )
        )
        actions.append(result.action)

    items = [_backpack(), *[_dagger(0x800 + index) for index in range(5)]]
    close = skill.step(
        _ctx(gumps=[_craft_gump(serial=0xB0)], items=items, memory=mem, goal_id=17)
    )
    actions.append(close.action)
    finished = skill.step(_ctx(items=items, memory=mem, goal_id=17))

    assert isinstance(actions[0], Use) and actions[0].serial == 0x40
    assert [action.button for action in actions[1:] if isinstance(action, GumpResponse)] == [
        RESOURCE_MENU_BTN,
        IRON_RESOURCE_BTN,
        CATEGORY_BTN,
        DAGGER_BTN,
        *([MAKE_LAST_BTN] * 4),
        0,
    ]
    assert finished.action is None
    assert mem["cap_craft_needed"] == 5
    assert mem["cap_craft_confirmed"] == 5
    assert sum(amount for _serial, amount in mem["cap_craft_produced"]) == 5
    assert mem["cap_craft_ingots_used"] == 15
    assert mem["cap_craft_finished_goal_id"] == 17
    assert mem["cap_craft_returned_goal_id"] == 17


def test_craft_capability_never_answers_a_foreign_gump_or_fetches_ground_metal():
    skill = CraftDaggers()
    mem: dict = {}
    items = [_backpack(), _pack_ingot(0x700, 15), _ground_ingot(0x701, 50, 1)]
    opened = skill.step(_ctx(items=items, memory=mem, goal_id=17))
    foreign = GumpView(serial=0xEEE, gump_id=0x999, layout="foreign")
    blocked = skill.step(
        _ctx(gumps=[foreign], items=items, memory=mem, goal_id=17)
    )

    assert isinstance(opened.action, Use)
    assert blocked.action is None
    assert not isinstance(blocked.action, (Walk, PickUp, Drop))


def test_craft_capability_waits_for_the_resource_page_reply_not_a_stale_root_gump():
    skill = CraftDaggers()
    mem: dict = {}
    items = [_backpack(), _pack_ingot(0x700, 15)]
    skill.step(_ctx(items=items, memory=mem, goal_id=17))
    root = _craft_gump(buttons=[RESOURCE_MENU_BTN, CATEGORY_BTN, MAKE_LAST_BTN])

    opened_resource = skill.step(
        _ctx(gumps=[root], items=items, memory=mem, goal_id=17)
    )
    stale = skill.step(_ctx(gumps=[root], items=items, memory=mem, goal_id=17))
    resource = _craft_gump(buttons=[RESOURCE_MENU_BTN, IRON_RESOURCE_BTN])
    selected_iron = skill.step(
        _ctx(gumps=[resource], items=items, memory=mem, goal_id=17)
    )

    assert isinstance(opened_resource.action, GumpResponse)
    assert opened_resource.action.button == RESOURCE_MENU_BTN
    assert stale.action is None
    assert isinstance(selected_iron.action, GumpResponse)
    assert selected_iron.action.button == IRON_RESOURCE_BTN


def test_craft_capability_records_one_failed_attempt_before_retrying():
    skill = CraftDaggers()
    mem: dict = {}
    items = [_backpack(), _pack_ingot(0x700, 15)]
    skill.step(_ctx(items=items, memory=mem, goal_id=17))
    for _button in (RESOURCE_MENU_BTN, IRON_RESOURCE_BTN, CATEGORY_BTN, DAGGER_BTN):
        skill.step(
            _ctx(gumps=[_craft_gump()], items=items, memory=mem, goal_id=17)
        )

    after_failure = [_backpack(), _pack_ingot(0x700, 12)]
    failure_gump = _craft_gump(serial=0xAC)
    failure_gump.layout += (
        f"{{ xmfhtmlgumpcolor 0 0 0 0 {CRAFT_FAILURE_CLILOC} 0 0 0 }}"
    )
    retry = skill.step(
        _ctx(
            gumps=[failure_gump],
            items=after_failure,
            memory=mem,
            goal_id=17,
        )
    )

    assert isinstance(retry.action, GumpResponse)
    assert retry.action.button == MAKE_LAST_BTN
    assert mem["cap_craft_failed_attempts"] == 1
    assert mem["cap_craft_failed_ingots"] == 3
    assert mem["cap_craft_failure_costs"] == (3,)
    assert mem["cap_craft_ingots_used"] == 3
    assert mem["cap_craft_attempts"] == 2

    stale_retry = skill.step(
        _ctx(
            gumps=[failure_gump],
            items=after_failure,
            memory=mem,
            goal_id=17,
        )
    )
    assert stale_retry.action is None
    assert mem["cap_craft_failed_attempts"] == 1


def test_craft_capability_records_a_server_confirmed_no_loss_failure():
    skill = CraftDaggers()
    mem: dict = {}
    items = [_backpack(), _pack_ingot(0x700, 15)]
    skill.step(_ctx(items=items, memory=mem, goal_id=17))
    for _button in (RESOURCE_MENU_BTN, IRON_RESOURCE_BTN, CATEGORY_BTN, DAGGER_BTN):
        skill.step(
            _ctx(gumps=[_craft_gump()], items=items, memory=mem, goal_id=17)
        )
    failure_gump = _craft_gump(serial=0xAC)
    failure_gump.layout += (
        f"{{ xmfhtmlgumpcolor 0 0 0 0 {CRAFT_FAILURE_NO_LOSS_CLILOC} 0 0 0 }}"
    )

    retry = skill.step(
        _ctx(gumps=[failure_gump], items=items, memory=mem, goal_id=17)
    )

    assert isinstance(retry.action, GumpResponse)
    assert retry.action.button == MAKE_LAST_BTN
    assert mem["cap_craft_failed_attempts"] == 1
    assert mem["cap_craft_failed_ingots"] == 0
    assert mem["cap_craft_failure_costs"] == (0,)


def test_craft_capability_does_not_attribute_partial_packets_or_foreign_gumps_as_failure():
    skill = CraftDaggers()
    mem: dict = {}
    items = [_backpack(), _pack_ingot(0x700, 15)]
    skill.step(_ctx(items=items, memory=mem, goal_id=17))
    for _button in (RESOURCE_MENU_BTN, IRON_RESOURCE_BTN, CATEGORY_BTN, DAGGER_BTN):
        skill.step(
            _ctx(gumps=[_craft_gump()], items=items, memory=mem, goal_id=17)
        )
    partial = [_backpack(), _pack_ingot(0x700, 12)]
    foreign = GumpView(
        serial=0xEEE,
        gump_id=0x999,
        layout=f"foreign {CRAFT_FAILURE_CLILOC}",
        elements=_craft_gump().elements,
    )

    stale = skill.step(
        _ctx(gumps=[_craft_gump()], items=partial, memory=mem, goal_id=17)
    )
    unrelated = skill.step(
        _ctx(gumps=[foreign], items=partial, memory=mem, goal_id=17)
    )

    assert stale.action is None
    assert unrelated.action is None
    assert mem["cap_craft_stage"] == "pending"
    assert mem["cap_craft_failed_attempts"] == 0
    assert mem["cap_craft_failed_ingots"] == 0
    assert mem["cap_craft_failure_costs"] == ()


def test_craft_failure_notice_with_a_success_delta_aborts_as_conflicting_evidence():
    skill = CraftDaggers()
    mem: dict = {}
    items = [_backpack(), _pack_ingot(0x700, 15)]
    skill.step(_ctx(items=items, memory=mem, goal_id=17))
    for _button in (RESOURCE_MENU_BTN, IRON_RESOURCE_BTN, CATEGORY_BTN, DAGGER_BTN):
        skill.step(
            _ctx(gumps=[_craft_gump()], items=items, memory=mem, goal_id=17)
        )
    conflicting = _craft_gump(serial=0xAC)
    conflicting.layout += (
        f"{{ xmfhtmlgumpcolor 0 0 0 0 {CRAFT_FAILURE_CLILOC} 0 0 0 }}"
    )
    changed = [_backpack(), _pack_ingot(0x700, 12), _dagger(0x800)]

    closed = skill.step(
        _ctx(gumps=[conflicting], items=changed, memory=mem, goal_id=17)
    )

    assert isinstance(closed.action, GumpResponse)
    assert closed.action.button == 0
    assert mem["cap_craft_abort_goal_id"] == 17
    assert mem["cap_craft_confirmed"] == 0


def test_craft_waits_for_dagger_page_after_a_stale_category_gump():
    skill = CraftDaggers()
    mem: dict = {}
    items = [_backpack(), _pack_ingot(0x700, 15)]
    skill.step(_ctx(items=items, memory=mem, goal_id=17))
    resource = _craft_gump(buttons=[RESOURCE_MENU_BTN, IRON_RESOURCE_BTN])
    root = _craft_gump(buttons=[RESOURCE_MENU_BTN, CATEGORY_BTN, MAKE_LAST_BTN])
    skill.step(_ctx(gumps=[root], items=items, memory=mem, goal_id=17))
    skill.step(_ctx(gumps=[resource], items=items, memory=mem, goal_id=17))
    category = skill.step(_ctx(gumps=[root], items=items, memory=mem, goal_id=17))

    stale = skill.step(_ctx(gumps=[root], items=items, memory=mem, goal_id=17))
    dagger_page = _craft_gump(buttons=[DAGGER_BTN, MAKE_LAST_BTN])
    dagger = skill.step(
        _ctx(gumps=[dagger_page], items=items, memory=mem, goal_id=17)
    )

    assert isinstance(category.action, GumpResponse)
    assert category.action.button == CATEGORY_BTN
    assert stale.action is None
    assert mem["cap_craft_stage"] == "pending"
    assert isinstance(dagger.action, GumpResponse)
    assert dagger.action.button == DAGGER_BTN


def test_craft_capability_aborts_on_an_unattributable_mixed_inventory_delta():
    skill = CraftDaggers()
    mem: dict = {}
    items = [_backpack(), _pack_ingot(0x700, 15)]
    skill.step(_ctx(items=items, memory=mem, goal_id=17))
    for _button in (RESOURCE_MENU_BTN, IRON_RESOURCE_BTN, CATEGORY_BTN, DAGGER_BTN):
        skill.step(
            _ctx(gumps=[_craft_gump()], items=items, memory=mem, goal_id=17)
        )

    malformed = [
        _backpack(),
        _pack_ingot(0x700, 9),
        _dagger(0x800),
    ]
    closed = skill.step(
        _ctx(gumps=[_craft_gump(serial=0xAC)], items=malformed, memory=mem, goal_id=17)
    )

    assert isinstance(closed.action, GumpResponse)
    assert closed.action.button == 0
    assert mem["cap_craft_abort_goal_id"] == 17
    assert mem["cap_craft_stage"] == "close_wait"
    assert "cap_craft_attempt_daggers" not in mem
    assert "cap_craft_attempt_ingots" not in mem

    skill.step(_ctx(items=malformed, memory=mem, goal_id=17))
    assert mem["cap_craft_stage"] == "finished"


@pytest.mark.parametrize("abort_kind", ["limit", "server_error"])
def test_craft_pending_abort_clears_attempt_and_reaches_safe_yield(abort_kind):
    skill = CraftDaggers()
    mem = {
        "cap_craft_goal_id": 17,
        "cap_craft_stage": "pending",
        "cap_craft_steps": 0,
        "cap_craft_attempts": 1,
        "cap_craft_attempt_daggers": (),
        "cap_craft_attempt_ingots": 15,
        "cap_craft_attempt_gump_serial": 0xAB,
        "cap_craft_attempt_wait": 1,
        "cap_craft_start_pos": (0, 0),
    }
    if abort_kind == "limit":
        mem["cap_craft_steps"] = skill.max_goal_steps
        gump = _craft_gump()
    else:
        gump = GumpView(
            serial=0xAB,
            gump_id=0xCD,
            layout=_CRAFT_LAYOUT + f"{{ xmfhtmlgumpcolor 0 0 0 0 {NOT_ENOUGH_METAL_CLILOC} 0 0 0 }}",
            elements=_craft_gump().elements,
        )
    items = [_backpack(), _pack_ingot(0x700, 15)]

    closed = skill.step(
        _ctx(gumps=[gump], items=items, memory=mem, goal_id=17)
    )
    assert isinstance(closed.action, GumpResponse)
    assert closed.action.button == 0
    assert "cap_craft_attempt_daggers" not in mem
    assert "cap_craft_attempt_ingots" not in mem

    skill.step(_ctx(items=items, memory=mem, goal_id=17))
    assert mem["cap_craft_stage"] == "finished"


def test_craft_close_reopen_without_a_gump_eventually_reaches_safe_yield():
    skill = CraftDaggers()
    mem = {
        "cap_craft_goal_id": 17,
        "cap_craft_stage": "close",
        "cap_craft_steps": 10,
        "cap_craft_needed": 5,
        "cap_craft_confirmed": 5,
        "cap_craft_start_pos": (0, 0),
    }
    items = [_backpack(), *[_dagger(0x800 + index) for index in range(5)]]

    for _ in range(11):
        waiting = skill.step(_ctx(items=items, memory=mem, goal_id=17))
        assert waiting.action is None
    reopened = skill.step(_ctx(items=items, memory=mem, goal_id=17))
    assert isinstance(reopened.action, Use)

    for _ in range(12):
        waiting = skill.step(_ctx(items=items, memory=mem, goal_id=17))
        assert waiting.action is None
    assert mem["cap_craft_stage"] == "close_wait"

    finished = skill.step(_ctx(items=items, memory=mem, goal_id=17))
    assert finished.action is None
    assert mem["cap_craft_stage"] == "finished"
    assert mem["cap_craft_finished_goal_id"] == 17
    assert mem["cap_craft_returned_goal_id"] == 17


@pytest.mark.parametrize(
    "limit_key",
    ["cap_craft_steps", "cap_craft_attempts"],
)
def test_craft_limit_abort_reaches_a_safe_yield(limit_key):
    skill = CraftDaggers()
    mem = {
        "cap_craft_goal_id": 17,
        "cap_craft_stage": "resource_menu",
        "cap_craft_steps": 0,
        "cap_craft_attempts": 0,
        "cap_craft_start_pos": (0, 0),
    }
    mem[limit_key] = (
        skill.max_goal_steps if limit_key == "cap_craft_steps" else skill.max_attempts
    )
    items = [_backpack(), _pack_ingot(0x700, 15)]

    skill.step(_ctx(items=items, memory=mem, goal_id=17))
    assert mem["cap_craft_abort_goal_id"] == 17
    assert mem["cap_craft_stage"] == "close"

    for _ in range(25):
        skill.step(_ctx(items=items, memory=mem, goal_id=17))
        if mem.get("cap_craft_stage") == "finished":
            break
    assert mem["cap_craft_stage"] == "finished"


def test_craft_last_allowed_attempt_success_wins_over_the_attempt_limit():
    skill = CraftDaggers()
    mem = {
        "cap_craft_goal_id": 17,
        "cap_craft_stage": "pending",
        "cap_craft_gump_id": 0xCD,
        "cap_craft_steps": 20,
        "cap_craft_attempts": skill.max_attempts,
        "cap_craft_attempt_daggers": tuple(
            (0x700 + index, 1) for index in range(4)
        ),
        "cap_craft_attempt_ingots": 15,
        "cap_craft_attempt_gump_serial": 0xAB,
        "cap_craft_attempt_wait": 1,
        "cap_craft_start_ingots": 15,
        "cap_craft_start_daggers": tuple(
            (0x700 + index, 1) for index in range(4)
        ),
        "cap_craft_start_pos": (0, 0),
        "cap_craft_needed": 1,
        "cap_craft_confirmed": 0,
        "cap_craft_produced": (),
        "cap_craft_failed_attempts": 19,
        "cap_craft_failed_ingots": 0,
        "cap_craft_failure_costs": (0,) * 19,
        "cap_craft_dagger_button_goal_id": 17,
    }
    items = [
        _backpack(),
        _pack_ingot(0x710, 12),
        *[_dagger(0x700 + index) for index in range(4)],
        _dagger(0x800),
    ]

    closed = skill.step(
        _ctx(gumps=[_craft_gump(serial=0xAC)], items=items, memory=mem, goal_id=17)
    )

    assert isinstance(closed.action, GumpResponse)
    assert closed.action.button == 0
    assert mem["cap_craft_confirmed"] == 1
    assert mem["cap_craft_stage"] == "close_wait"
    assert "cap_craft_abort_goal_id" not in mem


def test_craft_last_allowed_attempt_waits_for_a_fresh_result_before_succeeding():
    skill = CraftDaggers()
    mem = {
        "cap_craft_goal_id": 17,
        "cap_craft_stage": "pending",
        "cap_craft_gump_id": 0xCD,
        "cap_craft_steps": 20,
        "cap_craft_attempts": skill.max_attempts,
        "cap_craft_attempt_daggers": tuple(
            (0x700 + index, 1) for index in range(4)
        ),
        "cap_craft_attempt_ingots": 15,
        "cap_craft_attempt_gump_serial": 0xAB,
        "cap_craft_attempt_wait": 0,
        "cap_craft_start_ingots": 15,
        "cap_craft_start_daggers": tuple(
            (0x700 + index, 1) for index in range(4)
        ),
        "cap_craft_start_pos": (0, 0),
        "cap_craft_needed": 1,
        "cap_craft_confirmed": 0,
        "cap_craft_produced": (),
        "cap_craft_failed_attempts": 19,
        "cap_craft_failed_ingots": 0,
        "cap_craft_failure_costs": (0,) * 19,
        "cap_craft_dagger_button_goal_id": 17,
    }
    before = [
        _backpack(),
        _pack_ingot(0x710, 15),
        *[_dagger(0x700 + index) for index in range(4)],
    ]
    after = [
        _backpack(),
        _pack_ingot(0x710, 12),
        *[_dagger(0x700 + index) for index in range(4)],
        _dagger(0x800),
    ]

    stale = skill.step(
        _ctx(gumps=[_craft_gump(serial=0xAB)], items=before, memory=mem, goal_id=17)
    )
    assert stale.action is None
    assert mem["cap_craft_stage"] == "pending"
    assert "cap_craft_abort_goal_id" not in mem

    closed = skill.step(
        _ctx(gumps=[_craft_gump(serial=0xAC)], items=after, memory=mem, goal_id=17)
    )
    assert isinstance(closed.action, GumpResponse)
    assert closed.action.button == 0
    assert mem["cap_craft_confirmed"] == 1
    assert "cap_craft_abort_goal_id" not in mem
