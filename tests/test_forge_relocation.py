"""Vein-exhaustion relocation, through the FORGE MINER'S exact inheritance chain.

The health check flagged "a drained vein ends the forge economy" after forge2's dead
run and forge3's zero-reward tail. The relocation machinery lives in `Harvest.step`
and `MineSmeltDeliver` reaches it via MineAndSmelt -> Mine -> Harvest — but nothing
pinned that the DELIVER-configured path (smithy_drop set, the forge pair's actual
shape) still accumulates the stuck window and fires `_start_relocate`. This does.

It also pins the TIMING fact the live runs made easy to misread: the window must be
FULL (probe-ring x rotations = 24 samples, one per swing REPLY) before the rate can
trigger — at live cadence that is roughly two minutes of swinging at a dead vein, so
a shorter zero-reward tail is "not yet", not "broken".
"""

from anima2.contract import ItemView, JournalEntry, Observation, PlayerView, Position
from anima2.persona import Persona
from anima2.skills.base import SkillContext
from anima2.skills.harvest import BACKPACK_LAYER, Mine
from anima2.skills.smelt import MineSmeltDeliver

PLAYER, BP = 1, 0x50
PICKAXE = 0x0E86
NO_METAL = 503040  # "There is no metal here to mine."


def _obs(*, cursor: bool, no_metal: bool):
    journal = [JournalEntry(0, "", "", 0, 0, NO_METAL)] if no_metal else []
    return Observation(
        player=PlayerView(serial=PLAYER, pos=Position(50, 50, 0)),
        items=[ItemView(serial=BP, graphic=0x0E75, amount=1, pos=Position(),
                        container=PLAYER, layer=BACKPACK_LAYER, distance=0),
               ItemView(serial=0x900, graphic=PICKAXE, amount=1, pos=Position(),
                        container=BP, layer=0, distance=0)],
        pending_target=(1 if cursor else None),
        new_journal=journal,
    )


def _drain(skill, memory, swings):
    """One swing = a cursor tick (target emitted) + a reply tick (no-metal)."""
    relocated_at = None
    for i in range(swings):
        ctx = SkillContext(obs=_obs(cursor=True, no_metal=False),
                           persona=Persona(name="Grimm"), memory=memory)
        skill.step(ctx)
        ctx = SkillContext(obs=_obs(cursor=False, no_metal=True),
                           persona=Persona(name="Grimm"), memory=memory)
        skill.step(ctx)
        if memory.get("harvest_relocating") and relocated_at is None:
            relocated_at = i + 1
    return relocated_at


def test_the_forge_miner_relocates_off_a_dead_vein():
    # The forge pair's exact shape: MineSmeltDeliver WITH smithy_drop configured.
    memory = {"smithy_drop": (60, 50)}
    window = max(1, len(Mine.probe_offsets)) * Mine.stuck_window_rotations
    relocated_at = _drain(MineSmeltDeliver(), memory, swings=window + 4)
    assert relocated_at is not None, (
        "a fully drained vein never triggered relocation through the "
        "MineSmeltDeliver -> MineAndSmelt -> Mine -> Harvest chain"
    )
    # ...and it fires only once the window can actually be full — the timing fact
    # that made a two-minute live tail look like a hang.
    assert relocated_at >= int(window * Mine.stuck_rate_threshold), relocated_at


def test_a_mostly_dead_vein_still_relocates_despite_trickle_successes():
    # The windowed-rate design's whole point (a strict streak was defeated by rare
    # trickle-through successes — see harvest.py's module comment).
    memory = {"smithy_drop": (60, 50)}
    skill = MineSmeltDeliver()
    window = max(1, len(Mine.probe_offsets)) * Mine.stuck_window_rotations
    relocated = None
    for i in range(window * 2):
        no_metal = (i % 5) != 4  # 80% dead, 20% trickle
        ctx = SkillContext(obs=_obs(cursor=True, no_metal=False),
                           persona=Persona(name="Grimm"), memory=memory)
        skill.step(ctx)
        ctx = SkillContext(obs=_obs(cursor=False, no_metal=no_metal),
                           persona=Persona(name="Grimm"), memory=memory)
        skill.step(ctx)
        if memory.get("harvest_relocating"):
            relocated = i + 1
            break
    assert relocated is not None, "an 80%-dead vein must still trigger relocation"


# --- the no-progress liveness line (the guard forge2's silent death was missing) ----

def test_a_dead_agent_self_reports_instead_of_dying_silently(capsys):
    import threading

    from anima2.village import _run_worker

    class _DeadBody:
        connected = True

        def observe(self):
            return _obs(cursor=False, no_metal=False)

    class _Episodes:
        total_recorded = 0

        def total_reward(self):
            return 0.0

        def recent(self, n):
            return []

    class _DeadAgent:
        body = _DeadBody()
        persona = Persona(name="Grimm")
        episodes = _Episodes()
        memory: dict = {}

        def tick(self):
            return None  # forge2's exact shape: alive, connected, doing nothing

    _run_worker(_DeadAgent(), 85, 0, {}, threading.Lock(), "miner")
    out = capsys.readouterr().out
    assert "NO PROGRESS" in out, "a frozen agent must say so"
    assert out.count("NO PROGRESS") >= 2, "and keep saying it, not just once"


def test_a_progressing_agent_never_trips_the_liveness_line(capsys):
    import threading

    from anima2.contract import Walk
    from anima2.village import _run_worker

    class _Body:
        connected = True

        def __init__(self):
            self.x = 50

        def observe(self):
            o = _obs(cursor=False, no_metal=False)
            o.player.pos = Position(self.x, 50, 0)
            return o

    class _Episodes:
        total_recorded = 0

        def total_reward(self):
            return 0.0

        def recent(self, n):
            return []

    class _WalkingAgent:
        persona = Persona(name="Bjorn")
        episodes = _Episodes()
        memory: dict = {}

        def __init__(self):
            self.body = _Body()

        def tick(self):
            self.body.x += 1  # position moves every tick — alive and working
            return Walk(dir=2)

    _run_worker(_WalkingAgent(), 85, 0, {}, threading.Lock(), "lumberjack")
    out = capsys.readouterr().out
    assert "NO PROGRESS" not in out


# --- reflection fallback visibility (follow-up #3) -----------------------------------
#
# The fallback used to be silent, and it cost a false claim: a persisted insight was
# presented as LLM-authored until forensics matched its text to the heuristic template.
# Degrading is fine; degrading invisibly is how overstatements get written.

def test_a_failed_reflection_says_it_fell_back(capsys):
    from anima2.cognition import LLMReflection
    from anima2.memory import Episode

    class _Down:
        def complete(self, system, user):
            raise TimeoutError("provider down")

    r = LLMReflection(_Down())
    eps = [Episode(tick=1, kind="work", summary="chop → success", reward=0.1)]
    out = r.reflect(eps, Persona(name="Bjorn"))
    assert r.fallback_count == 1
    assert "heuristic fallback" in capsys.readouterr().out
    assert any("paid off" in i for i in out)  # the heuristic template, now labelled


def test_a_healthy_reflection_never_mentions_fallback(capsys):
    from anima2.cognition import LLMReflection
    from anima2.memory import Episode

    class _Up:
        def complete(self, system, user):
            return '["The east grove yields faster in the morning."]'

    r = LLMReflection(_Up())
    out = r.reflect([Episode(tick=1, kind="work", summary="chop → success", reward=0.1)],
                    Persona(name="Bjorn"))
    assert r.fallback_count == 0
    assert "fallback" not in capsys.readouterr().out
    assert out and "east grove" in out[0]
