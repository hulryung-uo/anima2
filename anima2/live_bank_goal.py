"""Live B3/B4 gate: a closed bank capability drives one verified transaction.

The fixture gives a fresh blacksmith one exact 100-gold stack and stages a
pinned banker.  The GM connection then closes before the Policy, Agent, goals,
or any work tick exist.  A sequenced cognition first proposes a cross-
profession capability goal (which must fail closed without a bank action), then
the trusted ``capability_goal`` constructor's blacksmith bank goal.

The positive leg is deliberately provenance-heavy: the gate requires the exact
live sequence ``PopupRequest(banker) -> PopupSelect(Bank) -> PickUp(gold) ->
Drop(gold, bankbox)``, and the same gold serial must move from this character's
backpack into this character's bank box.  No hammer use, craft-gump answer, or
vendor sale is allowed.  Internal status/reward alone can never pass the gate.

Targets the B3 opt-in API::

    capabilities.capability_goal(...)
    capabilities.CapabilityPolicy(...)
    Profession.planner(capability_goals=True)

Usage::

    python -m anima2.live_bank_goal --suffix b3manual
    python -m anima2.live_bank_goal --autonomous --suffix b4manual
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from .agent import Agent
from .capability_cognition import CapabilityCognition
from .capabilities import (
    CapabilityPolicy,
    capability_goal,
    installed_binding_for_goal,
)
from .contract import (
    Action,
    Drop,
    GumpResponse,
    Observation,
    PickUp,
    PopupRequest,
    PopupSelect,
    SellItems,
    Use,
)
from .cognition import ThreadedCognition
from .control import GmControl
from .goals import GoalOutcome, GoalSource
from .ipc_body import ResilientIpcBody, SUPPORTED_SCHEMA_VERSION
from .live_common import (
    GM_RELOGIN_COOLDOWN_S,
    fresh_suffix,
    login_throttle,
    print_gate_verdict,
    wipe_area,
)
from .persona import Persona
from .profession import (
    BANKER_SPOT,
    PROFESSIONS,
    TRADE_SMITH_SPOT,
    CapabilityBoundSkill,
)
from .skills.base import Goal, Skill, SkillContext
from .skills.craft import SMITH_TOOL_GRAPHICS
from .skills.harvest import BACKPACK_LAYER
from .skills.market import BANKBOX_LAYER, BANK_CLILOC, GOLD_GRAPHIC, BankGold

_PROFESSION = "blacksmith"
_CAPABILITY = "bank_gold"


@dataclass(frozen=True)
class _ActionRecord:
    action: Action
    observation: Observation


class _RecordingBody:
    """Cache observations and pair every action with the one that caused it."""

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
        self.actions.append(_ActionRecord(action=action, observation=self.last_obs))
        self.inner.act(action)


@dataclass(frozen=True)
class _SelectionRecord:
    skill: Skill
    goal: Goal | None
    goal_id: int | None
    action_offset: int


class _SelectionTracer:
    """Observe exact Planner selections without wrapping its authority boundary."""

    def __init__(self, body: _RecordingBody) -> None:
        self.body = body
        self.selections: list[_SelectionRecord] = []

    def __call__(self, skill: Skill, ctx: SkillContext) -> None:
        self.selections.append(
            _SelectionRecord(skill, ctx.goal, ctx.goal_id, len(self.body.actions))
        )


class _InvalidThenValidCognition:
    """One live negative admission probe, one valid proposal, then no replay."""

    def __init__(self, invalid: Goal, valid: Goal) -> None:
        self.invalid = invalid
        self.valid = valid
        self.calls = 0

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        self.calls += 1
        if self.calls == 1:
            return self.invalid
        if self.calls == 2:
            return self.valid
        return ctx.goal


class _SequencedCapabilityClient:
    """One strict negative reply, one valid choice, then explicit idle."""

    def __init__(self) -> None:
        self.responses = [
            '{"schema":1,"decision":"capability","capability":"bank_gold",'
            '"action":"drop"}',
            '{"schema":1,"decision":"capability","capability":"bank_gold"}',
            '{"schema":1,"decision":"idle"}',
        ]
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def _backpack_serial(obs: Observation) -> int | None:
    backpack = next(
        (
            item
            for item in obs.items
            if item.layer == BACKPACK_LAYER and item.container == obs.player.serial
        ),
        None,
    )
    return backpack.serial if backpack is not None else None


def _bankbox_serial(obs: Observation) -> int | None:
    box = next(
        (
            item
            for item in obs.items
            if item.layer == BANKBOX_LAYER and item.container == obs.player.serial
        ),
        None,
    )
    return box.serial if box is not None else None


def _pack_gold(obs: Observation) -> int:
    backpack = _backpack_serial(obs)
    if backpack is None:
        return 0
    return sum(
        item.amount
        for item in obs.items
        if item.graphic == GOLD_GRAPHIC and item.container == backpack
    )


def _bank_gold(obs: Observation) -> int:
    box = _bankbox_serial(obs)
    if box is None:
        return 0
    return sum(
        item.amount
        for item in obs.items
        if item.graphic == GOLD_GRAPHIC and item.container == box
    )


def _pack_items(obs: Observation, graphics: frozenset[int]):
    backpack = _backpack_serial(obs)
    if backpack is None:
        return []
    return [
        item
        for item in obs.items
        if item.graphic in graphics and item.container == backpack
    ]


def _gold_item(obs: Observation, *, container: int | None):
    if container is None:
        return None
    return next(
        (
            item
            for item in obs.items
            if item.graphic == GOLD_GRAPHIC and item.container == container
        ),
        None,
    )


def _bank_popup_select(record: _ActionRecord, banker_serial: int) -> bool:
    action = record.action
    popup = record.observation.popup
    if (
        not isinstance(action, PopupSelect)
        or action.serial != banker_serial
        or popup is None
        or popup.serial != banker_serial
    ):
        return False
    return any(
        entry.index == action.index and entry.cliloc == BANK_CLILOC
        for entry in popup.entries
    )


def _is_bank_transaction_action(action: Action) -> bool:
    return isinstance(action, (PopupRequest, PopupSelect, PickUp, Drop))


def _cross_profession(goal: Goal) -> Goal:
    """Retain the exact schema but make its profession fail admission."""

    params = dict(goal.params)
    if "profession" not in params:
        raise RuntimeError("capability goal schema has no profession trust boundary")
    params["profession"] = "miner"
    return Goal(kind=goal.kind, params=params)


def _run(args: argparse.Namespace) -> tuple[dict[str, bool], str]:
    account = args.account or f"animab3{args.suffix}"
    password = args.password or account
    smith_x, smith_y = TRADE_SMITH_SPOT
    banker_x, banker_y = BANKER_SPOT[-1]

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
            wipe_area(gm, smith_x, smith_y, radius=10, z=20)
            gx, gy, gz = gm.stage(
                serial,
                smith_x,
                smith_y,
                skills={"Blacksmith": 35},
                items=["SmithHammer 999"],
            )

            # A fresh ServUO character carries starting gold. Remove every
            # observed stack before granting the one exact proof object.
            staged_observations = [ipc.observe() for _ in range(3)]
            staged = staged_observations[-1]
            existing_gold = _pack_items(staged, frozenset({GOLD_GRAPHIC}))
            starting_gold_deleted = all(
                gm.command_on("[Delete", item.serial) for item in existing_gold
            )
            proof_gold_added = gm.command_on("[AddToPack Gold 100", serial)
            banker = gm.stage_npc(
                "Banker",
                banker_x,
                banker_y,
                gz,
                exclude=serial,
            )
            print(
                f"GM staged B3 smith; subject=0x{serial:X} pos=({gx},{gy},{gz}) "
                f"proof_gold=100 banker={getattr(banker, 'serial', None)}; closing GM"
            )

        # Trust boundary: warm packets only after the sole GM connection has
        # closed; Policy, goals, Agent, and every work action are constructed
        # below this point.
        gm_closed_before_agent = not gm.body.connected
        body = _RecordingBody(ipc)
        for _ in range(4):
            body.observe()
        assert body.last_obs is not None
        baseline = body.last_obs
        baseline_backpack = _backpack_serial(baseline)
        baseline_gold = _gold_item(baseline, container=baseline_backpack)
        baseline_tools = _pack_items(baseline, SMITH_TOOL_GRAPHICS)
        banker_serial = banker.serial if banker is not None else None

        valid_goal = capability_goal(_PROFESSION, _CAPABILITY)
        invalid_goal = _cross_profession(valid_goal)
        policy = CapabilityPolicy(_PROFESSION)
        capability_client: _SequencedCapabilityClient | None = None
        if args.autonomous:
            capability_client = _SequencedCapabilityClient()
            cognition = ThreadedCognition(
                CapabilityCognition(capability_client, _PROFESSION)
            )
        else:
            cognition = _InvalidThenValidCognition(invalid_goal, valid_goal)
        profession_planner = PROFESSIONS[_PROFESSION].planner(capability_goals=True)
        tracer = _SelectionTracer(body)
        profession_planner.selection_observer = tracer
        agent = Agent(
            body=body,
            persona=Persona(name="Tormund"),
            planner=profession_planner,
            cognition=cognition,
            cognition_interval=1,
            goal_policy=policy,
            profession=_PROFESSION,
        )
        agent.memory["banker_spot"] = BANKER_SPOT

        # Negative differential: the malformed cross-profession proposal gets
        # a complete live tick, but may not unlock any banking/crafting hand.
        negative_action_offset = len(body.actions)
        if args.autonomous:
            agent.tick()  # launch the strict-invalid model decision
            if not cognition.wait_idle(timeout=5.0):
                raise RuntimeError("autonomous invalid decision did not finish")
            agent.tick()  # deliver invalid; launch the valid decision
        else:
            agent.tick()
        negative_actions = body.actions[negative_action_offset:]
        assert body.last_obs is not None
        negative_pack_gold = _pack_gold(body.last_obs)
        invalid_rejected = bool(
            agent.goal_stack.current is None
            and (
                capability_client is not None and len(capability_client.calls) >= 1
                or int(agent.memory.get("cognition_admission_rejections", 0)) >= 1
            )
        )
        if args.autonomous and not cognition.wait_idle(timeout=5.0):
            raise RuntimeError("autonomous valid decision did not finish")

        target_goal_id: int | None = None
        target_action_offset: int | None = None
        completion_tick: int | None = None
        seen_bank_gold = 0
        for tick in range(args.ticks):
            agent.tick()
            assert body.last_obs is not None
            seen_bank_gold = max(seen_bank_gold, _bank_gold(body.last_obs))

            for selection in tracer.selections:
                if selection.goal == valid_goal and selection.goal_id is not None:
                    target_goal_id = selection.goal_id
                    target_action_offset = selection.action_offset
                    break

            if target_goal_id is not None:
                terminal = [
                    frame
                    for frame in agent.goal_stack.history
                    if frame.id == target_goal_id and frame.outcome is GoalOutcome.SUCCESS
                ]
                if terminal:
                    if completion_tick is None:
                        completion_tick = tick
                    if tick >= completion_tick + args.settle_ticks:
                        break

            if tick % args.snapshot_every == 0:
                frame = agent.goal_stack.current
                print(
                    f"tick={tick:03d} pack_gold={_pack_gold(body.last_obs)} "
                    f"bank_gold={_bank_gold(body.last_obs)} "
                    f"phase={agent.memory.get('mkt_phase', 'craft')!r} "
                    f"goal_id={frame.id if frame else None}"
                )

        assert body.last_obs is not None
        final_obs = body.last_obs
        final_bankbox = _bankbox_serial(final_obs)
        final_gold_item = _gold_item(final_obs, container=final_bankbox)
        capability_actions = body.actions[
            target_action_offset if target_action_offset is not None else len(body.actions):
        ]

        request_indices = [
            index
            for index, record in enumerate(capability_actions)
            if isinstance(record.action, PopupRequest)
            and record.action.serial == banker_serial
        ]
        select_indices = [
            index
            for index, record in enumerate(capability_actions)
            if banker_serial is not None and _bank_popup_select(record, banker_serial)
        ]
        pickup_indices = [
            index
            for index, record in enumerate(capability_actions)
            if isinstance(record.action, PickUp)
            and baseline_gold is not None
            and record.action.serial == baseline_gold.serial
            and record.action.amount == baseline_gold.amount
        ]
        drop_indices = [
            index
            for index, record in enumerate(capability_actions)
            if isinstance(record.action, Drop)
            and baseline_gold is not None
            and record.action.serial == baseline_gold.serial
            and final_bankbox is not None
            and record.action.container == final_bankbox
        ]
        exact_order = bool(
            request_indices
            and select_indices
            and pickup_indices
            and drop_indices
            and request_indices[0] < select_indices[0] < pickup_indices[0] < drop_indices[0]
        )

        target_selections = [
            selection for selection in tracer.selections if selection.goal == valid_goal
        ]
        selected_instances = {id(selection.skill) for selection in target_selections}
        allowed_instances = {id(skill) for skill in profession_planner.skills}
        target_frames = [
            frame
            for frame in (*agent.goal_stack.frames, *agent.goal_stack.history)
            if frame.goal == valid_goal
        ]
        terminal_matches = [
            frame
            for frame in agent.goal_stack.history
            if target_goal_id is not None and frame.id == target_goal_id
        ]
        forbidden_actions = [
            record.action
            for record in capability_actions
            if isinstance(record.action, (Use, GumpResponse, SellItems))
        ]
        negative_transaction_actions = [
            record.action
            for record in negative_actions
            if _is_bank_transaction_action(record.action)
            or isinstance(record.action, (Use, GumpResponse, SellItems))
        ]
        exact_transaction_counts = (
            len(request_indices) == 1
            and len(select_indices) == 1
            and len(pickup_indices) == 1
            and len(drop_indices) == 1
        )
        target_frame = target_frames[0] if len(target_frames) == 1 else None
        canonical_policy_frame = bool(
            target_frame is not None
            and target_frame.goal is not valid_goal
            and target_frame.goal.sealed
            and installed_binding_for_goal(target_frame.goal, _PROFESSION) is not None
            and target_frame.source is GoalSource.COGNITION
            and target_frame.deadline_tick is not None
            and target_frame.deadline_tick - target_frame.created_tick == 120
        )

        expected_transaction_indices = {
            *request_indices,
            *select_indices,
            *pickup_indices,
            *drop_indices,
        }
        all_transaction_indices = {
            index
            for index, record in enumerate(capability_actions)
            if _is_bank_transaction_action(record.action)
        }
        unexpected_capability_actions = [
            record.action
            for index, record in enumerate(capability_actions)
            if index not in expected_transaction_indices
        ]
        transaction_global_indices = {
            (target_action_offset or 0) + index
            for index in all_transaction_indices
        }
        transaction_owners: list[_SelectionRecord] = []
        for action_index in sorted(transaction_global_indices):
            owners = [
                selection
                for selection in tracer.selections
                if selection.action_offset == action_index
            ]
            if owners:
                transaction_owners.append(owners[-1])
        registry_owned_transaction = bool(
            len(transaction_owners) == len(transaction_global_indices)
            and transaction_owners
            and all_transaction_indices == expected_transaction_indices
            and all(
                owner.goal_id == target_goal_id
                and isinstance(owner.skill, CapabilityBoundSkill)
                and type(owner.skill.inner) is BankGold
                for owner in transaction_owners
            )
        )

        if not exact_order or final_gold_item is None or len(terminal_matches) != 1:
            print(
                "[TRACE] selections="
                + repr(
                    [
                        (record.skill.name, record.goal_id, record.goal == valid_goal)
                        for record in tracer.selections
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
            "gm_fixture_staged": bool(
                starting_gold_deleted and proof_gold_added and banker is not None
            ),
            "gm_connection_closed_before_agent": gm_closed_before_agent,
            "live_baseline_exact_gold_and_empty_bank": bool(
                baseline_backpack is not None
                and baseline_gold is not None
                and baseline_gold.amount == 100
                and _pack_gold(baseline) == 100
                and _bank_gold(baseline) == 0
                and baseline_tools
            ),
            "invalid_goal_rejected_without_transaction": bool(
                invalid_rejected
                and not negative_transaction_actions
                and negative_pack_gold == 100
            ),
            "closed_cognition_prompt_and_schema": bool(
                not args.autonomous
                or (
                    capability_client is not None
                    and len(capability_client.calls) == 3
                    and all("bank_gold" in user for _system, user in capability_client.calls[:2])
                    and all("BankGold" not in user for _system, user in capability_client.calls[:2])
                    and all("BlacksmithMarket" not in user for _system, user in capability_client.calls[:2])
                )
            ),
            "valid_goal_enqueued_once": bool(
                target_goal_id is not None
                and len(target_frames) == 1
                and canonical_policy_frame
            ),
            "profession_registry_instances_only": bool(
                target_selections
                and selected_instances
                and selected_instances <= allowed_instances
                and registry_owned_transaction
            ),
            "exact_popup_pickup_drop_order": exact_order,
            "bank_transaction_actions_once": exact_transaction_counts,
            "only_exact_capability_actions": bool(
                not forbidden_actions and not unexpected_capability_actions
            ),
            "same_gold_serial_moved_pack_to_bankbox": bool(
                baseline_gold is not None
                and final_gold_item is not None
                and final_gold_item.serial == baseline_gold.serial
                and final_bankbox is not None
                and final_gold_item.container == final_bankbox
            ),
            "live_gold_delta_confirmed": bool(
                _pack_gold(baseline) == 100
                and _pack_gold(final_obs) == 0
                and seen_bank_gold >= 100
                and _bank_gold(final_obs) >= 100
            ),
            "transaction_returned_idle": bool(
                agent.memory.get("mkt_phase", "craft") == "craft"
                and "bank_held" not in agent.memory
            ),
            "exact_goal_frame_succeeded_once": bool(
                len(terminal_matches) == 1
                and terminal_matches[0].outcome is GoalOutcome.SUCCESS
            ),
            "same_goal_not_reenqueued_during_settle": len(target_frames) == 1,
        }
        detail = (
            f"serial={serial} banker={banker_serial} gold={getattr(baseline_gold, 'serial', None)} "
            f"bankbox={final_bankbox} pack=100->{_pack_gold(final_obs)} "
            f"bank=0->{_bank_gold(final_obs)} goal_id={target_goal_id} "
            f"ticks={agent.ticks} cognition="
            f"{len(capability_client.calls) if capability_client is not None else cognition.calls}"
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
        "--autonomous",
        action="store_true",
        help="use production ThreadedCognition(CapabilityCognition) for the B4 gate",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        flags, detail = _run(args)
    except Exception as exc:
        label = "B4 CAPABILITY COGNITION GATE" if args.autonomous else "B3 BANK GOAL GATE"
        print(f"[FLAG] {label} FAILED: {type(exc).__name__}: {exc}")
        return 1
    label = "B4 CAPABILITY COGNITION GATE" if args.autonomous else "B3 BANK GOAL GATE"
    return 0 if print_gate_verdict(
        flags,
        label=label,
        detail=detail,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
