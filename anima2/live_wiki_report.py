"""Live wiki write-loop proof: a reflection pass judges a synthetic
contradiction against a real wiki page and files a discrepancy report
(PHASE4.md item 1's write half — closing DESIGN.md §6 item 1).

**SAFETY.** This script `git commit`s into whatever `--wiki-repo-root` names.
It refuses to touch a filesystem at all if that repo has ANY git remote
configured (`_assert_no_remote`, checked before anything else) — the
"own-shard-adjacent pollution" discipline this project already learned the
hard way once (see the `anima2-live-verification` memory note), applied to a
sibling git repo instead of a game shard. Point `--wiki-repo-root` at a
disposable, remote-less clone, **never** the real `../uowiki`:

    git clone --no-hardlinks ../uowiki /tmp/uowiki-test
    git -C /tmp/uowiki-test remote remove origin
    python -m anima2.live_wiki_report --wiki-repo-root /tmp/uowiki-test \\
        --wiki-root /tmp/uowiki-test/src/content/docs

1. Spawn the agent body (animatest) and stage a miner scenario via the
   Control plane (pickaxe in pack, Mining 35, teleport to the Minoc ridge) —
   the same setup `live_mine.py`/`live_reflect.py` already live-verify.
2. Run `ThreadedCognition(ReflectingCognition(LLMCognition(...),
   LLMReflection(...), wiki_reporter=LLMWikiReportProducer(...)))` — real
   mining, real skill gain, real episodes — but every LLM-shaped decision
   comes from a small **scripted** (not live-network) client, so the run is
   fully reproducible: `_ScriptedGoalClient` always replies the same canned
   goal/speech JSON regardless of the prompt (so `--wiki-reporter=on` vs
   `=off` produce an identical goal/speech decision sequence — the
   differential-inertness leg's comparison needs this: `wiki_reporter`'s own
   code path never touches `ctx`/`goal` at all, provably so when the
   goal-cognition role can't itself introduce variance). `_CyclingJudgeClient`
   is the wiki-judge role: claims the SAME synthetic contradiction on its
   first 3 calls, a DIFFERENT one from the 4th call on — exactly the
   "cycles 1-3 -> 1 commit, cycle 4 -> 2nd commit" shape the circuit-breaker
   proof below needs, without waiting on live LLM variance to line it up.
3. Prints every judge call, every filed report, the disposable clone's commit
   count, and a provenance check (each report's `- page:` line read back and
   compared against an *independent* `wiki.search()` call — never against
   what the judge said) — all directly inspectable in stdout, watchdog-bounded
   by `--ticks`.

**The genuine one-time real-wiki write (opt-in).** The default judge is a
*scripted* stand-in (`_CyclingJudgeClient`) that FABRICATES a fixed synthetic
claim — perfect for the disposable-clone circuit-breaker proof, but its report
must never reach the real wiki. Two flags gate a genuine run against the real
`../uowiki` (which has a GitHub remote):

- `--live-llm` swaps every scripted client for the real Replicate qwen client,
  so the judge decides against LIVE mining episodes vs the REAL wiki page — any
  report it files is a genuine, LLM-surfaced discrepancy, not a fabrication.
- `--allow-remote-repo` relaxes `_assert_no_remote` for a repo with a remote.
  It does NOT enable pushing — `file_report` still only commits.

An interlock refuses `--allow-remote-repo` with the scripted judge, so the
fabricated synthetic claim can never be committed into a remoted repo.

Usage:
    # disposable-clone proof (default, scripted judge, refuses a remote):
    python -m anima2.live_wiki_report --wiki-repo-root /tmp/uowiki-test [...]
    # genuine one-time real-wiki write (real qwen judge, remote allowed):
    python -m anima2.live_wiki_report --wiki-repo-root ../uowiki \\
        --live-llm --allow-remote-repo [--ticks N] [--wiki-reporter {on,off}]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .agent import Agent
from .cognition import LLMCognition, LLMReflection, LLMWikiReportProducer, ReflectingCognition, ThreadedCognition
from .control import GmControl
from .ipc_body import IpcBody
from .llm import ReplicateClient
from .persona import Persona
from .planner import Planner
from .skills import GoTo, Mine, SpeakPending
from .wiki import Wiki


class _ScriptedGoalClient:
    """Deterministic stand-in for the goal-cognition ("chatter") role: always
    replies the same canned JSON regardless of the prompt. Not a live network
    call — the point is reproducibility, not proving LLM prose quality (that's
    already live-verified by `live_reflect.py`/`village.py --llm-tiers`).
    A canned reply makes the differential-inertness leg's claim ("wiki_reporter
    never perturbs this role's decision") checkable by direct comparison
    instead of lost in live-LLM variance."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.reply


class _ScriptedReflectionClient:
    """Deterministic reflection-producer client: always the same insight."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.reply


class _CyclingJudgeClient:
    """Forced wiki-judge client for the circuit-breaker's multi-cycle proof
    (PHASE4.md item 1's live gate): calls 1-3 claim the SAME synthetic
    contradiction (`CLAIM_A`); call 4 onward claims a DIFFERENT one
    (`CLAIM_B`). Mirrors `../anima/anima/planner/strategy.py`'s own live-forced
    pattern — this script forces the LLM's *proposal*; `Wiki.file_report`'s
    circuit breaker and page-provenance guard downstream of it run for real,
    unmodified, exactly as they would with a genuine LLM reply."""

    CLAIM_A = "mining yields exactly 3 ore per successful swing"
    CLAIM_B = "smelting always requires a forge within 2 tiles of the miner"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        claim = self.CLAIM_A if self.calls <= 3 else self.CLAIM_B
        return json.dumps({
            "contradiction": True,
            "claim": claim,
            "observed": f"live session synthetic observation #{self.calls}",
            "expected": "per the wiki page",
        })


class _CountingClient:
    """Wraps a real `LLMClient` (the live Replicate qwen client) so the run's
    stdout can report per-role call counts exactly the way the scripted
    stand-ins already do — both expose a `.calls` counter. Delegates every
    `complete` unchanged; it adds a counter and nothing else. Only used on the
    `--live-llm` path (a genuine LLM judge over live observation); the default
    path keeps the scripted stand-ins untouched."""

    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.inner.complete(system, user)  # type: ignore[attr-defined]


def _assert_no_remote(repo_root: Path, *, allow_remote: bool = False) -> None:
    """Refuse outright if `repo_root` has ANY git remote configured — the
    disposable-clone-only safety gate this whole script exists to enforce,
    checked before any filesystem write (report files, commits).

    `allow_remote=True` (the `--allow-remote-repo` opt-in) relaxes ONLY this
    refusal: a repo WITH a remote is let through, but with a printed WARNING
    naming the repo and its remote. It does NOT enable pushing — `file_report`
    still only `git add` + `git commit`s, never `git push` (proven in
    `wiki.py`/`tests`). A repo with no remote is unaffected either way (returns
    silently, exactly as before — the default `allow_remote=False` path is
    byte-for-byte unchanged)."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "remote", "-v"],
        capture_output=True, text=True, check=True,
    )
    if not result.stdout.strip():
        return  # no remote configured — always safe, nothing to warn about
    if allow_remote:
        print(
            "WARNING: --allow-remote-repo set — skipping the no-remote safety gate for a "
            f"repo that HAS a git remote:\n  {repo_root}\n{result.stdout.rstrip()}\n"
            "  file_report still only commits (never pushes); nothing here can push."
        )
        return
    sys.exit(
        f"refusing to run: {repo_root} has a git remote configured:\n"
        f"{result.stdout}\n"
        "Point --wiki-repo-root at a disposable, remote-less clone — never "
        "a repo with a remote (see this script's own module docstring)."
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=600)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--wiki-repo-root", required=True,
                     help="a DISPOSABLE, remote-less clone of ../uowiki (git-operations root)")
    ap.add_argument("--wiki-root", default=None,
                     help="docs read root (default: <wiki-repo-root>/src/content/docs)")
    ap.add_argument("--wiki-reporter", choices=("on", "off"), default="on",
                     help="off = differential-inertness leg (wiki_reporter=None)")
    ap.add_argument("--cognition-interval", type=int, default=12)
    ap.add_argument("--live-llm", action="store_true",
                     help="use the REAL Replicate qwen client for the goal/reflection/judge "
                          "roles instead of the scripted stand-ins — REQUIRED for a genuine "
                          "discrepancy against a real wiki (the scripted judge FABRICATES a "
                          "synthetic claim, only valid for the disposable-clone proof)")
    ap.add_argument("--allow-remote-repo", action="store_true",
                     help="opt in to running against a repo that HAS a git remote (e.g. the "
                          "real ../uowiki). Relaxes ONLY the no-remote refusal; it does NOT "
                          "enable pushing — file_report still only commits, never pushes.")
    args = ap.parse_args()

    repo_root = Path(args.wiki_repo_root).expanduser().resolve()
    if not (repo_root / ".git").exists():
        sys.exit(f"refusing to run: {repo_root} is not a git repo (no .git/) — "
                  "point --wiki-repo-root at a disposable clone, e.g.:\n"
                  f"  git clone --no-hardlinks ../uowiki {repo_root}\n"
                  f"  git -C {repo_root} remote remove origin")
    # Safety interlock: the scripted judge FABRICATES a fixed synthetic claim
    # ("mining yields exactly 3 ore per successful swing"). Committing that into a
    # repo with a remote (the real wiki) would plant a fabricated report — exactly
    # what this whole exercise must never do. Only the real LLM judge (--live-llm)
    # produces a genuine, judge-surfaced discrepancy worth committing there.
    if args.allow_remote_repo and args.wiki_reporter == "on" and not args.live_llm:
        sys.exit(
            "refusing to run: --allow-remote-repo with the SCRIPTED judge would commit a "
            "FABRICATED synthetic claim into a repo that has a remote. Pass --live-llm to use "
            "the real LLM judge (the only judge whose report is genuine), or drop "
            "--allow-remote-repo to keep the scripted proof on a disposable clone."
        )
    _assert_no_remote(repo_root, allow_remote=args.allow_remote_repo)  # SAFETY GATE — before any filesystem write below

    wiki_root = Path(args.wiki_root) if args.wiki_root else repo_root / "src" / "content" / "docs"
    wiki = Wiki(root=wiki_root, repo_root=repo_root, report_cooldown_s=3600.0)
    print(f"wiki: root={wiki.root} repo_root={wiki.repo_root} available={wiki.available}")
    if not wiki.available:
        sys.exit(f"refusing to run: wiki root {wiki.root} doesn't exist")

    if args.live_llm:
        # Genuine loop: one real Replicate qwen client answers every LLM role.
        # Any report the judge files is then a real, LLM-surfaced discrepancy
        # over live mining episodes vs the real wiki page — not a scripted
        # fabrication. This is the only mode allowed to touch a remoted repo
        # (enforced by the interlock above).
        replicate = ReplicateClient.from_v1_config()
        if replicate is None:
            sys.exit("refusing to run: --live-llm needs the Replicate qwen client from "
                     "~/dev/uo/anima/config.yaml (or REPLICATE_API_TOKEN) — none configured.")
        goal_client = _CountingClient(replicate)
        reflect_client = _CountingClient(replicate)
        judge_client = _CountingClient(replicate)
        print(f"live-llm: real Replicate qwen client ({replicate.model}) for goal/reflection/judge roles")
    else:
        goal_client = _ScriptedGoalClient('{"say": "These veins run thin today.", "goal": "idle"}')
        reflect_client = _ScriptedReflectionClient('["A quiet, steady day at the ridge."]')
        judge_client = _CyclingJudgeClient()

    goal_cog = LLMCognition(goal_client, job="miner", wiki=wiki)
    reflect_producer = LLMReflection(reflect_client, wiki=wiki)
    wiki_reporter = LLMWikiReportProducer(judge_client) if args.wiki_reporter == "on" else None
    if wiki_reporter is None:
        judge_desc = "None (inertness leg)"
    elif args.live_llm:
        judge_desc = "LLMWikiReportProducer (REAL qwen judge — genuine discrepancies)"
    else:
        judge_desc = "LLMWikiReportProducer (scripted cycling judge — synthetic proof only)"
    print(f"wiki_reporter: {judge_desc}")

    reflecting = ReflectingCognition(
        goal_cog, reflect_producer, every_n_reconsiders=1, min_new_episodes=1,
        wiki_reporter=wiki_reporter,
    )
    cognition = ThreadedCognition(reflecting)

    with IpcBody.spawn(args.host, args.port, "animatest", "animatest", pump_ms=400) as agent_body:
        serial = agent_body.ready["player"]["serial"]
        print(f"agent: animatest serial={serial} at {agent_body.ready['player']['pos']}")

        with GmControl.spawn(args.host, args.port) as gm:
            spot = gm.setup_miner(serial)
            print(f"GM staged miner scenario at {spot}")

        agent_body.observe()  # let the teleport + pack grant settle
        agent = Agent(
            body=agent_body, persona=Persona(name="Grimm", title="a miner"),
            planner=Planner([SpeakPending(), GoTo(), Mine()]), cognition=cognition,
            cognition_interval=args.cognition_interval,
        )

        # Snapshot BEFORE ticking — reports/open/ may already hold files from a
        # prior --wiki-reporter=on run against this same disposable clone (the
        # differential-inertness leg is normally run as the 2nd of a pair, on
        # the same fixture). The inertness claim is "this run wrote nothing
        # NEW", not "the directory was empty" — comparing against `total 0`
        # would falsely fail on a paired run's own left-behind history.
        open_dir = repo_root / "reports" / "open"
        files_before = set(open_dir.glob("*.md")) if open_dir.exists() else set()

        goal_trace: list[str] = []
        for t in range(args.ticks):
            agent.tick()
            goal_trace.append(f"t{t}: goal={agent.goal} say={agent.memory.get('pending_say')!r}")
            if t % 40 == 0:
                print(f"  tick {t:3d}: episodes={agent.episodes.total_recorded} "
                      f"reward={agent.episodes.total_reward():.1f} "
                      f"goal_calls={goal_client.calls} judge_calls={judge_client.calls}")

        reflecting.wait_idle(timeout=5.0)  # let any in-flight reflection pass land

        print(f"\ndone. episodes={agent.episodes.total_recorded} "
              f"reward={agent.episodes.total_reward():.1f} "
              f"goal_calls={goal_client.calls} judge_calls={judge_client.calls}")

        files_after = set(open_dir.glob("*.md")) if open_dir.exists() else set()
        new_files = sorted(files_after - files_before)
        print(f"filed reports under {open_dir}: {len(files_after)} total, {len(new_files)} NEW this run")

        if wiki_reporter is not None:
            log = subprocess.run(["git", "-C", str(repo_root), "log", "--oneline"],
                                  capture_output=True, text=True, check=True)
            commits = log.stdout.strip().splitlines()
            print(f"disposable clone commits (git log --oneline | wc -l): {len(commits)}")
            for line in commits:
                print(f"  {line}")

            for f in new_files:
                text = f.read_text()
                page_line = next((ln for ln in text.splitlines() if ln.startswith("- page:")), "")
                page = page_line.removeprefix("- page:").strip()
                # Provenance: compare against an INDEPENDENT search call, never
                # against what the judge said (which was never even read for `page`).
                hits = wiki.search("mine miner", k=1)
                independent = hits[0].slug if hits else None
                match = "OK" if page and page == independent else "MISMATCH"
                print(f"  {f.name}: filed-page={page!r} independent-search={independent!r} [{match}]")
        else:
            assert not new_files, "wiki_reporter=off must write ZERO NEW files under reports/open/"
            print(f"wiki_reporter=off: zero NEW filesystem writes under reports/open/ this run "
                  f"({len(files_after)} pre-existing file(s) from an earlier on-leg run, untouched) "
                  "— confirmed inert.")

        print("\ngoal/speech trace (first 20 entries):")
        for line in goal_trace[:20]:
            print(f"  {line}")


if __name__ == "__main__":
    main()
