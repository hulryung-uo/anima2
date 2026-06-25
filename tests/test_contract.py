"""The Python contract must round-trip JSON faithfully (it mirrors anima-core)."""

from anima2.contract import (
    Observation,
    PickUp,
    TargetGround,
    TargetObject,
    Walk,
    action_from_dict,
)


def test_action_json_roundtrip():
    for action in [
        Walk(dir=3, run=True),
        PickUp(serial=0x4000_0001, amount=5),
        TargetObject(serial=0xAABBCCDD),
        TargetGround(x=1000, y=2000, z=-5, graphic=0x01A4),
    ]:
        again = action_from_dict(action.to_dict())
        assert again == action


def test_observation_skills_parsed():
    obs = Observation.from_dict(
        {
            "player": {"serial": 1},
            "skills": [{"id": 45, "value": 50.0, "base": 48.2, "cap": 100.0, "lock": 0}],
        }
    )
    assert len(obs.skills) == 1
    assert obs.skills[0].id == 45
    assert obs.skills[0].base == 48.2


def test_observation_pending_target_roundtrip():
    obs = Observation.from_dict(
        {
            "player": {"serial": 1},
            "pending_target": {"target_type": 1, "cursor_id": 43981, "cursor_flag": 0},
        }
    )
    assert obs.pending_target is not None
    assert obs.pending_target.cursor_id == 43981
    assert obs.pending_target.target_type == 1
    # Absent / null → None.
    assert Observation.from_dict({"player": {}}).pending_target is None


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
