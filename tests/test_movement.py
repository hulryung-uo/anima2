"""GoTo delegates to WalkTo (the body's A* route driver) and monitors progress
purely from position deltas across observations, falling back to greedy
`Walk`-by-`Walk` stepping if the route makes no progress at all — see
`anima2/skills/movement.py::GoTo`'s own docstring for the full design.
"""

from anima2.contract import Observation, PlayerView, Position, Walk, WalkTo
from anima2.persona import Persona
from anima2.skills.base import Goal, SkillContext, Status
from anima2.skills.movement import GoTo


def _ctx(pos: tuple[int, int], target: tuple[int, int], memory: dict) -> SkillContext:
    obs = Observation(player=PlayerView(serial=1, pos=Position(*pos)))
    goal = Goal(kind="goto", params={"target": Position(*target)})
    return SkillContext(obs=obs, persona=Persona(name="T"), goal=goal, memory=memory)


def test_goto_emits_walk_to_once_then_monitors_with_no_action():
    """The first tick of a new goto goal issues exactly one `WalkTo`; while
    nothing has changed yet, later ticks send no action at all — the route
    already in flight advances on its own via the body's own pump cadence."""
    mem: dict = {}
    skill = GoTo()

    first = skill.step(_ctx((100, 100), (110, 100), mem))
    assert first.status is Status.RUNNING
    assert isinstance(first.action, WalkTo)
    assert (first.action.x, first.action.y) == (110, 100)
    assert mem["goto_mode"] == "walkto"

    second = skill.step(_ctx((100, 100), (110, 100), mem))
    assert second.status is Status.RUNNING
    assert second.action is None
    assert mem["goto_mode"] == "walkto"


def test_goto_walkto_progress_resets_stall_and_arrives():
    """Steady per-tick progress (as the real bridge's `pump`-driven route
    produces) never re-sends `WalkTo` and never touches the stall counter;
    arrival is an exact tile match, terminal SUCCESS with reward."""
    mem: dict = {}
    skill = GoTo()
    skill.step(_ctx((100, 100), (110, 100), mem))  # issues the one WalkTo

    for x in range(101, 110):
        res = skill.step(_ctx((x, 100), (110, 100), mem))
        assert res.status is Status.RUNNING
        assert res.action is None  # never re-issues while making progress
        assert mem["goto_walkto_stall"] == 0

    arrived = skill.step(_ctx((110, 100), (110, 100), mem))
    assert arrived.status is Status.SUCCESS
    assert arrived.action is None
    assert arrived.reward == 1.0
    # Transient bookkeeping is gone; `goto_mode` survives as a breadcrumb
    # (this attempt finished entirely on the WalkTo route, never fell back).
    assert mem.get("goto_mode") == "walkto"
    assert "goto_walkto_last_pos" not in mem
    assert "goto_target" not in mem


def test_goto_walkto_stall_reissues_once_before_falling_back():
    """No improvement for `walkto_stall_limit` ticks is a bounded retry
    (fresh `WalkTo`, still walkto mode) — not an immediate fallback."""
    mem: dict = {}
    skill = GoTo()
    first = skill.step(_ctx((100, 100), (110, 100), mem))
    assert isinstance(first.action, WalkTo)

    for _ in range(skill.walkto_stall_limit - 1):
        res = skill.step(_ctx((100, 100), (110, 100), mem))
        assert res.action is None

    retry = skill.step(_ctx((100, 100), (110, 100), mem))
    assert isinstance(retry.action, WalkTo)
    assert (retry.action.x, retry.action.y) == (110, 100)
    assert mem["goto_mode"] == "walkto"
    assert mem["goto_walkto_retries"] == 1


def test_goto_falls_back_to_greedy_when_walkto_makes_no_progress_at_all():
    """The MockBody case: `WalkTo` is a silent no-op, position never moves.
    Bounded retries exhaust and GoTo falls back to `Walk`-by-`Walk` stepping,
    observably (`ctx.memory['goto_mode'] == 'greedy'`)."""
    mem: dict = {}
    skill = GoTo()
    saw_walk_to = False
    saw_walk = False
    for _ in range(30):
        result = skill.step(_ctx((100, 100), (110, 100), mem))
        if isinstance(result.action, WalkTo):
            saw_walk_to = True
        if isinstance(result.action, Walk):
            saw_walk = True
            break
    assert saw_walk_to, "GoTo must try the A* route first"
    assert saw_walk, "...then fall back to greedy stepping once it's clearly going nowhere"
    assert mem["goto_mode"] == "greedy"


def test_goto_tolerates_a_route_that_moves_away_before_curving_back():
    """A real A* detour around an obstacle routinely moves *away* from the
    target for a while before curving back (live-confirmed on the calibrated
    Minoc-ridge course — see `live_navigate.py`). Distance-to-target must NOT
    be the progress signal (it would misread the detour as a stall and
    abandon a working route) — only "did the tile position change at all"
    counts. This must stay in walkto mode the whole time, never falling back."""
    mem: dict = {}
    skill = GoTo()
    skill.step(_ctx((100, 100), (110, 100), mem))  # dist 10, issues WalkTo
    # The route arcs *away* first (dist climbs 10 -> 15) — still real
    # movement every tick, so this must never be read as a stall.
    detour = [(99, 100), (98, 101), (97, 103), (95, 105)]
    for x, y in detour:
        res = skill.step(_ctx((x, y), (110, 100), mem))
        assert res.status is Status.RUNNING
        assert res.action is None
        assert mem["goto_walkto_stall"] == 0
        assert mem["goto_mode"] == "walkto"
    # ...then curves back and arrives.
    arrived = skill.step(_ctx((110, 100), (110, 100), mem))
    assert arrived.status is Status.SUCCESS
    assert mem.get("goto_mode") == "walkto"  # never fell back


def test_goto_falls_back_after_partial_progress_then_a_real_stall():
    """A route that makes *some* progress and then genuinely stalls (not the
    zero-progress-ever case) still retries-then-falls-back, from wherever it
    actually got stuck — not from the original start."""
    mem: dict = {}
    skill = GoTo()
    skill.step(_ctx((100, 100), (110, 100), mem))  # dist 10, issues WalkTo
    # Three real steps of progress...
    for x in (101, 102, 103):
        res = skill.step(_ctx((x, 100), (110, 100), mem))
        assert res.action is None
    assert mem["goto_walkto_last_pos"] == (103, 100)
    # ...then the route plateaus at (103, 100) for good (a real dead end).
    saw_walk = False
    for _ in range(30):
        result = skill.step(_ctx((103, 100), (110, 100), mem))
        if isinstance(result.action, Walk):
            saw_walk = True
            break
    assert saw_walk
    assert mem["goto_mode"] == "greedy"


def test_goto_resets_state_when_the_target_changes_mid_flight():
    """Cognition can hand GoTo a new goto goal before the previous one
    finished (see `test_agent_loop.py`'s speak-does-not-drop-goto case) — a
    changed target must restart the WalkTo probe fresh, not inherit the old
    target's stall/retry bookkeeping."""
    mem: dict = {}
    skill = GoTo()
    skill.step(_ctx((100, 100), (110, 100), mem))
    skill.step(_ctx((100, 100), (110, 100), mem))  # one stall tick recorded
    assert mem["goto_walkto_stall"] == 1

    switched = skill.step(_ctx((100, 100), (90, 100), mem))
    assert isinstance(switched.action, WalkTo)
    assert (switched.action.x, switched.action.y) == (90, 100)
    assert mem["goto_target"] == (90, 100)
    assert mem["goto_walkto_stall"] == 0
    assert mem.get("goto_walkto_retries", 0) == 0


def test_goto_use_walkto_false_never_issues_walk_to():
    """`GoTo` with `use_walkto = False` — the control-run escape hatch for the
    greedy-vs-WalkTo differential proof (`live_navigate.py`) — behaves
    byte-for-byte like the pre-A* skill: pure greedy `Walk` stepping, no
    `WalkTo` ever emitted, same stall-bounded FAILURE on a dead end."""
    mem: dict = {}
    skill = GoTo()
    skill.use_walkto = False
    first = skill.step(_ctx((100, 100), (110, 100), mem))
    assert isinstance(first.action, Walk)
    assert mem["goto_mode"] == "greedy"

    result = None
    for _ in range(skill.stall_limit + 1):
        result = skill.step(_ctx((100, 100), (110, 100), mem))  # never moves
        if result.status is not Status.RUNNING:
            break
    assert result.status is Status.FAILURE
    assert mem["goto_mode"] == "greedy"


def test_goto_wedged_in_greedy_mode_fails_terminally():
    """Once in greedy fallback, sustained no-progress is a genuine dead end —
    FAILURE, same "wedged, let a higher layer re-plan" contract the pre-A*
    version had."""
    mem: dict = {}
    skill = GoTo()
    # Force straight into greedy mode without spending the walkto probe's own
    # bounded budget (isolates *this* test's behaviour from that one).
    mem["goto_target"] = (110, 100)
    mem["goto_mode"] = "greedy"

    result = None
    for _ in range(skill.stall_limit + 1):
        result = skill.step(_ctx((100, 100), (110, 100), mem))
        if result.status is not Status.RUNNING:
            break
    assert result.status is Status.FAILURE
    assert result.action is None
    # Terminal cleanup: no transient greedy bookkeeping left behind.
    assert "goto_stall" not in mem
    assert "goto_last_pos" not in mem


# --- Wander's optional leash ------------------------------------------------------
#
# Unbounded wandering silently breaks anything that needs an agent to be where it
# lives. Live-caught in the artisan+mage pipeline: with its prey dead the mage
# wandered ~15 tiles off its plaza, so it never observed the 140-gold purse the
# artisan had just delivered to its funding spot, and the production chain stopped
# closing even though every capability involved was behaving correctly.

from anima2.geometry import chebyshev  # noqa: E402
from anima2.skills.movement import Wander  # noqa: E402


def _wctx(pos: tuple[int, int], memory: dict) -> SkillContext:
    obs = Observation(player=PlayerView(serial=1, pos=Position(*pos)))
    return SkillContext(obs=obs, persona=Persona(name="T"), memory=memory)


def _walk_until_settled(start, memory, ticks=40):
    """Drive Wander for a while, moving one tile per emitted step (an unobstructed
    world), and report every tile visited."""
    from anima2.geometry import DIRECTION_DELTAS

    x, y = start
    seen = [(x, y)]
    skill = Wander()
    for _ in range(ticks):
        res = skill.step(_wctx((x, y), memory))
        dx, dy = DIRECTION_DELTAS[res.action.dir]
        x, y = x + dx, y + dy
        seen.append((x, y))
    return seen


def test_wander_without_a_home_is_unchanged():
    # The no-op guarantee: no `wander_home`, no leash, same steady drift as always.
    mem: dict = {}
    seen = _walk_until_settled((100, 100), mem)
    assert max(abs(px - 100) for px, _ in seen) > Wander.leash, (
        "an unleashed wander must still be free to walk away"
    )


def test_a_leashed_wander_stays_near_home():
    home = (100, 100)
    mem = {"wander_home": home}
    seen = _walk_until_settled(home, mem, ticks=60)
    worst = max(chebyshev(Position(px, py), Position(*home)) for px, py in seen)
    # It may cross the leash — the check happens on the step AFTER — but it must be
    # pulled back rather than drifting away forever.
    assert worst <= Wander.leash + 2, f"wandered {worst} tiles from home"
    assert chebyshev(Position(*seen[-1]), Position(*home)) <= Wander.leash + 2


def test_a_leashed_wander_walks_back_when_it_starts_outside():
    # The live case: already far away when the leash is applied.
    home = (100, 100)
    mem = {"wander_home": home}
    start = (140, 100)
    seen = _walk_until_settled(start, mem, ticks=25)
    assert chebyshev(Position(*seen[-1]), Position(*home)) < chebyshev(
        Position(*start), Position(*home)), "a leashed wanderer must close on home"


def test_a_malformed_home_is_ignored_rather_than_crashing():
    # Memory is scratch space many things write to; a bad value must not wedge the
    # default "be alive" skill.
    for bad in (None, (), (1,), "100,100", (1.5, 2.0), (True, False), [None, None]):
        res = Wander().step(_wctx((100, 100), {"wander_home": bad}))
        assert isinstance(res.action, Walk)


def test_a_blocked_leashed_wanderer_still_rotates_free():
    # Steering home every tick would pin an agent against an obstacle forever, so the
    # leash yields to the stuck-rotation once movement actually stalls.
    mem = {"wander_home": (100, 100)}
    skill = Wander()
    dirs = [skill.step(_wctx((140, 100), mem)).action.dir for _ in range(6)]
    assert len(set(dirs)) > 1, "a wedged leashed wanderer must try another direction"


def test_the_leash_length_is_overridable_per_agent():
    # How tight a leash must be depends on what the agent has to stay close enough to
    # notice, so it is a per-agent memory value rather than a fixed constant.
    home = (100, 100)
    seen = _walk_until_settled(home, {"wander_home": home, "wander_leash": 3}, ticks=60)
    worst = max(chebyshev(Position(px, py), Position(*home)) for px, py in seen)
    assert worst <= 5, f"a 3-tile leash let it reach {worst} tiles"
    assert worst < Wander.leash, "the override must actually bind tighter than the default"


def test_a_malformed_leash_falls_back_to_the_default():
    home = (100, 100)
    for bad in (None, -1, "3", 2.5, True):
        seen = _walk_until_settled(home, {"wander_home": home, "wander_leash": bad}, ticks=40)
        worst = max(chebyshev(Position(px, py), Position(*home)) for px, py in seen)
        assert worst <= Wander.leash + 2, f"leash={bad!r} let it reach {worst} tiles"
