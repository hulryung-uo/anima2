import json

import pytest

from anima2.life_config import LifeProfile, life_class
from anima2.village import _merge_profiles, _route_knobs


def test_duplicate_cli_knobs_are_not_silently_overwritten():
    from anima2.village import _parse_knobs

    with pytest.raises(SystemExit, match="twice"):
        _parse_knobs(["bank_reserve=129", "bank_reserve=400"])


def test_profile_round_trip_is_immutable_and_uses_the_lifes_allowlist(tmp_path):
    values = {"bank_trip_surplus": 10, "bank_reserve": 93}
    profile = LifeProfile("tinker", values)
    values["bank_reserve"] = 0
    path = tmp_path / "profiles" / "tinker.json"
    profile.save(path)
    loaded = LifeProfile.load(path)
    assert loaded == profile
    assert loaded.knobs["bank_reserve"] == 93
    assert set(loaded.knobs) <= life_class(loaded.role).KNOBS
    with pytest.raises(TypeError):
        loaded.knobs["bank_reserve"] = 0
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("role,knobs", [
    ("miner", {}), ("mage", {"profession": 1}), ("mage", {"steering": 1}),
    ("mage", {"bank_trip_surplus": 10}), ("tinker", {"bank_reserve": True}),
    ("tinker", {"bank_reserve": -1}), ("tinker", {"bank_reserve": "50"}),
    ("tinker", {"bank_reserve": 2.5}),
])
def test_invalid_profiles_fail_before_a_life_is_constructed(role, knobs):
    with pytest.raises(ValueError):
        LifeProfile(role, knobs)


@pytest.mark.parametrize("raw", [
    '{"schema":1,"schema":1,"role":"mage","knobs":{}}',
    '{"schema":true,"role":"mage","knobs":{}}',
    '{"schema":1,"role":"mage","knobs":{"bank_reserve":1,"bank_reserve":2}}',
    '{"schema":1,"role":"mage","knobs":{},"extra":1}',
])
def test_profile_json_is_a_closed_unambiguous_schema(tmp_path, raw):
    path = tmp_path / "profile.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        LifeProfile.load(path)


def test_profiles_route_to_each_life_without_overriding_cli_values(tmp_path):
    path = tmp_path / "profile.json"
    LifeProfile("woodsman", {"bank_reserve": 90}).save(path)
    merged = _merge_profiles([path], {"carpenter:bank_reserve": 129},
                             ("woodsman", "carpenter"))
    assert _route_knobs(merged, ("woodsman", "carpenter"), runner="pair", default_role=None) == {
        "woodsman": {"bank_reserve": 90}, "carpenter": {"bank_reserve": 129}}
    with pytest.raises(SystemExit, match="no 'woodsman'"):
        _merge_profiles([path], {}, ("mage",))
    with pytest.raises(SystemExit, match="twice"):
        _merge_profiles([path], {"woodsman:bank_reserve": 91}, ("woodsman",))
    with pytest.raises(SystemExit, match="twice"):
        _route_knobs(_merge_profiles([path], {"bank_reserve": 91}, ("woodsman",)),
                     ("woodsman",), runner="woodsman", default_role="woodsman")


@pytest.mark.parametrize("flags,role,runner", [
    (["--forge-pair"], "tinker", "run_forge_pair"),
    (["--woodsman"], "woodsman", "run_woodsman_life"),
    (["--carpenter"], "carpenter", "run_carpenter_life"),
    (["--warriors", "1"], "swordsman", "run_warrior_village"),
    (["--pipeline"], "mage", "run_artisan_mage_village"),
    (["--supply-pair"], "woodsman", "run_supply_pair"),
])
def test_village_cli_applies_profiles_to_the_selected_runner(tmp_path, monkeypatch,
                                                            flags, role, runner):
    import anima2.village as village

    path = tmp_path / "profile.json"
    LifeProfile(role, {"bank_reserve": 400}).save(path)
    calls = []
    monkeypatch.setattr(village, runner, lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr("sys.argv", ["village", *flags, "--profile", str(path)])
    village.main()
    name = {"run_artisan_mage_village": "mage_knobs", "run_supply_pair": "woodsman_knobs"}
    assert calls[0][name.get(runner, "knobs")] == {"bank_reserve": 400}


def test_an_invalid_cli_profile_cannot_connect(tmp_path, monkeypatch):
    import anima2.village as village

    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"schema": 1, "role": "tinker", "knobs": {"profession": 3}}))
    monkeypatch.setattr(village, "run_forge_pair", lambda **kw: pytest.fail("connected"))
    monkeypatch.setattr("sys.argv", ["village", "--forge-pair", "--profile", str(path)])
    with pytest.raises(SystemExit, match="not a Life knob"):
        village.main()
