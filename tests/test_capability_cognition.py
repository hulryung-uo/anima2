"""Closed-vocabulary cognition over observation-ready capability ids."""

from __future__ import annotations

import pytest

from anima2.agent import Agent
from anima2.capabilities import (
    CAPABILITIES,
    CapabilityPolicy,
    capability_goal,
    ready_capability_ids,
)
from anima2.capability_cognition import CapabilityCognition
from anima2.contract import ItemView, Observation, PlayerView, Position
from anima2.llm import StubLLMClient
from anima2.goals import GoalSource
from anima2.persona import Persona
from anima2.profession import PROFESSIONS
from anima2.skills.base import Goal, SkillContext
from anima2.skills.craft import DAGGER_GRAPHIC
from anima2.skills.smelt import INGOT_GRAPHICS


def _ctx(
    goal: Goal | None = None,
    *,
    pack_gold: int = 100,
    bank_gold: int = 0,
    banker_spot: object = (100, 100),
    daggers: int = 0,
    vendor_spot: object = (100, 100),
    ingots: int = 0,
    smith_tool: bool = False,
    craft_spot: object | None = None,
) -> SkillContext:
    player = PlayerView(
        serial=1,
        pos=Position(100, 100, 0),
        hits=100,
        hits_max=100,
    )
    backpack = ItemView(
        serial=2,
        graphic=0x0E75,
        amount=1,
        pos=player.pos,
        container=player.serial,
        layer=0x15,
        distance=0,
    )
    bank_box = ItemView(
        serial=3,
        graphic=0x0E7C,
        amount=1,
        pos=player.pos,
        container=player.serial,
        layer=0x1D,
        distance=0,
    )
    items = [backpack, bank_box]
    if pack_gold:
        items.append(
            ItemView(
                serial=4,
                graphic=0x0EED,
                amount=pack_gold,
                pos=player.pos,
                container=backpack.serial,
                layer=0,
                distance=0,
            )
        )
    if bank_gold:
        items.append(
            ItemView(
                serial=5,
                graphic=0x0EED,
                amount=bank_gold,
                pos=player.pos,
                container=bank_box.serial,
                layer=0,
                distance=0,
            )
        )
    if daggers:
        items.append(
            ItemView(
                serial=6,
                graphic=DAGGER_GRAPHIC,
                amount=daggers,
                pos=player.pos,
                container=backpack.serial,
                layer=0,
                distance=0,
            )
        )
    if ingots:
        items.append(
            ItemView(
                serial=7,
                graphic=next(iter(INGOT_GRAPHICS)),
                amount=ingots,
                pos=player.pos,
                container=backpack.serial,
                layer=0,
                distance=0,
            )
        )
    if smith_tool:
        items.append(
            ItemView(
                serial=8,
                graphic=0x13E3,
                amount=1,
                pos=player.pos,
                container=backpack.serial,
                layer=0,
                distance=0,
            )
        )
    memory = {}
    if banker_spot is not None:
        memory["banker_spot"] = banker_spot
    if vendor_spot is not None:
        memory["vendor_spot"] = vendor_spot
    if craft_spot is not None:
        memory["craft_spot"] = craft_spot
    return SkillContext(
        obs=Observation(player=player, items=items),
        persona=Persona(name="Tormund", title="a blacksmith"),
        goal=goal,
        memory=memory,
    )


_CAPABILITY_REPLY = (
    '{"schema":1,"decision":"capability","capability":"bank_gold"}'
)
_IDLE_REPLY = '{"schema":1,"decision":"idle"}'


def test_ready_capability_ids_returns_only_observation_ready_profession_ids() -> None:
    # Default `_ctx()` has 100 pack gold and 0 ingots at the vendor route, so
    # both banking the gold and buying a replenishment batch of iron are ready.
    assert ready_capability_ids("blacksmith", _ctx()) == ("bank_gold", "buy_ingots")
    assert ready_capability_ids("miner", _ctx()) == ()
    # 1 gold banks but can't afford the fixed buy batch (BUY_AMOUNT * 5 == 75).
    assert ready_capability_ids("blacksmith", _ctx(pack_gold=1)) == ("bank_gold",)
    assert ready_capability_ids(
        "blacksmith", _ctx(pack_gold=40, bank_gold=100)
    ) == ("bank_gold",)
    assert ready_capability_ids(
        "blacksmith", _ctx(pack_gold=0, bank_gold=100)
    ) == ()
    # No banker route means no bank, but the vendor route + 100 gold + 0 ingots
    # still make a buy ready — replenishment doesn't depend on the banker.
    assert ready_capability_ids("blacksmith", _ctx(banker_spot=None)) == ("buy_ingots",)
    assert ready_capability_ids("blacksmith", _ctx(), GoalSource.SKILL) == ()


def test_ready_capability_ids_exposes_real_registry_ordered_choice() -> None:
    # Daggers to sell, gold to bank, and 0 ingots to replenish — all three
    # vendor/banker operations are ready, in registry order.
    assert ready_capability_ids("blacksmith", _ctx(daggers=5)) == (
        "sell_daggers",
        "bank_gold",
        "buy_ingots",
    )
    # With no gold, only the sale is ready (nothing to bank, can't afford a buy).
    assert ready_capability_ids(
        "blacksmith", _ctx(pack_gold=0, daggers=5)
    ) == ("sell_daggers",)
    # Without a vendor route neither the sale nor the buy is ready.
    assert ready_capability_ids(
        "blacksmith", _ctx(daggers=5, vendor_spot=None)
    ) == ("bank_gold",)


def test_ready_capability_ids_exposes_craft_only_with_owned_exact_prerequisites() -> None:
    ready = _ctx(
        pack_gold=0,
        ingots=15,
        smith_tool=True,
        craft_spot=(100, 100),
    )
    assert ready_capability_ids("blacksmith", ready) == ("craft_daggers",)
    assert ready_capability_ids(
        "blacksmith",
        _ctx(pack_gold=0, ingots=14, smith_tool=True, craft_spot=(100, 100)),
    ) == ()
    assert ready_capability_ids(
        "blacksmith", _ctx(pack_gold=0, ingots=15, craft_spot=(100, 100))
    ) == ()
    assert ready_capability_ids(
        "blacksmith", _ctx(pack_gold=0, ingots=15, smith_tool=True)
    ) == ()


def test_registry_order_prefers_sale_then_bank_then_craft() -> None:
    ctx = _ctx(
        daggers=5,
        ingots=15,
        smith_tool=True,
        craft_spot=(100, 100),
    )
    assert ready_capability_ids("blacksmith", ctx) == (
        "sell_daggers",
        "bank_gold",
    )

    ctx = _ctx(
        daggers=4,
        ingots=3,
        smith_tool=True,
        craft_spot=(100, 100),
    )
    # 3 ingots is below the reorder point, so a buy is ready too — and it sorts
    # last, after the bank and craft ids, per registry order.
    assert ready_capability_ids("blacksmith", ctx) == (
        "bank_gold",
        "craft_daggers",
        "buy_ingots",
    )


def test_exact_capability_decision_returns_exact_unsealed_wire_goal() -> None:
    client = StubLLMClient(_CAPABILITY_REPLY)

    goal = CapabilityCognition(client, "blacksmith").reconsider(_ctx())

    assert goal == capability_goal("blacksmith", "bank_gold")
    assert goal is not None
    assert goal.sealed is False
    assert type(goal.params) is dict
    assert goal.params == {
        "schema": 1,
        "profession": "blacksmith",
        "capability": "bank_gold",
    }
    assert len(client.calls) == 1


def test_exact_second_ready_capability_can_be_selected() -> None:
    client = StubLLMClient(
        '{"schema":1,"decision":"capability","capability":"bank_gold"}'
    )

    goal = CapabilityCognition(client, "blacksmith").reconsider(_ctx(daggers=5))

    assert goal == capability_goal("blacksmith", "bank_gold")
    assert len(client.calls) == 1


def test_exact_craft_capability_can_be_selected_when_ready() -> None:
    client = StubLLMClient(
        '{"schema":1,"decision":"capability","capability":"craft_daggers"}'
    )
    ctx = _ctx(
        pack_gold=0,
        ingots=15,
        smith_tool=True,
        craft_spot=(100, 100),
    )

    goal = CapabilityCognition(client, "blacksmith").reconsider(ctx)

    assert goal == capability_goal("blacksmith", "craft_daggers")
    assert len(client.calls) == 1


def test_installed_but_not_ready_capability_is_rejected() -> None:
    client = StubLLMClient(
        '{"schema":1,"decision":"capability","capability":"sell_daggers"}'
    )

    assert CapabilityCognition(client, "blacksmith").reconsider(_ctx()) is None
    assert len(client.calls) == 1


def test_exact_idle_decision_returns_no_goal() -> None:
    client = StubLLMClient(_IDLE_REPLY)

    assert CapabilityCognition(client, "blacksmith").reconsider(_ctx()) is None
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        # Duplicate keys are ambiguous even when both values happen to agree.
        '{"schema":1,"schema":1,"decision":"capability","capability":"bank_gold"}',
        # No extra/free-form fields are part of either exact wire shape.
        '{"schema":1,"decision":"capability","capability":"bank_gold","args":{}}',
        '{"schema":1,"decision":"capability","capability":"bank_gold","source":"system"}',
        '{"schema":1,"decision":"capability","capability":"bank_gold","deadline":999}',
        '{"schema":1,"decision":"capability","capability":"bank_gold","x":100,"y":100}',
        '{"schema":1,"decision":"capability","capability":"bank_gold","goal":"goto"}',
        '{"schema":1,"decision":"idle","capability":"bank_gold"}',
        # Old/future schema revisions fail closed rather than being guessed at.
        '{"schema":0,"decision":"capability","capability":"bank_gold"}',
        '{"schema":2,"decision":"capability","capability":"bank_gold"}',
        # JSON equality must not blur schema types (True == 1 in Python).
        '{"schema":true,"decision":"capability","capability":"bank_gold"}',
        '{"schema":1.0,"decision":"capability","capability":"bank_gold"}',
        # Decision and capability ids have exact string types and vocabulary.
        '{"schema":1,"decision":true,"capability":"bank_gold"}',
        '{"schema":1,"decision":"capability","capability":1}',
        '{"schema":1,"decision":"capability","capability":true}',
        '{"schema":1,"decision":"capability","capability":null}',
        '{"schema":1,"decision":"capability","capability":{}}',
        '{"schema":1,"decision":"capability","capability":"unknown"}',
        '{"schema":1,"decision":"capability","capability":"Bank_Gold"}',
        '{"schema":1,"decision":"capability","capability":" bank_gold"}',
        '{"schema":1,"decision":"work","capability":"bank_gold"}',
        # Missing fields are not inferred.
        '{"schema":1,"decision":"capability"}',
        '{"decision":"capability","capability":"bank_gold"}',
        # A response is exactly one JSON object, never a prefix extraction.
        (
            '{"schema":1,"decision":"idle"} '
            '{"schema":1,"decision":"capability","capability":"bank_gold"}'
        ),
        "not json",
        "[]",
        '```json\n{"schema":1,"decision":"idle"}\n```',
        'reply: {"schema":1,"decision":"idle"}',
        '{"schema":NaN,"decision":"idle"}',
        '{"schema":Infinity,"decision":"idle"}',
    ],
)
def test_malformed_or_unavailable_decision_is_rejected(response: str) -> None:
    client = StubLLMClient(response)

    assert CapabilityCognition(client, "blacksmith").reconsider(_ctx()) is None
    assert len(client.calls) == 1


def test_oversize_capability_id_is_rejected() -> None:
    client = StubLLMClient(
        '{"schema":1,"decision":"capability","capability":"'
        + "x" * 81
        + '"}'
    )

    assert CapabilityCognition(client, "blacksmith").reconsider(_ctx()) is None
    assert len(client.calls) == 1


def test_oversize_response_is_rejected_even_if_its_json_suffix_is_valid() -> None:
    client = StubLLMClient(" " * 4097 + _IDLE_REPLY)

    assert CapabilityCognition(client, "blacksmith").reconsider(_ctx()) is None
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "ctx",
    [
        _ctx(pack_gold=0),
        _ctx(pack_gold=0, bank_gold=100),
        # No routes at all: no banker (no bank) and no vendor (no sell/buy),
        # even though the pack still holds affordable gold and low ingots.
        _ctx(banker_spot=None, vendor_spot=None),
    ],
    ids=["no-pack-gold", "existing-bank-only", "no-routes"],
)
def test_no_ready_capability_skips_the_client(ctx: SkillContext) -> None:
    client = StubLLMClient(_CAPABILITY_REPLY)

    assert CapabilityCognition(client, "blacksmith").reconsider(ctx) is None
    assert client.calls == []


def test_ready_predicate_exception_fails_closed() -> None:
    binding = CAPABILITIES[("blacksmith", "bank_gold")]
    original = binding.ready

    def _broken(skill_ctx: SkillContext) -> bool:
        raise RuntimeError("broken readiness")

    object.__setattr__(binding, "ready", _broken)
    try:
        # The broken capability is excluded (fails closed); the sibling
        # capabilities that are genuinely ready in this context are unaffected.
        assert "bank_gold" not in ready_capability_ids("blacksmith", _ctx())
    finally:
        object.__setattr__(binding, "ready", original)


def test_active_goal_skips_the_client_and_preserves_the_goal() -> None:
    active = Goal("goto", {"target": Position(101, 100, 0)})
    client = StubLLMClient(_CAPABILITY_REPLY)

    result = CapabilityCognition(client, "blacksmith").reconsider(_ctx(active))

    assert result is active
    assert client.calls == []


def test_no_client_uses_the_deterministic_ready_fallback() -> None:
    goal = CapabilityCognition(None, "blacksmith").reconsider(_ctx())

    assert goal == capability_goal("blacksmith", "bank_gold")
    assert goal is not None and not goal.sealed


def test_no_client_prefers_sale_when_both_operations_are_ready() -> None:
    goal = CapabilityCognition(None, "blacksmith").reconsider(_ctx(daggers=5))

    assert goal == capability_goal("blacksmith", "sell_daggers")


def test_transport_exception_uses_the_deterministic_ready_fallback() -> None:
    class _FailingClient:
        calls = 0

        def complete(self, system: str, user: str) -> str:
            self.calls += 1
            raise OSError("transport unavailable")

    client = _FailingClient()

    goal = CapabilityCognition(client, "blacksmith").reconsider(_ctx())

    assert goal == capability_goal("blacksmith", "bank_gold")
    assert goal is not None and not goal.sealed
    assert client.calls == 1


def test_prompt_exposes_only_ready_opaque_ids_not_implementation_names() -> None:
    client = StubLLMClient(_IDLE_REPLY)

    CapabilityCognition(client, "blacksmith").reconsider(_ctx())

    assert len(client.calls) == 1
    system, user = client.calls[0]
    prompt = system + "\n" + user
    assert "bank_gold" in prompt
    assert "sell_daggers" not in prompt
    assert "BankGold" not in prompt
    assert "BlacksmithMarket" not in prompt
    assert "anima2." not in prompt


def test_prompt_exposes_both_ready_opaque_ids_only() -> None:
    client = StubLLMClient(_IDLE_REPLY)

    CapabilityCognition(client, "blacksmith").reconsider(_ctx(daggers=5))

    system, user = client.calls[0]
    prompt = system + "\n" + user
    assert "sell_daggers" in prompt
    assert "bank_gold" in prompt
    assert "SellDaggers" not in prompt
    assert "BankGold" not in prompt


class _StaticBody:
    connected = True

    def __init__(self, observation: Observation) -> None:
        self.observation = observation
        self.actions: list[object] = []

    def observe(self) -> Observation:
        return self.observation

    def act(self, action: object) -> None:
        self.actions.append(action)


def _agent(client: StubLLMClient, ctx: SkillContext) -> tuple[Agent, _StaticBody]:
    body = _StaticBody(ctx.obs)
    agent = Agent(
        body=body,  # type: ignore[arg-type]
        persona=ctx.persona,
        planner=PROFESSIONS["blacksmith"].planner(capability_goals=True),
        cognition=CapabilityCognition(client, "blacksmith"),
        cognition_interval=1,
        profession="blacksmith",
        goal_policy=CapabilityPolicy("blacksmith"),
    )
    agent.memory["banker_spot"] = (100, 100)
    return agent, body


def test_agent_installs_a_separate_sealed_cognition_frame_and_deadline() -> None:
    agent, _body = _agent(StubLLMClient(_CAPABILITY_REPLY), _ctx())

    agent.tick()

    frame = agent.goal_stack.current
    assert frame is not None
    assert frame.goal.sealed
    assert frame.goal == capability_goal("blacksmith", "bank_gold")
    assert frame.deadline_tick == 120
    assert frame.created_tick == 0
    assert frame.source is GoalSource.COGNITION


def test_agent_rejects_a_malformed_model_decision_without_frame_or_action() -> None:
    client = StubLLMClient(
        '{"schema":1,"decision":"capability","capability":"bank_gold",'
        '"action":"drop"}'
    )
    agent, body = _agent(client, _ctx())

    action = agent.tick()

    assert action is None
    assert agent.goal is None
    assert body.actions == []


def test_authoritative_admission_rechecks_stale_snapshot_readiness() -> None:
    proposal = CapabilityCognition(None, "blacksmith").reconsider(_ctx())
    assert proposal is not None

    class _FixedCognition:
        def reconsider(self, ctx: SkillContext) -> Goal:
            return proposal

    live = _ctx(pack_gold=0)
    body = _StaticBody(live.obs)
    agent = Agent(
        body=body,  # type: ignore[arg-type]
        persona=live.persona,
        planner=PROFESSIONS["blacksmith"].planner(capability_goals=True),
        cognition=_FixedCognition(),
        cognition_interval=1,
        profession="blacksmith",
        goal_policy=CapabilityPolicy("blacksmith"),
    )
    agent.memory["banker_spot"] = (100, 100)

    action = agent.tick()

    assert action is None
    assert agent.goal is None
    assert body.actions == []
    assert agent.memory["cognition_admission_rejections"] == 1
