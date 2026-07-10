"""Fitness — the read-only oracle (PHASE5.md item 1, DESIGN.md A6).

    fitness = viability_gate x (skill_term + worth_term + produce_term + behavior_bonus)

Near-verbatim port of v1 `../anima/foundry/kernel/fitness.py`'s
`compute_fitness` — same formula, same locked weights, same gate — over
`foundry/trajectory.py`'s `TrajectorySummary` instead of v1's wire-parsed one
(signal source swapped, shape kept; see that module's own docstring for every
adaptation).

**LOCKED. The agent may not edit this module.** Weights below are "facts of
the ruler, not of the agent" (v1's own words, `fitness.py`'s module
docstring) — porting them verbatim rather than re-guessing keeps the ranking
meaning consistent with the well-calibrated v1 kernel.
`tests/test_foundry_import_guard.py` makes "the agent can't reach this" a
mechanical invariant: no module under `anima2/skills/`, `curriculum.py`,
`skill_tuning.py`, `cognition.py`, or `skill_library.py` may import
`anima2.foundry` at all.

**The `channel_b` parameter (new here — v1 has no analog, since v1's single
wire-packet channel carries everything).** anima2 splits ground truth into
channel (a) (GM `[Get` reads — load-bearing) and channel (b) (an in-process
observation tap — corroborating only; see `trajectory.py`'s module
docstring). `compute_fitness(summ, channel_b=False)` computes fitness from
channel (a) alone: `alive_fraction` drops to its coarse start/end binary
(never the finer `hp_samples`-corroborated one), `liveness`/`loop_penalty`
default to fully permissive (`1.0`/`0.0` — channel (a) has no in-window
action-stream data to assess either from, so this deliberately does NOT
collapse the gate to 0, which would make a channel-(a)-only recomputation
prove nothing), and `produce_term`/`behavior_bonus` (which need
`items_into_pack`/`positions`/`speech`/`damage` — all channel (b)) go to
zero. This is PHASE5.md item 1's own live-gate requirement: recomputing
fitness with channel (b) excluded must still rank an honest worker above a
self-report-gaming one, on `skill_term`/`worth_term` alone — proving the
load-bearing signal doesn't secretly depend on the in-process tap.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from . import uoconst
from .trajectory import TrajectorySummary

# --- locked weights (ported verbatim from v1 fitness.py) --------------------
W_SKILL = 1.0
W_WORTH = 0.3
W_PRODUCE = 0.2

# economy normalization: how many gold ~= 1 skill point of value, so the skill
# backbone dominates typical ranges. (Phase-0 calibratable in v1; ported as-is.)
GOLD_NORM = 20.0

# behavior_bonus sub-weights (per-hour rates already on a small scale).
WB_EXPLORE = 0.10   # unique regions / hr
WB_SOCIAL = 0.15    # speech that drew responses / hr
WB_COMBAT = 0.10    # damage dealt / hr (normalized) — always 0 here; see trajectory.py
DAMAGE_NORM = 10.0

# Caps on per-hour behavior rates — a short window's burst would otherwise
# extrapolate to an absurd per-hour value and swamp the skill backbone.
MOBILITY_RATE_CAP = 60.0   # regions/hr
SOCIAL_RATE_CAP = 20.0     # responses/hr

# viability gate tuning
TARGET_ACTION_RATE = 30.0    # actions/hr to reach full liveness (~1 / 2 min)
MIN_DURATION_H = 1.0 / 60.0  # 1 minute floor to avoid rate blow-ups


@dataclass
class FitnessBreakdown:
    """Transparent component view — the observer/mutator reads this."""

    total: float = 0.0

    # gate
    viability_gate: float = 1.0
    alive_fraction: float = 1.0
    liveness: float = 0.0
    loop_penalty: float = 0.0

    # terms (post-weight, post-normalization)
    skill_term: float = 0.0
    worth_term: float = 0.0
    produce_term: float = 0.0
    behavior_bonus: float = 0.0

    # raw rates (pre-weight) for interpretability
    skill_gain_rate: float = 0.0
    networth_rate: float = 0.0
    produce_value_rate: float = 0.0
    regions_rate: float = 0.0
    social_response_rate: float = 0.0
    damage_rate: float = 0.0

    duration_h: float = 0.0
    channel_b: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


def _liveness(summ: TrajectorySummary, dur_h: float) -> float:
    """0->1 anti-freeze factor: did the agent take varied, real actions?

    A frozen agent (no confirmed actions) -> ~0. Requires >=2 distinct action
    groups for full credit so spamming one action can't fake liveness.
    Ported verbatim from v1 (identical thresholds); only the input shape
    (`TrajectorySummary.action_counts`/`.total_actions`) changed.
    """
    action_rate = summ.total_actions / dur_h if dur_h > 0 else 0.0
    base = min(1.0, action_rate / TARGET_ACTION_RATE)
    distinct = len([g for g, n in summ.action_counts.items() if n > 0])
    variety = min(1.0, distinct / 2.0)
    return base * (0.5 + 0.5 * variety)


def _loop_penalty(summ: TrajectorySummary) -> float:
    """0->1 penalty for pathological repetition (wall-walking proxy): the
    deny ratio of the tapped `Walk` confirm/deny inference (see
    `trajectory.py`'s own docstring for how anima2 infers this without a
    wire-level DenyWalk reply). Ported verbatim from v1.
    """
    total_steps = summ.steps_confirmed + summ.steps_denied
    if total_steps < 5:
        return 0.0
    return min(1.0, summ.steps_denied / total_steps)


def _produce_value(summ: TrajectorySummary) -> float:
    """Gold-equivalent value of items the agent put into its own containers.
    Ported verbatim from v1, over the ported `uoconst.ITEM_VALUES` table.
    """
    total = 0
    for graphic, amount, _ts in summ.items_into_pack:
        total += uoconst.ITEM_VALUES.get(graphic, uoconst.ITEM_VALUE_DEFAULT) * amount
    return float(total)


def compute_fitness(summ: TrajectorySummary, *, channel_b: bool = True) -> FitnessBreakdown:
    """Compute the fitness scalar + breakdown from a recorded trajectory.

    `channel_b=True` (default): the full computation, both channels — what a
    live eval reports day to day. `channel_b=False`: channel-(a)-only — see
    the module docstring for exactly what that isolates and why (PHASE5.md
    item 1's live gate's decisive recomputation).
    """
    dur_h = max(summ.duration_h, MIN_DURATION_H)

    alive = summ.alive_fraction(channel_b=channel_b)
    if channel_b:
        liveness = _liveness(summ, dur_h)
        loop_pen = _loop_penalty(summ)
    else:
        # Channel (a) alone has no in-window action-stream data to assess
        # liveness/looping from — default fully permissive rather than
        # collapsing the gate to 0 (see module docstring).
        liveness = 1.0
        loop_pen = 0.0
    gate = alive * liveness * (1.0 - loop_pen)

    # raw rates
    skill_rate = summ.skill_gain_total / dur_h
    networth_rate = summ.gold_delta / dur_h
    produce_rate = (_produce_value(summ) / dur_h) if channel_b else 0.0
    regions_rate = (summ.unique_regions / dur_h) if channel_b else 0.0
    social_resp_rate = (
        (min(summ.speech_recv, 3 * summ.speech_sent) + 0.0) / dur_h
    ) if channel_b else 0.0
    damage_rate = (summ.damage_dealt / dur_h) if channel_b else 0.0

    # weighted terms (economy normalized into skill-point-equivalents)
    skill_term = W_SKILL * skill_rate
    worth_term = W_WORTH * (networth_rate / GOLD_NORM)
    produce_term = W_PRODUCE * (produce_rate / GOLD_NORM)

    # Descriptor-aligned behavior bonus (v1 FOUNDRY.md §5): a profession cell's
    # backbone is skill gain, so exploration/combat bonuses only carry the
    # expressive NONE-profession archetypes; the social bonus applies everywhere.
    has_profession = bool(summ.profession_skill_gains())
    behavior_bonus = WB_SOCIAL * min(social_resp_rate, SOCIAL_RATE_CAP)
    if not has_profession:
        behavior_bonus += (
            WB_EXPLORE * min(regions_rate, MOBILITY_RATE_CAP)
            + WB_COMBAT * (damage_rate / DAMAGE_NORM)
        )

    inner = skill_term + worth_term + produce_term + behavior_bonus
    total = gate * inner

    return FitnessBreakdown(
        total=total,
        viability_gate=gate,
        alive_fraction=alive,
        liveness=liveness,
        loop_penalty=loop_pen,
        skill_term=skill_term,
        worth_term=worth_term,
        produce_term=produce_term,
        behavior_bonus=behavior_bonus,
        skill_gain_rate=skill_rate,
        networth_rate=networth_rate,
        produce_value_rate=produce_rate,
        regions_rate=regions_rate,
        social_response_rate=social_resp_rate,
        damage_rate=damage_rate,
        duration_h=dur_h,
        channel_b=channel_b,
    )
