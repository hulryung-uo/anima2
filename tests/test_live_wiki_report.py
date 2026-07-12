"""`live_wiki_report.py`'s safety gate — `_assert_no_remote` and the
`--allow-remote-repo` opt-in (the flag that lets a genuine one-time write land
in the real, remoted `../uowiki`), plus the standing "file_report never pushes"
property that opt-in relies on.

Offline and deterministic: real `git init`/`git remote add` against `tmp_path`
repos (no network — a bogus remote URL is never contacted, only listed by
`git remote -v`), never the real `../uowiki`.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import textwrap
from pathlib import Path

import pytest

from anima2.live_wiki_report import _assert_no_remote
from anima2.wiki import Wiki


def _init_repo(path: Path, *, remote: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if remote is not None:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote],
                       check=True, capture_output=True, text=True)
    return path


def test_assert_no_remote_refuses_remoted_repo_without_the_flag(tmp_path):
    """(a) The default gate is unchanged: a repo WITH a remote is refused — the
    same discipline that has kept the real ../uowiki untouched."""
    repo = _init_repo(tmp_path / "remoted", remote="https://example.invalid/uowiki.git")
    with pytest.raises(SystemExit):
        _assert_no_remote(repo)  # allow_remote defaults to False


def test_assert_no_remote_allows_remoted_repo_with_the_flag(tmp_path, capsys):
    """(b) `--allow-remote-repo` (allow_remote=True) skips the refusal for a
    remoted repo, printing a WARNING that names the repo and its remote."""
    repo = _init_repo(tmp_path / "remoted", remote="https://example.invalid/uowiki.git")
    _assert_no_remote(repo, allow_remote=True)  # must NOT raise
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert str(repo) in out                       # names the repo
    assert "example.invalid/uowiki.git" in out    # names the remote
    assert "never pushes" in out                  # reassures pushing stays off


def test_assert_no_remote_passes_silently_on_a_remoteless_repo(tmp_path, capsys):
    """The common (disposable-clone) case is byte-for-byte unchanged: a repo
    with NO remote returns silently, with or without the flag — no warning."""
    repo = _init_repo(tmp_path / "clean")  # no remote
    _assert_no_remote(repo)                # default
    _assert_no_remote(repo, allow_remote=True)
    assert capsys.readouterr().out == ""   # nothing printed either way


def test_file_report_code_path_never_pushes():
    """(c) The opt-in only relaxes the *remote refusal*; it must never open a
    push path. Assert `push` appears nowhere in `Wiki.file_report`'s executable
    code — the one method that runs git — while `add`/`commit` (the two git
    subcommands it IS allowed to run) do. The docstring is stripped first (it
    mentions "never `git push`" in prose); the check is against real code only.
    A behavioral argv-spy proof of the same property lives in
    test_wiki.py::test_file_report_never_pushes_across_this_whole_test_file;
    this static check pins it directly against the method body."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(Wiki.file_report)))
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    first = func.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        func.body = func.body[1:]  # drop the docstring — check code, not prose
    code = ast.unparse(func)
    assert "push" not in code
    assert "add" in code and "commit" in code  # it does add+commit — meaningful check
