"""`foundry/trajectory.py` offline tests: `TrajectorySummary`'s derived
metrics, channel-(a) vs channel-(b) `alive_fraction` precedence,
`TrajectoryRecorder`'s `[Get`-driven channel (a) start/finish reads against a
scripted fake `GmControl` (mirrors `test_control.py`'s own scripted-fake
style), and `TappedBody`'s channel (b) pass-through + tap bookkeeping.
"""

from __future__ import annotations

import pytest

from anima2.contract import Attack, ItemView, JournalEntry, Observation, PlayerView, Position, Say, Use, Walk
from anima2.foundry.trajectory import SkillStat, TappedBody, TrajectoryRecorder, TrajectorySummary


# --- SkillStat / TrajectorySummary derived metrics ----------------------------


def test_skill_stat_gain_never_negative():
    assert SkillStat(id=45, name="Mining", first=40.0, last=35.0).gain == 0.0  # a GM rollback, not a gain
    assert SkillStat(id=45, name="Mining", first=35.0, last=42.5).gain == pytest.approx(7.5)


def test_trajectory_summary_duration_and_skill_gain_total():
    summ = TrajectorySummary(start_ts=1000.0, end_ts=1000.0 + 3600.0)
    summ.skills = {
        45: SkillStat(id=45, name="Mining", first=35.0, last=42.5),  # +7.5
        7: SkillStat(id=7, name="Blacksmithy", first=35.0, last=35.0),  # +0
    }
    assert summ.duration_h == pytest.approx(1.0)
    assert summ.skill_gain_total == pytest.approx(7.5)


def test_gold_delta_and_total_actions():
    summ = TrajectorySummary(gold_start=1000, gold_end=1250)
    summ.action_counts = {"use": 10, "move": 5}
    assert summ.gold_delta == 250
    assert summ.total_actions == 15


def test_unique_regions_buckets_by_8x8_tiles():
    summ = TrajectorySummary()
    summ.positions = [(0.0, 2560, 493), (1.0, 2565, 494), (2.0, 2700, 600)]
    # (2560,493) and (2565,494) both fall in the (320, 61) 8x8 bucket; (2700,600) doesn't.
    assert summ.unique_regions == 2


def test_profession_skill_gains_groups_by_category():
    summ = TrajectorySummary()
    summ.skills = {
        45: SkillStat(id=45, name="Mining", first=30.0, last=40.0),       # GATHERING +10
        7: SkillStat(id=7, name="Blacksmithy", first=30.0, last=33.0),    # CRAFTING +3
        21: SkillStat(id=21, name="Hiding", first=10.0, last=10.0),       # THIEF-STEALTH +0, excluded
    }
    gains = summ.profession_skill_gains()
    assert gains == {"GATHERING": pytest.approx(10.0), "CRAFTING": pytest.approx(3.0)}


# --- alive_fraction: channel (a) binary is load-bearing -----------------------


def test_alive_fraction_channel_a_dead_endpoint_overrides_channel_b():
    summ = TrajectorySummary(start_ts=0.0, end_ts=3600.0, alive_start=True, alive_end=False)
    summ.hp_samples = [(0.0, 100), (1800.0, 100), (3600.0, 100)]  # channel b alone would say "fully alive"
    assert summ.alive_fraction(channel_b=True) == 0.0
    assert summ.alive_fraction(channel_b=False) == 0.0


def test_alive_fraction_channel_b_refines_a_mid_window_death_channel_a_endpoints_miss():
    summ = TrajectorySummary(start_ts=0.0, end_ts=3600.0, alive_start=True, alive_end=True)
    summ.hp_samples = [(0.0, 100), (1800.0, 0), (3600.0, 100)]  # died and was resurrected mid-window
    frac_with_b = summ.alive_fraction(channel_b=True)
    assert 0.0 < frac_with_b < 1.0
    # channel-(a)-only can't see the dip — both endpoints alive -> coarse 1.0.
    assert summ.alive_fraction(channel_b=False) == 1.0


def test_alive_fraction_no_hp_samples_falls_back_to_coarse_binary():
    summ = TrajectorySummary(start_ts=0.0, end_ts=3600.0, alive_start=True, alive_end=True)
    assert summ.alive_fraction(channel_b=True) == 1.0


# --- TrajectoryRecorder: channel (a) [Get reads --------------------------------


class _FakeGm:
    """Replays scripted `get_property_value` replies, one per call, per
    property name — mirrors `test_control.py`'s `_ScriptedBody` style."""

    def __init__(self, replies: dict[str, list]) -> None:
        self._replies = {k: list(v) for k, v in replies.items()}

    def get_property_value(self, prop: str, serial: int, **kwargs):
        seq = self._replies.get(prop)
        if not seq:
            return None
        return seq.pop(0)


def test_recorder_start_finish_reads_skill_gold_alive_and_computes_gain():
    gm = _FakeGm({
        "Skills.Mining.Base": [35.0, 42.5],
        "TotalGold": [1000.0, 1200.0],
        "Hits": [80.0, 90.0],
    })
    rec = TrajectoryRecorder(gm, subject_serial=0xABCD, skill_names=("Mining",))
    rec.start()
    summ = rec.finish()

    assert summ.subject_serial == 0xABCD
    assert summ.skill_gain_total == pytest.approx(7.5)
    assert summ.skills[45].name == "Mining"
    assert summ.gold_start == 1000
    assert summ.gold_end == 1200
    assert summ.alive_start is True
    assert summ.alive_end is True
    assert summ.end_ts >= summ.start_ts


def test_recorder_finish_reads_zero_or_negative_hits_as_not_alive():
    gm = _FakeGm({
        "Skills.Mining.Base": [35.0, 35.0],
        "TotalGold": [0.0, 0.0],
        "Hits": [80.0, 0.0],
    })
    rec = TrajectoryRecorder(gm, subject_serial=1, skill_names=("Mining",))
    rec.start()
    summ = rec.finish()
    assert summ.alive_start is True
    assert summ.alive_end is False


def test_recorder_missing_gm_reply_defaults_to_alive_matching_v1_convention():
    gm = _FakeGm({})  # every get_property_value call returns None (no reply)
    rec = TrajectoryRecorder(gm, subject_serial=1, skill_names=("Mining",))
    rec.start()
    summ = rec.finish()
    assert summ.alive_start is True
    assert summ.alive_end is True
    assert summ.gold_start == 0
    assert summ.skills[45].first == 0.0


# --- TappedBody: channel (b) pass-through + tap bookkeeping --------------------


class _FakeInnerBody:
    """Replays a scripted sequence of Observations; records every act() call."""

    def __init__(self, observations: list[Observation]) -> None:
        self._obs = list(observations)
        self.acted: list = []

    @property
    def connected(self) -> bool:
        return True

    def observe(self) -> Observation:
        return self._obs.pop(0)

    def act(self, action) -> None:
        self.acted.append(action)


def _obs(x: int, y: int, hits: int = 100, items: list[ItemView] | None = None,
         journal: list[JournalEntry] | None = None) -> Observation:
    player = PlayerView(serial=0x1, name="Anima", pos=Position(x, y, 0), hits=hits)
    return Observation(player=player, items=items or [], new_journal=journal or [])


def test_tapped_body_forwards_observe_and_act_unchanged():
    """The measured agent's own behavior must be byte-for-byte identical
    whether its body is tapped or not — the recorder never sits in the
    decision path."""
    inner = _FakeInnerBody([_obs(10, 10)])
    rec = TrajectoryRecorder(_FakeGm({}), subject_serial=1)
    tapped = TappedBody(inner, rec)

    obs = tapped.observe()
    assert obs.player.pos == Position(10, 10, 0)
    assert tapped.connected is True

    action = Walk(dir=2)
    tapped.act(action)
    assert inner.acted == [action]


def test_tapped_body_infers_confirmed_and_denied_walk_steps():
    inner = _FakeInnerBody([
        _obs(10, 10),  # pre-walk
        _obs(11, 10),  # moved east — confirmed
        _obs(11, 10),  # pre second walk (no movement observed here)
        _obs(11, 10),  # didn't move — denied
    ])
    rec = TrajectoryRecorder(_FakeGm({}), subject_serial=1)
    tapped = TappedBody(inner, rec)

    tapped.observe()
    tapped.act(Walk(dir=2))  # east
    tapped.observe()  # moved -> confirmed

    tapped.observe()
    tapped.act(Walk(dir=2))
    tapped.observe()  # didn't move -> denied

    assert rec.summary.steps_confirmed == 1
    assert rec.summary.steps_denied == 1
    assert rec.summary.action_counts["move"] == 2


def test_tapped_body_credits_only_the_amount_delta_into_pack():
    """Mirrors v1's own pack-credit discipline: crediting every observed
    amount (not just growth) let a pickup/drop bounce mint produce score
    from one pile — see `trajectory.py`'s `tap_observation`."""
    bp = ItemView(serial=0x2, graphic=0x15, amount=0, pos=Position(0, 0, 0), container=0x1, layer=0x15, distance=0)
    ore1 = ItemView(serial=0x3, graphic=0x19B7, amount=5, pos=Position(0, 0, 0), container=0x2, layer=0, distance=0)
    ore2 = ItemView(serial=0x3, graphic=0x19B7, amount=8, pos=Position(0, 0, 0), container=0x2, layer=0, distance=0)
    ore3 = ItemView(serial=0x3, graphic=0x19B7, amount=8, pos=Position(0, 0, 0), container=0x2, layer=0, distance=0)  # bounce, no growth

    inner = _FakeInnerBody([_obs(0, 0, items=[bp, ore1]), _obs(0, 0, items=[bp, ore2]), _obs(0, 0, items=[bp, ore3])])
    rec = TrajectoryRecorder(_FakeGm({}), subject_serial=1)
    tapped = TappedBody(inner, rec)
    tapped.observe()
    tapped.observe()
    tapped.observe()

    entries = [(graphic, amount) for graphic, amount, _ts in rec.summary.items_into_pack]
    assert entries == [(0x19B7, 5), (0x19B7, 3)]


def test_tapped_body_counts_speech_sent_and_received():
    incoming = JournalEntry(serial=0x999, name="Other", text="hello", msg_type=0, hue=0)
    inner = _FakeInnerBody([_obs(0, 0, journal=[incoming])])
    rec = TrajectoryRecorder(_FakeGm({}), subject_serial=1)
    tapped = TappedBody(inner, rec)

    tapped.act(Say(text="hi"))  # tap_action needs a prior observe() for pre_obs, but Say has no pos dependency
    assert rec.summary.speech_sent == 0  # no _last_obs yet — tap_action is a no-op before the first observe()

    tapped.observe()
    assert rec.summary.speech_recv == 1


def test_action_group_classification_covers_move_use_speech_attack():
    inner = _FakeInnerBody([_obs(0, 0), _obs(0, 0), _obs(0, 0), _obs(0, 0)])
    rec = TrajectoryRecorder(_FakeGm({}), subject_serial=1)
    tapped = TappedBody(inner, rec)

    tapped.observe()
    tapped.act(Walk(dir=0))
    tapped.observe()
    tapped.act(Use(serial=0x5))
    tapped.observe()
    tapped.act(Say(text="hi"))
    tapped.observe()
    tapped.act(Attack(serial=0x6))

    assert rec.summary.action_counts == {"move": 1, "use": 1, "speech": 1, "attack": 1}


# --- fixture channel-data construction (spec's own test line item) -----------


def test_trajectory_summary_construction_from_fixture_channel_data():
    """End-to-end: build a TrajectorySummary from hand-built channel (a) +
    (b) fixture data (no recorder/body involved) and check the whole derived
    shape is internally consistent — the plain-fixture path
    `foundry/fitness.py`'s own tests build on."""
    summ = TrajectorySummary(
        subject_serial=0xBEEF, start_ts=5000.0, end_ts=5000.0 + 1800.0,
        gold_start=200, gold_end=260, alive_start=True, alive_end=True,
    )
    summ.skills = {45: SkillStat(id=45, name="Mining", first=35.0, last=39.0)}
    summ.items_into_pack = [(0x1BEF, 4, 5100.0)]
    summ.action_counts = {"use": 30, "move": 10}
    summ.steps_confirmed, summ.steps_denied = 38, 2
    summ.speech_sent, summ.speech_recv = 1, 2
    summ.positions = [(5000.0 + i, 2567 + i, 493) for i in range(5)]

    assert summ.duration_h == pytest.approx(0.5)
    assert summ.skill_gain_total == pytest.approx(4.0)
    assert summ.gold_delta == 60
    assert summ.total_actions == 40
    assert summ.alive_fraction() == 1.0
    assert summ.unique_regions >= 1
    assert summ.profession_skill_gains() == {"GATHERING": pytest.approx(4.0)}
