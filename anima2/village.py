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

from .agent import Agent
from .capabilities import CAPABILITIES, CapabilityPolicy
from .chronicle import ChronicleEvent, ChronicleLedger
from .contract import Observation, Say, Walk
from .control import GmControl
from .ipc_body import IpcBody, ResilientIpcBody
from .memory import Episode
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
from .skill_library import SkillLibrary
from .skill_tuning import DELIVER_THRESHOLD_CANDIDATES, ParamSpec, ParamTuner
from .skills import MineSmeltDeliver
from .skills.base import Status
from .uomap import find_tree_clusters

# Minoc-area woods (map 1), near the mining camp — keeps the village compact.
# Each lumberjack gets a distinct grove (a stand spot + the trees in reach).
FOREST_BASE = (2520, 450)
LUMBER_MAP = 1

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

#: Mirrors `skills/smelt.py::INGOT_GRAPHICS`/`curriculum.py::_INGOT_GRAPHICS`
#: — duplicated, not imported, matching this codebase's established
#: "duplicate a handful of graphic constants rather than reach into a
#: skill's own module" convention (see curriculum.py's own module docstring).
_CHRONICLE_BACKPACK_LAYER = 0x15
_CHRONICLE_DAGGER_INGOTS = 3
_CHRONICLE_INGOT_GRAPHICS = frozenset({0x1BEF, 0x1BF0, 0x1BF1, 0x1BF2})


def _pack_ingot_count(obs: Observation) -> int:
    bp = next((i for i in obs.items if i.layer == _CHRONICLE_BACKPACK_LAYER
              and i.container == obs.player.serial), None)
    if bp is None:
        return 0
    return sum(i.amount for i in obs.items if i.graphic in _CHRONICLE_INGOT_GRAPHICS and i.container == bp.serial)


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


def _run_worker(agent: Agent, ticks: int, idx: int, status: dict, lock: threading.Lock,
                job: str, *, chronicle: ChronicleLedger | None = None,
                counterpart: str | None = None,
                session_events: list[ChronicleEvent] | None = None) -> None:
    steps = says = 0
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
    for _ in range(ticks):
        if not agent.body.connected:
            break
        action = agent.tick()
        obs = agent.body.observe()
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
        if isinstance(action, Say):
            says += 1
            last_say = action.text
        with lock:
            line = (f"{agent.persona.name:<9} {job:<10} @({p.x},{p.y}) "
                    f"out+{agent.episodes.total_reward():.1f} steps={steps} says={says}")
            if last_say:
                line += f'  "{last_say[:60]}"'
            status[idx] = line


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


def _staging_items(plan_entry: dict, capability_goals: bool) -> list[str]:
    """Provision every prerequisite the selected closed operation cannot create."""

    items = list(plan_entry["prof"].items)
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
    groves = iter(find_tree_clusters(LUMBER_MAP, *FOREST_BASE))
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
                    items=_staging_items(p, capability_goals),
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
                    gm.stage_npc("Blacksmith", vx, vy, gz, exclude=all_agent_serials)
                if p["banker_spot"]:
                    bx, by = p["banker_spot"][-1]
                    gm.stage_npc("Banker", bx, by, gz, exclude=all_agent_serials)
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
            agent.memory["banker_spot"] = p["banker_spot"]  # blacksmith's bank route (trade pairing)
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


def main() -> None:
    ap = argparse.ArgumentParser()
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
