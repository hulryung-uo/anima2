"""Live gate for AUTONOMY-ROADMAP slice A1: flee, then bandage.

Runs one fresh character through two staged legs on ServUO using the real
`anima-client` bridge:

1. wounded and alone -> one bandage Use/TargetObject(self) exchange -> HP rises;
2. wounded with three pinned hostiles east -> running steps away from their
   observed centroid increase distance before the bounded bandage fallback.

Usage: python -m anima2.live_survival [--host H] [--port P] [--suffix S]
"""

from __future__ import annotations

import argparse

from .agent import Agent
from .contract import Action, Observation, TargetObject, Use, Walk
from .control import GmControl
from .ipc_body import IpcBody
from .live_common import fresh_suffix, login_throttle, print_gate_verdict, wipe_area
from .persona import Persona
from .planner import Planner
from .profession import HUNTING_SPOT
from .skills import Survive
from .skills.base import Skill, SkillContext, SkillResult, Status
from .skills.harvest import BACKPACK_LAYER
from .skills.survival import BANDAGE_GRAPHICS


class _RecordingBody:
    def __init__(self, inner: IpcBody) -> None:
        self.inner = inner
        self.last_obs: Observation | None = None
        self.actions: list[Action] = []

    @property
    def connected(self) -> bool:
        return self.inner.connected

    def observe(self) -> Observation:
        self.last_obs = self.inner.observe()
        return self.last_obs

    def act(self, action: Action) -> None:
        self.actions.append(action)
        self.inner.act(action)


class _Idle(Skill):
    """Always-runnable gate fallback; never force an inapplicable Survive step."""

    name = "live_gate_idle"

    def step(self, ctx: SkillContext) -> SkillResult:
        return SkillResult(Status.RUNNING, None)


def _bandages(obs: Observation) -> int:
    backpack = next(
        (item for item in obs.items
         if item.layer == BACKPACK_LAYER and item.container == obs.player.serial),
        None,
    )
    if backpack is None:
        return 0
    return sum(
        item.amount for item in obs.items
        if item.graphic in BANDAGE_GRAPHICS and item.container == backpack.serial
    )


def _run(args: argparse.Namespace) -> dict[str, bool]:
    account = f"survive{args.suffix}"
    x, y = HUNTING_SPOT
    with IpcBody.spawn(args.host, args.port, account, account, pump_ms=400) as ipc:
        serial = ipc.ready["player"]["serial"]
        login_throttle()
        with GmControl.spawn(args.host, args.port) as gm:
            gm.hide()
            wipe_area(gm, x, y, radius=8, z=15)
            # Deterministic gate: Healing 100 makes the normal HP-heal roll
            # certain, while the shipped hunter baseline remains 50 for ordinary
            # progression. Protocol/state-machine behavior is what this gate pins.
            gm.stage(serial, x, y, skills={"Healing": 100, "Anatomy": 100}, items=["Bandage 10"])
            gm.command_on("[Set Hits 30", serial)

            body = _RecordingBody(ipc)
            for _ in range(3):
                body.observe()
            assert body.last_obs is not None
            start_bandages = _bandages(body.last_obs)
            start_hp = body.last_obs.player.hits
            agent = Agent(
                body=body,
                persona=Persona(name="Ragnar", combat_disposition="aggressive"),
                planner=Planner([Survive(), _Idle()]),
            )

            healed = False
            for _ in range(args.heal_ticks):
                agent.tick()
                obs = body.last_obs
                if obs is not None and obs.player.hits > start_hp:
                    healed = True
                    break
            for _ in range(3):
                body.observe()
            assert body.last_obs is not None
            heal_actions = list(body.actions)
            bandages_after = _bandages(body.last_obs)

            # Leg B: reset HP, add three stationary enemies east of the player,
            # then observe the actual normal-character movement.
            blessed_set = gm.command_on("[Set Blessed true", serial)
            spawned: list[int] = []
            for dx, dy in ((2, -1), (2, 0), (2, 1)):
                if gm.command_at("[Add Mongbat", body.last_obs.player.pos.x + dx,
                                 body.last_obs.player.pos.y + dy, body.last_obs.player.pos.z):
                    mob = gm.find_mobile_near(
                        body.last_obs.player.pos.x + dx,
                        body.last_obs.player.pos.y + dy,
                        max_dist=2,
                        exclude={serial, *spawned},
                    )
                    if mob is not None:
                        spawned.append(mob.serial)
                        gm.command_on("[Set CantWalk true", mob.serial)

            # Spawning/pinning takes several seconds, enough for passive HP
            # regeneration to invalidate an earlier reset. Isolate combat damage
            # and stage HP only after the hostile scene is complete.
            hp_staged = gm.command_on("[Set Hits 30", serial)
            agent.memory.clear()
            for _ in range(3):
                body.observe()
            assert body.last_obs is not None
            flee_start_x = body.last_obs.player.pos.x
            start_distance = min(
                (m.distance for m in body.last_obs.mobiles if m.serial in spawned),
                default=0,
            )
            leg_b_start_hp = body.last_obs.player.hits
            leg_b_start_bandages = _bandages(body.last_obs)
            action_offset = len(body.actions)
            leg_b_healed = False
            for _ in range(args.flee_ticks):
                agent.tick()
                if body.last_obs is not None and body.last_obs.player.hits > leg_b_start_hp:
                    leg_b_healed = True
                    break
            for _ in range(3):
                body.observe()
            assert body.last_obs is not None
            flee_actions = body.actions[action_offset:]
            end_distance = min(
                (m.distance for m in body.last_obs.mobiles if m.serial in spawned),
                default=0,
            )
            leg_b_bandages_after = _bandages(body.last_obs)

            walk_indices = [i for i, action in enumerate(flee_actions) if isinstance(action, Walk)]
            use_indices = [i for i, action in enumerate(flee_actions) if isinstance(action, Use)]
            target_indices = [
                i for i, action in enumerate(flee_actions)
                if isinstance(action, TargetObject) and action.serial == serial
            ]
            leg_b_ordered = bool(
                walk_indices and use_indices and target_indices
                and walk_indices[0] < use_indices[0] < target_indices[0]
            )
            if not leg_b_ordered or not leg_b_healed:
                print(
                    "[TRACE] leg_b_actions="
                    + ",".join(type(action).__name__ for action in flee_actions)
                )
                print(
                    f"[TRACE] leg_b_walks={len(walk_indices)} uses={len(use_indices)} "
                    f"targets={len(target_indices)} hp={leg_b_start_hp}->"
                    f"{body.last_obs.player.hits} bandages={leg_b_start_bandages}->"
                    f"{leg_b_bandages_after}"
                )
            gm.command_on("[Set Blessed false", serial)

    return {
        "bandage_present": start_bandages >= 1,
        "bandage_used_once": sum(isinstance(a, Use) for a in heal_actions) == 1,
        "self_targeted": any(isinstance(a, TargetObject) and a.serial == serial for a in heal_actions),
        "hp_increased": healed,
        "bandage_consumed": bandages_after < start_bandages,
        "three_hostiles_staged": len(spawned) == 3,
        "leg_b_hp_staged": hp_staged and leg_b_start_hp == 30,
        "leg_b_damage_isolated": blessed_set,
        # `[Add` may settle a mobile one or two tiles off the requested point,
        # so the live centroid can legitimately call for NW/SW rather than due
        # west. The offline geometry test pins exact direction; live proves the
        # emitted move is a run and that it actually opens distance below.
        "running_flee_emitted": any(isinstance(a, Walk) and a.run for a in flee_actions),
        "player_moved_west": body.last_obs.player.pos.x < flee_start_x,
        "hostile_distance_increased": end_distance > start_distance,
        "leg_b_flee_bounded": 1 <= len(walk_indices) <= Survive.max_flee_steps,
        "leg_b_flee_then_bandage_ordered": leg_b_ordered,
        "leg_b_bandage_used_once": len(use_indices) == 1,
        "leg_b_self_targeted": len(target_indices) == 1,
        "leg_b_hp_increased": leg_b_healed,
        "leg_b_bandage_consumed": leg_b_bandages_after < leg_b_start_bandages,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2594)
    parser.add_argument("--suffix", default=fresh_suffix())
    parser.add_argument("--heal-ticks", type=int, default=60)
    parser.add_argument("--flee-ticks", type=int, default=60)
    args = parser.parse_args(argv)
    flags = _run(args)
    return 0 if print_gate_verdict(flags, label="AUTONOMY A1") else 1


if __name__ == "__main__":
    raise SystemExit(main())
