"""Live verification gate for PHASE6.md item 1 — persistent lives: disk-backed
`ReflectionMemory`, keyed per character, surviving a genuine process restart.

**The non-vacuous core.** Everything else that makes a character "the same
soul" across sessions already exists (ServUO's own server-side skills/gold,
`data/milestones.jsonl`'s own restart-survives ratchet) — this item closes
the one remaining gap: `ReflectionMemory`'s distilled `Insight`s used to
vanish the moment a `village.py` process exited. The load-bearing claim this
gate proves live, not just offline, is that a **genuinely new OS process**
can resume a specific character's insights from disk and see them in its
very first cognition prompt, before that new process has reflected even
once — see `_leg_session2` below.

A scripted reflection client (mirrors `live_wiki_report.py`'s
`_ScriptedReflectionClient`/`_CyclingJudgeClient`) always answers the same
fixed, recognizable insight string per leg, so what gets persisted is
unambiguous and can never be confused with organically-generated qwen prose
— the goal here is proving the persistence *plumbing*, not LLM prose
quality (already live-verified elsewhere: `live_reflect.py`).

**Four legs, each run as its own subprocess** (`--leg
{session1,session2,differential,inertness}`) — the literal process restart
the item's own spec calls for on session 2 ("a genuinely new process, not a
reused one"), applied uniformly to every leg here for consistency and so
every leg's own evidence is independently inspectable in its own transcript:

1. **session1** — a fresh persona (`Grimm-life1-<suffix>`), `--persist-
   insights`-equivalent wiring (`persist_path`/`agent_key` set), run long
   enough for `ReflectingCognition`'s UNCHANGED cadence defaults
   (`every_n_reconsiders=5`, `min_new_episodes=6`) to fire at least once.
2. **session2** — the SAME account/persona, a genuinely new process:
   `load_insights(...)` runs BEFORE the agent ticks even once, and the
   scripted reflection client now answers a DIFFERENT marker string. The
   assertion is on the very FIRST `reconsider()` call's prompt (tick 0,
   `Agent.tick`'s own `ticks % cognition_interval == 0` fires immediately) —
   at that point this session has recorded zero reflections of its own, so a
   match can only come from disk.
3. **differential** — a DIFFERENT persona (`Marina-life1-<suffix>`) sharing
   the same ledger file: `load_insights` must return nothing for it.
4. **inertness** — an identical scenario with persistence OFF (`insights=
   None`, the `--persist-insights`-unset default): the run genuinely
   reflects IN MEMORY (a positive control proving the engine ran) but
   `data/insights.jsonl` is byte-for-byte unchanged.

The orchestrator (`main()` with no `--leg`) spawns each leg, reads every
leg's own `[FLAG] name = value` evidence lines out of its full transcript,
and additionally does two of its own INDEPENDENT fresh-process readbacks
(`_independent_readback`, mirroring `live_trade.py::_cross_process_readback`)
straight off `data/insights.jsonl` — never trusting a leg's own in-process
say-so for the persistence claim itself. `data/insights.jsonl` is cleared at
the start of the run (mirrors `live_trade.py::_run_tuner`'s own "clear the
ledger first" convention) so every assertion reflects only this gate's run.

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_persistent_lives [--ticks N] [--host H] [--port P]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from .agent import Agent
from .cognition import LLMCognition, LLMReflection, ReflectingCognition, ThreadedCognition
from .control import GmControl
from .ipc_body import IpcBody
from .live_common import GM_RELOGIN_COOLDOWN_S, RecordingBody, fresh_suffix, print_gate_verdict, wipe_area
from .memory import load_insights
from .persona import Persona
from .planner import Planner
from .skills import GoTo, Mine, SpeakPending

#: `data/insights.jsonl` relative to the process's cwd — same constant
#: `village.py::INSIGHTS_PATH` uses; this script talks to the same real file
#: (cleared at the top of every gate run, see `main()`).
INSIGHTS_PATH = Path("data") / "insights.jsonl"

# Fixed, recognizable marker strings — one per leg — so what ends up on disk
# (or doesn't) can never be confused with organically-generated text.
SESSION1_INSIGHT = "The east vein pays better in the morning."
SESSION2_INSIGHT = "Storms brew over Trinsic tonight."
DIFFERENTIAL_INSIGHT = "Fish scatter when the tide turns."
INERTNESS_INSIGHT = "Rains soak the ridge by evening."

#: Bounded retries for the two legs whose evidence needs a genuine live
#: reflection to fire (session1, inertness) — this project's own
#: `anima2-harvest-freeze` memory note records an intermittent (~1/3-1/2 runs)
#: Mine/Harvest live freeze unrelated to this item's own code; a wedged
#: attempt is retried on a fresh account rather than treated as a gate
#: failure, mirroring `live_trade.py::_run_tuner`'s own live-wedge retry.
MAX_REFLECT_ATTEMPTS = 3

MINING_SPOT = (2567, 493)  # GmControl.setup_miner's own default (Minoc ridge)


class _ScriptedChatterClient:
    """Deterministic goal/speech client (mirrors `live_wiki_report.py`'s
    `_ScriptedGoalClient`) that ALSO records every situation prompt it's
    asked to complete — this script's evidence that a persisted insight
    actually reached `LLMCognition`'s "Lessons learned" line, not just that
    `load_insights()` itself parses correctly (already covered offline,
    `tests/test_memory.py`)."""

    def __init__(self, reply: str = '{"say": "Quiet work today.", "goal": "idle"}') -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        return self.reply


class _ScriptedReflectionClient:
    """Deterministic `LLMClient`-shaped stand-in for the reflection role:
    always replies with the same one fixed, recognizable insight string
    (mirrors `live_wiki_report.py`'s `_ScriptedReflectionClient`, generalized
    to take the marker text as a constructor arg since each leg below needs
    its own distinct marker). Raw `.complete()` shape, same as any other
    `LLMClient` — wrap in `cognition.LLMReflection` to get an actual
    `ReflectionProducer` (`.reflect(episodes, persona)`), exactly like every
    other caller in this codebase; `ReflectingCognition`'s `reflection`
    parameter is a `ReflectionProducer`, not a bare `LLMClient`."""

    def __init__(self, insight_text: str) -> None:
        self.insight_text = insight_text
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return json.dumps([self.insight_text])


def _stage_miner(gm: GmControl, serial: int, name: str) -> tuple[int, int, int]:
    spot = gm.setup_miner(serial, *MINING_SPOT)
    gm.command_on(f'[Set Name "{name}"', serial)
    return spot


def _run_leg(
    args: argparse.Namespace,
    *,
    account: str,
    persona_name: str,
    persist: bool,
    reflection_text: str,
    ticks: int,
) -> dict:
    """One staged live session: log in `account`, GM-stage a miner at the
    calibrated Minoc ridge spot under `persona_name`, run a scripted
    `ThreadedCognition(ReflectingCognition(...))` for `ticks`, and return the
    evidence every leg-specific caller below needs. `persist=True` wires
    `insights=load_insights(INSIGHTS_PATH, persona_name)` — read from disk
    BEFORE the agent's first tick — exactly `village.py --persist-insights`'s
    own wiring; `persist=False` leaves `insights=None`, `ReflectingCognition`'s
    own in-memory-only default (the inertness leg).
    """
    with IpcBody.spawn(args.host, args.port, account, account, pump_ms=400) as agent_body:
        serial = agent_body.ready["player"]["serial"]
        print(f"[{persona_name}] account={account} serial={serial} pos={agent_body.ready['player']['pos']}")

        with GmControl.spawn(args.host, args.port) as gm:
            gm.hide()
            spot = _stage_miner(gm, serial, persona_name)
            print(f"[{persona_name}] GM staged miner at {spot}")

        body = RecordingBody(agent_body)
        body.observe()  # let the teleport + pack grant settle

        # THE non-vacuous core for session2 (and the isolation proof for
        # differential): this read happens BEFORE the Agent/cognition below
        # is even constructed, let alone ticked.
        insights = load_insights(INSIGHTS_PATH, persona_name) if persist else None
        preloaded_count = len(insights) if insights is not None else 0
        print(f"[{persona_name}] preloaded insights from disk BEFORE first tick: {preloaded_count} "
              f"(persist={persist})")

        chatter = _ScriptedChatterClient()
        reflection_client = _ScriptedReflectionClient(reflection_text)
        reflection_producer = LLMReflection(reflection_client)
        # every_n_reconsiders/min_new_episodes: UNCHANGED ReflectingCognition
        # class defaults (5, 6) — per PHASE6.md item 1's own live-gate spec,
        # deliberately not overridden here the way live_reflect.py's demo does.
        reflecting = ReflectingCognition(LLMCognition(chatter, job="miner"), reflection_producer,
                                          insights=insights)
        cognition = ThreadedCognition(reflecting)

        agent = Agent(
            body=body, persona=Persona(name=persona_name, title="a miner"),
            planner=Planner([SpeakPending(), GoTo(), Mine()]), cognition=cognition,
            cognition_interval=args.cognition_interval,
        )

        first_prompt: str | None = None
        for t in range(ticks):
            agent.tick()
            if first_prompt is None and chatter.prompts:
                first_prompt = chatter.prompts[0]
            if t % 30 == 0:
                print(f"[{persona_name}] tick {t:3d}: episodes={agent.episodes.total_recorded} "
                      f"reward={agent.episodes.total_reward():.1f} "
                      f"reflect_calls={reflection_client.calls} insights_in_memory={len(reflecting.insights)}")

        reflecting.wait_idle(timeout=5.0)  # let any in-flight reflection pass land before we exit

        result = {
            "persona_name": persona_name,
            "account": account,
            "episodes_recorded": agent.episodes.total_recorded,
            "reward": agent.episodes.total_reward(),
            "reflect_calls": reflection_client.calls,
            "insights_in_memory": len(reflecting.insights),
            "preloaded_count": preloaded_count,
            "first_prompt": first_prompt or "",
        }
        print(f"[{persona_name}] done. episodes={result['episodes_recorded']} "
              f"reward={result['reward']:.1f} reflect_calls={result['reflect_calls']} "
              f"insights_in_memory={result['insights_in_memory']}")
        print(f"[{persona_name}] first reconsider prompt (tick 0): {result['first_prompt']!r}")
        return result


# --- leg entry points (each runs as its own standalone `--leg ...` subprocess) --------


def _leg_session1(args: argparse.Namespace) -> None:
    account = f"life1{args.suffix}a{args.attempt}"
    persona = f"Grimm-life1-{args.suffix}-a{args.attempt}"
    result = _run_leg(args, account=account, persona_name=persona, persist=True,
                       reflection_text=SESSION1_INSIGHT, ticks=args.ticks)
    reflected = result["reflect_calls"] > 0 and result["insights_in_memory"] > 0
    # Session1 does NOT itself assert the disk write landed — that's the
    # orchestrator's own independent fresh-process readback (see
    # `_independent_readback`), never a leg's own say-so about its own write.
    print(f"[FLAG] session1_reflected_in_memory = {reflected}")


def _leg_session2(args: argparse.Namespace) -> None:
    account = f"life1{args.suffix}a{args.attempt}"        # SAME account as session1
    persona = f"Grimm-life1-{args.suffix}-a{args.attempt}"  # SAME persona/agent_key as session1
    result = _run_leg(args, account=account, persona_name=persona, persist=True,
                       reflection_text=SESSION2_INSIGHT, ticks=args.session2_ticks)
    preloaded = result["preloaded_count"] > 0
    has_session1_marker = SESSION1_INSIGHT in result["first_prompt"]
    lacks_session2_own_marker = SESSION2_INSIGHT not in result["first_prompt"]
    print(f"[FLAG] session2_preloaded_from_disk_before_first_tick = {preloaded}")
    print(f"[FLAG] session2_first_tick_prompt_has_session1_insight = {has_session1_marker}")
    print(f"[FLAG] session2_first_tick_prompt_lacks_session2_own_insight = {lacks_session2_own_marker}")


def _leg_differential(args: argparse.Namespace) -> None:
    account = f"life2{args.suffix}"
    persona = f"Marina-life1-{args.suffix}"  # a DIFFERENT persona, same shared ledger file
    result = _run_leg(args, account=account, persona_name=persona, persist=True,
                       reflection_text=DIFFERENTIAL_INSIGHT, ticks=args.session2_ticks)
    isolated = result["preloaded_count"] == 0 and SESSION1_INSIGHT not in result["first_prompt"]
    print(f"[FLAG] differential_cross_persona_isolated = {isolated}")


def _leg_inertness(args: argparse.Namespace) -> None:
    account = f"life3{args.suffix}a{args.attempt}"
    persona = f"Grimm-life2-{args.suffix}-a{args.attempt}"  # fresh, distinct persona; never persisted
    result = _run_leg(args, account=account, persona_name=persona, persist=False,
                       reflection_text=INERTNESS_INSIGHT, ticks=args.ticks)
    reflected = result["reflect_calls"] > 0 and result["insights_in_memory"] > 0
    print(f"[FLAG] inertness_leg_reflected_in_memory = {reflected}")


_LEGS = {
    "session1": _leg_session1,
    "session2": _leg_session2,
    "differential": _leg_differential,
    "inertness": _leg_inertness,
}


# --- orchestrator: spawns every leg as its own fresh process, cross-checks disk -------


def _independent_readback(agent_key: str) -> dict:
    """Spawn a FRESH, independent Python process that imports `load_insights`
    and reads `INSIGHTS_PATH` from disk for `agent_key` — never this script's
    own memory, and never a leg subprocess's own memory either. Mirrors
    `live_trade.py::_cross_process_readback`'s identical discipline for item
    4's own ledger, applied here to item 1's."""
    script = (
        "import json\n"
        "from anima2.memory import load_insights\n"
        f"mem = load_insights({str(INSIGHTS_PATH)!r}, {agent_key!r})\n"
        "print(json.dumps({'count': len(mem), 'texts': [i.text for i in mem.recent(20)]}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          timeout=30, check=False)
    if proc.returncode != 0:
        print(f"  independent readback FAILED (exit {proc.returncode}): {proc.stderr.strip()}")
        return {"count": 0, "texts": []}
    try:
        return json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        print(f"  independent readback produced unparseable output: {proc.stdout!r}")
        return {"count": 0, "texts": []}


def _run_leg_subprocess(args: argparse.Namespace, leg: str) -> str:
    """Spawn THIS SAME SCRIPT as a genuinely new OS process for one leg — the
    literal process restart PHASE6.md item 1's own spec calls for on session
    2 ("a genuinely new process, not a reused one"), applied uniformly to
    every leg here. Prints the FULL transcript (never piped through tail —
    house convention) and returns it so the orchestrator can pull `[FLAG]`
    lines back out."""
    cmd = [sys.executable, "-m", "anima2.live_persistent_lives", "--leg", leg,
           "--host", args.host, "--port", str(args.port), "--suffix", args.suffix,
           "--attempt", str(args.attempt), "--ticks", str(args.ticks),
           "--session2-ticks", str(args.session2_ticks),
           "--cognition-interval", str(args.cognition_interval)]
    print(f"\n=== spawning fresh process for leg={leg!r}: {' '.join(cmd)} ===")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.leg_timeout, check=False)
    print(proc.stdout)
    if proc.stderr.strip():
        print(f"--- leg={leg!r} stderr ---\n{proc.stderr}")
    if proc.returncode != 0:
        sys.exit(f"leg {leg!r} subprocess FAILED (exit {proc.returncode})")
    return proc.stdout


def _flag_from_output(output: str, name: str) -> bool:
    marker = f"[FLAG] {name} = "
    for line in output.splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip() == "True"
    return False


def _run_leg_with_retries(args: argparse.Namespace, leg: str, flag_name: str) -> tuple[bool, str]:
    """Run `leg` up to `MAX_REFLECT_ATTEMPTS` times, bumping `args.attempt`
    (and thus that leg's own account/persona names) on each retry, until its
    own `[FLAG] {flag_name}` comes back True — see module docstring's note on
    the known intermittent Mine/Harvest live freeze this guards against."""
    out = ""
    for attempt in range(MAX_REFLECT_ATTEMPTS):
        args.attempt = attempt
        out = _run_leg_subprocess(args, leg)
        if _flag_from_output(out, flag_name):
            return True, out
        more = attempt + 1 < MAX_REFLECT_ATTEMPTS
        print(f"leg={leg!r} attempt {attempt}: {flag_name}=False within {args.ticks} ticks "
              f"(the known intermittent Mine/Harvest live freeze — see the anima2-harvest-freeze "
              f"memory note, unrelated to this item's own code) — "
              + ("retrying with a fresh account" if more else "exhausted retries"))
    return False, out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", choices=tuple(_LEGS), default=None,
                     help="run exactly one leg standalone (used internally by the orchestrator's own "
                          "subprocess spawns); omit to run the full 4-leg gate")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--ticks", type=int, default=150,
                     help="session1/inertness: ticks to run (long enough for ReflectingCognition's "
                          "UNCHANGED default cadence — every_n_reconsiders=5, min_new_episodes=6 — "
                          "to fire at least once)")
    ap.add_argument("--session2-ticks", type=int, default=30,
                     help="session2/differential: ticks to run (only the very first tick's prompt is "
                          "load-bearing; a few more give a fuller transcript)")
    ap.add_argument("--cognition-interval", type=int, default=6)
    ap.add_argument("--suffix", default=None, help="account/persona suffix (default: fresh, time-derived)")
    ap.add_argument("--attempt", type=int, default=0,
                     help="internal: retry counter folded into account/persona names (see "
                          "_run_leg_with_retries) — not normally passed by hand")
    ap.add_argument("--leg-timeout", type=int, default=600, help="orchestrator only: seconds per leg subprocess")
    args = ap.parse_args()
    if args.suffix is None:
        args.suffix = fresh_suffix()

    if args.leg is not None:
        _LEGS[args.leg](args)
        return

    # --- orchestrator: the full 4-leg gate -------------------------------------
    print("=== PHASE6.md item 1 live gate: persistent lives (disk-backed ReflectionMemory) ===")
    print(f"suffix={args.suffix} insights_path={INSIGHTS_PATH.resolve()}")

    with GmControl.spawn(args.host, args.port) as gm:
        gm.hide()
        wipe_area(gm, *MINING_SPOT, radius=15)
    print("staging area wiped.\n")

    INSIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Clear the ledger first (mirrors live_trade.py::_run_tuner's own
    # convention) so every assertion below reflects ONLY this gate's own run,
    # not leftover state from an earlier one — data/insights.jsonl is
    # gitignored, disposable.
    INSIGHTS_PATH.write_text("")
    print(f"cleared {INSIGHTS_PATH.resolve()} for a clean run.")

    # --- session 1 ---------------------------------------------------------------
    session1_reflected, out1 = _run_leg_with_retries(args, "session1", "session1_reflected_in_memory")
    grimm_key = f"Grimm-life1-{args.suffix}-a{args.attempt}"

    print("\n=== independent readback #1: a FRESH `python -c` process reads data/insights.jsonl ===")
    readback1 = _independent_readback(grimm_key)
    print(f"  agent_key={grimm_key!r}: {readback1}")
    session1_persisted = readback1["count"] >= 1 and SESSION1_INSIGHT in readback1["texts"]

    time.sleep(GM_RELOGIN_COOLDOWN_S)

    # --- session 2: SAME account/persona (args.attempt unchanged), a genuinely new process ---
    out2 = _run_leg_subprocess(args, "session2")
    session2_preloaded = _flag_from_output(out2, "session2_preloaded_from_disk_before_first_tick")
    session2_has_s1 = _flag_from_output(out2, "session2_first_tick_prompt_has_session1_insight")
    session2_lacks_own = _flag_from_output(out2, "session2_first_tick_prompt_lacks_session2_own_insight")

    time.sleep(GM_RELOGIN_COOLDOWN_S)

    # --- differential: a DIFFERENT persona, same shared ledger --------------------
    out3 = _run_leg_subprocess(args, "differential")
    differential_isolated = _flag_from_output(out3, "differential_cross_persona_isolated")
    marina_key = f"Marina-life1-{args.suffix}"
    print("\n=== independent readback #2: differential persona's own ledger rows ===")
    readback_marina = _independent_readback(marina_key)
    print(f"  agent_key={marina_key!r}: {readback_marina}")
    marina_never_sees_grimm = SESSION1_INSIGHT not in readback_marina["texts"]

    time.sleep(GM_RELOGIN_COOLDOWN_S)

    # --- differential-inertness: identical scenario, persistence OFF --------------
    before_bytes = INSIGHTS_PATH.read_bytes()
    inertness_reflected, out4 = _run_leg_with_retries(args, "inertness", "inertness_leg_reflected_in_memory")
    after_bytes = INSIGHTS_PATH.read_bytes()
    inertness_disk_unchanged = before_bytes == after_bytes

    flags = {
        "session1_reflected_in_memory": session1_reflected,
        "session1_insight_persisted_to_disk_fresh_process_readback": session1_persisted,
        "session2_preloaded_from_disk_before_first_tick": session2_preloaded,
        "session2_first_tick_prompt_has_session1_insight": session2_has_s1,
        "session2_first_tick_prompt_lacks_session2_own_insight": session2_lacks_own,
        "differential_cross_persona_isolated": differential_isolated,
        "differential_marina_never_sees_grimm_insight_fresh_readback": marina_never_sees_grimm,
        "inertness_leg_reflected_in_memory_positive_control": inertness_reflected,
        "inertness_leg_disk_unchanged": inertness_disk_unchanged,
    }
    print_gate_verdict(flags, label="PHASE6_ITEM1_PERSISTENT_LIVES")


if __name__ == "__main__":
    main()
