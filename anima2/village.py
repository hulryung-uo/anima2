"""A working village: agents with per-agent professions, each doing their job.

Releases a roster of agents, stages the workers via the Control plane (tools +
skills + a distinct workplace), names each character, then runs them all
concurrently. Miners mine at their own ore bank and **gain Mining skill** — the
village's "work output" is the skill each agent accrues (recorded as episodic
reward by the work skill). A roster with both a miner and a blacksmith gets
the first of each co-located at a calibrated trade spot with the miner's
delivery target set — goods actually flow between them (DESIGN.md §10 Phase
3; see `live_trade.py` for a focused 2-agent live proof).

Usage: python -m anima2.village [--miners N] [--townsfolk M] [--ticks T]
"""

from __future__ import annotations

import argparse
import threading
import time

from .agent import Agent
from .contract import Say, Walk
from .control import GmControl
from .ipc_body import IpcBody
from .persona import Persona
from .profession import (
    BANKER_SPOT,
    BLACKSMITH_SPOTS,
    FISHING_SPOTS,
    MINING_SPOTS,
    PROFESSIONS,
    TRADE_MINE_SPOT,
    TRADE_SMITH_SPOT,
    VENDOR_SPOT,
    Profession,
)
from .skill_library import SkillLibrary
from .skill_tuning import DELIVER_THRESHOLD_CANDIDATES, ParamSpec, ParamTuner
from .skills import MineSmeltDeliver
from .skills.base import Status
from .uomap import find_tree_clusters

# Minoc-area woods (map 1), near the mining camp — keeps the village compact.
# Each lumberjack gets a distinct grove (a stand spot + the trees in reach).
FOREST_BASE = (2520, 450)
LUMBER_MAP = 1


def _persona_for(prof: Profession, idx: int) -> Persona:
    return Persona(name=f"{prof.persona_name}{idx}", title=f"a {prof.key}",
                   combat_disposition=prof.combat_disposition)


def _run_worker(agent: Agent, ticks: int, idx: int, status: dict, lock: threading.Lock,
                job: str) -> None:
    steps = says = 0
    last_say = ""
    for _ in range(ticks):
        if not agent.body.connected:
            break
        action = agent.tick()
        steps += isinstance(action, Walk)
        if isinstance(action, Say):
            says += 1
            last_say = action.text
        p = agent.body.observe().player.pos
        with lock:
            line = (f"{agent.persona.name:<9} {job:<10} @({p.x},{p.y}) "
                    f"out+{agent.episodes.total_reward():.1f} steps={steps} says={says}")
            if last_say:
                line += f'  "{last_say[:60]}"'
            status[idx] = line


class _CountingClient:
    """Wraps an `LLMClient`, counting `complete()` calls — scoped to this script's
    own run, never persisted (contrast `llm.py::_UsageLoggingClient`, which
    `build_tiered_clients()` already applies underneath and *does* persist to
    `data/llm_usage.jsonl`). Exists so `--llm-tiers`'s live gate has an
    independent, in-process tally to cross-check the usage-log line count
    against — the ledger and this counter must agree, or the routing plumbing
    (or the ledger itself) is broken."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.inner.complete(system, user)


def run_village(roster: list[str], *, host: str = "127.0.0.1", port: int = 2594,
                ticks: int = 60, stagger: float = 4.0, forum: bool = False,
                chatter: bool = False, llm_tiers: str | None = None,
                tune_deliver_threshold: bool = False, ledger_path: str | None = None) -> None:
    # 1) Bring every agent online (staggered logins dodge the ServUO throttle).
    print(f"releasing {len(roster)} villagers: {roster}")
    online: list[tuple[IpcBody, Profession, Persona]] = []
    for i, key in enumerate(roster):
        prof = PROFESSIONS[key]
        try:
            body = IpcBody.spawn(host, port, f"anima{i}", f"anima{i}", pump_ms=300)
        except Exception as e:  # noqa: BLE001
            print(f"  anima{i} ({key}): login failed ({e})")
            continue
        online.append((body, prof, _persona_for(prof, i)))
        print(f"  anima{i}: {_persona_for(prof, i).name} the {key}")
        time.sleep(stagger)
    if not online:
        print("no villagers came online")
        return

    # 2) Assign each worker a distinct workplace. Miners get an ore bank; each
    #    lumberjack gets a grove (a stand spot + the exact tree statics in reach,
    #    found from the static map — trees can't be probed blindly, and a cluster
    #    lets a worker move tree-to-tree as each one depletes).
    #
    #    Phase 3: a roster with *both* a miner and a blacksmith gets the first of
    #    each co-located at the calibrated trade spot instead of drawn from the
    #    separate pools below, and the miner's `smithy_drop` is set so its ore
    #    haul actually goes somewhere — the first inter-agent economy loop
    #    (DESIGN.md §10). The same pairing also gets its own vendor + banker
    #    (item 2 — `skills/market.py::BlacksmithMarket`, opt-in the same way)
    #    staged near the smithy via `profession.py`'s `VENDOR_SPOT`/
    #    `BANKER_SPOT` routes. Any further miners/blacksmiths beyond that first
    #    pair fall back to their normal pools (and get no vendor/banker — the
    #    routes are calibrated to this one smithy spot's own narrow geometry,
    #    not the general `BLACKSMITH_SPOTS`), and a roster with only one of the
    #    two professions is untouched — same staging as before this feature.
    has_trade_pair = (any(p.key == "miner" for _, p, _ in online)
                      and any(p.key == "blacksmith" for _, p, _ in online))
    # TRADE_MINE_SPOT *is* MINING_SPOTS[1] — once a trade pairing claims it
    # directly (below), it must not also be handed out from this pool, or a
    # later miner ends up staged on top of the trade miner.
    spots = iter(s for s in MINING_SPOTS if not has_trade_pair or s != TRADE_MINE_SPOT)
    fish_spots = iter(FISHING_SPOTS)
    smith_spots = iter(BLACKSMITH_SPOTS)
    groves = iter(find_tree_clusters(LUMBER_MAP, *FOREST_BASE))
    trade_miner_placed = trade_smith_placed = not has_trade_pair
    plan: list[dict] = []
    for body, prof, persona in online:
        workplace, nodes, smithy_drop, vendor_spot, banker_spot = None, None, None, None, None
        if prof.key == "miner" and not trade_miner_placed:
            workplace = TRADE_MINE_SPOT
            smithy_drop = TRADE_SMITH_SPOT
            trade_miner_placed = True
        elif prof.key == "lumberjack":
            grove = next(groves, None)
            if grove is not None:
                workplace, trees = grove
                nodes = [(t.x, t.y, t.z, t.graphic) for t in trees]
        elif prof.key == "fisher":
            spot = next(fish_spots, None)
            if spot is not None:
                (sx, sy), (wx, wy, wz) = spot
                workplace = (sx, sy)
                nodes = [(wx, wy, wz, 0)]  # cast at the exact water tile (land target)
        elif prof.key == "blacksmith" and not trade_smith_placed:
            workplace = TRADE_SMITH_SPOT
            vendor_spot = VENDOR_SPOT
            banker_spot = BANKER_SPOT
            trade_smith_placed = True
        elif prof.key == "blacksmith":
            workplace = next(smith_spots, None)
        elif prof.needs_workplace:
            workplace = prof.workplace or next(spots)
        plan.append({"body": body, "prof": prof, "persona": persona,
                     "workplace": workplace, "nodes": nodes, "smithy_drop": smithy_drop,
                     "vendor_spot": vendor_spot, "banker_spot": banker_spot})

    # 3) Control plane: stage workers and name everyone.
    #    `find_mobile_near`'s own exclude set needs every agent serial the
    #    village knows, not just the one currently being staged — a widened
    #    search radius (see that method's docstring) can otherwise resolve to
    #    a *different* known agent standing nearby (e.g. the trade miner
    #    sitting within reach of the trade smithy's own vendor/banker spots)
    #    instead of the NPC actually being searched for.
    all_agent_serials = {p["body"].ready["player"]["serial"] for p in plan}
    with GmControl.spawn(host, port) as gm:
        gm.hide()
        for p in plan:
            serial = p["body"].ready["player"]["serial"]
            if p["workplace"] is not None:
                gx, gy, gz = gm.stage(serial, *p["workplace"],
                                      skills=p["prof"].skills, items=p["prof"].items)
                for stype, dx, dy in p["prof"].structures:
                    gm.command_at(f"[Add {stype}", gx + dx, gy + dy, gz)
                if p["vendor_spot"]:
                    # `stage_npc` adds, finds, corrects the position back onto
                    # the exact requested spot if `[Add` settled it a tile off
                    # (live-caught pinning it dead onto the trade corridor's
                    # own hub waypoint instead, permanently blocking every
                    # walk through it — see that method's docstring), and
                    # pins it (`VendorAI.DoActionWander` roams a BaseVendor
                    # when idle, which can drift it out of the market skill's
                    # search radius / the smith's fixed route).
                    vx, vy = p["vendor_spot"][-1]
                    gm.stage_npc("Blacksmith", vx, vy, gz, exclude=all_agent_serials)
                if p["banker_spot"]:
                    bx, by = p["banker_spot"][-1]
                    gm.stage_npc("Banker", bx, by, gz, exclude=all_agent_serials)
            gm.command_on(f'[Set Name "{p["persona"].name}"', serial)
    print("staged & named. work begins.\n")

    # 4) Run every villager concurrently; print a live snapshot of the village.
    #    With --chatter, each gets an LLM cognition (threaded, off the hot path) so
    #    they speak in character while they work. --llm-tiers supersedes --chatter:
    #    it builds a role-tiered client set (Phase 4 item 2 — llm.py::ROLE_TIER/
    #    build_tiered_clients) and, since proving the tiering actually routes by
    #    role needs a "standard"-tier caller too, also wires reflection (off until
    #    now — this flag is the first thing in village.py to turn it on).
    chat_client = None
    tiered_clients = None
    call_counters: dict[str, _CountingClient] = {}
    if llm_tiers:
        from .llm import ROLE_TIER, build_tiered_clients

        tiered_clients = build_tiered_clients(provider=llm_tiers)
        call_counters = {tier: _CountingClient(client) for tier, client in tiered_clients.items()}
        print(f"llm-tiers ({llm_tiers}):",
              "degraded — one client answers every tier" if tiered_clients.degraded
              else "tiered — 3 distinct models")
    elif chatter:
        from .llm import ReplicateClient

        chat_client = ReplicateClient.from_v1_config()
        print("chatter:", "LLM cognition on" if chat_client else "no LLM configured")

    # Phase 4 item 4 — deliver_threshold bandit tuning: one shared ParamTuner
    # for the whole roster (miners pull from the same candidate grid), seeded
    # from whatever `data/skill_ledger.jsonl` already has on disk (item 3's
    # own "read at construction time" convention — a process restart doesn't
    # throw away prior sessions' pulls). `skill_lib` is only constructed when
    # the flag is on — zero effect otherwise, matching every other opt-in
    # collaborator in this file.
    skill_lib: SkillLibrary | None = None
    tuner: ParamTuner | None = None
    if tune_deliver_threshold:
        skill_lib = SkillLibrary(ledger_path=ledger_path)
        deliver_spec = ParamSpec("deliver_threshold", DELIVER_THRESHOLD_CANDIDATES)
        tuner = ParamTuner.load_from_ledger(
            skill_lib.ledger_path, "mine_smelt_deliver", "deliver_threshold", deliver_spec,
        )
        print(f"deliver_threshold tuning: ON — ledger at {skill_lib.ledger_path.resolve()} "
              f"(seeded pulls: {tuner.pulls()})")

    status: dict[int, str] = {}
    lock = threading.Lock()
    threads = []
    agents: list[tuple[Agent, str, float | None]] = []
    for i, p in enumerate(plan):
        cognition = None
        if tiered_clients is not None:
            from .cognition import LLMCognition, LLMReflection, ReflectingCognition, ThreadedCognition

            inner = LLMCognition(call_counters[ROLE_TIER["chatter"]], job=p["prof"].key)
            reflection = LLMReflection(call_counters[ROLE_TIER["reflection"]])
            cognition = ThreadedCognition(ReflectingCognition(inner, reflection))
        elif chat_client is not None:
            from .cognition import LLMCognition, ThreadedCognition

            cognition = ThreadedCognition(LLMCognition(chat_client, job=p["prof"].key))
        planner = p["prof"].planner()

        # PHASE4.md item 4: pick a deliver_threshold once per miner, at
        # construction time (session granularity — held fixed for the whole
        # session, never re-tuned mid-run). `Profession.planner()` doesn't
        # hand back the constructed work-skill instance directly, so it's
        # located after the fact — the exact seam PHASE4.md item 4's own
        # Scope names.
        chosen_threshold: float | None = None
        if tuner is not None and p["prof"].key == "miner":
            miner_skill = next((s for s in planner.skills if isinstance(s, MineSmeltDeliver)), None)
            if miner_skill is not None:
                chosen_threshold = tuner.choose()
                miner_skill.deliver_threshold = chosen_threshold

        agent = Agent(body=p["body"], persona=p["persona"], planner=planner,
                      cognition=cognition, cognition_interval=12)
        if p["nodes"]:
            agent.memory["harvest_nodes"] = p["nodes"]  # the grove to work, tree by tree
        if p["smithy_drop"]:
            agent.memory["smithy_drop"] = p["smithy_drop"]  # miner's delivery target (trade pairing)
        if p["vendor_spot"]:
            agent.memory["vendor_spot"] = p["vendor_spot"]  # blacksmith's sell route (trade pairing)
        if p["banker_spot"]:
            agent.memory["banker_spot"] = p["banker_spot"]  # blacksmith's bank route (trade pairing)
        if chosen_threshold is not None:
            print(f"  {p['persona'].name}: deliver_threshold={chosen_threshold} (tuner-chosen)")
        agents.append((agent, p["prof"].key, chosen_threshold))
        t = threading.Thread(target=_run_worker,
                             args=(agent, ticks, i, status, lock, p["prof"].key), daemon=True)
        threads.append(t)
        t.start()

    while any(t.is_alive() for t in threads):
        time.sleep(2.5)
        with lock:
            snap = [status[i] for i in sorted(status)]
        print("— village —\n  " + "\n  ".join(snap))
    for t in threads:
        t.join()
    print("\nday's work done.")

    # PHASE4.md item 4: at session end, record (value, reward) for every
    # miner the tuner picked a value for — through the exact same
    # `SkillLibrary.record_outcome` ledger item 3 already established, tagged
    # via `param`/`param_value` so `ParamTuner.load_from_ledger` can pick
    # these lines back out from item 3's own per-tick (param=None) records.
    #
    # The recorded reward is the miner's raw `episodes.total_reward()` over
    # this run's fixed `--ticks` window — NOT `session_mean_reward` (a mean
    # per recorded episode). Every miner here already runs the same fixed
    # tick count (`_run_worker` has no early-stop), but a mean-per-episode
    # still isn't a fair cross-candidate objective: a higher deliver_threshold
    # triggers fewer, larger delivery events, so it accrues episodes at a
    # different rate than a lower one, which skews a per-episode mean even
    # when the session length itself is held fixed — the same live-caught
    # class of bug `live_trade.py::_run_session`'s own docstring documents in
    # detail (that live gate is where it was actually caught).
    #
    # A miner whose session recorded ZERO episodes is a live wedge (no
    # confirmed mining/delivery progress at all), not a genuine "this value
    # is bad" signal — skip recording rather than poison that arm with a
    # false 0.0 (mirrors `live_trade.py::_run_tuner`'s own guard).
    if tuner is not None and skill_lib is not None:
        print(f"\n— deliver_threshold tuning ({skill_lib.ledger_path}) —")
        for agent, job, chosen in agents:
            if chosen is None:
                continue
            if agent.episodes.total_recorded == 0:
                print(f"  {agent.persona.name} ({job}): deliver_threshold={chosen} — "
                      f"0 episodes recorded (live wedge) — SKIPPED, no ledger record")
                continue
            reward = agent.episodes.total_reward()
            tuner.update(chosen, reward)
            skill_lib.record_outcome("mine_smelt_deliver", "miner", reward, Status.SUCCESS,
                                     param="deliver_threshold", param_value=chosen)
            print(f"  {agent.persona.name} ({job}): deliver_threshold={chosen} "
                  f"reward(fixed-window total)={reward:.3f}")
        print(f"  cumulative pulls (this process, seeded + this session): {tuner.pulls()}")

    if call_counters:
        print(f"\n— llm tiers — (degraded={tiered_clients.degraded}) —")
        for tier, counter in call_counters.items():
            print(f"  {tier}: {counter.calls} calls")

    # 5) End of day: each villager writes about it on the tavern forum.
    if forum:
        from .forum import ForumClient, post_day
        from .llm import ReplicateClient

        client = ForumClient()
        if not client.configured:
            print("forum: no API key (set ANIMA_FORUM_API_KEY or anima/config.yaml).")
        else:
            llm = ReplicateClient.from_v1_config()  # in-character prose if available
            print(f"\n— the tavern board —{' (LLM-written)' if llm else ' (heuristic)'}")
            for agent, job, _chosen_threshold in agents:
                res = post_day(agent, job=job, client=client, llm=llm)
                print(f"  {agent.persona.name} posted about the day: {'ok' if res else 'failed'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--miners", type=int, default=2)
    ap.add_argument("--lumberjacks", type=int, default=1)
    ap.add_argument("--fishers", type=int, default=1)
    ap.add_argument("--blacksmiths", type=int, default=1)
    ap.add_argument("--townsfolk", type=int, default=1)
    # Opt-in, default 0: the hunter profession (Phase 3 item 3) has its own
    # calibrated, isolated field (`profession.HUNTING_SPOT`) and doesn't need
    # to join the default roster for the village to keep working exactly as
    # before — mirrors every other roster knob's own default-count shape.
    ap.add_argument("--hunters", type=int, default=0)
    ap.add_argument("--ticks", type=int, default=60)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--forum", action="store_true", help="post each villager's day to uotavern")
    ap.add_argument("--chatter", action="store_true", help="LLM cognition: speak in character while working")
    # Opt-in, unset by default: zero effect on any currently-passing roster unless
    # passed (Phase 4 item 2). Supersedes --chatter when both are given — it wires
    # a role-tiered cognition (chatter + reflection) rather than a single client.
    ap.add_argument("--llm-tiers", choices=["anthropic", "replicate", "stub"], default=None,
                     help="role-tiered LLM cognition (chatter + reflection) via build_tiered_clients")
    # Opt-in, unset by default (Phase 4 item 4): zero effect on any currently-
    # passing roster unless passed. Each miner picks a `MineSmeltDeliver.
    # deliver_threshold` via `ParamTuner.choose()` at construction time and
    # records the session's outcome back to `data/skill_ledger.jsonl`.
    ap.add_argument("--tune-deliver-threshold", action="store_true",
                     help="bandit-tune each miner's deliver_threshold (Phase 4 item 4)")
    ap.add_argument("--ledger-path", default=None,
                     help="override data/skill_ledger.jsonl (mainly for isolated test/live runs)")
    args = ap.parse_args()
    roster = (["miner"] * args.miners + ["lumberjack"] * args.lumberjacks
              + ["fisher"] * args.fishers + ["blacksmith"] * args.blacksmiths
              + ["townsfolk"] * args.townsfolk + ["hunter"] * args.hunters)
    run_village(roster, host=args.host, port=args.port, ticks=args.ticks,
                forum=args.forum, chatter=args.chatter, llm_tiers=args.llm_tiers,
                tune_deliver_threshold=args.tune_deliver_threshold, ledger_path=args.ledger_path)


if __name__ == "__main__":
    main()
