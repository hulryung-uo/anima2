"""The repeatable eval harness (PHASE5.md item 2) — kernel-owned.

One command -> one agent variant, one staged scenario, one independent
fitness number: `EvalConfig` -> `run_eval(cfg) -> EvalResult`, plus
`run_eval_multi` for multi-seed averaging. Ports v1 `../anima/foundry/kernel/
eval.py`'s `EvalConfig`/`EvalResult`/`run_eval` SHAPE onto anima2's Control
plane — the fixed-window / no-early-stop / multi-seed staging discipline v1
established (and PHASE4.md item 4's live gate independently re-discovered the
hard way), not v1's subprocess/worktree orchestration: anima2's genomes are
config-only (item 3's `Genome` — profession/sociability/`deliver_threshold`/
cognition tier — no mutation operator can ever edit source), so there is no
mutator worktree to isolate, unlike v1 where a genome *was* source in one.

Every eval is ONE staged character, ONE fixed-tick window (`agent.tick()` in
a straight `for` loop, no early-stop branch anywhere in this module — the
PHASE4.md item 4 lesson this harness bakes in from the start so every future
measurement is comparable), and ONE independent `foundry/fitness.py` score
computed from the `foundry/trajectory.py` recorder's channel (a)/(b) data —
never from the measured `Agent`'s own `episodes`/reward.

**Kernel-integrity guard** (`assert_kernel_clean`) — ported from v1
`safety.py`'s `kernel_is_clean`/`revert_kernel` in SPIRIT, not verbatim: v1
reverts a mutator's *worktree copy* of `foundry/kernel` before every eval
(its genomes edit source, so there is something to revert); anima2's genomes
are pure config, so there is nothing to revert here, only to detect.
`assert_kernel_clean` runs `git diff --quiet` + `git status --porcelain`
against `anima2/foundry` and raises `KernelTamperedError` if the tree isn't
clean vs `HEAD`. **Defense-in-depth, not load-bearing this phase** (PHASE5.md
item 2's own note) — kept cheap and simple on purpose; it becomes load-bearing
the moment a future skill-DSL (Phase 6) makes mutations touch code.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..agent import Agent
from ..control import GmControl
from ..ipc_body import IpcBody, IpcError
from ..live_common import GM_RELOGIN_COOLDOWN_S, login_throttle, wipe_area
from ..persona import Persona
from ..planner import Planner
from ..profession import FISHING_SPOTS, MINING_SPOTS
from ..skills import Fish, Mine
from ..skills.base import Skill
from ._filelock import append_line_locked
from .descriptor import compute_descriptor
from .fitness import FitnessBreakdown, compute_fitness
from .trajectory import TappedBody, TrajectoryRecorder

#: `data/eval_results.jsonl` relative to the process's cwd — mirrors
#: `skill_library.py`'s `_DEFAULT_LEDGER`/`llm.py`'s `_DEFAULT_USAGE_LOG`
#: convention exactly (created lazily, gitignored). Tests must always pass an
#: explicit `results_path=` (a `tmp_path`) so the suite never touches the
#: real file.
_DEFAULT_RESULTS_PATH = Path("data") / "eval_results.jsonl"

#: Guards concurrent appends to a single results file — mirrors
#: `skill_library.py::_ledger_lock`.
_results_lock = threading.Lock()

#: Generous enough to catch a scenario's own debris; matches
#: `live_fitness_gate.py`/`live_trade.py`'s own `WIPE_RADIUS`.
EVAL_WIPE_RADIUS = 10

#: `run_eval_multi`'s per-seed retry budget for a transient live-infra
#: failure (a dropped IPC/GM connection mid-eval — "Connection reset by
#: peer", the same class PHASE5.md item 1's own live gate honestly logged
#: as "a bridge broken-pipe at one session's tail," and this item's live
#: gate hit twice more live-testing this exact module). NOT a defect in the
#: variant being measured, so on any retry left this reruns the WHOLE
#: `run_eval` call (a fresh account, fresh connections) rather than trusting
#: a half-finished window — mirrors `live_trade.py --tuner`'s own "a live
#: wedge, not a real signal — retry" discipline, scoped to genuine
#: connection failures only; a real assertion/logic error still propagates
#: immediately, uncaught.
_TRANSIENT_RETRY_ATTEMPTS = 3
_TRANSIENT_RETRY_SLEEP_S = 6.0


def _run_eval_with_retry(cfg: "EvalConfig", *, kernel_repo_root: str | Path | None) -> "EvalResult":
    last_exc: Exception | None = None
    for attempt in range(_TRANSIENT_RETRY_ATTEMPTS):
        try:
            return run_eval(cfg, kernel_repo_root=kernel_repo_root)
        except (IpcError, ConnectionError, OSError) as e:
            last_exc = e
            more_left = attempt + 1 < _TRANSIENT_RETRY_ATTEMPTS
            print(f"  [run_eval_multi] seed={cfg.seed} account={cfg.account_name()} hit a transient "
                  f"live-infra error ({type(e).__name__}: {e}) — "
                  + ("retrying on a fresh connection" if more_left else
                     f"exhausted all {_TRANSIENT_RETRY_ATTEMPTS} attempts, re-raising"))
            if more_left:
                time.sleep(_TRANSIENT_RETRY_SLEEP_S)
    assert last_exc is not None
    raise last_exc


class KernelTamperedError(RuntimeError):
    """Raised by `assert_kernel_clean` when `anima2/foundry/` doesn't match
    `HEAD` — refuse to score rather than trust a ruler that might have been
    edited mid-run."""


def assert_kernel_clean(repo_root: str | Path = ".", kernel_path: str = "anima2/foundry") -> None:
    """Refuse to score if `kernel_path`'s tree has any uncommitted change
    (tracked-file edit OR a new untracked file) versus `HEAD`. Two `git`
    calls, both cheap: `git diff --quiet` catches tracked-file edits (exit 1),
    `git status --porcelain` additionally catches a freshly added,
    not-yet-committed module `git diff` alone wouldn't see. Raises
    `KernelTamperedError` on either finding, or if `git` itself can't be run
    (a missing/broken repo — refuse to score rather than silently skip the
    check).
    """
    try:
        diff = subprocess.run(
            ["git", "diff", "--quiet", "--", kernel_path],
            cwd=str(repo_root), capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise KernelTamperedError(f"could not check kernel integrity (git diff): {e}") from e
    if diff.returncode not in (0, 1):
        raise KernelTamperedError(
            f"git diff on {kernel_path} failed unexpectedly (exit {diff.returncode}): "
            f"{diff.stderr!r}"
        )
    if diff.returncode == 1:
        raise KernelTamperedError(f"{kernel_path} has uncommitted changes vs HEAD — refusing to score")

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", kernel_path],
            cwd=str(repo_root), capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise KernelTamperedError(f"could not check kernel integrity (git status): {e}") from e
    if status.returncode != 0:
        raise KernelTamperedError(
            f"git status on {kernel_path} failed unexpectedly (exit {status.returncode}): "
            f"{status.stderr!r}"
        )
    if status.stdout.strip():
        raise KernelTamperedError(
            f"{kernel_path} has untracked/uncommitted files — refusing to score:\n{status.stdout}"
        )


# --- scenario registry -------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """A fixed, GM-stageable scenario: the workplace, the skills/items
    `GmControl.stage()` sets, which skill names the recorder should track,
    and the zero-arg work `Skill` the staged agent runs. Kernel-owned and
    locked, same spirit as `fitness.py`'s weights — the eval harness always
    scores the scenario it says it staged, never a variant-mutated one.
    """

    id: str
    spot: tuple[int, int]
    skills: dict[str, float]
    items: tuple[str, ...]
    skill_names: tuple[str, ...] = ("Mining",)
    work_skill: type[Skill] = Mine
    #: PHASE6.md item 4: the staged agent's `harvest_nodes` cluster
    #: (`skills/harvest.py::Harvest._current_node`, checked before probing).
    #: `Fish` is technically capable of probing like `Mine` (its own class
    #: docstring: "water is contiguous terrain ... a fisher just probes the
    #: tiles in casting range") but `profession.py`'s own fisher wiring
    #: deliberately avoids that — "probing reach-4 wastes ticks reaching far
    #: water, so we target the known tile directly" — so any fishing
    #: `Scenario` needs `nodes` set for the same efficiency reason
    #: `village.py`'s real fisher wiring already does. `None` (every
    #: existing mining entry) is a no-op — `run_eval` only seeds
    #: `agent.memory["harvest_nodes"]` when this is set, mirroring
    #: `village.py`'s own `if p["nodes"]: agent.memory["harvest_nodes"] =
    #: p["nodes"]` wiring.
    nodes: tuple[tuple[int, int, int, int], ...] | None = None


#: PHASE5.md item 2's own required pair: a bare `Mine()` at a viable
#: `MINING_SPOTS` entry (`profession.py`'s own note, confirmed live in the
#: P0 hardening pass — see the `anima2-live-verification` memory note: ONLY
#: indices `[0..3]` have live, reachable ore; `[4:]` are calibration dead
#: ends), and the same scenario staged at Mining 50 instead of 35 — for the
#: live gate's ordering leg.
#: PHASE6.md item 4's second scenario-supported profession: fishing, chosen
#: as the minimal-risk addition (`Fish` is a `Harvest` subclass covered by
#: the same Phase 4 windowed-stuck-rate/`WalkTo`-relocation hardening `Mine`
#: already has, and `FISHING_SPOTS` — `profession.py`'s own already-
#: calibrated, already-live-verified fisher shore/water pool — needs no new
#: calibration work). `FISHING_SPOTS[0]` is `((2866, 647), (2865, 646, -5))`
#: — `spot` is the shore stand tile, `nodes` the exact water tile as a
#: 4-tuple with graphic `0` (a land-target cast), matching `village.py`'s own
#: fisher wiring (`if prof.key == "fisher": ... nodes = [(wx, wy, wz, 0)]`)
#: exactly.
_FISH_STAND, _FISH_WATER = FISHING_SPOTS[0]

SCENARIOS: dict[str, Scenario] = {
    "mining": Scenario(
        id="mining", spot=MINING_SPOTS[0], skills={"Mining": 35},
        items=("Pickaxe", "Pickaxe"), skill_names=("Mining",), work_skill=Mine,
    ),
    "mining_50": Scenario(
        id="mining_50", spot=MINING_SPOTS[0], skills={"Mining": 50},
        items=("Pickaxe", "Pickaxe"), skill_names=("Mining",), work_skill=Mine,
    ),
    "fishing": Scenario(
        id="fishing", spot=_FISH_STAND, skills={"Fishing": 35},
        items=("FishingPole",), skill_names=("Fishing",), work_skill=Fish,
        nodes=(_FISH_WATER + (0,),),
    ),
}


# --- EvalConfig / EvalResult --------------------------------------------------


@dataclass(frozen=True)
class EvalConfig:
    """One eval: which scenario, how long, which fresh account, and the
    variant knobs this item's own live gate needs on top of the scenario's
    own defaults. `ticks`, not `window_s` — anima2's fast loop has no
    wall-clock throttle of its own (one tick == one `Agent.tick()`, which
    pumps the bridge for `pump_ms`), and every existing live gate in this
    package (`live_fitness_gate.py`, PHASE4.md item 4's `--tuner-ticks`)
    already measures eval windows this way.

    `skill_overrides`/`item_overrides` are the "agent-variant config" the
    spec's own `EvalConfig` shape names — deliberately minimal (this item's
    own two required scenarios are both bare `Mine()`; item 3's `Genome`
    (profession/sociability/`deliver_threshold`/cognition tier) is the fuller
    variant space and doesn't need anticipating here). `skill_overrides`
    layers onto (doesn't replace) the scenario's own `skills` dict — e.g.
    staging Mining 50 on the plain `"mining"` scenario instead of using the
    `"mining_50"` entry. `item_overrides`, when not `None`, REPLACES the
    scenario's own `items` entirely — e.g. `()` for a "no pickaxe" variant
    (the live gate's own fallback ordering pair if the Mining-35-vs-50
    skill-gain-RATE comparison doesn't empirically go the expected way — see
    `live_eval_gate.py`).

    `nodes` mirrors `spot`: when not `None` it OVERRIDES the scenario's own
    `nodes` (the harvest node — the exact water/ore tile the staged agent
    probes, seeded into `agent.memory["harvest_nodes"]`); `None` (every
    existing mining caller AND a plain fishing `run_eval`) falls back to the
    scenario's own `nodes`, a byte-for-byte no-op. It exists for exactly one
    reason: fishing drains its 8x8 `HarvestBank` the same way mining drains an
    ore vein (5-15 fish, 10-20 min respawn — verified against
    `../servuo/Scripts/Services/Harvest/Fishing.cs`), so a multi-seed fishing
    run must rotate the WATER node in lockstep with the shore STAND `spot` —
    `FISHING_SPOTS` entries are `((stand), (water))` MATCHED pairs — or every
    seed re-fishes one draining bank (a real, live-caught PHASE6.md item 4
    failure: three with-pole seeds at `FISHING_SPOTS[0]` came back
    `[134.6, 237.7, 0.0]`, the third starved). `run_eval_multi`'s `nodes_pool=`
    rotates this per seed alongside `spot_pool=`; see its own docstring.
    """

    scenario_id: str
    ticks: int = 300
    seed: int = 0
    account_prefix: str = "eval"
    host: str = "127.0.0.1"
    port: int = 2594
    pump_ms: int = 400
    spot: tuple[int, int] | None = None
    nodes: tuple[tuple[int, int, int, int], ...] | None = None
    skill_overrides: dict[str, float] = field(default_factory=dict)
    item_overrides: tuple[str, ...] | None = None

    def account_name(self) -> str:
        return f"{self.account_prefix}{self.seed}"


@dataclass
class EvalResult:
    """One `run_eval` outcome: the independent fitness breakdown + the
    variant config + which scenario was staged, plus enough of the recorded
    trajectory's own aggregate shape (duration/skill-gain/gold/alive) to
    read the evidence back without re-deriving it — NOT the full raw
    `positions`/`hp_samples` traces (unbounded per-tick lists there's no
    reason to persist to a ledger). Written to `data/eval_results.jsonl`
    (kernel-owned, gitignored, append-only, cross-process readable, corrupt-
    line-tolerant on read — the exact `skill_library.py` ledger convention).
    """

    scenario_id: str
    config: EvalConfig
    fitness: FitnessBreakdown
    duration_h: float
    skill_gain_total: float
    gold_delta: int
    alive_fraction: float
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    #: `descriptor.py::compute_descriptor(summary).cell` — item 3's behavior
    #: cell key (Phase-0 active grid: `profession_focus x sociability_bin`),
    #: computed once inside `run_eval` (the only place the full
    #: `TrajectorySummary` is ever in scope — it's never persisted itself,
    #: see this class's own docstring) and carried on the result so item 4's
    #: `evolve.py` doesn't need to re-derive it or keep the summary alive.
    #: Defaults to `()` (empty tuple) for backward compatibility with any
    #: `eval_results.jsonl` line written before this field existed.
    descriptor_cell: tuple = field(default_factory=tuple)

    @property
    def score(self) -> float:
        return self.fitness.total

    def to_json(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "scenario_id": self.scenario_id,
            "config": asdict(self.config),
            "fitness": self.fitness.as_dict(),
            "duration_h": self.duration_h,
            "skill_gain_total": self.skill_gain_total,
            "gold_delta": self.gold_delta,
            "alive_fraction": self.alive_fraction,
            "descriptor_cell": list(self.descriptor_cell),
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "EvalResult":
        cfg_d = dict(d["config"])
        if cfg_d.get("spot") is not None:
            cfg_d["spot"] = tuple(cfg_d["spot"])
        if cfg_d.get("nodes") is not None:
            cfg_d["nodes"] = tuple(tuple(n) for n in cfg_d["nodes"])
        if cfg_d.get("item_overrides") is not None:
            cfg_d["item_overrides"] = tuple(cfg_d["item_overrides"])
        config = EvalConfig(**cfg_d)
        fitness = FitnessBreakdown(**d["fitness"])
        return cls(
            scenario_id=d["scenario_id"],
            config=config,
            fitness=fitness,
            duration_h=d["duration_h"],
            skill_gain_total=d["skill_gain_total"],
            gold_delta=d["gold_delta"],
            alive_fraction=d["alive_fraction"],
            ts=d.get("ts", ""),
            descriptor_cell=tuple(d.get("descriptor_cell", ())),
        )


def write_eval_result(result: EvalResult, path: str | Path | None = None) -> None:
    """Append one JSON line to `path` (default `data/eval_results.jsonl`).
    A broken/unwritable path degrades silently (never raises) — mirrors
    `skill_library.py::record_outcome`'s "never break the caller over a
    logging failure" discipline.
    """
    p = Path(path) if path is not None else _DEFAULT_RESULTS_PATH
    try:
        # `_results_lock` (same-process threads) + `append_line_locked`'s
        # `fcntl.flock` (cross-process — PHASE5.md item 4's forced follow-up,
        # see `_filelock.py`'s own module docstring) — same double-guard
        # `archive.py::Archive._append` uses.
        with _results_lock:
            append_line_locked(p, json.dumps(result.to_json()))
    except OSError:
        return


def read_eval_results(path: str | Path | None = None) -> list[EvalResult]:
    """Every parseable line of `path`, in order — a missing file yields no
    results (not an error), a corrupted/partial trailing line is skipped,
    never fatal. Mirrors `skill_library.py::_read_ledger`'s "degrade, never
    crash" discipline exactly; this is the function a fresh subprocess calls
    for the live gate's cross-process readback.
    """
    p = Path(path) if path is not None else _DEFAULT_RESULTS_PATH
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[EvalResult] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            out.append(EvalResult.from_json(d))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return out


# --- run_eval / run_eval_multi ------------------------------------------------


def run_eval(cfg: EvalConfig, *, kernel_repo_root: str | Path | None = ".") -> EvalResult:
    """Stage `cfg`'s scenario via `GmControl`, run a FIXED `cfg.ticks`-tick
    window (no early stop — a straight `for` loop, no break condition
    anywhere in this function) of the scenario's work skill, and compute
    independent fitness on the recorded trajectory. One eval == one fresh
    `IpcBody` + one `GmControl` connection, both closed before returning.

    `kernel_repo_root=None` skips the kernel-integrity guard entirely (only
    useful for a test fixture that doesn't want a real git repo on hand);
    every real caller leaves it at the default `"."`.
    """
    if kernel_repo_root is not None:
        assert_kernel_clean(kernel_repo_root)

    # ServUO login throttle — every real eval opens two fresh connections
    # (the subject IpcBody + a staging/recording GmControl); spacing every
    # call, not just the caller's own first one, is what keeps a multi-seed
    # `run_eval_multi` loop (12+ logins back to back for a full live gate)
    # from tripping the per-IP throttle. Lives here, not in
    # `run_eval_multi`'s own loop, so a caller that stubs out `run_eval`
    # entirely (every offline test in `tests/test_foundry_eval.py`) never
    # pays this cost.
    login_throttle()

    scenario = SCENARIOS[cfg.scenario_id]
    spot = cfg.spot or scenario.spot
    # `cfg.nodes` OVERRIDES the scenario's own `nodes` when set (the harvest
    # water/ore tile the staged agent probes) — the per-seed rotation hook
    # `run_eval_multi`'s `nodes_pool=` needs so a multi-seed fishing run moves
    # the water node in lockstep with the shore `spot`. `None` falls back to
    # the scenario's own `nodes` (every mining caller and a plain fishing
    # `run_eval` — a byte-for-byte no-op). See `EvalConfig.nodes`' docstring.
    nodes = cfg.nodes if cfg.nodes is not None else scenario.nodes
    skills = dict(scenario.skills)
    skills.update(cfg.skill_overrides)
    items = list(cfg.item_overrides) if cfg.item_overrides is not None else list(scenario.items)

    account = cfg.account_name()
    with IpcBody.spawn(cfg.host, cfg.port, account, account, pump_ms=cfg.pump_ms) as ipc:
        serial = ipc.ready["player"]["serial"]
        # The single shared GM account (hulryung) is reconnected every eval —
        # a longer, dedicated cooldown than the subject's own login throttle
        # (see GM_RELOGIN_COOLDOWN_S's own docstring for the live finding
        # this fixes: a shorter gap here left a stale prior GM session that
        # closed the new one mid-`[Get`-sequence a few calls in).
        login_throttle(GM_RELOGIN_COOLDOWN_S)
        with GmControl.spawn(cfg.host, cfg.port) as gm:
            gm.hide()
            wipe_area(gm, spot[0], spot[1], EVAL_WIPE_RADIUS)
            gm.stage(serial, spot[0], spot[1], skills=skills, items=items)

            # Let the teleport + pack grant settle before the window starts.
            ipc.observe()
            ipc.observe()

            # Channel (a): `gm` is a SEPARATE connection from the subject's
            # own `ipc` body — the same connection `stage()` used above is
            # reused for the recorder's `[Get` reads (identical to
            # `live_fitness_gate.py`'s own wiring; independence is about
            # being a different connection asking the server, not about
            # being freshly re-opened per read — see `trajectory.py`'s
            # module docstring).
            recorder = TrajectoryRecorder(gm, subject_serial=serial, skill_names=scenario.skill_names)
            recorder.start()

            tapped = TappedBody(ipc, recorder)
            agent = Agent(
                body=tapped, persona=Persona(name=f"eval-{cfg.scenario_id}-{cfg.seed}"),
                planner=Planner([scenario.work_skill()]),
            )
            if nodes:
                # Mirrors `village.py`'s own `if p["nodes"]: agent.memory[
                # "harvest_nodes"] = p["nodes"]` wiring — see `Scenario.
                # nodes`'s own docstring for why fishing needs this set
                # rather than falling back to `Fish`'s own probe capability.
                # `nodes` is `cfg.nodes` when the caller overrode it (per-seed
                # rotation), else the scenario's own — resolved above.
                agent.memory["harvest_nodes"] = list(nodes)
            for _ in range(cfg.ticks):
                agent.tick()

            summary = recorder.finish()

    fitness = compute_fitness(summary)
    # Item 3's descriptor, computed here (the only place the full
    # `TrajectorySummary` is in scope) and carried on the result as just its
    # `cell` key — see `EvalResult.descriptor_cell`'s own docstring.
    descriptor = compute_descriptor(summary)
    return EvalResult(
        scenario_id=cfg.scenario_id,
        config=cfg,
        fitness=fitness,
        duration_h=summary.duration_h,
        skill_gain_total=summary.skill_gain_total,
        gold_delta=summary.gold_delta,
        alive_fraction=summary.alive_fraction(),
        descriptor_cell=descriptor.cell,
    )


@dataclass
class MultiEvalResult:
    """`run_eval_multi`'s return shape: every seed's own `EvalResult` plus
    the mean/stdev over their fitness — item 3's `reliability_score`
    (`mean - PROMOTION_LAMBDA * pstdev`) needs the per-seed list, not just
    the mean, so this keeps both."""

    scenario_id: str
    base_config: EvalConfig
    results: list[EvalResult] = field(default_factory=list)

    @property
    def per_seed_fitness(self) -> list[float]:
        return [r.score for r in self.results]

    @property
    def mean_fitness(self) -> float:
        vals = self.per_seed_fitness
        return statistics.fmean(vals) if vals else 0.0

    @property
    def stdev_fitness(self) -> float:
        vals = self.per_seed_fitness
        return statistics.pstdev(vals) if len(vals) > 1 else 0.0


def run_eval_multi(
    cfg: EvalConfig,
    *,
    seeds: int = 3,
    spot_pool: Sequence[tuple[int, int]] | None = None,
    nodes_pool: Sequence[tuple[tuple[int, int, int, int], ...]] | None = None,
    kernel_repo_root: str | Path | None = ".",
    results_path: str | Path | None = None,
) -> MultiEvalResult:
    """`seeds` fresh-account repeats of `cfg`'s variant/scenario, averaged —
    directly tames the known `Harvest`/`Mine` intermittent freeze (PHASE4.md
    item 4's follow-up): a frozen seed gates to ~0 through `fitness.py`'s own
    viability gate and is AVERAGED IN, not dropped or retried — "average
    repeats, never trust one sample," now first-class in the harness rather
    than hand-rolled per live script.

    Each seed gets `cfg.seed + i` (so repeat calls with the same base `cfg`
    on fresh account suffixes never collide) and, when `spot_pool` is given,
    `spot_pool[i % len(spot_pool)]` as its `spot` — the live hygiene fix for
    mining scenarios specifically (a single `HarvestBank` grid cell respawns
    only 10-20 real minutes after first consumed; back-to-back seeds at ONE
    spot would silently share one thinning bank rather than being genuinely
    independent trials — see the `anima2-live-verification` memory note).
    `spot_pool=None` (the default) leaves every seed at `cfg.spot`/the
    scenario's own default spot — the caller's job to space in time if that
    matters for a non-mining scenario.

    `nodes_pool` is `spot_pool`'s FISHING companion, rotated IN LOCKSTEP:
    seed `i` gets `nodes_pool[i % len(nodes_pool)]` as its `nodes` override.
    Fishing drains its water bank exactly as mining drains ore (verified
    against `../servuo/.../Fishing.cs`; a real PHASE6.md item 4 gate run
    starved a spot's third seed to a `0.0`), and a fishing spot is a MATCHED
    `(shore-stand, water-node)` pair — so rotating only the stand `spot` isn't
    enough; the water node the agent actually casts at must move with it.
    Callers pass `spot_pool`/`nodes_pool` as same-length, index-aligned pairs
    (`spot_pool[i]`'s stand goes with `nodes_pool[i]`'s water) — see
    `live_eval_gate.py::_fishing_gate`. `nodes_pool=None` (every mining
    caller) leaves each seed at `cfg.nodes`/the scenario's own default node,
    a byte-for-byte no-op.

    `results_path` is passed to `write_eval_result` per seed when given (the
    live gate's own way of getting every seed onto `data/eval_results.jsonl`
    as it runs, not just at the end).
    """
    multi = MultiEvalResult(scenario_id=cfg.scenario_id, base_config=cfg)
    for i in range(seeds):
        seed_spot = spot_pool[i % len(spot_pool)] if spot_pool else cfg.spot
        seed_nodes = nodes_pool[i % len(nodes_pool)] if nodes_pool else cfg.nodes
        seed_cfg = replace(cfg, seed=cfg.seed + i, spot=seed_spot, nodes=seed_nodes)
        result = _run_eval_with_retry(seed_cfg, kernel_repo_root=kernel_repo_root)
        multi.results.append(result)
        if results_path is not None:
            write_eval_result(result, results_path)
    return multi
