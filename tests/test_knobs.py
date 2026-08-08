"""The tuning-knob channel: one clamped read, and a real path from a spec into a Life.

Two halves, both of which were SHAPE without WIRING before this suite existed.

*The clamp.* `skills/market.py::_bank_reserve` was the only knob read in the codebase
that had learned the lesson — one memory key, and rule + gate + FSM all reading it
through one clamping function, because a malformed value read raw by one side and
clamped by the other recreated the rule-vs-gate drift class THROUGH the knob itself.
That lesson lived in one function bound to one key, so knob N+1 inherited nothing.
`anima2/knobs.py` is its general form; these tests pin that generalizing it changed
`_bank_reserve` not at all (it is referenced by name in every Life, in `capabilities.py`
and in the audit, so byte-identical behavior is the requirement, not a nicety).

*The channel.* `LifeSpec.life_factory` was typed `(body, persona, routes) -> Life`, so a
spec could not express a threshold; then it could, and still nothing in `anima2/` set
one — the two shipped runners took no knob argument, so the only way to tune a production
Life was to hand-build a spec, which is a mechanism only the tests have. CLAUDE.md defers
the Phase-7 evolution-vs-random rerun on precondition (a), "the genome's axes can steer a
full Life", and a channel wireless at the ENTRY POINT makes that false however many knob
parameters the Life classes grow. These tests walk the whole path in one hop each:
`village.run_carpenter_life(knobs=...)` / `run_woodsman_life(knobs=...)` -> `LifeSpec.
knobs` -> `LifeRunner.build_life` -> the constructed Life's own memory and attributes.

What is covered by TEST and what is covered by INSPECTION, stated plainly: everything up
to and including `LifeRunner.build_life` is tested here, because the runners are pure
until they hand the spec over and `build_life` is a named seam for exactly this reason.
`LifeRunner.run()` — spawn, GM staging, worker thread — needs a live shard and is not
tested; it reaches the Life through that same `build_life` call, which is the whole point
of the seam existing rather than `run()` inlining the factory call.
"""

import ast
from pathlib import Path

import pytest

from anima2.contract import ItemView, Observation, PlayerView, Position
from anima2.knobs import knob_int, knob_param
from anima2.life_runner import LifeRunner, LifeSpec
from anima2.persona import Persona
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.market import _bank_reserve

PLAYER = 1
BP = 0x50

#: Sentinel for "the key is not in the memory at all" — distinct from a stored `None`,
#: which is a PRESENT malformed value and must clamp rather than fall back.
ABSENT = object()

#: `(stored value, expected read)` at `default=88`, pinned against the exact body
#: `_bank_reserve` had before it delegated::
#:
#:     if "bank_reserve" not in memory: return default
#:     reserve = memory.get("bank_reserve", 0)
#:     return reserve if type(reserve) is int and reserve > 0 else 0
#:
#: `True`/`False` are in the table on purpose: `bool` IS an `int` subclass, so an
#: `isinstance` clamp would quietly honour `True` as a reserve of 1.
CLAMP_TABLE = [
    (ABSENT, 88),
    (0, 0),
    (5, 5),
    (-50, 0),
    (12.5, 0),
    (True, 0),
    (False, 0),
    ("80", 0),
    (None, 0),
]


def _memory(value):
    return {} if value is ABSENT else {"bank_reserve": value}


class _MockBody:
    """A body that answers `observe()` from a canned sequence; no shard, no pumps."""

    connected = True

    def __init__(self, obs_seq=()):
        self._it = iter(obs_seq)
        self._last = None

    def observe(self):
        try:
            self._last = next(self._it)
        except StopIteration:
            pass
        return self._last

    def act(self, action):
        pass


def _obs(items=()):
    return Observation(player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0)),
                       items=[ItemView(serial=BP, graphic=0x0E75, amount=1,
                                       pos=Position(), container=PLAYER,
                                       layer=BACKPACK_LAYER, distance=0), *items])


# --- the clamp: generalizing it must not have moved `_bank_reserve` ------------------

def test_bank_reserve_behavior_is_unchanged_by_the_delegation():
    # The hard requirement: absent falls back to the caller's default, present-but-
    # malformed clamps to 0 on EVERY reader. A rule that read the raw value while the
    # gate read the clamp wanted `bank_gold` at any gold while the gate refused at 0g.
    for stored, expected in CLAMP_TABLE:
        assert _bank_reserve(_memory(stored), 88) == expected, f"stored={stored!r}"


def test_bank_reserve_is_exactly_the_general_read_bound_to_its_key():
    # Not "similar to": the same function. If these ever diverge, the Lives and the
    # gates are back to two clamps that merely agree today.
    for stored, _expected in CLAMP_TABLE:
        memory = _memory(stored)
        assert _bank_reserve(memory, 88) == knob_int(memory, "bank_reserve", 88)
    # ...including the unset default the gate and the FSM actually pass.
    assert _bank_reserve({}) == knob_int({}, "bank_reserve") == 0


def test_an_absent_knob_takes_the_callers_default_untouched():
    # The asymmetry is load-bearing: a Life passes its own derived constant while the
    # gate passes 0, and forward safety survives because the Life's is the stricter.
    # A module constant is trusted code, not a tuning write, so it is not clamped.
    assert knob_int({}, "grace", 6) == 6
    assert knob_param(None, 6) == 6


def test_the_floor_is_per_caller_because_zero_is_not_always_safe():
    # 0 is a safe floor for "gold to keep" and a catastrophic one for "ticks before the
    # disagreement detector fires": at 0 the detector fires every tick and its repair
    # closes a UI surface on every one of them.
    for bad in (-100, 0, 12.5, True, "3"):
        assert knob_int({"t": bad}, "t", 10, floor=1) == 1, f"stored={bad!r}"
        assert knob_param(bad, 10, floor=1) == 1, f"param={bad!r}"
    assert knob_int({"t": 3}, "t", 10, floor=1) == 3
    assert knob_param(3, 10, floor=1) == 3
    # `None` is where the two absence conventions differ, deliberately: as a stored
    # memory value it is a PRESENT malformed write and clamps; as a parameter it is
    # the "not tuned" sentinel and the caller's own default stands.
    assert knob_int({"t": None}, "t", 10, floor=1) == 1
    assert knob_param(None, 10, floor=1) == 10


def test_the_clamp_sits_below_every_layer_it_serves():
    """`knobs.py` imports nothing from `anima2` — the property that keeps it ONE read.

    A clamp that could import a skill, a capability or a Life would eventually be
    tempted to special-case one of them, and the moment it does, every caller's
    agreement stops being structural.
    """
    # Resolved from THIS FILE, never from the cwd. `Path("anima2/knobs.py")` only
    # exists when pytest happens to be invoked from the repo root, so the one assertion
    # that guards the module's "imports nothing from anima2" property silently became a
    # FileNotFoundError from any other directory — a structural test that stops running
    # is worse than no test, because the suite still reports green.
    tree = ast.parse((Path(__file__).resolve().parent.parent
                      / "anima2" / "knobs.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"relative import of {node.module!r}"
            assert node.module in {"__future__", "typing"}, node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("anima2"), alias.name


# --- the channel: a spec that can carry a threshold ---------------------------------

def _spec(**kwargs):
    return LifeSpec(profession="swordsman", persona_name="Bram", account_prefix="x",
                    life_factory=_warrior_factory, stage=lambda gm, s, b: None,
                    **kwargs)


def _warrior_factory(body, persona, routes, **knobs):
    from anima2.warrior_life import WarriorLife

    return WarriorLife(body=body, persona=persona, routes=routes, **knobs)


def test_a_spec_carries_no_knobs_until_somebody_sets_one():
    # Optional, and empty by default — every existing runner keeps its behavior.
    assert _spec().knobs == {}


def test_the_specs_knobs_reach_the_constructed_life():
    """The whole point: a tuned threshold set on the SPEC lands inside the Life.

    Both knob shapes at once, because they travel differently and the difference is the
    part that is easy to get backwards: `bank_reserve` must land in the econ agent's
    MEMORY (a `decide` rule is a staticmethod over `(obs, memory)` and can see nothing
    else), while `disagreement_ticks` rides an instance attribute (only `tick()` and a
    method read it).
    """
    spec = _spec(knobs={"bank_reserve": 4242, "disagreement_ticks": 3})
    life = LifeRunner(spec).build_life(_MockBody(), {"banker_spot": ((10, 10),)})
    assert life.econ_agent.memory["bank_reserve"] == 4242
    assert life.disagreement_ticks == 3
    # ...and the load-bearing arguments still arrive alongside the knobs.
    assert life.persona.name == "Bram"
    assert life.econ_agent.memory["banker_spot"] == ((10, 10),)


def test_an_unknobbed_spec_builds_the_life_exactly_as_before():
    from anima2.warrior_life import DISAGREEMENT_TICKS, WarriorLife

    life = LifeRunner(_spec()).build_life(_MockBody(), {})
    assert life.econ_agent.memory["bank_reserve"] == WarriorLife.DEFAULT_BANK_RESERVE
    assert life.disagreement_ticks == DISAGREEMENT_TICKS


def test_a_malformed_knob_from_the_spec_is_clamped_not_honoured():
    # A genome axis explores; it does not stop at the shipped value. A negative
    # hysteresis window must become the floor, not a Life that reports a disagreement
    # (and closes a UI surface) every single tick.
    life = LifeRunner(_spec(knobs={"disagreement_ticks": -5})).build_life(_MockBody(), {})
    assert life.disagreement_ticks == 1


def _capture_specs(**runner_kwargs):
    """Run the two real village runners far enough to grab their `LifeSpec`s.

    They construct their spec inline and hand it straight to `LifeRunner.run()`, so the
    only way to test the SHIPPED factories (rather than a lookalike written here) is to
    intercept the runner. Nothing touches a shard: the capture never calls `run`.

    Everything the runners do BEFORE constructing the spec is pure — argument defaults,
    a local import, `find_tree_clusters` over the static lumber map — so the capture
    reaches the spec offline. Everything AFTER it (`LifeRunner.run`: spawn, GM staging,
    the worker thread) needs a shard, which is exactly the boundary the `build_life`
    seam exists to make testable from both sides.
    """
    import anima2.life_runner as life_runner
    import anima2.village as village

    captured = []

    class _Capture:
        def __init__(self, spec, **kwargs):
            captured.append(spec)

        def run(self, worker):
            pass

    real = life_runner.LifeRunner
    life_runner.LifeRunner = _Capture
    try:
        village.run_carpenter_life(**runner_kwargs)
        village.run_woodsman_life(**runner_kwargs)
    finally:
        life_runner.LifeRunner = real
    return captured


def test_the_production_life_factories_accept_and_forward_knobs():
    """The gap this task closed, pinned where it was: the SHIPPED runners.

    Both village factories were `lambda body, persona, routes: XLife(...)` — a signature
    that cannot accept a knob and would `TypeError` the moment a spec carried one. A
    knob mechanism nothing in `anima2/` can reach is a mechanism only the tests have.
    """
    specs = _capture_specs()
    assert len(specs) == 2
    for spec in specs:
        life = spec.life_factory(_MockBody(), Persona(name="T"), {}, bank_reserve=1234)
        assert life.econ_agent.memory["bank_reserve"] == 1234, spec.profession
        # ...and unknobbed, each still writes its own derived reserve.
        plain = spec.life_factory(_MockBody(), Persona(name="T"), {})
        assert plain.econ_agent.memory["bank_reserve"] == type(plain).DEFAULT_BANK_RESERVE


# --- the ENTRY POINT: the runner argument a caller actually has -----------------------


def test_the_shipped_runners_take_no_knobs_and_behave_exactly_as_before():
    """Optional means optional: every existing call site keeps its shipped thresholds."""
    from anima2.warrior_life import DISAGREEMENT_TICKS, ECON_GRACE

    for spec in _capture_specs():
        assert spec.knobs == {}, spec.profession
        life = LifeRunner(spec).build_life(_MockBody(), {})
        assert life.econ_agent.memory["bank_reserve"] == type(life).DEFAULT_BANK_RESERVE
        assert life.disagreement_ticks == DISAGREEMENT_TICKS, spec.profession
        assert life.econ_grace == ECON_GRACE, spec.profession


def test_a_knob_travels_the_whole_way_from_the_runner_argument_into_the_life():
    """The gap this task closed, end to end: `run_X_life(knobs=...)` -> `LifeSpec.knobs`
    -> `LifeRunner.build_life` -> the constructed Life's own memory and attributes.

    Before this, `LifeSpec.knobs` existed and `CarpenterLife`/`WoodsmanLife` accepted
    `**knobs`, but the two SHIPPED runners took no knob argument and passed none — so the
    only way to tune a production Life was to hand-build a spec, which is a mechanism only
    the tests have. CLAUDE.md gates a multi-hour single-GM live budget on precondition
    (a), "the genome's axes can steer a full Life"; a channel wireless at the entry point
    makes that false however many knob parameters the Life classes grow.

    Both knob SHAPES travel here, because they travel differently and that is the part
    that is easy to get backwards: `bank_reserve` must land in the econ agent's MEMORY
    (a `decide` rule is a staticmethod over `(obs, memory)` and can see nothing else),
    while `econ_grace`/`disagreement_ticks` ride instance attributes.
    """
    tuned = {"bank_reserve": 4242, "econ_grace": 9, "disagreement_ticks": 3}
    specs = _capture_specs(knobs=tuned)
    assert len(specs) == 2
    for spec in specs:
        assert spec.knobs == tuned, spec.profession
        life = LifeRunner(spec).build_life(_MockBody(), {"banker_spot": ((10, 10),)})
        assert life.econ_agent.memory["bank_reserve"] == 4242, spec.profession
        assert life.econ_grace == 9, spec.profession
        assert life.disagreement_ticks == 3, spec.profession
        # ...and the load-bearing arguments still arrive alongside the knobs.
        assert life.persona.name == spec.persona_name
        assert life.econ_agent.memory["banker_spot"] == ((10, 10),)


def test_a_malformed_knob_from_the_runner_is_clamped_on_the_way_in():
    """A genome axis explores; it does not stop at the shipped value. Every one of these
    must land on its clamp FLOOR, because a threshold read raw on one side and clamped on
    the other is the rule-vs-gate drift class arriving through the tuning knob itself
    (see `anima2/knobs.py`). The floors are not all 0: `disagreement_ticks` at 0 would
    report a disagreement every tick and close a UI surface on each one."""
    specs = _capture_specs(knobs={"bank_reserve": -50, "econ_grace": -1,
                                  "disagreement_ticks": 0})
    for spec in specs:
        life = LifeRunner(spec).build_life(_MockBody(), {})
        # `bank_reserve` is clamped by its READERS, not the writer, and the concordance
        # suite walks that: the constructor stores what it was handed, and
        # `market._bank_reserve` — the one read point the rule, the gate and the FSM
        # share — answers 0 for it.
        assert _bank_reserve(life.econ_agent.memory) == 0, spec.profession
        assert life.econ_grace == 2, spec.profession
        assert life.disagreement_ticks == 1, spec.profession


def test_the_runner_copies_its_knobs_so_a_caller_cannot_retune_a_running_life():
    """A sweep script reuses one dict across runs. Aliasing it into the spec would let a
    later mutation reach a Life that was already built from it."""
    caller_dict = {"bank_reserve": 777}
    specs = _capture_specs(knobs=caller_dict)
    caller_dict["bank_reserve"] = 1
    for spec in specs:
        assert spec.knobs == {"bank_reserve": 777}, spec.profession


# --- the ALLOWLIST: what the channel must refuse to carry -----------------------------


def test_the_tuning_channel_refuses_to_reprofession_a_life():
    """The channel's worst reachable key, review-caught.

    `build_life` splats `knobs` into the Life constructor, and not every parameter there
    is a threshold — `profession` is IDENTITY. Handed through the channel it split the
    Life in two: `spec.profession` still drove the GM staging, the worker label and
    `telemetry_line`'s `ready=` list as a carpenter, while `_profession_key` drove the
    goal policy, the capability cognition and the disagreement detector as a mage, with
    `decide` still the carpenter's own rule. Measured on one observation (saw + 5000g in
    the pack, `banker_spot`/`vendor_spot`/`craft_spot` routed): the carpenter rule wants
    `buy_boards`, `ready_capability_ids("carpenter", ctx)` is `['bank_gold',
    'buy_boards']` and `ready_capability_ids("mage", ctx)` is `['bank_gold']` — so the
    split Life wants, permanently, a capability its own gate set no longer contains,
    while `telemetry_line` (which reads `spec.profession`) prints the carpenter's list
    and contradicts it. Past `disagreement_ticks` that is not just noise: read
    `warrior_life._detect_disagreement`, which calls `_clear_stale_ui` on EVERY
    subsequent tick with no rate limit — the audit's "16 unowned-vendor-window closes in
    30 ticks on a healthy agent" shape. The rule-vs-gate drift class this whole change
    set exists to kill, re-entered through the channel built to make tuning safe.

    Not a contrived key: `foundry/archive.py::Genome`'s first axis is literally named
    `profession`, and the genome is the searcher this channel is being built for.
    """
    from anima2.foundry.archive import Genome

    assert "profession" in Genome.__dataclass_fields__, (
        "the axis this test guards against was renamed — re-check the allowlist")
    with pytest.raises(ValueError, match="not a tuning knob"):
        _capture_specs(knobs={"profession": "mage"})
    # And the same for `steering`, which is not identity but cognition: it is clamped by
    # nothing and builds a real LLM client at construction time.
    with pytest.raises(ValueError, match="not a tuning knob"):
        _capture_specs(knobs={"steering": "llm"})


def test_a_typoed_axis_fails_before_the_login_not_after_the_staging():
    """An unknown key always raised — but from `build_life`, which `LifeRunner.run`
    reaches only AFTER the spawn, the GM staging, the provenance gold-wipe and the seed
    grant. No live run has hit that yet; the point is that a singular-vs-plural typo was
    positioned to spend a whole shard slot and abandon a spawned, logged-in, seeded
    character behind an unhandled traceback. `knobs` is fully known at spec construction,
    before the first packet, so that is where it is checked now — and the message names
    the keys this Life actually has."""
    with pytest.raises(ValueError) as e:
        _capture_specs(knobs={"disagreement_tick": 5})
    assert "disagreement_tick" in str(e.value)
    assert "disagreement_ticks" in str(e.value)


def test_a_subclass_extends_the_allowlist_it_never_replaces_it():
    """The tinker is the only profession with a knob of its own, and it is the flagship
    positive-margin loop — a `KNOBS` that replaced the inherited three would reject
    `bank_reserve` on the one Life whose reserve matters most."""
    from anima2.tinker_life import TinkerLife
    from anima2.warrior_life import WarriorLife

    assert WarriorLife.KNOBS < TinkerLife.KNOBS
    assert "bank_trip_surplus" in TinkerLife.KNOBS


def test_every_allowlisted_knob_is_really_a_constructor_parameter():
    """The allowlist is a promise in both directions. A name in `KNOBS` that no
    constructor accepts would pass the spec check and then `TypeError` in `build_life` —
    exactly the after-the-staging failure the check was added to move earlier."""
    import inspect

    from anima2.tinker_life import TinkerLife
    from anima2.warrior_life import WarriorLife

    for cls in (WarriorLife, TinkerLife):
        accepted = {name for klass in cls.__mro__
                    if "__init__" in vars(klass)
                    for name, p in inspect.signature(klass.__init__).parameters.items()
                    if p.kind is inspect.Parameter.KEYWORD_ONLY}
        assert cls.KNOBS <= accepted, f"{cls.__name__}: {cls.KNOBS - accepted}"


def test_the_shipped_specs_read_their_allowlist_off_the_class_they_build():
    """Spelled-out names in the runner would be a second source that can drift from the
    constructor it splats into; read off the class, a Life that gains a knob is tunable
    the same day."""
    from anima2.carpenter_life import CarpenterLife
    from anima2.woodsman_life import WoodsmanLife

    expected = {"carpenter": CarpenterLife.KNOBS, "lumberjack": WoodsmanLife.KNOBS}
    for spec in _capture_specs():
        assert spec.knob_names == expected[spec.profession], spec.profession


# --- the last rung: the command line -------------------------------------------------

def test_knob_pairs_parse_into_the_runners_dict():
    """`--knob KEY=VALUE`, repeatable. The runners grew a `knobs` argument and the spec
    grew a field, but until this parser existed the only way to set one was to import
    the module — a channel reachable by no human and no shell script."""
    from anima2.village import _parse_knobs

    assert _parse_knobs([]) == {}
    assert _parse_knobs(["bank_reserve=400"]) == {"bank_reserve": 400}
    assert _parse_knobs(["bank_reserve=400", "econ_grace=3"]) == {
        "bank_reserve": 400, "econ_grace": 3,
    }
    # Negative and zero parse fine here; clamping is `knobs.py`'s job, not the CLI's,
    # and duplicating the floor in the parser would be the second source all over again.
    assert _parse_knobs(["econ_grace=-1"]) == {"econ_grace": -1}


def test_a_malformed_knob_pair_dies_at_the_command_line_not_on_the_shard():
    """`knobs.py` clamps a bad value SILENTLY by design — a live run must not crash on a
    tuning typo. That is exactly why the one boundary that can be loud must be: this
    failure costs a shell prompt, the clamped one costs a run you then misread."""
    import pytest

    from anima2.village import _parse_knobs

    for bad in ["bank_reserve", "=5", ""]:
        with pytest.raises(SystemExit, match="KEY=VALUE"):
            _parse_knobs([bad])
    with pytest.raises(SystemExit, match="integer"):
        _parse_knobs(["bank_reserve=lots"])


def test_the_cli_refuses_a_knob_no_runner_would_carry():
    """A knob passed to a roster that builds no Life must not be silently dropped: the
    operator would read the run as tuned when it ran on defaults, which is the
    wireless-channel defect wearing a command line.

    This USED to assert `--pipeline` was refused, because only --carpenter and
    --woodsman carried the channel. That limitation is what audit follow-up 2 named and
    what the seven-site wiring removed, so the test now pins the property rather than
    the allowlist: the DEFAULT trade-village roster is plain `Agent`s with no
    orchestrator and no thresholds, and it is the one thing left that has to refuse.

    The guard has to run BEFORE any runner does, so this asserts on argument parsing
    alone — no shard, no bridge, no roster."""
    import pytest

    with pytest.raises(SystemExit, match="builds no Life"):
        _run_cli(["--miners", "1", "--knob", "bank_reserve=400"])


def test_the_cli_hands_a_parsed_knob_to_every_runner_that_carries_one():
    """The other half of the same guard: the value must ARRIVE. A refusal test alone
    would pass just as well against a CLI that dropped every knob.

    All SEVEN Life-construction sites, because "two of seven are wired" is exactly the
    state audit follow-up 2 warned against reading as done."""
    from unittest.mock import patch

    with patch("anima2.village.run_carpenter_life") as run:
        _run_cli(["--carpenter", "--knob", "bank_reserve=400", "--knob", "econ_grace=3"])
    assert run.call_args.kwargs["knobs"] == {"bank_reserve": 400, "econ_grace": 3}

    with patch("anima2.village.run_woodsman_life") as run:
        _run_cli(["--woodsman", "--knob", "bank_reserve=222"])
    assert run.call_args.kwargs["knobs"] == {"bank_reserve": 222}

    # The FLAGSHIP: the positive-margin miner->tinker chain, the loop a gold-per-life
    # fitness run would actually measure, and `bank_trip_surplus` is the tinker's own
    # knob — reachable from a shell for the first time here.
    with patch("anima2.village.run_forge_pair") as run:
        _run_cli(["--forge-pair", "--knob", "bank_trip_surplus=90"])
    assert run.call_args.kwargs["knobs"] == {"bank_trip_surplus": 90}

    with patch("anima2.village.run_warrior_village") as run:
        _run_cli(["--warriors", "2", "--knob", "econ_grace=5"])
    assert run.call_args.kwargs["knobs"] == {"econ_grace": 5}

    with patch("anima2.village.run_artisan_mage_village") as run:
        _run_cli(["--pipeline", "--knob", "bank_reserve=77"])
    assert run.call_args.kwargs["mage_knobs"] == {"bank_reserve": 77}

    # Two Lives, so two dicts, and the prefix decides which — no bare form here.
    with patch("anima2.village.run_supply_pair") as run:
        _run_cli(["--supply-pair", "--knob", "woodsman:bank_reserve=1",
                  "--knob", "carpenter:bank_reserve=2"])
    assert run.call_args.kwargs["woodsman_knobs"] == {"bank_reserve": 1}
    assert run.call_args.kwargs["carpenter_knobs"] == {"bank_reserve": 2}


def test_a_bare_knob_is_refused_where_it_would_have_to_guess_a_life():
    """`--supply-pair` builds a woodsman AND a carpenter, both with a `bank_reserve`.
    A bare `bank_reserve=400` there has no honest meaning, and picking one silently is
    the same misreporting the roster guard above exists to stop — so it is refused with
    the roles named and a corrected command in the message."""
    import pytest

    with pytest.raises(SystemExit, match="ambiguous"):
        _run_cli(["--supply-pair", "--knob", "bank_reserve=400"])
    # ...and the refusal must name a role that actually exists on this runner.
    with pytest.raises(SystemExit, match="has no 'tinker'"):
        _run_cli(["--supply-pair", "--knob", "tinker:bank_reserve=400"])


def test_the_role_prefix_is_optional_where_there_is_only_one_life():
    """Requiring it everywhere would break every shipped `--carpenter --knob K=V`
    invocation for no gain: with one Life the bare form cannot be ambiguous."""
    from unittest.mock import patch

    with patch("anima2.village.run_carpenter_life") as run:
        _run_cli(["--carpenter", "--knob", "carpenter:bank_reserve=400"])
    assert run.call_args.kwargs["knobs"] == {"bank_reserve": 400}


def _run_cli(argv: list[str]) -> None:
    import sys
    from unittest.mock import patch

    from anima2 import village

    with patch.object(sys, "argv", ["village", *argv]):
        village.main()


# --- the FIVE inline construction sites (audit follow-up 2) ---------------------------
#
# `LifeSpec`/`LifeRunner.build_life` covers two of the seven places a Life is built.
# The other five are hand-written inside `run_forge_pair` (the flagship positive-margin
# tinker), `run_supply_pair` (a woodsman AND a carpenter), `run_warrior_village` and
# `run_artisan_mage_village` — and CLAUDE.md gates a multi-hour single-GM live budget on
# precondition (a), "the genome's axes can steer a full Life". Two of seven is not that.


def test_every_life_class_can_be_built_through_the_inline_seam():
    """`build_tuned_life` is `LifeRunner.build_life` for the runners that have no spec,
    and it exists for the same stated reason: those runners need a shard, so without a
    named seam the only way to prove a tuned value reaches the Life is to re-implement
    the construction line in a test — the exact shape of an asserted-but-not-wired
    channel. Both knob shapes travel, as on the spec path."""
    from anima2.carpenter_life import CarpenterLife
    from anima2.life_runner import build_tuned_life
    from anima2.mage_life import MageLife
    from anima2.tinker_life import TinkerLife
    from anima2.warrior_life import WarriorLife
    from anima2.woodsman_life import WoodsmanLife

    for cls in (TinkerLife, CarpenterLife, WoodsmanLife, MageLife, WarriorLife):
        life = build_tuned_life(cls, {"bank_reserve": 4242, "disagreement_ticks": 3},
                                body=_MockBody(), persona=Persona(name="T"),
                                routes={"banker_spot": ((10, 10),)})
        assert life.econ_agent.memory["bank_reserve"] == 4242, cls.__name__
        assert life.disagreement_ticks == 3, cls.__name__
        assert life.econ_agent.memory["banker_spot"] == ((10, 10),), cls.__name__
        # Unknobbed is byte-for-byte the shipped behaviour, for every one of them.
        plain = build_tuned_life(cls, None, body=_MockBody(), persona=Persona(name="T"),
                                 routes={})
        assert plain.econ_agent.memory["bank_reserve"] == cls.DEFAULT_BANK_RESERVE


def test_the_inline_seam_reads_its_allowlist_off_the_class_it_builds():
    """The one thing this seam can do that `LifeSpec` cannot. A spec's factory is a
    lambda, so the spec must be TOLD its allowlist (`knob_names`) and can be told the
    wrong one; here the class is named once and the allowlist follows it. So the
    tinker's own knob is accepted on a tinker and refused on a carpenter, with no
    per-site declaration to keep in step."""
    import pytest

    from anima2.carpenter_life import CarpenterLife
    from anima2.life_runner import build_tuned_life
    from anima2.tinker_life import TinkerLife

    life = build_tuned_life(TinkerLife, {"bank_trip_surplus": 90}, body=_MockBody(),
                            persona=Persona(name="Pim"), routes={})
    assert life.econ_agent.memory["bank_trip_surplus"] == 90

    with pytest.raises(ValueError, match="bank_trip_surplus"):
        build_tuned_life(CarpenterLife, {"bank_trip_surplus": 90}, body=_MockBody(),
                         persona=Persona(name="Sten"), routes={})


def test_a_bad_knob_stops_an_inline_runner_BEFORE_it_spawns_anything():
    """The placement, which is the part each inline site has to choose and the part a
    shared helper cannot choose for it. `LifeSpec` checks at spec construction and gets
    this free; an inline runner builds its Lives only after the logins, the GM staging,
    the provenance gold-wipe and the seed grant, so a one-character typo would otherwise
    abandon spawned, staged characters behind a traceback.

    Asserted by making the FIRST network call explode: if the guard ran late, the test
    would see that explosion instead of the ValueError."""
    from unittest.mock import patch

    from anima2 import village

    class _NoNetwork:
        @staticmethod
        def spawn(*a, **k):
            raise AssertionError("the runner reached the shard before checking its knobs")

    calls = [
        (village.run_forge_pair, {"knobs": {"nope": 1}}),
        (village.run_supply_pair, {"woodsman_knobs": {"nope": 1}}),
        (village.run_supply_pair, {"carpenter_knobs": {"nope": 1}}),
        (village.run_artisan_mage_village, {"mage_knobs": {"nope": 1}}),
    ]
    with patch.object(village, "ResilientIpcBody", _NoNetwork):
        for fn, kwargs in calls:
            with pytest.raises(ValueError, match="not a tuning knob"):
                fn(**kwargs)
        with pytest.raises(ValueError, match="not a tuning knob"):
            village.run_warrior_village(2, knobs={"nope": 1})


def test_the_forge_pairs_banner_would_report_a_tuned_value_not_the_module_default():
    """The staged line is how a live operator learns the channel carried anything —
    `run_carpenter_life`'s equivalent printing `reserve 400` against a module default of
    129 is the whole of the 2026-08-03 live proof. The flagship pair prints BOTH of the
    tinker's knobs, and both are read off the BUILT Life through the same clamp every
    other reader uses, so the banner cannot drift from what the Life will do."""
    from anima2.life_runner import build_tuned_life
    from anima2.tinker_life import (
        BANK_RESERVE,
        BANK_TRIP_SURPLUS,
        TinkerLife,
        bank_trip_surplus,
    )

    plain = build_tuned_life(TinkerLife, None, body=_MockBody(),
                             persona=Persona(name="Pim"), routes={})
    assert _bank_reserve(plain.econ_agent.memory) == BANK_RESERVE
    assert bank_trip_surplus(plain.econ_agent.memory) == BANK_TRIP_SURPLUS

    tuned = build_tuned_life(TinkerLife, {"bank_reserve": 400, "bank_trip_surplus": 90},
                             body=_MockBody(), persona=Persona(name="Pim"), routes={})
    assert _bank_reserve(tuned.econ_agent.memory) == 400
    assert bank_trip_surplus(tuned.econ_agent.memory) == 90
    # ...and a malformed value collapses to the floor on the banner exactly as it does
    # in the rule, because they are the same read.
    bad = build_tuned_life(TinkerLife, {"bank_trip_surplus": -5}, body=_MockBody(),
                           persona=Persona(name="Pim"), routes={})
    assert bank_trip_surplus(bad.econ_agent.memory) == 0


# --- wander_leash: the axis that arrived through a second channel ---------------------
#
# Audit follow-up 4: "`wander_leash` is the cheapest remaining axis (§E's 'exploration
# radius') and needs no Life work — but it arrives through `Staged.leash`, a DIFFERENT
# channel, and `skills/movement.py::Wander._homeward` clamps it its own way." That
# divergence was carved out in `knobs.py`'s own opening paragraph, which is how a
# single-source module quietly stops being one.


def test_the_leash_is_a_life_knob_like_every_other_threshold():
    """It travels as a MEMORY KEY, not an instance attribute: `Wander` is a skill and
    reads `ctx.memory`, so an attribute would be invisible to the only thing that uses
    it. Same split as `bank_reserve` — written raw, clamped by its one reader."""
    from anima2.life_runner import build_tuned_life
    from anima2.skills.movement import wander_leash
    from anima2.tinker_life import TinkerLife
    from anima2.warrior_life import WarriorLife

    assert "wander_leash" in WarriorLife.KNOBS
    life = build_tuned_life(TinkerLife, {"wander_leash": 3}, body=_MockBody(),
                            persona=Persona(name="Pim"), routes={})
    for memory in (life.hunt_agent.memory, life.econ_agent.memory):
        assert wander_leash(memory) == 3
    # Unset changes nothing, byte for byte: the key is simply absent and `Wander`'s own
    # class default stands.
    plain = build_tuned_life(TinkerLife, None, body=_MockBody(),
                             persona=Persona(name="Pim"), routes={})
    assert "wander_leash" not in plain.econ_agent.memory


def test_a_tuned_leash_outranks_the_runners_derived_one():
    """The only knob with a precedence question, because it is the only one something
    else already writes: every runner calls `set_leash(home, derived)` AFTER
    construction. If that write won, the channel would report success and change nothing
    — which is the wireless-channel defect this whole thread has been closing, wearing a
    knob that looks wired."""
    from anima2.carpenter_life import CarpenterLife
    from anima2.life_runner import build_tuned_life
    from anima2.skills.movement import wander_leash

    tuned = build_tuned_life(CarpenterLife, {"wander_leash": 3}, body=_MockBody(),
                             persona=Persona(name="Sten"), routes={})
    tuned.set_leash((10, 20), 9)         # the derived value a runner would pass
    for memory in (tuned.hunt_agent.memory, tuned.econ_agent.memory):
        assert wander_leash(memory) == 3, "the derived write clobbered the tuned value"
        # ...and the HOME still lands. It is not a knob and has no contest.
        assert memory["wander_home"] == (10, 20)

    # Untuned, the derived value is exactly as authoritative as it has always been —
    # Sten's leash is live-caught (he drifted three tiles off his own supply drop) and
    # this must not weaken it.
    plain = build_tuned_life(CarpenterLife, None, body=_MockBody(),
                             persona=Persona(name="Sten"), routes={})
    plain.set_leash((10, 20), 9)
    assert wander_leash(plain.econ_agent.memory) == 9


def test_an_exploring_leash_axis_is_clamped_like_every_other_knob():
    """A genome axis explores; it does not stop at the shipped value. The floor is 1
    rather than `knob_int`'s natural 0 because 0 is catastrophic for THIS threshold: the
    only tile inside a 0-leash is `wander_home` itself, so the agent steers home on every
    tick it is not standing on it and never wanders at all."""
    from anima2.life_runner import build_tuned_life
    from anima2.skills.movement import LEASH_FLOOR, wander_leash
    from anima2.warrior_life import WarriorLife

    for bad in (-40, 0, True, 2.5, "6"):
        life = build_tuned_life(WarriorLife, {"wander_leash": bad}, body=_MockBody(),
                                persona=Persona(name="Bram"), routes={})
        assert wander_leash(life.econ_agent.memory) == LEASH_FLOOR, repr(bad)
    assert LEASH_FLOOR == 1


# --- the banner that lies: five occurrences, now an assertion -------------------------
#
# A runner that accepts a knob and prints a MODULE CONSTANT (or nothing) reports a tuning
# that may not have happened. It has been fixed five times: `run_carpenter_life`'s baked
# `BANK_RESERVE` (which is why `LifeRunner.staged_line` reads the built Life at all),
# then `run_supply_pair` (no reserve printed), `run_forge_pair` (needed the leash and the
# trip surplus), `run_artisan_mage_village` (printed neither), and `run_warrior_village`
# (printed nothing tunable at all). Five is enough; this is the class as a test.


def test_every_life_bearing_runner_reports_what_it_will_actually_run_under():
    """Each runner must READ a clamped knob value off the Life it built. Reading is the
    property that matters — a printed module constant is accurate exactly until somebody
    tunes that runner, which is the moment the number starts mattering."""
    import ast
    import inspect

    from anima2 import village

    #: The clamped single read points. A banner going through one of these cannot drift
    #: from what the rule, the gate and the FSM will do, because it IS their read.
    CLAMPED = {"_bank_reserve", "wander_leash", "leash_readout", "bank_trip_surplus"}
    #: These two build a `LifeSpec` and hand it to `LifeRunner`, whose `staged_line` does
    #: the reading centrally — asserted separately below.
    DELEGATES = {"run_carpenter_life", "run_woodsman_life"}
    INLINE = {"run_forge_pair", "run_supply_pair", "run_warrior_village",
              "run_artisan_mage_village"}

    tree = ast.parse(inspect.getsource(village))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in INLINE | DELEGATES:
        assert name in funcs, f"{name} is gone or renamed; this test needs updating"

    for name in INLINE:
        called = {n.func.id for n in ast.walk(funcs[name])
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert called & CLAMPED, (
            f"{name} takes knobs and never reads one back off the Life it built, so a "
            f"tuned run would print — or omit — a number that is not the one it uses. "
            f"Read through one of {sorted(CLAMPED)}.")

    for name in DELEGATES:
        src = ast.dump(funcs[name])
        assert "LifeRunner" in src, (
            f"{name} no longer goes through LifeRunner, so it no longer inherits "
            f"`staged_line`'s reading and needs its own.")

    # ...and the central one really does read both knobs it prints.
    staged = inspect.getsource(LifeRunner.staged_line)
    assert "_bank_reserve(" in staged
    assert "leash_readout(" in staged or "wander_leash(" in staged


def test_an_inert_leash_is_reported_as_inert():
    """`wander_leash` is on `WarriorLife.KNOBS`, so all five Lives and all six runners
    accept it — but it is inert without a `wander_home`, because `Wander._homeward`
    returns before it ever reads the leash. `run_warrior_village` never calls
    `set_leash` ON PURPOSE (a warrior roams while hunting), so a tuned leash there reads
    back as the tuned number and steers nothing. A banner printing the bare value would
    report a tuning that provably cannot happen."""
    from anima2.skills.movement import leash_readout

    assert leash_readout({"wander_leash": 3, "wander_home": (5, 5)}) == "3"
    assert "inert" in leash_readout({"wander_leash": 3})
    # A malformed home is no home — same shape check `_homeward` applies, one definition.
    for bad in (None, (1, 2, 3), "5,5", (True, False), 5):
        assert "inert" in leash_readout({"wander_leash": 3, "wander_home": bad}), repr(bad)


# --- what an independent review of the above found (2026-08-07) -----------------------


def test_a_bare_and_a_prefixed_copy_of_one_knob_is_refused_not_silently_merged():
    """`bank_reserve=1` and `carpenter:bank_reserve=2` are DIFFERENT parser keys that
    land on the same role and knob, so the second silently overwrote the first — a knob
    the run ignores, which is the one thing `_route_knobs` exists to refuse. It was
    refusing the ambiguous and the unknown while quietly dropping the duplicated."""
    from anima2.village import _route_knobs

    with pytest.raises(SystemExit, match="twice"):
        _route_knobs({"bank_reserve": 1, "carpenter:bank_reserve": 2}, ("carpenter",),
                     runner="--carpenter", default_role="carpenter")
    # The same key for DIFFERENT roles is not a duplicate — that is the prefix's job.
    out = _route_knobs({"woodsman:bank_reserve": 1, "carpenter:bank_reserve": 2},
                       ("woodsman", "carpenter"), runner="--supply-pair",
                       default_role=None)
    assert out == {"woodsman": {"bank_reserve": 1}, "carpenter": {"bank_reserve": 2}}


def test_a_tuned_leash_outranks_STAGING_only_and_a_later_re_leash_wins():
    """The precedence was applied to every `set_leash` call for the Life's lifetime, and
    that was review-caught as breaking a real caller: `live_frame_overdue_gate` teleports
    the tinker and re-leashes it to the new spot, with a stated invariant that `Wander`
    must not walk it back. Against a tuned Life that re-leash became a no-op and the gate
    could report a bogus pass. Nothing tunes that gate today, so it was latent.

    The split: a runner's STAGING call passes a value derived from the world as a
    default and yields to tuning; a LATER call is a runtime decision and wins."""
    from anima2.carpenter_life import CarpenterLife
    from anima2.life_runner import build_tuned_life
    from anima2.skills.movement import wander_leash

    life = build_tuned_life(CarpenterLife, {"wander_leash": 3}, body=_MockBody(),
                            persona=Persona(name="Sten"), routes={})
    life.set_leash((10, 20), 9)                 # staging: the derived default yields
    assert wander_leash(life.econ_agent.memory) == 3
    life.set_leash((30, 40), 2)                 # a runtime decision: it wins
    for memory in (life.hunt_agent.memory, life.econ_agent.memory):
        assert wander_leash(memory) == 2
        assert memory["wander_home"] == (30, 40)


def test_a_leash_tuned_past_its_runners_derived_reach_says_so():
    """`wander_leash` is clamped downward only, on purpose — §E calls it "exploration
    radius" and an axis that cannot explore upward is not one. But `run_forge_pair` and
    `run_supply_pair` DERIVE `min(max(1, shop_reach), PICKUP_RADIUS - 1)` so the agent
    stays inside pickup range of its own ground drop, which is the live-caught defect
    Sten's leash exists to prevent. A tuned 40 is a legitimate thing for a search to try
    and a silent way for the flagship chain to stop closing, so the BANNER says so rather
    than the clamp refusing it. Review-caught."""
    from anima2.life_runner import build_tuned_life
    from anima2.skills.movement import leash_readout
    from anima2.tinker_life import TinkerLife

    life = build_tuned_life(TinkerLife, {"wander_leash": 40}, body=_MockBody(),
                            persona=Persona(name="Pim"), routes={})
    life.set_leash((10, 10), 5)                 # the derived, correctness-bearing value
    out = leash_readout(life.econ_agent.memory, life)
    assert "40" in out and "TUNED over a derived 5" in out and "unfetched" in out

    # Tuned BELOW the derived reach is not a hazard and must not be shouted about.
    tight = build_tuned_life(TinkerLife, {"wander_leash": 2}, body=_MockBody(),
                             persona=Persona(name="Pim"), routes={})
    tight.set_leash((10, 10), 5)
    assert leash_readout(tight.econ_agent.memory, tight) == "2"
    # ...and an untuned Life reports the derived value plainly.
    plain = build_tuned_life(TinkerLife, None, body=_MockBody(),
                             persona=Persona(name="Pim"), routes={})
    plain.set_leash((10, 10), 5)
    assert leash_readout(plain.econ_agent.memory, plain) == "5"
