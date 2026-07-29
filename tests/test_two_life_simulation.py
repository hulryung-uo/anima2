"""The closed-loop two-Life simulation: a handover, offline, end to end.

Audit proposal 7. The rule<->gate concordance suite checks single-agent, single-tick
agreement; the runtime detector catches live drifts. What NEITHER can see is the
cross-agent, cross-time class — a leash bound in one memory of two, a worker that
stopped while its partner kept delivering — because those failures live in the space
BETWEEN agents over MANY ticks. This simulation runs two real Lives (the full
orchestrator: capability cognition, goal admission, leases, the FSMs) over one shared
MockBody world and asserts the handover both live supply chains stand on: boards leave
the woodsman's pack, cross the ground, and arrive in the carpenter's.

Scope, stated honestly: the mock has item physics (PickUp/Drop with conservation) and
walking, NOT gumps or vendors — so crafting and selling stay live-only, and the sim
stops at the completed handover. Terrain stays live-probe territory. Per the
offline-mock-fidelity rule (the anima2 memory that a mock re-encoding a production
constant can pass green while live stalls), the world here is pure geometry: every
threshold the agents act on comes from the REAL capability configs, none is re-encoded
in the mock.

Four oracles ride every tick, not just the end state:
  1. the handover completes (the point);
  2. `rule_gate_disagreement` stays None on BOTH Lives — the generic stall oracle,
     pinning the failure SHAPE rather than any one instance;
  3. both agents stay inside their leashes — the carpenter once drifted out of pickup
     range of its own supply because the leash bound only one of its two memories;
  4. boards are CONSERVED across packs, ground and lift cursors — a mock that leaks
     items would "pass" a handover that never happened.
"""

from anima2.carpenter_life import CarpenterLife
from anima2.contract import ItemView, PlayerView, Position
from anima2.mock_body import MockBody
from anima2.persona import Persona
from anima2.skills.carpentry import BuySaw
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.woodwork import BOARD_GRAPHIC, DeliverBoards
from anima2.woodsman_life import WoodsmanLife

BJORN, STEN = 0x1, 0x2
BJORN_PACK, STEN_PACK = 0xB1, 0xB2
HATCHET = 0x0F43
SAW = sorted(BuySaw.owned_tool_graphics)[0]
DROP = (10, 12)
BOARDS = DeliverBoards.deliver_threshold + 1  # one haul's worth, from the REAL config


def _item(serial, graphic, amount=1, *, container, layer=0, pos=(0, 0)):
    return ItemView(serial=serial, graphic=graphic, amount=amount,
                    pos=Position(pos[0], pos[1], 0), container=container, layer=layer,
                    distance=0)


def _shared_world():
    items = {
        BJORN_PACK: _item(BJORN_PACK, 0x0E75, container=BJORN, layer=BACKPACK_LAYER),
        STEN_PACK: _item(STEN_PACK, 0x0E75, container=STEN, layer=BACKPACK_LAYER),
        0x100: _item(0x100, HATCHET, container=BJORN_PACK),
        0x101: _item(0x101, BOARD_GRAPHIC, BOARDS, container=BJORN_PACK),
        0x102: _item(0x102, SAW, container=STEN_PACK),
    }
    bjorn_body = MockBody(player=PlayerView(serial=BJORN, name="Bjorn",
                                            pos=Position(10, 10, 0)), items=items)
    sten_body = MockBody(player=PlayerView(serial=STEN, name="Sten",
                                           pos=Position(10, 14, 0)), items=items)
    return items, bjorn_body, sten_body


def _boards_total(items, *bodies) -> int:
    total = sum(i.amount for i in items.values() if i.graphic == BOARD_GRAPHIC)
    for b in bodies:
        if b.held is not None and b.held.graphic == BOARD_GRAPHIC:
            total += b.held.amount
    return total


def _pack_boards(items, pack_serial) -> int:
    return sum(i.amount for i in items.values()
               if i.graphic == BOARD_GRAPHIC and i.container == pack_serial)


def test_the_handover_closes_offline_with_every_oracle_holding():
    items, bjorn_body, sten_body = _shared_world()

    bjorn = WoodsmanLife(body=bjorn_body, persona=Persona(name="Bjorn"),
                         routes={"carpenter_drop": DROP})
    bjorn.memory["lumber_home"] = (10, 10)
    bjorn.set_leash((10, 10), 4)

    sten = CarpenterLife(body=sten_body, persona=Persona(name="Sten"), routes={})
    sten.set_leash(DROP, 3)

    handover_done = False
    for tick in range(400):
        bjorn.tick()
        sten.tick()
        # Oracle 2: the generic stall shape, on BOTH sides, every tick.
        assert bjorn.rule_gate_disagreement is None, (
            f"t{tick}: Bjorn rule-vs-gate disagreement {bjorn.rule_gate_disagreement}")
        assert sten.rule_gate_disagreement is None, (
            f"t{tick}: Sten rule-vs-gate disagreement {sten.rule_gate_disagreement}")
        # Oracle 3: leashes hold (the +2 allows the mid-errand excursion the deliver
        # walk itself is — Bjorn's drop lies 2 beyond his leash on purpose).
        bp = bjorn_body.player.pos
        sp = sten_body.player.pos
        assert max(abs(bp.x - 10), abs(bp.y - 10)) <= 4 + 2, f"t{tick}: Bjorn strayed"
        assert max(abs(sp.x - DROP[0]), abs(sp.y - DROP[1])) <= 3 + 2, (
            f"t{tick}: Sten strayed out of pickup range of his own supply")
        # Oracle 4: conservation — a mock that leaks items would fake the handover.
        assert _boards_total(items, bjorn_body, sten_body) == BOARDS, (
            f"t{tick}: boards not conserved")
        if _pack_boards(items, STEN_PACK) >= DeliverBoards.deliver_threshold:
            handover_done = True
            break

    # Oracle 1: the point.
    assert handover_done, (
        f"the handover never completed: bjorn_pack={_pack_boards(items, BJORN_PACK)} "
        f"ground={sum(i.amount for i in items.values() if i.graphic == BOARD_GRAPHIC and i.container is None)} "
        f"sten_pack={_pack_boards(items, STEN_PACK)} "
        f"(bjorn: mode={bjorn.mode} want={bjorn.target_cap}; "
        f"sten: mode={sten.mode} want={sten.target_cap})"
    )
    assert _pack_boards(items, BJORN_PACK) < DeliverBoards.deliver_threshold
