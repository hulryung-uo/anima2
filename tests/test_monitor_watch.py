"""Smoke for the non-Chrome monitor watcher."""

from __future__ import annotations

from anima2.monitor_watch import _one_line


def test_one_line_formats_a_live_scene_and_marks_a_down_port():
    assert _one_line(8801, None) == "8801: down"
    scene = {
        "player": {
            "name": "Pim",
            "x": 2609,
            "y": 474,
            "gold": 82,
            "hits": 80,
            "hitsMax": 80,
        },
        "journal": [{"text": "craft tongs"}],
    }
    line = _one_line(8802, scene)
    assert line.startswith("8802:Pim @(2609, 474)")
    assert "gold=82" in line
    assert "craft tongs" in line
