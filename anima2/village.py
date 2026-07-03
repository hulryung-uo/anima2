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
from .uomap import find_tree_clusters

# Minoc-area woods (map 1), near the mining camp — keeps the village compact.
# Each lumberjack gets a distinct grove (a stand spot + the trees in reach).
FOREST_BASE = (2520, 450)
LUMBER_MAP = 1


def _persona_for(prof: Profession, idx: int) -> Persona:
    return Persona(name=f"{prof.persona_name}{idx}", title=f"a {prof.key}")


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


def run_village(roster: list[str], *, host: str = "127.0.0.1", port: int = 2594,
                ticks: int = 60, stagger: float = 4.0, forum: bool = False,
                chatter: bool = False) -> None:
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
    #    they speak in character while they work.
    chat_client = None
    if chatter:
        from .llm import ReplicateClient

        chat_client = ReplicateClient.from_v1_config()
        print("chatter:", "LLM cognition on" if chat_client else "no LLM configured")

    status: dict[int, str] = {}
    lock = threading.Lock()
    threads = []
    agents: list[tuple[Agent, str]] = []
    for i, p in enumerate(plan):
        cognition = None
        if chat_client is not None:
            from .cognition import LLMCognition, ThreadedCognition

            cognition = ThreadedCognition(LLMCognition(chat_client, job=p["prof"].key))
        agent = Agent(body=p["body"], persona=p["persona"], planner=p["prof"].planner(),
                      cognition=cognition, cognition_interval=12)
        if p["nodes"]:
            agent.memory["harvest_nodes"] = p["nodes"]  # the grove to work, tree by tree
        if p["smithy_drop"]:
            agent.memory["smithy_drop"] = p["smithy_drop"]  # miner's delivery target (trade pairing)
        if p["vendor_spot"]:
            agent.memory["vendor_spot"] = p["vendor_spot"]  # blacksmith's sell route (trade pairing)
        if p["banker_spot"]:
            agent.memory["banker_spot"] = p["banker_spot"]  # blacksmith's bank route (trade pairing)
        agents.append((agent, p["prof"].key))
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
            for agent, job in agents:
                res = post_day(agent, job=job, client=client, llm=llm)
                print(f"  {agent.persona.name} posted about the day: {'ok' if res else 'failed'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--miners", type=int, default=2)
    ap.add_argument("--lumberjacks", type=int, default=1)
    ap.add_argument("--fishers", type=int, default=1)
    ap.add_argument("--blacksmiths", type=int, default=1)
    ap.add_argument("--townsfolk", type=int, default=1)
    ap.add_argument("--ticks", type=int, default=60)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    ap.add_argument("--forum", action="store_true", help="post each villager's day to uotavern")
    ap.add_argument("--chatter", action="store_true", help="LLM cognition: speak in character while working")
    args = ap.parse_args()
    roster = (["miner"] * args.miners + ["lumberjack"] * args.lumberjacks
              + ["fisher"] * args.fishers + ["blacksmith"] * args.blacksmiths
              + ["townsfolk"] * args.townsfolk)
    run_village(roster, host=args.host, port=args.port, ticks=args.ticks,
                forum=args.forum, chatter=args.chatter)


if __name__ == "__main__":
    main()
