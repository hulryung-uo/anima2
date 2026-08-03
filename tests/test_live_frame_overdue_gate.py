"""The live gate's target, reproduced offline first — this project's stated method.

`anima2/live_frame_overdue_gate.py` claims one thing on a shard: `frame_overdue` became
True, `_repair_overdue_frame` closed the surface that was blocking the yield, and the
hold released — the ONE of the exit-edge hold's three bounds that had zero live ticks
after the 2026-08-03 runs (`docs/AUDIT-2026-07-29.md` §6: `FRAME OVERDUE` printed 0
times in 306 status samples). A gate is only worth live budget if the thing it looks for
has already been produced offline, so these tests drive the SAME path over `MockBody`
and judge it with the gate's OWN `FrameOverdueWatch` — the verdict the shard run
publishes is the verdict a mutant already died against here.

Two things are simulated, and only two, because MockBody has no craft-gump server side:
the gump itself (injected, standing in for the one `Use(tool)` opens live) and the wound
(a `hits` write, standing in for `[Set RawStr` + `[Set Hits`). Everything else —
`TinkerLife`, the capability registry, `GoalStack.expire_due`, the hold — is production
code.
"""

import pytest

from anima2.contract import GumpView, ItemView, PlayerView, Position
from anima2.live_frame_overdue_gate import FrameOverdueWatch
from anima2.mock_body import MockBody
from anima2.persona import Persona
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.smelt import INGOT_GRAPHICS
from anima2.skills.tinkering import TINKERTOOLS_GRAPHICS, TinkerTongs
from anima2.skills.warrior import BANDAGE_GRAPHIC
from anima2.tinker_life import TinkerLife

PLAYER, BP = 0x1, 0x50
IRON = sorted(INGOT_GRAPHICS)[0]
TOOL = sorted(TINKERTOOLS_GRAPHICS)[0]
CRAFT_SPOT = (5, 5)
#: Where the live gate's `[Set X` puts the character — outside `craft_spot_radius`, so
#: the tinker's own rule answers "hunt" and the mode can only be the ORCHESTRATOR's.
AWAY = Position(CRAFT_SPOT[0] + 8, CRAFT_SPOT[1], 0)
#: 4.8% of the bar, the same fraction `[Set RawStr 2000` + `[Set Hits 50` buys live.
WOUNDED_HITS = 4


def _item(serial, graphic, amount=1, *, container=BP, layer=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=0)


def _tinker_on_its_craft_spot():
    """The gate's staging: a tinker with a tool, a batch of iron and bandages, standing
    on its craft spot with NO vendor and NO banker — so `craft_tongs` is the only branch
    its rule can reach, exactly as `main()` stages it (no `stage_shops` call)."""
    body = MockBody(player=PlayerView(serial=PLAYER, name="Pim",
                                      pos=Position(*CRAFT_SPOT, 0),
                                      hits=80, hits_max=80, body=0x190))
    body.items[BP] = _item(BP, 0x0E75, container=PLAYER, layer=BACKPACK_LAYER)
    for it in (_item(0x900, TOOL),
               _item(0x901, IRON, TinkerTongs.craft_material_per_item
                     * TinkerTongs.craft_batch * 4),
               _item(0x950, BANDAGE_GRAPHIC, 50)):
        body.items[it.serial] = it
    life = TinkerLife(body=body, persona=Persona(name="Pim"), routes={})
    for memory in (life.memory, life.econ_agent.memory):
        memory["craft_spot"] = CRAFT_SPOT
    life.set_leash(CRAFT_SPOT, 2)
    return body, life


def _reach_the_craft_frame(life, limit=40):
    for _ in range(limit):
        life.tick()
        frame = life.econ_agent.goal_stack.current
        if frame is not None and frame.goal.params.get("capability") == "craft_tongs":
            return frame
    return None


def _arm(body, life, *, surface: bool, starve: bool, teleport: bool):
    """Perform the gate's three staging acts, in the gate's own order.

    `surface` injects the gump `Use(tool)` opens live; `starve` is the wound that takes
    the hands away from the capability FSM (`Survive` is `skills[0]` of the capability
    planner); `teleport` is the `[Set X` that makes the rule answer "hunt" so the mode
    becomes the orchestrator's to drop.
    """
    if surface:
        body.gumps = [GumpView(serial=0xABCD, gump_id=0x5AFE)]
    if starve:
        body.player.hits = WOUNDED_HITS
    if teleport:
        body.player.pos = AWAY
        life.set_leash((AWAY.x, AWAY.y), 2)
    watch = FrameOverdueWatch(life=life)
    watch.arm(body.observe())
    return watch


def _drive(life, watch, frame, extra=12):
    budget = watch.deadline_tick - frame.created_tick
    for _ in range(budget + extra):
        life.tick()
        watch.record()
        if watch.released_at is not None:
            break
    return budget


# --- the gate's target, produced offline ---------------------------------------------

def test_the_gate_reaches_bound_three_on_the_path_it_drives_live():
    """Every flag the live gate prints, produced by the same staging over MockBody.

    This is the whole point of building the gate this way: the judgement is a pure
    function of a recorded trace, so the trace can be produced without a shard and the
    live run becomes confirmation rather than experiment.
    """
    body, life = _tinker_on_its_craft_spot()
    frame = _reach_the_craft_frame(life)
    assert frame is not None, "the craft frame the whole gate hangs on was never admitted"
    watch = _arm(body, life, surface=True, starve=True, teleport=True)
    _drive(life, watch, frame)

    assert watch.flags() == {
        "staged_craft_gump": True,
        "fsm_starved": True,
        "hold_engaged": True,
        "went_overdue": True,
        "repair_closed_surface": True,
        "hold_released": True,
        "not_the_detector": True,
    }, watch.flags()
    # And the exact tick arithmetic the flags encode, spelled out once so a future
    # reader does not have to reconstruct it from the predicate:
    assert watch.overdue_at[1] == watch.deadline_tick + 1
    assert watch.repair_at == watch.overdue_at
    assert watch.released_at[1] == watch.overdue_at[1] + 1
    assert watch.extension() == 1 == watch.closes_while_overdue
    assert not body.gumps, "the surface that blocked every yield was never closed"
    # The documented worst case, and the CORRECT end state: `_craft_can_yield` still
    # refuses on `started and not terminal` once the gump is gone, so the Life ends with
    # a stale frame and its hands back — "a stale frame, but alive", not a retirement.
    assert life.econ_agent.goal_stack.current is not None
    assert life.mode == "hunt" and life.frame_overdue is True


def test_the_one_tick_race_is_why_this_gate_needs_forced_state():
    """The control, and the honest near-miss: a frame that CAN yield leaves on bound 2.

    `expire_due` fires at `ticks >= deadline_tick`; `frame_overdue` needs
    `ticks > deadline_tick`. So with no surface and an FSM that is being stepped — an
    ordinary craft run, which aborts at `max_goal_steps` (240) well inside its 300-tick
    budget and closes its own gump — the deadline wins by one tick and bound 3 never
    runs. This is what the 2026-08-03 forge run produced at 292/300 and 177/180, and it
    is why waiting for bound 3 to happen by itself is not a plan.
    """
    body, life = _tinker_on_its_craft_spot()
    frame = _reach_the_craft_frame(life)
    assert frame is not None
    watch = _arm(body, life, surface=False, starve=False, teleport=True)
    budget = watch.deadline_tick - frame.created_tick
    retired_at = None
    for tick in range(1, budget + 12):
        life.tick()
        watch.record()
        if life.econ_agent.goal_stack.current is None:
            retired_at = (tick, life.econ_agent.ticks)
            break
    assert retired_at is not None, "bound 2 did not close this frame either"
    assert watch.overdue_at is None, (
        "a yieldable frame must expire ON its deadline, one tick before `frame_overdue` "
        "could ever be true — if this fires the race has changed and the gate's whole "
        "staging argument needs redoing")
    assert [(f.goal.params.get("capability"), f.outcome.value)
            for f in life.econ_agent.goal_stack.history] == [("craft_tongs", "expired")]
    assert watch.flags()["went_overdue"] is False
    assert getattr(life, "_stale_ui_closes", 0) == 0


def test_the_extra_tick_is_the_repair_and_the_gate_can_tell_the_difference():
    """The release half needs no surface; the repair half is what buys the extra tick.

    Same starve, same teleport, no gump: `frame_overdue` still fires and the hold still
    drops — the release must not depend on there being anything to close. But
    `_repair_overdue_frame` finds nothing, returns False, and the hold drops on the SAME
    economy tick it went overdue rather than one later. That one tick is the gate's
    numeric evidence that the repair fired at all, which is why `hold_released` asserts
    `overdue + 1` and not merely "the mode went to hunt".
    """
    body, life = _tinker_on_its_craft_spot()
    frame = _reach_the_craft_frame(life)
    assert frame is not None
    watch = _arm(body, life, surface=False, starve=True, teleport=True)
    _drive(life, watch, frame)

    assert watch.overdue_at is not None and watch.released_at is not None
    assert watch.released_at[1] == watch.overdue_at[1], (
        "with no surface to close the hold must drop on the overdue tick itself")
    assert getattr(life, "_stale_ui_closes", 0) == 0
    flags = watch.flags()
    assert flags["went_overdue"] is True and flags["hold_engaged"] is True
    assert flags["repair_closed_surface"] is False, "there was nothing to close"
    assert flags["hold_released"] is False, (
        "and the gate must NOT report a repaired release when no repair happened — "
        "this is the flag that would otherwise pass on the cheap half of the bound")


def test_a_run_whose_fsm_kept_stepping_cannot_pass_the_staging_self_check():
    """`fsm_starved` is the flag that makes a pass mean something.

    If the capability FSM is still being stepped it answers its own gump, and any
    overdue that follows was reached some other way than the one the gate's argument
    describes. Same world, no wound: the FSM runs to `max_goal_steps`, `cap_craft_steps`
    climbs, and the self-check refuses the run — even though the injected gump still
    blocks every yield and `frame_overdue` does fire.
    """
    body, life = _tinker_on_its_craft_spot()
    frame = _reach_the_craft_frame(life)
    assert frame is not None
    watch = _arm(body, life, surface=True, starve=False, teleport=True)
    _drive(life, watch, frame)

    assert len(watch.craft_steps_seen) > 1, "this world no longer steps the FSM at all"
    assert watch.flags()["fsm_starved"] is False
    # And it really did reach the bound — so the self-check is doing work no other flag
    # does, rather than being entailed by a failure somewhere else.
    assert watch.flags()["went_overdue"] is True


def test_the_watch_never_credits_a_moment_it_did_not_see():
    """A watch that recorded nothing must fail every flag, not vacuously pass one.

    `fsm_starved` is the trap: "the steps never changed" is also true of a run that
    never armed, and a `<=` comparison would have passed it. The empty watch is the
    cheapest possible mutant and it must die.
    """
    body, life = _tinker_on_its_craft_spot()
    watch = FrameOverdueWatch(life=life)
    assert watch.flags() == {
        "staged_craft_gump": False,
        "fsm_starved": False,
        "hold_engaged": False,
        "went_overdue": False,
        "repair_closed_surface": False,
        "hold_released": False,
        # The one flag that is honestly true of a run that never happened: no
        # disagreement was reported. It is a NEGATIVE check (attribution, not
        # achievement), so it cannot carry a verdict on its own.
        "not_the_detector": True,
    }


@pytest.mark.parametrize("surface,starve,teleport,expected", [
    (True, True, False, "hold_engaged"),
    (False, False, True, "went_overdue"),
])
def test_each_staging_act_is_load_bearing(surface, starve, teleport, expected):
    """Drop one act and a specific flag goes out — so none of the three is decoration.

    Without the TELEPORT the tinker's own rule still wants `craft_tongs`, so the mode is
    economy on the rule's account and there is no hold to release. Without the SURFACE
    and the STARVE together the frame yields and bound 2 takes it.
    """
    body, life = _tinker_on_its_craft_spot()
    frame = _reach_the_craft_frame(life)
    assert frame is not None
    watch = _arm(body, life, surface=surface, starve=starve, teleport=teleport)
    _drive(life, watch, frame)
    assert watch.flags()[expected] is False
