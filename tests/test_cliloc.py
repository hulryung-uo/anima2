"""Cliloc resolution: placeholder substitution + JournalEntry passthrough."""

import pytest

from anima2 import cliloc
from anima2.contract import JournalEntry

CLILOC_FILE = cliloc.DEFAULT_CLILOC


def test_resolve_unknown_falls_back():
    # Force the missing-table path with a bogus path.
    out = cliloc.resolve(503044, "x", path="/nonexistent/Cliloc.enu")
    assert out.startswith("[cliloc 503044]")


def test_resolve_entry_passthrough_for_plain_speech():
    e = JournalEntry(serial=1, name="Bob", text="hail", msg_type=0, hue=0, cliloc=0)
    assert cliloc.resolve_entry(e) == "hail"


@pytest.mark.skipif(not CLILOC_FILE.exists(), reason="UO Cliloc.enu not present")
def test_known_mining_message_resolves_to_english():
    # 503044 = "You dig some ore and put it in your backpack."
    assert "dig some ore" in cliloc.resolve(503044).lower()


@pytest.mark.skipif(not CLILOC_FILE.exists(), reason="UO Cliloc.enu not present")
def test_placeholder_substitution_fills_args_without_leaks():
    # Find any cliloc with two placeholders and confirm args substitute cleanly.
    table = cliloc._table()
    target = next(
        (n for n, t in table.items() if "~1" in t and "~2" in t and len(t) < 40), None
    )
    assert target is not None
    text = cliloc.resolve(target, "ALPHA\tBETA")
    assert "ALPHA" in text and "BETA" in text and "~" not in text


@pytest.mark.skipif(not CLILOC_FILE.exists(), reason="UO Cliloc.enu not present")
def test_resolve_entry_resolves_cliloc():
    e = JournalEntry(serial=0, name="System", text="", msg_type=0, hue=0, cliloc=503044)
    assert "ore" in cliloc.resolve_entry(e).lower()
