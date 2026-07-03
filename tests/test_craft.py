"""The Blacksmith skill drives the craft gump with the right button sequence."""

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
from anima2.skills.craft import CATEGORY_BTN, DAGGER_BTN, MAKE_LAST_BTN, MIN_INGOTS, NOT_ENOUGH_METAL_CLILOC
from anima2.skills.harvest import BACKPACK_LAYER

HAMMER = 0x13E3
INGOT_GRAPHIC = 0x1BF2
BACKPACK = 0x50


def _tool():
    return ItemView(serial=0x40, graphic=HAMMER, amount=1, pos=Position(),
                    container=0x99, layer=0, distance=0)


def _backpack(serial=BACKPACK):
    return ItemView(serial=serial, graphic=0x0E75, amount=1, pos=Position(),
                    container=1, layer=BACKPACK_LAYER, distance=0)


def _pack_ingot(serial, amount, bp=BACKPACK):
    return ItemView(serial=serial, graphic=INGOT_GRAPHIC, amount=amount, pos=Position(),
                    container=bp, layer=0, distance=0)


def _ground_ingot(serial, amount, distance):
    return ItemView(serial=serial, graphic=INGOT_GRAPHIC, amount=amount,
                    pos=Position(10, 10, 0), container=None, layer=0, distance=distance)


def _ctx(gumps=(), state=None, items=(), journal=(), memory=None, pos=Position(0, 0, 0)):
    mem = memory if memory is not None else ({} if state is None else {"bs_state": state})
    obs = Observation(player=PlayerView(serial=1, pos=pos),
                      items=[_tool(), *items], gumps=list(gumps), new_journal=list(journal))
    return SkillContext(obs=obs, persona=Persona(name="T"), memory=mem)


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
