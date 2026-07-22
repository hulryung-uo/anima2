"""Live B7 gate: a fresh bank goal deposits gold over an existing balance.

The fixture grants one exact 200-gold stack, closes the GM connection, and
uses raw IPC to seed 100 gold into the subject's own bank box.  Only after the
closed seed leg has established ``pack=100`` and ``bank=100`` does this module
construct production ``CapabilityCognition``, ``CapabilityPolicy``, and
``Agent`` instances.

The measured leg must then produce exactly one ordered transaction::

    PopupRequest(banker) -> PopupSelect(Bank) -> PickUp(new_gold) ->
    Drop(new_gold, bankbox)

It passes only when the same goal's provenance memory confirms the exact
100-gold pack and bank deltas, the operation returns to its stand, and that
one goal succeeds once.  An absolute pre-existing bank threshold therefore
cannot satisfy this gate.

Usage::

    python -m anima2.live_repeat_bank_goal --suffix b7manual
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable

from .agent import Agent
from .capability_cognition import CapabilityCognition
from .capabilities import CapabilityPolicy, capability_goal, installed_binding_for_goal
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
from .profession import BANKER_SPOT, PROFESSIONS, TRADE_SMITH_SPOT, CapabilityBoundSkill
from .skills.base import Goal, Skill, SkillContext
from .skills.craft import SMITH_TOOL_GRAPHICS
from .skills.harvest import BACKPACK_LAYER
from .skills.market import BANKBOX_LAYER, BANK_CLILOC, GOLD_GRAPHIC, BankGold

_PROFESSION = "blacksmith"
_CAPABILITY = "bank_gold"
_SEED_GOLD = 100
_MEASURED_GOLD = 100


@dataclass(frozen=True)
class _ActionRecord:
    action: Action
    observation: Observation


class _RecordingBody:
    """Pair each action with the observation from which it was selected."""

    def __init__(self, inner: ResilientIpcBody) -> None:
        self.inner = inner
        self.last_obs: Observation | None = None
        self.actions: list[_ActionRecord] = []

    @property
    def connected(self) -> bool:
        return self.inner.connected

    def observe(self) -> Observation:
        self.last_obs = self.inner.observe()
        return self.last_obs

    def act(self, action: Action) -> None:
        assert self.last_obs is not None
        self.actions.append(_ActionRecord(action, self.last_obs))
        self.inner.act(action)


@dataclass(frozen=True)
class _SelectionRecord:
    skill: Skill
    goal: Goal | None
    goal_id: int | None
    action_offset: int


class _SelectionTracer:
    def __init__(self, body: _RecordingBody) -> None:
        self.body = body
        self.selections: list[_SelectionRecord] = []

    def __call__(self, skill: Skill, ctx: SkillContext) -> None:
        self.selections.append(
            _SelectionRecord(skill, ctx.goal, ctx.goal_id, len(self.body.actions))
        )


class _RepeatBankClient:
    """Propose the one production capability goal, then remain idle."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if len(self.calls) == 1:
            return '{"schema":1,"decision":"capability","capability":"bank_gold"}'
        return '{"schema":1,"decision":"idle"}'


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
    bankbox = next(
        (
            item
            for item in obs.items
            if item.layer == BANKBOX_LAYER and item.container == obs.player.serial
        ),
        None,
    )
    return bankbox.serial if bankbox is not None else None


def _container_gold(obs: Observation, container: int | None) -> int:
    if container is None:
        return 0
    return sum(
        item.amount
        for item in obs.items
        if item.graphic == GOLD_GRAPHIC and item.container == container
    )


def _pack_gold(obs: Observation) -> int:
    return _container_gold(obs, _backpack_serial(obs))


def _bank_gold(obs: Observation) -> int:
    return _container_gold(obs, _bankbox_serial(obs))


def _pack_items(obs: Observation, graphics: frozenset[int]):
    backpack = _backpack_serial(obs)
    if backpack is None:
        return []
    return [
        item
        for item in obs.items
        if item.graphic in graphics and item.container == backpack
    ]


def _gold_pile(obs: Observation, container: int | None):
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


def _observe_until(
    ipc: ResilientIpcBody,
    predicate: Callable[[Observation], bool],
    *,
    attempts: int,
    label: str,
) -> Observation:
    last: Observation | None = None
    for _ in range(attempts):
        last = ipc.observe()
        if predicate(last):
            return last
    raise RuntimeError(f"fixture seed timed out waiting for {label}: {last!r}")


def _seed_existing_balance(
    ipc: ResilientIpcBody,
    banker_serial: int,
) -> tuple[Observation, int, int]:
    """Move half of the fixture stack into the bank without Agent authority."""

    initial = _observe_until(
        ipc,
        lambda obs: _pack_gold(obs) == _SEED_GOLD + _MEASURED_GOLD,
        attempts=8,
        label="the exact 200-gold fixture stack",
    )
    source = _gold_pile(initial, _backpack_serial(initial))
    if source is None or source.amount != _SEED_GOLD + _MEASURED_GOLD:
        raise RuntimeError("the fixture gold did not settle as one exact stack")

    ipc.act(PopupRequest(serial=banker_serial))
    popup_obs = _observe_until(
        ipc,
        lambda obs: bool(obs.popup is not None and obs.popup.serial == banker_serial),
        attempts=10,
        label="the banker popup",
    )
    assert popup_obs.popup is not None
    bank_entry = next(
        (entry for entry in popup_obs.popup.entries if entry.cliloc == BANK_CLILOC),
        None,
    )
    if bank_entry is None:
        raise RuntimeError("the staged banker popup has no Bank entry")
    ipc.act(PopupSelect(serial=banker_serial, index=bank_entry.index))

    opened = _observe_until(
        ipc,
        lambda obs: _bankbox_serial(obs) is not None,
        attempts=12,
        label="the subject bank box",
    )
    bankbox = _bankbox_serial(opened)
    if bankbox is None:
        raise RuntimeError("bank box disappeared during fixture seed")
    # Match the production FSM's settle barrier before attempting the drop.
    for _ in range(3):
        ipc.observe()

    ipc.act(PickUp(serial=source.serial, amount=_SEED_GOLD))
    ipc.observe()
    ipc.observe()
    ipc.act(Drop(serial=source.serial, container=bankbox))
    seeded = _observe_until(
        ipc,
        lambda obs: (
            _pack_gold(obs) == _MEASURED_GOLD
            and _bank_gold(obs) == _SEED_GOLD
            and obs.pending_target is None
            and obs.popup is None
            and not obs.gumps
        ),
        attempts=16,
        label="pack=100 and bank=100",
    )
    return seeded, source.serial, bankbox


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


def _is_transaction(action: Action) -> bool:
    return isinstance(action, (PopupRequest, PopupSelect, PickUp, Drop))


def _evidence_has(entries: object, serial: int, amount: int) -> bool:
    """Accept tuple/list evidence entries whose first fields are serial/amount."""

    if not isinstance(entries, (tuple, list)):
        return False
    if (
        len(entries) >= 2
        and type(entries[0]) is int
        and type(entries[1]) is int
    ):
        return entries[0] == serial and entries[1] == amount
    return any(
        isinstance(entry, (tuple, list))
        and len(entry) >= 2
        and entry[0] == serial
        and entry[1] == amount
        for entry in entries
    )


def _run(args: argparse.Namespace) -> tuple[dict[str, bool], str]:
    account = args.account or f"animab7{args.suffix}"
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
            staged = [ipc.observe() for _ in range(3)][-1]
            starting_gold = _pack_items(staged, frozenset({GOLD_GRAPHIC}))
            starting_gold_deleted = all(
                gm.command_on("[Delete", item.serial) for item in starting_gold
            )
            fixture_gold_added = gm.command_on("[AddToPack Gold 200", serial)
            banker = gm.stage_npc(
                "Banker",
                banker_x,
                banker_y,
                gz,
                exclude=serial,
            )
            print(
                f"GM staged B7 smith; subject=0x{serial:X} pos=({gx},{gy},{gz}) "
                f"fixture_gold=200 banker={getattr(banker, 'serial', None)}; "
                "closing GM"
            )

        gm_closed_before_seed = not gm.body.connected
        if banker is None:
            raise RuntimeError("failed to stage a banker")
        seeded_obs, seed_source_serial, seed_bankbox = _seed_existing_balance(
            ipc, banker.serial
        )

        # Production authority begins only after the closed fixture seed leg.
        body = _RecordingBody(ipc)
        for _ in range(4):
            body.observe()
        assert body.last_obs is not None
        baseline = body.last_obs
        baseline_backpack = _backpack_serial(baseline)
        measured_pile = _gold_pile(baseline, baseline_backpack)
        baseline_bankbox = _bankbox_serial(baseline)
        baseline_tools = _pack_items(baseline, SMITH_TOOL_GRAPHICS)

        valid_goal = capability_goal(_PROFESSION, _CAPABILITY)
        policy = CapabilityPolicy(_PROFESSION)
        client = _RepeatBankClient()
        cognition = ThreadedCognition(CapabilityCognition(client, _PROFESSION))
        planner = PROFESSIONS[_PROFESSION].planner(capability_goals=True)
        tracer = _SelectionTracer(body)
        planner.selection_observer = tracer
        agent = Agent(
            body=body,
            persona=Persona(name="Tormund"),
            planner=planner,
            cognition=cognition,
            cognition_interval=1,
            goal_policy=policy,
            profession=_PROFESSION,
        )
        agent.memory["banker_spot"] = BANKER_SPOT

        # Launch and resolve the first real CapabilityCognition proposal.
        agent.tick()
        if not cognition.wait_idle(timeout=5.0):
            raise RuntimeError("repeat-bank capability decision did not finish")

        target_goal_id: int | None = None
        target_action_offset: int | None = None
        completion_tick: int | None = None
        seen_bank_gold = _bank_gold(baseline)
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
                    if frame.id == target_goal_id
                    and frame.outcome is GoalOutcome.SUCCESS
                ]
                if terminal:
                    completion_tick = completion_tick if completion_tick is not None else tick
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
        start = target_action_offset if target_action_offset is not None else len(body.actions)
        measured_actions = body.actions[start:]

        request_indices = [
            index
            for index, record in enumerate(measured_actions)
            if isinstance(record.action, PopupRequest)
            and record.action.serial == banker.serial
        ]
        select_indices = [
            index
            for index, record in enumerate(measured_actions)
            if _bank_popup_select(record, banker.serial)
        ]
        pickup_indices = [
            index
            for index, record in enumerate(measured_actions)
            if isinstance(record.action, PickUp)
            and measured_pile is not None
            and record.action.serial == measured_pile.serial
            and record.action.amount == measured_pile.amount == _MEASURED_GOLD
        ]
        drop_indices = [
            index
            for index, record in enumerate(measured_actions)
            if isinstance(record.action, Drop)
            and measured_pile is not None
            and record.action.serial == measured_pile.serial
            and baseline_bankbox is not None
            and record.action.container == baseline_bankbox
        ]
        exact_order = bool(
            request_indices
            and select_indices
            and pickup_indices
            and drop_indices
            and request_indices[0]
            < select_indices[0]
            < pickup_indices[0]
            < drop_indices[0]
        )
        exact_transaction_indices = {
            *request_indices,
            *select_indices,
            *pickup_indices,
            *drop_indices,
        }
        all_transaction_indices = {
            index
            for index, record in enumerate(measured_actions)
            if _is_transaction(record.action)
        }

        transaction_global_indices = {
            start + index for index in all_transaction_indices
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
        registry_owned = bool(
            transaction_owners
            and len(transaction_owners) == len(transaction_global_indices)
            and all_transaction_indices == exact_transaction_indices
            and all(
                owner.goal_id == target_goal_id
                and isinstance(owner.skill, CapabilityBoundSkill)
                and type(owner.skill.inner) is BankGold
                for owner in transaction_owners
            )
        )

        memory = agent.memory
        exact_goal_proof = bool(
            target_goal_id is not None
            and memory.get("cap_bank_goal_id") == target_goal_id
            and memory.get("cap_bank_baseline_goal_id") == target_goal_id
            and memory.get("cap_bank_start_bank_gold") == _SEED_GOLD
            and memory.get("cap_bank_expected_gold") == _MEASURED_GOLD
            and measured_pile is not None
            and _evidence_has(
                memory.get("cap_bank_start_piles"),
                measured_pile.serial,
                _MEASURED_GOLD,
            )
            and _evidence_has(
                memory.get("cap_bank_lifted_items"),
                measured_pile.serial,
                _MEASURED_GOLD,
            )
            and _evidence_has(
                memory.get("cap_bank_dropped_items"),
                measured_pile.serial,
                _MEASURED_GOLD,
            )
            and memory.get("cap_bank_pack_delta") == _MEASURED_GOLD
            and memory.get("cap_bank_bank_delta") == _MEASURED_GOLD
            and memory.get("cap_bank_confirmed") == _MEASURED_GOLD
            and memory.get("cap_bank_finished_goal_id") == target_goal_id
            and memory.get("cap_bank_returned_goal_id") == target_goal_id
        )
        forbidden = [
            record.action
            for record in measured_actions
            if isinstance(record.action, (Use, GumpResponse, SellItems))
        ]

        flags = {
            "schema_ready": ipc.ready.get("schema_version") == SUPPORTED_SCHEMA_VERSION,
            "gm_fixture_staged": bool(
                starting_gold_deleted and fixture_gold_added and banker is not None
            ),
            "gm_closed_before_seed_and_agent": gm_closed_before_seed,
            "closed_seed_leg_established_existing_balance": bool(
                seed_source_serial > 0
                and seed_bankbox > 0
                and _pack_gold(seeded_obs) == _MEASURED_GOLD
                and _bank_gold(seeded_obs) == _SEED_GOLD
            ),
            "measured_baseline_exact": bool(
                baseline_backpack is not None
                and baseline_bankbox == seed_bankbox
                and measured_pile is not None
                and measured_pile.amount == _MEASURED_GOLD
                and _pack_gold(baseline) == _MEASURED_GOLD
                and _bank_gold(baseline) == _SEED_GOLD
                and baseline_tools
            ),
            "production_capability_cognition_used": bool(
                client.calls
                and "bank_gold" in client.calls[0][1]
                and "BankGold" not in client.calls[0][1]
            ),
            "valid_goal_enqueued_once": bool(
                target_goal_id is not None
                and len(target_frames) == 1
                and canonical_policy_frame
            ),
            "registry_owned_bank_transaction": registry_owned,
            "exact_popup_pickup_drop_order": exact_order,
            "bank_transaction_actions_once": bool(
                len(request_indices) == 1
                and len(select_indices) == 1
                and len(pickup_indices) == 1
                and len(drop_indices) == 1
            ),
            "no_sibling_capability_actions": not forbidden,
            "exact_goal_scoped_proof": exact_goal_proof,
            "live_second_deposit_delta_confirmed": bool(
                _pack_gold(baseline) == _MEASURED_GOLD
                and _pack_gold(final_obs) == 0
                and _bank_gold(baseline) == _SEED_GOLD
                and seen_bank_gold >= _SEED_GOLD + _MEASURED_GOLD
                and _bank_gold(final_obs) == _SEED_GOLD + _MEASURED_GOLD
                and final_bankbox == baseline_bankbox
            ),
            "transaction_returned_idle": bool(
                memory.get("mkt_phase", "craft") == "craft"
                and "bank_held" not in memory
                and "cap_bank_release_pending" not in memory
            ),
            "exact_goal_frame_succeeded_once": bool(
                len(terminal_matches) == 1
                and terminal_matches[0].outcome is GoalOutcome.SUCCESS
            ),
            "same_goal_not_reenqueued_during_settle": len(target_frames) == 1,
        }
        detail = (
            f"serial={serial} banker={banker.serial} "
            f"measured_gold={getattr(measured_pile, 'serial', None)} "
            f"bankbox={final_bankbox} "
            f"pack={_pack_gold(baseline)}->{_pack_gold(final_obs)} "
            f"bank={_bank_gold(baseline)}->{_bank_gold(final_obs)} "
            f"goal_id={target_goal_id} ticks={agent.ticks} cognition={len(client.calls)}"
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
    parser.add_argument("--ticks", type=int, default=120)
    parser.add_argument("--settle-ticks", type=int, default=5)
    parser.add_argument("--snapshot-every", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        flags, detail = _run(args)
    except Exception as exc:
        print(f"[FLAG] B7 REPEAT BANK GOAL GATE FAILED: {type(exc).__name__}: {exc}")
        return 1
    return 0 if print_gate_verdict(
        flags,
        label="B7 REPEAT BANK GOAL GATE",
        detail=detail,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
