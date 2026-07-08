"""Automatic curriculum: milestone catalog + cadence-gated picker (PHASE4.md item 5).

Offline, no live server — hand-built `SkillContext`/`Observation` fixtures,
same style as `test_reflection.py`'s own `ReflectingCognition` cadence tests
(this controller mirrors that class closely) and `test_skill_library.py`'s
`diagnose()`-style boundary/negative-control fixtures.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from anima2.contract import ItemView, Observation, PlayerView, Position, SkillView
from anima2.curriculum import MILESTONES, CurriculumController
from anima2.llm import StubLLMClient
from anima2.memory import Episode
from anima2.persona import Persona
from anima2.skills.base import Goal, SkillContext

MINING_SKILL_ID = 45
FISHING_SKILL_ID = 18
ORE_GRAPHIC = 0x19B7
INGOT_GRAPHIC = 0x1BEF
GOLD_GRAPHIC = 0x0EED
BACKPACK_LAYER = 0x15


class _PassThroughInner:
    """A trivial `Cognition` stub: returns whatever goal is already on `ctx`,
    recording every call — mirrors `test_reflection.py`'s use of
    `HeuristicCognition` as `ReflectingCognition`'s inner cognition."""

    def __init__(self) -> None:
        self.calls = 0

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        self.calls += 1
        return ctx.goal


def _bp(container: int = 1) -> ItemView:
    """`container=1` — the player's own serial (every fixture below stages
    `PlayerView(serial=1, ...)`), matching `_backpack()`'s own `i.container
    == obs.player.serial` lookup, the same convention `test_skill_library.
    py::_bp` already established."""
    return ItemView(serial=0x50, graphic=0x0E75, amount=1, pos=Position(), container=container,
                    layer=BACKPACK_LAYER, distance=0)


def _pack_item(graphic: int, amount: int, serial: int = 0x60, container: int = 0x50) -> ItemView:
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(), container=container,
                    layer=0, distance=0)


def _obs(*, skill_base: dict[int, float] | None = None, pack: list[tuple[int, int]] | None = None) -> Observation:
    """A fresh player at (0,0,0), a visible backpack, plus whatever skill
    bases / pack item (graphic, amount) pairs the caller asks for. With
    neither arg, this is the "freshly staged, nothing achieved yet" fixture."""
    items = [_bp()]
    for graphic, amount in pack or []:
        items.append(_pack_item(graphic, amount, serial=0x60 + graphic))
    skills = [SkillView(id=sid, value=base, base=base, cap=100.0, lock=0) for sid, base in (skill_base or {}).items()]
    return Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)), items=items, skills=skills)


def _ctx(*, obs: Observation | None = None, episodes=None, episode_count: int = 0, memory=None) -> SkillContext:
    return SkillContext(
        obs=obs if obs is not None else _obs(),
        persona=Persona(name="Grimm", title="a miner"),
        episodes=episodes or [], episode_count=episode_count, memory=memory if memory is not None else {},
    )


def _controller(*, client=None, profession: str = "miner", milestones_path: Path | None = None,
                every_n_reconsiders: int = 1, min_new_episodes: int = 1) -> CurriculumController:
    """`milestones_path` ALWAYS resolves to a fresh, isolated temp path when
    the caller doesn't pass one explicitly — never the real `data/
    milestones.jsonl` default `CurriculumController` itself falls back to.
    Mirrors `test_skill_library.py`'s own "tests must always pass an
    explicit ledger_path=" discipline; unlike that file's tests (which all
    take a `tmp_path` fixture directly), this helper self-isolates so most
    call sites below don't need to thread a `tmp_path` fixture through just
    for this. Tests that specifically exercise restart-survives / corrupted-
    file behavior still pass their own `tmp_path`-based `milestones_path=`.
    """
    if milestones_path is None:
        milestones_path = Path(tempfile.mkdtemp()) / "milestones.jsonl"
    return CurriculumController(
        _PassThroughInner(), client or StubLLMClient("(unused)"), "Grimm", profession,
        every_n_reconsiders=every_n_reconsiders, min_new_episodes=min_new_episodes,
        milestones_path=milestones_path,
    )


def _milestone(profession: str, name: str):
    return next(m for m in MILESTONES[profession] if m.name == name)


# --- Milestone predicates: boundary values --------------------------------------


def test_mining_50_boundary_not_achieved_at_49_9():
    m = _milestone("miner", "miner_mining_50")
    ctx = _ctx(obs=_obs(skill_base={MINING_SKILL_ID: 49.9}))
    assert m.is_achieved(ctx) is False
    assert m.progress(ctx) < 1.0


def test_mining_50_boundary_achieved_at_52():
    m = _milestone("miner", "miner_mining_50")
    ctx = _ctx(obs=_obs(skill_base={MINING_SKILL_ID: 52.0}))
    assert m.is_achieved(ctx) is True
    assert m.progress(ctx) == 1.0


def test_hold_20_ore_boundary():
    m = _milestone("miner", "miner_hold_20_ore")
    ctx19 = _ctx(obs=_obs(pack=[(ORE_GRAPHIC, 19)]))
    ctx20 = _ctx(obs=_obs(pack=[(ORE_GRAPHIC, 20)]))
    assert m.is_achieved(ctx19) is False
    assert m.is_achieved(ctx20) is True
    assert m.progress(ctx19) == 19 / 20


def test_bank_100_gold_boundary():
    m = _milestone("blacksmith", "blacksmith_bank_100_gold")
    box99 = ItemView(serial=0x70, graphic=GOLD_GRAPHIC, amount=99, pos=Position(), container=0x71, layer=0, distance=0)
    bankbox99 = ItemView(serial=0x71, graphic=0x0E76, amount=1, pos=Position(), container=1, layer=0x1D, distance=0)
    obs99 = Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)), items=[bankbox99, box99])
    ctx99 = _ctx(obs=obs99)
    assert m.is_achieved(ctx99) is False

    box100 = ItemView(serial=0x70, graphic=GOLD_GRAPHIC, amount=100, pos=Position(), container=0x71, layer=0, distance=0)
    obs100 = Observation(player=PlayerView(serial=1, pos=Position(0, 0, 0)), items=[bankbox99, box100])
    ctx100 = _ctx(obs=obs100)
    assert m.is_achieved(ctx100) is True


def test_hunter_loot_cycles_boundary():
    m = _milestone("hunter", "hunter_5_loot_cycles")
    ctx4 = _ctx(memory={"hunt_looted": [1, 2, 3, 4]})
    ctx5 = _ctx(memory={"hunt_looted": [1, 2, 3, 4, 5]})
    assert m.is_achieved(ctx4) is False
    assert m.is_achieved(ctx5) is True


def test_fisher_catch_5_recent_window():
    m = _milestone("fisher", "fisher_catch_5")
    episodes_low = [Episode(tick=i, kind="skill", summary="fish → running", reward=1.0) for i in range(4)]
    episodes_high = [Episode(tick=i, kind="skill", summary="fish → running", reward=1.0) for i in range(5)]
    assert m.is_achieved(_ctx(episodes=episodes_low)) is False
    assert m.is_achieved(_ctx(episodes=episodes_high)) is True


# --- Negative control: a fresh/idle fixture never spuriously progresses --------


def test_negative_control_every_milestone_floor_on_a_fresh_fixture():
    """A freshly staged character (no relevant episodes, no relevant items,
    no skill gain yet) must read every single catalog milestone as
    `is_achieved() is False` and `progress() == 0.0` — an idle/off-task
    agent must never spuriously read as having made progress. Exercises the
    WHOLE catalog, not just one profession's."""
    ctx = _ctx(obs=_obs(), episodes=[], episode_count=0, memory={})
    for profession, milestones in MILESTONES.items():
        for m in milestones:
            assert m.is_achieved(ctx) is False, f"{profession}/{m.name} spuriously achieved"
            assert m.progress(ctx) == 0.0, f"{profession}/{m.name} spuriously progressed"


# --- CurriculumController: cost discipline (0-1 eligible → zero LLM calls) -----


def test_zero_eligible_makes_zero_llm_calls():
    """Every fisher milestone already achieved → 0 eligible, `current_
    milestone` becomes `None`, and the LLM is never consulted."""
    client = StubLLMClient('{"milestone": "should never be read"}')
    obs = _obs(skill_base={FISHING_SKILL_ID: 60.0})
    episodes = [Episode(tick=i, kind="skill", summary="fish → running", reward=1.0) for i in range(5)]
    controller = _controller(client=client, profession="fisher")

    controller.reconsider(_ctx(obs=obs, episodes=episodes, episode_count=5))
    assert controller.wait_idle(timeout=2.0)

    assert client.calls == []
    assert controller.current_milestone is None


def test_one_eligible_makes_zero_llm_calls_and_picks_it():
    """Fishing already at 50 (achieved); the catch-5 milestone is the only
    one left → picked deterministically, zero LLM calls."""
    client = StubLLMClient('{"milestone": "should never be read"}')
    obs = _obs(skill_base={FISHING_SKILL_ID: 55.0})
    controller = _controller(client=client, profession="fisher")

    controller.reconsider(_ctx(obs=obs, episodes=[], episode_count=1))
    assert controller.wait_idle(timeout=2.0)

    assert client.calls == []
    assert controller.current_milestone == "fisher_catch_5"


# --- 2+ eligible: LLM picks one name off the shown list ------------------------


def test_two_plus_eligible_picks_the_valid_llm_choice():
    client = StubLLMClient('{"milestone": "miner_hold_20_ore"}')
    controller = _controller(client=client, profession="miner")

    controller.reconsider(_ctx(obs=_obs(), episodes=[], episode_count=1))
    assert controller.wait_idle(timeout=2.0)

    assert len(client.calls) == 1
    assert controller.current_milestone == "miner_hold_20_ore"
    # The prompt shows all 3 eligible names, none picked by the model excluded.
    system, user = client.calls[0]
    assert "miner_hold_20_ore" in user and "miner_mining_50" in user and "miner_hold_10_ingots" in user
    assert "milestone" in system.lower()


def test_garbage_llm_reply_name_not_in_list_falls_back_to_lowest_progress():
    """A hallucinated name not on the shown list → falls back to the
    deterministic 'lowest current progress()' heuristic. Rigged so the
    fallback target is unambiguous by construction (not by name tie-break):
    ore already at 15/20 (0.75 progress) leaves the other two milestones
    (both still at 0 progress) strictly lower."""
    client = StubLLMClient('{"milestone": "totally_made_up_milestone_name"}')
    obs = _obs(pack=[(ORE_GRAPHIC, 15)])
    controller = _controller(client=client, profession="miner")

    controller.reconsider(_ctx(obs=obs, episodes=[], episode_count=1))
    assert controller.wait_idle(timeout=2.0)

    assert len(client.calls) == 1  # the call was made, just rejected after parsing
    assert controller.current_milestone != "miner_hold_20_ore"
    assert controller.current_milestone in ("miner_mining_50", "miner_hold_10_ingots")


def test_garbage_llm_reply_bare_prose_falls_back_to_lowest_progress():
    client = StubLLMClient("sure thing, let's focus on mining today!")
    obs = _obs(pack=[(ORE_GRAPHIC, 15)])
    controller = _controller(client=client, profession="miner")

    controller.reconsider(_ctx(obs=obs, episodes=[], episode_count=1))
    assert controller.wait_idle(timeout=2.0)

    assert controller.current_milestone != "miner_hold_20_ore"
    assert controller.current_milestone in ("miner_mining_50", "miner_hold_10_ingots")


def test_garbage_llm_reply_malformed_json_falls_back_to_lowest_progress():
    client = StubLLMClient('{"milestone": "miner_hold_20_ore"')  # missing closing brace/quote
    obs = _obs(pack=[(ORE_GRAPHIC, 15)])
    controller = _controller(client=client, profession="miner")

    controller.reconsider(_ctx(obs=obs, episodes=[], episode_count=1))
    assert controller.wait_idle(timeout=2.0)

    assert controller.current_milestone != "miner_hold_20_ore"
    assert controller.current_milestone in ("miner_mining_50", "miner_hold_10_ingots")


# --- achieved-transition: exactly once, idempotent ------------------------------


def test_achieved_transition_records_exactly_one_episode(tmp_path):
    controller = _controller(profession="miner", milestones_path=tmp_path / "milestones.jsonl")
    obs = _obs(skill_base={MINING_SKILL_ID: 52.0})  # miner_mining_50 achieved

    controller.reconsider(_ctx(obs=obs, episodes=[], episode_count=1))
    assert controller.wait_idle(timeout=2.0)
    milestone_eps = controller.episodes.by_kind("milestone")
    assert len(milestone_eps) == 1
    assert milestone_eps[0].summary == "miner_mining_50 achieved"

    # A later tick where it's STILL achieved must not spam a second episode.
    controller.reconsider(_ctx(obs=obs, episodes=[], episode_count=2))
    assert controller.wait_idle(timeout=2.0)
    assert len(controller.episodes.by_kind("milestone")) == 1


def test_achieved_transition_appends_one_milestones_jsonl_line(tmp_path):
    path = tmp_path / "milestones.jsonl"
    controller = _controller(profession="miner", milestones_path=path)
    obs = _obs(skill_base={MINING_SKILL_ID: 52.0})

    controller.reconsider(_ctx(obs=obs, episodes=[], episode_count=1))
    assert controller.wait_idle(timeout=2.0)

    lines = path.read_text().splitlines()
    assert len(lines) == 1
    assert '"milestone": "miner_mining_50"' in lines[0]
    assert '"persona": "Grimm"' in lines[0]
    assert '"profession": "miner"' in lines[0]


# --- restart-survives: seeded from a milestones.jsonl fixture ------------------


def test_restart_survives_does_not_refire_an_already_recorded_milestone(tmp_path):
    path = tmp_path / "milestones.jsonl"
    path.write_text(
        '{"ts": "2020-01-01T00:00:00+00:00", "persona": "Grimm", "profession": "miner", '
        '"milestone": "miner_mining_50"}\n'
    )
    controller = _controller(profession="miner", milestones_path=path)
    obs = _obs(skill_base={MINING_SKILL_ID: 60.0})  # still (already) achieved

    controller.reconsider(_ctx(obs=obs, episodes=[], episode_count=1))
    assert controller.wait_idle(timeout=2.0)

    assert controller.episodes.by_kind("milestone") == []  # no re-fire
    assert len(path.read_text().splitlines()) == 1  # file untouched, not appended to again


def test_restart_survives_corrupted_trailing_line_skipped_not_fatal(tmp_path):
    path = tmp_path / "milestones.jsonl"
    path.write_text(
        '{"ts": "x", "persona": "Grimm", "profession": "miner", "milestone": "miner_mining_50"}\n'
        '{not valid json\n'
    )
    controller = _controller(profession="miner", milestones_path=path)  # must not raise
    obs = _obs(skill_base={MINING_SKILL_ID: 60.0})

    controller.reconsider(_ctx(obs=obs, episodes=[], episode_count=1))
    assert controller.wait_idle(timeout=2.0)
    assert controller.episodes.by_kind("milestone") == []  # the valid line still seeded it


def test_missing_milestones_file_degrades_to_empty_achieved_set(tmp_path):
    controller = _controller(profession="miner", milestones_path=tmp_path / "does" / "not" / "exist.jsonl")
    obs = _obs(skill_base={MINING_SKILL_ID: 60.0})

    controller.reconsider(_ctx(obs=obs, episodes=[], episode_count=1))
    assert controller.wait_idle(timeout=2.0)
    assert len(controller.episodes.by_kind("milestone")) == 1  # fires normally, nothing to seed from


# --- mid-transaction defer -------------------------------------------------------


def test_mid_transaction_defer_keeps_the_previous_pick(tmp_path):
    client = StubLLMClient('{"milestone": "miner_hold_20_ore"}')
    controller = _controller(client=client, profession="miner", milestones_path=tmp_path / "milestones.jsonl")

    # Round 1: not mid-transaction, 3 eligible → LLM picks miner_hold_20_ore.
    controller.reconsider(_ctx(obs=_obs(), episodes=[], episode_count=1, memory={}))
    assert controller.wait_idle(timeout=2.0)
    assert controller.current_milestone == "miner_hold_20_ore"
    assert len(client.calls) == 1

    # Round 2: mid-transaction (an in-progress MineSmeltDeliver delivery leg)
    # AND ore progress has moved well past the other two milestones — a fresh
    # pick (LLM or heuristic) would have every reason to switch away from
    # miner_hold_20_ore. The defer guard must keep it anyway, and must not
    # even ask the LLM again (cost discipline during a deferred round).
    mid_txn_obs = _obs(pack=[(ORE_GRAPHIC, 19)])
    controller.reconsider(_ctx(obs=mid_txn_obs, episodes=[], episode_count=2,
                                memory={"smelt_phase": "deliver"}))
    assert controller.wait_idle(timeout=2.0)

    assert controller.current_milestone == "miner_hold_20_ore"  # unchanged
    assert len(client.calls) == 1  # no second call — the round was deferred entirely


def test_mid_transaction_defer_still_records_achieved_transitions(tmp_path):
    """The defer guard only ever protects `current_milestone` from being
    reassigned — it must NOT suppress recording a genuine achievement, which
    is a ground-truth fact about the world, not "changing the pick"."""
    client = StubLLMClient('{"milestone": "miner_hold_20_ore"}')
    controller = _controller(client=client, profession="miner", milestones_path=tmp_path / "milestones.jsonl")

    controller.reconsider(_ctx(obs=_obs(), episodes=[], episode_count=1, memory={}))
    assert controller.wait_idle(timeout=2.0)
    assert controller.current_milestone == "miner_hold_20_ore"

    mid_txn_obs = _obs(skill_base={MINING_SKILL_ID: 52.0}, pack=[(ORE_GRAPHIC, 15)])
    controller.reconsider(_ctx(obs=mid_txn_obs, episodes=[], episode_count=2,
                                memory={"mkt_phase": "bank"}))
    assert controller.wait_idle(timeout=2.0)

    assert controller.current_milestone == "miner_hold_20_ore"  # still deferred
    milestone_eps = controller.episodes.by_kind("milestone")
    assert len(milestone_eps) == 1
    assert milestone_eps[0].summary == "miner_mining_50 achieved"  # fired despite the defer


def test_mid_transaction_never_defers_the_very_first_pick():
    """With no prior pick to preserve (`current_milestone is None`), a
    mid-transaction fixture must not permanently freeze the controller at
    `None` — the guard only protects an EXISTING pick."""
    client = StubLLMClient('{"milestone": "miner_hold_20_ore"}')
    controller = _controller(client=client, profession="miner")

    controller.reconsider(_ctx(obs=_obs(), episodes=[], episode_count=1,
                                memory={"smelt_phase": "deliver"}))
    assert controller.wait_idle(timeout=2.0)
    assert controller.current_milestone == "miner_hold_20_ore"


# --- ctx.memory wiring: additive/observational only -----------------------------


def test_curriculum_milestone_exposed_on_ctx_memory_every_tick():
    client = StubLLMClient('{"milestone": "miner_hold_20_ore"}')
    controller = _controller(client=client, profession="miner")
    ctx = _ctx(obs=_obs(), episodes=[], episode_count=1)

    controller.reconsider(ctx)
    assert controller.wait_idle(timeout=2.0)
    assert ctx.memory["curriculum_milestone"] is None  # not due to have completed *within this call*

    controller.reconsider(ctx)  # a later tick now sees the completed pick
    assert ctx.memory["curriculum_milestone"] == "miner_hold_20_ore"


def test_curriculum_controller_returns_inner_goal_unchanged_immediately():
    """Mirrors `test_reflecting_cognition_returns_goal_before_reflection_
    completes`: the picking pass must never sit on the goal-delivery path."""
    import threading

    gate = threading.Event()

    class SlowInner:
        def reconsider(self, ctx):
            return Goal(kind="idle")

    client = StubLLMClient('{"milestone": "miner_hold_20_ore"}')

    class GatedClient:
        def complete(self, system, user):
            gate.wait(2.0)
            return client.complete(system, user)

    controller = CurriculumController(SlowInner(), GatedClient(), "Grimm", "miner", every_n_reconsiders=1,
                                       milestones_path=Path(tempfile.mkdtemp()) / "milestones.jsonl")
    ctx = _ctx(obs=_obs(), episodes=[], episode_count=1)

    goal = controller.reconsider(ctx)
    assert goal == Goal(kind="idle")  # returned immediately — the LLM call is still gated shut
    assert controller.current_milestone is None  # the background pass hasn't completed yet

    gate.set()
    assert controller.wait_idle(timeout=2.0)
    assert controller.current_milestone == "miner_hold_20_ore"


def test_non_overlap_guard_skips_a_due_round_already_in_flight():
    import threading

    gate = threading.Event()
    started = threading.Event()
    calls = []

    class SlowClient:
        def complete(self, system, user):
            calls.append(1)
            started.set()
            gate.wait(2.0)
            return '{"milestone": "miner_hold_20_ore"}'

    controller = CurriculumController(_PassThroughInner(), SlowClient(), "Grimm", "miner", every_n_reconsiders=1,
                                       milestones_path=Path(tempfile.mkdtemp()) / "milestones.jsonl")

    controller.reconsider(_ctx(obs=_obs(), episodes=[], episode_count=1))
    assert started.wait(2.0)
    assert len(calls) == 1

    controller.reconsider(_ctx(obs=_obs(), episodes=[], episode_count=2))
    assert len(calls) == 1  # no concurrent second call while the first is in flight

    gate.set()
    assert controller.wait_idle(timeout=2.0)
    assert controller.current_milestone == "miner_hold_20_ore"
