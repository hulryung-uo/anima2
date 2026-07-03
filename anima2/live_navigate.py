"""Live proof: A* navigate — GoTo delegated to WalkTo (DESIGN.md §10 Phase 3 item 4).

A **differential** proof, run on the exact same start/destination pair
(`profession.NAV_START`/`NAV_DEST` — see that module's comment for how the
pair was calibrated: real, collision-checked `Walk` probing found a Minoc-ridge
spur with **zero** straight-line progress in either direction, 36 tiles apart):

 1. **Control** — `GoTo` forced into pure greedy stepping (`use_walkto =
    False`, byte-for-byte the pre-A* skill) must **wedge/give up short of the
    target** — this is the "co-located workplaces only" constraint item 4
    exists to lift.
 2. **WalkTo-delegated** — the real, default `GoTo` (WalkTo-first, greedy
    fallback only if that makes no progress at all) must **arrive** (within
    `ARRIVAL_RADIUS` tiles), then navigate **back** to the start — a genuine
    round trip, not just a one-way pass (the multi-cycle lesson every other
    Phase 3 item's live proof already leaned on: a one-way run can't tell
    "works" from "got lucky once", and a return leg exercises fresh state a
    forward-only pass never would).

The pass gate requires **both**: the control run genuinely failed (proving
the course really is greedy-unreachable, not just slow), and the WalkTo run
arrived **both ways**.

Requires a running ServUO and the built bridge (`cargo build -p anima-net`).
Usage: python -m anima2.live_navigate [--control-ticks N] [--walk-ticks N]
"""

from __future__ import annotations

import argparse

from .agent import Agent
from .contract import Observation, Position
from .control import GmControl
from .geometry import chebyshev
from .ipc_body import IpcBody
from .persona import Persona
from .planner import Planner
from .profession import NAV_DEST, NAV_START
from .skills.base import Goal
from .skills.movement import GoTo

# "Close enough" for a live-routed arrival — the destination tile itself may
# not be the only tile a real route settles on (mirrors every other Phase 3
# live proof's own tolerance for "provably worked", not pixel-exact). GoTo's
# own internal SUCCESS is still an exact tile match (see that class's
# docstring) — this is a looser gate applied here, at the call site, per
# item 4's own ground truth ("must ARRIVE (radius <= 2)").
ARRIVAL_RADIUS = 2


class _RecordingBody:
    """Wraps a `Body`, caching the last `Observation` (see `live_trade.py`)."""

    def __init__(self, inner: IpcBody) -> None:
        self._inner = inner
        self.last_obs: Observation | None = None

    def observe(self) -> Observation:
        self.last_obs = self._inner.observe()
        return self.last_obs

    def act(self, action) -> None:
        self._inner.act(action)

    @property
    def connected(self) -> bool:
        return self._inner.connected


def _run_goto(
    body: _RecordingBody,
    *,
    label: str,
    target: tuple[int, int],
    z: int,
    use_walkto: bool,
    max_ticks: int,
    snapshot_every: int = 10,
) -> tuple[tuple[int, int], bool, str]:
    """Drive a fresh `Agent(GoTo)` toward `target` for up to `max_ticks`.
    Returns `(final_pos, goal_cleared, goto_mode)` — `goto_mode` is the
    breadcrumb `GoTo` leaves in `ctx.memory` even after a terminal result
    (see that class's own docstring), so the transcript can show whether a
    run actually used A*, fell back to greedy, or (the control run) never
    tried A* at all.
    """
    skill = GoTo()
    skill.use_walkto = use_walkto
    agent = Agent(body=body, persona=Persona(name=label), planner=Planner([skill]))
    agent.goal = Goal(kind="goto", params={"target": Position(target[0], target[1], z)})

    for t in range(max_ticks):
        agent.tick()
        assert body.last_obs is not None
        pos = body.last_obs.player.pos
        if t % snapshot_every == 0 or agent.goal is None:
            dist = chebyshev(pos, Position(target[0], target[1], z))
            mode = agent.memory.get("goto_mode", "?")
            print(f"    [{label}] tick {t:4d} pos=({pos.x},{pos.y}) dist={dist:3d} mode={mode}")
        if agent.goal is None:
            break

    assert body.last_obs is not None
    final = body.last_obs.player.pos
    return (final.x, final.y), agent.goal is None, agent.memory.get("goto_mode", "?")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control-ticks", type=int, default=20, help="budget for the greedy control run")
    ap.add_argument("--walk-ticks", type=int, default=250, help="budget for each WalkTo leg")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    # Fresh account each run (see live_trade.py's note on why).
    ap.add_argument("--nav-account", default="animanav")
    args = ap.parse_args()

    print(f"course: NAV_START={NAV_START} <-> NAV_DEST={NAV_DEST} "
          f"(chebyshev {chebyshev(Position(*NAV_START, 0), Position(*NAV_DEST, 0))} tiles apart)")

    with IpcBody.spawn(args.host, args.port, args.nav_account, args.nav_account, pump_ms=400) as nav_ipc:
        nav_serial = nav_ipc.ready["player"]["serial"]
        print(f"navigator: {args.nav_account} serial={nav_serial}")

        with GmControl.spawn(args.host, args.port) as gm:
            gm.hide()
            sx, sy = NAV_START
            dx, dy = NAV_DEST
            lo_x, hi_x = min(sx, dx) - 5, max(sx, dx) + 5
            lo_y, hi_y = min(sy, dy) - 5, max(sy, dy) + 5
            # Wipe stray debris from earlier live/debug runs on this ridge
            # (PHASE3.md item 1's "repeated live testing pollutes the shard").
            gm.command_area("[WipeNPCs", lo_x, lo_y, hi_x, hi_y, 20)
            gm.command_area("[WipeItems", lo_x, lo_y, hi_x, hi_y, 20)
            gx, gy, gz = gm.stage(nav_serial, sx, sy)  # no tools/skills — pure navigation
            print(f"GM staged navigator at ({gx},{gy},{gz})")

        nav_body = _RecordingBody(nav_ipc)
        nav_body.observe()
        nav_body.observe()  # let the teleport settle

        # --- (a) Control: pure greedy GoTo must wedge short of the target ---
        print("\n--- control run: greedy GoTo (use_walkto=False) ---")
        control_pos, control_cleared, control_mode = _run_goto(
            nav_body, label="control", target=NAV_DEST, z=gz,
            use_walkto=False, max_ticks=args.control_ticks,
        )
        control_dist = chebyshev(Position(*control_pos, gz), Position(*NAV_DEST, gz))
        greedy_failed = control_cleared and control_dist > ARRIVAL_RADIUS
        print(f"  greedy control result: final={control_pos} dist_to_dest={control_dist} "
              f"goal_cleared={control_cleared} mode={control_mode} -> "
              f"{'WEDGED (as expected)' if greedy_failed else 'DID NOT FAIL — course is not a valid greedy-blocker'}")

        # Clean slate for the WalkTo run — put the NAVIGATOR back at the
        # exact start tile regardless of how far the wedged control run
        # (didn't, per the geometry comment, but be robust) drifted.
        # (`gm.go` would move the GM character itself, not the navigator —
        # `stage()` with no skills/items is the serial-targeted teleport.)
        with GmControl.spawn(args.host, args.port) as gm:
            gm.hide()
            gm.stage(nav_serial, *NAV_START)
        nav_body.observe()
        nav_body.observe()

        # --- (b) WalkTo-delegated GoTo must arrive, then navigate back ---
        print("\n--- forward leg: WalkTo-delegated GoTo, NAV_START -> NAV_DEST ---")
        fwd_pos, fwd_cleared, fwd_mode = _run_goto(
            nav_body, label="forward", target=NAV_DEST, z=gz,
            use_walkto=True, max_ticks=args.walk_ticks,
        )
        fwd_dist = chebyshev(Position(*fwd_pos, gz), Position(*NAV_DEST, gz))
        forward_arrived = fwd_dist <= ARRIVAL_RADIUS
        print(f"  forward result: final={fwd_pos} dist_to_dest={fwd_dist} "
              f"goal_cleared={fwd_cleared} mode={fwd_mode} -> "
              f"{'ARRIVED' if forward_arrived else 'DID NOT ARRIVE'}")

        print("\n--- return leg: WalkTo-delegated GoTo, NAV_DEST -> NAV_START ---")
        back_pos, back_cleared, back_mode = _run_goto(
            nav_body, label="return", target=NAV_START, z=gz,
            use_walkto=True, max_ticks=args.walk_ticks,
        )
        back_dist = chebyshev(Position(*back_pos, gz), Position(*NAV_START, gz))
        back_arrived = back_dist <= ARRIVAL_RADIUS
        print(f"  return result: final={back_pos} dist_to_start={back_dist} "
              f"goal_cleared={back_cleared} mode={back_mode} -> "
              f"{'ARRIVED' if back_arrived else 'DID NOT ARRIVE'}")

        print("\n--- result ---")
        print(f"greedy control wedged short of the target:  {greedy_failed}")
        print(f"WalkTo GoTo arrived (forward leg):           {forward_arrived}")
        print(f"WalkTo GoTo arrived (return leg, round trip): {back_arrived}")
        if greedy_failed and forward_arrived and back_arrived:
            print("\nA* NAVIGATE CONFIRMED: greedy GoTo cannot cross this course; "
                  "WalkTo-delegated GoTo crosses it both ways.")
        else:
            print("\nDIFFERENTIAL PROOF INCOMPLETE — see the timeline above.")


if __name__ == "__main__":
    main()
