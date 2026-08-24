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

from collections.abc import Mapping

import pytest

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
    stage_key_readout,
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


# --- the status block's own read safety ----------------------------------------------


class _RacyMemory(Mapping):
    """Agent memory with the race made DETERMINISTIC: the key is popped by the very
    membership test that finds it — exactly what a worker thread popping `_CLEANUP_KEYS`
    does if it is scheduled between a `k in m` filter and its `m[k]` subscript.

    Copying it is safe (a copy goes through `keys()`/`__getitem__`, never `__contains__`),
    so the snapshot survives and the unsnapshotted read does not. That asymmetry is the
    whole point: a threads-and-sleeps test of this cannot fail reliably — measured on
    CPython 3.14, 500k+ interleavings produced zero errors — and a test that cannot fail is
    the §35.3 defect, not a proof of safety.
    """

    def __init__(self, data):
        self._d = dict(data)

    def __getitem__(self, k):
        return self._d[k]

    def __iter__(self):
        return iter(self._d)

    def __len__(self):
        return len(self._d)

    def __contains__(self, k):
        present = k in self._d
        self._d.pop(k, None)   # the worker thread, in between the two halves
        return present


def test_stage_key_readout_snapshots_before_reading():
    """The forge pair's stage-key group must not be a check-then-get against live agent
    memory. Unsnapshotted this raises `KeyError`, and the runners' status loops have no
    `except` while their workers are daemons — so it does not lose a line, it ends the run.

    Kills the mutant `snapshot = dict(memory)` -> `snapshot = memory`.
    """
    keys = ("mkt_phase", "sell_stage", "sell_stall")
    memory = _RacyMemory({"mkt_phase": "sell", "sell_stage": "popup", "sell_stall": 3})
    assert stage_key_readout(memory, keys) == "mkt_phase=sell sell_stage=popup sell_stall=3"
    # And the unguarded form this replaced really does die on the same input.
    fresh = _RacyMemory({"mkt_phase": "sell", "sell_stage": "popup"})
    with pytest.raises(KeyError):
        " ".join(f"{k}={fresh[k]}" for k in keys if k in fresh)


def test_grove_spot_pool_skips_home_and_stops_at_the_cap():
    """woodsman-20260818-1941 stood on groves[0] with no next stand to hop to.
    The home grove is already in `harvest_nodes`; seeding it into the pool
    would walk back to the bank that just confessed empty."""
    from anima2.uomap import Static
    from anima2.village import GROVE_POOL_SPOTS, grove_spot_pool

    def tree(x, y):
        return Static(x, y, 0, 0xCCA)

    home = (518, 1042)
    groves = [
        (home, [tree(518, 1041), tree(519, 1041)]),
        ((530, 1042), [tree(530, 1041)]),
        ((540, 1040), [tree(540, 1039)]),
    ]
    pool = grove_spot_pool(groves, home)
    assert [s for s, _ in pool] == [(530, 1042), (540, 1040)]
    assert pool[0][1] == [(530, 1041, 0, 0xCCA)]
    assert grove_spot_pool(groves[:1], home) == []

    many = [((100 + i * 10, 100), [tree(100 + i * 10, 99)]) for i in range(20)]
    capped = grove_spot_pool(many, (100, 100))
    assert len(capped) == GROVE_POOL_SPOTS
    assert (100, 100) not in {s for s, _ in capped}


#: The real Yew survey, `find_tree_clusters(0, 560, 1080)`, in the richest-first order it
#: returns — its first 14 stands after the home grove `(518, 1042)`, with their real tree
#: counts and their real chebyshev distance from home. Not a sketch: `grove_spot_pool`'s
#: whole job is to pick an ITINERARY out of this list, and the §51.2 tape paid the 51-tile
#: first hop that row 0 of it costs.
_YEW_HOME = (518, 1042)
_YEW_SURVEY_RICHEST_FIRST = [
    ((517, 1093), 5), ((610, 1050), 5), ((523, 1087), 4), ((535, 1037), 4),
    ((574, 1037), 4), ((604, 1056), 4), ((541, 1031), 4), ((554, 1031), 4),
    ((580, 1031), 4), ((506, 1025), 4), ((560, 1025), 4), ((524, 1039), 3),
    ((512, 1045), 3), ((556, 1078), 2),
]


def _yew_groves():
    """`find_tree_clusters` output shape for the survey above, home grove included."""
    from anima2.uomap import Static

    def grove(spot, n):
        # Trees sit within harvest reach 2 of the stand; the exact tiles do not matter
        # here, only that each grove carries the number of trees the survey found.
        return (spot, [Static(spot[0] + i - 1, spot[1] - 1, 0, 0xCCA) for i in range(n)])

    return [grove(_YEW_HOME, 5)] + [grove(s, n) for s, n in _YEW_SURVEY_RICHEST_FIRST]


def test_grove_pool_hops_to_the_nearest_grove_not_the_richest():
    """woodsman-20260822-1225 walked 51 tiles on its first hop; 17 was in the pool.

    Audit §51.3: "`grove_spot_pool` is richest-first, so the first hop was 51 tiles
    (pool[3] is 17)." The pool is consumed head-first as a relocation itinerary
    (`Harvest._begin_relocate` does `pool.pop(0)`), so its order IS the walk the day
    pays — and the walk is greedy with a stall give-up, so a longer hop is also more
    likely to wedge. `find_mine_spots` already orders its stands this way for the
    miner ("Nearest-first from (cx, cy), so a relocation hop is as short as the
    terrain allows"); this is the same rule reaching the lumber side.
    """
    from anima2.village import grove_spot_pool

    def d(spot):
        return max(abs(spot[0] - _YEW_HOME[0]), abs(spot[1] - _YEW_HOME[1]))

    pool = grove_spot_pool(_yew_groves(), _YEW_HOME)
    spots = [s for s, _ in pool]

    # The hop the tape actually paid, and the one it should have paid.
    assert d((517, 1093)) == 51 and d((524, 1039)) == 6  # the fixture reaches the case
    assert spots[0] == (524, 1039)

    # The whole itinerary is non-decreasing in distance, not just its head.
    assert [d(s) for s in spots] == sorted(d(s) for s in spots)

    # Ties keep the OLD key: `sorted` is stable, so among equidistant groves the
    # richest still leads. (524, 1039) and (512, 1045) are both 6 tiles out and both
    # carry 3 trees, ahead of no equidistant rival — but the rule is what is pinned.
    assert spots[:2] == [(524, 1039), (512, 1045)]

    # The cap now spends its 12 slots on near groves. (610, 1050) is the richest grove
    # in the survey after home — five trees — and 92 tiles away; it is exactly what a
    # relocation itinerary should not be buying, and richest-first put it at index 1.
    assert (610, 1050) not in spots
    assert (604, 1056) not in spots  # the other >60-tile stand the cap now drops
    assert (512, 1045) in spots      # 3 trees at 6 tiles, which richest-first cut


def test_harvest_aim_readout_splits_a_stale_grove_from_a_silent_new_one():
    """woodsman-20260822-1225: after `reloc=(517, 1093)` the tape cannot tell
    whether Chop still aimed at the home trees. `d=` is that split."""
    from anima2.contract import Observation, PlayerView, Position
    from anima2.village import harvest_aim_readout

    home_trees = [(518, 1041, 0, 0xCCA), (519, 1040, 0, 0xCCA)]
    new_trees = [(517, 1092, 0, 0xCCA), (516, 1093, 0, 0xCCA)]
    here = Observation(player=PlayerView(serial=1, pos=Position(517, 1093, 0)))
    stale = {"harvest_nodes": home_trees, "harvest_idx": 0}
    fresh = {"harvest_nodes": new_trees, "harvest_idx": 0}
    assert harvest_aim_readout(stale, here) == " tree=(518,1041) d=52"
    assert harvest_aim_readout(fresh, here) == " tree=(517,1092) d=1"
    assert harvest_aim_readout({}, here) == ""


# --- the warrior readout (2026-08-24) ------------------------------------------------


def test_warrior_readout_names_what_stops_a_warrior_living():
    """The first live warrior day this project has a tape for printed
    `— warrior village [Bram0:hunt] —` and nothing else. The warrior bled 125 HP to 3 over
    250 ticks and died, and the tape could not say whether it held a blade, had bandages
    to heal with, or wore any armour — the three facts that decide that fight.

    Field order is `decide_mode`'s own priority (blade, bandages, chest plate, bank), so a
    reader scans it in the order the rule acts on it.
    """
    from anima2.contract import ItemView, Observation, PlayerView, Position
    from anima2.skills.hunt import GOLD_GRAPHIC
    from anima2.skills.warrior import (
        BANDAGE_GRAPHIC,
        PLATE_ARMOR_LAYERS,
        SWORD_RANK,
        WEAPON_LAYER,
    )
    from anima2.village import warrior_readout

    me = 0x1
    best = max(SWORD_RANK, key=lambda g: SWORD_RANK[g])
    # graphic -> layer, in that order. The first version of this test read the pair
    # BACKWARDS, and so did the readout it was testing, so the two agreed and the
    # bug shipped to three live tapes: `plate=0/6` was being computed as
    # `worn[graphic].graphic == layer`, which is nonsense and can only ever be 0.
    # A test that shares its subject's mistake proves the mistake, not the code.
    chest_g, chest_layer = next(iter(PLATE_ARMOR_LAYERS.items()))

    def _it(serial, graphic, layer=0, container=me, amount=1):
        return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                        container=container, layer=layer, distance=0)

    pack = _it(0x50, 0x0E75, layer=0x15)
    obs = Observation(
        player=PlayerView(serial=me, pos=Position(), hits=90, hits_max=125),
        items=[pack,
               _it(0x900, best, layer=WEAPON_LAYER),
               _it(0x901, chest_g, layer=chest_layer),
               _it(0x902, BANDAGE_GRAPHIC, container=pack.serial, amount=87),
               _it(0x903, GOLD_GRAPHIC, container=pack.serial, amount=50)],
    )

    class _Life:
        kills = 2

    out = warrior_readout(_Life(), obs)
    assert f"blade=0x{best:04X}r{SWORD_RANK[best]}" in out, out
    assert f"plate=1/{len(PLATE_ARMOR_LAYERS)}(pack 0)" in out, out
    assert "bandages=87" in out and "gold=50" in out and "kills=2" in out, out

    # A DISARMED warrior is the case the tape could not show, and it must be loud.
    bare = Observation(player=PlayerView(serial=me, pos=Position(), hits=90, hits_max=125),
                       items=[pack])
    bare_out = warrior_readout(_Life(), bare)
    assert "blade=NONE" in bare_out, bare_out
    assert f"plate=0/{len(PLATE_ARMOR_LAYERS)}(pack 0)" in bare_out, bare_out
    assert "bandages=0" in bare_out

    # THE DISCRIMINATOR the first tape lacked: a suit sitting in the pack unworn reads
    # differently from a suit that was never handed over. Both print `plate=0/6`; only
    # the pack count separates a staging failure from an EquipArmor failure, and they
    # have opposite fixes.
    carried = Observation(
        player=PlayerView(serial=me, pos=Position(), hits=90, hits_max=125),
        items=[pack] + [_it(0xA00 + n, g, container=pack.serial)
                        for n, g in enumerate(PLATE_ARMOR_LAYERS)],
    )
    carried_out = warrior_readout(_Life(), carried)
    assert f"plate=0/{len(PLATE_ARMOR_LAYERS)}(pack {len(PLATE_ARMOR_LAYERS)})" in carried_out, \
        carried_out

    # WHO IS HITTING IT. The 2026-08-24 free-tile tape showed a warrior bleed out at
    # `(2577, 398)` — fourteen tiles from a pocket of PINNED prey, which by construction
    # cannot follow. The tape could not name the attacker, so "the retreat did not work"
    # and "something nobody staged is standing on him" were indistinguishable, and they
    # have opposite fixes. Nearest first, because melee reach is 1 and that distance is
    # the one that decides whether a bandage slips.
    from anima2.contract import MobileView

    def _mob(serial, dist, noto, hits=50):
        return MobileView(serial=serial, name="", pos=Position(), body=0,
                          notoriety=noto, hits=hits, hits_max=50, distance=dist)

    assert "foes=none" in out, out  # no mobiles at all in the base fixture
    assert "res=none" in out, out    # ...and no healer wired
    assert " ui=clear" in out, out   # ...and no surface open

    # A CURSOR NOBODY ANSWERS DISABLES `Survive` (`can_run` ends on
    # `pending_target is None`), so the line has to be able to show one.
    from anima2.contract import GumpView, TargetCursor
    snagged = Observation(
        player=PlayerView(serial=me, pos=Position(), hits=40, hits_max=125),
        items=[pack], pending_target=TargetCursor(target_type=0, cursor_id=7, cursor_flag=0),
        gumps=[GumpView(serial=0xABCD, gump_id=0x1234)])
    assert " ui=tgtg1" in warrior_readout(_Life(), snagged), warrior_readout(_Life(), snagged)

    # THE GHOST DISCRIMINATOR. `BACK ALIVE` is 0 on every warrior day and the two causes
    # have opposite owners -- no healer wired (this repo) versus a body that will not walk
    # a ghost (the sibling repo) -- so `res=` must read the spot the way `RecoverDeath`
    # itself does. Runners store `[(x, y)]`; a reader that only understood a bare `(x, y)`
    # would print `none` for a healer that IS wired and send the blame to the wrong repo.
    class _Wired:
        kills = 2
        # The LIFE's own forwarder, which points at whichever inner agent was ticked.
        last_skill_name = "survive"
        hunt_agent = type("A", (), {"memory": {"resurrection_spot": [(2575, 408)]},
                                    # Stale on purpose: in economy mode the hunt agent
                                    # has not run for hundreds of ticks, and reading it
                                    # printed `skill=hunt` through a bank-trip death.
                                    "last_skill_name": "hunt"})()

    wired = Observation(player=PlayerView(serial=me, pos=Position(2587, 408, 0),
                                          hits=0, hits_max=125), items=[pack])
    wired_out = warrior_readout(_Wired(), wired)
    assert " res=(2575,408)@d12" in wired_out, wired_out
    assert " skill=survive" in wired_out, wired_out

    from anima2.skills.combat import is_hostile
    foe_noto = next(n for n in range(1, 8) if is_hostile(_mob(0, 0, n)))
    friend_noto = next(n for n in range(1, 8) if not is_hostile(_mob(0, 0, n)))

    crowded = Observation(
        player=PlayerView(serial=me, pos=Position(), hits=90, hits_max=125),
        items=[pack],
        mobiles=[_mob(0xB03, 7, foe_noto), _mob(0xB01, 1, foe_noto),
                 _mob(0xB02, 4, foe_noto),
                 _mob(me, 0, foe_noto),              # ourself is not a foe
                 _mob(0xB04, 2, friend_noto),        # a Healer standing by is not a foe
                 _mob(0xB05, 1, foe_noto, hits=0),   # a corpse is not a foe
                 _mob(0xB06, 9, foe_noto)],          # capped: nearest three, no more
    )
    # Anchored on BOTH ends: `foes=d1,d4,d7` as a bare substring also matches an
    # uncapped `foes=d1,d4,d7,d9`, so the cap would go untested.
    crowded_out = warrior_readout(_Life(), crowded)
    assert " foes=d1,d4,d7 " in crowded_out, crowded_out

    # ...and it never raises, whatever it is handed.
    assert warrior_readout(object(), None).strip(), "must still say something"
    assert warrior_readout(object(), object()).strip()


class _FakeGm:
    """A GM control plane that can be told to lose a spawn, or to refuse the pin."""

    def __init__(self, *, found=True, pins_after=0):
        self.found, self.pins_after = found, pins_after
        self.sets = 0
        self.commands: list[str] = []

    def command_at(self, cmd, x, y, z):
        self.commands.append(f"{cmd}@({x},{y},{z})")

    def command_on(self, cmd, serial):
        self.commands.append(f"{cmd}#{serial}")
        if cmd.startswith("[Set CantWalk"):
            self.sets += 1

    def get_property_value(self, prop, serial):
        assert prop == "CantWalk"
        return "True" if self.sets > self.pins_after else "False"

    def find_mobile_near(self, x, y, retries=2, exclude=frozenset()):
        self.retries = retries
        self.excluded = set(exclude)
        # Whatever the caller excludes, it does not come back — the real one filters.
        for serial in (0xBEEF, 0xCAFE):
            if serial not in self.excluded:
                return type("M", (), {"serial": serial})() if self.found else None
        return None


def test_prey_that_will_not_pin_is_deleted_not_left_to_roam():
    """`[Set CantWalk true` was sent and never read back, so a prey that did not pin
    became a silent chaser. Measured 2026-08-24 (`warrior-...-foes.log`): the warrior
    stationary at `@(2585, 411)` while its nearest hostiles oscillated `d3,d3,d3 ->
    d1,d3,d3 -> d2,d3,d3` and one read `d0`, on his own tile. Pinned prey cannot move,
    so every retreat the village stages is void against one that was never pinned.
    """
    from anima2.village import stage_pinned_prey

    ok = _FakeGm()
    assert stage_pinned_prey(ok, "Ettin", 10, 20, 0) == "pinned"
    assert "[Delete#48879" not in ok.commands, ok.commands
    # MORE THAN ONE observation. The old spawner looked once, for throughput on the
    # shared control connection, and a miss is what leaves the roamer behind.
    assert ok.retries >= 2, ok.retries

    # Refuses the pin however many times it is asked -> removed, because this spawner
    # runs every monitor cycle and a leftover ACCUMULATES.
    stubborn = _FakeGm(pins_after=99)
    assert stage_pinned_prey(stubborn, "Ettin", 10, 20, 0) == "deleted"
    assert stubborn.sets == 2, "one retry before giving up"
    assert "[Delete#48879" in stubborn.commands, stubborn.commands

    # A pin that takes on the SECOND attempt must not be thrown away.
    flaky = _FakeGm(pins_after=1)
    assert stage_pinned_prey(flaky, "Ettin", 10, 20, 0) == "pinned"
    assert "[Delete#48879" not in flaky.commands, flaky.commands

    # Never found: the creature EXISTS and we have no serial, so it cannot be cleaned
    # up. That is the worse outcome and must be reported as its own thing.
    lost = _FakeGm(found=False)
    assert stage_pinned_prey(lost, "Ettin", 10, 20, 0) == "lost"
    assert not any(c.startswith("[Delete") for c in lost.commands), lost.commands

    # THE EXCLUDE SET IS THE CALLER'S ONLY DEFENCE against re-finding a creature that
    # was already pinned here: `find_mobile_near` returns the NEAREST match, so without
    # it the lookup can pin an old Ettin, report success, and leave the one just added
    # roaming — with `0 lost, 0 deleted` printed over the top (audit §61.10).
    reuse = _FakeGm()
    assert stage_pinned_prey(reuse, "Ettin", 10, 20, 0, exclude={0xBEEF}) == "pinned"
    assert 0xBEEF in reuse.excluded and "[Set CantWalk true#51966" in reuse.commands, \
        reuse.commands


def test_walkable_run_stops_where_the_ground_stops_being_climbable():
    """ServUO allows a land step only when `startZ + StepHeight >= landZ`
    (`Scripts/Services/Pathing/Movement.cs`, `StepHeight = 2`): ascent capped at 2,
    descent unbounded. A vendor trip is a ROUND trip, though, so a 25-tile drop on the
    way out is a 25-tile climb on the way home — the bound has to hold both ways or the
    runner cheerfully places a shop the warrior can reach exactly once.

    Measured 2026-08-24 (audit §58): the warrior pocket at `(2587, 408)` is a z=15
    plateau ringed by cliffs, and all four shops were staged at a flat 12.
    """
    from anima2 import uomap
    from anima2.uomap import STEP_HEIGHT, walkable_run

    # A synthetic ridge, so the assertion does not depend on the shard's map files.
    profile = {0: 15, 1: 15, 2: 16, 3: 18, 4: 28, 5: 30}   # the +10 at step 4

    def fake_cells(map_index, x0, y0, x1, y1):
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                yield x, y, 0x0003, profile.get(y - 100, 15)

    real, uomap.land_cells = uomap.land_cells, fake_cells
    try:
        assert walkable_run(0, 50, 100, 0, 1, 5) == 3, "stops one short of the +10"
        assert walkable_run(0, 50, 100, 0, 1, 2) == 2, "never reports past max_len"
        # Descent is bounded too, and that is the round-trip half of the rule.
        assert STEP_HEIGHT == 2
        profile.update({1: 15 - STEP_HEIGHT - 1})
        assert walkable_run(0, 50, 100, 0, 1, 5) == 0, "a drop is a climb on the way back"

        # OFF THE EDGE OF THE MAP is a stop, not a crash. The box this reads is derived
        # from `max_len`, so a ray that leaves loaded data yields no cell at all.
        holes = {(50, 100): 15, (50, 101): 15}
        uomap.land_cells = lambda mi, x0, y0, x1, y1: (
            (x, y, 0x0003, holes[(x, y)])
            for (x, y) in holes if x0 <= x <= x1 and y0 <= y <= y1)
        assert walkable_run(0, 50, 100, 0, 1, 5) == 1
        assert walkable_run(0, 99, 99, 0, 1, 5) == 0, "no ground under our own feet"
    finally:
        uomap.land_cells = real


def test_the_warrior_pocket_is_walled_in_and_the_map_says_so():
    """The live finding itself, pinned to the real map files so it cannot silently stop
    being true: at `HUNTING_SPOT` no direction allows the fixed 12-tile shop offset."""
    from anima2.profession import HUNTING_SPOT
    from anima2.uomap import walkable_run
    from anima2.village import _VENDOR_GAP, _VENDOR_MIN_GAP

    gx, gy = HUNTING_SPOT
    runs = {name: walkable_run(0, gx, gy, dx, dy, _VENDOR_GAP)
            for name, (dx, dy) in (("weapon", (1, 0)), ("healer", (-1, 0)),
                                   ("banker", (0, 1)), ("armorer", (0, -1)))}
    assert all(n < _VENDOR_GAP for n in runs.values()), (
        f"the pocket stopped being walled in — re-read §58 before trusting the old "
        f"vendor distances: {runs}")
    assert all(n >= _VENDOR_MIN_GAP for n in runs.values()), (
        f"a leg is now unusably short and the runner must say so loudly: {runs}")


def test_a_warrior_between_the_heal_thresholds_still_gets_something_to_fight():
    """The restock gate asked how FULL the warrior was; it has to ask whether the heal
    reflex currently owns him.

    `WarriorSurvive` enters the heal window below `heal_below_fraction` (0.40) and heals
    to `heal_until_fraction` (0.75). Gating the top-up on 0.75 therefore starves a warrior
    anywhere in between — not healing, perfectly able to fight, and fed nothing. Measured
    2026-08-24 (audit §60): after clearing its pocket at hp=96/150 (64%) the warrior had
    no hostile inside `engage_range` and spent the rest of the day in `skill=wander`,
    drifting to `foes=d17`.
    """
    from anima2.contract import ItemView, Observation, PlayerView, Position
    from anima2.skills.warrior import SWORD_GRAPHICS, WEAPON_LAYER, WarriorSurvive
    from anima2.village import ready_to_fight

    me = 0x1
    blade = ItemView(serial=0x900, graphic=sorted(SWORD_GRAPHICS)[0], amount=1,
                     pos=Position(), container=me, layer=WEAPON_LAYER, distance=0)

    class _Life:
        def __init__(self, healing=False):
            self.hunt_agent = type("A", (), {
                "memory": {WarriorSurvive._HEAL_LATCH: True} if healing else {}})()

    def _obs(hits, *, items=(blade,), dead=False):
        return Observation(player=PlayerView(serial=me, pos=Position(), hits=hits,
                                             hits_max=150, dead=dead),
                           items=list(items))

    lo, hi = WarriorSurvive.heal_below_fraction, WarriorSurvive.heal_until_fraction
    between = int(150 * (lo + hi) / 2)
    assert lo < between / 150 < hi, "the fixture must sit BETWEEN the two thresholds"
    assert ready_to_fight(_Life(), _obs(between)), \
        "not healing and armed — feed him"
    assert not ready_to_fight(_Life(healing=True), _obs(between)), \
        "the reflex owns him; top-up beside his live tile makes retreat futile"

    # The clauses that were already right must stay right.
    assert not ready_to_fight(_Life(), _obs(150, items=())), "unarmed is not ready"
    assert not ready_to_fight(_Life(), _obs(150, dead=True)), "a corpse is not ready"
    assert not ready_to_fight(_Life(), None), "unknown reads as NOT ready"
    assert not ready_to_fight(object(), _obs(150)), "and it never raises into the GM loop"


def test_the_warrior_village_fills_in_a_return_reach_a_hunter_can_reach():
    """`WarriorLife.DEFAULT_BANK_RETURN_REACH` is 0 and must stay 0: the carpenter,
    tinker, mage and woodsman all inherit it and all have a workbench their bank trip
    must return to exactly. The hunter does not, and a deposit it walked away from was
    being recorded as a give-up — 904 gold banked, `landed=0/6` (audit §62.4).

    So the value is the VILLAGE's, and a `--knob` still outranks it.
    """
    from anima2.skills.market import BANK_REACH
    from anima2.village import _WARRIOR_BANK_RETURN_REACH, warrior_village_knobs
    from anima2.warrior_life import WarriorLife

    assert WarriorLife.DEFAULT_BANK_RETURN_REACH == 0, "crafters keep the exact tile"
    assert warrior_village_knobs({})["bank_return_reach"] == _WARRIOR_BANK_RETURN_REACH
    assert _WARRIOR_BANK_RETURN_REACH == BANK_REACH, (
        "home and at-the-bank must use one tolerance, or a trip can be closer to "
        "arriving than to leaving")
    # Nothing else is invented, and the caller always wins.
    assert warrior_village_knobs({}).keys() == {"bank_return_reach"}
    assert warrior_village_knobs({"bank_return_reach": 5})["bank_return_reach"] == 5
    assert warrior_village_knobs({"bank_reserve": 400}) == {
        "bank_reserve": 400, "bank_return_reach": _WARRIOR_BANK_RETURN_REACH}


def test_a_walled_in_pocket_is_rescued_and_a_workable_one_is_left_alone():
    """`run_warrior_village` stages its roster along a straight line at `spacing = 25` and
    never asked whether the 25th tile along was usable ground. With one warrior the
    question never came up. Measured 2026-08-25 on the first three-warrior roster (audit
    §64.3): the second pocket landed at `(2612, 408)` where the banker ray is ONE tile
    long, so its economy leg was dead on arrival — and the only reason anyone knew is that
    §58's "walled in" line printed.
    """
    from anima2 import uomap
    from anima2.uomap import find_warrior_stand, walkable_run
    from anima2.village import _VENDOR_GAP, _VENDOR_MIN_GAP

    def _rays(x, y):
        return {n: walkable_run(0, x, y, dx, dy, _VENDOR_GAP)
                for n, (dx, dy) in (("E", (1, 0)), ("W", (-1, 0)),
                                    ("S", (0, 1)), ("N", (0, -1)))}

    # THE PROVEN POCKET DOES NOT MOVE. Its shortest ray is 3 — tight, and it is where two
    # 2000-gold days were measured (§63.4/§63.5). Trading that for a tile or two would be
    # churning the most-tested configuration this project has.
    proven = find_warrior_stand(0, 2587, 408, want=_VENDOR_GAP, floor=_VENDOR_MIN_GAP)
    assert proven == (2587, 408), proven
    assert min(_rays(*proven).values()) >= _VENDOR_MIN_GAP

    # THE UNUSABLE ONE MOVES. `(2637, 408)` is land tile 169 — `Impassable | Wet`, open
    # water — and the GM teleport that stages a warrior ignores passability, so this is a
    # warrior standing in a lake.
    from anima2.uomap import land_walkable
    tile = next(t for x, y, t, _z in uomap.land_cells(0, 2637, 408, 2637, 408))
    assert not land_walkable(tile), f"tile 0x{tile:04X} stopped being water"
    assert min(_rays(2637, 408).values()) < _VENDOR_MIN_GAP
    rescued = find_warrior_stand(0, 2637, 408, want=_VENDOR_GAP, floor=_VENDOR_MIN_GAP)
    assert rescued is not None and rescued != (2637, 408)
    assert min(_rays(*rescued).values()) >= _VENDOR_MIN_GAP, _rays(*rescued)
    # ...and it stays in the neighbourhood, or the roster's spacing stops meaning anything.
    assert max(abs(rescued[0] - 2637), abs(rescued[1] - 408)) <= 20, rescued

    # A NEIGHBOUR'S POCKET IS OFF LIMITS however good the ground is: two stands sharing a
    # wipe radius share prey and contaminate each other's evidence.
    assert find_warrior_stand(0, 2612, 408, want=_VENDOR_GAP, floor=_VENDOR_MIN_GAP,
                              avoid=((2587, 408),), min_gap=25) is None


def test_a_stand_search_with_nowhere_to_go_says_so_instead_of_guessing():
    """`None`, not the spot it was handed. A stand in the middle of a lake is not a
    fallback — a warrior teleported there cannot take one step for the whole run, which
    is precisely what happened (audit §64.4) — so the caller has to be able to skip it."""
    from anima2 import uomap
    from anima2.uomap import find_warrior_stand

    # A world of two-tile ledges: every tile has a ray of length 2 in some direction and
    # a cliff at 3, so the best candidate anywhere scores 2 — better than nothing, and
    # still under the floor. That is the case the guard is for: `best` HAS moved off the
    # nominal spot by then, so returning it would relocate the stand for no benefit.
    def fake_cells(map_index, x0, y0, x1, y1):
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                flat = abs(x - 505) <= 2 and abs(y - 505) <= 2
                yield x, y, 0x0003, 15 if flat else 15 + 40 * ((x + y) % 2)

    real, uomap.land_cells = uomap.land_cells, fake_cells
    try:
        from anima2.uomap import walkable_run
        best_anywhere = max(
            min(walkable_run(0, x, y, dx, dy, 12) for dx, dy in
                ((1, 0), (-1, 0), (0, 1), (0, -1)))
            for x in range(495, 512) for y in range(495, 512))
        assert 0 < best_anywhere < 3, best_anywhere
        # ...and that best is somewhere ELSE, or the guard would be untested: without it
        # the search returns the 5x5 patch and relocates a stand for no benefit.
        assert min(walkable_run(0, 500, 500, dx, dy, 12) for dx, dy in
                   ((1, 0), (-1, 0), (0, 1), (0, -1))) < best_anywhere
        assert find_warrior_stand(0, 500, 500, want=12, floor=3) is None
    finally:
        uomap.land_cells = real


def test_walkable_run_refuses_ground_nothing_can_stand_on():
    """z was the whole model, so a **flat lake read as perfect walking ground**.

    ServUO's `Movement.CheckMovement` blocks an `Impassable` land tile unless the mover
    `CanSwim`, and every water tile carries `Impassable | Wet`. Measured 2026-08-25
    (audit §64.4): the first three-warrior roster teleported two of three warriors onto
    tiles 100 and 169 — the GM ignores passability — and both stood at full health for
    1200 ticks emitting walks the server refused.

    The origin and the step are checked separately here on purpose: in the live pocket
    both were water, so each gate masked the other and neither was really tested.
    """
    from anima2 import uomap
    from anima2.uomap import land_walkable, walkable_run

    GRASS, WATER = 0x0006, 0x00A9
    assert land_walkable(GRASS) and not land_walkable(WATER), "the tile ids moved"

    def _flat(water: "set[tuple[int, int]]"):
        def cells(map_index, x0, y0, x1, y1):
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    yield x, y, (WATER if (x, y) in water else GRASS), 15
        return cells

    real = uomap.land_cells
    try:
        # ONE water tile under our feet, grass all around: only the ORIGIN gate can see it.
        uomap.land_cells = _flat({(500, 500)})
        assert walkable_run(0, 500, 500, 1, 0, 6) == 0, "standing in a lake goes nowhere"
        # Grass under our feet, water three tiles along: only the STEP gate can see it.
        uomap.land_cells = _flat({(503, 500)})
        assert walkable_run(0, 500, 500, 1, 0, 6) == 2, "the run stops at the shore"
        # ...and with neither, the flat ground runs the whole way.
        uomap.land_cells = _flat(set())
        assert walkable_run(0, 500, 500, 1, 0, 6) == 6
    finally:
        uomap.land_cells = real


def test_the_leash_is_the_pocket_and_not_the_walk_to_the_shops():
    """It was the shortest walkable shop ray, which is 3 at the proven pocket — so it read
    as correct — and 12 at a pocket with open ground on every side.

    Measured 2026-08-25 (audit §64.5): the rescued second warrior of a three-warrior
    roster drifted seven tiles off its stand, left its pinned prey behind at
    `foes=d4,d4,d5`, and spent the run swinging across ground it could cross one tile of.
    """
    from anima2.village import _PREY_GAP, _VENDOR_GAP, _VENDOR_MIN_GAP, warrior_leash

    open_ground = dict.fromkeys(("weapon", "healer", "banker", "armorer"), _VENDOR_GAP)
    assert warrior_leash(open_ground) == _PREY_GAP + 2, (
        "open ground is not a licence to wander: the pocket is the prey ring plus a step")

    # THE PROVEN POCKET DOES NOT MOVE. Its shortest ray is 3, and two 2000-gold days were
    # measured at that leash (§63.4/§63.5).
    proven = {"weapon": 3, "healer": 6, "banker": 4, "armorer": 5}
    assert warrior_leash(proven) == 3

    # A pocket whose shops are closer than the prey ring is still capped by the shops —
    # walking past them is not guaranteed to get anywhere.
    assert warrior_leash({"weapon": _VENDOR_MIN_GAP, "healer": 9, "banker": 9,
                          "armorer": 9}) == _VENDOR_MIN_GAP
    # ...and never below the floor, or the warrior cannot leave the tile it stands on.
    assert warrior_leash(dict.fromkeys(("a", "b"), 0)) == _VENDOR_MIN_GAP
