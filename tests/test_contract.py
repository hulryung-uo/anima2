"""The Python contract must round-trip JSON faithfully (it mirrors anima-core)."""

from anima2.contract import (
    Observation,
    PickUp,
    Walk,
    action_from_dict,
)


def test_action_json_roundtrip():
    for action in [
        Walk(dir=3, run=True),
        PickUp(serial=0x4000_0001, amount=5),
    ]:
        again = action_from_dict(action.to_dict())
        assert again == action


def test_walk_json_shape():
    assert Walk(dir=2).to_dict() == {"type": "Walk", "dir": 2, "run": False}


def test_observation_from_dict():
    obs = Observation.from_dict(
        {
            "player": {"serial": 1, "name": "Anima", "pos": {"x": 10, "y": 20, "z": 0}, "hits": 50},
            "mobiles": [
                {"serial": 2, "name": "rat", "pos": {"x": 12, "y": 20}, "distance": 2}
            ],
            "items": [],
            "new_journal": [{"name": "System", "text": "hello"}],
        }
    )
    assert obs.player.name == "Anima"
    assert obs.player.pos.x == 10
    assert obs.mobiles[0].name == "rat"
    assert obs.new_journal[0].text == "hello"
