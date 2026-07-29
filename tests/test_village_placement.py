"""The pipeline village's PLACEMENT invariants.

These exist because of a live failure that cost a long time to read correctly: the
artisan's capability planner selected `sell_tongs` on every tick, held the goal, and
still never sold anything — because the vendor had been dropped on an arbitrary
`hunting_spot + 8` tile of the Minoc ridge, which a live probe later showed to be
unwalkable in all four directions (0 tiles moved in 16 real steps).

Both legs that carry the pipeline walk GREEDILY, with no A* — the shop trip
(`market._market_walk_toward`) and the delivery walk (`woodwork.DeliverBoards`, "walks
greedily to within one tile of the drop"). That makes an unreachable tile look exactly
like a broken brain from the outside, so the geometry is worth pinning in a test that
runs offline, where a live re-run is expensive.

What is NOT claimed here: no unit test can prove a tile is walkable — that took real
probe walks against the live shard. These pin the *relationships* between the tiles, so
a later edit can't quietly reintroduce a route-shaped assumption the walk can't satisfy.
"""

from anima2.geometry import chebyshev
from anima2.contract import Position
from anima2.profession import TRADE_SMITH_SPOT, VENDOR_SPOT
from anima2.skills.market import BUY_REACH, SELL_REACH
from anima2.skills.tinkering import DeliverGold
from anima2.village import (
    ARTISAN_STAND,
    ARTISAN_VENDOR_ROUTE,
    MAGE_BANKER,
    MAGE_DROP,
    MAGE_STAND,
    MAGE_VENDOR,
    PREY_SPOT,
)


def _cheb(a, b):
    return chebyshev(Position(a[0], a[1], 0), Position(b[0], b[1], 0))


def test_the_artisan_stands_and_sells_on_ground_a_live_gate_already_proved():
    # Not a fresh guess: this is the exact stand + curated route `live_sell_goal.py`
    # sells on, which is the whole reason it is trusted.
    assert ARTISAN_STAND == TRADE_SMITH_SPOT
    assert ARTISAN_VENDOR_ROUTE == VENDOR_SPOT


def test_the_artisan_s_vendor_is_configured_as_a_route_and_lands_within_reach():
    # The regression itself: the old staging handed the artisan a bare point on ground
    # no walk could cross. Two independent things make this one safe, and both are
    # pinned because either alone would be fragile.
    assert len(ARTISAN_VENDOR_ROUTE) > 1, "a one-point vendor spot re-imports the bug"
    # And the layout is co-located: the vendor sits inside `SELL_REACH`/`BUY_REACH` of
    # the stand, so `_walk_route`'s final-reach check short-circuits and the trip needs
    # no crossing at all — the property that makes this staging robust rather than
    # merely calibrated (`_walk_route` documents this exact trade-smithy case).
    assert _cheb(ARTISAN_STAND, ARTISAN_VENDOR_ROUTE[-1]) <= min(SELL_REACH, BUY_REACH)


def test_the_drop_sits_within_a_delivery_walk_of_both_sides():
    # `DeliverGold` walks to within ONE tile of the drop and the mage's `fetch_gold`
    # picks it up off the ground, so the purse has to be a short walk from each stand
    # rather than merely "somewhere between them".
    assert _cheb(ARTISAN_STAND, MAGE_DROP) <= 3
    assert _cheb(MAGE_STAND, MAGE_DROP) <= 2
    # It must also be a real handover point, not a tile someone is standing on.
    assert MAGE_DROP not in (ARTISAN_STAND, MAGE_STAND)


def test_the_delivery_threshold_is_reachable_from_one_batch_of_wares():
    # A placement that is walkable but never triggers is still a dead pipeline: the
    # artisan must be able to clear `deliver_threshold` from what selling wares pays.
    assert DeliverGold.deliver_threshold <= 200, (
        "a threshold above a few batches' takings would stall the handover even with "
        "perfect placement"
    )


def test_the_mage_s_shops_are_adjacent_and_distinct():
    for spot in (MAGE_VENDOR, MAGE_BANKER):
        assert _cheb(MAGE_STAND, spot) <= 2, "a shop out of reach re-imports the bug"
    assert MAGE_VENDOR != MAGE_BANKER, "two NPCs cannot share one tile"


def test_pinned_prey_cannot_reach_the_artisan_but_can_reach_the_mage():
    # Prey is staged with `[Set CantWalk true`, so distance alone decides who it can
    # touch. Live-caught: an artisan 2 tiles from a pinned Ettin produced nothing for
    # 900 ticks, because `Survive` preempted crafting — inside melee reach even pinned.
    assert _cheb(PREY_SPOT, ARTISAN_STAND) > 2, "the artisan would be preempted by Survive"
    assert _cheb(PREY_SPOT, MAGE_STAND) <= 4, "the mage still needs something to cast at"


def test_no_two_occupied_tiles_collide():
    # Every staged occupant needs its own tile; an NPC settling onto a waypoint has
    # already been live-caught denying every step through it.
    occupied = [ARTISAN_STAND, MAGE_STAND, MAGE_VENDOR, MAGE_BANKER, PREY_SPOT,
                ARTISAN_VENDOR_ROUTE[-1]]
    assert len(set(occupied)) == len(occupied)
    # ...and nothing may sit on the drop tile, or the purse becomes unpickable.
    assert MAGE_DROP not in occupied


# --- worker liveness ---------------------------------------------------------------
#
# A worker that has ENDED and one that is merely idle look identical from outside: the
# monitor reprints whatever `status[idx]` last held. That cost several live runs and a
# wrong conclusion — a throttled mage spent its tick budget early, and its frozen last
# observation was read for three runs as "the mage cannot see the delivered purse",
# when in truth it had stopped observing before the purse ever landed.

from anima2.village import _ThrottledAgent  # noqa: E402


class _CountingAgent:
    """Minimal Agent stand-in that records how many real ticks it received."""

    def __init__(self):
        self.real_ticks = 0

    def tick(self):
        self.real_ticks += 1
        return None


def test_a_throttled_agent_reports_what_its_yielded_ticks_cost():
    # The budget scale is what lets a caller keep a throttled agent alive as long as an
    # unthrottled peer; without it the throttled one ends `every` times sooner.
    inner = _CountingAgent()
    agent = _ThrottledAgent(inner, every=8)
    agent.yield_pause_s = 0  # the pause is a live scheduling point, not under test
    assert agent.tick_budget_scale == 8
    for _ in range(8 * 10):
        agent.tick()
    assert inner.real_ticks == 10, "1 real tick per 8 iterations"


def test_an_unthrottled_agent_has_a_neutral_budget_scale():
    # The village reads this with getattr(..., 1), so a plain Agent must be unaffected.
    assert getattr(_CountingAgent(), "tick_budget_scale", 1) == 1


def test_scaling_gives_a_throttled_agent_the_same_real_tick_count():
    # The property under test: budget * scale real-ticks == the peer's tick count.
    ticks = 200
    inner = _CountingAgent()
    agent = _ThrottledAgent(inner, every=8)
    agent.yield_pause_s = 0  # the pause is a live scheduling point, not under test
    for _ in range(ticks * agent.tick_budget_scale):
        agent.tick()
    assert inner.real_ticks == ticks


# --- monitoring --------------------------------------------------------------------
#
# A spectator CANNOT be a second login of the same character: ServUO's character-select
# handler disposes the previous `NetState`, so a "monitor login" would kick the agent
# off its own body. The viewer therefore attaches to the session the agent bridge
# already owns, which means one viewer per agent — there is no way to watch several
# characters through one connection.

from anima2.village import MONITOR_PORT_BASE, _monitor_ports  # noqa: E402


def test_monitoring_off_gives_every_agent_no_port():
    assert _monitor_ports(False, ["tinker", "mage"]) == {"tinker": None, "mage": None}


def test_each_agent_gets_its_own_port():
    ports = _monitor_ports(True, ["tinker", "mage"])
    assert ports == {"tinker": MONITOR_PORT_BASE, "mage": MONITOR_PORT_BASE + 1}
    # Distinct is the whole point: two bridges cannot share one HTTP port, and two
    # characters cannot share one session.
    assert len(set(ports.values())) == len(ports)


def test_ports_stay_distinct_across_a_larger_roster():
    ports = _monitor_ports(True, [f"w{i}" for i in range(5)])
    assert len(set(ports.values())) == 5
    assert min(ports.values()) == MONITOR_PORT_BASE


def test_an_empty_roster_is_not_an_error():
    assert _monitor_ports(True, []) == {}


def test_idle_wandering_cannot_carry_the_mage_into_melee_reach():
    # Live-caught with the prey VERIFIED pinned: a leash of 3 let the mage drift to one
    # tile from a creature it could no longer hurt, and it was beaten to death standing
    # next to it. `ArmedHunt` stops it ENGAGING; only the leash stops it wandering in.
    from anima2.skills.mage import KeepDistance
    from anima2.village import MAGE_LEASH

    closest = _cheb(MAGE_DROP, PREY_SPOT) - MAGE_LEASH
    assert closest >= KeepDistance.too_close + 1, (
        f"idle wandering reaches within {closest} tiles of the prey — inside the band "
        "KeepDistance is meant to hold"
    )


def test_the_leash_is_derived_from_the_layout_not_hand_picked():
    # If the drop or the prey moves, the leash must follow rather than silently go stale.
    from anima2.skills.mage import KeepDistance
    from anima2.village import MAGE_LEASH

    expected = max(1, _cheb(PREY_SPOT, MAGE_DROP) - (KeepDistance.too_close + 1))
    assert MAGE_LEASH == expected
    assert MAGE_LEASH >= 1, "a zero leash would pin the mage to one tile"


def test_the_mage_can_still_reach_its_shops_while_leashed():
    # The leash bounds IDLE wandering only, but a shop it can never walk to is useless:
    # its economy route must stay within reach of where it actually stands.
    from anima2.village import MAGE_LEASH

    for spot in (MAGE_VENDOR, MAGE_BANKER, MAGE_DROP):
        assert _cheb(MAGE_STAND, spot) <= MAGE_LEASH + 3, f"{spot} is out of practical reach"
