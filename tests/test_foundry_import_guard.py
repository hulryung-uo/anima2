"""The import-graph guard (PHASE5.md item 1): a static, AST-level scan proving
the agent's own learning code has no import path to the kernel-owned
`anima2/foundry/` package — mirrors v1 `../anima/foundry/kernel/safety.py`'s
kernel-integrity intent at the import layer (item 2 later ports v1's runtime
git-diff check on top of this).

AST-level, not `grep`, so a `# imports anima2.foundry` comment or a string
mentioning it doesn't produce a false positive/negative — only a real
`import`/`from ... import` statement counts.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ANIMA2 = Path(__file__).resolve().parents[1] / "anima2"

#: Every module PHASE5.md item 1 names as "the agent's learning code" — must
#: never import `anima2.foundry` (absolute) or `foundry` (relative, any level).
_LEARNING_MODULES: list[Path] = [
    _ANIMA2 / "curriculum.py",
    _ANIMA2 / "skill_tuning.py",
    _ANIMA2 / "cognition.py",
    _ANIMA2 / "skill_library.py",
    *sorted((_ANIMA2 / "skills").glob("*.py")),
]


def _imports_foundry(source: str) -> bool:
    """True iff `source` contains a real `import`/`from ... import` naming
    the `foundry` package, at any relative level or as `anima2.foundry`."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "foundry" or alias.name.startswith("foundry.") \
                        or alias.name == "anima2.foundry" or alias.name.startswith("anima2.foundry."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            # `node.module` never carries the leading dots of a relative
            # import (those live in `node.level`) — checking bare "foundry"
            # catches `from .foundry import X` / `from ..foundry import X`
            # at ANY level, and "anima2.foundry" catches the absolute form.
            mod = node.module or ""
            if mod == "foundry" or mod.startswith("foundry.") \
                    or mod == "anima2.foundry" or mod.startswith("anima2.foundry."):
                return True
    return False


def test_no_learning_module_imports_the_foundry_kernel():
    assert _LEARNING_MODULES, "the scan target list is empty — the guard would pass vacuously"
    offenders = [
        str(path.relative_to(_ANIMA2.parent))
        for path in _LEARNING_MODULES
        if _imports_foundry(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"learning module(s) import the kernel-owned anima2.foundry package "
        f"(the ruler must be unreachable from learning code): {offenders}"
    )


def test_scan_target_list_covers_every_exported_skill_module():
    """Guards the guard itself: if `anima2/skills/` grows a new file that
    `skills/__init__.py` doesn't re-export from, this still catches it (glob,
    not the `__all__` list) — but assert the glob actually found the skills
    package's known files, so a moved/renamed `skills/` directory doesn't
    silently shrink the scan to nothing.
    """
    skill_files = {p.name for p in (_ANIMA2 / "skills").glob("*.py")}
    assert {"harvest.py", "smelt.py", "hunt.py", "craft.py", "combat.py"} <= skill_files


# --- positive control: prove the scanner actually has teeth -------------------


def test_detects_absolute_import_of_foundry():
    assert _imports_foundry("import anima2.foundry\n")
    assert _imports_foundry("import anima2.foundry.fitness\n")


def test_detects_relative_import_of_foundry_at_any_level():
    assert _imports_foundry("from .foundry import fitness\n")
    assert _imports_foundry("from ..foundry import fitness\n")
    assert _imports_foundry("from ...foundry.trajectory import TrajectorySummary\n")


def test_detects_absolute_from_import_of_foundry():
    assert _imports_foundry("from anima2.foundry import fitness\n")


def test_does_not_false_positive_on_unrelated_imports_or_comments():
    source = (
        "from __future__ import annotations\n"
        "import json\n"
        "from .skills.base import Skill\n"
        "from ..contract import Observation\n"
        "# this comment mentions anima2.foundry but is not an import\n"
        "FOUNDRY_LOOKALIKE = 'from .foundry import x'  # a string, not code\n"
    )
    assert not _imports_foundry(source)
