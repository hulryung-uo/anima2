"""Automatic curriculum — a hand-written milestone catalog + a cadence-gated picker.

PHASE4.md item 5 (DESIGN.md §6): Voyager's spirit (difficulty ratchets, tasks are
proposed) without Voyager's free-form task/skill invention, which this codebase
has no safety infrastructure for. Every milestone's completion predicate is
Observation/EpisodicMemory-derived (never an agent's own self-report) so it can
never be gamed — the Foundry kernel's "independently observable fitness"
principle (`../anima/foundry/kernel/fitness.py`) applied without needing any of
that kernel's wire-level trajectory-capture machinery, since anima2's own
Observation contract already carries the needed signals (skill values, pack
contents, bank contents).

Two halves, deliberately separated:

- **`Milestone`/`MILESTONES`** — pure data, mirroring v1 `../anima/anima/
  planner/modes.py::Mode`'s zero-import discipline: no anima2-internal imports
  beyond `contract`/`skills.base`. `is_achieved`/`progress` mirror v1 `../anima/
  anima/planner/goals.py::Goal`'s `is_satisfied_fn`/`progress_fn` shape exactly
  (a predicate + a `[0, 1]`-clamped progress float, each independently callable
  against a `SkillContext`).
- **`CurriculumController`** — cadence-gated exactly like `cognition.
  ReflectingCognition` (counts reconsiders, runs its own daemon thread, a
  non-overlap guard, broad `except Exception`, never blocks goal delivery). 0-1
  eligible-and-unachieved milestones for the agent's profession → picked
  deterministically, **zero LLM calls**; 2+ eligible → the LLM picks **one name
  off the shown list** (never free-form), reusing `../anima/anima/planner/
  strategy.py::StrategySelector._is_strategy_viable`'s pattern: the LLM's pick
  is checked against the shown list (ground truth already computed), and any
  parse failure or a hallucinated non-list name falls back to the deterministic
  "lowest current `progress()`" heuristic.

This landing is **additive/observational only**: the chosen milestone is
exposed as `ctx.memory["curriculum_milestone"]` for reflection/forum prompts
and future items to read — no new `Goal` kind, no planner change, nothing
reads it to drive behavior yet. An achieved-transition (not-achieved →
achieved, exactly once) records one `Episode(kind="milestone", ...)` into the
agent's own `EpisodicMemory` and appends one line to `data/milestones.jsonl`
(gitignored, mirrors `skill_library.py`'s ledger convention) — read at
construction time to seed the already-achieved set, so a process restart
doesn't lose curriculum progress or re-fire an already-recorded milestone.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .cognition import _parse_json  # module-private, reused not duplicated — mirrors
# skill_tuning.py reusing skill_library.py's _read_ledger the same way.
from .contract import Observation
from .llm import LLMClient
from .memory import Episode, EpisodicMemory
from .skills.base import Goal, SkillContext

# ============================================================================
# Pure data: `Milestone` + `MILESTONES`. Zero anima2-internal imports beyond
# `contract`/`skills.base` from here down to `_mid_transaction` — mirrors v1
# `modes.py::Mode`'s "loads anywhere" discipline (item 3's own citation of the
# same file, for the same reason). Graphic/skill-id constants below are
# duplicated from the relevant `skills/*.py` module rather than imported —
# this codebase's own established convention (`skills/hunt.py` duplicates
# `GOLD_GRAPHIC` instead of importing `skills/market.py`'s identical one) —
# so this section never has to reach into a `Skill` subclass's own module.
# ============================================================================


@dataclass(frozen=True)
class Milestone:
    """One hand-written curriculum milestone: a name, a human-readable
    description, the profession it applies to, and two predicates —
    `is_achieved`/`progress`, mirroring v1 `goals.py::Goal`'s
    `is_satisfied_fn`/`progress_fn` shape exactly. Both take a `SkillContext`
    (the same object `Skill.step`/`can_run`/`diagnose` already receive) and
    must be pure, side-effect-free reads of `ctx.obs`/`ctx.episodes`/
    `ctx.memory` — never an LLM call, never a write. `progress` should return
    a value in `[0.0, 1.0]`; `CurriculumController` clamps defensively either
    way (mirrors `Goal.progress`'s own clamp).
    """

    name: str
    description: str
    profession: str
    is_achieved: Callable[[SkillContext], bool]
    progress: Callable[[SkillContext], float]


# --- shared obs-scanning helpers -------------------------------------------

_BACKPACK_LAYER = 0x15  # Server/Item.cs Layer.Backpack — matches skills/harvest.py::BACKPACK_LAYER
_BANKBOX_LAYER = 0x1D  # Server/Item.cs Layer.Bank — matches skills/market.py::BANKBOX_LAYER

_MINING_SKILL_ID = 45  # matches skills/harvest.py::SKILL_MINING
_FISHING_SKILL_ID = 18  # matches skills/harvest.py::SKILL_FISHING
_LUMBERJACKING_SKILL_ID = 44  # matches skills/harvest.py::SKILL_LUMBERJACKING
_BLACKSMITHING_SKILL_ID = 7  # matches skills/craft.py::SKILL_BLACKSMITHING

# ServUO ore/ingot art ids (Scripts/Items/Resource/Ore.cs) — matches
# skills/smelt.py::ORE_GRAPHICS/INGOT_GRAPHICS.
_ORE_GRAPHICS = frozenset({0x19B7, 0x19B8, 0x19B9, 0x19BA})
_INGOT_GRAPHICS = frozenset({0x1BEF, 0x1BF0, 0x1BF1, 0x1BF2})
_GOLD_GRAPHIC = 0x0EED  # Scripts/Items/Consumables/Gold.cs — matches skills/hunt.py::GOLD_GRAPHIC
_DAGGER_GRAPHIC = 0x0F52  # matches skills/craft.py::DAGGER_GRAPHIC


def _backpack(obs: Observation) -> Any:
    return next((i for i in obs.items if i.layer == _BACKPACK_LAYER and i.container == obs.player.serial), None)


def _bankbox(obs: Observation) -> Any:
    return next((i for i in obs.items if i.layer == _BANKBOX_LAYER and i.container == obs.player.serial), None)


def _pack_amount(obs: Observation, graphics: frozenset[int]) -> int:
    bp = _backpack(obs)
    if bp is None:
        return 0
    return sum(i.amount for i in obs.items if i.graphic in graphics and i.container == bp.serial)


def _bankbox_amount(obs: Observation, graphics: frozenset[int]) -> int:
    box = _bankbox(obs)
    if box is None:
        return 0
    return sum(i.amount for i in obs.items if i.graphic in graphics and i.container == box.serial)


def _skill_base(obs: Observation, skill_id: int) -> float | None:
    s = next((s for s in obs.skills if s.id == skill_id), None)
    return s.base if s is not None else None


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _skill_threshold(skill_id: int, threshold: float) -> tuple[Callable[[SkillContext], bool], Callable[[SkillContext], float]]:
    """A "reach skill base >= threshold" predicate pair — the simplest,
    least-gameable signal available (`obs.skills`, a direct server value):
    `Mining=49.9` not achieved, `Mining=52.0` achieved (exact boundary this
    item's own offline tests exercise). `None` (no skill entry at all — a
    freshly staged character before its first `[Set Skills...` lands, or a
    profession that was never staged with this skill) reads as `0.0`
    progress / not achieved — the negative-control floor."""

    def is_achieved(ctx: SkillContext) -> bool:
        base = _skill_base(ctx.obs, skill_id)
        return base is not None and base >= threshold

    def progress(ctx: SkillContext) -> float:
        base = _skill_base(ctx.obs, skill_id)
        return 0.0 if base is None else _clamp(base / threshold)

    return is_achieved, progress


def _pack_threshold(graphics: frozenset[int], threshold: int) -> tuple[Callable[[SkillContext], bool], Callable[[SkillContext], float]]:
    """A "hold >= threshold of these graphics in the backpack at once"
    predicate pair — an instantaneous, obs-derived proxy for sustained
    gathering/crafting output that needs no per-skill delivery/sale
    attribution (see the module docstring's `MILESTONES` comment on why this
    shape was chosen over a literal "N delivered" count for skills that
    compose several reward-bearing phases under one skill name)."""

    def is_achieved(ctx: SkillContext) -> bool:
        return _pack_amount(ctx.obs, graphics) >= threshold

    def progress(ctx: SkillContext) -> float:
        return _clamp(_pack_amount(ctx.obs, graphics) / threshold)

    return is_achieved, progress


def _bankbox_threshold(graphics: frozenset[int], threshold: int) -> tuple[Callable[[SkillContext], bool], Callable[[SkillContext], float]]:
    """Same shape as `_pack_threshold`, but reading the bank box's own
    container contents (`_bankbox_amount`) — the spec's own "bank 100 gold"
    example: obs-derived, unambiguous, and immune to a pack count that's
    merely passing through on its way to the vendor."""

    def is_achieved(ctx: SkillContext) -> bool:
        return _bankbox_amount(ctx.obs, graphics) >= threshold

    def progress(ctx: SkillContext) -> float:
        return _clamp(_bankbox_amount(ctx.obs, graphics) / threshold)

    return is_achieved, progress


def _episode_reward_threshold(skill_name_prefix: str, threshold: float) -> tuple[Callable[[SkillContext], bool], Callable[[SkillContext], float]]:
    """A "accumulate >= threshold reward from this skill in the recent
    episode window" predicate pair — `EpisodicMemory`-derived. Only used for
    single-purpose work skills whose *every* reward-bearing episode already
    means the one thing being measured (e.g. `Fish`'s only reward source is a
    catch, `Chop`'s only reward source is Lumberjacking skill gain) — a
    skill like `MineSmeltDeliver`/`BlacksmithMarket`, which folds several
    distinct reward-bearing phases (mine + smelt + deliver, or craft + sell +
    bank) under one skill name, would make the "which phase produced this
    reward" question ambiguous from `Episode.summary` alone, so those
    professions' second/third milestones use `_pack_threshold`/
    `_bankbox_threshold` instead (instantaneous, unambiguous obs state).
    Deliberately window-bounded (`ctx.episodes`, not a lifetime total) — the
    same bounded-recency `ReflectingCognition` itself already treats as "the
    current session" for reflection purposes."""

    def _sum(ctx: SkillContext) -> float:
        return sum(
            e.reward for e in ctx.episodes
            if getattr(e, "kind", None) == "skill" and str(getattr(e, "summary", "")).startswith(skill_name_prefix)
        )

    def is_achieved(ctx: SkillContext) -> bool:
        return _sum(ctx) >= threshold

    def progress(ctx: SkillContext) -> float:
        return _clamp(_sum(ctx) / threshold)

    return is_achieved, progress


def _memory_list_len_threshold(key: str, threshold: int) -> tuple[Callable[[SkillContext], bool], Callable[[SkillContext], float]]:
    """A "this skill's own `ctx.memory` bookkeeping list has grown to >=
    threshold entries" predicate pair — e.g. `Hunt`'s own `hunt_looted` (the
    permanently-retired, fully-looted corpse serials it tracks), mirroring
    `live_hunt.py`'s own `MIN_LOOT_CYCLES` gate, "now expressed as a
    milestone" per this item's own spec. `ctx.memory` is a legitimate
    ground-truth source here, on the same footing as `ctx.obs`/`ctx.episodes`
    — it's code-computed bookkeeping a hand-written skill's `step()`
    deterministically derives from confirmed Observation deltas, never an
    agent/LLM self-report, so it satisfies the same "can't be gamed"
    property `SkillContext.obs`/`.episodes` do."""

    def _len(ctx: SkillContext) -> int:
        return len(ctx.memory.get(key) or ())

    def is_achieved(ctx: SkillContext) -> bool:
        return _len(ctx) >= threshold

    def progress(ctx: SkillContext) -> float:
        return _clamp(_len(ctx) / threshold)

    return is_achieved, progress


#: The catalog: 2-3 entries per existing profession that has a work skill
#: (`profession.py::PROFESSIONS` — `townsfolk` has none and is deliberately
#: absent here; a profession key with no entry here simply never has any
#: eligible milestone, which `CurriculumController` treats the same as "0
#: eligible" — zero LLM calls, `curriculum_milestone` stays `None`).
MILESTONES: dict[str, list[Milestone]] = {
    "miner": [
        Milestone("miner_mining_50", "Reach Mining skill 50.", "miner",
                  *_skill_threshold(_MINING_SKILL_ID, 50.0)),
        Milestone("miner_hold_20_ore", "Hold 20 raw ore in the pack at once.", "miner",
                  *_pack_threshold(_ORE_GRAPHICS, 20)),
        Milestone("miner_hold_10_ingots", "Hold 10 smelted ingots in the pack at once.", "miner",
                  *_pack_threshold(_INGOT_GRAPHICS, 10)),
    ],
    "fisher": [
        Milestone("fisher_fishing_50", "Reach Fishing skill 50.", "fisher",
                  *_skill_threshold(_FISHING_SKILL_ID, 50.0)),
        Milestone("fisher_catch_5", "Catch 5 fish in a recent session window.", "fisher",
                  *_episode_reward_threshold("fish", 5.0)),
    ],
    "blacksmith": [
        Milestone("blacksmith_blacksmithing_50", "Reach Blacksmith skill 50.", "blacksmith",
                  *_skill_threshold(_BLACKSMITHING_SKILL_ID, 50.0)),
        Milestone("blacksmith_bank_100_gold", "Bank 100 gold.", "blacksmith",
                  *_bankbox_threshold(frozenset({_GOLD_GRAPHIC}), 100)),
        Milestone("blacksmith_hold_10_daggers", "Hold 10 crafted daggers in the pack at once.", "blacksmith",
                  *_pack_threshold(frozenset({_DAGGER_GRAPHIC}), 10)),
    ],
    "lumberjack": [
        Milestone("lumberjack_lumberjacking_50", "Reach Lumberjacking skill 50.", "lumberjack",
                  *_skill_threshold(_LUMBERJACKING_SKILL_ID, 50.0)),
        Milestone("lumberjack_earn_5_recent", "Earn 5.0 cumulative Lumberjacking reward in a recent session window.",
                  "lumberjack", *_episode_reward_threshold("chop", 5.0)),
    ],
    "hunter": [
        Milestone("hunter_5_loot_cycles", "Complete 5 kill -> corpse -> loot cycles.", "hunter",
                  *_memory_list_len_threshold("hunt_looted", 5)),
        Milestone("hunter_hold_50_gold", "Hold 50 looted gold in the pack at once.", "hunter",
                  *_pack_threshold(frozenset({_GOLD_GRAPHIC}), 50)),
    ],
}


def _mid_transaction(memory: dict[str, Any]) -> bool:
    """True while `memory` shows the agent mid a multi-phase skill
    transaction — ported in spirit from v1 `strategy.py::StrategySelector.
    _do_refresh`'s own "never switch strategy mid-batch" check (there:
    suppressing a refresh during an expedition's COLLECTING/CRAFTING_TRIP
    phase; here: generalized to every composed multi-phase work skill this
    codebase has). Checked against the exact phase-key/value pairs each
    skill's own `step()` already maintains in `ctx.memory` — no import of
    `skills/smelt.py`/`market.py`/`hunt.py` needed, just their known string
    keys, the same duplication-over-cross-import convention this module
    already uses for graphic constants."""
    if memory.get("smelt_phase") in ("deliver", "return"):
        return True
    if memory.get("mkt_phase") in ("sell", "sell_return", "bank", "bank_return"):
        return True
    if memory.get("hunt_phase") == "loot":
        return True
    return False


# ============================================================================
# CurriculumController — cadence-gated exactly like `cognition.
# ReflectingCognition`. Everything below this line is free to import whatever
# it needs (llm.py, memory.py, cognition.py's private JSON parser) — the
# "pure data" discipline above only ever applied to `Milestone`/`MILESTONES`.
# ============================================================================

#: `data/milestones.jsonl` relative to the process's cwd — mirrors `skill_
#: library.py`'s `_DEFAULT_LEDGER`/`llm.py`'s `_DEFAULT_USAGE_LOG` convention
#: exactly (created lazily, gitignored — see `.gitignore`'s own comment
#: already anticipating this file). Tests must always pass an explicit
#: `milestones_path=` (a `tmp_path`) so the suite never touches the real file.
_DEFAULT_MILESTONES_LOG = Path("data") / "milestones.jsonl"

#: Guards concurrent appends to a single milestones-log file across threads —
#: mirrors `skill_library.py::_ledger_lock`/`llm.py::_usage_log_lock` (several
#: agents' `CurriculumController`s could fire an achieved-transition around
#: the same wall-clock moment in a multi-agent `village.py` roster).
_milestones_log_lock = threading.Lock()


class CurriculumController:
    """Wraps a `Cognition`; periodically evaluates the milestone catalog for
    the agent's profession, exposing the current pick as `ctx.memory
    ["curriculum_milestone"]` (additive/observational only this landing — see
    the module docstring). Mirrors `cognition.ReflectingCognition` closely:
    cadence lives in the slow loop only (`reconsider()` returns `inner.
    reconsider(ctx)`'s goal immediately; a due evaluation pass is handed to
    its own daemon thread), a non-overlap guard (`_picking`) means at most
    one pass runs at a time, and a pass failure (bad predicate, flaky LLM)
    is caught the same way `ReflectingCognition._reflect_bg` guards its own
    body — it can never wedge the guard or kill cognition.

    The LLM's role is the smallest possible: 0-1 eligible-and-unachieved
    milestones for this profession → picked deterministically, **zero LLM
    calls**; 2+ eligible → the tiered `"curriculum_pick"`-role client (`llm.
    ROLE_TIER`, wired by the caller — see `village.py`/`live_trade.py`) picks
    **one name off the shown list**, reused v1 `strategy.py::
    StrategySelector._is_strategy_viable`'s pattern: the LLM's pick is
    checked against the shown list (ground truth already computed) and any
    parse failure or hallucinated non-list name falls back to the
    deterministic "lowest current `progress()`" heuristic (explore-what's-
    furthest-behind) — so, like `HeuristicCognition`/`HeuristicReflection`
    before it, this has meaningful behavior with zero LLM calls, and the LLM
    is a thin, occasionally-consulted refinement, never the only working path.
    """

    def __init__(
        self,
        inner: Any,
        client: LLMClient,
        persona_name: str,
        profession: str,
        *,
        every_n_reconsiders: int = 5,
        min_new_episodes: int = 6,
        episodes: EpisodicMemory | None = None,
        milestones_path: str | Path | None = None,
    ) -> None:
        self.inner = inner
        self.client = client
        self.persona_name = persona_name
        self.profession = profession
        self.every_n_reconsiders = every_n_reconsiders
        self.min_new_episodes = min_new_episodes
        #: Where achieved-transition episodes land. `None` (the default)
        #: creates a standalone `EpisodicMemory` — fine for offline tests
        #: that never construct a real `Agent`. A live caller wiring this
        #: into a real `Agent` must pass (and then rebind `agent.episodes`
        #: to) the SAME instance — see `village.py`/`live_trade.py`'s own
        #: wiring comments for why: `Agent.__init__` builds its own
        #: `EpisodicMemory` internally with no override constructor arg, so
        #: the caller reassigns the public `agent.episodes` attribute right
        #: after construction, before the first tick — safe, since nothing
        #: reads/writes it before then.
        self.episodes = episodes if episodes is not None else EpisodicMemory()
        self.milestones_path = Path(milestones_path) if milestones_path is not None else _DEFAULT_MILESTONES_LOG
        self._reconsiders_since = 0
        self._episode_count_at_last = 0
        # Non-overlap guard for the background evaluation thread, plus a
        # test-observable idle signal — mirrors `ReflectingCognition`'s
        # `_reflecting`/`_reflect_lock`/`_idle` exactly.
        self._lock = threading.Lock()
        self._picking = False
        self._idle = threading.Event()
        self._idle.set()
        #: The current pick's name, or `None` (nothing eligible, or no pass
        #: has completed yet). Read synchronously into `ctx.memory` every
        #: `reconsider()` call — see that method.
        self.current_milestone: str | None = None
        #: Restart-survives ratchet: `(persona, profession, milestone)`
        #: triples already recorded as achieved, seeded from
        #: `milestones_path` at construction time (mirrors `skill_library.py`
        #: /`skill_tuning.py`'s own "read the ledger at construction" idiom).
        self._achieved: set[tuple[str, str, str]] = self._load_achieved()

    # -- Cognition protocol -----------------------------------------------------

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        goal = self.inner.reconsider(ctx)
        # Keep the exposed pick current every tick, not just on a due round —
        # mirrors `ReflectingCognition` reading `self.insights.recent(3)` into
        # `ctx.insights` on every call regardless of whether reflection fires.
        ctx.memory["curriculum_milestone"] = self.current_milestone

        self._reconsiders_since += 1
        new_episodes = ctx.episode_count - self._episode_count_at_last
        due = new_episodes >= 1 and (
            self._reconsiders_since >= self.every_n_reconsiders
            or new_episodes >= self.min_new_episodes
        )
        if due:
            self._start_pick(ctx)
        return goal

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until any in-flight background evaluation pass has
        finished. For tests — a deterministic join point instead of a
        sleep/poll loop (mirrors `ReflectingCognition.wait_idle`)."""
        return self._idle.wait(timeout)

    # -- background pass ----------------------------------------------------------

    def _start_pick(self, ctx: SkillContext) -> None:
        with self._lock:
            if self._picking:
                return
            self._picking = True
            self._idle.clear()
        # Snapshot what the background thread needs as a fresh `SkillContext`
        # over plain, safe-to-share data (a dict copy of `ctx.memory`, a list
        # copy of `ctx.episodes`) — mirrors `ReflectingCognition._start_
        # reflection`'s own snapshot discipline: `ctx` itself is reused/
        # mutated by the fast loop on later ticks, so the thread must not
        # touch it directly. `ctx.obs`/`ctx.persona` are already fresh,
        # per-tick-constructed objects nothing later mutates in place, so
        # handing those off directly (not copying) is safe, the same way
        # `_reflect_bg` hands off `ctx.persona` as-is.
        snap = SkillContext(
            obs=ctx.obs, persona=ctx.persona, goal=ctx.goal,
            memory=dict(ctx.memory), episodes=list(ctx.episodes),
            episode_count=ctx.episode_count, insights=list(ctx.insights),
        )
        reconsiders, count_at_last = self._reconsiders_since, self._episode_count_at_last
        self._reconsiders_since = 0
        self._episode_count_at_last = ctx.episode_count
        try:
            threading.Thread(target=self._pick_bg, args=(snap,), daemon=True).start()
        except RuntimeError:  # spawn failed: _pick_bg's finally will never run —
            # release the guard here and restore the counters so the round stays due.
            self._reconsiders_since, self._episode_count_at_last = reconsiders, count_at_last
            with self._lock:
                self._picking = False
                self._idle.set()

    def _pick_bg(self, snap: SkillContext) -> None:
        """Runs on its own daemon thread, off the goal-delivery path entirely."""
        try:
            self._run(snap)
        except Exception:  # noqa: BLE001 — a flaky predicate/LLM must not wedge or kill cognition
            pass
        finally:
            with self._lock:
                self._picking = False
                self._idle.set()

    def _run(self, snap: SkillContext) -> None:
        milestones = MILESTONES.get(self.profession, [])
        if not milestones:
            return

        # Achieved-transition check first, for EVERY milestone regardless of
        # eligibility/defer state below: this is a ground-truth fact about
        # the world, not "changing the pick" — the mid-transaction defer
        # guard (below) only ever protects `current_milestone` from being
        # reassigned, never suppresses recording a genuine achievement.
        for m in milestones:
            key = (self.persona_name, self.profession, m.name)
            if key in self._achieved:
                continue  # idempotent: already recorded (this run or a prior one)
            if self._safe_achieved(m, snap):
                self._achieved.add(key)
                self._record_achieved(snap, m)

        eligible = [m for m in milestones if (self.persona_name, self.profession, m.name) not in self._achieved]

        if _mid_transaction(snap.memory) and self.current_milestone is not None:
            return  # defer: keep whatever was picked before (see module docstring)

        if not eligible:
            self.current_milestone = None
        elif len(eligible) == 1:
            self.current_milestone = eligible[0].name
        else:
            self.current_milestone = self._pick_name(eligible, snap)

    # -- picking --------------------------------------------------------------------

    def _pick_name(self, eligible: list[Milestone], snap: SkillContext) -> str:
        try:
            raw = self.client.complete(self._system(), self._situation(eligible, snap))
        except Exception:  # noqa: BLE001 — a flaky LLM must not break curriculum picking
            return self._lowest_progress_name(eligible, snap)
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        data = _parse_json(raw)
        picked = data.get("milestone") if isinstance(data, dict) else None
        names = {m.name for m in eligible}
        if isinstance(picked, str) and picked in names:
            return picked
        # Malformed JSON, bare prose, or a hallucinated/non-list name — all
        # three fall back to the same deterministic heuristic (never a crash,
        # never a bogus milestone switch): strategy.py::_is_strategy_viable's
        # "the LLM proposes, code validates against ground truth it already
        # computed" pattern, applied here.
        return self._lowest_progress_name(eligible, snap)

    def _lowest_progress_name(self, eligible: list[Milestone], snap: SkillContext) -> str:
        """Explore-what's-furthest-behind: the eligible milestone with the
        lowest current `progress()` (ties broken by name for determinism)."""
        return min(eligible, key=lambda m: (self._safe_progress(m, snap), m.name)).name

    @staticmethod
    def _safe_achieved(m: Milestone, snap: SkillContext) -> bool:
        try:
            return bool(m.is_achieved(snap))
        except Exception:  # noqa: BLE001 — mirrors v1 Goal.is_satisfied's own guard
            return False

    @staticmethod
    def _safe_progress(m: Milestone, snap: SkillContext) -> float:
        try:
            return _clamp(float(m.progress(snap)))
        except Exception:  # noqa: BLE001 — mirrors v1 Goal.progress's own guard
            return 0.0

    def _system(self) -> str:
        return (
            "You are choosing which training milestone an Ultima Online worker "
            "should focus on next. Pick exactly ONE milestone from the candidate "
            "list shown, by its exact name. Never invent a name not on the list. "
            'Reply with ONLY a JSON object: {"milestone": "<one name from the list>"}.'
        )

    def _situation(self, eligible: list[Milestone], snap: SkillContext) -> str:
        lines = [f"Profession: {self.profession}", "Candidate milestones (not yet achieved):"]
        for m in eligible:
            lines.append(f"- {m.name}: {m.description} (current progress {self._safe_progress(m, snap):.0%})")
        lines.append("Which milestone should be the current focus? Reply with the JSON object only.")
        return "\n".join(lines)

    # -- achieved-transition persistence ---------------------------------------------

    def _record_achieved(self, snap: SkillContext, m: Milestone) -> None:
        p = snap.obs.player.pos
        self.episodes.record(Episode(
            tick=snap.episode_count, kind="milestone", summary=f"{m.name} achieved",
            reward=0.0, pos=(p.x, p.y),
        ))
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "persona": self.persona_name,
            "profession": self.profession,
            "milestone": m.name,
        }
        try:
            with _milestones_log_lock:
                self.milestones_path.parent.mkdir(parents=True, exist_ok=True)
                with self.milestones_path.open("a") as f:
                    f.write(json.dumps(record) + "\n")
        except OSError:
            pass  # degrade silently — matches skill_library.py's own logging discipline

    def _load_achieved(self) -> set[tuple[str, str, str]]:
        try:
            text = self.milestones_path.read_text(encoding="utf-8")
        except OSError:
            return set()
        achieved: set[tuple[str, str, str]] = set()
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # corrupted/partial trailing line — skip, never fatal
            if not isinstance(record, dict):
                continue
            persona, profession, milestone = record.get("persona"), record.get("profession"), record.get("milestone")
            if isinstance(persona, str) and isinstance(profession, str) and isinstance(milestone, str):
                achieved.add((persona, profession, milestone))
        return achieved
