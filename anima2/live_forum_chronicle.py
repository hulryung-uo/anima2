"""Live verification gate: the forum as continuing chronicle (PHASE6.md item 3).

Reuses `live_chronicle.py`'s exact staged miner+blacksmith trade scenario
(`MAX_WEDGE_ATTEMPTS`/`STALL_TICKS`/`_SOLO_MINE_SPOT_POOL`/
`_attempt_ledger_path`, imported directly, not reimplemented) — but as its
own **standalone driver**, mirroring `live_persistent_lives.py`'s/
`live_chronicle.py`'s own "standalone driver, not a `village.py` CLI
wrapper" precedent (`village.py`'s roster login hardcodes `anima{i}` account
names with no override, and this gate needs fresh accounts per leg). The
REAL shipped functions are exercised directly — this script's tick loop
calls the exact `village._chronicle_events_this_tick`/`village.
_accumulate_deliver_reward`/`chronicle.ChronicleLedger.queue_event` PHASE6.md
item 2 already live-verified (never reimplemented, and never re-proven
correct here — that's item 2's own gate's job), and the forum posts
themselves go through the REAL, unmodified `forum.post_day`/
`forum.compose_post_llm`, with a REAL `ForumClient` (the live uotavern API —
"the remote POST itself is still exercised for real", per this item's own
Scope) and the REAL Replicate qwen client (`llm.py::ReplicateClient.
from_v1_config()`, the only live LLM available in this environment).

**Session-scoped grounding, exactly `village.py`'s own new wiring.** Each
session's tick loop collects the MINER's own `ChronicleEvent`s into a local
`session_events` list (the exact same `chronicle_ledger.queue_event(...)`
return-value pattern `village.py::_run_worker` now uses — PHASE6.md item 3's
own small, additive `queue_event() -> ChronicleEvent` change) rather than
reading `data/chronicle.jsonl` back with a `since_tick` heuristic — sidesteps
any ambiguity from a shared ledger file persisting across many gate runs.

**Reflection: a scripted client, not qwen — deliberately, mirroring item 1's
own justification restated here.** `live_persistent_lives.py`'s own module
docstring: "the goal here is proving the persistence *plumbing*, not LLM
prose quality (already live-verified elsewhere)." The forum PROSE itself
still goes through the real qwen client (see above) — only the REFLECTION
step (which produces the one insight this gate's session2 leg must find
verbatim on disk) uses a fixed, recognizable marker string
(`SESSION1_INSIGHT`), via the REAL `cognition.LLMReflection`/
`ReflectingCognition`/`memory.ReflectionMemory(persist_path=...)` — never a
reimplementation of the persistence write path itself (that's item 1's own,
already live-verified, gate).

**Four legs:**

1. **session1** (paired miner+blacksmith, chronicle ON, reflection ON):
   stops once at least one confirmed `delivered_ingots` event AND at least
   one reflected insight have landed (or the tick budget/stall guard gives
   up). Posts to the REAL forum via the REAL `forum.post_day`, grounded in
   this session's own real `session_events` — `yesterday=None` (nothing
   precedes session1). The decisive check: a **fresh subprocess** reads
   `data/forum_log_gate_<suffix>_paired.jsonl` and confirms the miner's most
   recently posted content contains the blacksmith's exact persona name.
2. **differential (solo)**: a solo miner (no blacksmith paired, drawn from
   `_SOLO_MINE_SPOT_POOL` so it never competes with session1 for the same
   draining bank — PHASE6.md item 2's own bank-drain lesson), chronicle
   still ON (the machinery is genuinely active, just structurally has no
   counterpart to report — the same "real, not vacuous" proof item 2's own
   leg B established). The negative-control half of the SAME grounding
   claim: its post must name neither the paired session's miner nor
   blacksmith — proving the mention above was earned by a real event, not a
   stock phrase the composer always includes.
3. **inertness** (paired again, item 1/2's flags both OFF — chronicle
   disabled entirely, no reflection wired): posts via the REAL `forum.
   post_day` with `yesterday=None, chronicle_events=None` explicitly, the
   exact defaults every pre-item-3 `--forum` run already used — the posted
   content must carry none of the item-3-specific tells ("Yesterday", or any
   `forum._CHRONICLE_VERB` phrase).
4. **session2** (subprocess re-exec, `--leg session2`, the ONE leg that
   genuinely needs to be "a new process, not a reused one" per the item's
   own spec wording — no live ServUO connection at all, since the property
   under test is prompt CONSTRUCTION, not live play): loads session1's own
   `data/insights_gate_<suffix>.jsonl` via the REAL `memory.load_insights`,
   and asserts the resulting "yesterday" text reaches a scripted, PROMPT-
   CAPTURING `LLMClient`'s `user` argument via `forum.compose_post_llm` —
   "prove the request was built correctly," the same standard Phase 4 item
   2's own prompt-cache-control gate used, rather than grading the model's
   prose quality.

**Bug caught by this gate's own first run — in this script, never in the
shipped code.** The first draft posted from EVERY attempt `_run_forum_
session` made, including one the retry wrapper (`_run_forum_session_with_
retry`) was about to discard as a stalled live wedge (bank exhaustion at the
shared, non-rotatable `TRADE_MINE_SPOT` — the exact PHASE6.md item 2 "Bank-
drain resilience" pattern, hit again here). The discarded attempt's
incomplete data still reached the REAL, live forum before being thrown away
— an extra, harmless-but-wasteful real post, not a false-positive risk (the
decisive readback always reads the WINNING attempt's own, chronologically
LATEST post), but sloppy: a session the gate itself judges "not a real
signal" has no business publishing to a live, shared service on that
signal's strength. Fixed by gating the post on `not stalled`, mirroring item
2's own "a discarded attempt's data must never bleed into the winning
attempt's own evidence" lesson, applied here to a live side effect instead
of a ledger file.

**A second bug, also caught by this gate's own live runs, also in this
script only.** The inertness leg (chronicle OFF, paired at the same
non-rotatable `TRADE_MINE_SPOT` session1 already mined) had no delivery
signal to stop on at all, so it ran unbounded toward `session_ticks`/
`STALL_TICKS` — directly competing with session1's own draw on the same
real bank (10-20 minute real respawn, per `live_evolve_gate.py`'s own module
docstring — far longer than this gate's cooldowns) within one gate
invocation. All 3 retry attempts wedged on a real run. Fixed by stopping
this leg as soon as it has a modest, clearly-positive episode count (the
only thing its own "engine still ran" flag actually needs), rather than
mining far more than necessary.

**Not run by this script — bundled into item 3's spec as a separate,
human-supervised, one-time action:** the real `../uowiki` write check.
`live_wiki_report.py`'s `_assert_no_remote` unconditionally refuses ANY
target with a configured git remote, and `../uowiki` genuinely has one
(`origin` -> a real GitHub repo) — so running that script verbatim against
the real repo, as the spec's own prose describes, is structurally
impossible without weakening a safety check that exists specifically to
prevent exactly this. That is a deliberate design tension worth a human
decision, not something this script resolves unilaterally; see PHASE6.md
item 3's "As landed" note for the full writeup.

Requires a running ServUO and the built bridge (`cargo build -p anima-net`),
a configured forum (`ANIMA_FORUM_API_KEY` or `../anima/config.yaml`), and a
configured Replicate client (`../anima/config.yaml`'s `llm:` section).
Usage: python -m anima2.live_forum_chronicle [--ticks N] [--host H] [--port P]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from .agent import Agent
from .chronicle import ChronicleEvent, ChronicleLedger
from .cognition import HeuristicCognition, LLMReflection, ReflectingCognition, ThreadedCognition
from .control import GmControl
from .forum import ForumClient, compose_post_llm, post_day
from .ipc_body import IpcBody
from .live_chronicle import MAX_WEDGE_ATTEMPTS, STALL_TICKS, _SOLO_MINE_SPOT_POOL, _attempt_ledger_path
from .live_common import (
    GM_RELOGIN_COOLDOWN_S,
    RecordingBody,
    fresh_suffix,
    login_throttle,
    print_gate_verdict,
    wipe_bounds,
)
from .llm import ReplicateClient, StubLLMClient
from .memory import ReflectionMemory, load_insights
from .persona import Persona
from .planner import Planner
from .profession import TRADE_MINE_SPOT, TRADE_SMITH_SPOT
from .skills import Blacksmith
from .skills.smelt import MineSmeltDeliver
from .village import _accumulate_deliver_reward, _chronicle_events_this_tick, _pack_ingot_count

WIPE_RADIUS = 10
#: The item's own Scope wording: "at least one confirmed delivery event" —
#: a lower bar than item 2's own gate (`MIN_DELIVERIES = 2` there), since
#: item 3 only needs ONE real event to ground a post, not a multi-cycle
#: accounting proof (that's item 2's own, already-settled, job).
MIN_DELIVERIES = 1
#: A fixed, recognizable marker — distinct from item 1's own gate markers
#: (`SESSION1_INSIGHT` etc. in `live_persistent_lives.py`) so a readback can
#: never be confused with a stray insight from a different gate sharing the
#: same `data/` directory.
SESSION1_INSIGHT = "Tormund pays fair for a clean haul."


def _run_forum_session(
    args: argparse.Namespace, *, label: str, miner_account: str, smith_account: str | None,
    miner_persona: str, smith_persona: str | None, session_ticks: int,
    chronicle_path: Path | None, reflect_insights_path: Path | None,
    forum_log_path: Path, forum_client: ForumClient, llm_client,
    mine_spot: tuple[int, int] = TRADE_MINE_SPOT,
) -> dict:
    """One staged session — mirrors `live_chronicle.py::_run_trade_session`'s
    own staging exactly, extended with: (a) collecting the miner's OWN
    `ChronicleEvent`s into a local `session_events` list (via `queue_event`'s
    return value — see the module docstring), (b) optionally wiring a
    scripted-client `ReflectingCognition` for the miner so a real insight
    gets persisted via item 1's own `ReflectionMemory(persist_path=...)`
    write path, and (c) calling the REAL `forum.post_day` once the session
    ends, before the IpcBody connections close.
    """
    paired = smith_account is not None
    mx, my = mine_spot
    chronicle_ledger = ChronicleLedger(ledger_path=chronicle_path) if chronicle_path is not None else None
    reflect = reflect_insights_path is not None
    print(f"\n=== {label}: miner={miner_account} paired={paired} mine_spot=({mx},{my}) "
          f"chronicle={'ON' if chronicle_ledger else 'OFF'} reflect={'ON' if reflect else 'OFF'} ===")

    with IpcBody.spawn(args.host, args.port, miner_account, miner_account, pump_ms=400) as miner_ipc:
        miner_serial = miner_ipc.ready["player"]["serial"]
        login_throttle(args.stagger)

        smith_ipc = IpcBody.spawn(args.host, args.port, smith_account, smith_account, pump_ms=400) \
            if paired else None
        try:
            smith_serial = smith_ipc.ready["player"]["serial"] if smith_ipc is not None else None

            with GmControl.spawn(args.host, args.port) as gm:
                gm.hide()
                if paired:
                    sx, sy = TRADE_SMITH_SPOT
                    x1, y1 = min(mx, sx) - WIPE_RADIUS, min(my, sy) - WIPE_RADIUS
                    x2, y2 = max(mx, sx) + WIPE_RADIUS, max(my, sy) + WIPE_RADIUS
                else:
                    x1, y1, x2, y2 = mx - WIPE_RADIUS, my - WIPE_RADIUS, mx + WIPE_RADIUS, my + WIPE_RADIUS
                wipe_bounds(gm, x1, y1, x2, y2)

                mgx, mgy, mgz = gm.stage(miner_serial, mx, my, skills={"Mining": 35},
                                         items=["Pickaxe", "Pickaxe"])
                gm.command_at("[Add Forge", mgx + 1, mgy + 1, mgz)
                gm.command_on(f'[Set Name "{miner_persona}"', miner_serial)
                print(f"GM staged miner at ({mgx},{mgy},{mgz}) + forge")

                if paired:
                    sgx, sgy, sgz = gm.stage(smith_serial, sx, sy, skills={"Blacksmith": 35},
                                             items=["SmithHammer 999", f"IronIngot {args.smith_ingots}"])
                    gm.command_at("[Add Forge", sgx, sgy - 1, sgz)
                    gm.command_at("[Add Anvil", sgx, sgy + 1, sgz)
                    gm.command_on(f'[Set Name "{smith_persona}"', smith_serial)
                    print(f"GM staged blacksmith at ({sgx},{sgy},{sgz}) + forge/anvil, "
                          f"{args.smith_ingots} starting ingots")

            miner_body = RecordingBody(miner_ipc)
            miner_body.observe()
            miner_body.observe()

            miner_skill = MineSmeltDeliver()
            miner_skill.deliver_threshold = args.deliver_threshold

            insights_mem: ReflectionMemory | None = None
            miner_cognition = None
            if reflect:
                scripted = StubLLMClient(json.dumps([SESSION1_INSIGHT]))
                insights_mem = ReflectionMemory(persist_path=reflect_insights_path, agent_key=miner_persona)
                miner_cognition = ThreadedCognition(ReflectingCognition(
                    HeuristicCognition(), LLMReflection(scripted),
                    insights=insights_mem, every_n_reconsiders=1, min_new_episodes=1,
                ))
            miner = Agent(body=miner_body, persona=Persona(name=miner_persona), planner=Planner([miner_skill]),
                          cognition=miner_cognition, cognition_interval=5)
            if paired:
                miner.memory["smithy_drop"] = TRADE_SMITH_SPOT

            smith = None
            smith_body = None
            if paired:
                smith_body = RecordingBody(smith_ipc)
                smith_body.observe()
                smith_body.observe()
                smith = Agent(body=smith_body, persona=Persona(name=smith_persona), planner=Planner([Blacksmith()]))

            # PHASE6.md item 3: this MINER's own session-scoped grounding
            # events — mirrors `village.py::_run_worker`'s own `session_events`
            # accumulation exactly (the real `queue_event` return value, never
            # reconstructed). The blacksmith's own events aren't collected
            # here — this gate only posts on the miner's behalf.
            session_events: list[ChronicleEvent] = []
            real_deliver_phase_reward = 0.0
            prev_miner_memory: dict = dict(miner.memory)
            prev_miner_recorded = miner.episodes.total_recorded
            prev_smith_memory: dict = dict(smith.memory) if smith is not None else {}
            prev_smith_recorded = smith.episodes.total_recorded if smith is not None else 0
            fetch_entry_ingots: int | None = None
            deliveries = 0
            last_progress_tick = 0
            stalled = False

            t = 0
            for t in range(session_ticks):
                miner.tick()
                new_miner_episode = None
                if miner.episodes.total_recorded > prev_miner_recorded:
                    new_miner_episode = miner.episodes.recent(1)[0]
                    last_progress_tick = t

                # The REAL chronicle path — the exact functions
                # village.py::_run_worker calls, and the exact ChronicleLedger.
                real_deliver_phase_reward = _accumulate_deliver_reward(
                    real_deliver_phase_reward, prev_miner_memory, new_miner_episode,
                )
                if chronicle_ledger is not None:
                    for kind, to_persona, amount in _chronicle_events_this_tick(
                        "miner", smith_persona if paired else None,
                        prev_miner_memory, miner.memory, new_miner_episode,
                        fetch_entry_ingots=None, pack_ingots_now=0,
                        deliver_phase_reward=real_deliver_phase_reward,
                    ):
                        event = chronicle_ledger.queue_event(tick=miner.ticks, from_persona=miner_persona,
                                                             to_persona=to_persona, kind=kind, amount=amount)
                        session_events.append(event)
                        if kind == "delivered_ingots":
                            deliveries += 1
                            print(f"  {label} tick {t:4d}: [chronicle] delivered_ingots amount={amount:.1f} "
                                  f"(deliveries so far: {deliveries})")
                if miner.memory.get("smelt_phase") != "deliver":
                    real_deliver_phase_reward = 0.0
                prev_miner_memory = dict(miner.memory)
                prev_miner_recorded = miner.episodes.total_recorded

                if smith is not None:
                    smith.tick()
                    sobs = smith_body.last_obs
                    assert sobs is not None
                    new_smith_episode = None
                    if smith.episodes.total_recorded > prev_smith_recorded:
                        new_smith_episode = smith.episodes.recent(1)[0]
                    pack_ingots_now = _pack_ingot_count(sobs)
                    if prev_smith_memory.get("bs_state") != "fetch" and smith.memory.get("bs_state") == "fetch":
                        fetch_entry_ingots = pack_ingots_now
                    if chronicle_ledger is not None:
                        for kind, to_persona, amount in _chronicle_events_this_tick(
                            "blacksmith", miner_persona, prev_smith_memory, smith.memory, new_smith_episode,
                            fetch_entry_ingots=fetch_entry_ingots, pack_ingots_now=pack_ingots_now,
                        ):
                            chronicle_ledger.queue_event(tick=smith.ticks, from_persona=smith_persona,
                                                         to_persona=to_persona, kind=kind, amount=amount)
                    if smith.memory.get("bs_state") != "fetch":
                        fetch_entry_ingots = None
                    prev_smith_memory = dict(smith.memory)
                    prev_smith_recorded = smith.episodes.total_recorded

                if t % 100 == 0:
                    print(f"  {label} tick {t:4d}: SNAPSHOT deliveries={deliveries} "
                          f"miner_episodes={miner.episodes.total_recorded} "
                          f"insights={len(insights_mem) if insights_mem is not None else 0}")

                # The inertness leg (chronicle OFF) has no delivery signal to
                # stop on at all — left unbounded, it mines the shared,
                # non-rotatable TRADE_MINE_SPOT all the way to `session_ticks`
                # or a stall, competing hard with session1's own draw on the
                # same bank (real respawn is 10-20 minutes — `live_evolve_
                # gate.py`'s own module docstring — far longer than this
                # gate's own cooldowns). It only ever needs a POSITIVE episode
                # count (the "engine still ran" control), so it stops as soon
                # as it has a modest, clearly-non-zero one — live-caught after
                # this gate's own first attempt at this leg wedged on all 3
                # retries immediately following session1's own mining here.
                no_chronicle_positive_control = (
                    chronicle_ledger is None and miner.episodes.total_recorded >= 10
                )
                if (deliveries >= MIN_DELIVERIES and (not reflect or len(insights_mem) >= 1)) \
                        or no_chronicle_positive_control:
                    reason = "no chronicle tracking, but a clear positive episode count" \
                        if no_chronicle_positive_control else (
                            f"reached {MIN_DELIVERIES} confirmed delivery/ies"
                            + (" and >=1 reflected insight" if reflect else ""))
                    print(f"\n{label}: {reason} by tick {t} — stopping.")
                    break
                if t - last_progress_tick > STALL_TICKS:
                    stalled = True
                    print(f"\n{label}: no new miner episode for {STALL_TICKS} ticks — "
                          "likely resource-bank exhaustion — giving up this attempt.")
                    break

            n_flushed = chronicle_ledger.flush() if chronicle_ledger is not None else 0
            if chronicle_ledger is not None:
                print(f"  {label}: flushed {n_flushed} chronicle event(s) to {chronicle_path}")
            insight_recorded = reflect and len(insights_mem) >= 1
            if reflect:
                print(f"  {label}: reflection insights recorded this session: "
                      f"{len(insights_mem) if insights_mem is not None else 0}")

            # --- the forum post: the REAL forum.post_day, REAL client -------
            # A STALLED attempt (bank exhaustion, a live wedge — see STALL_TICKS
            # above) is about to be discarded by the retry wrapper as "not a
            # real signal" (mirrors PHASE6.md item 2's own "retry isolation"
            # lesson: a discarded attempt's data must never bleed into the
            # winning attempt's own evidence) — it must not ALSO publish a
            # real post to the live forum on the strength of that same
            # incomplete data. Only a genuinely completed session posts.
            post_result = None
            if miner.episodes.total_recorded > 0 and not stalled:
                post_result = post_day(
                    miner, job="miner", client=forum_client, llm=llm_client,
                    yesterday=None,  # this leg's own session never has a "yesterday" of its own
                    chronicle_events=session_events if chronicle_ledger is not None else None,
                    forum_log_path=forum_log_path,
                )
                print(f"  {label}: forum post: {'ok (remote)' if post_result else 'no remote confirmation'} "
                      f"(attempt logged to {forum_log_path})")

            return {
                "label": label, "miner_persona": miner_persona, "smith_persona": smith_persona,
                "deliveries": deliveries, "miner_episodes_recorded": miner.episodes.total_recorded,
                "stalled": stalled, "ticks_run": t + 1,
                "session_events": session_events, "insight_recorded": insight_recorded,
                "forum_posted": post_result is not None, "forum_log_path": forum_log_path,
            }
        finally:
            if smith_ipc is not None:
                smith_ipc.close()


def _run_forum_session_with_retry(
    args: argparse.Namespace, *, suffix_tag: str, mine_spots=None, **kwargs,
) -> dict:
    """Retries `_run_forum_session` (fresh account suffix each attempt,
    bounded) on a live wedge or a raised connection exception — mirrors
    `live_chronicle.py::_run_trade_session_with_retry` exactly, including its
    per-attempt ledger-path isolation (`_attempt_ledger_path`, imported
    directly)."""
    base_miner_account = kwargs.pop("miner_account")
    base_smith_account = kwargs.pop("smith_account", None)
    base_chronicle_path: Path | None = kwargs.pop("chronicle_path")
    result = None
    for attempt in range(MAX_WEDGE_ATTEMPTS):
        if attempt > 0:
            print(f"[{suffix_tag}] cooling down {GM_RELOGIN_COOLDOWN_S}s before retry "
                  f"(stale GM session + login throttle)...")
            time.sleep(GM_RELOGIN_COOLDOWN_S)
        attempt_chronicle_path = _attempt_ledger_path(base_chronicle_path, attempt)
        session_kwargs = dict(kwargs)
        if mine_spots:
            session_kwargs["mine_spot"] = mine_spots[attempt % len(mine_spots)]
        try:
            result = _run_forum_session(
                args, miner_account=f"{base_miner_account}r{attempt}",
                smith_account=(f"{base_smith_account}r{attempt}" if base_smith_account else None),
                chronicle_path=attempt_chronicle_path, **session_kwargs,
            )
        except Exception as e:  # noqa: BLE001 — a throttled login/connection drop must not crash the whole gate
            more_left = attempt + 1 < MAX_WEDGE_ATTEMPTS
            print(f"[{suffix_tag}] attempt {attempt + 1} raised {type(e).__name__}: {e} — "
                  + ("retrying with a fresh account" if more_left else
                     f"all {MAX_WEDGE_ATTEMPTS} attempts failed"))
            if not more_left:
                raise
            continue
        if result["miner_episodes_recorded"] > 0 and not result.get("stalled"):
            return result
        more_left = attempt + 1 < MAX_WEDGE_ATTEMPTS
        reason = "stalled mid-session" if result.get("stalled") else \
            f"produced ZERO miner episodes over {result['ticks_run']} ticks"
        print(f"[{suffix_tag}] attempt {attempt + 1} {reason} — a live wedge, not a real signal — "
              + ("retrying with a fresh account" if more_left else
                 f"all {MAX_WEDGE_ATTEMPTS} attempts wedged"))
    return result


def _cross_process_forum_readback(forum_log_path: Path, persona: str) -> dict | None:
    """Spawn a SECOND, freshly started Python process that reads
    `forum_log_path` from disk directly (no `anima2` import needed — the
    ledger is plain JSON lines) — mirrors `live_chronicle.py::
    _cross_process_chronicle_readback`'s identical discipline."""
    script = (
        "import json\n"
        f"path = {str(forum_log_path)!r}\n"
        f"persona = {persona!r}\n"
        "try:\n"
        "    lines = open(path, encoding='utf-8').readlines()\n"
        "except OSError:\n"
        "    lines = []\n"
        "records = [json.loads(l) for l in lines if l.strip()]\n"
        "mine = [r for r in records if r.get('persona') == persona]\n"
        "last = mine[-1] if mine else None\n"
        "print(json.dumps({'count': len(mine), "
        "'last_content': last['content'] if last else None, "
        "'last_remote_ok': last['remote_ok'] if last else None}))\n"
    )
    try:
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  cross-process forum readback FAILED to launch: {e}")
        return None
    if proc.returncode != 0:
        print(f"  cross-process forum readback FAILED (exit {proc.returncode}): {proc.stderr.strip()}")
        return None
    try:
        return json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        print(f"  cross-process forum readback produced unparseable output: {proc.stdout!r}")
        return None


def _leg_session2(args: argparse.Namespace) -> None:
    """The ONE leg that genuinely needs "a new process, not a reused one" —
    no live ServUO connection at all (the property under test is prompt
    CONSTRUCTION, not live play). Loads session1's own persisted insight via
    the REAL `memory.load_insights`, then proves it reaches a scripted,
    prompt-capturing `LLMClient`'s `user` argument via the REAL `forum.
    compose_post_llm` — printed as `[FLAG] name = value` lines the
    orchestrator parses back out of this subprocess's stdout.
    """
    insights_path = Path(args.insights_path)
    miner_persona = args.miner_persona
    insights = load_insights(insights_path, miner_persona)
    recent = insights.recent(1)
    yesterday = recent[-1].text if recent else None
    print(f"[session2] loaded insights for {miner_persona!r} from {insights_path} "
          f"(a genuinely new process — no live connection): {len(insights)} total, "
          f"yesterday={yesterday!r}")

    stub = StubLLMClient('{"title": "A New Day", "body": "Back at it, same as always."}')
    persona = Persona(name=miner_persona, title="a miner")
    compose_post_llm(stub, persona, [], job="miner", yesterday=yesterday)
    assert len(stub.calls) == 1, "compose_post_llm must call the client exactly once before falling back"
    _system, user = stub.calls[0]

    print(f"[FLAG] session2_loaded_insight_from_disk = {yesterday is not None}")
    print(f"[FLAG] session2_prompt_contains_session1_insight = "
          f"{bool(yesterday) and yesterday in user}")


def _run_leg_session2_subprocess(insights_path: Path, miner_persona: str) -> str:
    cmd = [sys.executable, "-m", "anima2.live_forum_chronicle", "--leg", "session2",
           "--insights-path", str(insights_path), "--miner-persona", miner_persona]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    print(proc.stdout)
    if proc.returncode != 0:
        print(f"session2 subprocess FAILED (exit {proc.returncode}): {proc.stderr}")
    return proc.stdout


def _flag_from_output(output: str, name: str) -> bool:
    marker = f"[FLAG] {name} = "
    for line in output.splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip() == "True"
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", choices=("session2",), default=None,
                     help="run exactly one leg standalone (used internally by the orchestrator's "
                          "own subprocess spawn); omit to run the full gate")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--stagger", type=float, default=4.0)
    ap.add_argument("--ticks", type=int, default=700, help="max ticks for the session1/inertness legs")
    ap.add_argument("--solo-ticks", type=int, default=350, help="ticks for the differential solo-miner leg")
    ap.add_argument("--deliver-threshold", type=int, default=5)
    ap.add_argument("--smith-ingots", type=int, default=15)
    ap.add_argument("--insights-path", default=None, help="[--leg session2 only]")
    ap.add_argument("--miner-persona", default=None, help="[--leg session2 only]")
    args = ap.parse_args()

    if args.leg == "session2":
        _leg_session2(args)
        return

    suffix = fresh_suffix()
    # PHASE6.md item 3's own gate lesson, distinct from item 1/2's: unlike
    # those items' code-only, exact-match ledger keys, THIS gate needs a real
    # LLM to reproduce a persona's name verbatim inside freely-written prose.
    # A long, dash-suffixed technical name (`f"Grimm-fc{suffix}"`, this
    # script's own first-draft choice) reliably gets paraphrased away by
    # genuine in-character prose (a real person doesn't refer to a friend by
    # a UNIX-time-derived ID) — live-caught on this gate's own second run
    # ("Dropped the ingots with Tormund" — no suffix at all). A SHORT numeric
    # tag with no separator, matching `village.py`'s own real persona-naming
    # shape exactly (`f"{prof.persona_name}{idx}"`, e.g. "Grimm0"/"Tormund3"),
    # empirically survives verbatim far more often (confirmed by a standalone
    # offline check against the real qwen client before this fix landed) —
    # both because it reads as a plausible name-plus-digit and because
    # `compose_post_llm`'s own 2-retry-then-fallback discipline (`forum.py`)
    # means even a dropped attempt or two still very likely lands on a post
    # that names it, whether via genuine prose or the code-composed fallback.
    # Every leg gets a WHOLLY DISTINCT name root (never a shared prefix, e.g.
    # never "Grimm12"/"Grimm12b") so a substring check can never accidentally
    # match one persona's name against a different persona's own signature.
    short = suffix[-2:]
    flags: dict[str, bool] = {}

    llm_client = ReplicateClient.from_v1_config()
    forum_client = ForumClient()
    print(f"forum configured: {forum_client.configured}; live LLM configured: {bool(llm_client)}")
    if not forum_client.configured:
        sys.exit("refusing to run: forum not configured (set ANIMA_FORUM_API_KEY or ../anima/config.yaml).")
    if llm_client is None:
        sys.exit("refusing to run: no live LLM configured (../anima/config.yaml's llm: section).")

    insights_path = Path("data") / f"insights_gate_fc{suffix}.jsonl"

    # === session1: paired, chronicle ON, reflection ON =======================
    paired_chronicle_base = Path("data") / f"chronicle_gate_fc{suffix}_paired.jsonl"
    paired_forum_log = Path("data") / f"forum_log_gate_fc{suffix}_paired.jsonl"
    miner_persona = f"Grimm{short}"
    smith_persona = f"Tormund{short}"
    session1 = _run_forum_session_with_retry(
        args, suffix_tag="session1", label="[session1: paired, chronicle+reflection ON]",
        miner_account=f"fcA{suffix}", smith_account=f"fcB{suffix}",
        miner_persona=miner_persona, smith_persona=smith_persona,
        session_ticks=args.ticks, chronicle_path=paired_chronicle_base,
        reflect_insights_path=insights_path, forum_log_path=paired_forum_log,
        forum_client=forum_client, llm_client=llm_client,
    )
    flags["session1_at_least_one_confirmed_delivery"] = session1["deliveries"] >= MIN_DELIVERIES
    flags["session1_insight_recorded_for_continuity"] = session1["insight_recorded"]
    # Review-caught: the retry wrapper only ever returns sessions with
    # episodes recorded, so OR-ing that in made this flag unconditionally
    # true — it must reflect the post attempt alone.
    flags["session1_forum_post_attempted"] = session1["forum_posted"]

    readback1 = _cross_process_forum_readback(session1["forum_log_path"], miner_persona)
    print(f"  session1: cross-process forum_log readback: {readback1}")
    flags["session1_forum_log_readback_ok"] = readback1 is not None and readback1["count"] >= 1
    flags["session1_post_mentions_paired_counterpart_name"] = (
        readback1 is not None and readback1["last_content"] is not None
        and smith_persona in readback1["last_content"]
    )

    # === differential: solo miner, chronicle ON, no counterpart ==============
    print(f"\ncooling down {GM_RELOGIN_COOLDOWN_S}s before the differential (solo) leg...")
    time.sleep(GM_RELOGIN_COOLDOWN_S)
    solo_chronicle_base = Path("data") / f"chronicle_gate_fc{suffix}_solo.jsonl"
    solo_forum_log = Path("data") / f"forum_log_gate_fc{suffix}_solo.jsonl"
    solo_persona = f"Doran{short}"
    solo = _run_forum_session_with_retry(
        args, suffix_tag="differential solo", label="[differential: solo miner, chronicle ON]",
        mine_spots=_SOLO_MINE_SPOT_POOL,
        miner_account=f"fcSolo{suffix}", smith_account=None,
        miner_persona=solo_persona, smith_persona=None,
        session_ticks=args.solo_ticks, chronicle_path=solo_chronicle_base,
        reflect_insights_path=None, forum_log_path=solo_forum_log,
        forum_client=forum_client, llm_client=llm_client,
    )
    flags["differential_solo_had_real_mining_activity"] = solo["miner_episodes_recorded"] > 0
    flags["differential_solo_zero_chronicle_events"] = len(solo["session_events"]) == 0

    readback_solo = _cross_process_forum_readback(solo["forum_log_path"], solo_persona)
    print(f"  differential solo: cross-process forum_log readback: {readback_solo}")
    solo_content = (readback_solo["last_content"] if readback_solo else None) or ""
    flags["differential_solo_post_attempted"] = readback_solo is not None and readback_solo["count"] >= 1
    flags["differential_solo_post_mentions_no_other_persona"] = (
        readback_solo is not None and readback_solo["last_content"] is not None
        and smith_persona not in solo_content and miner_persona not in solo_content
    )

    # === inertness: paired again, item 1/2's flags OFF entirely, live ========
    print(f"\ncooling down {GM_RELOGIN_COOLDOWN_S}s before the inertness leg...")
    time.sleep(GM_RELOGIN_COOLDOWN_S)
    inert_forum_log = Path("data") / f"forum_log_gate_fc{suffix}_inertness.jsonl"
    inert_miner_persona = f"Boru{short}"
    inert_smith_persona = f"Aldric{short}"
    inertness = _run_forum_session_with_retry(
        args, suffix_tag="inertness", label="[inertness: paired, chronicle+persist OFF]",
        miner_account=f"fcInA{suffix}", smith_account=f"fcInB{suffix}",
        miner_persona=inert_miner_persona, smith_persona=inert_smith_persona,
        session_ticks=args.ticks, chronicle_path=None,  # chronicle OFF entirely
        reflect_insights_path=None,  # persistence OFF entirely
        forum_log_path=inert_forum_log, forum_client=forum_client, llm_client=llm_client,
    )
    flags["inertness_engine_still_ran"] = inertness["miner_episodes_recorded"] > 0
    readback_inert = _cross_process_forum_readback(inert_forum_log, inert_miner_persona)
    print(f"  inertness: cross-process forum_log readback: {readback_inert}")
    inert_content = (readback_inert["last_content"] if readback_inert else None) or ""
    tells = ("Yesterday", "delivered ingots to", "picked up ingots from",
             "sold goods to a vendor", "banked gold", "looted a corpse")
    flags["inertness_post_has_no_grounding_tells"] = (
        readback_inert is not None and readback_inert["count"] >= 1
        and not any(t in inert_content for t in tells)
    )

    # === session2: genuinely new process, cross-session reference ============
    print(f"\n=== session2: genuinely new process reads {insights_path} for {miner_persona!r} ===")
    output = _run_leg_session2_subprocess(insights_path, miner_persona)
    flags["session2_loaded_insight_from_disk"] = _flag_from_output(output, "session2_loaded_insight_from_disk")
    flags["session2_prompt_contains_session1_insight"] = _flag_from_output(
        output, "session2_prompt_contains_session1_insight",
    )

    print()
    print_gate_verdict(flags, label="PHASE6_ITEM3_FORUM_CHRONICLE", detail="the forum as continuing chronicle")
    print()
    print("NOTE: the item's own bundled one-time real ../uowiki write check is NOT run by this "
          "script — see PHASE6.md item 3's 'As landed' note for why (a real, unresolved tension "
          "with live_wiki_report.py's own _assert_no_remote refusing any repo with a configured "
          "remote, which ../uowiki genuinely has) and the human decision it's deferred to.")


if __name__ == "__main__":
    main()
