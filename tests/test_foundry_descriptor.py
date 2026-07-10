"""`foundry/descriptor.py` offline tests (PHASE5.md item 3's "Offline tests
(planned)" list): `compute_descriptor` bins a fixture summary into the
expected cell, `profession_focus`'s categorical mapping picks the
highest-gain skill category, the bin edges are ported verbatim from v1, and
the negative control (a degenerate all-zero trajectory lands in the
NONE-profession cell). All fixtures are hand-built `TrajectorySummary`s — no
live server, mirrors `test_foundry_fitness.py`'s own offline-fixture style.
"""

from __future__ import annotations

import pytest

from anima2.foundry import descriptor, uoconst
from anima2.foundry.trajectory import SkillStat, TrajectorySummary

MINING_ID = 45     # GATHERING
BLACKSMITHY_ID = 7  # CRAFTING
WRESTLING_ID = 43   # COMBAT


def _summary(**overrides) -> TrajectorySummary:
    base = dict(start_ts=0.0, end_ts=3600.0, alive_start=True, alive_end=True)
    base.update(overrides)
    return TrajectorySummary(**base)


# --- locked bin edges, ported verbatim from v1 --------------------------------


def test_bin_edges_match_v1():
    """Cited to v1 `../anima/foundry/kernel/descriptor.py:24-26` — kernel-owned,
    ported verbatim (not re-guessed)."""
    assert descriptor.SOCIABILITY_EDGES == (0.02, 0.10)
    assert descriptor.AGGRESSION_EDGES == (0.02, 0.15)
    assert descriptor.MOBILITY_EDGES == (10.0, 40.0)
    assert descriptor.ACTIVE_AXES == ("profession_focus", "sociability")


def test_bin_helper_low_mid_high_boundaries():
    edges = (0.02, 0.10)
    assert descriptor._bin(0.0, edges) == 0     # low
    assert descriptor._bin(0.019, edges) == 0   # low, just under first edge
    assert descriptor._bin(0.02, edges) == 1    # mid, exactly at first edge
    assert descriptor._bin(0.099, edges) == 1   # mid, just under second edge
    assert descriptor._bin(0.10, edges) == 2    # high, exactly at second edge
    assert descriptor._bin(5.0, edges) == 2     # high


# --- compute_descriptor bins a fixture summary into the expected cell --------


def test_compute_descriptor_bins_fixture_into_expected_cell():
    summ = _summary()
    summ.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=40.0)}  # GATHERING +10
    summ.action_counts = {"use": 80, "move": 20}  # 100 actions
    summ.speech_sent = 5  # sociability = 5/100 = 0.05 -> mid bin

    d = descriptor.compute_descriptor(summ)

    assert d.profession_focus == uoconst.GATHERING
    assert d.sociability == pytest.approx(0.05)
    assert d.sociability_bin == 1  # mid: 0.02 <= 0.05 < 0.10
    assert d.cell == (uoconst.GATHERING, 1)
    assert d.full_cell == (uoconst.GATHERING, 1, d.aggression_bin, d.mobility_bin)


def test_compute_descriptor_silent_worker_lands_in_low_sociability_bin():
    summ = _summary()
    summ.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=31.0)}
    summ.action_counts = {"use": 100}
    summ.speech_sent = 0  # sociability 0.0 -> low

    d = descriptor.compute_descriptor(summ)

    assert d.sociability_bin == 0
    assert d.cell == (uoconst.GATHERING, 0)


def test_compute_descriptor_chatty_worker_lands_in_high_sociability_bin():
    summ = _summary()
    summ.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=31.0)}
    summ.action_counts = {"use": 80, "move": 20}  # 100 actions
    summ.speech_sent = 15  # sociability = 0.15 -> high

    d = descriptor.compute_descriptor(summ)

    assert d.sociability_bin == 2
    assert d.cell == (uoconst.GATHERING, 2)


# --- profession_focus categorical mapping -------------------------------------


def test_profession_focus_picks_highest_gain_category():
    summ = _summary()
    summ.skills = {
        MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=33.0),          # GATHERING +3
        BLACKSMITHY_ID: SkillStat(id=BLACKSMITHY_ID, name="Blacksmithy", first=30.0, last=40.0),  # CRAFTING +10
    }
    summ.action_counts = {"use": 10}

    d = descriptor.compute_descriptor(summ)

    assert d.profession_focus == uoconst.CRAFTING
    assert d.profession_gains == {"GATHERING": pytest.approx(3.0), "CRAFTING": pytest.approx(10.0)}


def test_profession_focus_combat_category():
    summ = _summary()
    summ.skills = {WRESTLING_ID: SkillStat(id=WRESTLING_ID, name="Wrestling", first=40.0, last=50.0)}
    summ.action_counts = {"attack": 5, "move": 5}

    d = descriptor.compute_descriptor(summ)

    assert d.profession_focus == uoconst.COMBAT


def test_profession_focus_none_when_no_skill_gain():
    summ = _summary()
    summ.skills = {MINING_ID: SkillStat(id=MINING_ID, name="Mining", first=30.0, last=30.0)}  # 0 gain
    summ.action_counts = {"use": 10}

    d = descriptor.compute_descriptor(summ)

    assert d.profession_focus == uoconst.NONE
    assert d.profession_gains == {}


# --- aggression: anima2 adaptation (action_counts["attack"], not a packet tally) --


def test_aggression_reads_tapped_attack_action_group():
    summ = _summary()
    summ.action_counts = {"attack": 20, "move": 80}  # 100 actions -> aggression 0.20 -> high

    d = descriptor.compute_descriptor(summ)

    assert d.aggression == pytest.approx(0.20)
    assert d.aggression_bin == 2


# --- mobility_rate ------------------------------------------------------------


def test_mobility_rate_regions_per_hour():
    summ = _summary(start_ts=0.0, end_ts=1800.0)  # 0.5h
    summ.positions = [(t, 8 * i, 0) for i, t in enumerate(range(10))]  # 10 distinct 8x8 regions
    summ.action_counts = {"move": 10}

    d = descriptor.compute_descriptor(summ)

    assert d.mobility_rate == pytest.approx(20.0)  # 10 regions / 0.5h
    assert d.mobility_bin == 1  # mid: 10.0 <= 20.0 < 40.0


# --- negative control: degenerate all-zero trajectory -------------------------


def test_negative_control_all_zero_trajectory_lands_in_none_profession_cell():
    """A degenerate trajectory (no skill gain, no actions, no speech at all)
    must land in the NONE-profession cell — never mistaken for a real
    worker's category. `total_actions == 0` also must not raise a
    division-by-zero (v1's own `max(1, ...)` guard, ported)."""
    summ = _summary()  # everything defaults to empty/zero

    d = descriptor.compute_descriptor(summ)

    assert d.profession_focus == uoconst.NONE
    assert d.profession_gains == {}
    assert d.sociability == 0.0
    assert d.sociability_bin == 0
    assert d.cell == (uoconst.NONE, 0)


def test_label_formats_profession_and_sociability():
    d = descriptor.Descriptor(profession_focus=uoconst.GATHERING, sociability_bin=1)
    assert d.label() == "gathering/mid-social"
