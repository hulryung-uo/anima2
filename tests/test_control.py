"""`GmControl`'s offline-testable surface: `[Get` reply parsing (hardened past
PHASE4.md item 3's "returned empty" finding) and `stage()`'s proximity-aware
teleport ordering (PHASE5.md item 1's follow-up: `[Set X/Y/Z` silently no-ops
when the GM isn't near the character's *current* position — live-confirmed
against a real ServUO shard, see `control.py::stage`'s own docstring).

`get_property`/`get_property_value` end-to-end tests use a tiny scripted fake
`Body` (not `MockBody`, which has no target-cursor support) since `GmControl`
only needs `act`/`observe`. `stage()`'s tests monkeypatch `GmControl`'s own
`hide`/`go`/`get_property_value`/`command_on` methods directly and assert the
call sequence — `stage()` never touches `self.body` except through those,
so a real body isn't needed at all.
"""

from __future__ import annotations

from anima2.contract import JournalEntry, Observation, TargetCursor
from anima2.control import GmControl, parse_property_reply


class _ScriptedBody:
    """Replays a fixed sequence of `Observation`s, one per `observe()` call;
    records every `act()` call. Enough of the `Body` protocol for `GmControl`."""

    def __init__(self, observations: list[Observation]) -> None:
        self._obs = list(observations)
        self.actions: list = []

    def act(self, action) -> None:
        self.actions.append(action)

    def observe(self) -> Observation:
        return self._obs.pop(0) if self._obs else Observation()


# --- parse_property_reply: fixture lines copied verbatim from a real ServUO
# reply (see control.py::parse_property_reply's own docstring) --------------


def test_parse_numeric_with_hex_annotation():
    assert parse_property_reply("TotalGold = 1000 (0x3E8)", "TotalGold") == 1000.0
    assert parse_property_reply("Str = 60 (0x3C)", "Str") == 60.0


def test_parse_plain_float():
    assert parse_property_reply("Skills.Mining.Base = 42.5", "Skills.Mining.Base") == 42.5


def test_parse_quoted_string():
    assert parse_property_reply('Name = "Anima"', "Name") == "Anima"


def test_parse_compound_value_falls_back_to_raw_string():
    # ServUO doesn't quote a Point3D reply — no numeric/quoted pattern matches,
    # so the raw text (minus the "<prop> = " prefix) comes back as-is.
    assert parse_property_reply("Location = (3734, 2222, 20)", "Location") == "(3734, 2222, 20)"


def test_parse_error_reply_is_none():
    assert parse_property_reply("Property 'Gold' not found.", "Gold") is None


def test_parse_none_and_empty_are_none():
    assert parse_property_reply(None, "X") is None
    assert parse_property_reply("", "X") is None


def test_parse_picks_the_echoed_line_out_of_noisy_joined_text():
    # get_property's own " | "-joined noisy-journal fallback: an unrelated
    # line arrived before the actual reply.
    raw = "Someone yells for help! | TotalGold = 1000 (0x3E8)"
    assert parse_property_reply(raw, "TotalGold") == 1000.0


def test_parse_wrong_property_name_does_not_match_a_different_echo():
    # "Gold" is not a valid ServUO property (it's "TotalGold") -- a reply
    # echoing a *different* property name must not be mistaken for a match.
    assert parse_property_reply("TotalGold = 1000 (0x3E8)", "Gold") is None


# --- get_property / get_property_value end to end ---------------------------


def test_get_property_collects_across_noisy_pumps_and_stops_on_echo():
    cursor = TargetCursor(target_type=1, cursor_id=1, cursor_flag=0)
    noise = JournalEntry(0, "System", "Someone yells for help!", 0, 0)
    reply = JournalEntry(0, "System", "TotalGold = 1000 (0x3E8)", 0, 0)
    body = _ScriptedBody([
        Observation(pending_target=cursor),  # _await_cursor sees the cursor open
        Observation(new_journal=[noise]),  # pump 1: unrelated noise (live_hunt.py's own confound)
        Observation(new_journal=[reply]),  # pump 2: the actual reply — stop here
    ])
    gm = GmControl(body)
    raw = gm.get_property("TotalGold", 0x1234)
    assert raw == "Someone yells for help! | TotalGold = 1000 (0x3E8)"


def test_get_property_value_returns_typed_number():
    cursor = TargetCursor(target_type=1, cursor_id=1, cursor_flag=0)
    reply = JournalEntry(0, "System", "Skills.Mining.Base = 42.5", 0, 0)
    body = _ScriptedBody([Observation(pending_target=cursor), Observation(new_journal=[reply])])
    gm = GmControl(body)
    assert gm.get_property_value("Skills.Mining.Base", 0x1234) == 42.5


def test_get_property_returns_none_when_cursor_never_opens():
    body = _ScriptedBody([Observation() for _ in range(8)])  # no pending_target ever
    gm = GmControl(body)
    assert gm.get_property("TotalGold", 0x1234) is None


def test_get_property_returns_none_when_nothing_arrives():
    cursor = TargetCursor(target_type=1, cursor_id=1, cursor_flag=0)
    body = _ScriptedBody([Observation(pending_target=cursor)] + [Observation()] * 6)
    gm = GmControl(body)
    assert gm.get_property("TotalGold", 0x1234) is None


# --- stage()'s proximity-aware teleport ordering -----------------------------


def _staging_gm(current_x: float | None, current_y: float | None) -> tuple[GmControl, list[tuple]]:
    """A `GmControl` with every method `stage()` touches monkeypatched to a
    call-recording stub — `stage()` never reaches `self.body` directly, so a
    real body isn't needed (see this module's own docstring)."""
    gm = GmControl(body=None)  # type: ignore[arg-type]
    calls: list[tuple] = []
    gm.hide = lambda: calls.append(("hide",))  # type: ignore[method-assign]
    gm.go = lambda x, y: (calls.append(("go", x, y)), (x, y, 20))[1]  # type: ignore[method-assign]
    values = {"X": current_x, "Y": current_y}
    gm.get_property_value = lambda prop, serial, **kw: (  # type: ignore[method-assign]
        calls.append(("get", prop)), values[prop]
    )[1]
    gm.command_on = lambda cmd, serial: (calls.append(("command_on", cmd)), True)[1]  # type: ignore[method-assign]
    return gm, calls


def test_stage_detours_via_characters_current_position_when_known():
    gm, calls = _staging_gm(current_x=3734.0, current_y=2222.0)
    result = gm.stage(0xABCD, 2611, 474, skills={"Mining": 35.0}, items=["Pickaxe"])
    assert result == (2611, 474, 20)
    assert calls == [
        ("hide",),
        ("go", 2611, 474),
        ("get", "X"),
        ("get", "Y"),
        ("go", 3734, 2222),  # detour to where the character actually is
        ("command_on", "[Set X 2611 Y 474 Z 20"),  # now succeeds — GM is in range
        ("go", 2611, 474),  # back to the work location (postcondition callers rely on)
        ("command_on", "[Set Skills.Mining.Base 35.0"),
        ("command_on", "[AddToPack Pickaxe"),
    ]


def test_stage_skips_detour_when_position_lookup_fails():
    """A `[Get X`/`[Get Y` failure (e.g. a noisy pump) degrades to the old,
    simpler behavior rather than blowing up — no detour, straight through."""
    gm, calls = _staging_gm(current_x=None, current_y=2222.0)
    gm.stage(0xABCD, 2611, 474, skills={}, items=[])
    assert calls == [
        ("hide",),
        ("go", 2611, 474),
        ("get", "X"),
        ("get", "Y"),
        ("command_on", "[Set X 2611 Y 474 Z 20"),
    ]
    assert ("go", 3734, 2222) not in calls
