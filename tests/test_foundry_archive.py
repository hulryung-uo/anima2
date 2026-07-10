"""`foundry/archive.py` offline tests (PHASE5.md item 3's "Offline tests
(planned)" list): `reliability_score`'s locked formula, `Archive.add`'s
promotion rule — the LOAD-BEARING optimizer's-curse guard (a higher raw mean
but higher variance must NOT displace a steadier, higher-reliability
incumbent, while a genuinely-better low-variance genome does, and a
lower-scoring one never does), different cells coexisting, `elites()`/
`summary()` correctness, a real `archive.jsonl` round-trip across two
`Archive` instances (persistence is real, not just in-memory), corrupt-line
tolerance, and the negative control (a degenerate genome landing in the
NONE-profession cell never displaces a real worker elite in a different
cell). All fixtures are hand-built `Genome`s with pre-computed `eval` dicts —
no live server, no eval harness dependency (item 3 is independent of items
1-2 per PHASE5.md's own dependency note).
"""

from __future__ import annotations

import json

import pytest

from anima2.foundry import archive as archive_mod
from anima2.foundry import uoconst
from anima2.foundry.archive import Archive, Genome, InsertResult, cell_to_str, reliability_score


def _genome(gid: str, cell: tuple, per_seed: list[float] | None = None, fitness: float | None = None,
            **overrides) -> Genome:
    per_seed = per_seed or []
    fit = fitness if fitness is not None else (sum(per_seed) / len(per_seed) if per_seed else 0.0)
    ev = {"cell": list(cell), "fitness": fit}
    if per_seed:
        ev["per_seed_fitness"] = per_seed
    kwargs = dict(id=gid, eval=ev)
    kwargs.update(overrides)
    return Genome(**kwargs)


GATHERING_CELL = (uoconst.GATHERING, 0)
CRAFTING_CELL = (uoconst.CRAFTING, 1)
NONE_CELL = (uoconst.NONE, 0)


# --- locked constant + reliability_score formula, ported verbatim -------------


def test_promotion_lambda_locked_at_one():
    assert archive_mod.PROMOTION_LAMBDA == 1.0


def test_reliability_score_single_seed_falls_back_to_point_estimate():
    assert reliability_score([], 42.0) == 42.0
    assert reliability_score([50.0], 50.0) == 50.0  # < 2 seeds -> point estimate


def test_reliability_score_matches_mean_minus_lambda_times_pstdev():
    vals = [37.0, 39.0, 41.0]
    import statistics
    expected = statistics.fmean(vals) - archive_mod.PROMOTION_LAMBDA * statistics.pstdev(vals)
    assert reliability_score(vals, point=39.0) == pytest.approx(expected)
    assert reliability_score(vals, point=39.0) == pytest.approx(37.367, abs=1e-3)


def test_genome_reliability_property_reads_from_eval():
    g = _genome("g_00001", GATHERING_CELL, per_seed=[37.0, 39.0, 41.0])
    assert g.fitness == pytest.approx(39.0)
    assert g.reliability == pytest.approx(37.367, abs=1e-3)


def test_cell_to_str_joins_with_pipe():
    assert cell_to_str((uoconst.GATHERING, 1)) == "GATHERING|1"


# --- Archive.add: first genome fills an empty cell -----------------------------


def test_add_first_genome_fills_empty_cell(tmp_path):
    arc = Archive(tmp_path / "archive.jsonl")
    g = _genome("g_00001", GATHERING_CELL, fitness=10.0)

    result = arc.add(g)

    assert result.status == "filled"
    assert result.entered_grid is True
    assert result.prev_fitness is None
    assert arc.get_elite(GATHERING_CELL) is g
    assert arc.filled_cells() == 1


# --- THE LOAD-BEARING CASE: reliability-discounted promotion ------------------


def test_lucky_high_variance_genome_does_not_displace_steadier_incumbent(tmp_path):
    """The ported optimizer's-curse guard (v1 `g_00070`, archive.py:28-44
    adapted): a genome with a HIGHER raw mean (40 > 39) but much higher
    variance must NOT displace a steadier incumbent whose reliability_score
    is higher (37.37 > 1.06) — porting a raw-fitness promotion rule instead
    would silently let this happen."""
    arc = Archive(tmp_path / "archive.jsonl")
    steady = _genome("g_00001", GATHERING_CELL, per_seed=[37.0, 39.0, 41.0])  # mean 39, reliability ~37.37
    arc.add(steady)

    lucky = _genome("g_00002", GATHERING_CELL, per_seed=[10.0, 15.0, 95.0])  # mean 40 (higher!), reliability ~1.06
    assert lucky.fitness > steady.fitness  # the raw mean IS higher
    assert lucky.reliability < steady.reliability  # but reliability is far lower

    result = arc.add(lucky)

    assert result.status == "rejected"
    assert result.entered_grid is False
    assert arc.get_elite(GATHERING_CELL) is steady  # incumbent holds


def test_promotion_compares_reliability_to_the_incumbents_reliability_not_its_mean(tmp_path):
    """Kills a specific port mutant the other promotion tests cannot: a rule
    comparing the challenger's reliability against the incumbent's RAW MEAN.
    Incumbent per_seed=[30, 50] -> mean 40, reliability 30. Challenger
    per_seed=[35, 35] -> mean 35, reliability 35. Correct rule: 35 > 30 ->
    DISPLACES. The mutant rule (35 > 40?) would wrongly reject — so this
    test fails on that mutant while every other promotion test passes."""
    arc = Archive(tmp_path / "archive.jsonl")
    incumbent = _genome("g_00001", GATHERING_CELL, per_seed=[30.0, 50.0])  # mean 40, reliability 30
    arc.add(incumbent)

    challenger = _genome("g_00002", GATHERING_CELL, per_seed=[35.0, 35.0])  # mean 35, reliability 35
    assert challenger.fitness < incumbent.fitness  # raw mean is LOWER
    assert challenger.reliability > incumbent.reliability  # but steadier

    result = arc.add(challenger)

    assert result.status == "improved"
    assert arc.get_elite(GATHERING_CELL) is challenger


def test_genuinely_better_low_variance_genome_displaces_incumbent(tmp_path):
    arc = Archive(tmp_path / "archive.jsonl")
    steady = _genome("g_00001", GATHERING_CELL, per_seed=[37.0, 39.0, 41.0])  # reliability ~37.37
    arc.add(steady)

    better = _genome("g_00002", GATHERING_CELL, per_seed=[45.0, 46.0, 47.0])  # reliability ~45.18
    assert better.reliability > steady.reliability

    result = arc.add(better)

    assert result.status == "improved"
    assert result.entered_grid is True
    assert result.prev_fitness == pytest.approx(steady.fitness)
    assert arc.get_elite(GATHERING_CELL) is better


def test_lower_scoring_genome_does_not_displace_incumbent(tmp_path):
    arc = Archive(tmp_path / "archive.jsonl")
    steady = _genome("g_00001", GATHERING_CELL, per_seed=[37.0, 39.0, 41.0])  # reliability ~37.37
    arc.add(steady)

    lower = _genome("g_00002", GATHERING_CELL, per_seed=[20.0, 21.0, 22.0])  # reliability ~20.18
    assert lower.reliability < steady.reliability

    result = arc.add(lower)

    assert result.status == "rejected"
    assert arc.get_elite(GATHERING_CELL) is steady


# --- different cells coexist ---------------------------------------------------


def test_different_cells_coexist_independently(tmp_path):
    arc = Archive(tmp_path / "archive.jsonl")
    miner = _genome("g_00001", GATHERING_CELL, fitness=10.0)
    smith = _genome("g_00002", CRAFTING_CELL, fitness=5.0)

    r1 = arc.add(miner)
    r2 = arc.add(smith)

    assert r1.status == "filled"
    assert r2.status == "filled"
    assert arc.get_elite(GATHERING_CELL) is miner
    assert arc.get_elite(CRAFTING_CELL) is smith
    assert arc.filled_cells() == 2


# --- elites() / summary() -------------------------------------------------------


def test_elites_and_summary_report_the_grid_correctly(tmp_path):
    arc = Archive(tmp_path / "archive.jsonl")
    miner = _genome("g_00001", GATHERING_CELL, fitness=10.0)
    smith = _genome("g_00002", CRAFTING_CELL, fitness=25.0)
    arc.add(miner)
    arc.add(smith)
    # a rejected genome must still be persisted (full lineage) but not an elite
    also_miner = _genome("g_00003", GATHERING_CELL, per_seed=[1.0, 1.0])  # reliability 1.0, worse
    arc.add(also_miner)

    elites = {g.id for g in arc.elites()}
    assert elites == {"g_00001", "g_00002"}
    assert len(arc.all_genomes()) == 3  # full lineage, including the rejected one

    summ = arc.summary()
    assert summ["total_genomes"] == 3
    assert summ["filled_cells"] == 2
    assert summ["qd_score"] == pytest.approx(35.0)  # 10 + 25
    assert summ["best_fitness"] == pytest.approx(25.0)
    assert summ["cells"] == {
        cell_to_str(GATHERING_CELL): "g_00001",
        cell_to_str(CRAFTING_CELL): "g_00002",
    }


def test_summary_all_zero_when_archive_empty(tmp_path):
    arc = Archive(tmp_path / "archive.jsonl")
    summ = arc.summary()
    assert summ == {"total_genomes": 0, "filled_cells": 0, "qd_score": 0.0, "best_fitness": 0.0, "cells": {}}


def test_next_id_is_sequential(tmp_path):
    arc = Archive(tmp_path / "archive.jsonl")
    assert arc.next_id() == "g_00001"
    arc.add(_genome(arc.next_id(), GATHERING_CELL, fitness=1.0))
    assert arc.next_id() == "g_00002"


# --- archive.jsonl round-trip across two Archive instances --------------------


def test_archive_jsonl_round_trips_across_two_instances(tmp_path):
    """Persistence is real: a second, fresh `Archive` pointed at the same
    path reconstructs the identical grid/elites purely from the log."""
    path = tmp_path / "archive.jsonl"
    arc1 = Archive(path)
    steady = _genome("g_00001", GATHERING_CELL, per_seed=[37.0, 39.0, 41.0])
    better = _genome("g_00002", GATHERING_CELL, per_seed=[45.0, 46.0, 47.0])
    smith = _genome("g_00003", CRAFTING_CELL, fitness=8.0)
    arc1.add(steady)
    arc1.add(better)   # displaces steady
    arc1.add(smith)

    assert path.exists()
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 3  # full lineage, including the displaced genome

    arc2 = Archive(path)  # fresh instance, same file

    assert arc2.summary() == arc1.summary()
    assert {g.id for g in arc2.elites()} == {g.id for g in arc1.elites()}
    assert arc2.get_elite(GATHERING_CELL).id == "g_00002"  # the reliability winner, not g_00001
    assert arc2.get_elite(CRAFTING_CELL).id == "g_00003"
    assert len(arc2.all_genomes()) == 3


def test_reload_replays_promotion_rule_rather_than_last_line_wins(tmp_path):
    """The docstring claims reload REPLAYS every line through the promotion
    rule. Distinguish that from a naive last-line-wins reload: persist an
    elite, then a REJECTED (weaker) genome — which still lands in the file,
    full lineage — and reload. A replay keeps the true elite; a last-line-
    wins reload would crown the rejected genome."""
    path = tmp_path / "archive.jsonl"
    arc = Archive(path)
    elite = _genome("g_00001", GATHERING_CELL, per_seed=[40.0, 42.0, 44.0])
    arc.add(elite)
    weaker = _genome("g_00002", GATHERING_CELL, per_seed=[5.0, 6.0, 7.0])
    assert arc.add(weaker).status == "rejected"  # persisted (lineage) but not elite

    reloaded = Archive(path)
    assert len(reloaded.all_genomes()) == 2  # lineage survived
    assert reloaded.get_elite(GATHERING_CELL).id == "g_00001"  # replay, not last-line-wins


def test_archive_jsonl_round_trip_preserves_genome_config_fields(tmp_path):
    path = tmp_path / "archive.jsonl"
    arc1 = Archive(path)
    g = _genome(
        "g_00001", GATHERING_CELL, fitness=12.0,
        profession="miner", sociability=0.7, deliver_threshold=12.0, cognition_tier="standard",
        parent="g_00000", hypothesis="try a chattier miner", ts=1234.5,
    )
    arc1.add(g)

    arc2 = Archive(path)
    round_tripped = arc2.get("g_00001")

    assert round_tripped.profession == "miner"
    assert round_tripped.sociability == pytest.approx(0.7)
    assert round_tripped.deliver_threshold == pytest.approx(12.0)
    assert round_tripped.cognition_tier == "standard"
    assert round_tripped.parent == "g_00000"
    assert round_tripped.hypothesis == "try a chattier miner"
    assert round_tripped.ts == pytest.approx(1234.5)


def test_archive_jsonl_read_tolerates_corrupt_trailing_line(tmp_path):
    """Matches `skill_library.py::SkillLibrary._read_ledger`'s "skip a
    malformed line, never fatal" discipline — an interrupted write leaving a
    partial trailing line must not crash the whole archive load."""
    path = tmp_path / "archive.jsonl"
    arc1 = Archive(path)
    arc1.add(_genome("g_00001", GATHERING_CELL, fitness=10.0))
    # simulate a crash mid-write: append a garbage/partial line and a blank line
    with path.open("a", encoding="utf-8") as f:
        f.write('{"id": "g_00002", "eval": {"cell": ["GATHER\n')  # truncated JSON
        f.write("\n")
        f.write("not even json at all\n")

    arc2 = Archive(path)

    assert len(arc2.all_genomes()) == 1
    assert arc2.get("g_00001") is not None
    assert arc2.get_elite(GATHERING_CELL).id == "g_00001"


def test_missing_archive_file_starts_empty(tmp_path):
    arc = Archive(tmp_path / "does_not_exist.jsonl")
    assert arc.all_genomes() == []
    assert arc.elites() == []
    assert arc.summary()["total_genomes"] == 0


# --- negative control: degenerate genome never displaces a real worker elite --


def test_negative_control_degenerate_genome_lands_in_none_cell_never_displaces_worker(tmp_path):
    """A genome whose trajectory was all-zero (descriptor.py's own negative
    control: NONE profession, cell (NONE, 0)) must land in ITS OWN cell and
    never be able to touch a real worker's elite in a different cell — even
    when given a deliberately HIGH fitness, to prove it's cell separation
    (not a fitness comparison) doing the work."""
    arc = Archive(tmp_path / "archive.jsonl")
    worker = _genome("g_00001", GATHERING_CELL, per_seed=[37.0, 39.0, 41.0])  # a real miner elite
    arc.add(worker)

    degenerate = _genome(
        "g_00002", NONE_CELL, fitness=9999.0,  # deliberately huge — must still not touch GATHERING_CELL
        profession="none", sociability=0.0, deliver_threshold=0.0, cognition_tier="cheap",
    )
    result = arc.add(degenerate)

    assert result.status == "filled"  # NONE_CELL was empty
    assert arc.get_elite(NONE_CELL).id == "g_00002"
    assert arc.get_elite(GATHERING_CELL) is worker  # untouched
    assert arc.filled_cells() == 2


def test_negative_control_end_to_end_descriptor_derives_the_none_cell(tmp_path):
    """The review-flagged gap: exercise compute_descriptor + Archive.add
    TOGETHER. An all-zero TrajectorySummary is binned by the real descriptor
    (not a hand-built cell) and the resulting genome must land in the
    NONE-profession cell without touching a real worker's elite."""
    from anima2.foundry.descriptor import compute_descriptor
    from anima2.foundry.trajectory import TrajectorySummary

    zero = TrajectorySummary(start_ts=0.0, end_ts=3600.0, alive_start=True, alive_end=True)
    desc = compute_descriptor(zero)
    assert desc.profession_focus == uoconst.NONE  # derived, not asserted by hand

    arc = Archive(tmp_path / "archive.jsonl")
    worker = _genome("g_00001", GATHERING_CELL, per_seed=[37.0, 39.0, 41.0])
    arc.add(worker)

    idle = _genome("g_00002", desc.cell, fitness=9999.0,
                   profession="none", sociability=0.0, deliver_threshold=0.0, cognition_tier="cheap")
    result = arc.add(idle)

    assert result.status == "filled"
    assert arc.get_elite(desc.cell).id == "g_00002"
    assert arc.get_elite(GATHERING_CELL) is worker


def test_insert_result_is_a_plain_dataclass_shape():
    r = InsertResult(status="filled", cell=(uoconst.NONE, 0), fitness=1.0, prev_fitness=None)
    assert r.entered_grid is True
    r2 = InsertResult(status="rejected", cell=(uoconst.NONE, 0), fitness=1.0, prev_fitness=2.0)
    assert r2.entered_grid is False


def test_add_persists_raw_json_line_shape(tmp_path):
    """Sanity check on the on-disk shape: one JSON object per line, with the
    fields `Genome.from_dict` expects — not a nested/batched structure."""
    path = tmp_path / "archive.jsonl"
    arc = Archive(path)
    arc.add(_genome("g_00001", GATHERING_CELL, fitness=10.0, profession="miner"))

    line = path.read_text(encoding="utf-8").strip()
    obj = json.loads(line)
    assert obj["id"] == "g_00001"
    assert obj["profession"] == "miner"
    assert obj["eval"]["cell"] == list(GATHERING_CELL)
