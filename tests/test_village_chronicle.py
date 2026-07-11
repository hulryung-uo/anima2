"""`village.py`'s PHASE6.md item 2 event detectors — offline, unit-testable
pure functions of `(prev_memory, memory, ...)`. Each detector is exercised
as its own hand-built fixture "sequence" (a genuine phase-exit/growth tick
vs. a negative control where the phase churns but nothing is confirmed),
plus `_chronicle_events_this_tick`'s own per-profession dispatch.

`_delivered_ingots`/`_looted_corpse` take an ACCUMULATED reward total
(`deliver_phase_reward`/`hunt_reward_accum`), not a single tick's own
episode — a live-caught fix (PHASE6.md item 2's own live gate): a multi-pile
ingot haul or a multi-item corpse pays its confirmed reward across several
ticks, not as one lump sum on the exact transition/growth tick, so checking
only that tick's own episode silently missed (or under-counted) real,
confirmed deliveries. See `_delivered_ingots`'s own docstring for the full
story.
"""

from __future__ import annotations

from anima2.contract import ItemView, Observation, PlayerView, Position
from anima2.memory import Episode
from anima2.village import (
    _accumulate_deliver_reward,
    _accumulate_hunt_reward,
    _banked_gold,
    _chronicle_events_this_tick,
    _delivered_ingots,
    _looted_corpse,
    _pack_ingot_count,
    _picked_up_ingots,
    _reward_if_named,
    _sold_to_vendor,
)

BACKPACK_LAYER = 0x15
INGOT_GRAPHIC = 0x1BEF


def _ep(summary: str, reward: float, tick: int = 1) -> Episode:
    return Episode(tick=tick, kind="skill", summary=summary, reward=reward)


# --- _reward_if_named (still used by sold_to_vendor/banked_gold) -----------


def test_reward_if_named_matches_prefix_and_positive_reward():
    assert _reward_if_named(_ep("blacksmith_market → running", 4.0), "blacksmith") == 4.0


def test_reward_if_named_none_when_no_episode_this_tick():
    assert _reward_if_named(None, "blacksmith") is None


def test_reward_if_named_none_when_reward_not_positive():
    assert _reward_if_named(_ep("blacksmith_market → running", 0.0), "blacksmith") is None


def test_reward_if_named_none_when_wrong_prefix():
    assert _reward_if_named(_ep("hunt → running", 4.0), "blacksmith") is None


def test_reward_if_named_startswith_covers_blacksmith_market_subclass_name():
    # BlacksmithMarket.name == "blacksmith_market" — the live work skill —
    # must still match a plain "blacksmith" prefix check (see the function's
    # own docstring for why this matters).
    assert _reward_if_named(_ep("blacksmith_market → running", 12.0), "blacksmith") == 12.0


# --- delivered_ingots (miner -> blacksmith) ----------------------------------


def test_delivered_ingots_fires_on_genuine_confirmed_delivery():
    prev = {"smelt_phase": "deliver"}
    now = {"smelt_phase": "return"}
    assert _delivered_ingots(prev, now, deliver_phase_reward=10.0) == 10.0


def test_delivered_ingots_negative_control_wedged_delivery_no_confirmed_reward():
    """Phase churn (deliver -> return) with NOTHING accumulated over the
    whole trip — a wedged/failed delivery that never confirms anything —
    must fire zero events, not a phantom one."""
    prev = {"smelt_phase": "deliver"}
    now = {"smelt_phase": "return"}
    assert _delivered_ingots(prev, now, deliver_phase_reward=0.0) is None


def test_delivered_ingots_no_event_without_the_phase_transition():
    # Still "deliver" both ticks (mid-trip) — even with accumulated reward
    # already on the books, this isn't the confirmed *exit* transition yet.
    prev = {"smelt_phase": "deliver"}
    now = {"smelt_phase": "deliver"}
    assert _delivered_ingots(prev, now, deliver_phase_reward=10.0) is None


def test_delivered_ingots_sums_reward_confirmed_across_a_multi_pile_haul():
    """The live-caught bug, regression-pinned: a real 3-pile delivery pays
    5.0 + 5.0 + 5.0 across three separate ticks (one per confirmed
    pile-drop) — a detector that only read the exact transition tick's own
    episode would see 0.0 (or, worse, just the last pile's own increment)
    on the tick the phase actually flips to "return", since the first two
    piles' reward already landed on EARLIER ticks. The accumulated total
    (`deliver_phase_reward`, summed by the caller across every tick of the
    trip — see `_run_worker`) is what makes the full 15.0 visible here.
    """
    prev = {"smelt_phase": "deliver"}
    now = {"smelt_phase": "return"}
    # Simulates _run_worker's own running total after 3 piles confirmed
    # (5.0 each) plus the final transition tick contributing 0.0 more.
    assert _delivered_ingots(prev, now, deliver_phase_reward=15.0) == 15.0


def test_delivered_ingots_transition_tick_itself_can_contribute_zero():
    """The exact failure mode live-caught: the transition tick's OWN
    increment is 0.0 (everything already paid on earlier ticks), yet the
    accumulated total across the whole trip is still positive and correct —
    proves the fix doesn't depend on the LAST tick contributing anything."""
    prev = {"smelt_phase": "deliver"}
    now = {"smelt_phase": "return"}
    # deliver_phase_reward already reflects everything paid on prior ticks
    # PLUS this tick's own (zero) increment — the caller's accumulation, not
    # this pure function's concern; this asserts the detector trusts it.
    assert _delivered_ingots(prev, now, deliver_phase_reward=8.0) == 8.0


# --- the accumulation itself: a full multi-tick simulation, end to end -----
#
# `_delivered_ingots`/`_looted_corpse`'s own tests above prove the DETECTOR
# trusts an already-accumulated total; these simulate `_run_worker`'s own
# tick-by-tick loop building that total from scratch — the actual shape of
# the live-caught bug (a first-draft version checked only the transition
# tick's own episode and missed real, multi-pile deliveries entirely).


def test_deliver_reward_accumulates_across_a_simulated_multi_pile_delivery_trip():
    """A 3-pile haul: piles confirmed on ticks 10, 11, and 12 (5.0 each),
    the phase itself flipping to "return" on tick 12 with that tick's OWN
    episode contributing nothing further (already fully paid). The
    accumulated total by the time the transition is detected must be the
    FULL 15.0 — not 5.0 (only the last pile) and not 0.0 (the transition
    tick's own increment)."""
    accum = 0.0
    smelt_phase = "deliver"

    # Tick 10: first pile confirmed, still mid-trip.
    prev_memory = {"smelt_phase": smelt_phase}
    accum = _accumulate_deliver_reward(accum, prev_memory, _ep("mine_smelt_deliver → running", 5.0, tick=10))
    assert accum == 5.0

    # Tick 11: second pile confirmed, still mid-trip.
    prev_memory = {"smelt_phase": smelt_phase}
    accum = _accumulate_deliver_reward(accum, prev_memory, _ep("mine_smelt_deliver → running", 5.0, tick=11))
    assert accum == 10.0

    # Tick 12: third (last) pile confirmed — same tick the phase transitions.
    # `prev_memory` still reads "deliver" going into this tick's step().
    prev_memory = {"smelt_phase": smelt_phase}
    accum = _accumulate_deliver_reward(accum, prev_memory, _ep("mine_smelt_deliver → running", 5.0, tick=12))
    assert accum == 15.0

    # Detection: this tick's OWN memory now reads "return".
    now_memory = {"smelt_phase": "return"}
    assert _delivered_ingots(prev_memory, now_memory, deliver_phase_reward=accum) == 15.0


def test_deliver_reward_accumulation_survives_a_transition_tick_with_zero_of_its_own():
    """The exact live-caught shape: the LAST pile's confirmation lands one
    tick BEFORE the phase actually flips (a one-tick observation lag), so
    the transition tick's own episode is `None`/zero — the accumulated
    total from earlier ticks must still be reported correctly."""
    accum = 0.0
    prev_memory = {"smelt_phase": "deliver"}
    accum = _accumulate_deliver_reward(accum, prev_memory, _ep("mine_smelt_deliver → running", 8.0, tick=40))
    assert accum == 8.0

    # Transition tick: no new episode at all this tick (nothing left to pay).
    prev_memory = {"smelt_phase": "deliver"}
    accum = _accumulate_deliver_reward(accum, prev_memory, None)
    assert accum == 8.0  # unchanged — a None episode contributes nothing, loses nothing

    now_memory = {"smelt_phase": "return"}
    assert _delivered_ingots(prev_memory, now_memory, deliver_phase_reward=accum) == 8.0


def test_deliver_reward_accumulator_ignores_mining_and_smelting_phase_episodes():
    """`MineSmeltDeliver.name == "mine_smelt_deliver"` regardless of internal
    phase, so mining-phase skill-gain and smelting-phase ingot-gain episodes
    ALSO carry that name prefix — the accumulator must only ever add while
    `prev_memory` shows the "deliver" phase, never during "mine"/"smelt"."""
    accum = 0.0
    # A mining-phase skill-gain episode — must NOT be folded into a later
    # delivery's total.
    accum = _accumulate_deliver_reward(accum, {"smelt_phase": "mine"},
                                       _ep("mine_smelt_deliver → running", 0.3, tick=1))
    assert accum == 0.0
    # A smelting-phase ingot-gain episode — also must NOT be folded in.
    accum = _accumulate_deliver_reward(accum, {"smelt_phase": "smelt"},
                                       _ep("mine_smelt_deliver → running", 4.0, tick=5))
    assert accum == 0.0
    # Only once the deliver phase actually starts does accumulation begin.
    accum = _accumulate_deliver_reward(accum, {"smelt_phase": "deliver"},
                                       _ep("mine_smelt_deliver → running", 6.0, tick=20))
    assert accum == 6.0


def test_hunt_reward_accumulates_across_a_simulated_multi_item_corpse():
    accum = 0.0
    accum = _accumulate_hunt_reward(accum, _ep("hunt → running", 20.0, tick=5))  # gold
    assert accum == 20.0
    accum = _accumulate_hunt_reward(accum, _ep("hunt → running", 17.0, tick=6))  # a gem, next tick
    assert accum == 37.0
    assert _looted_corpse({"hunt_looted": []}, {"hunt_looted": ["c1"]}, hunt_reward_accum=accum) == 37.0


def test_hunt_reward_accumulator_ignores_non_hunt_episodes_and_non_positive_reward():
    accum = 0.0
    accum = _accumulate_hunt_reward(accum, None)
    assert accum == 0.0
    accum = _accumulate_hunt_reward(accum, _ep("blacksmith_market → running", 5.0))
    assert accum == 0.0
    accum = _accumulate_hunt_reward(accum, _ep("hunt → running", 0.0))
    assert accum == 0.0


# --- picked_up_ingots (blacksmith <- miner, reverse direction) --------------


def test_picked_up_ingots_fires_on_confirmed_pack_delta_across_fetch_trip():
    prev = {"bs_state": "fetch"}
    now = {"bs_state": "open"}
    assert _picked_up_ingots(prev, now, fetch_entry_ingots=2, pack_ingots_now=8) == 6.0


def test_picked_up_ingots_negative_control_no_pack_growth():
    prev = {"bs_state": "fetch"}
    now = {"bs_state": "open"}
    assert _picked_up_ingots(prev, now, fetch_entry_ingots=8, pack_ingots_now=8) is None


def test_picked_up_ingots_none_without_baseline():
    prev = {"bs_state": "fetch"}
    now = {"bs_state": "open"}
    assert _picked_up_ingots(prev, now, fetch_entry_ingots=None, pack_ingots_now=8) is None


def test_picked_up_ingots_no_event_without_the_state_transition():
    prev = {"bs_state": "fetch"}
    now = {"bs_state": "fetch"}  # still mid-fetch
    assert _picked_up_ingots(prev, now, fetch_entry_ingots=2, pack_ingots_now=8) is None


def test_pack_ingot_count_sums_ingot_graphics_in_the_backpack_only():
    obs = Observation(
        player=PlayerView(serial=1, pos=Position(0, 0, 0)),
        items=[
            ItemView(serial=10, graphic=0, amount=1, pos=Position(), container=1,
                    layer=BACKPACK_LAYER, distance=0),
            ItemView(serial=11, graphic=INGOT_GRAPHIC, amount=5, pos=Position(), container=10,
                    layer=0, distance=0),
            ItemView(serial=12, graphic=INGOT_GRAPHIC, amount=3, pos=Position(), container=10,
                    layer=0, distance=0),
            ItemView(serial=13, graphic=INGOT_GRAPHIC, amount=99, pos=Position(), container=None,
                    layer=0, distance=0),  # on the ground — excluded
        ],
    )
    assert _pack_ingot_count(obs) == 8


def test_pack_ingot_count_zero_when_no_backpack_visible():
    obs = Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)), items=[])
    assert _pack_ingot_count(obs) == 0


# --- sold_to_vendor / banked_gold (blacksmith -> world) ---------------------
#
# Unlike delivered_ingots/looted_corpse, these stay single-episode-reward
# checks: a vendor sale is one SellItems action covering every dagger at
# once, and GOLD_GRAPHIC never fragments into multiple piles the way
# ORE_GRAPHICS/INGOT_GRAPHICS do (`skills/market.py`'s own module docstring:
# "no small/large-pile variants the way ore/ingots have") — neither is
# exposed to the multi-tick-fragmented-payment pattern that motivated
# delivered_ingots/looted_corpse's own fix.


def test_sold_to_vendor_fires_on_confirmed_sale():
    prev = {"mkt_phase": "sell"}
    now = {"mkt_phase": "sell_return"}
    episode = _ep("blacksmith_market → running", 30.0)
    assert _sold_to_vendor(prev, now, episode) == 30.0


def test_sold_to_vendor_negative_control_gave_up_sale_no_confirmed_reward():
    prev = {"mkt_phase": "sell"}
    now = {"mkt_phase": "sell_return"}
    assert _sold_to_vendor(prev, now, None) is None


def test_banked_gold_fires_on_confirmed_deposit():
    prev = {"mkt_phase": "bank"}
    now = {"mkt_phase": "bank_return"}
    episode = _ep("blacksmith_market → running", 100.0)
    assert _banked_gold(prev, now, episode) == 100.0


def test_banked_gold_negative_control_gave_up_deposit_no_confirmed_reward():
    prev = {"mkt_phase": "bank"}
    now = {"mkt_phase": "bank_return"}
    assert _banked_gold(prev, now, None) is None


def test_sold_and_banked_are_independent_phases():
    # A sell-phase exit must never be mistaken for a bank-phase exit or vice
    # versa, even with an otherwise-matching reward-bearing episode.
    episode = _ep("blacksmith_market → running", 5.0)
    assert _sold_to_vendor({"mkt_phase": "bank"}, {"mkt_phase": "bank_return"}, episode) is None
    assert _banked_gold({"mkt_phase": "sell"}, {"mkt_phase": "sell_return"}, episode) is None


# --- looted_corpse (hunter -> world) ----------------------------------------


def test_looted_corpse_fires_on_hunt_looted_growth_with_confirmed_loot_value():
    prev = {"hunt_looted": ["corpseA"]}
    now = {"hunt_looted": ["corpseA", "corpseB"]}
    assert _looted_corpse(prev, now, hunt_reward_accum=7.0) == 7.0


def test_looted_corpse_fires_with_zero_amount_for_a_genuinely_empty_corpse():
    # hunt_looted still grows (the corpse was legitimately opened and fully
    # accounted for), but nothing whitelisted was in it — still a real
    # loot-cycle event, just a zero-value one (see the function's docstring).
    prev = {"hunt_looted": ["corpseA"]}
    now = {"hunt_looted": ["corpseA", "corpseB"]}
    assert _looted_corpse(prev, now, hunt_reward_accum=0.0) == 0.0


def test_looted_corpse_negative_control_no_list_growth():
    # Phase churn (e.g. hunt_phase toggling) with the bookkeeping list
    # unchanged must never fabricate a loot event.
    prev = {"hunt_looted": ["corpseA"]}
    now = {"hunt_looted": ["corpseA"]}
    assert _looted_corpse(prev, now, hunt_reward_accum=7.0) is None


def test_looted_corpse_missing_key_treated_as_empty_list():
    prev: dict = {}
    now = {"hunt_looted": ["corpseA"]}
    assert _looted_corpse(prev, now, hunt_reward_accum=0.0) == 0.0


def test_looted_corpse_sums_reward_confirmed_across_a_multi_item_corpse():
    """Mirrors `test_delivered_ingots_sums_reward_confirmed_across_a_multi_pile_haul`:
    a corpse holding gold AND a gem pays its confirmed value across two
    separate lift-then-place ticks before `hunt_looted` finally grows — the
    accumulated total, not the growth tick's own (possibly zero) episode, is
    what must be reported."""
    prev = {"hunt_looted": []}
    now = {"hunt_looted": ["corpseA"]}
    assert _looted_corpse(prev, now, hunt_reward_accum=37.0) == 37.0


# --- _chronicle_events_this_tick: per-profession dispatch -------------------


def test_dispatch_miner_only_detects_delivered_ingots():
    prev = {"smelt_phase": "deliver"}
    now = {"smelt_phase": "return"}
    events = _chronicle_events_this_tick(
        "miner", "Tormund0", prev, now, None, fetch_entry_ingots=None, pack_ingots_now=0,
        deliver_phase_reward=10.0,
    )
    assert events == [("delivered_ingots", "Tormund0", 10.0)]


def test_dispatch_miner_no_events_on_negative_control():
    prev = {"smelt_phase": "deliver"}
    now = {"smelt_phase": "return"}
    events = _chronicle_events_this_tick(
        "miner", "Tormund0", prev, now, None, fetch_entry_ingots=None, pack_ingots_now=0,
        deliver_phase_reward=0.0,
    )
    assert events == []


def test_dispatch_blacksmith_can_fire_multiple_kinds_same_tick_from_independent_detectors():
    # Sell-phase exit and bank-phase exit are mutually exclusive in real
    # `mkt_phase` sequencing, but the dispatcher itself imposes no such
    # constraint (each detector is independently evaluated) — this proves
    # dispatch composition, not a claim about real phase reachability.
    prev = {"bs_state": "fetch", "mkt_phase": "sell"}
    now = {"bs_state": "open", "mkt_phase": "sell_return"}
    episode = _ep("blacksmith_market → running", 20.0)
    events = _chronicle_events_this_tick(
        "blacksmith", "Grimm0", prev, now, episode, fetch_entry_ingots=3, pack_ingots_now=9,
    )
    assert ("picked_up_ingots", "Grimm0", 6.0) in events
    assert ("sold_to_vendor", None, 20.0) in events
    assert len(events) == 2


def test_dispatch_hunter_ignores_blacksmith_and_miner_keys():
    prev = {"smelt_phase": "deliver", "hunt_looted": []}
    now = {"smelt_phase": "return", "hunt_looted": ["corpseA"]}
    events = _chronicle_events_this_tick(
        "hunter", None, prev, now, None, fetch_entry_ingots=None, pack_ingots_now=0,
        deliver_phase_reward=999.0,  # must be ignored — job isn't "miner"
        hunt_reward_accum=0.0,
    )
    assert events == [("looted_corpse", None, 0.0)]


def test_dispatch_townsfolk_never_detects_anything():
    prev = {"smelt_phase": "deliver", "bs_state": "fetch", "hunt_looted": []}
    now = {"smelt_phase": "return", "bs_state": "open", "hunt_looted": ["x"]}
    episode = _ep("mine_smelt_deliver → running", 10.0)
    events = _chronicle_events_this_tick(
        "townsfolk", None, prev, now, episode, fetch_entry_ingots=0, pack_ingots_now=5,
        deliver_phase_reward=10.0, hunt_reward_accum=10.0,
    )
    assert events == []


def test_dispatch_hunter_two_corpses_retired_same_tick_emit_two_events():
    """Review-caught gap: Hunt._advance can recurse same-tick into an
    already-resolved next corpse, growing hunt_looted by 2 in one tick. The
    event COUNT must stay faithful (one zero-amount event per extra
    retirement), with the combined confirmed loot on the first event."""
    prev = {"hunt_looted": ["corpseA"]}
    now = {"hunt_looted": ["corpseA", "corpseB", "corpseC"]}
    events = _chronicle_events_this_tick(
        "hunter", None, prev, now, None, fetch_entry_ingots=None, pack_ingots_now=0,
        deliver_phase_reward=0.0, hunt_reward_accum=11.0,
    )
    assert events == [("looted_corpse", None, 11.0), ("looted_corpse", None, 0.0)]
    assert sum(a for _, _, a in events) == 11.0
