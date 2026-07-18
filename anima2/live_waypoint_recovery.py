"""Live A4 gate: recover from death using only ServUO waypoints.

The GM connection exists solely to make the scenario repeatable.  It stages a
normal healer and the subject, kills the subject, records the server's corpse
serial, and is then closed.  Only after that close does the recovery loop run.
``RecoverDeath`` receives no coordinate fixture: it must discover the healer
from the type-6 0xE5 waypoint, walk to it, accept a structurally verified free
resurrection gump, observe the healer's 0xE6 removal, strongly attribute the
subject's corpse, recover the pre-death item, and resume the original Goal.

ServUO's corpse waypoint is intentionally informational in this gate.  The
shard sends it only on Enhanced Client paths and can point at a previous
corpse, so its presence is printed but never accepted as ownership proof.

Requires a running local ServUO shard and a schema-v8 ``anima-agent`` bridge.

Usage::

    python -m anima2.live_waypoint_recovery --suffix a4manual
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from .agent import Agent
from .contract import (
    Action,
    GumpResponse,
    Observation,
    Position,
    TargetCancel,
    Use,
    WalkTo,
    WAYPOINT_CORPSE,
    WAYPOINT_RESURRECTION,
)
from .control import GmControl
from .geometry import chebyshev
from .ipc_body import IpcBody, ResilientIpcBody
from .live_common import (
    GM_RELOGIN_COOLDOWN_S,
    fresh_suffix,
    login_throttle,
    print_gate_verdict,
    wipe_area,
)
from .live_recovery import (
    DAGGER_GRAPHIC,
    _GoalWorkProbe,
    _pack_item,
    _property_serial,
)
from .persona import Persona
from .planner import Planner
from .profession import HUNTING_SPOT
from .skills import RecoverDeath
from .skills.base import Goal

HEALER_OFFSET = 3


@dataclass(frozen=True)
class _ActionRecord:
    action: Action
    dead: bool
    pos: Position
    gumps: list
    waypoints: list


class _RecordingBody:
    """Cache every perception used to make an action during the live proof."""

    def __init__(self, inner: IpcBody | ResilientIpcBody) -> None:
        self.inner = inner
        self.last_obs: Observation | None = None
        self.observations: list[Observation] = []
        self.actions: list[_ActionRecord] = []

    @property
    def connected(self) -> bool:
        return self.inner.connected

    def observe(self) -> Observation:
        self.last_obs = self.inner.observe()
        self.observations.append(self.last_obs)
        return self.last_obs

    def act(self, action: Action) -> None:
        assert self.last_obs is not None
        self.actions.append(
            _ActionRecord(
                action=action,
                dead=self.last_obs.player.dead,
                pos=self.last_obs.player.pos,
                gumps=list(self.last_obs.gumps),
                waypoints=list(self.last_obs.waypoints),
            )
        )
        self.inner.act(action)


def _same_tile(action: Action, waypoint: object) -> bool:
    return (
        isinstance(action, WalkTo)
        and hasattr(waypoint, "pos")
        and (action.x, action.y) == (waypoint.pos.x, waypoint.pos.y)
    )


def _run(args: argparse.Namespace) -> tuple[dict[str, bool], str]:
    account = args.account or f"animaa4{args.suffix}"
    password = args.password or account
    x, y = HUNTING_SPOT

    with ResilientIpcBody.spawn(
        args.host,
        args.port,
        account,
        password,
        bridge=args.bridge,
        pump_ms=args.pump_ms,
        response_timeout_s=args.response_timeout,
    ) as ipc:
        serial = ipc.ready["player"]["serial"]
        body = _RecordingBody(ipc)
        login_throttle(GM_RELOGIN_COOLDOWN_S)

        healer_serial: int | None = None
        corpse_readback: int | None = None
        killed = False
        young_disable_command = False
        young_disabled = False
        predeath_goal_started = False
        goal_before = Goal(
            kind="live_recovery_probe",
            params={"token": f"waypoint-{args.suffix}"},
        )
        recovery = RecoverDeath()
        agent = Agent(
            body=body,
            persona=Persona(name="A4 Waypoint Recovery Probe"),
            planner=Planner([recovery, _GoalWorkProbe()]),
            goal=goal_before,
        )

        # Control-plane-only setup.  HUNTING_SPOT is a calibrated, level z=15
        # pocket with at least three open tiles in every direction.  Placing
        # the healer three tiles east forces one real ghost step across the
        # standard BaseHealer outside->inside radius-2 trigger.
        with GmControl.spawn(
            args.host,
            args.port,
            bridge=args.bridge,
        ) as gm:
            gm.hide()
            wipe_area(gm, x, y, radius=10, z=15)
            staged_x, staged_y, staged_z = gm.stage(
                serial,
                x,
                y,
                items=["Dagger 1"],
            )
            young_disable_command = gm.command_on("[Set Young false", serial)
            young_readback = gm.get_property_value("Young", serial)
            young_disabled = young_disable_command and (
                young_readback == 0
                or (isinstance(young_readback, str) and young_readback.strip().lower() == "false")
            )
            healer_x = staged_x + HEALER_OFFSET
            healer_y = staged_y
            healer = gm.stage_npc(
                "Healer",
                healer_x,
                healer_y,
                staged_z,
                exclude={serial},
            )
            if healer is not None:
                healer_serial = healer.serial

            # Establish the rolling alive body/position/pack ownership evidence
            # and start a two-step Goal before death.
            for _ in range(3):
                body.observe()
            predeath_action = agent.tick()
            predeath_goal_started = (
                getattr(predeath_action, "text", None) == "Working before death."
                and agent.goal == goal_before
                and agent.memory.get("live_recovery_goal_phase") == 1
            )
            assert body.last_obs is not None
            dagger_before = _pack_item(body.last_obs, DAGGER_GRAPHIC)
            death_pos = body.last_obs.player.pos
            death_map_index = body.last_obs.map_index
            if dagger_before is not None:
                # Force a real corpse transition. The load-bearing proof below
                # is observational: this exact serial must later appear inside
                # the exact corpse and only then return to the backpack.
                gm.command_on("[Set LootType Regular", dagger_before.serial)
                gm.command_on("[Set Insured false", dagger_before.serial)

            killed = gm.command_on("[Kill", serial)
            gm.command_on("[Set Frozen false", serial)
            corpse_readback = _property_serial(gm.get_property("Corpse", serial))
            print(
                "GM staged and killed subject; "
                f"subject=0x{serial:X} healer={healer_serial!r} "
                f"corpse={corpse_readback!r}; closing GM"
            )

        # This is the trust boundary of the gate.  No GmControl object is
        # opened or called below it.
        gm_closed_before_recovery = not gm.body.connected
        recovery_generation = ipc.generation
        recovery_observation_offset = len(body.observations)
        recovery_action_offset = len(body.actions)

        healer_waypoint_seen = False
        healer_waypoint_selected = False
        healer_waypoint_removed = False
        corpse_waypoint_seen = False
        dead_seen = False
        ghost_moved = False
        alive_again = False
        strong_corpse_selected = False
        dagger_seen_in_exact_corpse = False
        item_recovered = False
        generation_and_map_stable = True
        goal_preserved = True
        resumed = False
        ticks_used = 0

        for tick in range(args.recovery_ticks):
            action = agent.tick()
            ticks_used = tick + 1
            assert body.last_obs is not None
            obs = body.last_obs
            generation_and_map_stable = generation_and_map_stable and (
                ipc.generation == recovery_generation and obs.map_index == death_map_index
            )

            exact_healer_waypoints = [
                waypoint
                for waypoint in obs.waypoints
                if waypoint.serial == healer_serial
                and waypoint.kind == WAYPOINT_RESURRECTION
                and waypoint.map == obs.map_index
                and (waypoint.pos.x, waypoint.pos.y) == (healer_x, healer_y)
            ]
            healer_waypoint_seen = healer_waypoint_seen or (
                generation_and_map_stable and bool(exact_healer_waypoints)
            )
            corpse_waypoint_seen = corpse_waypoint_seen or any(
                waypoint.kind == WAYPOINT_CORPSE for waypoint in obs.waypoints
            )
            selected = agent.memory.get(recovery._RES_TARGET)
            healer_waypoint_selected = healer_waypoint_selected or (
                isinstance(selected, tuple)
                and len(selected) == 4
                and selected[0] == healer_serial
                and selected[1:3] == (healer_x, healer_y)
            )

            if obs.player.dead:
                dead_seen = True
                ghost_moved = ghost_moved or (
                    (obs.player.pos.x, obs.player.pos.y) != (death_pos.x, death_pos.y)
                )
            alive_again = dead_seen and not obs.player.dead
            if alive_again and healer_waypoint_seen and generation_and_map_stable:
                healer_waypoint_removed = healer_waypoint_removed or not any(
                    waypoint.serial == healer_serial for waypoint in obs.waypoints
                )

            if body.last_obs.player.dead or agent.memory.get(recovery._CORPSE_PENDING):
                goal_preserved = goal_preserved and agent.goal == goal_before
            strong_corpse_selected = strong_corpse_selected or (
                corpse_readback is not None
                and agent.memory.get(recovery._CORPSE_SERIAL) == corpse_readback
            )
            dagger_seen_in_exact_corpse = dagger_seen_in_exact_corpse or (
                corpse_readback is not None
                and dagger_before is not None
                and any(
                    item.serial == dagger_before.serial and item.container == corpse_readback
                    for item in obs.items
                )
            )
            current_dagger = _pack_item(obs, DAGGER_GRAPHIC)
            item_recovered = item_recovered or (
                alive_again
                and dagger_seen_in_exact_corpse
                and dagger_before is not None
                and current_dagger is not None
                and current_dagger.serial == dagger_before.serial
            )
            resumed = agent.memory.get("live_recovery_goal_phase") == 2 and agent.goal is None

            if tick % args.snapshot_every == 0 or action is not None or resumed:
                wp_summary = [(w.serial, w.kind, w.pos.x, w.pos.y) for w in obs.waypoints[:8]]
                print(
                    f"tick={tick:3d} dead={obs.player.dead} "
                    f"pos=({obs.player.pos.x},{obs.player.pos.y}) "
                    f"action={type(action).__name__ if action else '-'} "
                    f"waypoints={wp_summary} total={len(obs.waypoints)}"
                )
            # Goal resumption is terminal for RecoverDeath. Any missing corpse,
            # item, or E6 evidence cannot appear after this point, so fail fast.
            if resumed:
                break

        recovery_records = body.actions[recovery_action_offset:]
        recovery_observations = body.observations[recovery_observation_offset:]
        verified_responses = [
            record.action
            for record in recovery_records
            if isinstance(record.action, GumpResponse)
            and any(
                gump.serial == record.action.serial
                and gump.gump_id == record.action.gump_id
                and RecoverDeath._is_free_resurrection(gump)
                for gump in record.gumps
            )
        ]
        all_gump_responses = [
            record.action for record in recovery_records if isinstance(record.action, GumpResponse)
        ]
        exact_waypoint_walks = [
            record.action
            for record in recovery_records
            if record.dead
            and any(
                waypoint.serial == healer_serial
                and waypoint.kind == WAYPOINT_RESURRECTION
                and waypoint.map == death_map_index
                and (waypoint.pos.x, waypoint.pos.y) == (healer_x, healer_y)
                and _same_tile(record.action, waypoint)
                for waypoint in record.waypoints
            )
        ]
        corpse_uses = [
            record.action
            for record in recovery_records
            if isinstance(record.action, Use)
            and corpse_readback is not None
            and record.action.serial == corpse_readback
        ]
        all_use_actions = [
            record.action for record in recovery_records if isinstance(record.action, Use)
        ]
        invalid_action_while_dead = any(
            record.dead and not isinstance(record.action, (WalkTo, TargetCancel, GumpResponse))
            for record in recovery_records
        )
        e5_then_e6 = healer_waypoint_seen and healer_waypoint_removed and generation_and_map_stable
        no_fixture_target = recovery.resurrection_target is None
        no_gm_during_recovery = gm_closed_before_recovery

        if not resumed or not item_recovered:
            print(
                "[TRACE] recovery_actions="
                + repr(
                    [
                        (
                            type(record.action).__name__,
                            getattr(record.action, "serial", None),
                            record.dead,
                            (record.pos.x, record.pos.y),
                        )
                        for record in recovery_records
                    ]
                )
            )
            print(f"[TRACE] recovery_memory={agent.memory}")
            print(
                "[TRACE] final_waypoints="
                + repr(
                    [
                        (w.serial, w.kind, w.map, w.pos.x, w.pos.y, w.name)
                        for w in (body.last_obs.waypoints[:20] if body.last_obs else [])
                    ]
                )
            )

        flags = {
            "schema_v8_ready": ipc.ready.get("schema_version") == 8,
            "healer_staged_three_tiles_away": (
                healer_serial is not None
                and chebyshev(death_pos, Position(healer_x, healer_y, staged_z)) == HEALER_OFFSET
            ),
            "young_item_protection_disabled": young_disabled,
            "death_injected": killed,
            "corpse_readback_available": corpse_readback is not None,
            "predeath_goal_and_ownership_snapshot_started": (
                predeath_goal_started and dagger_before is not None
            ),
            "gm_connection_closed_before_recovery": gm_closed_before_recovery,
            "recover_death_has_no_coordinate_fixture": no_fixture_target,
            "dead_observed_after_gm_close": dead_seen,
            "exact_healer_e5_observed": healer_waypoint_seen,
            "healer_waypoint_selected_by_serial": healer_waypoint_selected,
            "walkto_exact_observed_waypoint": bool(exact_waypoint_walks),
            "ghost_movement_observed": ghost_moved,
            "only_free_resurrection_accepted_once": (
                len(verified_responses) == 1 and len(all_gump_responses) == 1
            ),
            "alive_again": alive_again,
            "bridge_generation_and_map_stable_through_e6": generation_and_map_stable,
            "healer_e6_removal_observed": e5_then_e6,
            "dead_action_whitelist_respected": not invalid_action_while_dead,
            "strong_exact_corpse_selected": strong_corpse_selected,
            "selected_exact_own_corpse_once": (len(corpse_uses) == 1 and len(all_use_actions) == 1),
            "predeath_item_seen_in_corpse_then_recovered": item_recovered,
            "original_goal_preserved_and_resumed": (goal_preserved and resumed),
            "no_gm_connection_during_recovery": no_gm_during_recovery,
        }
        detail = (
            f"serial={serial} healer={healer_serial} corpse={corpse_readback} "
            f"ticks={ticks_used} corpse_e5_seen={corpse_waypoint_seen} "
            f"generation={recovery_generation} map={death_map_index} "
            f"recovery_observations={len(recovery_observations)}"
        )
        return flags, detail


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2594)
    parser.add_argument("--bridge", default=None)
    parser.add_argument("--suffix", default=fresh_suffix())
    parser.add_argument("--account", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--pump-ms", type=int, default=400)
    parser.add_argument("--response-timeout", type=float, default=8.0)
    parser.add_argument("--recovery-ticks", type=int, default=300)
    parser.add_argument("--snapshot-every", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        flags, detail = _run(args)
    except Exception as exc:
        print(f"[FLAG] A4 WAYPOINT RECOVERY GATE FAILED: {type(exc).__name__}: {exc}")
        return 1
    return (
        0
        if print_gate_verdict(
            flags,
            label="A4 WAYPOINT RECOVERY GATE",
            detail=detail,
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
