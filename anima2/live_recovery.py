"""Live gate for A2: poison cure, safe resurrection, and own-corpse recovery."""

from __future__ import annotations

import argparse
import re

from .agent import Agent
from .contract import (
    Action,
    GumpResponse,
    Observation,
    Say,
    TargetCancel,
    TargetObject,
    Use,
    WalkTo,
)
from .control import GmControl
from .ipc_body import IpcBody
from .live_common import fresh_suffix, login_throttle, print_gate_verdict, wipe_area
from .persona import Persona
from .planner import Planner
from .profession import HUNTING_SPOT
from .skills import RecoverDeath, Survive
from .skills.base import Goal, Skill, SkillContext, SkillResult, Status
from .skills.harvest import BACKPACK_LAYER
from .skills.survival import BANDAGE_GRAPHICS

DAGGER_GRAPHIC = 0x0F52


class _RecordingBody:
    def __init__(self, inner: IpcBody) -> None:
        self.inner = inner
        self.last_obs: Observation | None = None
        self.actions: list[tuple[Action, bool]] = []
        self.action_gumps: list[list] = []

    @property
    def connected(self) -> bool:
        return self.inner.connected

    def observe(self) -> Observation:
        self.last_obs = self.inner.observe()
        return self.last_obs

    def act(self, action: Action) -> None:
        dead = self.last_obs.player.dead if self.last_obs is not None else False
        self.actions.append((action, dead))
        self.action_gumps.append(list(self.last_obs.gumps) if self.last_obs is not None else [])
        self.inner.act(action)


class _GoalWorkProbe(Skill):
    """A two-step goal whose second action must resume after death recovery."""

    name = "live_recovery_goal_probe"
    consumes_goal = True

    def can_run(self, ctx: SkillContext) -> bool:
        return ctx.goal is not None and ctx.goal.kind == "live_recovery_probe"

    def step(self, ctx: SkillContext) -> SkillResult:
        phase = int(ctx.memory.get("live_recovery_goal_phase", 0))
        if phase == 0:
            ctx.memory["live_recovery_goal_phase"] = 1
            return SkillResult(Status.RUNNING, Say("Working before death."))
        ctx.memory["live_recovery_goal_phase"] = 2
        return SkillResult(Status.SUCCESS, Say("Resumed the same goal."))


def _backpack(obs: Observation):
    return next(
        (
            item for item in obs.items
            if item.layer == BACKPACK_LAYER and item.container == obs.player.serial
        ),
        None,
    )


def _bandages(obs: Observation) -> int:
    backpack = _backpack(obs)
    if backpack is None:
        return 0
    return sum(
        item.amount for item in obs.items
        if item.graphic in BANDAGE_GRAPHICS and item.container == backpack.serial
    )


def _pack_item(obs: Observation, graphic: int):
    backpack = _backpack(obs)
    if backpack is None:
        return None
    return next(
        (item for item in obs.items if item.graphic == graphic and item.container == backpack.serial),
        None,
    )


def _property_serial(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"0x([0-9a-fA-F]+)", value)
    return int(match.group(1), 16) if match else None


def _run(args: argparse.Namespace) -> dict[str, bool]:
    account = f"recover{args.suffix}"
    x, y = HUNTING_SPOT
    with IpcBody.spawn(args.host, args.port, account, account, pump_ms=400) as ipc:
        serial = ipc.ready["player"]["serial"]
        login_throttle()
        with GmControl.spawn(args.host, args.port) as gm:
            gm.hide()
            wipe_area(gm, x, y, radius=10, z=15)
            gm.stage(
                serial,
                x,
                y,
                skills={"Healing": 100, "Anatomy": 100},
                items=["Bandage 20", "Dagger 1"],
            )
            young_disabled = gm.command_on("[Set Young false", serial)

            healer_serial = None
            # Use the exact staged tile so the healer shares the ridge's z=15;
            # five tiles east drops to z=-15 and fails LOS despite looking close
            # in 2D. RecoverDeath's bounded exit/re-entry handles starting inside
            # the healer trigger radius.
            if gm.command_at("[Add Healer", x, y, 15):
                healer = gm.find_mobile_near(x, y, max_dist=2, exclude={serial})
                if healer is not None:
                    healer_serial = healer.serial
                    gm.command_on("[Set CantWalk true", healer.serial)
                    healer_target = (healer.pos.x, healer.pos.y)
                else:
                    healer_target = (x, y)
            else:
                healer_target = (x, y)
            healer_title = (
                gm.get_property("Title", healer_serial) if healer_serial is not None else None
            )

            body = _RecordingBody(ipc)
            for _ in range(3):
                body.observe()
            assert body.last_obs is not None
            recovery = RecoverDeath(resurrection_target=healer_target)
            agent = Agent(
                body=body,
                persona=Persona(name="Ragnar", combat_disposition="aggressive"),
                planner=Planner([Survive(), recovery, _GoalWorkProbe()]),
            )

            # Leg P: inject real poison and require observation-confirmed cure.
            poison_staged = gm.command_on("[Set Poison Lesser", serial)
            poison_seen = False
            poison_offset = len(body.actions)
            poison_start_bandages = _bandages(body.last_obs)
            poison_cleared = False
            poison_ticks_used = 0
            for poison_tick in range(args.poison_ticks):
                agent.tick()
                poison_ticks_used = poison_tick + 1
                assert body.last_obs is not None
                poison_seen = poison_seen or body.last_obs.player.poisoned
                if poison_seen and not body.last_obs.player.poisoned:
                    poison_cleared = True
                    break
            assert body.last_obs is not None
            poison_actions = [action for action, _ in body.actions[poison_offset:]]
            poison_end_bandages = _bandages(body.last_obs)

            # Leg D: kill the same character. The staged healer is the only
            # fixture hint; every death/gump/corpse transition is driven through
            # the real bridge and ordinary Agent ticks.
            dagger_before = _pack_item(body.last_obs, DAGGER_GRAPHIC)
            goal_before = Goal(
                kind="live_recovery_probe",
                params={"token": f"goal-{args.suffix}"},
            )
            agent.goal = goal_before
            predeath_action = agent.tick()
            predeath_goal_started = (
                isinstance(predeath_action, Say)
                and predeath_action.text == "Working before death."
                and agent.goal == goal_before
                and agent.memory.get("live_recovery_goal_phase") == 1
            )
            death_offset = len(body.actions)
            death_gump_offset = len(body.action_gumps)
            killed = gm.command_on("[Kill", serial)
            frozen_after_kill = gm.get_property("Frozen", serial)
            criminal_after_kill = gm.get_property("Criminal", serial)
            karma_after_kill = gm.get_property("Karma", serial)
            gm.command_on("[Set Frozen false", serial)
            corpse_readback = _property_serial(gm.get_property("Corpse", serial))

            dead_seen = False
            ghost_body_seen = False
            alive_again = False
            corpse_recovered = False
            resumed = False
            goal_preserved = True
            death_ticks_used = 0
            dead_positions: list[tuple[int, int]] = []
            seen_gumps: list[tuple[int, list[dict]]] = []
            death_journal: list[tuple[int, str]] = []
            hp_trace: list[tuple[int, int, bool, str | None, int]] = []
            for death_tick in range(args.death_ticks):
                agent.tick()
                death_ticks_used = death_tick + 1
                assert body.last_obs is not None
                hp_trace.append(
                    (
                        death_tick,
                        body.last_obs.player.hits,
                        body.last_obs.player.dead,
                        agent.memory.get("survival_bandage_phase"),
                        int(agent.memory.get("survival_bandage_wait", 0)),
                    )
                )
                death_journal.extend(
                    (entry.cliloc, entry.text) for entry in body.last_obs.new_journal
                )
                if body.last_obs.player.dead:
                    dead_positions.append(
                        (body.last_obs.player.pos.x, body.last_obs.player.pos.y)
                    )
                    seen_gumps.extend(
                        (gump.gump_id, gump.elements) for gump in body.last_obs.gumps
                    )
                dead_seen = dead_seen or body.last_obs.player.dead
                if body.last_obs.player.dead or agent.memory.get(recovery._CORPSE_PENDING):
                    goal_preserved = goal_preserved and agent.goal == goal_before
                ghost_body_seen = ghost_body_seen or (
                    body.last_obs.player.dead and body.last_obs.player.body in {
                        402, 403, 607, 608, 694, 695, 970,
                    }
                )
                alive_again = dead_seen and not body.last_obs.player.dead
                current_dagger = _pack_item(body.last_obs, DAGGER_GRAPHIC)
                corpse_recovered = (
                    alive_again
                    and dagger_before is not None
                    and current_dagger is not None
                    and current_dagger.serial == dagger_before.serial
                )
                if alive_again and not agent.memory.get(recovery._CORPSE_PENDING):
                    if (
                        agent.memory.get("live_recovery_goal_phase") == 2
                        and agent.goal is None
                    ):
                        resumed = True
                        break
            death_records = body.actions[death_offset:]
            death_action_gumps = body.action_gumps[death_gump_offset:]
            death_actions = [action for action, _ in death_records]
            corpse_uses = [
                action.serial for action in death_actions
                if isinstance(action, Use) and action.serial == corpse_readback
            ]
            res_responses = [
                action for action in death_actions
                if isinstance(action, GumpResponse) and action.button == 1
            ]
            invalid_action_while_dead = any(
                was_dead
                and not isinstance(action, (WalkTo, TargetCancel, GumpResponse))
                for action, was_dead in death_records
            )
            verified_responses = [
                action
                for (action, _), gumps in zip(
                    death_records, death_action_gumps, strict=True
                )
                if isinstance(action, GumpResponse)
                and any(
                    gump.serial == action.serial
                    and gump.gump_id == action.gump_id
                    and RecoverDeath._is_free_resurrection(gump)
                    for gump in gumps
                )
            ]
            if not corpse_uses or not resumed:
                print(
                    "[TRACE] death_actions_detail="
                    + repr(
                        [
                            (type(action).__name__, getattr(action, "serial", None), was_dead)
                            for action, was_dead in death_records
                        ]
                    )
                )
                print(
                    f"[TRACE] corpse_readback={corpse_readback} dagger_before="
                    f"{getattr(dagger_before, 'serial', None)} memory={agent.memory}"
                )
                print(
                    "[TRACE] final_items="
                    + repr(
                        [
                            (item.serial, item.graphic, item.amount, item.container, item.layer)
                            for item in body.last_obs.items
                            if item.graphic in {DAGGER_GRAPHIC, 0x2006, 0x0E75}
                        ]
                    )
                )
                print(f"[TRACE] death_journal={death_journal[-40:]}")
                print(f"[TRACE] hp_trace={hp_trace[-80:]}")
                print(
                    "[TRACE] final_skills="
                    + repr(
                        [
                            (skill.id, skill.value, skill.base)
                            for skill in body.last_obs.skills
                            if skill.id in {1, 17}
                        ]
                    )
                )
                print(
                    f"[TRACE] goal_started={predeath_goal_started} "
                    f"goal_preserved={goal_preserved} ticks_used={death_ticks_used}"
                )
            if not alive_again:
                print(
                    "[TRACE] death_actions="
                    + ",".join(type(action).__name__ for action in death_actions)
                )
                print(
                    f"[TRACE] healer_target={healer_target} positions="
                    f"{dead_positions[:8]}...{dead_positions[-8:]}"
                )
                print(f"[TRACE] dead_gumps={seen_gumps[-3:]}")
                print(
                    "[TRACE] visible_mobiles="
                    + repr(
                        [
                            (m.serial, m.name, m.pos.x, m.pos.y, m.pos.z, m.body, m.notoriety)
                            for m in body.last_obs.mobiles
                        ]
                    )
                )
                print(f"[TRACE] healer_serial={healer_serial} title={healer_title!r}")
                print(
                    f"[TRACE] frozen={frozen_after_kill!r} criminal="
                    f"{criminal_after_kill!r} karma={karma_after_kill!r}"
                )
                print(f"[TRACE] death_journal={death_journal[-12:]}")
                print(f"[TRACE] recovery_memory={agent.memory}")

            print(
                f"[METRIC] poison_ticks={poison_ticks_used}/{args.poison_ticks} "
                f"death_ticks={death_ticks_used}/{args.death_ticks} pump_ms=400"
            )

    return {
        "contract_body_present": ipc.ready["player"].get("body", 0) > 0,
        "contract_poison_key_present": "poisoned" in ipc.ready["player"],
        "contract_dead_key_present": "dead" in ipc.ready["player"],
        "poison_staged": poison_staged,
        "poison_observed": poison_seen,
        "poison_bandage_used": any(isinstance(action, Use) for action in poison_actions),
        "poison_self_targeted": any(
            isinstance(action, TargetObject) and action.serial == serial
            for action in poison_actions
        ),
        "poison_cleared": poison_cleared,
        "poison_bandage_consumed": poison_end_bandages < poison_start_bandages,
        "healer_staged": healer_serial is not None and "healer" in (healer_title or "").lower(),
        "death_injected": killed,
        "young_item_protection_disabled": young_disabled,
        "dead_observed": dead_seen,
        "ghost_body_observed": ghost_body_seen,
        "dead_action_whitelist_respected": not invalid_action_while_dead,
        "free_resurrection_accepted_once": (
            len(res_responses) == 1 and len(verified_responses) == 1
        ),
        "alive_again": alive_again,
        "corpse_readback_available": corpse_readback is not None,
        "selected_exact_own_corpse": len(corpse_uses) == 1,
        "predeath_item_recovered": corpse_recovered,
        "original_goal_preserved_and_resumed": (
            predeath_goal_started and goal_preserved and resumed
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2594)
    parser.add_argument("--suffix", default=fresh_suffix())
    parser.add_argument("--poison-ticks", type=int, default=60)
    parser.add_argument("--death-ticks", type=int, default=240)
    args = parser.parse_args(argv)
    return 0 if print_gate_verdict(_run(args), label="AUTONOMY A2") else 1


if __name__ == "__main__":
    raise SystemExit(main())
