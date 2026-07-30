"""The LifeRunner harness — the lessons are structural, so pin the structure.

Five runners grew by copy-paste and each new one shipped WITHOUT some piece its
predecessor had paid for live. The harness exists so omission is a construction error,
not a live rediscovery; these tests pin exactly that property, plus the staging
verification clauses that were each a real live failure.
"""

import pytest

from anima2.contract import ItemView, Observation, PlayerView, Position
from anima2.life_runner import (
    LifeSpec,
    StagingError,
    banked_amount,
    monitor_ports,
    owned_tool_readout,
    pack_amount,
    stage_shops,
    telemetry_line,
)
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.hunt import GOLD_GRAPHIC
from anima2.skills.market import BANKBOX_LAYER, SELL_REACH

PLAYER = 1
BP = 0x50


def _item(serial, graphic, amount=1, *, container=BP, layer=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=0)


def _obs(items):
    return Observation(player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0)),
                       items=[_item(BP, 0x0E75, container=PLAYER, layer=BACKPACK_LAYER),
                              *items])


# --- readbacks -----------------------------------------------------------------------

def test_pack_amount_is_owner_filtered_and_accepts_sets():
    other_pack = ItemView(serial=0x9A, graphic=0x0E75, amount=1, pos=Position(),
                          container=0x99, layer=BACKPACK_LAYER, distance=1)
    obs = _obs([_item(0x900, GOLD_GRAPHIC, 70),
                other_pack,
                _item(0x901, GOLD_GRAPHIC, 999, container=0x9A)])
    assert pack_amount(obs, GOLD_GRAPHIC) == 70
    assert pack_amount(obs, {GOLD_GRAPHIC}) == 70
    assert pack_amount(None, GOLD_GRAPHIC) == 0


def test_banked_amount_reads_the_bank_box_not_the_pack():
    # A falling pack count is not a deposit; only gold in OUR bank box counts.
    box = ItemView(serial=0x60, graphic=0x0E7C, amount=1, pos=Position(),
                   container=PLAYER, layer=BANKBOX_LAYER, distance=0)
    obs = _obs([box, _item(0x902, GOLD_GRAPHIC, 150),
                _item(0x903, GOLD_GRAPHIC, 70, container=0x60)])
    assert banked_amount(obs) == 70
    assert pack_amount(obs, GOLD_GRAPHIC) == 150


def test_owned_tool_readout_never_says_yes_for_a_neighbours_tool():
    # Reporting "yes" for the Weaponsmith's axe hid the exact defect that broke a run.
    HATCHET = 0x0F43
    obs = _obs([_item(0x991, HATCHET, container=0x99, layer=1)])
    assert owned_tool_readout(obs, {HATCHET}) == "NO"
    assert owned_tool_readout(_obs([_item(0x992, HATCHET)]), {HATCHET}) == "yes"
    assert owned_tool_readout(_obs([_item(0x993, HATCHET, container=None)]),
                              {HATCHET}) == "yes"


# --- staging verification ------------------------------------------------------------

class _Mob:
    def __init__(self, serial, x, y):
        self.serial = serial
        self.pos = Position(x, y, 0)


class _FakeGm:
    """Answers stage_npc from a script: the live-caught failure shapes on demand."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = []

    def stage_npc(self, npc, x, y, z, *, exclude=None):
        self.calls.append((npc, x, y, set(exclude or ())))
        return self._answers.pop(0)


def test_stage_shops_excludes_already_placed_npcs():
    # The Banker once resolved to the Weaponsmith's own serial — the second call must
    # carry the first shop's serial in its exclude set.
    gm = _FakeGm([_Mob(0x10, 6, 5), _Mob(0x11, 4, 5)])
    routes, tiles = stage_shops(gm, z=0, anchor=(5, 5), exclude=PLAYER,
                                spots={"a": ("Carpenter", (6, 5)),
                                       "b": ("Banker", (4, 5))})
    assert routes == {"a": [(6, 5)], "b": [(4, 5)]}
    assert 0x10 in gm.calls[1][3], "second stage must exclude the first NPC"
    assert tiles == {(6, 5), (4, 5)}


def test_strict_staging_raises_on_the_three_live_caught_failures():
    # (a) an NPC that failed to stage at all
    with pytest.raises(StagingError):
        stage_shops(_FakeGm([None]), z=0, anchor=(5, 5), exclude=PLAYER,
                    spots={"a": ("Carpenter", (6, 5))})
    # (b) two shops resolving to one NPC
    dup = _Mob(0x10, 6, 5)
    with pytest.raises(StagingError):
        stage_shops(_FakeGm([dup, dup]), z=0, anchor=(5, 5), exclude=PLAYER,
                    spots={"a": ("Carpenter", (6, 5)), "b": ("Banker", (4, 5))})
    # (c) a shop that settled out of reach — indistinguishable from a broken planner
    with pytest.raises(StagingError):
        stage_shops(_FakeGm([_Mob(0x10, 5 + SELL_REACH + 1, 5)]), z=0, anchor=(5, 5),
                    exclude=PLAYER, spots={"a": ("Carpenter", (6, 5))})


def test_non_strict_staging_preserves_the_old_print_and_continue_behavior():
    routes, tiles = stage_shops(_FakeGm([None]), z=0, anchor=(5, 5), exclude=PLAYER,
                                strict=False, spots={"a": ("Carpenter", (6, 5))})
    assert routes == {} and tiles == set()


def test_a_shared_placed_dict_spans_multi_anchor_calls():
    # The supply pair stages shops for two agents; a shop placed for one must never
    # answer for the other's.
    gm = _FakeGm([_Mob(0x10, 6, 5), _Mob(0x11, 20, 20)])
    placed: dict = {}
    stage_shops(gm, z=0, anchor=(5, 5), exclude=PLAYER, placed=placed,
                spots={"a": ("Carpenter", (6, 5))})
    stage_shops(gm, z=0, anchor=(20, 21), exclude=PLAYER, placed=placed,
                spots={"b": ("Banker", (20, 20))})
    assert 0x10 in gm.calls[1][3], "the second CALL must exclude the first call's NPC"


# --- the spec's no-defaults guarantee ------------------------------------------------

def test_forgetting_a_load_bearing_spec_field_is_a_construction_error():
    # The whole point of the harness: omission is a TypeError, not a live rediscovery.
    with pytest.raises(TypeError):
        LifeSpec(profession="carpenter", persona_name="Sten",
                 account_prefix="x", life_factory=lambda *a: None)  # no stage


def test_monitor_ports_allocates_distinct_ports_only_when_enabled():
    assert monitor_ports(False, ["a", "b"]) == {"a": None, "b": None}
    ports = monitor_ports(True, ["a", "b", "c"])
    assert len(set(ports.values())) == 3


# --- telemetry -----------------------------------------------------------------------

def test_telemetry_line_shows_want_admitted_and_ready_separately():
    # `want` alone is intent, and an unadmitted goal looks identical to a busy one.
    class _Stack:
        current = None

    class _Econ:
        memory = {}
        goal_stack = _Stack()

    class _Life:
        target_cap = "sell_boards"
        econ_agent = _Econ()
        persona = None

    from anima2.persona import Persona

    life = _Life()
    life.persona = Persona(name="T")
    line = telemetry_line(life, "lumberjack", _obs([]))
    assert "want=sell_boards" in line and "admitted=None" in line and "ready=" in line


def test_pickaxes_scale_with_the_budget_between_floor_and_weight_ceiling():
    from anima2.village import _pickaxes_for

    assert _pickaxes_for(120) == ["Pickaxe"] * 2       # smoke floor
    assert _pickaxes_for(300) == ["Pickaxe"] * 3
    assert _pickaxes_for(1200) == ["Pickaxe"] * 8      # capped: weight is capacity
    assert _pickaxes_for(1500) == ["Pickaxe"] * 8


def test_run_worker_reads_the_ticks_observation_and_never_observes_again():
    # forge10 postmortem (2026-07-30): `_run_worker`'s own `body.observe()` after
    # every tick STOLE the journal batch — `new_journal` is since-last-observe, so
    # with two consumers on one body the agent's skills saw empty journals: the
    # miner's relocation window stayed EMPTY through three full forge days while
    # every single-consumer probe passed. The worker is an observer of the AGENT,
    # not of the world: it must read the tick's own `_last_observation`.
    import threading

    from anima2.agent import Agent
    from anima2.contract import ItemView, PlayerView, Position
    from anima2.mock_body import MockBody
    from anima2.persona import Persona
    from anima2.planner import Planner
    from anima2.skills import Mine
    from anima2.skills.harvest import BACKPACK_LAYER
    from anima2.village import _run_worker

    PICKAXE = 0x0E86

    class CountingBody(MockBody):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.observes = 0

        def observe(self):
            self.observes += 1
            return super().observe()

    items = {
        0xB1: ItemView(serial=0xB1, graphic=0x0E75, amount=1, pos=Position(),
                       container=0x1, layer=BACKPACK_LAYER, distance=0),
        0x100: ItemView(serial=0x100, graphic=PICKAXE, amount=1, pos=Position(),
                        container=0xB1, layer=0, distance=0),
    }
    body = CountingBody(player=PlayerView(serial=0x1, name="Spy",
                                          pos=Position(10, 10, 0)), items=items)
    agent = Agent(body=body, persona=Persona(name="Spy"),
                  planner=Planner([Mine()]))
    _run_worker(agent, 5, 0, {}, threading.Lock(), "miner")
    # One observe per Agent.tick — the worker itself must add ZERO.
    assert body.observes == 5, (
        f"{body.observes} observes for 5 ticks — a second consumer is stealing "
        f"the journal batch again")


def test_market_resolver_prefers_the_staged_pin_over_the_nearest_guess():
    # Live (urgent-band gate, 2026-07-30): a Tinker and a Banker staged one tile
    # either side of a requested spot are EQUIDISTANT from it, so the resolver's
    # nearest-to-spot tie broke toward whichever the observation listed first —
    # two runs of three asked the Tinker to open a bank box, got no Bank entry,
    # and threw the trip away one tick after admitting the goal.
    from anima2.contract import MobileView, Observation, PlayerView, Position
    from anima2.persona import Persona
    from anima2.skills.base import SkillContext
    from anima2.skills.market import BlacksmithMarket

    VENDOR, BANKER, SPOT = 0x4443, 0x4444, (10, 11)

    def _mob(serial, x, y):
        return MobileView(serial=serial, name="", pos=Position(x, y, 0), body=400,
                          notoriety=1, hits=1, hits_max=1, distance=1)

    def _ctx(memory):
        # The vendor is listed FIRST and is exactly as close to the spot as the
        # banker — the coin flip, made deterministic against the wrong answer.
        obs = Observation(player=PlayerView(serial=1, pos=Position(10, 10, 0)),
                          mobiles=[_mob(VENDOR, 10, 10), _mob(BANKER, 10, 12)])
        return SkillContext(obs=obs, persona=Persona(name="Pim"), memory=memory)

    unpinned = _ctx({})
    assert BlacksmithMarket._find_market_mobile(unpinned, SPOT, "w") == VENDOR

    pinned = _ctx({"shop_serials": {SPOT: BANKER}})
    assert BlacksmithMarket._find_market_mobile(pinned, SPOT, "w") == BANKER

    # A pin whose mobile is out of sight WAITS rather than settling for a
    # neighbour that cannot serve the errand.
    missing = _ctx({"shop_serials": {SPOT: 0xDEAD}})
    assert BlacksmithMarket._find_market_mobile(missing, SPOT, "w") is None
    assert missing.memory["w"] == 1


def test_stage_shops_records_the_serial_it_actually_staged():
    from anima2.life_runner import stage_shops

    class _Mob:
        def __init__(self, serial, x, y):
            self.serial, self.pos = serial, type("P", (), {"x": x, "y": y, "z": 0})()

    class _Gm:
        def __init__(self):
            self._n = 0

        def stage_npc(self, npc, x, y, z, exclude=None):
            self._n += 1
            return _Mob(0x1000 + self._n, x, y)  # lands exactly where asked

    out: dict = {}
    routes, _tiles = stage_shops(_Gm(), z=0, anchor=(10, 10), exclude=1,
                                 spots={"vendor_spot": ("Tinker", (10, 9)),
                                        "banker_spot": ("Banker", (10, 11))},
                                 serials_out=out)
    assert out == {(10, 9): 0x1001, (10, 11): 0x1002}
    # Keyed by the READBACK position — the same tuple the route's last waypoint
    # holds, which is what the resolver is called with.
    assert all(tuple(routes[k][-1]) in out for k in routes)


def test_every_stage_shops_caller_passes_the_identity_pin():
    # Structural, because the failure is silent: a runner that forgets the pin
    # falls back to nearest-to-spot resolution, and that only misbehaves when
    # two shops happen to tie — one run in three, live-measured. Wiring it is a
    # one-liner; NOT wiring it costs a whole trip and looks like a slow banker.
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "anima2"
    offenders = []
    for path in sorted(root.glob("*.py")):
        src = path.read_text()
        for m in re.finditer(r"\bstage_shops\(", src):
            if "def stage_shops" in src[max(0, m.start() - 120):m.start()]:
                continue  # the definition itself
            # The call spans lines; take up to its closing paren.
            depth, i = 0, m.end() - 1
            while i < len(src):
                depth += (src[i] == "(") - (src[i] == ")")
                if depth == 0:
                    break
                i += 1
            call = src[m.start():i]
            if "serials_out" not in call:
                line = src[:m.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line}")
    assert not offenders, (
        "these stage_shops callers drop the shop-identity pin: " + ", ".join(offenders))


def test_pinned_runners_hand_the_map_to_the_agent_memory():
    # The pin is useless in a local variable: the resolver reads it from
    # ctx.memory["shop_serials"]. Every module that collects one must also hand
    # it over (Staged memory/econ_memory, or a direct memory write).
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "anima2"
    for path in sorted(root.glob("*.py")):
        src = path.read_text()
        if "serials_out=" not in src:
            continue
        assert re.search(r'["\']shop_serials["\']\s*[:\]]', src), (
            f"{path.name} collects a pin but never puts it in an agent memory")
