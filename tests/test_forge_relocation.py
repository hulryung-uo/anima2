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


# --- the WORK-liveness line (the guard the silent miner needed the SECOND time) ------
#
# `NO PROGRESS` above is BODY-liveness and it is defeated by an agent that keeps
# WALKING. Grimm died exactly that way on 2026-08-03: cumulative reward frozen at
# `out+176.9` from t=765 to the end of an 1800-tick run, no smelt and no deliver on
# any of the 126 remaining samples, while he went on relocating between mine faces —
# so `NO PROGRESS` fired ten times at exactly "40 ticks", three of them in the HEALTHY
# first half, and never escalated. Nothing in the status line flagged it. See
# `village._run_worker`'s `_STALL_TICKS` block for the measurement behind the number.

STALL_TICKS = 240  # `_QUIET_TICKS * 6`, measured — see `_run_worker`


class _CountingEpisodes:
    """An episode ledger whose `total_recorded` the test drives directly."""

    def __init__(self):
        self.total_recorded = 0

    def total_reward(self):
        return 0.0

    def recent(self, n):
        return []


class _EconStandIn:
    """The `econ_agent` half of a Life, as `village._work_recorded` reads it."""

    def __init__(self):
        self.episodes = _CountingEpisodes()


class _WalkerWithLedger:
    """Walks EVERY tick (so body-liveness can never fire) and records an episode only
    while `producing_until` has not passed — the shape of a miner that keeps
    relocating after its last skill ever finished.

    `econ_produce_every` gives it the SECOND ledger a Life has. That is not decoration:
    `Life.episodes` is the HUNT agent's alone, and a carpenter measured 0 there against
    176 in the economy agent over 3000 offline ticks, so a Life whose hunt ledger is
    frozen while it banks gold every few ticks is the NORMAL case and must stay silent.
    `skill` is `Agent.last_skill_name`, the alarm's arming condition.

    `dead_ticks` is the set of the worker's OWN tick numbers (1-based, matching
    `ticks_done`) on which this agent's observation reports a corpse, so a test can
    stage a death and a resurrection at known ticks and read the edges back."""

    def __init__(self, name, *, produce_every=9, producing_until=10**9, mode=None,
                 econ_produce_every=None, skill="mine", dead_ticks=(), hits=80):
        from anima2.contract import Walk

        self._walk = Walk
        self.persona = Persona(name=name)
        self.episodes = _CountingEpisodes()
        self.memory: dict = {}
        self.produce_every = produce_every
        self.producing_until = producing_until
        self.econ_produce_every = econ_produce_every
        self.last_skill_name = skill
        self.n = 0
        self.dead_ticks = frozenset(dead_ticks)
        if mode is not None:
            self.mode = mode
        if econ_produce_every is not None:
            self.econ_agent = _EconStandIn()

        owner = self

        class _Body:
            connected = True
            x = 50

            def observe(self):
                o = _obs(cursor=False, no_metal=False)
                o.player.pos = Position(self.x, 50, 0)
                o.player.hits, o.player.hits_max = hits, hits
                o.player.dead = owner.n in owner.dead_ticks
                return o

        self.body = _Body()

    def tick(self):
        self.n += 1
        self.body.x += 1  # position moves every tick — body-liveness stays reset
        if self.n <= self.producing_until and self.n % self.produce_every == 0:
            self.episodes.total_recorded += 1
        if (self.econ_produce_every is not None
                and self.n <= self.producing_until
                and self.n % self.econ_produce_every == 0):
            self.econ_agent.episodes.total_recorded += 1
        return self._walk(dir=2)


def test_a_walking_agent_that_stops_producing_no_longer_dies_silently(capsys):
    """Grimm's exact shape. The old alarm cannot see this: reward, steps and position
    all keep moving (he relocates), only the WORK stops."""
    import threading

    from anima2.village import _run_worker

    agent = _WalkerWithLedger("Grimm", producing_until=100)
    _run_worker(agent, 100 + STALL_TICKS * 2 + 5, 0, {}, threading.Lock(), "miner")
    out = capsys.readouterr().out
    assert "NO OUTPUT" in out, "an agent that stopped producing must say so"
    # ...and the count ESCALATES, unlike the ten identical "40 ticks" lines the
    # body-liveness alarm printed across the healthy AND the dead half alike.
    assert f"NO OUTPUT for {STALL_TICKS} ticks" in out
    assert f"NO OUTPUT for {STALL_TICKS * 2} ticks" in out
    # The old alarm is untouched and still silent here — it is a different failure.
    assert "NO PROGRESS" not in out


def test_a_healthy_long_single_skill_stretch_never_trips_the_work_liveness_line(capsys):
    """THE anti-requirement, and the whole risk of the feature.

    159 ticks is Grimm's longest measured HEALTHY reward-silence stretch across both
    2026-08-03 forge logs (600-tick run, t=414→573): `ph=mine`, steps frozen at 67,
    two full relocations with all-stuck windows, then live rock and recovery. For its
    whole length it is indistinguishable from the death above, which is why the
    threshold cannot be tightened below it. A miner that finishes a skill only every
    159 ticks for 1000 ticks is healthy and must stay silent."""
    import threading

    from anima2.village import _run_worker

    agent = _WalkerWithLedger("Grimm", produce_every=159)
    status: dict = {}
    _run_worker(agent, 1000, 0, status, threading.Lock(), "miner")
    out = capsys.readouterr().out
    assert agent.episodes.total_recorded >= 6, "the stretch must actually be productive"
    assert "NO OUTPUT" not in out, f"a healthy 159-tick mining stretch tripped it: {out}"
    assert "!stalled" not in status[0], status[0]
    assert "STALLED" not in status[0], status[0]


def test_a_life_is_judged_by_BOTH_its_ledgers_not_the_hunt_one_alone(capsys):
    """A Life's `episodes` is its HUNT agent's ledger, and the tinker Pim spent 180 of
    208 samples in economy mode — judged on that ledger alone he would have fired 7
    times in the 1800-tick run and 2 in the 600-tick run while being the most productive
    agent present (`out+0.3` frozen for 1789 ticks while banking 503g over six
    deposits). The first draft of this alarm answered that by excluding every Life
    (`mode is None`), which left `run_supply_pair` and `run_warrior_village` with ZERO
    work-liveness coverage. The sum of both ledgers is the answer instead: silent for
    Pim, and still loud for a Life that has genuinely stopped."""
    import threading

    from anima2.village import _run_worker

    # Pim: hunt ledger dead flat for the whole window, economy ledger working. The
    # huge `produce_every` is how the hunt ledger is starved while the econ one is fed.
    pim = _WalkerWithLedger("Pim", produce_every=10**9, mode="economy",
                            econ_produce_every=9, skill="capability_bound")
    status: dict = {}
    _run_worker(pim, STALL_TICKS * 3, 0, status, threading.Lock(), "tinker")
    out = capsys.readouterr().out
    assert pim.episodes.total_recorded == 0, "the hunt ledger really is frozen"
    assert pim.econ_agent.episodes.total_recorded > 0, "the economy ledger really moved"
    assert "NO OUTPUT" not in out, out
    assert "!stalled" not in status[0] and "STALLED" not in status[0], status[0]

    # ...and the coverage the `mode` gate used to throw away: a Life whose work has
    # stopped on BOTH sides is Grimm's failure on `run_supply_pair`, and it now speaks.
    dead = _WalkerWithLedger("Bjorn", producing_until=100, mode="economy",
                             econ_produce_every=9, skill="capability_bound")
    status = {}
    _run_worker(dead, 100 + STALL_TICKS + 5, 0, status, threading.Lock(), "woodsman")
    out = capsys.readouterr().out
    assert f"NO OUTPUT for {STALL_TICKS} ticks" in out, out
    assert "!stalled" in status[0], status[0]


def test_the_status_line_carries_the_episode_count_for_every_agent():
    """The alarm scrolls between status blocks; `status[idx]` is reprinted every ~4s and
    is what an operator actually reads. It is also the only way the NEXT run measures the
    real `total_recorded` distribution — no log today records it."""
    import threading

    from anima2.village import _run_worker

    healthy = _WalkerWithLedger("Bjorn", produce_every=5)
    status: dict = {}
    _run_worker(healthy, 50, 0, status, threading.Lock(), "lumberjack")
    assert f"eps={healthy.episodes.total_recorded}" in status[0], status[0]
    assert healthy.episodes.total_recorded == 10

    # A Life carries it too, and it carries the SUM: a hunt-only reading would print
    # `eps=0` on a carpenter that had retired 176 capability frames.
    life = _WalkerWithLedger("Pim", produce_every=5, mode="economy",
                             econ_produce_every=10)
    _run_worker(life, 50, 0, status, threading.Lock(), "tinker")
    assert life.episodes.total_recorded == 10
    assert life.econ_agent.episodes.total_recorded == 5
    assert "eps=15" in status[0], status[0]


def test_an_agent_wandering_by_design_is_never_called_stalled(capsys):
    """The alarm's arming condition, and without it the line is 100% WRONG on the
    DEFAULT roster. `townsfolk` is defined `work_skill=None  # no job — just lives in
    town (wander + greet)` and ships at `--townsfolk 1`; `Greet` records once per new
    serial and `Wander` records nothing, ever. Measured through this same `_run_worker`
    (2026-08-03): 1000 ticks, five neighbours, five greets in the first five ticks, then
    `NO OUTPUT` at 240/480/720/960 and a terminal `[BUDGET SPENT · STALLED 995]` for an
    agent behaving exactly as specified. An idle hunter (`Hunt.can_run` false with no
    hostile in range, so the planner falls to the same `Wander`) is the same shape."""
    import threading

    from anima2.village import _run_worker

    for idle in ("wander", "capability_wait"):
        agent = _WalkerWithLedger("Sera", producing_until=5, produce_every=1, skill=idle)
        status: dict = {}
        _run_worker(agent, STALL_TICKS * 4 + 5, 0, status, threading.Lock(), "townsfolk")
        out = capsys.readouterr().out
        assert agent.episodes.total_recorded == 5, "the greets really did stop"
        assert "NO OUTPUT" not in out, f"{idle}: {out}"
        assert "!stalled" not in status[0] and "STALLED" not in status[0], status[0]


def test_the_real_townsfolk_planner_is_silent_and_the_real_miner_planner_is_not():
    """The arming condition against the PRODUCTION skill names, not the stand-in's.

    A stand-in that hard-codes "wander" proves the gate, not that anything selects it.
    `Wander.name` and `CapabilityWait.name` are what `_doing_work` matches, and a
    profession planner's last skill is the always-runnable fallback."""
    from anima2.profession import PROFESSIONS, CapabilityWait
    from anima2.skills.movement import Wander
    from anima2.village import _IDLE_SKILLS, _doing_work

    assert Wander.name in _IDLE_SKILLS
    assert CapabilityWait.name in _IDLE_SKILLS
    assert isinstance(PROFESSIONS["townsfolk"].planner().skills[-1], Wander)
    assert PROFESSIONS["miner"].work_skill().name not in _IDLE_SKILLS

    class _Stub:
        last_skill_name = Wander.name

    assert not _doing_work(_Stub())
    _Stub.last_skill_name = PROFESSIONS["miner"].work_skill().name
    assert _doing_work(_Stub())
    # An object that cannot answer keeps the alarm it would have had — the gate removes
    # KNOWN-idle false alarms, it does not demand proof of work before speaking.
    assert _doing_work(object())


def test_a_stalled_worker_says_so_on_its_line_and_in_its_terminal_suffix():
    """Grimm's actual last line was
    `Grimm  miner  @(2593,499) t=1800 out+176.9 steps=139 says=0  [BUDGET SPENT]` —
    the most misleading possible summary of an agent that had produced nothing for its
    last 1035 ticks, and the first line any post-hoc reader looks at."""
    import threading

    from anima2.village import _run_worker

    agent = _WalkerWithLedger("Grimm", producing_until=10)
    status: dict = {}
    _run_worker(agent, 10 + STALL_TICKS + 5, 0, status, threading.Lock(), "miner")
    assert "!stalled" in status[0], status[0]
    assert "[BUDGET SPENT · STALLED" in status[0], status[0]


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


# --- DEATH: the reading the tape still could not take (follow-up 18) -----------------
#
# `docs/AUDIT-2026-07-29.md` §8.1, on what the work-liveness line above bought: "the
# tape now says the miner STOPPED and still cannot say whether he DIED". A death, a
# lost pickaxe and a dead vein all read as `out+176.9 eps=45` frozen forever, and the
# audit named this gap on 2026-07-30, again as follow-up 17, and again as follow-up 18
# — three times, unacted each time.
#
# Two readings, and each covers the other's blind spot: `hp=` is a LEVEL that says
# whether the agent is a corpse RIGHT NOW, and `deaths=` is an EDGE count read per
# TICK, so a death resolved between two ~4s samples still lands. That second shape is
# not hypothetical: §8.1 names "an agent whose work is dead but which cycles
# death/resurrection once per 240 ticks" as exactly what the work-liveness proxy
# misses, because `RecoverDeath` returns terminal statuses that keep `eps=` moving.


def test_a_death_is_reported_once_as_an_edge_not_once_per_ghost_tick(capsys):
    import threading

    from anima2.village import _run_worker

    # Dead for 30 straight ticks — one event, not thirty.
    agent = _WalkerWithLedger("Grimm", dead_ticks=range(20, 50))
    status: dict = {}
    _run_worker(agent, 80, 0, status, threading.Lock(), "miner")
    out = capsys.readouterr().out
    assert out.count("DIED") == 1, f"a 30-tick corpse is ONE death: {out}"
    assert "death #1" in out, out
    assert out.count("BACK ALIVE") == 1, out
    # The ghost stretch is reported, because a death recovered from in 30 ticks and one
    # the agent never comes back from are the same number in a death COUNT.
    assert "after 30 ticks dead" in out, out


def test_hp_and_deaths_ride_the_status_line_for_every_agent():
    import threading

    from anima2.village import _run_worker

    # While dead: the level signal says so, on the line an operator actually reads.
    agent = _WalkerWithLedger("Grimm", dead_ticks=range(20, 200))
    status: dict = {}
    _run_worker(agent, 40, 0, status, threading.Lock(), "miner")
    assert "hp=DEAD" in status[0], status[0]
    assert "deaths=1" in status[0], status[0]

    # ...and after the recovery the level signal has decayed to healthy, which is why
    # the edge count has to be there beside it: `hp=80/80 deaths=2` is a diagnosis and
    # `hp=80/80` alone is the same string a miner who never died prints.
    twice = _WalkerWithLedger("Grimm", dead_ticks=list(range(20, 30)) + list(range(50, 60)))
    status = {}
    _run_worker(twice, 80, 0, status, threading.Lock(), "miner")
    assert "hp=80/80" in status[0], status[0]
    assert "deaths=2" in status[0], status[0]


def test_an_agent_that_never_dies_says_so_rather_than_saying_nothing(capsys):
    """`deaths=` prints at 0 on purpose. An ABSENT field is ambiguous — no deaths, or a
    build that could not count them — and the whole point of the pair is to make a
    frozen `eps=` beside `deaths=0` (a lost tool, a dead vein) a different diagnosis
    from the same frozen `eps=` beside `deaths=3`."""
    import threading

    from anima2.village import _run_worker

    agent = _WalkerWithLedger("Bjorn", produce_every=5)
    status: dict = {}
    _run_worker(agent, 50, 0, status, threading.Lock(), "lumberjack")
    assert "deaths=0" in status[0], status[0]
    assert "hp=80/80" in status[0], status[0]
    out = capsys.readouterr().out
    assert "DIED" not in out and "BACK ALIVE" not in out, out


def test_a_corpse_on_the_first_observation_is_counted_but_named_apart(capsys):
    """A run that opens on a corpse must not read as a run with no deaths — and must
    not claim this worker watched it happen, because it did not."""
    import threading

    from anima2.village import _run_worker

    agent = _WalkerWithLedger("Grimm", dead_ticks=range(0, 10))
    status: dict = {}
    _run_worker(agent, 30, 0, status, threading.Lock(), "miner")
    out = capsys.readouterr().out
    assert "DEAD at first observation" in out, out
    assert "before this worker's first tick" in out, out
    assert "DIED at" not in out, "it was not watched, so it must not claim to be"
    assert "deaths=1" in status[0], status[0]


def test_a_LIFE_counts_one_death_once_though_it_owns_two_death_markers(capsys):
    """The mutant this kills is the obvious implementation: read the `death_episode`
    marker `Agent.tick` already maintains instead of counting the edge in the worker.

    A Life owns TWO Agents, ticks exactly one per orchestrator tick, and each keeps its
    own `death_observed_dead` flag — so one death observed first by the economy agent
    and then by the hunt agent (which the death override guarantees) increments BOTH.
    Measured on this exact fixture: `hunt + econ` reports 2 for the ONE death staged
    here. The sibling assertion below stages the mirror case, where `max(hunt, econ)`
    reports 1 for TWO deaths. Neither reduction is a death counter; the worker's own
    edge count is."""
    import threading

    from anima2.carpenter_life import CarpenterLife
    from anima2.contract import PlayerView
    from anima2.mock_body import MockBody
    from anima2.village import _run_worker

    def _life(name):
        body = MockBody(player=PlayerView(serial=PLAYER, name=name, pos=Position(5, 5, 0),
                                          hits=80, hits_max=80, body=0x190))
        body.items[BP] = ItemView(serial=BP, graphic=0x0E75, amount=1, pos=Position(),
                                  container=PLAYER, layer=BACKPACK_LAYER, distance=0)
        return body, CarpenterLife(body=body, persona=Persona(name=name), routes={})

    # (a) ONE death, seen by the economy agent first — the sum double-counts.
    body, life = _life("Sten")

    class _Driver:
        """Ticks the Life, flipping the corpse flag on a schedule, exactly as the worker
        would see it. `_run_worker` reads `agent.body.observe()`, which the Life's
        `_CachingBody` serves from THIS tick's cache — so the worker and the Life see
        the same observation, which is what makes the edge count trustworthy."""

        def __init__(self, life, body, dead_at, alive_at, econ_at=()):
            self.life, self.body_, self.n = life, body, 0
            self.dead_at, self.alive_at, self.econ_at = dead_at, alive_at, set(econ_at)

        def __getattr__(self, k):
            return getattr(self.life, k)

        def tick(self):
            self.n += 1
            if self.n == self.dead_at:
                self.body_.player.dead = True
            if self.n == self.alive_at:
                self.body_.player.dead = False
            if self.n in self.econ_at:
                self.life.mode = "economy"
            return self.life.tick()

    driver = _Driver(life, body, dead_at=20, alive_at=60, econ_at=(20,))
    status: dict = {}
    _run_worker(driver, 100, 0, status, threading.Lock(), "carpenter")
    out = capsys.readouterr().out
    hunt = int(life.hunt_agent.memory.get("death_episode", 0))
    econ = int(life.econ_agent.memory.get("death_episode", 0))
    assert (hunt, econ) == (1, 1), (hunt, econ)
    assert hunt + econ == 2, "the sum really does double-count one death"
    assert out.count("DIED") == 1, out
    assert "deaths=1" in status[0], status[0]

    # (b) TWO deaths inside ONE worker's run, one seen by each agent — the max
    # under-counts. Both resurrections land the tick after the death, so the agent that
    # did NOT hold that tick never observes the corpse and never increments.
    body, life = _life("Sten")

    class _TwoDeaths(_Driver):
        def tick(self):
            self.n += 1
            body.player.dead = self.n in (20, 50)
            if self.n == 20:
                self.life.mode = "economy"   # death #1 lands on the economy agent
            return self.life.tick()

    driver = _TwoDeaths(life, body, dead_at=0, alive_at=0)
    status = {}
    _run_worker(driver, 90, 0, status, threading.Lock(), "carpenter")
    out = capsys.readouterr().out
    hunt = int(life.hunt_agent.memory.get("death_episode", 0))
    econ = int(life.econ_agent.memory.get("death_episode", 0))
    assert (hunt, econ) == (1, 1), (hunt, econ)
    assert max(hunt, econ) == 1, "the max really does under-count two deaths"
    assert out.count("DIED") == 2, out
    assert "deaths=2" in status[0], status[0]


# --- follow-up 27: the pool the miner actually mines out --------------------------------
#
# Three of the last four live forge days were decided by the MINER, not by anything in the
# tinker's economy. Audit §24.3 measured why: `find_mine_spots` uses `spacing=8` so each
# stand is one 8x8 `HarvestBank` (10-34 ore), the pool was capped at SIX, and on two
# consecutive 1800-tick days the miner stood on all six and then cycled dead tiles at
# `win=23/23` from about sample 115 of 209 to the end.


def test_the_mine_pool_is_not_smaller_than_the_survey_it_draws_from():
    """The cap threw away half the rock the survey had already found — 6 of 12 — and the
    six were measured running out twice. A cap BELOW what the survey returns is free
    starvation, and this is the assertion that keeps it from drifting back."""
    from anima2.uomap import find_mine_spots
    from anima2.village import LUMBER_MAP, MINE_POOL_SPOTS, TRADE_MINE_SPOT

    found = find_mine_spots(LUMBER_MAP, *TRADE_MINE_SPOT)
    assert len(found) >= 12, (
        f"the trade mine's survey used to return 12 stands; it now returns {len(found)}, "
        f"so the pool size below needs re-deriving rather than assuming")
    assert MINE_POOL_SPOTS >= len(found) - 1, (
        f"the pool caps at {MINE_POOL_SPOTS} while the survey finds {len(found)} stands "
        f"(one of which is HOME and excluded) — that discards rock the miner has already "
        f"paid a survey for, which is what starved two live days")


def test_every_pooled_stand_is_a_DISTINCT_harvest_bank():
    """The pool's value is banks, not tiles. `find_mine_spots(spacing=8)` exists because
    two stands inside one 8x8 bank share the same 10-34 ore: relocating between them is
    'a walk to the same empty pool', in that function's own words. A larger cap only buys
    more ore if the extra stands are genuinely different banks."""
    from anima2.village import LUMBER_MAP, MINE_POOL_SPOTS, TRADE_MINE_SPOT
    from anima2.uomap import find_mine_spots

    pool = [s for s, _ in find_mine_spots(LUMBER_MAP, *TRADE_MINE_SPOT)][:MINE_POOL_SPOTS + 1]
    banks = {(x // 8, y // 8) for x, y in pool}
    assert len(banks) == len(pool), (
        f"{len(pool)} stands share only {len(banks)} harvest banks — the extra stands "
        f"are the same ore twice: {sorted(pool)}")


def test_the_pool_stays_within_a_days_walk_of_home():
    """The trade-off the bigger pool accepts, pinned so it stays bounded. Relocation is
    nearest-first, so a far stand is only ever reached once the near ones are dead — the
    comparison is a long walk versus exhausted rock, not versus a short walk. But it is
    still a walk, and a pool that reached across the map would spend the day travelling."""
    from anima2.village import LUMBER_MAP, MINE_POOL_SPOTS, TRADE_MINE_SPOT
    from anima2.uomap import find_mine_spots

    mx, my = TRADE_MINE_SPOT
    pool = [s for s, _ in find_mine_spots(LUMBER_MAP, mx, my)][:MINE_POOL_SPOTS + 1]
    worst = max(max(abs(x - mx), abs(y - my)) for x, y in pool)
    assert worst <= 45, f"the furthest pooled stand is {worst} tiles from home"

    # NOT monotone in straight-line distance, and that is correct rather than a defect:
    # `find_mine_spots` orders by BFS depth over walkable non-mine land, so a stand that
    # is a shorter WALK can be further as the crow flies when the face bends around. The
    # walk is the thing a relocation hop pays, so the walk is the right metric — measured
    # here as 0,8,16,13,24,22,... which a straight-line ordering check would call broken.
    dists = [max(abs(x - mx), abs(y - my)) for x, y in pool]
    assert dists != sorted(dists), (
        "straight-line distances are monotone, so the survey may have switched from BFS "
        "ordering to a naive one — which would put far-flank stands in the pool, the "
        "forge12 failure this file already records")


def test_the_walk_and_swing_counters_split_the_ticks_between_them():
    """Follow-up 28's instrument. §27 established that ore never respawns inside a forge
    day, so a miner's output is `banks reached x 10-34` and the only lever is reaching
    more banks — which makes "does a dead stand cost us the walk or the proving?" the
    question that picks the fix.

    The existing telemetry cannot answer it, and the failed attempt is why this exists:
    the worker's `steps=` reads ZERO across both dead tails, which looks like "no walking
    at all" and is an artefact — it counts `Walk` actions, and a relocation issues one
    fire-and-forget `WalkTo` and then IDLES while the server advances the route."""
    memory = {"smithy_drop": (60, 50)}
    skill = MineSmeltDeliver()
    window = max(1, len(Mine.probe_offsets)) * Mine.stuck_window_rotations
    ticks = 0
    for _ in range(window + 40):
        for cursor, no_metal in ((True, False), (False, True)):
            skill.step(SkillContext(obs=_obs(cursor=cursor, no_metal=no_metal),
                                    persona=Persona(name="Grimm"), memory=memory))
            ticks += 1

    walk = memory.get("harvest_walk_ticks", 0)
    swing = memory.get("harvest_swing_ticks", 0)
    assert swing > 0, "the swing counter never moved"
    assert walk + swing == ticks, (
        f"every harvest tick must land in exactly one bucket: {walk}+{swing} != {ticks}")
    # The vein is dead, so this drove a relocation — and its walk ticks are counted even
    # though the agent emits no `Walk` at all while the route advances.
    assert walk > 0, (
        "a relocation must register as WALKING; if this is 0 the counter is measuring "
        "the same nothing `steps=` measured")


def test_the_counters_are_cumulative_and_never_double_count_an_arrival():
    """An arrival tick falls through the relocation branch into the harvest machine, and
    is counted as a SWING — right, because it does resume harvesting, and a one-tick
    attribution per relocation either way. What must never happen is a tick landing in
    both, which would make the ratio the whole instrument exists for meaningless."""
    memory = {"smithy_drop": (60, 50)}
    skill = MineSmeltDeliver()
    seen = []
    for _ in range(120):
        before = (memory.get("harvest_walk_ticks", 0), memory.get("harvest_swing_ticks", 0))
        skill.step(SkillContext(obs=_obs(cursor=False, no_metal=True),
                                persona=Persona(name="Grimm"), memory=memory))
        after = (memory.get("harvest_walk_ticks", 0), memory.get("harvest_swing_ticks", 0))
        seen.append((after[0] - before[0], after[1] - before[1]))
    assert all(d in ((1, 0), (0, 1)) for d in seen), (
        f"a tick landed in both buckets or neither: {set(seen)}")
