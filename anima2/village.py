"""A working village: agents with per-agent professions, each doing their job.

Releases a roster of agents, stages the workers via the Control plane (tools +
skills + a distinct workplace), names each character, then runs them all
concurrently. Miners mine at their own ore bank and **gain Mining skill** — the
village's "work output" is the skill each agent accrues (recorded as episodic
reward by the work skill). A roster with both a miner and a blacksmith gets
the first of each co-located at a calibrated trade spot with the miner's
delivery target set — goods actually flow between them (DESIGN.md §10 Phase
3; see `live_trade.py` for a focused 2-agent live proof).

Usage: python -m anima2.village [--miners N] [--townsfolk M] [--ticks T]
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path
from typing import Any

from .agent import Agent
from .capabilities import CAPABILITIES, CapabilityPolicy
from .chronicle import ChronicleEvent, ChronicleLedger
from .contract import Observation, Say, Walk
from .control import GmControl
from .ipc_body import IpcBody, ResilientIpcBody
from .memory import Episode
# The ONE definition of "what does this Observation say we have" (see `obsview.py`'s
# docstring for what the hand-written copies drifted into). Module level, not
# function-local like most of this file's imports: `obsview` sits BELOW everything
# village.py touches — its whole import closure is `contract` + four `skills` modules,
# every one of which is already loaded by this file's own top-level imports — so it
# costs nothing and cannot cycle.
from .obsview import pack_amount, pack_serial
from .persona import Persona
from .profession import (
    BANKER_SPOT,
    BLACKSMITH_SPOTS,
    FISHING_SPOTS,
    MINING_SPOTS,
    PROFESSIONS,
    TRADE_MINE_SPOT,
    TRADE_SMITH_SPOT,
    VENDOR_SPOT,
    Profession,
)
from .skills.mage import KeepDistance
from .skill_library import SkillLibrary
from .skill_tuning import DELIVER_THRESHOLD_CANDIDATES, ParamSpec, ParamTuner
from .skills import MineSmeltDeliver
from .skills.market import walk_readout
from .skills.base import Status
from .uomap import find_mine_spots, find_tree_clusters, play_map

# Minoc-area woods, near the mining camp — keeps the village compact.
# Each lumberjack gets a distinct grove (a stand spot + the trees in reach).
FOREST_BASE = (2520, 450)
#: Fallback facet when no observation exists yet. The authority is
#: `Observation.map_index` via `play_map` / `_survey_map` (follow-up 41).
#: 1 = Trammel in the contract; a body on Felucca (0) must not keep surveying 1.
LUMBER_MAP = 1
#: Relocation stands pre-surveyed for the forge miner — each gets its own forge at
#: staging, so this is a provisioning cap, not a wander limit (the blind compass walk
#: remains the fallback when the pool runs dry).
#:
#: **Twelve, because that is every stand the survey already finds** at the trade mine
#: (`find_mine_spots(LUMBER_MAP, *TRADE_MINE_SPOT)` returns exactly 12 at its default
#: radius; 17 at radius 60). It was SIX, which threw half of them away — and the six were
#: measured running out, twice.
#:
#: The measurement (audit §24.3, follow-up 27). `spacing=8` puts one stand per 8x8
#: `HarvestBank`, and an ore bank holds 10-34 ore, so six stands plus home is ~7 banks of
#: finite rock. On two consecutive 1800-tick days the miner stood on ALL SIX pool spots
#: and then cycled dead tiles at `win=23/23` — every swing in the relocation window a
#: no-metal verdict — from roughly sample 115 of 209 to the end. One of those days
#: delivered a single 10-ingot pile all run and starved the flagship chain to `net=+35g`.
#: Both ended parked on the same tile. The pool was not thin; it was EMPTY.
#:
#: The trade-off, stated: the furthest pooled stand is 36 tiles from home, so a delivery
#: from it is a long walk. That is the wrong comparison. Relocation only reaches stand N
#: once 1..N-1 are dead, so the choice at that moment is a far stand versus standing on
#: exhausted rock — and the second produces nothing at any distance.
#:
#: "Nearest-first" here means nearest by WALK, not by straight line: `find_mine_spots`
#: orders by BFS depth over walkable non-mine land, so the pooled stands measure
#: 0,8,16,13,24,22,... in Chebyshev — non-monotone, and correctly so, because a face that
#: bends makes a shorter walk look further as the crow flies. The BFS is also the
#: same-flank filter forge12 needed, when the greedy walker wedged into the mountain and
#: burned the whole pool without arriving anywhere.
#:
#: This is the lumberjack's lesson, arriving late for the miner: the note directly below
#: records thin Minoc woods "the size that runs dry mid-session and leaves `Harvest`
#: relocating instead of working", diagnosed for one profession and never connected to the
#: other.
MINE_POOL_SPOTS = 12


def _survey_map(*bodies, fallback: int = LUMBER_MAP) -> int:
    """Facet for `find_mine_spots` / `find_tree_clusters`: the body's own map.

    `LUMBER_MAP` is only the fallback. A logged-in body with `last_obs` wins,
    so a Felucca character is not surveyed against Trammel statics (follow-up 41).
    """
    for body in bodies:
        if body is None:
            continue
        obs = getattr(body, "last_obs", None)
        if obs is None:
            observe = getattr(body, "observe", None)
            if callable(observe):
                obs = observe()
        if obs is not None:
            return play_map(obs, fallback=fallback)
    return fallback


#: Where a SOLO woodsman works. The multi-profession village keeps its lumberjacks in
#: the Minoc woods above to stay compact, but those woods are thin — a live survey found
#: 284 tree statics there and a best grove of just TWO trees, which is the size that runs
#: dry mid-session and leaves `Harvest` relocating instead of working. The Yew woods
#: carry 1192 statics with several five-tree groves, so a lone woodsman gets sustained
#: work and `Harvest`'s relocation has somewhere to go when a bank does thin out.
#: (Surveyed across 7 candidate bases; this was the densest by a wide margin.)
YEW_FOREST = (560, 1080)

#: `data/insights.jsonl` relative to the process's cwd — mirrors `curriculum.
#: py`'s `_DEFAULT_MILESTONES_LOG`/`skill_library.py`'s `_DEFAULT_LEDGER`
#: convention exactly (created lazily, gitignored). PHASE6.md item 1.
INSIGHTS_PATH = Path("data") / "insights.jsonl"
_LiveIpcBody = IpcBody | ResilientIpcBody


def _persona_for(prof: Profession, idx: int) -> Persona:
    return Persona(name=f"{prof.persona_name}{idx}", title=f"a {prof.key}",
                   combat_disposition=prof.combat_disposition)


# ============================================================================
# PHASE6.md item 2 — the village chronicle: pure event detectors.
#
# Each detector is a small, unit-testable pure function reading only
# `ctx.memory`'s own phase-key strings (duplicated, never cross-imported from
# `skills/*.py` — mirrors `curriculum.py::_mid_transaction`'s identical
# discipline, cited by name in this item's own spec) plus, where a skill
# actually has one, a confirmed reward-bearing `Episode` recorded THIS tick.
# `_run_worker` below calls these once per tick, per agent, only when
# `--chronicle` is set — every detector returns `None` when nothing fired.
# ============================================================================

#: Ingots one dagger consumes — the arithmetic `_crafted_daggers` checks a craft against.
_CHRONICLE_DAGGER_INGOTS = 3
#: Mirrors `skills/smelt.py::INGOT_GRAPHICS`/`curriculum.py::_INGOT_GRAPHICS`
#: — duplicated, not imported, matching this codebase's established
#: "duplicate a handful of graphic constants rather than reach into a
#: skill's own module" convention (see curriculum.py's own module docstring).
_CHRONICLE_INGOT_GRAPHICS = frozenset({0x1BEF, 0x1BF0, 0x1BF1, 0x1BF2})


def _pack_ingot_count(obs: Observation) -> int:
    """Ingots in OUR backpack. The body was a hand-written readback (its own
    `_CHRONICLE_BACKPACK_LAYER`, its own owner filter, its own `bp is None` guard); it is
    `obsview.pack_amount` now. The NAME and signature stay: `live_chronicle.py`,
    `live_forum_chronicle.py` and `tests/test_village_chronicle.py` all import it."""
    return pack_amount(obs, _CHRONICLE_INGOT_GRAPHICS)


def _reward_if_named(
    new_episode: Episode | None,
    prefix: str | tuple[str, ...],
) -> float | None:
    """`new_episode`'s own reward, iff it was actually recorded THIS tick
    (the caller only ever passes an episode here when `agent.episodes.
    total_recorded` grew since the last check — see `_run_worker`), its
    `summary` names the expected skill (`str.startswith`, not exact
    equality: the live blacksmith work skill is `market.py::
    BlacksmithMarket`, `name = "blacksmith_market"`, which a `"blacksmith"`
    prefix check still matches — the same episode a plain solo `Blacksmith`
    would also produce), and the reward is positive — the "confirmed, not
    merely attempted" gate every detector below shares. `None` otherwise —
    never fabricates an event off phase-key noise alone.
    """
    if new_episode is not None and new_episode.summary.startswith(prefix) and new_episode.reward > 0:
        return float(new_episode.reward)
    return None


def _delivered_ingots(prev_memory: dict, memory: dict, deliver_phase_reward: float) -> float | None:
    """miner -> blacksmith: `smelt_phase` transitions out of `"deliver"`
    (prior tick `"deliver"`, this tick `"return"` — `skills/smelt.py::
    MineSmeltDeliver.step()`'s own same-tick "deliver done -> resume via
    return" fallthrough) — `amount` is `deliver_phase_reward`, the TOTAL
    confirmed reward the caller accumulated across every tick of the trip
    (see `_run_worker`'s own accumulator), not a single tick's own episode
    reward.

    This is necessary, not merely careful — **live-caught** (PHASE6.md item
    2's own live gate): `INGOT_GRAPHICS` has 4 distinct graphics, matching
    `ORE_GRAPHICS`'s own pile fragmentation (`Item.WillStack` requires an
    exact graphic match, so a smelted haul is often 2-4 separate piles, not
    always one), and `MineSmeltDeliver._deliver_step` pays its reward as one
    increment **per confirmed pile-drop**, not as one lump sum on the exact
    tick `smelt_phase` finally flips to `"return"` — that tick's own reward
    is often `0.0` (everything already paid on earlier ticks), even for a
    real, fully-confirmed delivery. A first-draft version of this detector
    checked only that tick's own episode and silently missed every delivery
    with more than one pile — caught because `picked_up_ingots` (the
    blacksmith's side of the identical edge, reward-channel-free by
    necessity — see that detector's own docstring) kept firing with real,
    correct amounts while this one stayed silent.
    """
    if prev_memory.get("smelt_phase") == "deliver" and memory.get("smelt_phase") == "return":
        if deliver_phase_reward > 0:
            return deliver_phase_reward
    return None


def _picked_up_ingots(
    prev_memory: dict, memory: dict, fetch_entry_ingots: int | None, pack_ingots_now: int,
) -> float | None:
    """blacksmith <- miner, the same edge, reverse direction: `bs_state`
    transitions out of `"fetch"` (`skills/craft.py::Blacksmith._fetch_step`'s
    own state, held for the whole fetch-plus-walk-home trip). Unlike the
    other three detectors, `Blacksmith.step()` has **no dedicated reward
    channel for the pickup itself** — verified directly: its only reward
    computation is Blacksmithing skill-base gain, computed unconditionally
    at the top of every tick and attached to whatever action that tick
    happens to return, fetch included, purely incidentally. So `amount`
    here is a **confirmed pack-ingot delta** (`pack_ingots_now -
    fetch_entry_ingots`, both Observation-derived — `_pack_ingot_count`)
    over the fetch trip instead of an episode reward — the same "only a
    confirmed, observed outcome pays" discipline the reward-based detectors
    get from `EpisodicMemory`, applied here via a direct pack-count
    comparison since no reward channel exists to read it from.
    `fetch_entry_ingots` is the pack count `_run_worker` captured the tick
    `bs_state` first became `"fetch"` (its own snapshot, taken once per
    fetch trip — not recomputed here); `None` (never captured) yields no
    event rather than a bogus delta against a missing baseline.
    """
    if prev_memory.get("bs_state") == "fetch" and memory.get("bs_state") != "fetch":
        if fetch_entry_ingots is not None:
            amount = pack_ingots_now - fetch_entry_ingots
            if amount > 0:
                return float(amount)
    return None


def _sold_to_vendor(
    prev_memory: dict,
    memory: dict,
    new_episode: Episode | None,
    sell_reward_accum: float | None = None,
) -> float | None:
    """blacksmith -> world: `mkt_phase` transitions out of `"sell"` (`skills/
    market.py::BlacksmithMarket._sell_step`'s own same-tick fallthrough to
    `"sell_return"`) with a confirmed reward-bearing episode — `amount` is
    the gold gained (`_sell_step`'s own confirmed-gain accounting)."""
    def _capability_sale_amount(snapshot: dict) -> float | None:
        goal_id = snapshot.get("cap_sell_goal_id")
        sent = snapshot.get("cap_sell_sent_daggers")
        expected = snapshot.get("cap_sell_expected_gold")
        offered = snapshot.get("cap_sell_offered_items")
        dagger_delta = snapshot.get("cap_sell_dagger_delta")
        gold_delta = snapshot.get("cap_sell_gold_delta")
        if (
            type(goal_id) is int
            and snapshot.get("cap_sell_sent_goal_id") == goal_id
            and type(sent) is int
            and sent > 0
            and type(expected) is int
            and expected > 0
            and isinstance(offered, tuple)
            and offered
            and all(
                isinstance(entry, tuple)
                and len(entry) == 3
                and all(type(value) is int and value > 0 for value in entry)
                for entry in offered
            )
            and sum(amount for _serial, amount, _price in offered) == sent
            and sum(amount * price for _serial, amount, price in offered) == expected
            and snapshot.get("cap_sell_offered_cleared") is True
            and snapshot.get("cap_sell_offered_removed") == sent
            and type(dagger_delta) is int
            and dagger_delta >= sent
            and type(gold_delta) is int
            and gold_delta >= expected
        ):
            return float(expected)
        return None

    capability_amount = _capability_sale_amount(memory)
    if capability_amount is not None and _capability_sale_amount(prev_memory) is None:
        return capability_amount
    if (
        prev_memory.get("mkt_phase") == "sell"
        and memory.get("mkt_phase") in {"sell_return", "craft"}
    ):
        if sell_reward_accum is not None:
            return sell_reward_accum if sell_reward_accum > 0 else None
        return _reward_if_named(new_episode, ("blacksmith", "sell_daggers"))
    return None


def _banked_gold(
    prev_memory: dict,
    memory: dict,
    new_episode: Episode | None,
    bank_reward_accum: float | None = None,
) -> float | None:
    """blacksmith -> world: one confirmed bank deposit.

    A capability-owned deposit is identified by its goal-scoped baseline,
    action manifest, and terminal return evidence.  Its observed confirmed
    amount wins over the older phase/reward detector, including when a
    bounded operation could confirm only part of its original baseline.  A
    malformed active capability goal fails closed instead of falling through
    to a coincidental positive episode reward.

    The phase/reward branch remains for the legacy ``BlacksmithMarket`` path,
    which does not publish ``cap_bank_goal_id``.
    """

    def _completion(snapshot: dict) -> tuple[int, float] | None:
        goal_id = snapshot.get("cap_bank_goal_id")
        start_piles = snapshot.get("cap_bank_start_piles")
        expected = snapshot.get("cap_bank_expected_gold")
        start_bank = snapshot.get("cap_bank_start_bank_gold")
        bankbox = snapshot.get("cap_bank_box_serial")
        lifted = snapshot.get("cap_bank_lifted_items")
        dropped = snapshot.get("cap_bank_dropped_items")
        pack_delta = snapshot.get("cap_bank_pack_delta")
        bank_delta = snapshot.get("cap_bank_bank_delta")
        confirmed = snapshot.get("cap_bank_confirmed")

        start_valid = bool(
            isinstance(start_piles, tuple)
            and start_piles
            and all(
                isinstance(entry, tuple)
                and len(entry) == 2
                and type(entry[0]) is int
                and entry[0] > 0
                and type(entry[1]) is int
                and entry[1] > 0
                for entry in start_piles
            )
            and len({serial for serial, _amount in start_piles}) == len(start_piles)
        )
        lifted_valid = bool(
            isinstance(lifted, tuple)
            and lifted
            and all(
                isinstance(entry, tuple)
                and len(entry) == 2
                and type(entry[0]) is int
                and entry[0] > 0
                and type(entry[1]) is int
                and entry[1] > 0
                for entry in lifted
            )
            and len({serial for serial, _amount in lifted}) == len(lifted)
        )
        dropped_valid = bool(
            isinstance(dropped, tuple)
            and dropped
            and all(
                isinstance(entry, tuple)
                and len(entry) == 3
                and type(entry[0]) is int
                and entry[0] > 0
                and type(entry[1]) is int
                and entry[1] > 0
                and type(entry[2]) is int
                and entry[2] > 0
                for entry in dropped
            )
        )
        start_by_serial = dict(start_piles) if start_valid else {}
        lifted_within_baseline = bool(
            lifted_valid
            and all(
                serial in start_by_serial and amount <= start_by_serial[serial]
                for serial, amount in lifted
            )
        )
        dropped_matches_lifted = bool(
            dropped_valid
            and type(bankbox) is int
            and bankbox > 0
            and tuple((serial, amount) for serial, amount, _target in dropped)
            == lifted
            and all(target == bankbox for _serial, _amount, target in dropped)
        )

        if (
            type(goal_id) is int
            and goal_id > 0
            and snapshot.get("cap_bank_baseline_goal_id") == goal_id
            and snapshot.get("cap_bank_sent_goal_id") == goal_id
            and snapshot.get("cap_bank_finished_goal_id") == goal_id
            and snapshot.get("cap_bank_returned_goal_id") == goal_id
            and start_valid
            and type(expected) is int
            and expected > 0
            and sum(amount for _serial, amount in start_piles) == expected
            and type(start_bank) is int
            and start_bank >= 0
            and lifted_within_baseline
            and dropped_matches_lifted
            and type(pack_delta) is int
            and confirmed is not None
            and type(bank_delta) is int
            and type(confirmed) is int
            and 0 < confirmed <= expected
            and confirmed <= sum(amount for _serial, amount in lifted)
            and confirmed <= pack_delta <= expected
            and confirmed <= bank_delta <= expected
        ):
            return goal_id, float(confirmed)
        return None

    # Presence, rather than truthiness, makes a corrupt ``None``/boolean goal
    # id an active but invalid capability record.  It must not earn through
    # the legacy reward fallback.
    if "cap_bank_goal_id" in memory:
        current = _completion(memory)
        if current is None:
            return None
        previous = _completion(prev_memory)
        if previous is not None and previous[0] == current[0]:
            return None
        return current[1]

    if prev_memory.get("mkt_phase") != "bank":
        return None
    phase = memory.get("mkt_phase")
    if phase == "bank_return":
        if bank_reward_accum is not None:
            return bank_reward_accum if bank_reward_accum > 0 else None
        return _reward_if_named(new_episode, "blacksmith")
    if phase == "craft":
        if bank_reward_accum is not None:
            return bank_reward_accum if bank_reward_accum > 0 else None
        return _reward_if_named(new_episode, "bank_gold")
    return None


def _crafted_daggers(prev_memory: dict, memory: dict) -> float | None:
    """blacksmith -> world: one newly completed closed craft goal.

    The capability leaf already settles packet ordering and owns the exact
    inventory provenance.  Chronicle therefore records only a transition to
    a complete, internally consistent goal token; replaying the same memory
    snapshot cannot emit duplicate events, while a later goal id can.
    """

    def _completion(snapshot: dict) -> tuple[int, float] | None:
        goal_id = snapshot.get("cap_craft_goal_id")
        needed = snapshot.get("cap_craft_needed")
        confirmed = snapshot.get("cap_craft_confirmed")
        produced = snapshot.get("cap_craft_produced")
        ingots_used = snapshot.get("cap_craft_ingots_used")
        start_ingots = snapshot.get("cap_craft_start_ingots")
        failed_attempts = snapshot.get("cap_craft_failed_attempts")
        failed_ingots = snapshot.get("cap_craft_failed_ingots")
        failure_costs = snapshot.get("cap_craft_failure_costs")
        start_daggers = snapshot.get("cap_craft_start_daggers")
        produced_valid = bool(
            isinstance(produced, tuple)
            and produced
            and all(
                isinstance(entry, tuple)
                and len(entry) == 2
                and type(entry[0]) is int
                and entry[0] > 0
                and type(entry[1]) is int
                and entry[1] == 1
                for entry in produced
            )
            and len({serial for serial, _amount in produced}) == len(produced)
        )
        start_valid = bool(
            isinstance(start_daggers, tuple)
            and all(
                isinstance(entry, tuple)
                and len(entry) == 2
                and type(entry[0]) is int
                and entry[0] > 0
                and type(entry[1]) is int
                and entry[1] == 1
                for entry in start_daggers
            )
            and len({serial for serial, _amount in start_daggers}) == len(start_daggers)
        )
        start_count = (
            sum(amount for _serial, amount in start_daggers) if start_valid else -1
        )
        close_proven = bool(
            snapshot.get("cap_craft_close_sent") is True
            or (
                snapshot.get("cap_craft_close_reopen_sent") is True
                and snapshot.get("cap_craft_close_absent_wait", 0) >= 12
                and snapshot.get("cap_craft_close_reopen_wait", 0) >= 12
            )
        )
        if (
            type(goal_id) is int
            and snapshot.get("cap_craft_dagger_button_goal_id") == goal_id
            and snapshot.get("cap_craft_finished_goal_id") == goal_id
            and snapshot.get("cap_craft_returned_goal_id") == goal_id
            and snapshot.get("cap_craft_abort_goal_id") != goal_id
            and snapshot.get("cap_craft_stage") == "finished"
            and close_proven
            and type(needed) is int
            and needed > 0
            and start_valid
            and start_count + needed == 5
            and type(confirmed) is int
            and confirmed == needed
            and produced_valid
            and sum(amount for _serial, amount in produced) == needed
            and not ({serial for serial, _amount in start_daggers} &
                     {serial for serial, _amount in produced})
            and type(ingots_used) is int
            and type(start_ingots) is int
            and start_ingots >= ingots_used
            and type(failed_attempts) is int
            and failed_attempts >= 0
            and type(failed_ingots) is int
            and isinstance(failure_costs, tuple)
            and len(failure_costs) == failed_attempts
            and all(
                type(cost) is int and cost in {0, _CHRONICLE_DAGGER_INGOTS}
                for cost in failure_costs
            )
            and failed_ingots == sum(failure_costs)
            and ingots_used == _CHRONICLE_DAGGER_INGOTS * needed + failed_ingots
        ):
            return goal_id, float(needed)
        return None

    current = _completion(memory)
    return current[1] if current is not None and current != _completion(prev_memory) else None


def _looted_corpse(prev_memory: dict, memory: dict, hunt_reward_accum: float) -> float | None:
    """hunter -> world: growth in `len(memory["hunt_looted"])` since the
    last check — mirrors `curriculum.py::_memory_list_len_threshold`'s exact
    "this skill's own bookkeeping list grew" signal. `amount` is
    `hunt_reward_accum`, the confirmed loot value the caller has accumulated
    since `hunt_looted` last grew (see `_run_worker`'s own accumulator) —
    **not** a single tick's own episode reward, for the identical reason
    `_delivered_ingots` isn't: a corpse can hold more than one whitelisted
    item (`skills/hunt.py::LOOT_GRAPHICS` — gold plus gems), each looted in
    its own lift-then-place tick, so the confirmed reward can land across
    several ticks before the corpse is finally retired and `hunt_looted`
    grows. `0.0` (not `None`) for a genuinely empty corpse (the skill's own
    module docstring: "a corpse can legitimately be empty") — still a real
    loot-cycle event, just a zero-value one.
    """
    prev_n = len(prev_memory.get("hunt_looted") or ())
    now_n = len(memory.get("hunt_looted") or ())
    if now_n > prev_n:
        return hunt_reward_accum
    return None


def _accumulate_deliver_reward(current: float, prev_memory: dict, new_episode: Episode | None) -> float:
    """One tick's contribution to the miner's running `deliver_phase_reward`
    total (see `_delivered_ingots`'s docstring for why a running total, not
    a single tick's episode, is needed) — extracted from `_run_worker`'s own
    loop as its own pure, independently-testable function. `prev_memory.get
    ("smelt_phase") == "deliver"` is true both mid-trip and on the exact
    transition tick itself (that tick's own `memory` already reads
    `"return"` post-step, but it was `"deliver"` going into this tick's
    `step()` call), so this also folds in the final pile's own increment.
    """
    if (prev_memory.get("smelt_phase") == "deliver" and new_episode is not None
            and new_episode.summary.startswith("mine_smelt_deliver") and new_episode.reward > 0):
        return current + new_episode.reward
    return current


def _accumulate_hunt_reward(current: float, new_episode: Episode | None) -> float:
    """One tick's contribution to the hunter's running `hunt_reward_accum`
    total (see `_looted_corpse`'s docstring) — not phase-gated the way
    `_accumulate_deliver_reward` is, since a corpse's confirmed loot value
    can settle during `Hunt`'s own `hunt_val_settle` window, after
    `hunt_phase` has already reset back to `"engage"`.
    """
    if new_episode is not None and new_episode.summary.startswith("hunt") and new_episode.reward > 0:
        return current + new_episode.reward
    return current


def _accumulate_bank_reward(
    current: float,
    prev_memory: dict,
    new_episode: Episode | None,
) -> float:
    """Accumulate every confirmed stack deposited during one bank phase."""

    if (
        prev_memory.get("mkt_phase") == "bank"
        and new_episode is not None
        and new_episode.reward > 0
        and new_episode.summary.startswith(("blacksmith", "bank_gold"))
    ):
        return current + new_episode.reward
    return current


def _accumulate_sell_reward(
    current: float,
    prev_memory: dict,
    new_episode: Episode | None,
) -> float:
    """Accumulate sale gold that can arrive before dagger removal is observed."""

    if (
        prev_memory.get("mkt_phase") == "sell"
        and new_episode is not None
        and new_episode.reward > 0
        and new_episode.summary.startswith(("blacksmith", "sell_daggers"))
    ):
        return current + new_episode.reward
    return current


def _chronicle_events_this_tick(
    job: str, counterpart: str | None, prev_memory: dict, memory: dict, new_episode: Episode | None,
    *, fetch_entry_ingots: int | None, pack_ingots_now: int,
    deliver_phase_reward: float = 0.0, hunt_reward_accum: float = 0.0,
    sell_reward_accum: float | None = None,
    bank_reward_accum: float | None = None,
) -> list[tuple[str, str | None, float]]:
    """Every chronicle event `job`'s own detectors fired this tick, as
    `(kind, to_persona, amount)` triples — `village.py`'s only place that
    decides *which* detectors apply to which profession (the detectors
    themselves are profession-agnostic pure functions above). `counterpart`
    is supplied statically from `run_village`'s own trade-pairing wiring
    (never learned by a skill) — `None` for a solo miner/blacksmith or any
    other profession. `deliver_phase_reward`/`hunt_reward_accum` are the
    caller's own running accumulators (see `_run_worker`) — pure inputs to
    this function, not state it owns; `_delivered_ingots`/`_looted_corpse`'s
    own docstrings explain why a single tick's episode reward isn't enough.
    """
    events: list[tuple[str, str | None, float]] = []
    if job == "miner":
        amount = _delivered_ingots(prev_memory, memory, deliver_phase_reward)
        if amount is not None:
            events.append(("delivered_ingots", counterpart, amount))
    elif job == "blacksmith":
        amount = _picked_up_ingots(prev_memory, memory, fetch_entry_ingots, pack_ingots_now)
        if amount is not None:
            events.append(("picked_up_ingots", counterpart, amount))
        amount = _sold_to_vendor(
            prev_memory,
            memory,
            new_episode,
            sell_reward_accum,
        )
        if amount is not None:
            events.append(("sold_to_vendor", None, amount))
        amount = _banked_gold(
            prev_memory,
            memory,
            new_episode,
            bank_reward_accum,
        )
        if amount is not None:
            events.append(("banked_gold", None, amount))
        amount = _crafted_daggers(prev_memory, memory)
        if amount is not None:
            events.append(("crafted_daggers", None, amount))
    elif job == "hunter":
        amount = _looted_corpse(prev_memory, memory, hunt_reward_accum)
        if amount is not None:
            events.append(("looted_corpse", None, amount))
            # Two corpses can retire in one tick (`Hunt._advance` recurses
            # same-tick into an already-resolved next corpse), which would
            # otherwise silently undercount loot-cycle events. Keep the event
            # COUNT faithful with one zero-amount event per extra retirement —
            # the combined confirmed loot stays on the first event, since a
            # per-corpse split of a same-tick accumulator is unknowable.
            grew = (len(memory.get("hunt_looted") or ())
                    - len(prev_memory.get("hunt_looted") or ()))
            for _ in range(grew - 1):
                events.append(("looted_corpse", None, 0.0))
    return events


#: What each retirement reason MEANS, spelled out in the alarm rather than left to a
#: reader who would have to know which of `CapabilityGoalComplete`'s two branches, or
#: `expire_due`, closed the frame. Naming the bound is the point of the report: bounds 2
#: and 3 of the exit-edge hold are live-proven and bound 1 has never been distinguished
#: from an ordinary successful sale on any log (`docs/AUDIT-2026-07-29.md`, 2026-08-03).
#: `achieved` needs no gloss — it is the ordinary outcome, and annotating it would bury
#: the two that are not. See `life_runner.retirement_reason` for the bucket definitions.
_RETIREMENT_NOTES = {
    "giveup": " (bound 1: the FSM's give-up ladder)",
    "expired": " (bound 2: the frame's own deadline)",
}


#: Skills whose healthy steady state is to record NOTHING, so a run of them is not
#: evidence of anything being wrong. `wander` is the always-runnable fallback every
#: profession planner ends with — it returns RUNNING with reward 0 forever, and for a
#: `townsfolk` (`work_skill=None  # no job — just lives in town`) or a hunter with no
#: hostile in range it is the WHOLE job. `capability_wait` is its economy-mode twin:
#: no capability is admitted, so there is nothing to execute.
#:
#: Excluding them costs little detection, because an agent that is idle AND WEDGED is
#: not moving, and not moving is what the NO PROGRESS alarm beside this one watches.
#: Measured, on the wedged half of the 1800-tick forge run: the tinker sat behind a
#: stale `ui=shopbuy` with position and steps frozen and the runner printed
#: `NO PROGRESS for 560 ticks` beside the rule-vs-gate disagreement alarm. (Which SKILL
#: it was running through that stretch the log does not record — the point here is only
#: that the other alarm speaks when an idle agent is stuck rather than merely idle.)
_IDLE_SKILLS = frozenset({"wander", "capability_wait", "curriculum_wait"})


def _work_recorded(agent) -> int:
    """Every skill outcome this agent's ledgers have recorded — the work-liveness signal.

    For a plain `Agent` that is `agent.episodes.total_recorded`. For a **Life** it is the
    HUNT ledger PLUS the ECONOMY one, and the sum is the whole point: `Life.episodes` is
    `hunt_agent.episodes` alone (`warrior_life.py`), and a carpenter measured over 3000
    offline ticks recorded 0 there against 176 in the economy agent — so the hunt ledger
    is not a weak signal for a Life, it is a constant. That is why the alarm's first
    draft had to exclude every Life by testing `mode is None`, and why it then covered 2
    of the ~9 agents the four inline runners drive: `run_supply_pair` (WoodsmanLife +
    throttled CarpenterLife) and `run_warrior_village` (all WarriorLife) got ZERO
    work-liveness coverage, which is the same blindness the alarm exists to remove.
    Summed, the longest silence that same healthy 3000-tick carpenter shows is 22 ticks,
    an order of magnitude under the 240-tick threshold.

    Monotone, because both terms are. `_ThrottledAgent` proxies `econ_agent`, so a
    throttled Life sums the same two ledgers as an unthrottled one.

    `getattr` and the `try` are the same rule every readout in this loop follows:
    telemetry must never be the thing that raises, because stand-ins in tests and live
    gates are duck-typed and an alarm that crashes the worker it watches is worse than
    no alarm. Stated precisely, since a guard that cannot fire is worth knowing about:
    the FIRST read is already proven safe for `_run_worker`'s own caller, which does an
    unguarded `agent.episodes.total_recorded` before the loop starts — that guard is for
    other callers. The SECOND is the live one: `econ_agent` is whatever a Life or a
    proxy hands back, and it is read for the first time here.
    """
    try:
        total = int(getattr(agent.episodes, "total_recorded", 0) or 0)
    except Exception:  # noqa: BLE001 — telemetry must never break the run
        return 0
    try:
        econ = getattr(agent, "econ_agent", None)
        if econ is not None:
            total += int(getattr(econ.episodes, "total_recorded", 0) or 0)
    except Exception:  # noqa: BLE001 — same rule
        pass
    return total


def _doing_work(agent) -> bool:
    """Is this agent currently running a skill that is SUPPOSED to finish or pay?

    The arming condition for the work-liveness alarm, and the thing that keeps it from
    being 100% wrong on a healthy wanderer — see `_IDLE_SKILLS`. Reads
    `Agent.last_skill_name`, which a Life forwards from whichever inner agent it ticked,
    so hunt-mode idling and economy-mode idling are both recognised.

    Unknown (`None`) counts as working: a stand-in that does not expose the field keeps
    the alarm it would have had, rather than silently losing it. That direction is
    deliberate — this gate exists to remove FALSE alarms on skills known to be idle, not
    to require proof of work before speaking.
    """
    return getattr(agent, "last_skill_name", None) not in _IDLE_SKILLS


def stage_key_readout(memory, keys) -> str:
    """`mkt_phase=sell sell_stall=3` — the keys of `keys` this memory actually has.

    A named function for one reason: the SNAPSHOT is the whole of it, and a guarantee that
    lives inline in a 90-line status block is a guarantee no test can fail.

    The hazard is a check-then-get across a generator. Written inline as
    `" ".join(f"{k}={m[k]}" for k in keys if k in m)`, the `k in m` filter and the `m[k]`
    subscript are separated by a generator resumption — the interpreter may switch threads
    between them. These status blocks run on a runner's MAIN thread while the worker
    thread ticks the very agent whose memory this is, and the market FSMs pop whole key
    groups mid-trip: `SellItemCapability._CLEANUP_KEYS` (four of the eleven keys the forge
    pair asks for) on every trip end, and the `{tag}_stall`/`{tag}_last_pos` pair on every
    leg advance and every give-up. A `KeyError` here is not a lost line — the runners'
    status loops have no `except` and their workers are daemon threads, so it escapes the
    main thread and ends the run. `docs/AUDIT-2026-07-29.md` §35.6 records this as one of
    the two corrections follow-up 32's telemetry needed; it is a defect in the SHIPPED
    code independently of that telemetry, which is why it is fixed rather than avoided.

    `dict(memory)` first, then read only the copy. Copying a mapping runs no Python-level
    callback between its own steps, so it cannot be interleaved the way the filter/subscript
    pair can, and every field in the group then describes ONE instant instead of as many
    instants as there are fields.
    """
    snapshot = dict(memory)
    return " ".join(f"{k}={snapshot[k]}" for k in keys if k in snapshot)


def agent_walk_readout(agent, pos) -> str:
    """`market.walk_readout` for whatever object a runner handed `_run_worker`.

    Two things this owns, and both are why it is not a one-liner at the call site.

    WHICH MEMORY. A Life's market state is on its ECONOMY agent; everything else's is on
    itself. `getattr(agent, "econ_agent", None) or agent` is the same resolution
    `life_runner.frame_retirements` makes, for the same reason — `run_village`'s
    `--capability-goals` fleet hands this worker a plain `Agent` that has no `econ_agent`
    and never will. Falling back to a Life's OWN memory would be worse than failing: that
    is the hunt agent's, which carries none of these keys, so it would render a confident
    `trip=none` for a Life mid-transaction.

    AND IT CANNOT RAISE. `_run_worker` composes its status line outside any `except`, and
    its callers' loops have none either, so an `AttributeError` here escapes a daemon
    worker's parent thread and ends the run. Not hypothetical: `tests/test_forge_relocation`
    drives this worker with an `econ_agent` stand-in that has no `memory` at all, and the
    unguarded version raised on it — a duck-typed object this repo already ships. The
    honest rendering for "cannot read this agent's market state" is `trip=?`, which is
    already `walk_readout`'s own token for it, not a silent omission and not a confident
    `trip=none`.
    """
    try:
        econ = getattr(agent, "econ_agent", None) or agent
        return walk_readout(dict(econ.memory), pos)
    except Exception:  # noqa: BLE001 — telemetry must never break the run
        return "trip=?"


def _run_worker(agent: Agent, ticks: int, idx: int, status: dict, lock: threading.Lock,
                job: str, *, narrate: bool = False,
                chronicle: ChronicleLedger | None = None,
                counterpart: str | None = None,
                session_events: list[ChronicleEvent] | None = None) -> None:
    # Local for CONSISTENCY with the ten other in-function `life_runner` imports in
    # this file — not for a cycle.
    # An earlier draft of this comment claimed one; measured and refuted: importing
    # `anima2.life_runner` (or `anima2.warrior_life`) does not pull `anima2.village`
    # into `sys.modules`, `life_runner.py` contains no `village` import at any level
    # (`LifeRunner.run` takes its `worker` injected precisely so one never appears), and
    # a module-top `from .life_runner import frame_retirements` imports clean. The
    # module-BOTTOM re-exports at the end of this file (`MONITOR_PORT_BASE`,
    # `monitor_ports`) are module-level too, so "every other one is local" was wrong in
    # both halves. One import per worker, not per tick.
    from .life_runner import frame_retirements, hp_readout
    from .narrate import intent as agent_intent

    steps = says = 0
    ticks_done = 0
    last_say = ""
    # PHASE6.md item 2's own bookkeeping (only touched when `chronicle` is
    # set): a snapshot of `agent.memory` and `agent.episodes.total_recorded`
    # from the PREVIOUS tick, so this tick's detectors can see the exact
    # phase-key transition and whether a fresh episode landed — mirrors
    # `curriculum.py::CurriculumController`'s own `_episode_count_at_last`
    # "new episodes since last check" bookkeeping.
    prev_memory: dict = dict(agent.memory)
    prev_recorded = agent.episodes.total_recorded
    fetch_entry_ingots: int | None = None
    # Running accumulators for the two detectors whose skill has no
    # single-tick reward channel to read a delivery/loot-run's TOTAL off of
    # (see `_delivered_ingots`/`_looted_corpse`'s own docstrings — a
    # multi-pile ingot haul or a multi-item corpse pays its reward across
    # several ticks, not as one lump sum on the transition/growth tick).
    deliver_phase_reward = 0.0
    hunt_reward_accum = 0.0
    sell_reward_accum = 0.0
    bank_reward_accum = 0.0
    # Why this worker stopped, appended to its own status line when it does. A worker
    # that has ENDED and one that is merely idle look identical from the outside — the
    # monitor keeps reprinting whatever `status[idx]` last held, and every reading taken
    # off it silently becomes a reading of a frozen snapshot. Live-caught the expensive
    # way: a throttled mage exhausted its tick budget early, and its stale last
    # observation was then read for several runs as "the mage cannot see the purse".
    stopped = ""
    # No-progress liveness (health-check follow-up #1's guard): forge2's miner issued
    # ZERO actions for an entire run and nothing flagged it — a dead agent and a
    # patient one look identical from outside. If reward, steps, speech AND position
    # all freeze for _QUIET_TICKS straight, say so loudly and keep saying it. The
    # threshold sits far above any legitimate quiet stretch (a bank trip is ~12 ticks,
    # a craft item ~10) and far below the half-hour forge2 sat dead.
    _QUIET_TICKS = 40
    _quiet = 0
    # WEDGED WALK — the third liveness alarm, follow-up 35. `NO PROGRESS` above is
    # structurally unable to fire during the failure this one is for, and that is why the
    # 2026-08-11 day (`docs/AUDIT-2026-07-29.md` §30.2 — 203 `sell_tongs` frames given up
    # at age 8, 0 gold banked) was silent on every instrument the runner had.
    #
    # `steps` counts EMITTED walks (`steps += isinstance(action, Walk)`), not movement,
    # and `market._market_walk_toward` returns `SkillResult(RUNNING, Walk(...), reward)`
    # on every tick of a greedy approach. So an agent hammering a blocked tile bumps
    # `steps` every tick while its position never changes: the pulse always differs,
    # `_quiet` resets every tick, and an alarm reading "reward/steps/speech/position all
    # frozen" can never reach 40. Measured on that day's shape (a real `CarpenterLife`
    # walled off from its vendor, 400 ticks, frozen on one tile): **0 fires**.
    #
    # THE OBVIOUS FIX IS WRONG, and it was implemented and measured before being thrown
    # away: simply dropping `steps` from the pulse detects the wedge (0 -> 9 fires) but
    # SILENTLY MOVES the surviving alarm's threshold. `_quiet` does not reset when a
    # walk leg gives up — the position is still frozen through the give-up, the retry and
    # the next wedge — so "a stalled leg cannot reach 40 because `stall_limit` is 6" is
    # false. Measured: a TRANSIENT obstruction cleared after 42 worker ticks (~18s at the
    # 2026-08-03 forge cadence) fires, and 41 does not. A one-tick margin on an alarm
    # this project has already been burned by false-firing (see `_STALL_TICKS` below:
    # "ten times at exactly 40 ticks, three of them in the HEALTHY first half") is not a
    # margin. Review-caught, by three independent lenses, after the author's own transient
    # grid stopped at 41 — one tick below the first firing point.
    #
    # So the wedge gets its OWN alarm and its own threshold, and `NO PROGRESS` is left
    # byte-identical. That makes this change strictly ADDITIVE: no existing alarm's
    # behaviour moves, so no measured threshold is silently re-tuned. It is also the rule
    # `NO OUTPUT` already established in this file — two different failures must never
    # share one line of text, because identical text in both halves is zero information.
    #
    # 240 IS DERIVED, not picked. It is `_STALL_TICKS`, the work-liveness threshold
    # already measured in this file against the longest healthy silence any live log
    # contains (159 ticks, Grimm's 600-tick run). The derivation is that BOTH alarms
    # answer the same question — "is this stretch long enough that no healthy agent
    # explains it?" — and this one can afford at least as much patience as that one,
    # because the failure it names is PERMANENT: §30.2's wedge lasted the entire day, so
    # detection latency costs nothing while a false fire costs the operator's trust.
    #
    # What it is NOT measured against: a live obstruction-duration distribution. No log
    # records how long a real NPC stands on a tile. 240 ticks is ~100s wall at forge
    # cadence, six times the 42 that the rejected version would have fired at.
    #
    # THE MAJORITY RULE, and why the first version was wrong — caught on this alarm's
    # FIRST live run (2026-08-13, §39). The guard was `_tried > 0`: "were any walks
    # emitted during this stretch?". On a shard that fired six times on a tinker who was
    # BANKING 1483 GOLD, printing `3 walk actions emitted over 1440 ticks` while he stood
    # on his own craft tile. Three stray walks across 1440 ticks reclassified a stationary
    # crafter as wedged, and the line read absurd on its face.
    #
    # The failure gets WORSE with time, which is the part worth naming: `_tried` is fixed
    # once the strays are emitted while `_still` keeps climbing, so the evidence ratio
    # DECAYS while the alarm keeps firing — 1.25% at the first fire, 0.21% at the sixth.
    #
    # Measured separation, live false positive versus the offline §30.2 reproduction:
    #
    #     productive tinker (false) : 1.25% -> 0.21% of ticks were walk attempts
    #     real wedge (true)         : 75.8% -> 75.1%, stable across every fire
    #
    # A ~60x gap, so the threshold is not a tuned number and is deliberately not written
    # as a percentage: a MAJORITY of the stretch must be walk attempts. That is a
    # statement about what a wedge IS — an agent walking, every tick, into the same tile —
    # rather than a constant someone must later re-derive.
    _WEDGE_TICKS = 240
    _still = 0
    _last_stillness = None
    #: `steps` when the current stillness stretch began. `steps - _still_steps` is the
    #: count of walks that went nowhere, which is what separates a wedge from an agent
    #: that is merely idle — and it must be REBASED per stretch, not read since t=0, or
    #: an agent that walked earlier and then died is reported as a wedge.
    _still_steps = 0
    #: Consecutive ticks a Life has reported an overdue capability frame — same throttle
    #: as `_quiet`, for the same reason (see the report below).
    _overdue = 0
    _last_pulse = None
    # WORK-liveness, the second alarm this loop needs and the reason `NO PROGRESS`
    # beside it was not enough. That one is BODY-liveness — it watches reward, steps,
    # speech AND position, so an agent that keeps WALKING resets it forever. The forge
    # miner died exactly that way, twice: on 2026-08-03 Grimm's cumulative reward froze
    # at `out+176.9` at t=765 and never moved again — no smelt and no deliver on any of
    # the 126 remaining samples — while he went on relocating between mine faces, so
    # `NO PROGRESS` fired ten times at exactly "40 ticks" and never escalated, three of
    # them in the HEALTHY first half. Identical text in both halves is zero information.
    # `docs/AUDIT-2026-07-29.md` lines 153-156 already named this line as worth adopting
    # after forge2; it was not adopted, and it happened again.
    #
    # The signal is `_work_recorded` — the agent's own count of skill outcomes that were
    # terminal OR rewarded (`agent.py`'s record filter), summed over every ledger it
    # owns. Grimm's death is precisely a skill returning `RUNNING, reward=0` forever,
    # which records nothing. It is monotone (unlike `total_reward()`, which sums a
    # bounded 500-entry deque), orthogonal to walking, and it counts REPEATS, not
    # variety — a miner swinging productively in one skill for 3000 ticks bumps it
    # thousands of times, so this is not a procedure-diversity heuristic.
    #
    # It is armed by `_doing_work`, and without that gate it is 100% WRONG on a healthy
    # agent. "Recorded nothing for 240 ticks" is Grimm's death and it is also the
    # SPECIFIED behaviour of a `townsfolk` — `work_skill=None  # no job — just lives in
    # town (wander + greet)`, in the DEFAULT roster at `--townsfolk 1`. Measured through
    # this very function (review, 2026-08-03): 1000 ticks, five neighbours, five greets
    # in the first five ticks and then `wander` forever — `NO PROGRESS` fires 0 times
    # (walking resets it), this alarm fires at 240/480/720/960 and the run ends
    # `!stalled  [BUDGET SPENT · STALLED 995]`. An idle hunter is the same shape for a
    # different reason (`Hunt.can_run` false with no hostile → the same `wander`
    # fallback): 500 ticks, eps=0, two fires. So the counter only advances on ticks
    # where the agent ran a skill that is supposed to finish or pay. Grimm's dead 1035
    # ticks read `ph=mine` on every sample with the mining window still advancing
    # (`win=5/6`, `9/10`, `14/15`) — his work skill was selected and running, not
    # wandering — so the catch survives the gate. Verified on a real `Agent` whose work
    # skill stops finishing while it keeps walking: fires at 240 and 480, names the
    # skill, ends `!stalled · STALLED 486`.
    #
    # 240 ticks, measured, not chosen. Sample cadence in both 2026-08-03 forge logs is
    # 9 ticks median / 10 max, so 240 ticks ≈ 25 samples ≈ 100s wall. Grimm's longest
    # HEALTHY reward-silence stretch across both runs is 159 ticks (600-tick log,
    # t=414→573: `ph=mine`, steps frozen, two full relocations with all-stuck windows
    # (15,22)→(19,19)→(14,14), then live rock and recovery) — indistinguishable from
    # death for its whole length, which is why nothing shorter can work. At T=240:
    # 0 false fires in the healthy first 765 ticks of the 1800 log and 0 across the
    # whole 600 log, and Grimm's death is called at t≈1005 — 44% of the run still to go,
    # and before the last two of its six deposits (t=1039 and t=1122; the FREEZE at
    # t=765 is what preceded five of the six — an earlier draft of this comment
    # attributed the freeze's timing to the alarm's). T=160 also scores 0/0 but clears
    # the measured 159 by ONE tick, which is not a margin. Reward-silence is an UPPER
    # bound on episode-silence (every reward change implies a record, not conversely),
    # so those false-positive counts are conservative — and t≈1005 is a PREDICTION off
    # the reward proxy, not a measurement: those logs print no `eps=` field, which is
    # why the status line below now carries one.
    #
    # The proxy's own limit, stated because the comment above is not a proof of
    # detection: it is orthogonal to WALKING, not to every zero-reward terminal skill.
    # `RecoverDeath` (`skills/recovery.py`) and `Survive`'s bandage-confirm both return
    # terminal statuses with no reward, so an agent whose work is dead but which cycles
    # death/resurrection once per 240 ticks keeps this counter moving. That failure
    # shape has not been observed; it is what this alarm would still miss.
    #
    # EVERY agent, Life or not — the `mode is None` gate this replaced was the alarm's
    # own blind spot. It was there for a real reason: a Life's `episodes` is its HUNT
    # ledger, the tinker Pim spent 180 of 208 samples in economy mode, and under a
    # hunt-only proxy he fires 7 times in the 1800 run and 2 in the 600 run while being
    # the most productive agent present. But excluding Lives left `run_supply_pair` and
    # `run_warrior_village` with no work-liveness coverage at all — a WoodsmanLife that
    # dies the way Grimm died, on the runner that feeds the board chain, still silent.
    # `_work_recorded` sums the hunt AND economy ledgers instead, which is monotone,
    # orthogonal to mode switching, and measured at a 22-tick worst-case silence on a
    # healthy 3000-tick carpenter. (`_ThrottledAgent` proxies `econ_agent` as of this
    # change, so `econ_agent` and `mode` would now agree as Life discriminators — the
    # older claim here that an `econ_agent` test misclassifies the throttled carpenter
    # and mage described the pre-change class and is kept only as the history of why the
    # blast-radius review insisted on a discriminator that a proxy could not fake.)
    _STALL_TICKS = _QUIET_TICKS * 6
    _stalled = 0
    _stall_since = 0
    _recorded = _work_recorded(agent)
    #: Cursor for the frame-retirement report: the last frame ID already reported, NOT
    #: a count. Goal-stack history is bounded at 128, so a count silently under-reports
    #: after an overflow; ids are monotonic, so `> _last_retired` is total.
    _last_retired = 0
    #: The narration a human reads (`anima2/narrate.py`). `_said` is the SHORT line still
    #: owed to the game journal: it waits for a tick the agent spent doing nothing, because
    #: spending a tick on speech would skip an agent tick and silently distort every
    #: per-tick instrument that reads `new_journal` — the mining cause split counts one
    #: sample per tick and would merge two swings' verdicts into one.
    _intent = ""
    _said = ""
    # NOTHING LANDS — the fourth liveness alarm, follow-up 37, and the last of the four
    # blindnesses the 2026-08-11 day exposed.
    #
    # The other three all answer "has this agent stopped?". None answers "is any of this
    # WORKING?", and that is a different failure with its own live history:
    #
    #   * §30.2 — 203 `sell_tongs` frames given up in one day, 0 gold banked.
    #   * §22.2 — a tinker whose vendor had sold out re-admitted `buy_iron` **49 more
    #     times**, each trip walking to the shop, opening the window, re-rolling its full
    #     budget and coming back empty. Every one was CORRECT; the loop was not.
    #
    # Neither is visible to the others, and the buy case shows why the three cannot be
    # patched into covering it. That tinker WALKED (so `NO PROGRESS` and `WEDGED WALK`
    # both reset on its position) and RECORDED (so `NO OUTPUT` read it as productive —
    # measured on the wedge fixture: `_work_recorded` advances every 8 ticks while
    # `total_reward` stays at exactly 0.000). An agent can be busy, mobile, finishing
    # skills and completing NOTHING, indefinitely, and until now the tape said it was fine.
    #
    # The signal is the retirement REASON, which `frame_retirements` already computes and
    # this loop already drains every tick — so this costs one comparison per retirement
    # and no new state anywhere else. `achieved` is the only reason that clears it: it is
    # the only one that means a transaction completed. `giveup` / `expired` / `replaced` /
    # `cancelled` all mean the opposite, however healthy the agent looks from outside.
    #
    # REWARD WAS THE OBVIOUS SIGNAL AND IT CANNOT BE USED. Two reasons, both measured:
    # `Episodes.total_reward()` sums a BOUNDED 500-entry deque (`memory.py`), so it is not
    # monotone and a "frozen reward" test drifts as old episodes fall out — this file's own
    # `_STALL_TICKS` comment already rejects it for that. And offline it does not
    # discriminate at all: all five healthy Lives run 3000 ticks with ZERO paid events,
    # because `MockBody` has no vendor to sell to. A signal whose healthy case cannot be
    # constructed cannot have a threshold measured against it.
    #
    # THE THRESHOLD IS THE HARD PART, and `_STALL_TICKS` is the WRONG answer — measured,
    # after writing it down as the right one. A healthy day is not a steady drip of
    # achievements: §17's 1800-tick forge run banked SIX times and did it as one early 23g
    # deposit and then five more only after t≈756, because the tinker was working through a
    # single 69-ingot delivery. At 240 ticks this alarm fires repeatedly across that gap on
    # a run that banked 503g. That is the follow-up 35 mistake exactly — a threshold
    # inherited by analogy instead of measured — and it is caught here only because the gap
    # happens to be written down.
    #
    # So it is derived the way `_STALL_TICKS` itself was: the longest measured HEALTHY
    # silence, times the same ~1.5 safety factor that turned 159 into 240. Applied to the
    # 756-tick gap above: 756 x 1.5 = 1134, rounded to 1200 (5 x `_STALL_TICKS`). It still
    # fires within an ordinary 1800-tick day on §30.2's shape, which achieved NOTHING for
    # the whole run.
    #
    # PROVISIONAL, and the honest statement is that it rests on ONE observation. No live
    # log records the distribution of gaps between achievements — the tape has never
    # carried the streak. `landed=` on the status line collects exactly that, so the next
    # ordinary forge day supplies the data this number should have been derived from; see
    # `docs/AUDIT-2026-07-29.md` §38 for the prediction, written before the run.
    #
    # It cannot double-report with `NO OUTPUT`: the counter only advances while frames are
    # still RETIRING (a retirement inside the last `_STALL_TICKS`), and an agent that has
    # stopped retiring anything is precisely what `NO OUTPUT` is for. One failure, one
    # alarm — the rule `NO OUTPUT` itself established here.
    #
    # NOT measured against: a healthy SELL loop, which no offline fixture can produce
    # (`MockVendor` models the buy side only — follow-up 34). The healthy control here is
    # a buy that achieves.
    _THRASH_TICKS = _STALL_TICKS * 5
    _unachieved = 0
    _thrash = 0
    _last_retire_tick = 0
    #: Cumulative, for `landed=`: how many capability frames retired at all, and how many
    #: of those ACHIEVED. Counted here rather than re-derived from `retirement_tally`
    #: because that one reads bounded history and says `>=` once 128 frames overflow it.
    _retired_total = 0
    _achieved = 0
    # DEATH — the fourth self-report, and the one that answers the question the other
    # three structurally cannot. Follow-up 18, named three times in
    # `docs/AUDIT-2026-07-29.md` (2026-07-30, follow-up 17, §8.6) and deferred each
    # time. §8.1's own summary of what the work-liveness line bought: "the tape now
    # says the miner STOPPED and still cannot say whether he DIED" — a death, a lost
    # pickaxe and a dead vein all read as `out+176.9 eps=45` frozen forever.
    #
    # Two readings, because one of them is not enough:
    #  - `hp=` on the status line is a LEVEL, and it decays to nothing. Grimm's freeze
    #    at t=765 would show `hp=DEAD` only on the samples he was still a ghost for; a
    #    death he was resurrected from before the next ~4s sample is invisible to it,
    #    and that is precisely the shape §8.1 names as the work-liveness proxy's own
    #    blind spot ("an agent whose work is dead but which cycles death/resurrection
    #    once per 240 ticks keeps this counter moving and stays silent").
    #  - `_deaths` is an EDGE count, read per TICK, so it survives the sampling gap the
    #    same way the retirement report above does. `deaths=2` beside a frozen `eps=`
    #    is a different diagnosis from `deaths=0` beside the same frozen `eps=`.
    #
    # Counted HERE, off the worker's own observation, rather than read off the
    # `death_episode` marker `Agent.tick` already maintains — because that marker is
    # per-AGENT and a Life owns two of them, each with its own `death_observed_dead`
    # flag, exactly one ticked per orchestrator tick. Both reductions were measured on
    # a real `CarpenterLife` over `MockBody` rather than argued: for ONE staged death
    # observed first by the economy agent and then by the hunt agent under the death
    # override, `hunt + econ` reports **2**; for TWO staged deaths seen by one agent
    # each, `max(hunt, econ)` reports **1**. A sum double-counts and a max
    # under-counts, so neither is a death counter. One body has one death; one worker
    # watching that body counts it once. (This is `_work_recorded`'s lesson —
    # a Life's telemetry cannot be read off one of its two ledgers — with the opposite
    # conclusion, because episodes ADD across the two agents and deaths do not.)
    _was_dead: bool | None = None
    _deaths = 0
    _dead_since = 0
    for _ in range(ticks):
        if not agent.body.connected:
            stopped = "DISCONNECTED"
            break
        action = agent.tick()
        # READ the tick's own observation — never observe() again. new_journal is
        # since-LAST-observe: a second consumer on the same body steals the batch,
        # and the agent's next tick sees an empty journal. This single line kept
        # the relocation window EMPTY through three full forge days (the miner's
        # verdict clilocs landed HERE and were discarded) while the standalone
        # probes — one consumer — kept passing. The Lives never hit it because
        # `_CachingBody` exists for exactly this; a plain Agent has no such shield.
        obs = getattr(agent, "_last_observation", None) or agent.body.observe()
        p = obs.player.pos

        if chronicle is not None:
            memory = agent.memory
            new_episode = agent.episodes.recent(1)[0] if agent.episodes.total_recorded > prev_recorded else None
            pack_ingots_now = _pack_ingot_count(obs) if job == "blacksmith" else 0
            if job == "blacksmith" and prev_memory.get("bs_state") != "fetch" and memory.get("bs_state") == "fetch":
                fetch_entry_ingots = pack_ingots_now  # baseline captured once, at fetch entry
            # Accumulate BEFORE detecting — see `_accumulate_deliver_reward`/
            # `_accumulate_hunt_reward`'s own docstrings.
            if job == "miner":
                deliver_phase_reward = _accumulate_deliver_reward(deliver_phase_reward, prev_memory, new_episode)
            if job == "hunter":
                hunt_reward_accum = _accumulate_hunt_reward(hunt_reward_accum, new_episode)
            if job == "blacksmith":
                sell_reward_accum = _accumulate_sell_reward(
                    sell_reward_accum,
                    prev_memory,
                    new_episode,
                )
                bank_reward_accum = _accumulate_bank_reward(
                    bank_reward_accum,
                    prev_memory,
                    new_episode,
                )
            for kind, to_persona, amount in _chronicle_events_this_tick(
                job, counterpart, prev_memory, memory, new_episode,
                fetch_entry_ingots=fetch_entry_ingots, pack_ingots_now=pack_ingots_now,
                deliver_phase_reward=deliver_phase_reward, hunt_reward_accum=hunt_reward_accum,
                sell_reward_accum=sell_reward_accum,
                bank_reward_accum=bank_reward_accum,
            ):
                # queue_event is O(1), in-memory-only, threading.Lock-guarded
                # — safe to call from this (or any other agent's) worker
                # thread. See chronicle.py's module docstring: the ONLY
                # file I/O happens once, later, from run_village's own
                # joined main thread (chronicle_ledger.flush()).
                event = chronicle.queue_event(tick=agent.ticks, from_persona=agent.persona.name,
                                              to_persona=to_persona, kind=kind, amount=amount)
                # PHASE6.md item 3: also collect this agent's OWN events into
                # its private, session-scoped list (independent of the
                # shared ChronicleLedger's in-memory queue, which mixes every
                # agent together and is cleared by flush()) — the forum
                # block reads this back after the run to ground the day's
                # post, with no dependency on data/chronicle.jsonl's
                # cross-session persistence or a since_tick heuristic.
                if session_events is not None:
                    session_events.append(event)
                if kind == "looted_corpse":
                    hunt_reward_accum = 0.0  # this batch's total has been attributed
                # delivered_ingots needs no reset here — memory.get("smelt_phase")
                # is always "return" (not "deliver") whenever it fires, so the
                # unconditional phase check just below already resets it.
            if job == "blacksmith" and memory.get("bs_state") != "fetch":
                fetch_entry_ingots = None
            if memory.get("smelt_phase") != "deliver":
                deliver_phase_reward = 0.0  # never entered/no longer in the deliver phase
            if memory.get("mkt_phase") != "bank":
                bank_reward_accum = 0.0
            if memory.get("mkt_phase") != "sell":
                sell_reward_accum = 0.0
            prev_memory = dict(memory)
            prev_recorded = agent.episodes.total_recorded

        steps += isinstance(action, Walk)
        # A Life that has detected a rule-vs-gate disagreement says so LOUDLY, every
        # tick it persists. Six live failures sat in exactly this state looking like an
        # agent at work; the one thing that must never happen again is it being quiet.
        _disagree = getattr(agent, "rule_gate_disagreement", None)
        if _disagree is not None:
            print(f"  ** {agent.persona.name}: RULE-vs-GATE DISAGREEMENT — wants "
                  f"{_disagree[0]!r}, admission refuses, {_disagree[1]} ticks **")
        # The other silent stall the Life can now self-report: a capability frame that has
        # outlived its own deadline and still cannot reach a safe yield point, so neither
        # the FSM's give-up ladder nor `expire_due` can close it. The orchestrator has
        # already released its economy hold for it (`WarriorLife.tick`'s third bound) and
        # pointed the stale-UI repair at it; what is left to say is that the frame is
        # STILL there, because until it closes no new capability can be admitted.
        # Throttled like NO PROGRESS beside it: the state persists for as long as the
        # frame does, and unthrottled it measured 3,881 identical lines in one 4,000-tick
        # run — enough to bury both of the other two alarms in this loop.
        if getattr(agent, "frame_overdue", False):
            if _overdue % _QUIET_TICKS == 0:
                print(f"  ** {agent.persona.name}: FRAME OVERDUE for {_overdue + 1} ticks "
                      f"— a transaction past its deadline that cannot yield still holds "
                      f"the capability stack **")
            _overdue += 1
        else:
            _overdue = 0
        # The third self-report, and the one that gives BOUND 1 of the exit-edge hold
        # its first observable signature. A retirement is an EDGE — one per transaction
        # — unlike `frame_overdue`, whose LEVEL signal measured 3,881 identical lines,
        # so this one is deliberately NOT throttled. It is printed from HERE, every
        # tick, beside the other two: the runners' own status loops sample every ~4s
        # and cannot see an edge. It survives that sampling gap anyway, because it is
        # read off durable goal-stack HISTORY rather than a live field.
        for _fid, _cap, _age, _budget, _why in frame_retirements(agent,
                                                                 after_id=_last_retired):
            _last_retired = _fid
            print(f"  ** {agent.persona.name}: FRAME RETIRED {_cap}#{_fid} "
                  f"age={_age}/{_budget} -> {_why}{_RETIREMENT_NOTES.get(_why, '')} **")
            # Follow-up 37's bookkeeping, on the drain that already runs — see
            # `_THRASH_TICKS`. An ACHIEVED retirement is the only thing that clears it,
            # because it is the only evidence a transaction ever completes.
            _last_retire_tick = ticks_done
            _retired_total += 1
            if _why == "achieved":
                _achieved += 1
                _unachieved = _thrash = 0
            else:
                _unachieved += 1
        if narrate:
            _short, _detail, _key = agent_intent(agent, obs)
            if _key != _intent:
                # Into the TAPE, with tick and tile, because the point is not only to be
                # readable live: a post-hoc reader reconstructing why an agent did
                # something is exactly what §41 needed a whole workflow to do from bare
                # counters. `~~` rather than `**` so narration and alarms grep apart.
                print(f"  ~~ {agent.persona.name} t={ticks_done} @({p.x},{p.y}) "
                      f"{_short} | {_detail}")
                _intent, _said = _key, _short
            if _said and action is None:
                # A tick the agent chose to spend on nothing — speech is free here, and
                # only here. See `narrate.py` for why this is a hard constraint.
                try:
                    agent.body.act(Say(text=_said[:120]))
                    says += 1
                    last_say = _said
                except Exception:  # noqa: BLE001 — narration must never break the run
                    pass
                _said = ""
        if isinstance(action, Say):
            says += 1
            last_say = action.text
        ticks_done += 1
        # The death edge, unthrottled for the same reason `FRAME RETIRED` is: one line
        # per event, not per tick the condition holds. `hp=DEAD` on the status line is
        # the level signal beside it; the ghost stretch is bounded by the run, so this
        # cannot outrun the retirement report's own volume. See `_deaths` above for why
        # it is counted here rather than read off `Agent.memory["death_episode"]`.
        _dead_now = bool(obs.player.dead)
        if _was_dead is None:
            if _dead_now:
                # Dead on the worker's FIRST observation. Counted, because a run that
                # begins with a corpse must not read as a run with no deaths — and
                # named apart from a death this worker WATCHED, because it did not.
                _deaths, _dead_since = 1, ticks_done
                print(f"  ** {agent.persona.name}: DEAD at first observation "
                      f"@({p.x},{p.y}) — counted as death #1, though it happened "
                      f"before this worker's first tick **")
        elif _dead_now and not _was_dead:
            _deaths += 1
            _dead_since = ticks_done
            print(f"  ** {agent.persona.name}: DIED at ({p.x},{p.y}) "
                  f"— death #{_deaths} **")
        elif _was_dead and not _dead_now:
            # The recovery, and how long it took. A death that resolves in 30 ticks and
            # one the agent never comes back from look identical in a death COUNT, and
            # the second is the one that explains a silent rest-of-run.
            print(f"  ** {agent.persona.name}: BACK ALIVE at ({p.x},{p.y}) after "
                  f"{ticks_done - _dead_since} ticks dead (death #{_deaths}) **")
        _was_dead = _dead_now
        _pulse = (round(agent.episodes.total_reward(), 3), steps, says, p.x, p.y)
        _quiet = _quiet + 1 if _pulse == _last_pulse else 0
        _last_pulse = _pulse
        if _quiet and _quiet % _QUIET_TICKS == 0:
            print(f"  ** {agent.persona.name}: NO PROGRESS for {_quiet} ticks "
                  f"(reward/steps/speech/position all frozen) **")
        # WEDGED WALK — the third liveness alarm, and follow-up 35. See `_WEDGE_TICKS`.
        # The same pulse WITHOUT `steps`, so a walk that emits and never moves
        # accumulates here instead of resetting everything.
        _stillness = (round(agent.episodes.total_reward(), 3), says, p.x, p.y)
        _still = _still + 1 if _stillness == _last_stillness else 0
        if _still == 0:
            _still_steps = steps  # baseline: walks emitted BEFORE this stretch began
        _last_stillness = _stillness
        _tried = steps - _still_steps
        # A MAJORITY of the stretch must be walk attempts. `_tried > 0` was the first
        # version and it false-fired on its very first live run — see `_WEDGE_TICKS`.
        if _still and _tried * 2 >= _still and _still % _WEDGE_TICKS == 0:
            print(f"  ** {agent.persona.name}: WEDGED WALK — {_tried} walk actions "
                  f"over {_still} ticks ({_tried * 100 // _still}% of them) and the "
                  f"position never changed (t={ticks_done}, @({p.x},{p.y})) **")
        # NOTHING LANDS — transactions keep retiring and none of them achieve. See
        # `_THRASH_TICKS`. The "still retiring" clause is what keeps this from
        # double-reporting an agent that `NO OUTPUT` already owns.
        if _unachieved and ticks_done - _last_retire_tick < _STALL_TICKS:
            _thrash += 1
        else:
            _thrash = 0
        if _thrash and _thrash % _THRASH_TICKS == 0:
            print(f"  ** {agent.persona.name}: NOTHING LANDS — {_unachieved} capability "
                  f"frames retired in a row and not one ACHIEVED, over {_thrash} ticks "
                  f"(t={ticks_done}) — the agent is busy and completing nothing **")
        # The work-liveness detector described where `_STALL_TICKS` is defined.
        # Deliberately NOT the string "NO PROGRESS": the two alarms are different
        # failures (that one is body-liveness, this one work-liveness), both stay, and
        # `tests/test_forge_relocation.py` asserts the absence of that exact string for
        # a walking agent. The count in the text ESCALATES (240 → 480 → 720 → 960),
        # unmistakably unlike the ten identical "40"s the other alarm produced.
        _recorded_now = _work_recorded(agent)
        if _recorded_now != _recorded or not _doing_work(agent):
            # Either it produced, or it is idling in a skill that produces nothing by
            # design. Both reset — an idle stretch is not evidence, so it must not
            # ACCUMULATE toward one either.
            _stalled = 0
            _stall_since = ticks_done
        else:
            _stalled += 1
            if _stalled % _STALL_TICKS == 0:
                print(f"  ** {agent.persona.name}: NO OUTPUT for {_stalled} ticks "
                      f"(eps={_recorded_now} unchanged since t={_stall_since}, "
                      f"skill={getattr(agent, 'last_skill_name', None)}) — "
                      f"no skill has finished or paid since **")
        _recorded = _recorded_now
        with lock:
            # `eps=` rides here for EVERY agent, Life or not, and for a Life it is the
            # SUM of both ledgers (`_work_recorded`) — a hunt-only reading would print
            # `eps=0` next to `retired=176` for a carpenter, one status block saying
            # the agent has recorded nothing while it retired 176 capability frames.
            # The alarm above scrolls away between status blocks; this line is reprinted
            # every ~4s and is what an operator actually reads. It is also the only way
            # the next run measures the real `total_recorded` distribution — today's
            # logs cannot supply it.
            # `hp=` and `deaths=` ride here for EVERY agent on EVERY runner, which is
            # why they are built in this worker and not in the six runners' own status
            # loops. `run_forge_pair`'s `grimm[…]` group was where follow-up 18 proposed
            # putting hp, and one group on one runner is what that would have bought;
            # every runner prints this snapshot directly beneath its own line, so the
            # reading lands at the same sample and in the same place on screen for the
            # forge pair, the supply pair, the warrior village, the artisan+mage
            # pipeline, `run_village` and `LifeRunner.run` alike.
            #
            # `deaths=` prints even at 0, unlike `!stalled` beside it. An ABSENT field
            # is ambiguous — no deaths, or a build that could not count them — and the
            # whole point of this pair is to make a frozen `eps=` beside `deaths=0`
            # (a lost tool, a dead vein) a different diagnosis from the same frozen
            # `eps=` beside `deaths=3`.
            # `trip=` rides HERE — beside `@(x,y)`, on the one line every runner prints
            # for every agent — and that placement is follow-up 32's actual content.
            # A coordinate has been on this line since 2026-06-30 (`6f279a7`), six weeks
            # before the day the follow-up was written about, so "no per-tick coordinate
            # is printed anywhere today" was never the gap. What was missing is the
            # coordinate's COUNTERPART: the tile the walk is trying to reach and the
            # reach it needs. The two are useless apart and unambiguous together.
            #
            # Mounted on the WORKER rather than on `life_runner.telemetry_line`, which
            # was the first attempt and structurally could not work: `telemetry_line`
            # requires a Life (`life.econ_agent`), and `run_village`'s trade blacksmith —
            # the ONLY production agent carrying the multi-waypoint `VENDOR_SPOT` route
            # that `walk_readout` handles `route[leg]` for — is a plain `Agent`. The
            # instrument would have missed the exact configuration its own design
            # justification cited. Review-caught.
            #
            trip = agent_walk_readout(agent, p)
            # `landed=<achieved>/<retired>+<streak>` — the LEVEL signal beside the
            # `NOTHING LANDS` edge, and the measurement `_THRASH_TICKS` should have been
            # derived from. `retired=` already tallies outcomes on the LIFE line, and
            # §30.2's day printed `retired>=128:128g` — 128 give-ups, zero achieved —
            # while the day still went undiagnosed, so a cumulative tally on three
            # surfaces is demonstrably not enough. This is on EVERY agent's line, and it
            # carries the STREAK, which no tally can show: `+30` is thirty transactions
            # in a row that completed nothing. It prints even at `0/0`, for the `deaths=`
            # reason two fields along — an absent field is ambiguous between "nothing
            # retired" and "a build that could not count".
            line = (f"{agent.persona.name:<9} {job:<10} @({p.x},{p.y}) t={ticks_done} "
                    f"hp={hp_readout(obs)} deaths={_deaths} {trip} "
                    f"landed={_achieved}/{_retired_total}"
                    f"{f'+{_unachieved}' if _unachieved else ''} "
                    f"out+{agent.episodes.total_reward():.1f} eps={_recorded_now} "
                    f"steps={steps} says={says}")
            if _stalled >= _STALL_TICKS:
                line += " !stalled"
            if last_say:
                line += f'  "{last_say[:60]}"'
            status[idx] = line
    else:
        stopped = "BUDGET SPENT"
    # Say so, permanently. Everything printed from here on is a frozen snapshot, and a
    # reader that cannot tell that will mistake this agent's last state for its current
    # one — which is exactly how a stale observation got read as a live one.
    #
    # A stall is folded into the SAME suffix, because the terminal line is the first
    # thing any post-hoc reader looks at and Grimm's was
    # `Grimm  miner  @(2593,499) t=1800 out+176.9 steps=139 says=0  [BUDGET SPENT]` —
    # the most misleading possible summary of an agent that had produced nothing for
    # its last 1035 ticks.
    with lock:
        tail = stopped or "ENDED"
        if _stalled >= _STALL_TICKS:
            tail += f" · STALLED {_stalled}"
        status[idx] = f"{status.get(idx, agent.persona.name)}  [{tail}]"


class _CountingClient:
    """Wraps an `LLMClient`, counting `complete()` calls — scoped to this script's
    own run, never persisted (contrast `llm.py::_UsageLoggingClient`, which
    `build_tiered_clients()` already applies underneath and *does* persist to
    `data/llm_usage.jsonl`). Exists so `--llm-tiers`'s live gate has an
    independent, in-process tally to cross-check the usage-log line count
    against — the ledger and this counter must agree, or the routing plumbing
    (or the ledger itself) is broken."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.inner.complete(system, user)


def _capability_mode_enabled(
    profession: Profession,
    banker_spot: object,
    requested: bool,
) -> bool:
    return bool(
        requested
        and banker_spot
        and any(bound == profession.key for bound, _capability in CAPABILITIES)
    )


def _pickaxes_for(ticks: int) -> list[str]:
    """Pickaxes for a mining day of `ticks`: ServUO's Pickaxe carries 50 uses and a
    swing costs ~2 fast-loop ticks (cursor tick + swing tick), so one pick is ~100
    miner ticks. forge4 (2026-07-30): a fixed pair of picks wore out mid-day and the
    miner froze SILENTLY for 320+ ticks — relocation can never fire without swings
    (its window fills from swing replies), so a toolless miner starves its whole
    chain. Two is the floor (one in use, one spare) for short smokes — and EIGHT the
    ceiling: a pickaxe weighs 11 stones, and pack WEIGHT was half of the original
    harvest-freeze root cause (docs/HISTORY.md) — tool-life must not cost ore
    capacity."""
    return ["Pickaxe"] * max(2, min(8, -(-ticks // 100)))


def _staging_items(plan_entry: dict, capability_goals: bool,
                   ticks: int | None = None) -> list[str]:
    """Provision every prerequisite the selected closed operation cannot create."""

    items = list(plan_entry["prof"].items)
    if ticks is not None and plan_entry["prof"].key == "miner":
        # The profession default (two picks) only survives a smoke-length day.
        items = [i for i in items if i != "Pickaxe"] + _pickaxes_for(ticks)
    if _capability_mode_enabled(
        plan_entry["prof"], plan_entry["banker_spot"], capability_goals
    ):
        # Closed hands cannot craft their own first sale inventory. Five
        # explicit daggers make both selector choices real, while Gold 100
        # guarantees the subsequent bank milestone independently of a shard's
        # optional fresh-character starting gold.
        items.extend(["Dagger"] * 5)
        items.append("Gold 100")
    return items


def _build_capability_runtime(
    profession: Profession,
    client: object | None,
    *,
    reflection: object | None = None,
    insights: object | None = None,
):
    """Build the exact planner/cognition/policy triple used by the village."""

    from .capability_cognition import CapabilityCognition
    from .cognition import ReflectingCognition, ThreadedCognition

    inner = CapabilityCognition(client, profession.key)
    cognition = (
        ThreadedCognition(ReflectingCognition(inner, reflection, insights=insights))
        if reflection is not None
        else ThreadedCognition(inner)
    )
    return (
        profession.planner(capability_goals=True),
        cognition,
        CapabilityPolicy(profession.key),
    )


def _build_villager_agent(
    plan_entry: dict,
    planner,
    cognition,
    capability_policy: CapabilityPolicy | None,
    curriculum_ctrl,
    curriculum_goals: bool,
) -> Agent:
    """Single construction seam shared by legacy, curriculum, and capability modes."""

    return Agent(
        body=plan_entry["body"],
        persona=plan_entry["persona"],
        planner=planner,
        cognition=cognition,
        cognition_interval=12,
        profession=plan_entry["prof"].key,
        goal_policy=capability_policy,
        goal_validator=(
            curriculum_ctrl.validate_goal
            if curriculum_goals and curriculum_ctrl is not None
            else None
        ),
        goal_progress=(
            curriculum_ctrl.goal_progress
            if curriculum_goals and curriculum_ctrl is not None
            else None
        ),
    )


def run_village(roster: list[str], *, host: str = "127.0.0.1", port: int = 2594,
                ticks: int = 60, stagger: float = 4.0, forum: bool = False,
                account_prefix: str = "anima",
                chatter: bool = False, llm_tiers: str | None = None,
                tune_deliver_threshold: bool = False, ledger_path: str | None = None,
                curriculum: bool = False, persist_insights: bool = False,
                curriculum_goals: bool = False,
                capability_goals: bool = False,
                chronicle: bool = False, chronicle_path: str | None = None,
                talkativeness_gate: bool = False) -> None:
    if capability_goals and (curriculum or curriculum_goals):
        raise ValueError("capability goals cannot be combined with curriculum modes")
    if (
        not account_prefix
        or len(account_prefix) > 24
        or not account_prefix.isascii()
        or not account_prefix.isalnum()
    ):
        raise ValueError("account_prefix must be 1-24 ASCII alphanumeric characters")
    registry_professions = {profession for profession, _capability in CAPABILITIES}
    if capability_goals and not any(key in registry_professions for key in roster):
        raise ValueError("roster has no profession with an installed capability")
    # The current market capabilities need the calibrated vendor/banker staged
    # only for the first miner+blacksmith trade pair. Fail before opening
    # sockets rather than silently turning a solo smith into a permanent waiter.
    if capability_goals and not {"miner", "blacksmith"}.issubset(roster):
        raise ValueError("market capabilities require a miner+blacksmith trade pair")
    # 1) Bring every agent online (staggered logins dodge the ServUO throttle).
    print(f"releasing {len(roster)} villagers: {roster}")
    online: list[tuple[_LiveIpcBody, Profession, Persona]] = []
    try:
        for i, key in enumerate(roster):
            prof = PROFESSIONS[key]
            persona = _persona_for(prof, i)
            account = f"{account_prefix}{i}"
            try:
                body = ResilientIpcBody.spawn(
                    host, port, account, account, pump_ms=300,
                )
            except Exception as e:  # noqa: BLE001
                print(f"  {account} ({key}): login failed ({e})")
                continue
            online.append((body, prof, persona))
            print(f"  {account}: {persona.name} the {key}")
            time.sleep(stagger)
        if not online:
            if capability_goals:
                raise RuntimeError("no capability villagers came online")
            print("no villagers came online")
            return
        if capability_goals:
            online_professions = {profession.key for _body, profession, _persona in online}
            if not {"miner", "blacksmith"}.issubset(online_professions):
                raise RuntimeError(
                    "capability runtime lost its miner+blacksmith pair during login"
                )

        _run_online_village(
            online,
            host=host,
            port=port,
            ticks=ticks,
            forum=forum,
            chatter=chatter,
            llm_tiers=llm_tiers,
            tune_deliver_threshold=tune_deliver_threshold,
            ledger_path=ledger_path,
            curriculum=curriculum,
            curriculum_goals=curriculum_goals,
            capability_goals=capability_goals,
            persist_insights=persist_insights,
            chronicle=chronicle,
            chronicle_path=chronicle_path,
            talkativeness_gate=talkativeness_gate,
        )
    finally:
        _close_online(online)


def _close_online(online: list[tuple[_LiveIpcBody, Profession, Persona]]) -> None:
    """Close all villager bridges without letting one cleanup failure block another."""
    for body, _prof, persona in reversed(online):
        try:
            body.close()
        except Exception as exc:  # noqa: BLE001 — cleanup must continue for the village
            print(f"  {persona.name}: close failed ({exc})")


def _run_online_village(
    online: list[tuple[_LiveIpcBody, Profession, Persona]],
    *,
    host: str,
    port: int,
    ticks: int,
    forum: bool,
    chatter: bool,
    llm_tiers: str | None,
    tune_deliver_threshold: bool,
    ledger_path: str | None,
    curriculum: bool,
    curriculum_goals: bool = False,
    capability_goals: bool = False,
    persist_insights: bool,
    chronicle: bool,
    chronicle_path: str | None,
    talkativeness_gate: bool,
) -> None:

    # 2) Assign each worker a distinct workplace. Miners get an ore bank; each
    #    lumberjack gets a grove (a stand spot + the exact tree statics in reach,
    #    found from the static map — trees can't be probed blindly, and a cluster
    #    lets a worker move tree-to-tree as each one depletes).
    #
    #    Phase 3: a roster with *both* a miner and a blacksmith gets the first of
    #    each co-located at the calibrated trade spot instead of drawn from the
    #    separate pools below, and the miner's `smithy_drop` is set so its ore
    #    haul actually goes somewhere — the first inter-agent economy loop
    #    (DESIGN.md §10). The same pairing also gets its own vendor + banker
    #    (item 2 — `skills/market.py::BlacksmithMarket`, opt-in the same way)
    #    staged near the smithy via `profession.py`'s `VENDOR_SPOT`/
    #    `BANKER_SPOT` routes. Any further miners/blacksmiths beyond that first
    #    pair fall back to their normal pools (and get no vendor/banker — the
    #    routes are calibrated to this one smithy spot's own narrow geometry,
    #    not the general `BLACKSMITH_SPOTS`), and a roster with only one of the
    #    two professions is untouched — same staging as before this feature.
    has_trade_pair = (any(p.key == "miner" for _, p, _ in online)
                      and any(p.key == "blacksmith" for _, p, _ in online))
    # PHASE6.md item 2: `village.py` already knows the trade pairing
    # structurally (the same `has_trade_pair` fact staging already computes)
    # — so the chronicle's `delivered_ingots`/`picked_up_ingots` detectors
    # can be handed each side's counterpart persona name statically, at
    # wiring time, rather than teaching a skill to learn it. `None` when
    # there's no pairing (a solo miner/blacksmith never gets a counterpart).
    trade_miner_persona = (next((persona for _, p, persona in online if p.key == "miner"), None)
                           if has_trade_pair else None)
    trade_smith_persona = (next((persona for _, p, persona in online if p.key == "blacksmith"), None)
                           if has_trade_pair else None)
    # TRADE_MINE_SPOT *is* MINING_SPOTS[1] — once a trade pairing claims it
    # directly (below), it must not also be handed out from this pool, or a
    # later miner ends up staged on top of the trade miner.
    spots = iter(s for s in MINING_SPOTS if not has_trade_pair or s != TRADE_MINE_SPOT)
    fish_spots = iter(FISHING_SPOTS)
    smith_spots = iter(BLACKSMITH_SPOTS)
    groves = iter(find_tree_clusters(
        _survey_map(*(body for body, _, _ in online)), *FOREST_BASE))
    trade_miner_placed = trade_smith_placed = not has_trade_pair
    plan: list[dict] = []
    for body, prof, persona in online:
        workplace, nodes, smithy_drop, vendor_spot, banker_spot, counterpart = (
            None, None, None, None, None, None,
        )
        if prof.key == "miner" and not trade_miner_placed:
            workplace = TRADE_MINE_SPOT
            smithy_drop = TRADE_SMITH_SPOT
            counterpart = trade_smith_persona.name if trade_smith_persona is not None else None
            trade_miner_placed = True
        elif prof.key == "lumberjack":
            grove = next(groves, None)
            if grove is not None:
                workplace, trees = grove
                nodes = [(t.x, t.y, t.z, t.graphic) for t in trees]
        elif prof.key == "fisher":
            spot = next(fish_spots, None)
            if spot is not None:
                (sx, sy), (wx, wy, wz) = spot
                workplace = (sx, sy)
                nodes = [(wx, wy, wz, 0)]  # cast at the exact water tile (land target)
        elif prof.key == "blacksmith" and not trade_smith_placed:
            workplace = TRADE_SMITH_SPOT
            vendor_spot = VENDOR_SPOT
            banker_spot = BANKER_SPOT
            counterpart = trade_miner_persona.name if trade_miner_persona is not None else None
            trade_smith_placed = True
        elif prof.key == "blacksmith":
            workplace = next(smith_spots, None)
        elif prof.needs_workplace:
            workplace = prof.workplace or next(spots)
        plan.append({"body": body, "prof": prof, "persona": persona,
                     "workplace": workplace, "nodes": nodes, "smithy_drop": smithy_drop,
                     "vendor_spot": vendor_spot, "banker_spot": banker_spot, "counterpart": counterpart})

    # 3) Control plane: stage workers and name everyone.
    #    `find_mobile_near`'s own exclude set needs every agent serial the
    #    village knows, not just the one currently being staged — a widened
    #    search radius (see that method's docstring) can otherwise resolve to
    #    a *different* known agent standing nearby (e.g. the trade miner
    #    sitting within reach of the trade smithy's own vendor/banker spots)
    #    instead of the NPC actually being searched for.
    all_agent_serials = {p["body"].ready["player"]["serial"] for p in plan}
    with GmControl.spawn(host, port) as gm:
        gm.hide()
        for p in plan:
            serial = p["body"].ready["player"]["serial"]
            if p["workplace"] is not None:
                gx, gy, gz = gm.stage(
                    serial,
                    *p["workplace"],
                    skills=p["prof"].skills,
                    items=_staging_items(p, capability_goals, ticks),
                )
                for stype, dx, dy in p["prof"].structures:
                    gm.command_at(f"[Add {stype}", gx + dx, gy + dy, gz)
                if p["vendor_spot"]:
                    # `stage_npc` adds, finds, corrects the position back onto
                    # the exact requested spot if `[Add` settled it a tile off
                    # (live-caught pinning it dead onto the trade corridor's
                    # own hub waypoint instead, permanently blocking every
                    # walk through it — see that method's docstring), and
                    # pins it (`VendorAI.DoActionWander` roams a BaseVendor
                    # when idle, which can drift it out of the market skill's
                    # search radius / the smith's fixed route).
                    vx, vy = p["vendor_spot"][-1]
                    npc = gm.stage_npc("Blacksmith", vx, vy, gz,
                                       exclude=all_agent_serials)
                    if npc is not None:
                        # Pin by the REQUESTED tile, because that is what this
                        # runner stores as the route (unlike `stage_shops`, which
                        # stores its readback) — the pin key must always be the
                        # waypoint the market resolver will be handed.
                        p.setdefault("shop_serials", {})[(vx, vy)] = npc.serial
                if p["banker_spot"]:
                    bx, by = p["banker_spot"][-1]
                    npc = gm.stage_npc("Banker", bx, by, gz,
                                       exclude=all_agent_serials)
                    if npc is not None:
                        p.setdefault("shop_serials", {})[(bx, by)] = npc.serial
            gm.command_on(f'[Set Name "{p["persona"].name}"', serial)
    print("staged & named. work begins.\n")

    # 4) Run every villager concurrently; print a live snapshot of the village.
    #    With --chatter, each gets an LLM cognition (threaded, off the hot path) so
    #    they speak in character while they work. --llm-tiers supersedes --chatter:
    #    it builds a role-tiered client set (Phase 4 item 2 — llm.py::ROLE_TIER/
    #    build_tiered_clients) and, since proving the tiering actually routes by
    #    role needs a "standard"-tier caller too, also wires reflection (off until
    #    now — this flag is the first thing in village.py to turn it on).
    chat_client = None
    tiered_clients = None
    call_counters: dict[str, _CountingClient] = {}
    if llm_tiers:
        from .llm import ROLE_TIER, build_tiered_clients

        tiered_clients = build_tiered_clients(provider=llm_tiers)
        call_counters = {tier: _CountingClient(client) for tier, client in tiered_clients.items()}
        print(f"llm-tiers ({llm_tiers}):",
              "degraded — one client answers every tier" if tiered_clients.degraded
              else "tiered — 3 distinct models")
    elif chatter:
        from .llm import ReplicateClient

        chat_client = ReplicateClient.from_v1_config()
        print("chatter:", "LLM cognition on" if chat_client else "no LLM configured")

    # Phase 4 item 4 — deliver_threshold bandit tuning: one shared ParamTuner
    # for the whole roster (miners pull from the same candidate grid), seeded
    # from whatever `data/skill_ledger.jsonl` already has on disk (item 3's
    # own "read at construction time" convention — a process restart doesn't
    # throw away prior sessions' pulls). `skill_lib` is only constructed when
    # the flag is on — zero effect otherwise, matching every other opt-in
    # collaborator in this file.
    skill_lib: SkillLibrary | None = None
    tuner: ParamTuner | None = None
    if tune_deliver_threshold:
        skill_lib = SkillLibrary(ledger_path=ledger_path)
        deliver_spec = ParamSpec("deliver_threshold", DELIVER_THRESHOLD_CANDIDATES)
        tuner = ParamTuner.load_from_ledger(
            skill_lib.ledger_path, "mine_smelt_deliver", "deliver_threshold", deliver_spec,
        )
        print(f"deliver_threshold tuning: ON — ledger at {skill_lib.ledger_path.resolve()} "
              f"(seeded pulls: {tuner.pulls()})")

    # PHASE6.md item 2 — opt-in, unset by default: zero effect on any
    # currently-passing roster unless `--chronicle` is passed. ONE
    # `ChronicleLedger` shared by the whole roster: every agent's worker
    # thread below calls `queue_event()` on it (in-memory only, no I/O); this
    # function's own MAIN thread flushes it exactly once, after every worker
    # has already joined (see the `for t in threads: t.join()` block below) —
    # the same "compute in worker threads, persist once from the joined main
    # thread" shape the `deliver_threshold` tuner-outcome recording above
    # already uses, and the real precedent this item's own `queue_event()`/
    # `flush()` split follows (see `chronicle.py`'s module docstring).
    chronicle_ledger: ChronicleLedger | None = None
    # PHASE6.md item 3: one private, session-scoped event list per agent
    # (keyed by persona name — unique within a roster), pre-populated before
    # any worker thread starts so each thread only ever appends to an
    # already-existing list it owns (never inserts a new key concurrently).
    # Stays `{}` when `--chronicle` is off — every `.get()` below then
    # returns `None`, reproducing today's forum behavior exactly.
    session_chronicle: dict[str, list[ChronicleEvent]] = {}
    if chronicle:
        chronicle_ledger = ChronicleLedger(ledger_path=chronicle_path)
        session_chronicle = {p["persona"].name: [] for p in plan}
        print(f"chronicle: ON — ledger at {chronicle_ledger.ledger_path.resolve()}")

    status: dict[int, str] = {}
    lock = threading.Lock()
    threads = []
    agents: list[tuple[Agent, str, float | None]] = []
    # PHASE6.md item 3: each persona's most recently persisted insight text,
    # captured right after `load_insights()` — i.e. what was true BEFORE this
    # session's own ticks/reflections, the "yesterday" a continuing forum
    # post refers to. Stays `{}` when `--persist-insights` (or `--llm-tiers`)
    # is off, matching `session_chronicle`'s own no-op-by-default shape.
    yesterday_texts: dict[str, str] = {}
    for i, p in enumerate(plan):
        capability_enabled = _capability_mode_enabled(
            p["prof"], p["banker_spot"], capability_goals
        )
        capability_policy = None
        planner = None
        cognition = None
        if tiered_clients is not None:
            from .cognition import LLMCognition, LLMReflection, ReflectingCognition, ThreadedCognition
            from .memory import load_insights

            reflection = LLMReflection(call_counters[ROLE_TIER["reflection"]])
            # PHASE6.md item 1: resume this persona's distilled insights from a
            # prior session, if any — `load_insights` returns an empty (but
            # already disk-wired) ReflectionMemory when there's nothing to
            # resume, so this is safe on a persona's very first run too.
            # `persist_insights=False` (the default) leaves `insights=None`,
            # letting ReflectingCognition build its own in-memory-only default —
            # zero effect on any currently-passing `--llm-tiers` roster.
            insights = load_insights(INSIGHTS_PATH, p["persona"].name) if persist_insights else None
            # PHASE6.md item 3: snapshot "yesterday" right here, at load
            # time — before this session's own reflections (if any) can
            # append a newer insight to the same ReflectionMemory — so the
            # forum post's "yesterday you noted" always refers to what was
            # actually persisted BEFORE this session started, never
            # something this same session just reflected on.
            if insights is not None:
                prior = insights.recent(1)
                if prior:
                    yesterday_texts[p["persona"].name] = prior[-1].text
            if capability_enabled:
                planner, cognition, capability_policy = _build_capability_runtime(
                    p["prof"],
                    (
                        None
                        if llm_tiers == "stub"
                        else call_counters[ROLE_TIER["capability_pick"]]
                    ),
                    reflection=reflection,
                    insights=insights,
                )
            else:
                inner = LLMCognition(
                    call_counters[ROLE_TIER["chatter"]],
                    job=p["prof"].key,
                    talkativeness_gate=talkativeness_gate,
                )
                cognition = ThreadedCognition(
                    ReflectingCognition(inner, reflection, insights=insights)
                )
        elif capability_enabled:
            planner, cognition, capability_policy = _build_capability_runtime(
                p["prof"], chat_client
            )
        elif chat_client is not None:
            from .cognition import LLMCognition, ThreadedCognition

            cognition = ThreadedCognition(LLMCognition(chat_client, job=p["prof"].key,
                                                       talkativeness_gate=talkativeness_gate))
        if planner is None:
            planner = p["prof"].planner(curriculum_goals=curriculum_goals)

        # PHASE4.md item 4: pick a deliver_threshold once per miner, at
        # construction time (session granularity — held fixed for the whole
        # session, never re-tuned mid-run). `Profession.planner()` doesn't
        # hand back the constructed work-skill instance directly, so it's
        # located after the fact — the exact seam PHASE4.md item 4's own
        # Scope names.
        chosen_threshold: float | None = None
        if tuner is not None and p["prof"].key == "miner":
            miner_skill = next(
                (
                    s if isinstance(s, MineSmeltDeliver) else getattr(s, "inner", None)
                    for s in planner.skills
                    if isinstance(s, MineSmeltDeliver)
                    or isinstance(getattr(s, "inner", None), MineSmeltDeliver)
                ),
                None,
            )
            if miner_skill is not None:
                chosen_threshold = tuner.choose()
                miner_skill.deliver_threshold = chosen_threshold

        # PHASE4.md item 5: opt-in automatic curriculum. Wrap whatever
        # cognition exists (or a no-LLM `HeuristicCognition` when none) in a
        # `CurriculumController` — cadence-gated, its own daemon thread, never
        # on the fast loop. `curriculum_milestone` is observational only this
        # landing (nothing drives behaviour from it yet). The controller
        # records an achieved-transition `Episode` into its `.episodes`, which
        # must be the SAME instance as `agent.episodes` — `Agent.__init__`
        # builds its own, so we rebind right after construction, before the
        # first tick (nothing reads/writes it before then). Client for the
        # 2+-eligible LLM pick: the tiered `curriculum_pick` role if wired,
        # else the chatter client, else a stub (0-1 eligible needs no LLM, and
        # a bad reply falls back deterministically — so a stub is harmless).
        curriculum_ctrl = None
        if curriculum or curriculum_goals:
            from .cognition import HeuristicCognition
            from .curriculum import CurriculumController
            if tiered_clients is not None:
                from .llm import ROLE_TIER
                pick_client = call_counters[ROLE_TIER["curriculum_pick"]]
            elif chat_client is not None:
                pick_client = chat_client
            else:
                from .llm import StubLLMClient
                pick_client = StubLLMClient('{"milestone": ""}')
            curriculum_ctrl = CurriculumController(
                cognition if cognition is not None else HeuristicCognition(),
                pick_client, p["persona"].name, p["prof"].key,
                drive_goals=curriculum_goals,
            )  # default milestones_path = data/milestones.jsonl
            cognition = curriculum_ctrl

        agent = _build_villager_agent(
            p,
            planner,
            cognition,
            capability_policy,
            curriculum_ctrl,
            curriculum_goals,
        )
        if curriculum_ctrl is not None:
            curriculum_ctrl.episodes = agent.episodes  # rebind: milestone Episodes land in the agent's own memory
        if p["nodes"]:
            agent.memory["harvest_nodes"] = p["nodes"]  # the grove to work, tree by tree
        if p["smithy_drop"]:
            agent.memory["smithy_drop"] = p["smithy_drop"]  # miner's delivery target (trade pairing)
        if p["vendor_spot"]:
            agent.memory["vendor_spot"] = p["vendor_spot"]  # blacksmith's sell route (trade pairing)
        if p["banker_spot"]:
            agent.memory["banker_spot"] = p["banker_spot"]  # blacksmith's bank route
        if p.get("shop_serials"):
            # Identity pin for the market resolver: with a vendor and a banker a
            # tile either side of a stand, nearest-to-spot is a coin flip.
            agent.memory["shop_serials"] = p["shop_serials"]
        if capability_enabled and p["workplace"] is not None:
            agent.memory["craft_spot"] = p["workplace"]
        if chosen_threshold is not None:
            print(f"  {p['persona'].name}: deliver_threshold={chosen_threshold} (tuner-chosen)")
        agents.append((agent, p["prof"].key, chosen_threshold))
        t = threading.Thread(
            target=_run_worker,
            args=(agent, ticks, i, status, lock, p["prof"].key),
            kwargs={"chronicle": chronicle_ledger, "counterpart": p["counterpart"],
                    "session_events": session_chronicle.get(p["persona"].name)},
            daemon=True,
        )
        threads.append(t)
        t.start()

    while any(t.is_alive() for t in threads):
        time.sleep(2.5)
        with lock:
            snap = [status[i] for i in sorted(status)]
        print("— village —\n  " + "\n  ".join(snap))
    for t in threads:
        t.join()

    # PHASE6.md item 2: flush every queued chronicle event now — the ONE
    # place this run touches data/chronicle.jsonl, strictly after every
    # worker thread (the only queue_event() callers) has already exited. A
    # mid-run crash loses only that session's queued-but-unflushed events —
    # the accepted tradeoff `chronicle.py`'s own module docstring documents,
    # the same one the deliver_threshold tuner's own end-of-session-only
    # ledger write already carries.
    if chronicle_ledger is not None:
        n = chronicle_ledger.flush()
        print(f"chronicle: flushed {n} event(s) to {chronicle_ledger.ledger_path.resolve()}")

    print("\nday's work done.")

    # PHASE4.md item 4: at session end, record (value, reward) for every
    # miner the tuner picked a value for — through the exact same
    # `SkillLibrary.record_outcome` ledger item 3 already established, tagged
    # via `param`/`param_value` so `ParamTuner.load_from_ledger` can pick
    # these lines back out from item 3's own per-tick (param=None) records.
    #
    # The recorded reward is the miner's raw `episodes.total_reward()` over
    # this run's fixed `--ticks` window — NOT `session_mean_reward` (a mean
    # per recorded episode). Every miner here already runs the same fixed
    # tick count (`_run_worker` has no early-stop), but a mean-per-episode
    # still isn't a fair cross-candidate objective: a higher deliver_threshold
    # triggers fewer, larger delivery events, so it accrues episodes at a
    # different rate than a lower one, which skews a per-episode mean even
    # when the session length itself is held fixed — the same live-caught
    # class of bug `live_trade.py::_run_session`'s own docstring documents in
    # detail (that live gate is where it was actually caught).
    #
    # A miner whose session recorded ZERO episodes is a live wedge (no
    # confirmed mining/delivery progress at all), not a genuine "this value
    # is bad" signal — skip recording rather than poison that arm with a
    # false 0.0 (mirrors `live_trade.py::_run_tuner`'s own guard).
    if tuner is not None and skill_lib is not None:
        print(f"\n— deliver_threshold tuning ({skill_lib.ledger_path}) —")
        for agent, job, chosen in agents:
            if chosen is None:
                continue
            if agent.episodes.total_recorded == 0:
                print(f"  {agent.persona.name} ({job}): deliver_threshold={chosen} — "
                      f"0 episodes recorded (live wedge) — SKIPPED, no ledger record")
                continue
            reward = agent.episodes.total_reward()
            tuner.update(chosen, reward)
            skill_lib.record_outcome("mine_smelt_deliver", "miner", reward, Status.SUCCESS,
                                     param="deliver_threshold", param_value=chosen)
            print(f"  {agent.persona.name} ({job}): deliver_threshold={chosen} "
                  f"reward(fixed-window total)={reward:.3f}")
        print(f"  cumulative pulls (this process, seeded + this session): {tuner.pulls()}")

    if call_counters:
        print(f"\n— llm tiers — (degraded={tiered_clients.degraded}) —")
        for tier, counter in call_counters.items():
            print(f"  {tier}: {counter.calls} calls")

    # 5) End of day: each villager writes about it on the tavern forum.
    if forum:
        from .forum import ForumClient, post_day
        from .llm import ReplicateClient

        client = ForumClient()
        if not client.configured:
            print("forum: no API key (set ANIMA_FORUM_API_KEY or anima/config.yaml).")
        else:
            llm = ReplicateClient.from_v1_config()  # in-character prose if available
            print(f"\n— the tavern board —{' (LLM-written)' if llm else ' (heuristic)'}")
            for agent, job, _chosen_threshold in agents:
                # PHASE6.md item 3: `None` for both unless `--persist-insights`/
                # `--chronicle` were actually passed — exactly reproducing
                # today's forum behavior when neither is set.
                res = post_day(
                    agent, job=job, client=client, llm=llm,
                    yesterday=yesterday_texts.get(agent.persona.name),
                    chronicle_events=session_chronicle.get(agent.persona.name),
                )
                print(f"  {agent.persona.name} posted about the day: {'ok' if res else 'failed'}")


def run_supply_pair(*, host: str = "127.0.0.1", port: int = 2594,
                    ticks: int = 1200, account_prefix: str = "animapair",
                    monitor: bool = False, narrate: bool = False,
                    carpenter_tick_every: int = 3,
                    woodsman_knobs: dict[str, Any] | None = None,
                    carpenter_knobs: dict[str, Any] | None = None) -> None:
    """Bjorn supplies Sten: a lumberjack hauls boards to a carpenter's drop point.

    The two capabilities were built for each other and had never met — `deliver_boards`
    on one side, `fetch_boards` on the other — so this runs them as an actual pair,
    coordinating only through the ground between them.

    Be clear about what this is worth, because the numbers are not flattering. At vendor
    prices a Board sells for 2g, and EVERY carpentry recipe on this shard sells for less
    than the boards it eats — a Throne is 19 boards (38g raw) for 24g, and the best of
    them, a Club, is 9 boards (18g) for 13g. Measured against gold, the village would be
    richer if Bjorn simply sold his boards and Sten stayed home.

    What the pair demonstrates is the MECHANISM — two independent agents, separate
    memories, no messages between them, one leaving material on the ground and the other
    finding it — which is the thing a village is made of and the thing that pays off the
    moment crafted goods are worth more than vendor scrap. The gold is reported honestly
    either way.

    TWO knob dicts, one per Life, and deliberately not one shared `knobs=`. Both sides
    are Lives with a `bank_reserve`, so a single dict would have to mean "both" — and
    "both" is a value a searcher can never ask for separately afterwards. Naming them
    costs one argument and removes the only ambiguity a multi-Life runner has.
    """
    from .carpenter_life import SAW_COST, CarpenterLife
    from .life_runner import build_tuned_life, validate_knobs
    from .live_common import GM_RELOGIN_COOLDOWN_S, fresh_suffix, login_throttle, wipe_area
    from .skills.carpentry import SellFurniture
    from .skills.hunt import GOLD_GRAPHIC
    from .skills.craft import PICKUP_RADIUS
    from .skills.woodwork import BOARD_GRAPHIC, LOG_GRAPHIC
    from .woodsman_life import WoodsmanLife

    # Before the first packet — see `run_forge_pair`'s copy for why the placement, not
    # the check, is the part that has to be chosen at each inline site.
    woodsman_knobs = validate_knobs(woodsman_knobs, WoodsmanLife.KNOBS,
                                    label="supply pair woodsman")
    carpenter_knobs = validate_knobs(carpenter_knobs, CarpenterLife.KNOBS,
                                     label="supply pair carpenter")

    FURNITURE = SellFurniture.sold_graphic

    print(f"raising a supply pair at {host}:{port}")
    bodies = {}
    seats = _monitor_ports(monitor, ["woodsman", "carpenter"])
    for role, acct in (("woodsman", f"{account_prefix}w{fresh_suffix()}"),
                       ("carpenter", f"{account_prefix}c{fresh_suffix()}")):
        try:
            bodies[role] = ResilientIpcBody.spawn(host, port, acct, acct, pump_ms=400,
                                                  monitor_port=seats[role])
            watch = f"  watch: http://127.0.0.1:{seats[role]}/" if seats[role] else ""
            print(f"  {acct}: the {role}{watch}")
        except Exception as e:  # noqa: BLE001
            print(f"  {acct} ({role}): login failed ({e})")
        time.sleep(3.0)
    if len(bodies) < 2:
        print("the pair needs both; aborting")
        for b in bodies.values():
            if hasattr(b, "close"):
                b.close()
        return

    facet = _survey_map(bodies.get("woodsman"), bodies.get("carpenter"))
    groves = find_tree_clusters(facet, *YEW_FOREST)
    if not groves:
        print(f"no grove found near {YEW_FOREST} on map {facet}; aborting")
        for b in bodies.values():
            if hasattr(b, "close"):
                b.close()
        return
    (gx, gy), trees = groves[0]
    print(f"grove: map={facet} stand ({gx},{gy}) with {len(trees)} trees in reach")

    login_throttle(GM_RELOGIN_COOLDOWN_S)
    gm = GmControl.spawn(host, port).__enter__()
    w_routes: dict = {}
    c_routes: dict = {}
    drop = None
    try:
        gm.hide()
        serials = {r: b.ready["player"]["serial"] for r, b in bodies.items()}
        allser = set(serials.values())
        wx, wy, wz = gm.stage(serials["woodsman"], gx, gy,
                              skills=PROFESSIONS["lumberjack"].skills,
                              items=list(PROFESSIONS["lumberjack"].items))
        wipe_area(gm, wx, wy, radius=10, z=wz)
        # The carpenter stands a short walk south; the drop sits between them, inside
        # the deliver walk's reach from one side and the pickup's from the other.
        drop_wanted = (wx, wy + 2)
        cx, cy, cz = gm.stage(serials["carpenter"], wx, wy + 3,
                              skills=PROFESSIONS["carpenter"].skills,
                              items=list(PROFESSIONS["carpenter"].items))
        from .life_runner import enforce_gold_provenance
        for role in ("woodsman", "carpenter"):
            gm.command_on(f'[Set Name "{"Bjorn" if role == "woodsman" else "Sten"}"',
                          serials[role])
            enforce_gold_provenance(gm, bodies[role], serials[role])
        # Sten gets a saw's worth of seed and nothing else: his boards must come from
        # Bjorn, which is the whole point. Bjorn gets nothing — he starts with an axe.
        gm.command_on(f"[AddToPack Gold {SAW_COST}", serials["carpenter"])
        # The shared verified-staging path (life_runner.stage_shops): one `placed` dict
        # spans both calls so a shop placed for one agent can never answer for the
        # other's — the Banker-as-Weaponsmith aliasing, cross-agent edition.
        from .life_runner import stage_shops
        placed: dict[int, str] = {}
        # One pin map spans both calls too: it is keyed by TILE, so each agent only
        # ever looks up the waypoints its own routes carry (see `stage_shops`).
        shop_serials: dict = {}
        w_routes, t1 = stage_shops(
            gm, z=cz, anchor=(wx, wy), exclude=allser, strict=False, placed=placed,
            spots={"vendor_spot": ("Carpenter", (wx - 1, wy))},
            serials_out=shop_serials)
        c_routes, t2 = stage_shops(
            gm, z=cz, anchor=(cx, cy), exclude=allser, strict=False, placed=placed,
            spots={"vendor_spot": ("Carpenter", (cx + 1, cy)),
                   "banker_spot": ("Banker", (cx - 1, cy))},
            serials_out=shop_serials)
        npc_tiles = t1 | t2
        # The HANDOVER tile has to be clear too. An `[Add`-ed NPC settles a tile or two
        # off the request, and one settled exactly onto this drop on the first attempt —
        # a shops-vs-shops collision check never looks at it, which is the same
        # half-verification that let two shops share one NPC earlier. Boards cannot be
        # left on a tile somebody is standing on, and the failure would read as "the
        # carpenter never got supplied".
        drop = drop_wanted
        if drop in npc_tiles:
            drop = next(((wx, y) for y in range(wy + 1, cy)
                         if (wx, y) not in npc_tiles), drop_wanted)
            print(f"  drop {drop_wanted} was occupied by an NPC — moved to {drop}")
    finally:
        try:
            gm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass

    bjorn = build_tuned_life(WoodsmanLife, woodsman_knobs,
                             body=bodies["woodsman"], persona=Persona(name="Bjorn"),
                             routes={**w_routes, "carpenter_drop": drop})
    bjorn.memory["harvest_nodes"] = [(t.x, t.y, t.z, t.graphic) for t in trees]
    for m in (bjorn.memory, bjorn.econ_agent.memory):
        m["shop_serials"] = shop_serials  # identity pin, not a nearest-tile guess
    bjorn.set_leash((wx, wy))
    sten = build_tuned_life(CarpenterLife, carpenter_knobs,
                            body=bodies["carpenter"], persona=Persona(name="Sten"),
                            routes=c_routes)
    for m in (sten.memory, sten.econ_agent.memory):
        m["craft_spot"] = (cx, cy)
        m["shop_serials"] = shop_serials
    # Leash Sten to the DROP, not to his own feet. A carpenter that drifts out of pickup
    # range stops being supplied — live-caught: he wandered nine tiles off and was never
    # once admitted a `fetch_boards` goal in 1200 ticks, while boards sat on the ground
    # in plain view. DERIVED, like the woodsman's: far enough to reach every shop he
    # owns, and strictly inside `PICKUP_RADIUS` so a delivery is always fetchable.
    shop_reach = max((max(abs(v[0][0] - drop[0]), abs(v[0][1] - drop[1]))
                      for v in c_routes.values()), default=1)
    sten_leash = min(max(1, shop_reach), PICKUP_RADIUS - 1)
    sten.set_leash(drop, sten_leash)
    # Report what is IN FORCE, not what was asked for. `sten_leash` is the DERIVED
    # default and a `wander_leash` knob outranks it (see `WarriorLife.set_leash`), so
    # printing the local would start lying the moment anyone tuned this runner — the
    # same defect `run_carpenter_life`'s banner had before it read the built Life, and
    # the reason `run_forge_pair`'s survived becoming tunable unchanged.
    from .skills.movement import leash_readout
    print(f"  Sten leashed to the drop at "
          f"{leash_readout(sten.econ_agent.memory, sten)} tiles "
          f"(shops reach {shop_reach}, pickup radius {PICKUP_RADIUS})")
    # Both reserves read off the BUILT Lives through `market._bank_reserve` — the clamp
    # the decide rule, the `bank_gold` gate and `BankGold`'s FSM all share. This runner
    # printed no reserve at all before it could be tuned, which was defensible then and
    # is not now: a tuned value an operator cannot see is one they will read the run as
    # if it had never carried. Same shape as `LifeRunner.staged_line`'s, for the same
    # reason it reads the Life and not the module constant.
    from .skills.market import _bank_reserve
    print(f"staged: Bjorn@({wx},{wy}) -> drop {drop} -> Sten@({cx},{cy})  "
          f"(reserves: Bjorn {_bank_reserve(bjorn.econ_agent.memory)}, "
          f"Sten {_bank_reserve(sten.econ_agent.memory)})\n")

    agents = [("lumberjack", bjorn),
              ("carpenter", _ThrottledAgent(sten, carpenter_tick_every))]
    status: dict[int, str] = {}
    lock = threading.Lock()
    threads = []
    for i, (role, agent) in enumerate(agents):
        budget = ticks * getattr(agent, "tick_budget_scale", 1)
        t = threading.Thread(target=_run_worker,
                             args=(agent, budget, i, status, lock, role),
                             kwargs={"narrate": narrate}, daemon=True)
        threads.append(t)
        t.start()
        time.sleep(0.7)

    # `_pack` was written out here; it is `obsview.pack_amount` now, and it carried BOTH
    # recorded drift defects at once — `... if bp else 0` (a backpack whose serial is
    # literally 0 reads as ABSENT) and `i.graphic == graphic` (hand a set to that form and
    # it answers a silent 0, not an error). See `obsview.pack_amount`'s docstring.
    while any(t.is_alive() for t in threads):
        time.sleep(4.0)
        w_obs = getattr(bjorn.body, "last_obs", None)
        c_obs = getattr(sten.body, "last_obs", None)
        # Boards ON THE GROUND, read from BOTH sides — the handover is only real if the
        # receiver can see it, and a purse one agent sees and the other cannot is the
        # exact shape that cost this project three runs on the artisan+mage pipeline.
        def _ground(o):
            return sum(i.amount for i in (o.items if o else [])
                       if i.graphic == BOARD_GRAPHIC and i.container is None)
        # want / admitted / ready, for BOTH sides. `want` alone is intent, and an
        # unadmitted goal looks exactly like a busy one — the pair runner shipped
        # without this and promptly needed it when Bjorn sat on 20 logs for 123 samples.
        # ONE definition of the readout: `life_runner.telemetry_line`. This used to
        # re-derive `adm=` here as a bare capability name, which is the very ambiguity
        # that line's age/`+hold`/`!frozen`/`!overdue` decorations were added to kill —
        # so the two runners that drive the carpenter and the lumberjack were the two
        # printing the old, lying form. Review-caught as divergent copies that will
        # drift again; only the `process_logs` counters stay local, because they are
        # this pair's debugging, not the shared readout.
        def _layers(life, prof_key):
            try:
                from .life_runner import telemetry_line
                o = getattr(life.body, "last_obs", None)
                extra = ""
                cur = life.econ_agent.goal_stack.current
                if cur is not None and cur.goal.params.get("capability") == "process_logs":
                    m = life.econ_agent.memory
                    extra = (f" need={m.get('cap_process_needed')}"
                             f" delta={m.get('cap_process_board_delta')}"
                             f" left={m.get('cap_process_logs_remaining')}"
                             f" fin={m.get('cap_process_finished_goal_id')}")
                return telemetry_line(life, prof_key, o) + extra
            except Exception:  # noqa: BLE001 — telemetry must never break the run
                return "want=? admitted=? ready=?"

        with lock:
            snap = [status[i] for i in sorted(status)]
        print(f"  bjorn: {_layers(bjorn, 'lumberjack')}")
        print(f"  sten : {_layers(sten, 'carpenter')}")
        print(f"— supply pair  bjorn[{bjorn.mode}/{bjorn.target_cap} "
              f"logs={pack_amount(w_obs, LOG_GRAPHIC)} "
              f"boards={pack_amount(w_obs, BOARD_GRAPHIC)} "
              f"gold={pack_amount(w_obs, GOLD_GRAPHIC)}]  "
              f"drop[bjorn_sees={_ground(w_obs)} sten_sees={_ground(c_obs)}]  "
              f"sten[{sten.mode}/{sten.target_cap} "
              f"boards={pack_amount(c_obs, BOARD_GRAPHIC)} "
              f"furniture={pack_amount(c_obs, FURNITURE)} "
              f"gold={pack_amount(c_obs, GOLD_GRAPHIC)}] —")
        for line in snap:
            print(f"  {line}")
    for t in threads:
        t.join(timeout=5)
    print("\nthe pair's day is done")


def run_forge_pair(*, host: str = "127.0.0.1", port: int = 2594,
                   ticks: int = 1200, account_prefix: str = "animaforge",
                   monitor: bool = False, narrate: bool = False,
                   knobs: dict[str, Any] | None = None) -> None:
    """The FLAGSHIP pair: Grimm mines and delivers iron, Pim turns it into tongs.

    This is the one supply chain the shard's own price tables reward (pinned by
    tests/test_price_tripwire.py): a raw ingot sells for 4g, a tongs — one ingot, one
    craft — sells for 7g, so every delivered ingot is worth 1.75x its raw value and
    the pair is POSITIVE-margin end to end, unlike the frozen lumberjack->carpenter
    chain (-33g/throne; DESIGN.md §10). The corridor is the Phase-3 calibrated trade
    ground (`TRADE_MINE_SPOT` -> `TRADE_SMITH_SPOT`), the miner is `live_trade.py`'s
    own live-verified MineSmeltDeliver agent, and the tinker is the fifth Life —
    born on the harness, with the concordance suite and the disagreement detector
    already covering it.

    Provenance: BOTH start broke; the tinker gets no iron and no gold. Every coin at
    the end came out of the mountain.

    `knobs` tunes PIM, the one Life here (Grimm is a plain `Agent` driving
    `MineSmeltDeliver` and has no knob channel — see the module note on the two halves
    of a pair). This is the runner audit follow-up 2 named as "the closest candidate,
    and the one that matters most": the flagship positive-margin loop is the one a
    gold-per-life fitness run would actually measure, so a genome that cannot reach it
    cannot steer anything worth scoring.
    """
    from .life_runner import (
        banked_amount,
        enforce_gold_provenance,
        monitor_ports,
        pack_amount,
        stage_shops,
        telemetry_line,
    )
    from .life_runner import build_tuned_life, validate_knobs
    from .live_common import GM_RELOGIN_COOLDOWN_S, fresh_suffix, login_throttle, wipe_area
    from .skills.craft import PICKUP_RADIUS
    from .skills.hunt import GOLD_GRAPHIC
    from .skills.smelt import INGOT_GRAPHICS, MineSmeltDeliver
    from .skills.tinkering import TONGS_GRAPHIC
    from .planner import Planner
    from .skills.market import _bank_reserve
    from .tinker_life import TinkerLife

    # BEFORE the first packet. A bad key raises `TypeError` from the constructor
    # otherwise, and that construction happens after two logins, the GM staging and the
    # provenance gold-wipe — a one-character typo would abandon two spawned, staged
    # characters behind a traceback. `LifeSpec` gets this placement for free by checking
    # at spec construction; an inline runner has to choose it.
    knobs = validate_knobs(knobs, TinkerLife.KNOBS, label="forge pair tinker")

    print(f"raising the forge pair at {host}:{port}")
    bodies = {}
    seats = monitor_ports(monitor, ["miner", "tinker"])
    for role, acct in (("miner", f"{account_prefix}m{fresh_suffix()}"),
                       ("tinker", f"{account_prefix}t{fresh_suffix()}")):
        try:
            bodies[role] = ResilientIpcBody.spawn(host, port, acct, acct, pump_ms=400,
                                                  monitor_port=seats[role])
            watch = f"  watch: http://127.0.0.1:{seats[role]}/" if seats[role] else ""
            print(f"  {acct}: the {role}{watch}")
        except Exception as e:  # noqa: BLE001
            print(f"  {acct} ({role}): login failed ({e})")
        time.sleep(3.0)
    if len(bodies) < 2:
        print("the pair needs both; aborting")
        for b in bodies.values():
            if hasattr(b, "close"):
                b.close()
        return

    login_throttle(GM_RELOGIN_COOLDOWN_S)
    routes: dict = {}
    with GmControl.spawn(host, port) as gm:
        gm.hide()
        serials = {r: b.ready["player"]["serial"] for r, b in bodies.items()}
        allser = set(serials.values())
        mx, my = TRADE_MINE_SPOT
        sx, sy = TRADE_SMITH_SPOT
        wipe_area(gm, (mx + sx) // 2, (my + sy) // 2,
                  radius=max(abs(mx - sx), abs(my - sy)) // 2 + 10, z=20)
        # The miner, exactly as live_trade.py stages it (its corridor and forge
        # placement are live-calibrated; east/west of the smith stand is the miner's
        # approach and must stay clear).
        mgx, mgy, mgz = gm.stage(serials["miner"], mx, my, skills={"Mining": 35},
                                 items=_pickaxes_for(ticks))
        gm.command_at("[Add Forge", mgx + 1, mgy + 1, mgz)
        # Terrain-aware relocation (forge11's lesson: the blind compass walk
        # marched 60+ tiles off the mountain face while the economy starved).
        # Survey the face around the calibrated stand: exact rock nodes for HOME
        # — targeted digs, no invalid-tile replies — plus a pool of next stands,
        # one per 8x8 HarvestBank cell, each with its OWN forge ([Add Forge):
        # smelting needs FORGE_REACH=2, and a relocated miner without a forge in
        # reach silently stops smelting (ore piles up, deliveries never trigger).
        facet = _survey_map(bodies["miner"])
        mine_spots = find_mine_spots(facet, mx, my)
        home_nodes = next((n for s, n in mine_spots if s == (mgx, mgy)),
                          mine_spots[0][1] if mine_spots else None)
        spot_pool = [(s, n) for s, n in mine_spots if s != (mgx, mgy)][:MINE_POOL_SPOTS]
        for (px, py), _nodes in spot_pool:
            gm.command_at("[Add Forge", px + 1, py + 1, mgz)
        print(f"mine survey: map={facet} home face {len(home_nodes or [])} tiles, "
              f"pool {[s for s, _ in spot_pool]}")
        gm.command_on('[Set Name "Grimm"', serials["miner"])
        # The tinker at the calibrated smith stand with its tool and NOTHING else.
        tgx, tgy, tgz = gm.stage(serials["tinker"], sx, sy,
                                 skills=PROFESSIONS["tinker"].skills,
                                 items=["TinkerTools 999"])
        gm.command_on('[Set Name "Pim"', serials["tinker"])
        for role in ("miner", "tinker"):
            enforce_gold_provenance(gm, bodies[role], serials[role])
        # One Tinker NPC serves every errand (SBTinker both sells iron at 5g and pays
        # 7g for tongs) plus a Banker — on the hand-calibrated VENDOR/BANKER spots.
        shop_serials: dict = {}
        routes, _tiles = stage_shops(
            gm, z=tgz, anchor=(tgx, tgy), exclude=allser,
            spots={"vendor_spot": ("Tinker", VENDOR_SPOT[-1]),
                   "banker_spot": ("Banker", BANKER_SPOT[-1])},
            serials_out=shop_serials)

    miner = Agent(body=bodies["miner"], persona=Persona(name="Grimm"),
                  planner=Planner([MineSmeltDeliver()]))
    miner.memory["smithy_drop"] = TRADE_SMITH_SPOT  # the deliver phase's only wiring
    if home_nodes:
        miner.memory["harvest_nodes"] = home_nodes  # dig real rock, not a blind ring
    miner.memory["harvest_spot_pool"] = spot_pool  # relocation walks to KNOWN faces

    pim = build_tuned_life(TinkerLife, knobs,
                           body=bodies["tinker"], persona=Persona(name="Pim"),
                           routes=routes)
    for m in (pim.memory, pim.econ_agent.memory):
        m["craft_spot"] = (tgx, tgy)
        # Shop-identity pin: the Tinker and the Banker stand a tile either side
        # of their requested spots, and the resolver's nearest-to-spot guess
        # broke toward the wrong one two runs out of three (urgent-band gate).
        m["shop_serials"] = shop_serials
    # Leash Pim to the DROP (his own stand): far enough for his shops, strictly inside
    # pickup reach so a delivery is always fetchable — the carpenter's derivation.
    shop_reach = max((max(abs(v[0][0] - tgx), abs(v[0][1] - tgy))
                      for v in routes.values()), default=1)
    pim.set_leash((tgx, tgy), min(max(1, shop_reach), PICKUP_RADIUS - 1))
    # Read off PIM, through the clamp every other reader of these keys uses — not off the
    # module constants. That was written when nothing could tune this runner, on the
    # argument that it "stays true the day something does"; `knobs=` is that day, and the
    # banner needed no change to survive it, which is the whole point of reading the
    # BUILT Life. `run_carpenter_life`'s banner was the same shape and its live proof
    # (2026-08-03, `reserve 400` against a module default of 129) is what this now makes
    # available on the flagship pair. Both of the tinker's knobs are here: a channel whose
    # value an operator cannot see is one they will read the run as if it had not carried.
    from .skills.movement import leash_readout
    from .tinker_life import bank_trip_surplus
    print(f"staged: Grimm@({mgx},{mgy}) -> drop {TRADE_SMITH_SPOT} -> Pim@({tgx},{tgy}) "
          f"(reserve {_bank_reserve(pim.econ_agent.memory)}, "
          f"trip surplus {bank_trip_surplus(pim.econ_agent.memory)}, "
          f"leash {leash_readout(pim.econ_agent.memory, pim)}, both broke)\n")

    status: dict[int, str] = {}
    lock = threading.Lock()
    threads = []
    for i, (role, agent) in enumerate((("miner", miner), ("tinker", pim))):
        budget = ticks * getattr(agent, "tick_budget_scale", 1)
        t = threading.Thread(target=_run_worker,
                             args=(agent, budget, i, status, lock, role),
                             kwargs={"narrate": narrate}, daemon=True)
        threads.append(t)
        t.start()
        time.sleep(0.7)

    started = time.monotonic()
    while any(t.is_alive() for t in threads):
        time.sleep(4.0)
        m_obs = getattr(miner.body, "last_obs", None)
        p_obs = getattr(pim.body, "last_obs", None)

        def _ground_iron(o):
            return sum(i.amount for i in (o.items if o else [])
                       if i.graphic in INGOT_GRAPHICS and i.container is None)
        earned = pack_amount(p_obs, GOLD_GRAPHIC) + banked_amount(p_obs)
        hours = max(1e-9, (time.monotonic() - started) / 3600.0)
        with lock:
            snap = [status[i] for i in sorted(status)]
        # When ANY capability goal holds the stack, show its FSM's own stage keys —
        # an ADMITTED goal that does not progress is invisible to both the
        # concordance suite (steady-state only) and the disagreement detector
        # (no-goal guard), so the FSM has to say where it is stuck itself. Was
        # bank_gold-only; forge7 (2026-07-30) spent a full 1500-tick day with an
        # admitted-and-ready sell_tongs goal silently not progressing (53 liveness
        # fires, zero sales) and this line was blind to the sell FSM's stage.
        bank_state = ""
        cur = pim.econ_agent.goal_stack.current
        if cur is not None:
            capname = cur.goal.params.get("capability")
            # ONE instant for the whole group — see `stage_key_readout`, which owns the
            # reason and is where the guarantee is tested.
            m = dict(pim.econ_agent.memory)
            if capname == "bank_gold":
                bank_state = (f" bank(stage={m.get('bank_stage')} leg={m.get('bank_leg')} "
                              f"banker={m.get('bank_banker')} "
                              f"attempts={m.get('bank_deposit_attempts')} "
                              f"popup_wait={m.get('bank_popup_wait')})")
            elif capname:
                # `*_stall` is the WALK's own progress counter, and its absence is why
                # follow-up 29 could not be diagnosed from a full day's log. That run
                # showed `mkt_phase=sell` on 134 samples with `sell_stage` never written
                # once — so the trip started and died before its first stage, which is the
                # walk, and no key here could say so.
                #
                # `*_leg` was tried first and is USELESS for these runners, which is worth
                # writing down: `_walk_route` returns `_ARRIVED` before touching the leg
                # cursor, and on a SINGLE-waypoint route — which is what `stage_shops`
                # produces — leg 0 is also the last leg, so the key is never written on any
                # path at all. Arrived-instantly, walking and wedged are all indistinguish-
                # able by it. Caught by noticing `sell_leg` absent on a run where the sell
                # was WORKING, before a second day was spent on it.
                #
                # `{tag}_stall` is what `_market_walk_toward` actually maintains: it climbs
                # while the greedy step makes no progress and the walk gives up at
                # `stall_limit`. A wedged approach shows it climbing; an instant arrival
                # never writes it either, but paired with `sell_stage` the two are no
                # longer ambiguous — stage set means arrival, stall climbing means wedge.
                stage_keys = ("mkt_phase", "bs_state", "sell_stall", "sell_stage",
                              "sell_vendor", "sell_find_wait", "sell_popup_wait",
                              "cap_craft_stage", "buy_stall", "buy_stage", "fetch_stage")
                kv = stage_key_readout(m, stage_keys)
                bank_state = f" {capname}({kv})" if kv else f" {capname}()"
        # The tool-gone confession (skills/harvest.py): a toolless miner makes no
        # swings, so neither relocation nor the reward stream will ever name this —
        # the status line must.
        # WHICH surface is refusing every capability? Sixteen gates share one
        # "idle UI" clause, so a single stale gump/shop window empties the ready
        # set — forge15 burned 671 ticks on that with the status line unable to
        # say which one. Named here, once, for both agents' sake.
        def _ui(o):
            if o is None:
                return ""
            flags = ["gump" for _ in (o.gumps or [])][:1]
            if getattr(o, "popup", None) is not None:
                flags.append("popup")
            if getattr(o, "shop_buy", None) is not None:
                flags.append("shopbuy")
            if getattr(o, "shop_sell", None) is not None:
                flags.append("shopsell")
            if getattr(o, "pending_target", None) is not None:
                flags.append("cursor")
            return " ui=" + ",".join(flags) if flags else ""

        tool_gone = miner.memory.get("harvest_tool_missing", 0)
        grimm_flag = (" NO-TOOL!"
                      if tool_gone >= MineSmeltDeliver.tool_missing_confess else "")
        # The relocation window, live: `stuck/size` of real swing verdicts (see
        # `Harvest.productive_clilocs`). forge6: the pair status was blind to WHY a
        # dead-vein miner wasn't relocating — the window's own fill rate is the
        # difference between "still deciding" and "samples are being lost".
        recent = miner.memory.get("harvest_recent_stuck")
        if recent is not None and len(recent) > 0:
            grimm_flag += f" win={sum(recent)}/{len(recent)}"
        silent = miner.memory.get("harvest_silent")
        if silent:
            grimm_flag += f" silent={silent}"
        # WHY the window is full, which `win=` alone cannot say. The three causes
        # want three different fixes — `nores` is an exhausted bank (relocate),
        # `inval` is a tile we cannot hit at all (cycle to another node; the whole
        # point of `node_exhausted_clilocs`), `packfull` is a sink problem no walk
        # can help. A partition — one tick charged once — and CUMULATIVE over the
        # run, which `win=` is not: it is a rolling window that every relocation
        # empties. So these do NOT sum to `win=` and a reader must not try; an
        # earlier comment here and in the audit both claimed they did.
        # Printed beside `banks=`, the output ceiling itself — and `banks=` counts
        # banks the shard gave a VERDICT about, never banks merely aimed at.
        by = miner.memory.get("harvest_stuck_by_cause") or {}
        if by:
            grimm_flag += " " + " ".join(f"{k}={by[k]}" for k in sorted(by))
        banks = miner.memory.get("harvest_banks_touched")
        if banks:
            grimm_flag += f" banks={len(banks)}"
        # The MineSmeltDeliver phase: a frozen miner in `smelt` is a different
        # wedge (forge unreachable, ore unsmeltable) than one in `mine` (dead
        # vein / lost tool) — from outside they look identical without this.
        grimm_flag += f" ph={miner.memory.get('smelt_phase', 'mine')}"
        # Walk vs swing, the split that decides follow-up 28 (audit §27.4). Ore never
        # respawns inside a day, so output is `banks reached x 10-34` and the only lever
        # is reaching more banks — which makes "is a dead stand costing us the walk or
        # the proving?" the question. `steps=` on the worker line CANNOT answer it: a
        # relocation issues one fire-and-forget `WalkTo` and then idles, so it reads 0.
        walk = int(miner.memory.get("harvest_walk_ticks", 0) or 0)
        swing = int(miner.memory.get("harvest_swing_ticks", 0) or 0)
        if walk or swing:
            grimm_flag += f" walk={walk} swing={swing}"
        # The streak distribution the window's size has to respect (audit §28.3). Printed
        # as longest-recovered(total): if no vein ever came back after N consecutive stuck
        # replies, a give-up above N abandons nothing that would have paid.
        rec = miner.memory.get("harvest_recoveries") or {}
        peak = int(miner.memory.get("harvest_stuck_max", 0) or 0)
        if rec or peak:
            # `recov=` is the longest streak a vein ever came BACK from; `peak=` is the
            # longest ever reached at all. Printed together because either alone is
            # unreadable: 0 recoveries with a peak of 3 says almost nothing, and the same
            # 0 with a peak of 24 says every length up to the window was tried.
            grimm_flag += (f" recov={max(rec)}({sum(rec.values())})" if rec
                           else " recov=none")
            grimm_flag += f" peak={peak}"
        print(f"— forge pair {bank_state} "
              f"grimm[iron={pack_amount(m_obs, INGOT_GRAPHICS)}{grimm_flag}]  "
              f"drop[grimm_sees={_ground_iron(m_obs)} pim_sees={_ground_iron(p_obs)}]  "
              f"pim[{pim.mode} {telemetry_line(pim, 'tinker', p_obs)} "
              f"iron={pack_amount(p_obs, INGOT_GRAPHICS)} "
              f"tongs={pack_amount(p_obs, TONGS_GRAPHIC)} "
              f"gold={pack_amount(p_obs, GOLD_GRAPHIC)} banked={banked_amount(p_obs)}"
              f"{_ui(p_obs)} "
              f"net={earned:+d}g ({earned / hours:+.0f}g/h)] —")
        for line in snap:
            print(f"  {line}")
    for t in threads:
        t.join(timeout=5)
    print("\nthe forge pair's day is done")


def run_carpenter_life(*, host: str = "127.0.0.1", port: int = 2594,
                       ticks: int = 900, account_prefix: str = "animacarp",
                       monitor: bool = False,
                       knobs: dict[str, Any] | None = None) -> None:
    """Run ONE carpenter LIVING the full autonomous loop via `CarpenterLife`.

    The fourth life, and the first for a profession with no work skill — everything it
    does is a goal-scoped capability, so this run is really a test of whether the
    capability layer alone can carry an agent through a day. Runs on the `LifeRunner`
    harness, which owns staging verification, provenance, the leash on both agent
    memories, and the standard telemetry — see `life_runner.py` for why those are
    structural rather than per-runner.

    `knobs` is the ENTRY POINT of the tuning channel, and it exists for one reason:
    without it the channel is wireless at the only end that matters. `LifeSpec.knobs`
    forwards to `life_factory` and `CarpenterLife.__init__` accepts `**knobs`, but no
    production caller could set one — a caller had to hand-build a spec, which is the
    shape of a mechanism only the tests have. CLAUDE.md defers the Phase-7
    evolution-vs-random rerun on precondition (a), "the genome's axes can steer a full
    Life", and a multi-hour single-GM live budget is gated on that being TRUE; this
    parameter is where a genome axis, a bandit or a tuning sweep reaches the Life.

    Optional by design (`None` == the shipped constants, byte-identical to every
    existing caller). Every key must be a knob the Life routes through `anima2/knobs.py`
    — `bank_reserve`, `econ_grace`, `disagreement_ticks` today. A raw threshold tuned
    from out here is a new drift avenue, not an axis: a malformed value read raw on one
    side and clamped on the other is the rule-vs-gate class arriving through the very
    knob that was supposed to be safe (see that module's docstring).

    That "must" is now ENFORCED rather than documented: `LifeSpec.__post_init__` checks
    every key against `CarpenterLife.KNOBS` and raises here, before the login. It was a
    comment alone until a reviewer walked `knobs={"profession": "mage"}` through it and
    got a Life that staged and reported as a carpenter while deciding as a mage.
    """
    # No `BANK_RESERVE` here on purpose: the staged line's reserve is read off the built
    # Life by `LifeRunner.run`, which is the only place a TUNED value exists.
    from .carpenter_life import BOARD_BATCH_COST, SAW_COST, CarpenterLife
    from .life_runner import LifeRunner, LifeSpec, Staged, pack_amount, stage_shops
    from .life_runner import owned_tool_readout
    from .live_common import wipe_area
    from .skills.carpentry import BuySaw, FetchBoards, SellFurniture

    SAW_GRAPHICS = frozenset(BuySaw.owned_tool_graphics)
    BOARD_GRAPHICS = frozenset(FetchBoards.fetched_graphics)
    FURNITURE = SellFurniture.sold_graphic

    def stage(gm, serial, body) -> Staged:
        prof = PROFESSIONS["carpenter"]
        cx, cy, cz = gm.stage(serial, *TRADE_SMITH_SPOT,
                              skills=prof.skills, items=list(prof.items))
        wipe_area(gm, cx, cy, radius=8, z=cz)
        gm.command_on('[Set Name "Sten"', serial)
        shop_serials: dict = {}
        routes, _tiles = stage_shops(
            gm, z=cz, anchor=(cx, cy), exclude=serial,
            spots={"vendor_spot": ("Carpenter", VENDOR_SPOT[-1]),
                   "banker_spot": ("Banker", BANKER_SPOT[-1])},
            serials_out=shop_serials)
        # Seed money, and ONLY that: a carpenter cannot make its own material, so with
        # an empty purse and no supplier it would correctly wait forever. The seed buys
        # exactly one batch of boards plus a spare saw; everything past that is earned.
        return Staged(routes=routes, home=(cx, cy),
                      econ_memory={"craft_spot": (cx, cy),
                                   "shop_serials": shop_serials},
                      memory={"craft_spot": (cx, cy),
                              "shop_serials": shop_serials},
                      seed_gold=BOARD_BATCH_COST + SAW_COST)

    def status_extra(life, obs) -> str:
        return (f"saw={owned_tool_readout(obs, SAW_GRAPHICS)} "
                f"boards={pack_amount(obs, BOARD_GRAPHICS)} "
                f"furniture={pack_amount(obs, FURNITURE)}")

    LifeRunner(
        LifeSpec(profession="carpenter", persona_name="Sten",
                 account_prefix=account_prefix,
                 # `**k` is the spec's tuning channel (named `k` only to keep it clear
                 # of the runner's own `knobs` argument, which is what FILLS it): a
                 # factory that drops it would silently swallow every axis a caller set.
                 life_factory=lambda body, persona, routes, **k: CarpenterLife(
                     body=body, persona=persona, routes=routes, **k),
                 stage=stage, status_extra=status_extra,
                 # Read off the CLASS the factory builds, not spelled out here: the spec
                 # then tracks a subclass that gains a knob, and can never disagree with
                 # the constructor it splats into about which keys are thresholds.
                 knob_names=CarpenterLife.KNOBS,
                 # Copied, not aliased: the spec outlives this call and a caller that
                 # kept its dict could otherwise retune a running Life by mutating it.
                 knobs=dict(knobs or {})),
        host=host, port=port, ticks=ticks, monitor=monitor,
    ).run(_run_worker)


def run_woodsman_life(*, host: str = "127.0.0.1", port: int = 2594,
                      ticks: int = 600, account_prefix: str = "animawood",
                      monitor: bool = False, persist_insights: bool = False,
                      forest: tuple[int, int] = YEW_FOREST,
                      knobs: dict[str, Any] | None = None) -> None:
    """Run ONE lumberjack LIVING the full autonomous loop via `WoodsmanLife`.

    The third profession to get a life of its own, and the first that does not fight
    for a living: its chain is tree -> log -> board -> gold, and its tool is
    consumable. Runs on the `LifeRunner` harness (staging verification, provenance,
    both-memory leash, standard telemetry — see `life_runner.py`).

    `knobs` is the tuning channel's ENTRY POINT — see `run_carpenter_life` for the whole
    reason it exists (a channel that stops at the spec is a channel only tests can
    reach, and CLAUDE.md gates a multi-hour live budget on it reaching a Life). Optional:
    `None` is byte-identical to every existing caller. Keys must be knobs the Life routes
    through `anima2/knobs.py`, and `LifeSpec.__post_init__` enforces that against
    `WoodsmanLife.KNOBS` before the login rather than trusting this sentence.
    """
    from .life_runner import LifeRunner, LifeSpec, Staged, pack_amount, stage_shops
    from .life_runner import owned_tool_readout
    from .live_common import wipe_area
    from .skills.woodwork import AXE_GRAPHICS, BOARD_GRAPHIC, LOG_GRAPHIC
    from .woodsman_life import WoodsmanLife

    def stage(gm, serial, body) -> Staged:
        facet = _survey_map(body)
        groves = find_tree_clusters(facet, *forest)
        if not groves:
            raise RuntimeError(f"no grove found near {forest} on map {facet}")
        (sx, sy), trees = groves[0]
        print(f"grove: map={facet} stand ({sx},{sy}) with {len(trees)} trees in reach "
              f"({len(groves)} groves near {forest})")
        prof = PROFESSIONS["lumberjack"]
        wx, wy, wz = gm.stage(serial, sx, sy, skills=prof.skills,
                              items=list(prof.items))
        wipe_area(gm, wx, wy, radius=8, z=wz)
        gm.command_on('[Set Name "Bjorn"', serial)
        shop_serials: dict = {}
        routes, _tiles = stage_shops(
            gm, z=wz, anchor=(wx, wy), exclude=serial,
            spots={"vendor_spot": ("Carpenter", (wx + 1, wy)),
                   "tool_vendor_spot": ("Weaponsmith", (wx - 1, wy)),
                   "banker_spot": ("Banker", (wx, wy + 1))},
            serials_out=shop_serials)
        # THREE shops around one stand — the tightest packing any runner has, and
        # exactly the geometry where nearest-to-spot resolution flips a coin.
        return Staged(routes=routes, home=(wx, wy),
                      econ_memory={"shop_serials": shop_serials},
                      memory={"harvest_nodes": [(t.x, t.y, t.z, t.graphic)
                                                for t in trees],
                              "shop_serials": shop_serials},
                      banner="with a hatchet")

    def status_extra(life, obs) -> str:
        line = (f"axe={owned_tool_readout(obs, AXE_GRAPHICS)} "
                f"logs={pack_amount(obs, LOG_GRAPHIC)} "
                f"boards={pack_amount(obs, BOARD_GRAPHIC)}")
        # When a process_logs goal holds the stack, show the completion bookkeeping the
        # achievement check reads — "not achieved" has several distinct causes, and this
        # block earned its place in a real debugging round (commit c49c444).
        cur = life.econ_agent.goal_stack.current
        if cur is not None and cur.goal.params.get("capability") == "process_logs":
            m = life.econ_agent.memory
            line += (f" (need={m.get('cap_process_needed')}"
                     f" delta={m.get('cap_process_board_delta')}"
                     f" left={m.get('cap_process_logs_remaining')}"
                     f" fin={m.get('cap_process_finished_goal_id')})")
        return line

    LifeRunner(
        LifeSpec(profession="lumberjack", persona_name="Bjorn",
                 account_prefix=account_prefix,
                 # `**k` is the spec's tuning channel (named `k` only to keep it clear
                 # of the runner's own `knobs` argument, which is what FILLS it): a
                 # factory that drops it would silently swallow every axis a caller set.
                 life_factory=lambda body, persona, routes, **k: WoodsmanLife(
                     body=body, persona=persona, routes=routes, **k),
                 stage=stage, status_extra=status_extra,
                 # Read off the CLASS the factory builds — see `run_carpenter_life`.
                 knob_names=WoodsmanLife.KNOBS,
                 # Copied, not aliased: the spec outlives this call and a caller that
                 # kept its dict could otherwise retune a running Life by mutating it.
                 knobs=dict(knobs or {})),
        host=host, port=port, ticks=ticks, monitor=monitor,
        persist_insights=persist_insights,
    ).run(_run_worker)


def run_warrior_village(count: int, *, host: str = "127.0.0.1", port: int = 2594,
                        ticks: int = 200, account_prefix: str = "animawar",
                        prey: str = "Ettin", prey_target: int = 2, spacing: int = 25,
                        monitor: bool = False,
                        knobs: dict[str, Any] | None = None) -> None:
    """Run `count` swordsmen LIVING the full autonomous loop via `WarriorLife`: each
    hunts, and when it loses its blade or runs low on bandages it re-arms/restocks/banks
    on its own, then resumes. Each warrior is staged at its own hunting pocket with a
    Weaponsmith, a Healer, and a Banker (spread far enough apart that each buy resolves
    to the right one), full plate + Katana + bandages + seed gold, and a KILLS-DRIVEN
    prey supply the monitor tops up (spawn one per confirmed kill — no accumulating
    swarm). Uses `_run_worker` unchanged (`WarriorLife` duck-types as an `Agent`).

    ONE `knobs` dict for the whole roster, not one per warrior. Every Bram is the same
    class staged the same way, so per-warrior tuning would be a fleet EXPERIMENT (N
    genomes on one shard) and not a runner argument — and this runner's roster shares a
    single GM control plane and one prey budget, which is exactly the confound that makes
    such a comparison unsound. Whoever wants that should say so and build it.
    """
    from .life_runner import build_tuned_life, enforce_gold_provenance, validate_knobs
    from .live_common import GM_RELOGIN_COOLDOWN_S, fresh_suffix, login_throttle
    from .profession import HUNTING_SPOT
    from .warrior_life import WarriorLife

    # Before the first packet — and this runner spawns `count` of them, so the cost of
    # checking late scales with the roster.
    knobs = validate_knobs(knobs, WarriorLife.KNOBS, label="warrior village")

    hx, hy = HUNTING_SPOT
    prof = PROFESSIONS["swordsman"]
    items = ["Bandage 100", "Katana", "PlateChest", "PlateLegs", "PlateArms",
             "PlateGloves", "PlateGorget", "PlateHelm"]

    print(f"raising a warrior village: {count} swordsman(men) at {host}:{port}")
    bodies: list[tuple[int, ResilientIpcBody]] = []
    seats = _monitor_ports(monitor, [f"w{i}" for i in range(count)])
    for i in range(count):
        account = f"{account_prefix}{i}{fresh_suffix()}"
        seat = seats[f"w{i}"]
        try:
            body = ResilientIpcBody.spawn(host, port, account, account, pump_ms=400,
                                          monitor_port=seat)
        except Exception as e:  # noqa: BLE001
            print(f"  {account}: login failed ({e})")
            continue
        bodies.append((i, body))
        watch = f"  watch: http://127.0.0.1:{seat}/" if seat else ""
        print(f"  {account}: Bram{i} the swordsman{watch}")
        time.sleep(3.0)
    if not bodies:
        print("no warriors came online")
        return

    login_throttle(GM_RELOGIN_COOLDOWN_S)
    gm = GmControl.spawn(host, port).__enter__()
    warriors: list[dict] = []
    try:
        gm.hide()
        # PASS 1 — place every warrior and clear every pocket BEFORE any vendor or prey
        # exists. Wiping is per-pocket but its radius overlaps a neighbour's stand
        # (spacing 25, wipe 20), so doing it inline would delete the PREVIOUS warrior's
        # freshly staged vendors (they sit at +/-12) and prey. Two passes keep each
        # warrior's furniture safe no matter how many warriors there are.
        placed: list[tuple[int, object, int, tuple[int, int, int]]] = []
        for i, body in bodies:
            serial = body.ready["player"]["serial"]
            sx, sy = hx + i * spacing, hy
            gx, gy, gz = gm.stage(serial, sx, sy, skills=prof.skills, items=items)
            for r in (20, 12, 6):  # clear stray mobiles so a fresh pocket, not a swarm
                gm.command_area("[WipeNPCs", gx - r, gy - r, gx + r, gy + r, gz)
            placed.append((i, body, serial, (gx, gy, gz)))

        # PASS 2 — now that every pocket is clear, dress each warrior and stage its own
        # vendors + prey; nothing wipes after this point.
        for i, body, serial, (gx, gy, gz) in placed:
            for c in ("[Set Str 150", "[Set Dex 125", "[Set Hits 150", "[Set HitsMax 150"):
                gm.command_on(c, serial)
            gm.command_on(f'[Set Name "Bram{i}"', serial)
            # Delete the ~1000 starter gold (else the warrior banks it immediately
            # instead of hunting) and seed a small reserve, below BANK_RESERVE, so it
            # can re-arm a lost blade before it has looted much. This runner predates
            # the harness and had `enforce_gold_provenance` INLINED — the same three
            # observes, the same owner-filtered pack lookup, the same delete loop. It is
            # the function now: a duplicated function drifts exactly the way the
            # duplicated readbacks did (`obsview.py`'s docstring), and provenance is the
            # one thing a measured-income claim rests on.
            enforce_gold_provenance(gm, body, serial)
            gm.command_on("[AddToPack Gold 50", serial)
            # Vendors pushed well OUT of the hunting pocket (>=10 tiles, spread apart):
            # near the stand they'd distract Greet/Wander (the warrior drifts to greet a
            # friendly NPC when no prey is adjacent) and each buy's "closest mobile to its
            # spot" pick needs them well-separated anyway.
            # Exclude EVERY warrior serial (not just this one): with several warriors the
            # widened mobile search can otherwise resolve to a different agent standing
            # nearby — the same hazard `run_village` guards with `all_agent_serials`.
            all_serials = {b.ready["player"]["serial"] for _i, b in bodies}
            # An IRONWORKER, not a Weaponsmith: ServUO picks a Weaponsmith's extra stock
            # with `Utility.Random(3)` and only one branch carries swords, so it stocks a
            # Katana on a 1-in-3 roll fixed at spawn. IronWorker installs SBSwordWeapon,
            # whose Katana is unconditional (see skills/warrior.py::BuyWeapon).
            gm.stage_npc("IronWorker", gx + 12, gy, gz, exclude=all_serials)
            gm.stage_npc("Healer", gx - 12, gy, gz, exclude=all_serials)
            gm.stage_npc("Banker", gx, gy + 12, gz, exclude=all_serials)
            gm.stage_npc("Armorer", gx, gy - 12, gz, exclude=all_serials)
            # Prey spawned ADJACENT to the stand so Hunt engages immediately (before the
            # warrior can drift), each on its own tile around the warrior.
            # Prey spawned adjacent AND PINNED (`CantWalk`) so a wounded creature stands
            # and fights instead of fleeing out of reach at low HP (live-caught: real
            # Attacks took an Ettin to 3 HP, then it fled from distance 1 to 13).
            adj = [(1, 0), (-1, 0), (0, 1), (0, -1)]
            for k in range(prey_target):
                dx, dy = adj[k % len(adj)]
                gm.command_at(f"[Add {prey}", gx + dx, gy + dy, gz)
                mob = gm.find_mobile_near(gx + dx, gy + dy, exclude=all_serials)
                if mob is not None:
                    gm.command_on("[Set CantWalk true", mob.serial)
            routes = {"weapon_vendor_spot": [(gx + 12, gy)],
                      "healer_spot": [(gx - 12, gy)],
                      "banker_spot": [(gx, gy + 12)],
                      "armorer_spot": [(gx, gy - 12)]}
            life = build_tuned_life(
                WarriorLife, knobs, body=body,
                persona=Persona(name=f"Bram{i}", combat_disposition="aggressive"),
                routes=routes)
            warriors.append({"i": i, "life": life, "spot": (gx, gy, gz), "respawned": 0})
        # Staging is serial and GM-heavy, so with a big roster the FIRST-staged bodies sit
        # idle for a long time before anyone starts playing. Warm every body (and report
        # its liveness) right before the threads start, so a body that went stale during
        # staging is visible here instead of silently never acting.
        for w in warriors:
            body = w["life"].hunt_agent.body
            for _ in range(2):
                body.observe()
            print(f"  Bram{w['i']}: connected={body.connected}")
        # The knobs this roster will actually run under, read off a BUILT Life through
        # the clamps its own readers use — the fifth banner in this file to need it, and
        # the one that reported nothing tunable at all. `leash_readout` rather than the
        # bare number because THIS runner never calls `set_leash`: a warrior roams while
        # hunting, on purpose, so `wander_leash` is inert here and a banner printing it
        # as a plain value would report a tuning that provably cannot happen.
        from .skills.market import _bank_reserve
        from .skills.movement import leash_readout
        _m = warriors[0]["life"].econ_agent.memory
        print(f"staged {len(warriors)} warrior(s). the hunt begins.  "
              f"(reserve {_bank_reserve(_m)}, "
              f"leash {leash_readout(_m, warriors[0]['life'])})\n")

        status: dict[int, str] = {}
        lock = threading.Lock()
        threads: list[threading.Thread] = []
        for w in warriors:
            t = threading.Thread(target=_run_worker,
                                 args=(w["life"], ticks, w["i"], status, lock, "swordsman"),
                                 daemon=True)
            threads.append(t)
            t.start()
            # Stagger the starts so the warriors' pump windows interleave on the shared
            # shard instead of every bridge asking for service on the same beat.
            time.sleep(0.7)

        # Monitor: print a live snapshot + keep prey IN MELEE. Two things matter:
        #  (a) spawn prey adjacent to the warrior's LIVE position (from its cached last
        #      observation), not a fixed stand the warrior has drifted away from; and
        #  (b) PIN each spawned creature (`[Set CantWalk true`, the same pin `stage_npc`
        #      uses for vendors) — a live action trace showed combat works fine (the
        #      Ettin's HP fell 9->5->3 under real Attacks) but a wounded ServUO creature
        #      FLEES at low HP and outruns the warrior (distance 1 -> 13), so the kill was
        #      never landed. Pinned prey stands and fights, so a won fight actually
        #      finishes.
        # Bounded: replace each confirmed kill, top up only when idle — no swarm.
        adj = [(1, 0), (-1, 0), (0, 1), (0, -1)]

        def _spawn_pinned(px: int, py: int, pz: int, dx: int, dy: int) -> None:
            # Every GM call costs a full pump (~pump_ms) on the ONE shared control
            # connection, and the single-threaded shard serves that connection in the same
            # loop as the warriors' own. `retries=1` is the big win here: the creature was
            # just `[Add`-ed at a known tile, so one observation finds it — the default 3
            # retries tripled this helper's cost (~2s -> ~0.8s) and, multiplied by warriors
            # x prey, was what starved the warrior bridges at 3+ warriors.
            gm.command_at(f"[Add {prey}", px + dx, py + dy, pz)
            mob = gm.find_mobile_near(px + dx, py + dy, retries=1,
                                      exclude={w["life"].hunt_agent.body.ready["player"]["serial"]
                                               for w in warriors})
            if mob is not None:
                gm.command_on("[Set CantWalk true", mob.serial)
        # GM-work BUDGET per monitor cycle. The control connection is shared and every GM
        # call costs a pump on the same single-threaded shard the warriors are playing on,
        # so unbounded per-warrior restocking scales GM traffic linearly with the roster
        # and starves the warriors themselves (measured: 3 warriors x 2 prey ~= 12s of GM
        # work inside a 3s cycle -> every warrior frozen). Two guards keep it flat:
        #  - at most `_GM_SPAWNS_PER_CYCLE` spawns per cycle, ROUND-ROBIN over the roster
        #    (a starting offset that advances each cycle), so every warrior is served
        #    regularly without any single cycle blowing the budget; and
        #  - a cycle interval that grows with the roster.
        # The budget scales with the roster but stays a bounded FRACTION of the cycle: a
        # spawn costs ~0.8s of GM pumps, the cycle is `3 + (N-1)`s, and we let GM use at
        # most about half of it — so restocking keeps up with a bigger roster without the
        # control plane ever monopolizing the shard again. (A fixed budget of 2 would need
        # ~5 cycles / ~35s for one full pass at 5 warriors.)
        cycle_s = 3.0 + 1.0 * (len(warriors) - 1)
        gm_spawns_per_cycle = max(2, int(cycle_s / 2))
        rr = 0
        while any(t.is_alive() for t in threads):
            time.sleep(cycle_s)
            budget = gm_spawns_per_cycle
            order = [warriors[(rr + n) % len(warriors)] for n in range(len(warriors))]
            rr = (rr + 1) % len(warriors)
            for w in order:
                if budget <= 0:
                    break
                lo = w["life"].body.last_obs
                if lo is None or lo.player.dead:
                    continue
                px, py, pz = lo.player.pos.x, lo.player.pos.y, lo.player.pos.z
                w["last_kills"] = w["life"].kills
                # PRESENCE-based top-up (not a timer): count the live hostiles actually
                # near THIS warrior and refill toward `prey_target`. A pinned creature can
                # still end up out of reach if the warrior drifts, and a kill removes one
                # — this keeps fightable creatures on top of the warrior, and spawns
                # nothing when the pocket is stocked.
                near = sum(1 for m in lo.mobiles
                           if m.serial != lo.player.serial and m.hits > 0 and m.distance <= 3)
                for k in range(min(budget, max(0, prey_target - near))):
                    dx, dy = adj[k % len(adj)]
                    _spawn_pinned(px, py, pz, dx, dy)
                    budget -= 1
            with lock:
                snap = [status[i] for i in sorted(status)]
            modes = " ".join(f"Bram{w['i']}:{w['life'].mode}" for w in warriors)
            print(f"— warrior village [{modes}] —\n  " + "\n  ".join(snap))
        for t in threads:
            t.join()
    finally:
        try:
            gm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
    total_kills = sum(w["life"].kills for w in warriors)
    print(f"\nthe day's hunt is done. total kills across the village: {total_kills}")


class _ChainPriorityClient:
    """Pick the ready capability that ADVANCES a production chain, not the first one.

    `CapabilityCognition(None, ...)` chooses `ready[0]` — the first OBSERVATION-READY
    capability in registry order. That is a fine default, but it cannot finish a chain
    whose earliest link stays ready: the artisan's `craft_tongs` remains ready while iron
    remains, so it is picked forever and the wares are never sold or delivered (measured
    live — the artisan sat on `tongs=5` while the purse never moved).

    Readiness stays the eligibility rule; this only expresses PREFERENCE among the ready.
    The capability's own admission still gates everything, so a preference for something
    not actually ready is simply not admitted — safe by construction.
    """

    def __init__(self, priority: tuple[str, ...]) -> None:
        self.priority = priority

    def complete(self, system: str, user: str) -> str:
        # `CapabilityCognition._situation` lists the currently-ready ids in the prompt.
        for capability in self.priority:
            if capability in user:
                return '{"schema":1,"decision":"capability","capability":"%s"}' % capability
        return '{"schema":1,"decision":"idle"}'


class _TapBody:
    """Record the last observation an agent took, without adding any traffic of its own.

    The roster's status line shows reward/steps, but a craft capability confirms no reward
    (a solo artisan made 5 tongs with `total_reward() == 0.0`), so those columns cannot say
    whether the pipeline is moving. This tap lets the monitor read what each agent last saw
    and report the things that actually matter: tongs made, gold carried, reagents bought.
    """

    def __init__(self, inner) -> None:
        self.inner = inner
        self.last_obs = None

    @property
    def connected(self) -> bool:
        return self.inner.connected

    @property
    def ready(self):
        return self.inner.ready

    def observe(self):
        self.last_obs = self.inner.observe()
        return self.last_obs

    def act(self, action) -> None:
        self.inner.act(action)


class _ThrottledAgent:
    """Tick an agent only 1 tick in `every`, yielding the rest of the shard to someone else.

    The artisan+mage roster measured the real constraint: on a single-threaded shard a
    GUMP-DRIVEN craft FSM (many server round-trips per item) is starved by a hunting agent,
    while hunting itself is mostly LOCAL decisions and loses very little from a slower
    cadence. A second shard port cannot fix this — a second ServUO instance owns its own
    `Saves/` world, so the two agents would be in different universes and could never hand
    gold to each other, which is the whole pipeline. Budgeting the one shard between them
    is the lever that fits.

    Duck-types as an `Agent` (like `WarriorLife`) so `_run_worker` drives it unchanged.
    """

    #: Pause per YIELDED tick — a scheduling point, not a rate limit (see tick()).
    #: A class attribute so the budget-scale unit tests can zero it: 1,600 paced
    #: no-ops would cost them 16 seconds of pure sleep for nothing under test.
    yield_pause_s: float = 0.01

    def __init__(self, inner, every: int = 3) -> None:
        self.inner = inner
        self.every = max(1, int(every))
        self._n = 0

    @property
    def tick_budget_scale(self) -> int:
        """How many worker iterations one REAL tick of this agent costs.

        A yielded tick does no work and waits on nothing, so a throttled agent burns a
        fixed tick budget `every` times faster than an unthrottled peer and finishes
        long before it — silently, since a finished worker just stops updating. Callers
        that want the two to live equally long scale the throttled one's budget by this.
        """
        return self.every

    def tick(self):
        self._n += 1
        if self._n % self.every:
            # Yield WITH a breath. A yielded tick used to cost literally nothing,
            # which made the worker a GIL-hot pure-python spin between real ticks —
            # the prime suspect for the steered-pipeline run whose main thread
            # reached its monitor loop once in ~4 hours (health-check follow-up #4).
            # 10ms per yielded tick bounds the cost at `(every-1)*10ms` per real
            # tick (~70ms at every=8) while giving every other thread a scheduling
            # point the spin never offered.
            if self.yield_pause_s:
                time.sleep(self.yield_pause_s)
            return None  # yield this tick: no observe, no action, no server traffic
        return self.inner.tick()

    @property
    def body(self):
        return self.inner.body

    @property
    def persona(self):
        return self.inner.persona

    @property
    def episodes(self):
        return self.inner.episodes

    @property
    def memory(self):
        return self.inner.memory

    @property
    def ticks(self) -> int:
        return self.inner.ticks

    @property
    def mode(self):
        return getattr(self.inner, "mode", None)

    # Both of a Life's SELF-REPORTS pass through, because a report the runner cannot
    # read is not a report. `_run_worker` reads these off the object it drives, and for
    # the throttled carpenter (`run_supply_pair`) and the throttled mage
    # (`run_artisan_mage_village`) that object is this proxy — which has no
    # `__getattr__`, so until these existed `getattr(agent, ..., None)` was
    # unconditionally None and the two Lives that run their economy agent nearly every
    # tick were the two that could never print either line. Caught by the blast-radius
    # review of the exit-edge hold, 2026-08-03; the hold's own overdue report would have
    # shipped dead through exactly the same hole.
    @property
    def rule_gate_disagreement(self):
        return getattr(self.inner, "rule_gate_disagreement", None)

    @property
    def frame_overdue(self) -> bool:
        return bool(getattr(self.inner, "frame_overdue", False))

    @property
    def econ_agent(self):
        """The Life's ECONOMY agent — whose goal stack owns every capability frame, and
        therefore the retirement history `_run_worker`'s FRAME RETIRED report projects.

        The third passenger through the same hole as the two self-reports above, and
        added for the same reason: without it the report ships DEAD for precisely the
        throttled carpenter and the throttled mage, the two Lives that run their economy
        agent nearly every tick and so retire the most frames. `None` for a plain Agent
        underneath, which `frame_retirements` then resolves by falling back to the
        object itself (this proxy exposes no `goal_stack`, so that read fails closed to
        "nothing to report" — the unthrottled plain Agent is the shape that fallback is
        actually for)."""
        return getattr(self.inner, "econ_agent", None)

    @property
    def last_skill_name(self):
        """Which skill the wrapped agent last ran — the work-liveness alarm's arming
        condition (`_doing_work`). Fourth passenger through the same hole; without it a
        throttled Life reads `None`, which `_doing_work` treats as "working", and the
        alarm would judge a legitimately idle carpenter."""
        return getattr(self.inner, "last_skill_name", None)


def _pipeline_progress(tin_tap, mage) -> str:
    """What the pipeline has actually moved, read off each agent's last observation."""
    from .skills.hunt import GOLD_GRAPHIC
    from .skills.mage import SULFUROUS_ASH_GRAPHIC

    TONGS = 0x0FBB

    # `_pack` was written out here too — the same falsy-backpack and `==`-vs-`in` forms
    # `run_supply_pair`'s copy had. It is `obsview.pack_amount` now.
    t_obs = tin_tap.last_obs
    m_obs = getattr(mage.body, "last_obs", None)

    def _ground_gold(obs):
        return sum(i.amount for i in (obs.items if obs else [])
                   if i.graphic == GOLD_GRAPHIC and i.container is None)

    # Read the delivered purse from BOTH sides. The mage's view alone is not evidence
    # the handover happened: a mage that has wandered off, or died, simply cannot see
    # the tile — which reads identically to "nothing was ever delivered". The artisan
    # stands at the drop when it lets go, so its own view is the honest witness.
    ground = _ground_gold(m_obs)
    dropped = _ground_gold(t_obs)
    # Where the purse lies and how far the mage is from it. Worth carrying permanently:
    # a purse the artisan can see and the mage cannot is the exact shape of the one
    # failure this pipeline keeps hitting, and a bare "mage_sees=0" cannot express it.
    where = [(i.pos.x, i.pos.y) for i in (t_obs.items if t_obs else [])
             if i.graphic == GOLD_GRAPHIC and i.container is None]
    near = ""
    if where and m_obs is not None:
        d = min(max(abs(m_obs.player.pos.x - x), abs(m_obs.player.pos.y - y)) for x, y in where)
        near = f" at={where[0]} mage_is={d}away"
    # A frozen agent looks the same as a busy one from the outside, so say plainly
    # whether each side is alive and whole — a dead or bleeding mage explains a stalled
    # hunt far better than any guess about its planner. ONE definition of that readout:
    # `life_runner.hp_readout`. This line used to re-derive it inline, which is how the
    # only hp on any village status line ended up being a copy that no other runner
    # could reuse — and the ARTISAN, standing right beside it in the same string, had
    # none at all while being the half of this pipeline that earns the gold.
    from .life_runner import hp_readout
    return (f"artisan[hp={hp_readout(t_obs)} tongs={pack_amount(t_obs, TONGS)} "
            f"gold={pack_amount(t_obs, GOLD_GRAPHIC)}] "
            f"purse[mage_sees={ground} artisan_sees={dropped}{near}] "
            f"mage[hp={hp_readout(m_obs)} gold={pack_amount(m_obs, GOLD_GRAPHIC)} "
            f"ash={pack_amount(m_obs, SULFUROUS_ASH_GRAPHIC)}]")



def _cheb2(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Chebyshev distance between two (x, y) tiles — UO's own movement metric."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


#: The pipeline village's ground, CALIBRATED LIVE rather than guessed — every tile below
#: was confirmed by walking real steps to it and back with the same greedy stepping the
#: shop trip and the delivery walk actually use (`market._market_walk_toward` /
#: `woodwork.DeliverBoards`), with a control leg on known-good ground proving the probe
#: itself worked. This mirrors how `VENDOR_SPOT`/`BANKER_SPOT` were originally curated,
#: and exists because an unreachable vendor is indistinguishable from a broken brain:
#: the artisan selected `sell_tongs` correctly on every tick and still never sold.
#: The artisan's own stand + vendor route are simply the ground `live_sell_goal.py`
#: already sells on; the mage's tiles were probed fresh (the plaza south of the smithy).
ARTISAN_STAND = TRADE_SMITH_SPOT       #: proven by the standalone sell gate
ARTISAN_VENDOR_ROUTE = VENDOR_SPOT     #: curated 2-waypoint route; its end is the vendor
MAGE_DROP = (2609, 476)                #: greedy-reachable from BOTH stands (probed)
MAGE_STAND = (2609, 477)               #: open plaza — 9 of 12 probed neighbours walkable
MAGE_VENDOR = (2608, 477)              #: reagents, one tile from the mage's stand
MAGE_BANKER = (2610, 477)
PREY_SPOT = (2609, 480)                #: 3 tiles from the mage, 6 from the artisan
#: How far the mage may idle from its drop. DERIVED, not chosen: idle wandering must never
#: carry it inside the prey's melee reach, so the nearest tile the leash allows has to stay
#: at least `KeepDistance.too_close + 1` away from `PREY_SPOT`. Live-caught with the prey
#: verified pinned: a leash of 3 let the mage drift to one tile from a creature it could no
#: longer hurt, and it was beaten to death standing next to it.
MAGE_LEASH = max(1, _cheb2(PREY_SPOT, MAGE_DROP) - (KeepDistance.too_close + 1))


# Monitor seats live in `life_runner` now (one definition); re-exported under the old
# names because the CLI, the multi-agent runners and the placement tests address them.
from .life_runner import MONITOR_PORT_BASE  # noqa: E402, F401 — re-export
from .life_runner import monitor_ports as _monitor_ports  # noqa: E402


def run_artisan_mage_village(*, host: str = "127.0.0.1", port: int = 2594,
                            ticks: int = 400, account_prefix: str = "animapipe",
                            prey: str = "Ettin", mage_tick_every: int = 8,
                            monitor: bool = False,
                            steer_mage: str = "scripted",
                            mage_knobs: dict[str, Any] | None = None) -> None:
    """Run the WHOLE production pipeline unattended: an artisan and a mage, side by side.

    This is the goal's arc turned into a standing village rather than a scripted proof. The
    tinker crafts tongs, sells them, and carries the purse to the mage's funding spot; the
    mage collects it, buys reagents, and hunts with spells — each deciding for itself, with
    no driver telling either one what to do next:

      - the ARTISAN runs its capability planner under a `CapabilityCognition` with NO
        client, which picks the first OBSERVATION-READY capability. The readiness gates are
        therefore the whole policy: craft while it has iron, sell while it has tongs,
        deliver once the purse is worth a trip, re-buy iron when it runs low.
      - the MAGE runs `MageLife`, which switches itself between hunting and resupply
        (reagents > collect a delivered purse > bank > hunt).

    Both are driven by `_run_worker` unchanged (`MageLife` duck-types as an `Agent`, and the
    artisan simply IS one).
    `mage_knobs` names its target because only ONE of these two is a Life: the artisan is
    a plain `Agent` under a `CapabilityCognition`, with no orchestrator and so no
    thresholds to tune. `knobs=` would have read as "the pipeline's knobs" and silently
    meant "the mage's".
    """
    from .capability_cognition import CapabilityCognition
    from .life_runner import build_tuned_life, validate_knobs
    from .live_common import GM_RELOGIN_COOLDOWN_S, fresh_suffix, login_throttle, wipe_area
    from .mage_life import MageLife

    # Before the first packet — see `run_forge_pair`'s copy.
    mage_knobs = validate_knobs(mage_knobs, MageLife.KNOBS, label="pipeline mage")

    print(f"raising an artisan+mage village at {host}:{port}")
    bodies = {}
    viewers = _monitor_ports(monitor, ["tinker", "mage"])
    for role, acct in (("tinker", f"{account_prefix}t{fresh_suffix()}"),
                       ("mage", f"{account_prefix}m{fresh_suffix()}")):
        try:
            bodies[role] = ResilientIpcBody.spawn(host, port, acct, acct, pump_ms=400,
                                                  monitor_port=viewers[role])
            seat = f"  watch: http://127.0.0.1:{viewers[role]}/" if viewers[role] else ""
            print(f"  {acct}: the {role}{seat}")
        except Exception as e:  # noqa: BLE001
            print(f"  {acct} ({role}): login failed ({e})")
        time.sleep(3.0)
    if len(bodies) < 2:
        print("the pipeline needs both an artisan and a mage; aborting")
        for b in bodies.values():
            b.close() if hasattr(b, "close") else None
        return

    login_throttle(GM_RELOGIN_COOLDOWN_S)
    gm = GmControl.spawn(host, port).__enter__()
    agents = []
    try:
        gm.hide()
        serials = {r: b.ready["player"]["serial"] for r, b in bodies.items()}
        all_serials = set(serials.values())
        # The ARTISAN stands on the trade smithy's own calibrated tile, with its vendor
        # reached by the hand-curated `VENDOR_SPOT` route — the exact ground
        # `live_sell_goal.py` already sells on. This replaces an earlier, guessed
        # `hunting_spot + 14` stand on the Minoc ridge, where the artisan chose
        # `sell_tongs` correctly every tick and still never sold a thing: BOTH legs that
        # matter here walk GREEDILY (the shop trip in `market._market_walk_toward`, the
        # delivery in `woodwork.DeliverBoards`), so an unwalkable stand is indistinguish-
        # able from a stalled brain. A probe that walked real steps settled it — from the
        # old ridge stand the character moved 0 tiles in 16 tries in all four directions,
        # while a control on this pocket moved normally.
        tx, ty, tz = gm.stage(serials["tinker"], *ARTISAN_STAND,
                              skills=PROFESSIONS["tinker"].skills,
                              items=["TinkerTools 999", "IronIngot 60"])
        wipe_area(gm, tx, ty, radius=10, z=tz)
        # The MAGE works the open plaza south of the artisan (9 of 12 probed neighbours
        # walkable, versus a walled-in west and north), close enough that the delivery
        # walk is a few tiles and the drop lands where the mage already looks.
        mx, my, mz = gm.stage(serials["mage"], *MAGE_STAND,
                              skills=PROFESSIONS["mage"].skills,
                              # A SMALL starting pouch on purpose: with 30 the mage never
                              # reaches its own reorder line inside a session, so the last
                              # link of the chain (spending the artisan's gold on the
                              # ability to cast) never gets exercised. It still has to
                              # EARN what it spends — both agents start broke.
                              items=["Spellbook", "SulfurousAsh 12", "Bandage 50"])
        for role, (px, py) in (("mage", (mx, my)), ("tinker", (tx, ty))):
            gm.command_on(f'[Set Name "{role.capitalize()}"', serials[role])
        for c in ("[Set Int 100", "[Set Mana 100", "[Set ManaMax 100",
                  "[Set Str 80", "[Set Hits 80", "[Set HitsMax 80"):
            gm.command_on(c, serials["mage"])
        # Provenance, both sides. The MAGE starts broke, so every coin it spends came from
        # the artisan; and the ARTISAN starts broke too, so the only gold it can ever
        # deliver is what it EARNED selling its wares — otherwise it would simply hand over
        # its ~1000 starter gold and the "production funds the fighter" claim would be
        # hollow (it also outranks crafting in the chain priority, so it would never craft).
        from .life_runner import enforce_gold_provenance
        for role in ("mage", "tinker"):
            enforce_gold_provenance(gm, bodies[role], serials[role])
        st = [bodies["mage"].observe() for _ in range(3)][-1]
        # `pack_serial` reads OUR pack out of `st` by `st.player.serial`, which IS
        # `serials["mage"]`: `ResilientIpcBody` enforces
        # `observation.player.serial == ready["player"]["serial"]` on every observe, so
        # this is a body-level invariant rather than an assumption about whose body
        # answered.
        pack = pack_serial(st)
        # A staged spellbook is EMPTY; ServUO refuses a cast whose spell is not in the book.
        book = next((i for i in st.items if i.graphic in (0x0EFA, 0x0EFB)
                     and i.container in (pack, serials["mage"])), None)
        if book is not None:
            gm.command_on("[AllSpells", book.serial)
        # Vendors, each on probed-walkable ground and far enough apart that a buy resolves
        # to the intended NPC. The artisan's Tinker sits at the calibrated route's end.
        gm.stage_npc("Tinker", *ARTISAN_VENDOR_ROUTE[-1], tz, exclude=all_serials)
        gm.stage_npc("Mage", *MAGE_VENDOR, mz, exclude=all_serials)      # sells reagents
        gm.stage_npc("Banker", *MAGE_BANKER, mz, exclude=all_serials)
        # Prey for the mage, pinned so a wounded creature stands and fights. Pinning also
        # keeps the artisan safe: `CantWalk` means it cannot close the tiles between them,
        # which is what an earlier live catch needed (an artisan 2 tiles from a pinned
        # Ettin produced nothing for 900 ticks — inside melee reach even while pinned).
        gm.command_at(f"[Add {prey}", *PREY_SPOT, mz)
        mob = gm.find_mobile_near(*PREY_SPOT, retries=3, exclude=all_serials)
        pinned = False
        if mob is not None:
            gm.command_on("[Set CantWalk true", mob.serial)
            # Read it back FROM THE SERVER. `if mob is not None: set` used to be the whole
            # story, so a prey that was never found — or never actually pinned — produced
            # a silently roaming creature, and "the mage keeps dying" then looked like a
            # brain problem instead of a staging one. Never infer staging from the fact
            # that a command was sent.
            pinned = str(gm.get_property_value("CantWalk", mob.serial)).lower() in ("true", "1")
        print(f"prey: {prey} at {PREY_SPOT} — "
              f"{'pinned' if pinned else 'NOT PINNED (it will roam and chase)'}")

        # The ARTISAN: its own capability planner, choosing by readiness (no client).
        tin_prof = PROFESSIONS["tinker"]
        tin_tap = _TapBody(bodies["tinker"])
        tinker = Agent(
            body=tin_tap, persona=Persona(name="Tinker"),
            planner=tin_prof.planner(capability_goals=True),
            # Finish the chain: turn wares into gold and hand the purse over BEFORE
            # making more, then restock iron; craft only when there is nothing to move.
            cognition=CapabilityCognition(
                _ChainPriorityClient((
                    "sell_tongs", "deliver_gold", "buy_iron",
                    "craft_tongs", "buy_tinker_tool", "bank_gold",
                )), "tinker"),
            cognition_interval=1,
            profession="tinker", goal_policy=CapabilityPolicy("tinker"),
        )
        tinker.memory.update({
            "craft_spot": (tx, ty),
            # A ROUTE, not a point: a single straight-line greedy walk cannot reach this
            # vendor from the smith's stand — the very reason `VENDOR_SPOT` was curated.
            "vendor_spot": list(ARTISAN_VENDOR_ROUTE),   # sell tongs / buy iron
            "mage_drop": MAGE_DROP,                      # where the purse goes
        })
        # The MAGE: its own orchestrator, switching itself between hunting and resupply.
        mage = build_tuned_life(
            MageLife, mage_knobs, body=bodies["mage"],
            persona=Persona(name="Mage", combat_disposition="aggressive"),
            routes={"mage_vendor_spot": [MAGE_VENDOR], "banker_spot": [MAGE_BANKER]},
            steering=steer_mage)
        # Leash the mage to its plaza. Without this it wanders off once the prey is dead
        # and never observes the purse the artisan delivers — live-caught here: a correct
        # `deliver_gold` handed over 140 gold that simply sat on the ground, because by
        # then the mage was ~15 tiles away and `fetch_gold` (rightly) needs to SEE it.
        # Leash it to the DROP itself, tightly: the mage has to stay close enough to
        # notice a purse arriving, not merely close enough to be in the neighbourhood.
        mage.set_leash(MAGE_DROP, MAGE_LEASH)
        # Budget the one shard: the mage yields most ticks so the artisan's
        # round-trip-hungry craft FSM actually gets served (see _ThrottledAgent).
        agents = [("tinker", tinker), ("mage", _ThrottledAgent(mage, mage_tick_every))]
        # The mage's two tunable numbers, read off the BUILT Life through the clamps its
        # own readers use — the fourth banner in this file to need it, and the last
        # runner that could still have carried a tuned value an operator could not see.
        # `MAGE_LEASH` is DERIVED (prey distance minus the kite radius) and a tuned
        # `wander_leash` outranks it, so printing the constant would be the same lie
        # `run_supply_pair`'s local `sten_leash` would have told.
        from .skills.market import _bank_reserve
        from .skills.movement import leash_readout
        print(f"staged: artisan@({tx},{ty}) with iron | mage@({mx},{my}) broke, hunting"
              f"  (mage reserve {_bank_reserve(mage.econ_agent.memory)}, "
              f"leash {leash_readout(mage.econ_agent.memory, mage)})\n")

        status: dict[int, str] = {}
        lock = threading.Lock()
        threads = []
        for i, (role, agent) in enumerate(agents):
            # Scale a throttled agent's budget by its own throttle, so both workers live
            # about as long. Without this the mage — yielding 7 ticks in 8, each costing
            # nothing — spends `ticks` iterations roughly `every` times faster than the
            # artisan and quietly ends mid-run, leaving its last observation to be read
            # as if it were current.
            budget = ticks * getattr(agent, "tick_budget_scale", 1)
            t = threading.Thread(target=_run_worker,
                                 args=(agent, budget, i, status, lock, role), daemon=True)
            threads.append(t)
            t.start()
            time.sleep(0.7)

        # Monitor discipline (health-check follow-up #4): the steered run printed ONE
        # status block in ~4 hours, and nothing in the log could say why — so the loop
        # now (a) prints FIRST and sleeps after, so even a loop that somehow runs once
        # shows the start-state, (b) timestamps every block with the wall-clock delta
        # since the last one, making starvation and slowdown measurable IN the log,
        # and (c) prints any body exception instead of letting anything swallow it.
        _mon_started = time.monotonic()
        _mon_last = _mon_started
        _mon_n = 0
        while any(t.is_alive() for t in threads):
            _mon_now = time.monotonic()
            _mon_gap, _mon_last = _mon_now - _mon_last, _mon_now
            _mon_n += 1
            with lock:
                snap = [status[i] for i in sorted(status)]
            mode = mage.mode
            # Why is the artisan doing what it is doing? Show its READY set and the goal
            # it actually holds — the difference between "not eligible" and "not chosen".
            try:
                from .capabilities import ready_capability_ids
                from .skills.base import SkillContext as _SC
                _ready = ()
                if tin_tap.last_obs is not None:
                    _ready = ready_capability_ids(
                        "tinker",
                        _SC(obs=tin_tap.last_obs, persona=tinker.persona, memory=tinker.memory),
                    )
                _cur = tinker.goal_stack.current
                _goal = _cur.goal.params.get("capability") if _cur else None
            except Exception:  # noqa: BLE001 — telemetry must never break the run
                _ready, _goal = ("?",), "?"
            # Steering evidence (audit #8): every LLM consult as candidates->chosen.
            # NOTE the indentation of this whole block is LOAD-BEARING: its first
            # version sat one level too shallow, which silently moved every print
            # below OUT of the while loop — the run monitored nothing for four hours
            # and printed once at exit, and a whole "monitor starvation" investigation
            # (health-check follow-up #4) traced back to exactly this dedent.
            if mage.steering_log:
                cands, chosen, used = mage.steering_log[-1]
                print(f"  steer#{len(mage.steering_log)}: {list(cands)} -> {chosen} "
                      f"({'LLM' if used else 'fallback'})")
            print(f"[mon#{_mon_n} +{_mon_gap:.0f}s "
                  f"T+{(_mon_now - _mon_started) / 60:.1f}m] "
                  f"— artisan+mage village [mage:{mode}] "
                  f"{_pipeline_progress(tin_tap, mage)} "
                  f"artisan_ready={list(_ready)} artisan_goal={_goal} —"
                  f"\n  " + "\n  ".join(snap))
            time.sleep(4.0)  # sleep AFTER printing — a starved loop still shows state
        for t in threads:
            t.join()
    finally:
        try:
            gm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        for b in bodies.values():
            try:
                b.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
    print("\nthe pipeline village has closed for the day.")


def _parse_knobs(pairs: list[str]) -> dict[str, int]:
    """`--knob KEY=VALUE` pairs into the runners' `knobs` dict.

    The last rung of the tuning channel: `LifeSpec.knobs` reaches a Life and
    `run_carpenter_life`/`run_woodsman_life` expose the argument, but until this
    parser existed no human or script could set one without importing the module.

    Values are ints because every knob today is one, and a bad value dies HERE —
    before the login, the GM staging and the provenance gold-wipe. `knobs.py`
    clamps a malformed value silently by design (a live run must not crash on a
    tuning typo), which is exactly why the boundary that CAN be loud should be.
    Unknown KEYS are not checked here: `LifeSpec.__post_init__` owns that, against
    the allowlist of the class it builds, so the two can never disagree.
    """
    out: dict[str, int] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--knob wants KEY=VALUE, got {pair!r}")
        try:
            out[key] = int(raw)
        except ValueError:
            raise SystemExit(f"--knob {key} wants an integer, got {raw!r}") from None
    return out


def _route_knobs(parsed: dict[str, int], roles: tuple[str, ...], *, runner: str,
                 default_role: str | None) -> dict[str, dict[str, int]]:
    """Split `--knob [ROLE:]KEY=VALUE` pairs across the LIVES a runner actually builds.

    The role prefix exists because a runner can build more than one Life and they are
    not interchangeable: `run_supply_pair` builds a woodsman AND a carpenter, both with
    a `bank_reserve`, so a bare `bank_reserve=400` there has no honest meaning. Rather
    than pick one silently it is refused, with the roles named — `default_role=None` is
    how a runner declares that. Where there IS only one Life the bare form is the whole
    interface and the prefix is optional.

    Three refusals, all of them the same failure the pre-existing blanket guard caught
    for two runners ("a knob the run silently ignores is the wireless-channel defect
    wearing a CLI"), now stated per runner instead of allowlisted:
      - a knob passed to a runner that builds no Life at all,
      - a role this runner does not have,
      - a bare key on a runner with several.

    What it does NOT check is the knob NAMES: that belongs to `validate_knobs` against
    the allowlist of the class each runner actually builds, so the two can never
    disagree. Same division of labour `_parse_knobs` already documents for values.
    """
    by_role: dict[str, dict[str, int]] = {r: {} for r in roles}
    for key, value in parsed.items():
        role, sep, name = key.rpartition(":")
        if not sep:
            role = None
        if not roles:
            raise SystemExit(
                f"--knob does not apply to {runner}: it builds no Life, and every "
                "tuning knob is a Life threshold. Ignoring it silently would misreport "
                "what the run actually ran.")
        if role is None:
            if default_role is None:
                raise SystemExit(
                    f"--knob {key}={value} is ambiguous on {runner}, which builds "
                    f"{len(roles)} Lives ({', '.join(roles)}). Say which: "
                    f"--knob {roles[0]}:{name}={value}")
            role = default_role
        if role not in by_role:
            raise SystemExit(
                f"--knob {key}={value}: {runner} has no {role!r}. "
                f"Roles here: {', '.join(roles)}")
        # `bank_reserve=1` and `carpenter:bank_reserve=2` are DIFFERENT parser keys that
        # land on the same role and knob, so without this the second silently overwrote
        # the first — a knob the run ignores, which is the one thing this function exists
        # to refuse. Review-caught.
        if name in by_role[role]:
            raise SystemExit(
                f"--knob {name} was given twice for {role!r} on {runner} "
                f"({by_role[role][name]} and {value}). A bare KEY=VALUE and a "
                f"{role}:KEY=VALUE are the same knob; pass one.")
        by_role[role][name] = value
    return by_role


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", action="store_true",
                    help="run an artisan+mage village: the crafter funds the mage, unattended")
    ap.add_argument("--mage-tick-every", type=int, default=8,
                    help="tick the mage 1 in N (budgets the shared shard toward the "
                         "round-trip-hungry craft loop; measured: the artisan is starved "
                         "outright by an unthrottled mage)")
    ap.add_argument("--warriors", type=int, default=0,
                    help="run N swordsmen living the autonomous hunt<->re-arm loop (WarriorLife); "
                         "supersedes the trade-village roster when > 0")
    ap.add_argument("--miners", type=int, default=2)
    ap.add_argument("--lumberjacks", type=int, default=1)
    ap.add_argument("--fishers", type=int, default=1)
    ap.add_argument("--blacksmiths", type=int, default=1)
    ap.add_argument("--townsfolk", type=int, default=1)
    # Opt-in, default 0: the hunter profession (Phase 3 item 3) has its own
    # calibrated, isolated field (`profession.HUNTING_SPOT`) and doesn't need
    # to join the default roster for the village to keep working exactly as
    # before — mirrors every other roster knob's own default-count shape.
    ap.add_argument("--hunters", type=int, default=0)
    ap.add_argument("--ticks", type=int, default=60)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument(
        "--account-prefix",
        default="anima",
        help="account/password prefix for isolated or repeatable village runs",
    )
    ap.add_argument("--steer-mage", choices=["scripted", "llm"], default="scripted",
                    help="pipeline only: let a real LLM pick the mage's economy branch "
                         "whenever 2+ are simultaneously admissible (closed vocabulary; "
                         "an invalid answer falls back to the rule's own choice)")
    ap.add_argument("--forge-pair", action="store_true",
                    help="run the FLAGSHIP positive-margin chain: a miner delivering "
                         "iron to a TinkerLife that crafts and sells tongs (7g per "
                         "delivered ingot vs 4g raw)")
    ap.add_argument("--supply-pair", action="store_true",
                    help="run a lumberjack supplying a carpenter (deliver_boards -> "
                         "fetch_boards), coordinating only through the ground")
    ap.add_argument("--carpenter", action="store_true",
                    help="run ONE carpenter living the full loop (buy boards -> craft -> "
                         "sell -> bank, and replace a lost saw) via CarpenterLife")
    ap.add_argument("--woodsman", action="store_true",
                    help="run ONE lumberjack living the full loop (chop -> process -> "
                         "sell -> bank, and replace a broken axe) via WoodsmanLife")
    ap.add_argument("--knob", action="append", metavar="[ROLE:]KEY=VALUE", default=[],
                    help="tune one Life threshold, repeatable (e.g. --knob bank_reserve=400, "
                         "or --knob carpenter:bank_reserve=400 on a runner with two Lives). "
                         "Carried by --carpenter, --woodsman, --forge-pair, --supply-pair, "
                         "--warriors and --pipeline; --supply-pair REQUIRES the role prefix. "
                         "Every key must be a knob the Life routes through anima2/knobs.py; "
                         "an unknown key or role fails before the shard connection")
    ap.add_argument("--monitor", action="store_true",
                    help="serve a read-only web view of each agent (loopback only); "
                         "the URL per agent is printed at startup")
    ap.add_argument("--narrate", action="store_true",
                    help="say what each agent is doing and WHY — a `~~` line per intent "
                         "change into the log (tick + tile + evidence), and a short clause "
                         "spoken in-game so it shows in the client view under --monitor. "
                         "Costs no agent tick: speech only rides a tick the agent spent "
                         "idle. See anima2/narrate.py and docs/OBSERVATIONS.md")
    ap.add_argument("--forum", action="store_true", help="post each villager's day to uotavern")
    ap.add_argument("--chatter", action="store_true", help="LLM cognition: speak in character while working")
    # Opt-in, unset by default: zero effect on any currently-passing roster unless
    # passed (Phase 4 item 2). Supersedes --chatter when both are given — it wires
    # a role-tiered cognition (chatter + reflection) rather than a single client.
    ap.add_argument("--llm-tiers", choices=["anthropic", "replicate", "stub"], default=None,
                     help="role-tiered LLM cognition (chatter + reflection) via build_tiered_clients")
    # Opt-in, unset by default (Phase 4 item 4): zero effect on any currently-
    # passing roster unless passed. Each miner picks a `MineSmeltDeliver.
    # deliver_threshold` via `ParamTuner.choose()` at construction time and
    # records the session's outcome back to `data/skill_ledger.jsonl`.
    ap.add_argument("--tune-deliver-threshold", action="store_true",
                     help="bandit-tune each miner's deliver_threshold (Phase 4 item 4)")
    ap.add_argument("--ledger-path", default=None,
                     help="override data/skill_ledger.jsonl (mainly for isolated test/live runs)")
    # Opt-in, unset by default (Phase 4 item 5): zero effect on any currently-
    # passing roster unless passed. Wraps each agent's cognition in a
    # `CurriculumController` that picks an Observation-derived milestone and
    # records an `Episode` when one is achieved (observational only for now).
    ap.add_argument("--curriculum", action="store_true",
                     help="automatic curriculum: track/pick milestones (Phase 4 item 5)")
    ap.add_argument(
        "--curriculum-goals",
        action="store_true",
        help="drive admitted profession work from curriculum milestones (B2 opt-in)",
    )
    ap.add_argument(
        "--capability-goals",
        action="store_true",
        help="choose verified operation capabilities from a closed vocabulary (B4 opt-in)",
    )
    # Opt-in, unset by default (Phase 6 item 1): zero effect on any currently-
    # passing roster unless passed, and only takes effect at all when
    # reflection is itself wired (today: only via --llm-tiers). Resumes each
    # agent's distilled insights from data/insights.jsonl at construction and
    # keeps appending newly-distilled ones to the same file as the run goes.
    ap.add_argument("--persist-insights", action="store_true",
                     help="disk-backed ReflectionMemory: resume + persist insights across restarts "
                          "(Phase 6 item 1; requires --llm-tiers to have any effect)")
    # Opt-in, unset by default (Phase 6 item 2): zero effect on any currently-
    # passing roster unless passed. Each agent's worker thread queues
    # confirmed trade/market/hunt events (in-memory only, no I/O); the main
    # thread flushes them all to data/chronicle.jsonl once, after every
    # worker has finished.
    ap.add_argument("--chronicle", action="store_true",
                     help="record inter-agent trade/market/hunt events to data/chronicle.jsonl "
                          "(Phase 6 item 2)")
    ap.add_argument("--chronicle-path", default=None,
                     help="override data/chronicle.jsonl (mainly for isolated test/live runs)")
    # Opt-in, unset by default (Phase 6 item 5): zero effect on any currently-
    # passing roster unless passed, and only takes effect alongside
    # --chatter/--llm-tiers (the flags that wire an LLMCognition at all). When
    # set, LLMCognition gates each queued line on a `random()` draw vs the
    # persona's `talkativeness`, so chatty personas visibly out-talk quiet
    # ones. Off by default so every prior chatter proof (which assumed every
    # valid reply is voiced) stays byte-for-byte unchanged — see
    # `cognition.py::LLMCognition`'s docstring for why the gate is opt-in.
    ap.add_argument("--talkativeness-gate", action="store_true",
                     help="gate LLM speech on Persona.talkativeness (Phase 6 item 5; "
                          "needs --chatter or --llm-tiers to have any effect)")
    args = ap.parse_args()
    parsed_knobs = _parse_knobs(args.knob)
    # Every branch routes its own knobs now. The blanket guard this replaces —
    # "--knob needs --carpenter or --woodsman" — was an allowlist of the two runners that
    # carried the channel, and it was correct only while the other five construction
    # sites were wireless (audit follow-up 2).
    if args.forge_pair:
        k = _route_knobs(parsed_knobs, ("tinker",), runner="--forge-pair",
                         default_role="tinker")
        run_forge_pair(host=args.host, port=args.port, ticks=args.ticks,
                       monitor=args.monitor, narrate=args.narrate, knobs=k["tinker"])
        return

    if args.supply_pair:
        # No default role ON PURPOSE: two Lives, both with a `bank_reserve`.
        k = _route_knobs(parsed_knobs, ("woodsman", "carpenter"),
                         runner="--supply-pair", default_role=None)
        run_supply_pair(host=args.host, port=args.port, ticks=args.ticks,
                        monitor=args.monitor, narrate=args.narrate,
                        woodsman_knobs=k["woodsman"], carpenter_knobs=k["carpenter"])
        return

    if args.carpenter:
        k = _route_knobs(parsed_knobs, ("carpenter",), runner="--carpenter",
                         default_role="carpenter")
        run_carpenter_life(host=args.host, port=args.port, ticks=args.ticks,
                           monitor=args.monitor, knobs=k["carpenter"])
        return

    if args.woodsman:
        k = _route_knobs(parsed_knobs, ("woodsman",), runner="--woodsman",
                         default_role="woodsman")
        run_woodsman_life(host=args.host, port=args.port, ticks=args.ticks,
                          monitor=args.monitor,
                          persist_insights=args.persist_insights, knobs=k["woodsman"])
        return

    if args.pipeline:
        k = _route_knobs(parsed_knobs, ("mage",), runner="--pipeline",
                         default_role="mage")
        run_artisan_mage_village(host=args.host, port=args.port, ticks=args.ticks,
                                 monitor=args.monitor, steer_mage=args.steer_mage,
                                 account_prefix=args.account_prefix,
                                 mage_tick_every=args.mage_tick_every,
                                 mage_knobs=k["mage"])
        return

    if args.warriors > 0:
        # One dict for the roster — see `run_warrior_village` for why per-warrior tuning
        # is a fleet experiment and not a runner argument.
        k = _route_knobs(parsed_knobs, ("swordsman",), runner="--warriors",
                         default_role="swordsman")
        run_warrior_village(args.warriors, host=args.host, port=args.port,
                            monitor=args.monitor, knobs=k["swordsman"],
                            ticks=args.ticks, account_prefix=args.account_prefix)
        return

    # The trade-village roster builds plain `Agent`s, not Lives, so it has no thresholds
    # to tune and says so rather than accepting a knob it would drop.
    _route_knobs(parsed_knobs, (), runner="the trade-village roster", default_role=None)

    roster = (["miner"] * args.miners + ["lumberjack"] * args.lumberjacks
              + ["fisher"] * args.fishers + ["blacksmith"] * args.blacksmiths
              + ["townsfolk"] * args.townsfolk + ["hunter"] * args.hunters)
    run_village(roster, host=args.host, port=args.port, ticks=args.ticks,
                account_prefix=args.account_prefix,
                forum=args.forum, chatter=args.chatter, llm_tiers=args.llm_tiers,
                tune_deliver_threshold=args.tune_deliver_threshold, ledger_path=args.ledger_path,
                curriculum=args.curriculum, persist_insights=args.persist_insights,
                curriculum_goals=args.curriculum_goals,
                capability_goals=args.capability_goals,
                chronicle=args.chronicle, chronicle_path=args.chronicle_path,
                talkativeness_gate=args.talkativeness_gate)


if __name__ == "__main__":
    main()
