"""Behavior descriptor — the QD axes (PHASE5.md item 3, DESIGN.md §6.6).

Near-verbatim port of v1 `../anima/foundry/kernel/descriptor.py`'s
`compute_descriptor` over anima2's `foundry/trajectory.py::TrajectorySummary`
instead of v1's wire-parsed one (signal source swapped, shape mostly kept —
see "Adaptations from v1" below for the one real change).

Computed purely from a parsed trajectory (behavior, not code/intent). Captures
WHAT KIND of soul the agent is; `foundry/fitness.py` (HOW GOOD at being that
kind) is deliberately never imported here and never feeds into a bin edge —
the two are decoupled by construction, exactly as v1 states its own invariant:
profession_focus reads *which* skill category (not how much it's worth),
sociability reads a raw action-type *fraction* (not a reward), so the grid
expresses real diversity, not a fitness ramp.

4 locked axes: `profession_focus` (categorical, from `uoconst.SKILL_CATEGORY`
via `TrajectorySummary.profession_skill_gains()`) + `sociability` / `aggression`
/ `mobility` (continuous, 3 bins each — low/mid/high). Phase-0 ACTIVE grid is
`profession_focus x sociability` (v1's own "phased activation" — `aggression`/
`mobility` are computed and stored on `Descriptor` but not yet part of the
cell key). Bin boundaries below are ported **verbatim** from v1
`../anima/foundry/kernel/descriptor.py:24-26` — kernel-owned, calibrated in
v1's own Phase 0, not re-guessed here.

**Adaptations from v1, stated plainly (not glossed over):**
 - `profession_focus`/`sociability`/`mobility_rate` reuse
   `TrajectorySummary.profession_skill_gains()`/`.speech_sent`/
   `.total_actions`/`.unique_regions`/`.duration_h` with the same field
   names, so the formulas below are copied verbatim from v1's
   `compute_descriptor`. One denominator caveat, stated rather than glossed:
   anima2's `total_actions` tallies action *groups* the `TappedBody` tap
   sees (including `TargetObject`/`TargetGround` answers and `WarMode`
   toggles that v1's packet-decoded action counter did not count), so the
   sociability/aggression *ratios* sit on a slightly larger denominator
   than v1's. The bin edges are kept verbatim anyway: the ratios stay in
   the same order of magnitude, and re-binning is a Phase-0-style
   calibration question for live data, not a port decision.
 - `aggression` is the one real adaptation. v1 tallies a dedicated
   `attacks_initiated` counter, incremented while decoding raw C->S attack
   packets. anima2 has no packet stream (`trajectory.py`'s own module
   docstring); the direct analog is `TrajectorySummary.action_counts["attack"]`
   — the same channel-(b) `_ACTION_GROUP` bucket `TappedBody.tap_action`
   already classifies `Attack`/`WarMode` actions into for `fitness.py`'s
   `_liveness` variety check. Using it here (rather than adding a second,
   redundant counter) keeps one tapped signal serving both the fitness gate
   and the descriptor, matching how v1's own single wire tap fed both kernel
   modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import uoconst
from .trajectory import TrajectorySummary

# Continuous-axis bin boundaries (low | mid | high). Ported VERBATIM from v1
# `../anima/foundry/kernel/descriptor.py:24-26` — kernel-owned, calibratable
# only by editing this module (never by the agent's learning code).
SOCIABILITY_EDGES = (0.02, 0.10)        # speech / total actions
AGGRESSION_EDGES = (0.02, 0.15)         # attacks / total actions
MOBILITY_EDGES = (10.0, 40.0)           # unique regions / hour

BIN_NAMES = ("low", "mid", "high")

# Phase-0 active grid axes (v1 FOUNDRY.md §4 phased activation, ported as-is).
ACTIVE_AXES: tuple[str, ...] = ("profession_focus", "sociability")


def _bin(value: float, edges: tuple[float, float]) -> int:
    """Ported verbatim from v1: 0 (low) / 1 (mid) / 2 (high)."""
    if value < edges[0]:
        return 0
    if value < edges[1]:
        return 1
    return 2


@dataclass
class Descriptor:
    profession_focus: str = uoconst.NONE
    sociability_bin: int = 0
    aggression_bin: int = 0
    mobility_bin: int = 0

    # raw axis values (interpretability)
    sociability: float = 0.0
    aggression: float = 0.0
    mobility_rate: float = 0.0
    profession_gains: dict[str, float] = field(default_factory=dict)

    @property
    def cell(self) -> tuple:
        """Active-grid cell key (Phase 0: profession_focus x sociability)."""
        parts: list = []
        for axis in ACTIVE_AXES:
            if axis == "profession_focus":
                parts.append(self.profession_focus)
            elif axis == "sociability":
                parts.append(self.sociability_bin)
            elif axis == "aggression":
                parts.append(self.aggression_bin)
            elif axis == "mobility":
                parts.append(self.mobility_bin)
        return tuple(parts)

    @property
    def full_cell(self) -> tuple:
        """Full 4-axis cell key (for when all axes are activated)."""
        return (
            self.profession_focus,
            self.sociability_bin,
            self.aggression_bin,
            self.mobility_bin,
        )

    def label(self) -> str:
        soc = BIN_NAMES[self.sociability_bin]
        return f"{self.profession_focus.lower()}/{soc}-social"


def compute_descriptor(summ: TrajectorySummary) -> Descriptor:
    """Ported verbatim from v1 `compute_descriptor`, except `aggression`'s
    signal source (see module docstring)."""
    actions = max(1, summ.total_actions)

    gains = summ.profession_skill_gains()
    if gains:
        profession = max(gains, key=gains.get)
    else:
        profession = uoconst.NONE

    sociability = summ.speech_sent / actions
    # Adaptation from v1's dedicated `attacks_initiated` counter — see module
    # docstring: anima2 has no packet stream, so this reuses the same tapped
    # `action_counts["attack"]` group `fitness.py::_liveness` already reads.
    aggression = summ.action_counts.get("attack", 0) / actions
    mobility_rate = summ.unique_regions / max(summ.duration_h, 1.0 / 60.0)

    return Descriptor(
        profession_focus=profession,
        sociability_bin=_bin(sociability, SOCIABILITY_EDGES),
        aggression_bin=_bin(aggression, AGGRESSION_EDGES),
        mobility_bin=_bin(mobility_rate, MOBILITY_EDGES),
        sociability=sociability,
        aggression=aggression,
        mobility_rate=mobility_rate,
        profession_gains=gains,
    )
