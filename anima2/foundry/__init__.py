"""foundry — the human-owned kernel (PHASE5.md, DESIGN.md §8 A6).

Everything under this package is the **ruler**, not the agent: fitness weights,
the independent trajectory recorder, and (later) the descriptor bins/archive/
eval harness/evolution loop. `anima2/skills/*`, `curriculum.py`,
`skill_tuning.py`, `cognition.py`, and `skill_library.py` — the agent's own
learning code — must never import from here; `tests/test_foundry_import_guard.py`
makes that a mechanical, AST-checked invariant rather than a convention (mirrors
v1 `../anima/foundry/kernel/safety.py`'s kernel-integrity intent, at the import
layer rather than a runtime git-diff check — item 2 ports the latter).

Ports `../anima/foundry/kernel/` (`fitness.py`, `trajectory.py`, `uoconst.py`)
with each module's *signal source* swapped: v1 parses raw ServUO wire packets
(anima v1 touches the wire); anima2 never does (DESIGN.md §2, "Brain ⊥ Body"),
so `trajectory.py` sources ground truth from two channels instead — see that
module's own docstring for the full channel (a)/(b) breakdown and every
adaptation from v1's shape.
"""

from __future__ import annotations
