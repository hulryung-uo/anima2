"""`foundry/fitness.py` offline tests (PHASE5.md item 1's "Offline tests
(planned)" list): component math at known inputs reproduces v1's formulas, a
frozen trajectory gates to ~0, a wall-walker (high deny ratio) is penalized, a
one-action spammer doesn't fake liveness (the >=2-distinct-groups rule), and
`channel_b=False` isolates channel (a) exactly the way the live gate's own
decisive recomputation needs. All fixtures are hand-built `TrajectorySummary`s
— no live server, mirrors `test_control.py`'s own offline-fixture style.
"""

from __future__ import annotations

import pytest

from anima2.foundry import fitness
from anima2.foundry.trajectory import SkillStat, TrajectorySummary

MINING_ID = 45  # uoconst.SKILL_NAMES[45] == "Mining" (GATHERING category)


def _summary(**overrides) -> TrajectorySummary:
    base = dict(start_ts=0.0, end_ts=3600.0, alive_start=True, alive_end=True)
    base.update(overrides)
    return TrajectorySummary(**base)


# --- locked weights ----------------------------------------------------------


def test_locked_weights_match_v1():
    """Cited to v1 `../anima/foundry/kernel/fitness.py` — porting these
    verbatim (not re-guessing) is the whole point of "locked"."""
    assert fitness.W_SKILL == 1.0
    assert fitness.W_WORTH == 0.3
    assert fitness.W_PRODUCE == 0.2
    assert fitness.GOLD_NORM == 20.0


# --- component math at known inputs ------------------------------------------


def test_component_math_at_known_inputs_matches_v1_formula():
    summ = _summary()
    summ.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=40.0)}  # +10
    summ.gold_start, summ.gold_end = 1000, 1200  # +200 over 1h
    summ.action_counts = {"use": 40, "move": 20}  # 60 actions/hr, 2 distinct groups -> liveness 1.0
    summ.items_into_pack = [(0x1BEF, 10, 100.0)]  # ingot, value 6 each -> 60 gold-equivalent/hr

    fb = fitness.compute_fitness(summ)

    assert fb.duration_h == pytest.approx(1.0)
    assert fb.skill_gain_rate == pytest.approx(10.0)
    assert fb.skill_term == pytest.approx(10.0)  # W_SKILL * 10
    assert fb.networth_rate == pytest.approx(200.0)
    assert fb.worth_term == pytest.approx(3.0)  # W_WORTH * (200 / GOLD_NORM)
    assert fb.produce_value_rate == pytest.approx(60.0)
    assert fb.produce_term == pytest.approx(0.6)  # W_PRODUCE * (60 / GOLD_NORM)
    assert fb.viability_gate == pytest.approx(1.0)  # alive=1, liveness=1, loop_penalty=0
    assert fb.total == pytest.approx(13.6)  # gate * (10 + 3 + 0.6 + 0)


def test_worth_and_produce_terms_zero_when_no_economy_activity():
    summ = _summary()
    summ.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=30.0)}  # 0 gain
    summ.action_counts = {"use": 40, "move": 20}
    fb = fitness.compute_fitness(summ)
    assert fb.skill_term == 0.0
    assert fb.worth_term == 0.0
    assert fb.produce_term == 0.0


# --- viability gate: frozen trajectory ---------------------------------------


def test_frozen_trajectory_gates_to_zero_even_with_skill_gain():
    """A frozen agent (no confirmed actions at all) must gate to ~0 —
    PHASE5.md's own point: this is *correct*, not a bug, given the known
    Harvest/Mine freeze this fitness must be robust to."""
    summ = _summary()
    summ.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=40.0)}
    summ.action_counts = {}  # nothing confirmed — the freeze case

    fb = fitness.compute_fitness(summ)

    assert fb.liveness == 0.0
    assert fb.viability_gate == 0.0
    assert fb.total == pytest.approx(0.0)


def test_dead_at_end_gates_to_zero_regardless_of_activity():
    summ = _summary(alive_start=True, alive_end=False)
    summ.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=40.0)}
    summ.action_counts = {"use": 100, "move": 100}
    fb = fitness.compute_fitness(summ)
    assert fb.alive_fraction == 0.0
    assert fb.total == pytest.approx(0.0)


# --- loop penalty: wall-walker ------------------------------------------------


def test_wall_walker_high_deny_ratio_penalized_vs_normal_walker():
    honest = _summary()
    honest.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=40.0)}
    honest.action_counts = {"use": 60, "move": 40}
    honest.steps_confirmed, honest.steps_denied = 95, 5  # low deny ratio

    wall_walker = _summary()
    wall_walker.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=40.0)}
    wall_walker.action_counts = {"use": 60, "move": 40}
    wall_walker.steps_confirmed, wall_walker.steps_denied = 5, 95  # high deny ratio

    fb_honest = fitness.compute_fitness(honest)
    fb_wall = fitness.compute_fitness(wall_walker)

    assert fb_wall.loop_penalty > fb_honest.loop_penalty
    assert fb_wall.viability_gate < fb_honest.viability_gate
    assert fb_wall.total < fb_honest.total


def test_loop_penalty_ignored_below_five_total_steps():
    """Matches v1: too few steps to say anything about looping -> 0 penalty."""
    summ = _summary()
    summ.steps_confirmed, summ.steps_denied = 0, 3  # denied 3/3, but total_steps < 5
    summ.action_counts = {"use": 30, "move": 30}
    fb = fitness.compute_fitness(summ)
    assert fb.loop_penalty == 0.0


# --- liveness: one-action spammer doesn't fake it -----------------------------


def test_one_action_spammer_does_not_fake_full_liveness():
    spammer = _summary()
    spammer.action_counts = {"use": 1000}  # 1 distinct group, saturates action_rate

    varied = _summary()
    varied.action_counts = {"use": 500, "move": 500}  # 2 distinct groups, same total

    fb_spam = fitness.compute_fitness(spammer)
    fb_varied = fitness.compute_fitness(varied)

    assert fb_spam.liveness == pytest.approx(0.75)  # base=1.0 * (0.5 + 0.5*0.5)
    assert fb_varied.liveness == pytest.approx(1.0)  # base=1.0 * (0.5 + 0.5*1.0)
    assert fb_spam.liveness < fb_varied.liveness


# --- channel_b=False isolates channel (a) -------------------------------------


def test_channel_b_excluded_keeps_skill_and_worth_terms_identical():
    summ = _summary()
    summ.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=40.0)}
    summ.gold_start, summ.gold_end = 500, 700
    summ.items_into_pack = [(0x1BEF, 10, 100.0)]
    summ.action_counts = {"use": 60, "move": 40}
    summ.positions = [(t, 100 + t, 200) for t in range(0, 20)]
    summ.speech_sent, summ.speech_recv = 2, 5

    fb_full = fitness.compute_fitness(summ, channel_b=True)
    fb_a = fitness.compute_fitness(summ, channel_b=False)

    assert fb_a.channel_b is False
    assert fb_a.skill_term == fb_full.skill_term
    assert fb_a.worth_term == fb_full.worth_term
    assert fb_a.produce_term == 0.0
    assert fb_full.produce_term > 0.0
    assert fb_a.behavior_bonus == 0.0
    assert fb_a.liveness == 1.0
    assert fb_a.loop_penalty == 0.0


def test_channel_b_excluded_still_ranks_honest_worker_above_zero_skill_gainer():
    """Offline analog of PHASE5.md item 1's live gate: even with channel (b)
    stripped entirely, an agent with real (channel-a) skill gain outranks one
    with none, however much channel-(b)-only activity/produce/behavior data
    the zero-skill agent racked up — the load-bearing signal (skill_term)
    never depended on the in-process tap.
    """
    honest = _summary()
    honest.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=35.0, last=45.0)}
    honest.action_counts = {"use": 50, "move": 10}

    rigged = _summary()
    rigged.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=35.0, last=35.0)}  # 0 gain
    rigged.action_counts = {"speech": 500, "move": 500}  # inflated activity, no real work
    rigged.items_into_pack = [(0x1BEF, 999, 1.0)]  # would inflate produce_term if channel b counted
    rigged.positions = [(t, t, t) for t in range(200)]  # would inflate behavior_bonus via regions
    rigged.speech_sent, rigged.speech_recv = 500, 500  # would inflate behavior_bonus via social

    fb_honest = fitness.compute_fitness(honest, channel_b=False)
    fb_rigged = fitness.compute_fitness(rigged, channel_b=False)

    assert fb_honest.total > fb_rigged.total
    assert fb_rigged.skill_term == 0.0
    assert fb_rigged.produce_term == 0.0
    assert fb_rigged.behavior_bonus == 0.0


# --- produce_value uses the ported ITEM_VALUES table --------------------------


def test_produce_term_uses_item_values_table_with_default_for_unknown_graphics():
    summ = _summary()
    summ.items_into_pack = [
        (0x1BEF, 10, 1.0),   # ingot, ITEM_VALUES == 6 -> 60
        (0x9999, 4, 2.0),    # unknown graphic -> ITEM_VALUE_DEFAULT == 1 -> 4
    ]
    fb = fitness.compute_fitness(summ)
    assert fb.produce_value_rate == pytest.approx(64.0)  # (60 + 4) / 1h
    assert fb.produce_term == pytest.approx(fitness.W_PRODUCE * (64.0 / fitness.GOLD_NORM))


def test_profession_cell_skill_backbone_excludes_explore_and_combat_bonus():
    """A profession cell (has_profession True) must not fund score from
    wandering/combat — only the social bonus is universal (v1's own
    descriptor-aligned invariant, ported)."""
    summ = _summary()
    summ.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=31.0)}
    summ.positions = [(t, t, t) for t in range(500)]  # would swing behavior_bonus if counted
    summ.damage_dealt = 100
    fb = fitness.compute_fitness(summ)
    assert fb.behavior_bonus == 0.0  # no speech, and explore/combat excluded for a profession cell
