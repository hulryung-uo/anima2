"""Live B2 gate: curriculum observation -> validated goal -> allowed skill.

This gate deliberately starts one milestone immediately below its threshold:
the miner has nine ingots, while the two earlier miner milestones are already
true in the first live post-staging observation.  The GM connection is closed
before the ``Agent`` or ``CurriculumController`` is constructed.  From there,
the goal-driving curriculum must:

* observe that ``miner_hold_10_ingots`` is the sole unachieved milestone;
* enqueue one validator-approved curriculum goal;
* select the profession planner's own ``MineSmeltDeliver`` instance; and
* actually smelt staged ore through ``Use(ore) -> TargetObject(forge)``, then
  complete the exact frame after a live observation reports at least 10 ingots.

The staged 20 ore makes the proof deterministic and short without making it
vacuous: ingots must still cross from exactly 9 to >=10 after the control-plane
trust boundary has closed.  No GM read or write is used during goal work.

This module targets the B2 opt-in API::

    CurriculumController(..., drive_goals=True)
    Profession.planner(curriculum_goals=True)
    Agent(..., goal_validator=controller.validate_goal)

Usage::

    python -m anima2.live_curriculum_goal --suffix b2manual
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import Agent
from .cognition import HeuristicCognition
from .contract import Action, Observation, Position, TargetObject, Use
from .control import GmControl
from .curriculum import CurriculumController
from .goals import GoalOutcome
from .ipc_body import ResilientIpcBody, SUPPORTED_SCHEMA_VERSION
from .live_common import (
    GM_RELOGIN_COOLDOWN_S,
    fresh_suffix,
    login_throttle,
    print_gate_verdict,
    wipe_area,
)
from .llm import StubLLMClient
from .persona import Persona
from .profession import PROFESSIONS, TRADE_MINE_SPOT
from .skills.base import Goal, Skill, SkillContext
from .skills.harvest import BACKPACK_LAYER, PICKAXE_GRAPHICS
from .skills.smelt import FORGE_GRAPHICS, INGOT_GRAPHICS, ORE_GRAPHICS

_TARGET_MILESTONE = "miner_hold_10_ingots"
_WORK_SKILL = "mine_smelt_deliver"
_MINING_SKILL_ID = 45


@dataclass(frozen=True)
class _ActionRecord:
    action: Action
    pos: Position
    observation_index: int


class _RecordingBody:
    """Record the exact live observation against which each action was sent."""

    def __init__(self, inner: ResilientIpcBody) -> None:
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
                pos=self.last_obs.player.pos,
                observation_index=len(self.observations) - 1,
            )
        )
        self.inner.act(action)


@dataclass(frozen=True)
class _SelectionRecord:
    skill: Skill
    goal: Goal | None
    goal_id: int | None
    action_offset: int


class _TracingPlanner:
    """Transparent Planner proxy that proves which shipped instance ran."""

    def __init__(self, inner: Any, body: _RecordingBody) -> None:
        self.inner = inner
        self.body = body
        self.skills = inner.skills
        self.selections: list[_SelectionRecord] = []

    def preselect_interrupt(self, ctx: SkillContext):
        return self.inner.preselect_interrupt(ctx)

    def select_cached(self, ctx: SkillContext, applicability: dict[int, bool]) -> Skill:
        skill = self.inner.select_cached(ctx, applicability)
        self.selections.append(
            _SelectionRecord(
                skill=skill,
                goal=ctx.goal,
                goal_id=ctx.goal_id,
                action_offset=len(self.body.actions),
            )
        )
        return skill

    def select(self, ctx: SkillContext) -> Skill:
        return self.inner.select(ctx)


def _backpack(obs: Observation):
    return next(
        (
            item
            for item in obs.items
            if item.layer == BACKPACK_LAYER and item.container == obs.player.serial
        ),
        None,
    )


def _pack_amount(obs: Observation, graphics: frozenset[int]) -> int:
    backpack = _backpack(obs)
    if backpack is None:
        return 0
    return sum(
        item.amount
        for item in obs.items
        if item.graphic in graphics and item.container == backpack.serial
    )


def _pack_serials(obs: Observation, graphics: frozenset[int]) -> set[int]:
    backpack = _backpack(obs)
    if backpack is None:
        return set()
    return {
        item.serial
        for item in obs.items
        if item.graphic in graphics and item.container == backpack.serial
    }


def _mining_base(obs: Observation) -> float | None:
    skill = next((entry for entry in obs.skills if entry.id == _MINING_SKILL_ID), None)
    return skill.base if skill is not None else None


def _goal_milestone(goal: Goal | None) -> str | None:
    if goal is None:
        return None
    for key in ("milestone", "curriculum_milestone", "name"):
        value = goal.params.get(key)
        if isinstance(value, str):
            return value
    return None


def _milestone_episodes(agent: Agent) -> list:
    return [
        episode
        for episode in agent.episodes.recent(9999)
        if episode.kind == "milestone" and _TARGET_MILESTONE in episode.summary
    ]


def _run(args: argparse.Namespace) -> tuple[dict[str, bool], str]:
    account = args.account or f"animab2{args.suffix}"
    password = args.password or account
    mine_x, mine_y = TRADE_MINE_SPOT
    ledger_dir = Path(args.milestones_dir)
    ledger_path = ledger_dir / f"{account}.jsonl"

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
        login_throttle(GM_RELOGIN_COOLDOWN_S)

        with GmControl.spawn(args.host, args.port, bridge=args.bridge) as gm:
            gm.hide()
            wipe_area(gm, mine_x, mine_y, radius=8, z=20)
            gx, gy, gz = gm.stage(
                serial,
                mine_x,
                mine_y,
                skills={"Mining": 51},
                items=["Pickaxe", "IronOre 20", "IronIngot 9"],
            )
            forge_staged = gm.command_at("[Add Forge", gx + 1, gy + 1, gz)
            print(
                f"GM staged B2 miner; subject=0x{serial:X} pos=({gx},{gy},{gz}) "
                "Mining=51 ore=20 ingots=9 forge=(+1,+1); closing GM"
            )

        # Trust boundary: no controller, Agent, goal, or work tick exists until
        # the sole control-plane connection is closed.  These observations only
        # let the teleport and AddToPack/forge packets settle.
        gm_closed_before_agent = not gm.body.connected
        body = _RecordingBody(ipc)
        for _ in range(4):
            body.observe()
        assert body.last_obs is not None
        baseline = body.last_obs
        baseline_mining = _mining_base(baseline)
        baseline_ore = _pack_amount(baseline, ORE_GRAPHICS)
        baseline_ingots = _pack_amount(baseline, INGOT_GRAPHICS)
        baseline_ore_serials = _pack_serials(baseline, ORE_GRAPHICS)
        baseline_tool_serials = _pack_serials(baseline, PICKAXE_GRAPHICS)
        visible_forges = [
            item for item in baseline.items
            if item.graphic in FORGE_GRAPHICS and item.distance <= 2
        ]
        forge_serials = {item.serial for item in visible_forges}

        # Garbage can never invent a task: with the first two miner milestones
        # already true, the deterministic sole-eligible path must choose the
        # ingot milestone without consulting or trusting this client.
        controller = CurriculumController(
            HeuristicCognition(),
            StubLLMClient("not JSON and not a milestone"),
            "Grimm",
            "miner",
            every_n_reconsiders=1,
            min_new_episodes=1,
            milestones_path=ledger_path,
            drive_goals=True,
        )
        profession = PROFESSIONS["miner"]
        profession_planner = profession.planner(curriculum_goals=True)
        allowed_work = next(
            (skill for skill in profession_planner.skills if skill.name == _WORK_SKILL),
            None,
        )
        planner = _TracingPlanner(profession_planner, body)
        agent = Agent(
            body=body,
            persona=Persona(name="Grimm"),
            planner=planner,
            cognition=controller,
            cognition_interval=1,
            goal_validator=controller.validate_goal,
            goal_progress=controller.goal_progress,
        )
        controller.episodes = agent.episodes

        picked_target = False
        target_goal: Goal | None = None
        target_goal_id: int | None = None
        completion_tick: int | None = None
        max_ingots = baseline_ingots

        for tick in range(args.ticks):
            agent.tick()
            assert body.last_obs is not None
            max_ingots = max(max_ingots, _pack_amount(body.last_obs, INGOT_GRAPHICS))
            picked_target = picked_target or controller.current_milestone == _TARGET_MILESTONE

            for selection in planner.selections:
                if (
                    selection.skill is allowed_work
                    and _goal_milestone(selection.goal) == _TARGET_MILESTONE
                ):
                    target_goal = selection.goal
                    target_goal_id = selection.goal_id
                    break

            if target_goal_id is not None:
                matches = [
                    frame for frame in agent.goal_stack.history
                    if frame.id == target_goal_id and frame.outcome is GoalOutcome.SUCCESS
                ]
                if matches and _pack_amount(body.last_obs, INGOT_GRAPHICS) >= 10:
                    if completion_tick is None:
                        completion_tick = tick
                    controller.wait_idle(timeout=2.0)
                    if tick >= completion_tick + args.settle_ticks:
                        break

            if tick % args.snapshot_every == 0:
                frame = agent.goal_stack.current
                print(
                    f"tick={tick:03d} mining={_mining_base(body.last_obs)} "
                    f"ore={_pack_amount(body.last_obs, ORE_GRAPHICS)} "
                    f"ingots={_pack_amount(body.last_obs, INGOT_GRAPHICS)} "
                    f"pick={controller.current_milestone!r} "
                    f"goal={_goal_milestone(frame.goal) if frame else None!r}"
                )

        controller.wait_idle(timeout=5.0)
        assert body.last_obs is not None
        final_obs = body.last_obs
        final_ore = _pack_amount(final_obs, ORE_GRAPHICS)
        final_ingots = _pack_amount(final_obs, INGOT_GRAPHICS)

        target_selections = [
            selection
            for selection in planner.selections
            if selection.skill is allowed_work
            and _goal_milestone(selection.goal) == _TARGET_MILESTONE
        ]
        first_work_offset = (
            target_selections[0].action_offset if target_selections else len(body.actions)
        )
        goal_actions = body.actions[first_work_offset:]
        ore_use_indices = [
            index
            for index, record in enumerate(goal_actions)
            if isinstance(record.action, Use)
            and record.action.serial in baseline_ore_serials
        ]
        forge_target_indices = [
            index
            for index, record in enumerate(goal_actions)
            if isinstance(record.action, TargetObject)
            and record.action.serial in forge_serials
        ]
        ordered_real_smelt = bool(
            ore_use_indices
            and forge_target_indices
            and ore_use_indices[0] < forge_target_indices[0]
        )

        first_target_selection = next(
            (
                index
                for index, selection in enumerate(planner.selections)
                if selection.skill is allowed_work
                and _goal_milestone(selection.goal) == _TARGET_MILESTONE
            ),
            len(planner.selections),
        )
        work_without_validated_goal = any(
            selection.skill is allowed_work
            for selection in planner.selections[:first_target_selection]
        )
        goal_frames = [
            frame
            for frame in (*agent.goal_stack.frames, *agent.goal_stack.history)
            if _goal_milestone(frame.goal) == _TARGET_MILESTONE
        ]
        terminal_matches = [
            frame
            for frame in agent.goal_stack.history
            if target_goal_id is not None and frame.id == target_goal_id
        ]
        target_episodes = _milestone_episodes(agent)

        if not ordered_real_smelt or final_ingots < 10 or len(terminal_matches) != 1:
            print(
                "[TRACE] selections="
                + repr(
                    [
                        (record.skill.name, record.goal_id, _goal_milestone(record.goal))
                        for record in planner.selections
                    ]
                )
            )
            print(
                "[TRACE] actions="
                + repr(
                    [
                        (type(record.action).__name__, getattr(record.action, "serial", None))
                        for record in body.actions
                    ]
                )
            )

        flags = {
            "schema_ready": ipc.ready.get("schema_version") == SUPPORTED_SCHEMA_VERSION,
            "gm_fixture_staged": forge_staged,
            "gm_connection_closed_before_agent": gm_closed_before_agent,
            "live_baseline_has_tool_ore_and_forge": bool(
                baseline_tool_serials and baseline_ore_serials and visible_forges
            ),
            "live_baseline_prerequisites_true_target_false": bool(
                baseline_mining is not None
                and baseline_mining >= 50.0
                and baseline_ore >= 20
                and baseline_ingots == 9
            ),
            "sole_curriculum_target_observed": picked_target,
            "validated_goal_enqueued_once": bool(
                target_goal is not None
                and target_goal_id is not None
                and len(goal_frames) == 1
            ),
            "no_profession_work_before_validated_goal": not work_without_validated_goal,
            "profession_allowed_instance_selected": bool(
                allowed_work is not None and target_selections
                and all(selection.skill is allowed_work for selection in target_selections)
            ),
            "real_use_then_forge_target_emitted": ordered_real_smelt,
            "live_ore_decreased_and_ingots_crossed_9_to_10": bool(
                final_ore < baseline_ore and baseline_ingots == 9 and max_ingots >= 10
            ),
            "exact_goal_frame_succeeded_once": bool(
                len(terminal_matches) == 1
                and terminal_matches[0].outcome is GoalOutcome.SUCCESS
            ),
            "milestone_recorded_exactly_once": len(target_episodes) == 1,
            "same_goal_not_reenqueued_during_settle": len(goal_frames) == 1,
        }
        detail = (
            f"serial={serial} baseline=(Mining={baseline_mining},ore={baseline_ore},"
            f"ingots={baseline_ingots}) final=(ore={final_ore},ingots={final_ingots}) "
            f"goal_id={target_goal_id} selections={len(target_selections)} "
            f"episodes={len(target_episodes)} ledger={ledger_path}"
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
    parser.add_argument("--ticks", type=int, default=100)
    parser.add_argument("--settle-ticks", type=int, default=5)
    parser.add_argument("--snapshot-every", type=int, default=5)
    parser.add_argument(
        "--milestones-dir",
        default="data/curriculum_goal_livegate",
        help="isolated milestone-ledger directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        flags, detail = _run(args)
    except Exception as exc:
        print(f"[FLAG] B2 CURRICULUM GOAL GATE FAILED: {type(exc).__name__}: {exc}")
        return 1
    return 0 if print_gate_verdict(
        flags,
        label="B2 CURRICULUM GOAL GATE",
        detail=detail,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
