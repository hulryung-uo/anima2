"""Skill library v0: registry, keyword retrieval, persisted outcome ledger,
and `Skill.diagnose()` (PHASE4.md item 3). Offline, no live server — hand-
built ledger fixtures and `SkillContext`s, same style as `test_wiki.py`'s
own ranking assertions.
"""

from __future__ import annotations

import json

import anima2.skills as skills_pkg
from anima2.contract import ItemView, MobileView, Observation, PlayerView, Position
from anima2.persona import Persona
from anima2.skill_library import REGISTRY, SkillLibrary, SkillStats
from anima2.skills import Blacksmith, Chop, Hunt, MineSmeltDeliver, Skill, Wander
from anima2.skills.base import SkillContext, Status
from anima2.skills.harvest import BACKPACK_LAYER

# --- registry ------------------------------------------------------------------


def test_registry_covers_every_exported_skill():
    """Fails loudly if `skills/__init__.py`'s `__all__` ever grows a `Skill`
    subclass this registry doesn't know about — the two can't silently drift.
    """
    exported_skill_classes = {
        getattr(skills_pkg, name)
        for name in skills_pkg.__all__
        if isinstance(getattr(skills_pkg, name), type)
        and issubclass(getattr(skills_pkg, name), Skill)
        and getattr(skills_pkg, name) is not Skill
    }
    registered = {entry.skill_cls for entry in REGISTRY}
    missing = exported_skill_classes - registered
    assert not missing, f"skills/__init__.py exports a skill the registry doesn't know: {missing}"
    extra = registered - exported_skill_classes
    assert not extra, f"registry references a skill not exported from skills/__init__.py: {extra}"


def test_registry_entries_use_the_skill_classes_own_name_and_description():
    for entry in REGISTRY:
        assert entry.name == entry.skill_cls.name
        assert entry.description == entry.skill_cls.description


# --- retrieval -------------------------------------------------------------------


def test_retrieve_ranks_mining_skills_above_unrelated_ones():
    lib = SkillLibrary()
    hits = lib.retrieve("mine ore", k=5)
    names = [e.name for e in hits]
    assert "mine" in names
    mine_rank = names.index("mine")
    for other in ("fish", "chop"):
        if other in names:
            assert names.index(other) > mine_rank


def test_retrieve_surfaces_hunt_first_for_a_hunting_query():
    lib = SkillLibrary()
    hits = lib.retrieve("hunt weak creatures", k=3)
    assert hits and hits[0].name == "hunt"
    assert hits[0].skill_cls is Hunt


def test_retrieve_returns_empty_for_blank_or_stopword_only_query():
    lib = SkillLibrary()
    assert lib.retrieve("") == []
    assert lib.retrieve("   ") == []
    assert lib.retrieve("the and of") == []


def test_retrieve_k_bounds_result_count():
    lib = SkillLibrary()
    hits = lib.retrieve("mine ore fish chop craft", k=2)
    assert len(hits) <= 2


def test_retrieve_no_match_returns_empty():
    lib = SkillLibrary()
    assert lib.retrieve("xyzzyplugh", k=3) == []


# --- outcome ledger --------------------------------------------------------------


def test_record_outcome_and_stats_round_trip(tmp_path):
    lib = SkillLibrary(ledger_path=tmp_path / "skill_ledger.jsonl")
    lib.record_outcome("hunt", "hunter", 5.0, Status.SUCCESS)
    lib.record_outcome("hunt", "hunter", 3.0, Status.SUCCESS)
    lib.record_outcome("hunt", "hunter", 0.0, Status.FAILURE)
    stats = lib.stats("hunt", "hunter")
    assert stats.count == 3
    assert stats.mean_reward == (5.0 + 3.0 + 0.0) / 3
    assert stats.success_rate == 2 / 3


def test_stats_for_unknown_key_is_all_zero(tmp_path):
    lib = SkillLibrary(ledger_path=tmp_path / "skill_ledger.jsonl")
    assert lib.stats("mine", "miner") == SkillStats()


def test_two_instances_on_the_same_ledger_see_each_others_writes(tmp_path):
    """The load-bearing claim of this whole item: persistence isn't a no-op.
    Instance A's writes land on disk immediately, so a *separate*, freshly
    constructed instance B — which never saw A's `record_outcome` calls in
    process memory — reads them back purely from the shared file.
    """
    ledger = tmp_path / "skill_ledger.jsonl"
    a = SkillLibrary(ledger_path=ledger)
    b = SkillLibrary(ledger_path=ledger)
    a.record_outcome("mine", "miner", 2.0, Status.SUCCESS)
    a.record_outcome("mine", "miner", 4.0, Status.SUCCESS)

    stats = b.stats("mine", "miner")
    assert stats.count == 2
    assert stats.mean_reward == 3.0

    # And the reverse direction: b's own write is visible to a *third*, fresh
    # instance reading afterward — not just a one-way a-then-b fluke.
    b.record_outcome("mine", "miner", 6.0, Status.SUCCESS)
    c = SkillLibrary(ledger_path=ledger)
    assert c.stats("mine", "miner").count == 3


def test_same_instance_stays_warm_across_further_writes(tmp_path):
    """The 'kept warm in memory' half of the persistence claim: calling
    `stats()` once doesn't freeze an instance's own view — later
    `record_outcome` calls on the *same* instance are reflected without
    needing to re-read the file."""
    ledger = tmp_path / "skill_ledger.jsonl"
    lib = SkillLibrary(ledger_path=ledger)
    lib.record_outcome("fish", "fisher", 1.0, Status.SUCCESS)
    assert lib.stats("fish", "fisher").count == 1
    lib.record_outcome("fish", "fisher", 1.0, Status.SUCCESS)
    assert lib.stats("fish", "fisher").count == 2


def test_corrupted_trailing_line_is_skipped_not_fatal(tmp_path):
    ledger = tmp_path / "skill_ledger.jsonl"
    lib = SkillLibrary(ledger_path=ledger)
    lib.record_outcome("chop", "lumberjack", 1.0, Status.SUCCESS)
    with ledger.open("a") as f:
        f.write("{not valid json\n")

    fresh = SkillLibrary(ledger_path=ledger)
    stats = fresh.stats("chop", "lumberjack")
    assert stats.count == 1  # the corrupted line was skipped, not fatal


def test_missing_ledger_file_degrades_to_empty_stats(tmp_path):
    lib = SkillLibrary(ledger_path=tmp_path / "does" / "not" / "exist.jsonl")
    assert lib.stats("mine", "miner") == SkillStats()


def test_record_outcome_creates_data_dir_lazily(tmp_path):
    nested = tmp_path / "nested" / "skill_ledger.jsonl"
    assert not nested.parent.exists()
    lib = SkillLibrary(ledger_path=nested)
    lib.record_outcome("mine", "miner", 1.0, Status.SUCCESS)
    assert nested.exists()


def test_record_outcome_carries_param_and_param_value(tmp_path):
    ledger = tmp_path / "skill_ledger.jsonl"
    lib = SkillLibrary(ledger_path=ledger)
    lib.record_outcome("mine_smelt_deliver", "miner", 4.0, Status.SUCCESS,
                       param="deliver_threshold", param_value=12)
    line = json.loads(ledger.read_text().splitlines()[0])
    assert line["param"] == "deliver_threshold"
    assert line["param_value"] == 12
    assert line["status"] == "SUCCESS"


def test_record_outcome_never_raises_on_unwritable_path(tmp_path):
    # A path whose parent can never be created (a file sitting where a
    # directory needs to go) — degrade silently, matching `wiki.py`'s
    # "never crash the caller" discipline.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    lib = SkillLibrary(ledger_path=blocker / "nested" / "skill_ledger.jsonl")
    lib.record_outcome("mine", "miner", 1.0, Status.SUCCESS)  # must not raise


# --- diagnose() ------------------------------------------------------------------


def _bp(serial=0x50, container=1):
    return ItemView(serial=serial, graphic=0x0E75, amount=1, pos=Position(),
                    container=container, layer=BACKPACK_LAYER, distance=0)


def test_default_diagnose_is_none_when_can_run_true():
    # Wander.can_run is always True (no override) — the ABC default fires.
    obs = Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)))
    ctx = SkillContext(obs=obs, persona=Persona(name="T"), memory={})
    assert Wander().can_run(ctx) is True
    assert Wander().diagnose(ctx) is None


def test_default_diagnose_gives_a_generic_reason_when_blocked():
    # Chop needs an axe in the pack (Harvest.can_run) — none here.
    obs = Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)))
    ctx = SkillContext(obs=obs, persona=Persona(name="T"), memory={})
    assert Chop().can_run(ctx) is False
    reason = Chop().diagnose(ctx)
    assert reason is not None and "chop" in reason


def test_blacksmith_diagnose_none_when_tool_and_metal_present():
    tool = ItemView(serial=0x40, graphic=0x13E3, amount=1, pos=Position(), container=0x99, layer=0, distance=0)
    ingots = ItemView(serial=0x41, graphic=0x1BF2, amount=50, pos=Position(), container=0x50, layer=0, distance=0)
    obs = Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)), items=[tool, _bp(), ingots])
    ctx = SkillContext(obs=obs, persona=Persona(name="T"), memory={})
    assert Blacksmith().diagnose(ctx) is None


def test_blacksmith_diagnose_no_tool():
    obs = Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)), items=[_bp()])
    ctx = SkillContext(obs=obs, persona=Persona(name="T"), memory={})
    assert Blacksmith().can_run(ctx) is False
    reason = Blacksmith().diagnose(ctx)
    assert reason is not None and "tool" in reason


def test_blacksmith_diagnose_starved_of_ingots_no_pile_in_range():
    """A known-blocked fixture: tool present (`can_run` is `True`) but out of
    metal with nothing in reach to fetch — a richer diagnosis than the
    binary `can_run` alone gives (see `Blacksmith.diagnose`'s own docstring
    for why this is layered on top of `can_run` rather than folded into it).
    """
    tool = ItemView(serial=0x40, graphic=0x13E3, amount=1, pos=Position(), container=0x99, layer=0, distance=0)
    obs = Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)), items=[tool, _bp()])
    ctx = SkillContext(obs=obs, persona=Persona(name="T"), memory={})
    assert Blacksmith().can_run(ctx) is True  # tool present
    assert Blacksmith().diagnose(ctx) == "starved of ingots, no pile in range"


def test_hunt_diagnose_pacifist():
    obs = Observation(player=PlayerView(serial=1, pos=Position(100, 100, 0)))
    ctx = SkillContext(obs=obs, persona=Persona(name="T", combat_disposition="pacifist"), memory={})
    assert Hunt().diagnose(ctx) == "pacifist — will not engage"


def test_hunt_diagnose_empty_queue_no_target():
    obs = Observation(player=PlayerView(serial=1, pos=Position(100, 100, 0)))
    ctx = SkillContext(obs=obs, persona=Persona(name="T", combat_disposition="aggressive"), memory={})
    assert Hunt().can_run(ctx) is False
    reason = Hunt().diagnose(ctx)
    assert reason is not None and "loot" in reason


def test_hunt_diagnose_none_with_a_target_in_range():
    mob = MobileView(serial=0xAA, name="a mongbat", pos=Position(101, 100, 0), body=39,
                     notoriety=3, hits=5, hits_max=5, distance=1)
    obs = Observation(player=PlayerView(serial=1, pos=Position(100, 100, 0)), mobiles=[mob])
    ctx = SkillContext(obs=obs, persona=Persona(name="T", combat_disposition="aggressive"), memory={})
    assert Hunt().can_run(ctx) is True
    assert Hunt().diagnose(ctx) is None


def test_mine_smelt_deliver_diagnose_none_when_runnable_and_unblocked():
    pickaxe = ItemView(serial=0x30, graphic=0x0E86, amount=1, pos=Position(), container=0x50, layer=0, distance=0)
    obs = Observation(player=PlayerView(serial=1, pos=Position(100, 100, 0)), items=[pickaxe, _bp()])
    ctx = SkillContext(obs=obs, persona=Persona(name="T"), memory={})
    assert MineSmeltDeliver().can_run(ctx) is True
    assert MineSmeltDeliver().diagnose(ctx) is None


def test_mine_smelt_deliver_diagnose_no_pickaxe_no_backpack():
    obs = Observation(player=PlayerView(serial=1, pos=Position(100, 100, 0)))
    ctx = SkillContext(obs=obs, persona=Persona(name="T"), memory={})
    assert MineSmeltDeliver().can_run(ctx) is False
    reason = MineSmeltDeliver().diagnose(ctx)
    assert reason is not None and "mine" in reason


def test_mine_smelt_deliver_diagnose_blocked_delivery():
    obs = Observation(player=PlayerView(serial=1, pos=Position(100, 100, 0)), items=[_bp()])
    ctx = SkillContext(obs=obs, persona=Persona(name="T"),
                       memory={"smithy_drop": (200, 200), "deliver_giveup_ingots": 10})
    assert MineSmeltDeliver().can_run(ctx) is True
    reason = MineSmeltDeliver().diagnose(ctx)
    assert reason is not None and "delivery" in reason
