"""The Python contract must round-trip JSON faithfully (it mirrors anima-core)."""

import pytest

from anima2.contract import (
    BuyItems,
    CastSpell,
    Drop,
    Equip,
    GumpView,
    Observation,
    PickUp,
    PlayerView,
    PopupRequest,
    PopupSelect,
    SellItems,
    TargetGround,
    TargetCancel,
    TargetObject,
    Walk,
    WalkTo,
    action_from_dict,
)


def test_action_json_roundtrip():
    for action in [
        Walk(dir=3, run=True),
        WalkTo(x=1200, y=800),
        PickUp(serial=0x4000_0001, amount=5),
        TargetObject(serial=0xAABBCCDD),
        TargetGround(x=1000, y=2000, z=-5, graphic=0x01A4),
        TargetCancel(),
        Equip(serial=0x4000_0002, layer=2),
        Drop(serial=0x4000_0003, x=10, y=20, z=0, container=0xFFFFFFFF),
        CastSpell(spell=5),
        BuyItems(vendor=0x111, items=[(0x222, 3), (0x333, 1)]),
        SellItems(vendor=0x111, items=[(0x444, 2)]),
        PopupRequest(serial=0x555),
        PopupSelect(serial=0x555, index=2),
    ]:
        again = action_from_dict(action.to_dict())
        assert again == action


def test_walk_to_json_shape_matches_json_rs():
    # Mirrors anima-net's `action_from_json`: `{"type": "WalkTo", "x": .., "y": ..}`,
    # no extra keys (no `run`/`z` — a route, not a single step).
    assert WalkTo(x=1200, y=800).to_dict() == {"type": "WalkTo", "x": 1200, "y": 800}


def test_walk_to_malformed_missing_coordinate_errors():
    # json.rs's `req_u16` errors (not a silent 0/map-origin default) on a
    # missing WalkTo coordinate; `action_from_dict` mirrors that with a
    # `KeyError` for either required key.
    with pytest.raises(KeyError):
        action_from_dict({"type": "WalkTo", "y": 800})
    with pytest.raises(KeyError):
        action_from_dict({"type": "WalkTo", "x": 1200})


def test_buy_items_json_shape_matches_json_rs():
    # Mirrors anima-net's `action_from_json` — `items` as a list of [serial,
    # amount] pairs (see `json.rs`'s `shop_items_from_json`, which also accepts
    # {"serial":..,"amount":..} but this is what `to_dict` emits).
    assert BuyItems(vendor=5, items=[(1, 2)]).to_dict() == {
        "type": "BuyItems", "vendor": 5, "items": [[1, 2]],
    }
    assert SellItems(vendor=5, items=[(1, 3)]).to_dict() == {
        "type": "SellItems", "vendor": 5, "items": [[1, 3]],
    }


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


def test_player_survival_state_roundtrip_and_backward_defaults():
    player = PlayerView.from_dict(
        {"serial": 1, "body": 0x192, "poisoned": True, "dead": True}
    )
    assert player.body == 0x192
    assert player.poisoned is True
    assert player.dead is True

    old_wire = PlayerView.from_dict({"serial": 2})
    assert old_wire.body == 0
    assert old_wire.poisoned is False
    assert old_wire.dead is False


def test_gump_structured_elements_are_preserved_for_safe_reply_selection():
    gump = GumpView.from_dict(
        {
            "serial": 10,
            "gump_id": 20,
            "layout": "{ button 65 227 4005 4007 1 0 1 }",
            "elements": [
                {"type": "html", "text": {"cliloc": {"id": 1011022}}},
                {"type": "button", "reply_id": 1, "pageflag": 1},
            ],
        }
    )
    assert gump.elements[0]["text"]["cliloc"]["id"] == 1011022
    assert gump.elements[1]["reply_id"] == 1


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


def test_observation_shop_buy_roundtrip():
    obs = Observation.from_dict(
        {
            "player": {"serial": 1},
            "shop_buy": {
                "vendor": 0xAAA, "container": 0xBBB,
                "entries": [{"price": 16, "name": "iron ingot"}, {"price": 14, "name": "tongs"}],
            },
        }
    )
    assert obs.shop_buy is not None
    assert obs.shop_buy.vendor == 0xAAA
    assert obs.shop_buy.container == 0xBBB
    assert len(obs.shop_buy.entries) == 2
    assert obs.shop_buy.entries[0].price == 16
    assert obs.shop_buy.entries[0].name == "iron ingot"
    # Absent / null → None, exactly like pending_target.
    assert Observation.from_dict({"player": {}}).shop_buy is None


def test_observation_shop_sell_roundtrip():
    obs = Observation.from_dict(
        {
            "player": {"serial": 1},
            "shop_sell": {
                "vendor": 0xCCC,
                "items": [
                    {"serial": 0x700, "graphic": 0x0F52, "hue": 0, "amount": 5, "price": 10, "name": "dagger"},
                ],
            },
        }
    )
    assert obs.shop_sell is not None
    assert obs.shop_sell.vendor == 0xCCC
    assert len(obs.shop_sell.items) == 1
    item = obs.shop_sell.items[0]
    assert (item.serial, item.graphic, item.amount, item.price, item.name) == (0x700, 0x0F52, 5, 10, "dagger")
    assert Observation.from_dict({"player": {}}).shop_sell is None


def test_observation_without_shop_keys_still_parses():
    # Backwards compatible: an observation dict from before this change (no
    # shop_buy/shop_sell keys at all, not even null) must still parse fine.
    obs = Observation.from_dict({"player": {"serial": 1}, "items": []})
    assert obs.shop_buy is None
    assert obs.shop_sell is None
    assert obs.popup is None


def test_observation_popup_roundtrip():
    obs = Observation.from_dict(
        {
            "player": {"serial": 1},
            "popup": {
                "serial": 0x777,
                "entries": [
                    {"index": 0, "cliloc": 6103, "flags": 0},
                    {"index": 1, "cliloc": 6104, "flags": 0},
                ],
            },
        }
    )
    assert obs.popup is not None
    assert obs.popup.serial == 0x777
    assert len(obs.popup.entries) == 2
    assert obs.popup.entries[1].cliloc == 6104  # "Sell" (ServUO VendorSellEntry)
    assert Observation.from_dict({"player": {}}).popup is None


def test_observation_corpse_of_roundtrip():
    obs = Observation.from_dict(
        {
            "player": {"serial": 1},
            "corpse_of": [{"corpse": 0x4001, "killed": 0x2001}, {"corpse": 0x4002, "killed": 0x2002}],
        }
    )
    assert len(obs.corpse_of) == 2
    assert obs.corpse_of[0].corpse == 0x4001
    assert obs.corpse_of[0].killed == 0x2001
    assert obs.corpse_of[1].corpse == 0x4002
    assert obs.corpse_of[1].killed == 0x2002
    # Absent → empty list, not None (corpse_of/corpse_equip are lists of links,
    # not a single "currently open" slot like pending_target/shop_buy/popup).
    assert Observation.from_dict({"player": {}}).corpse_of == []


def test_observation_corpse_equip_roundtrip():
    obs = Observation.from_dict(
        {
            "player": {"serial": 1},
            "corpse_equip": [
                {"corpse": 0x4001, "entries": [{"layer": 1, "serial": 0x9001}, {"layer": 7, "serial": 0x9002}]},
            ],
        }
    )
    assert len(obs.corpse_equip) == 1
    assert obs.corpse_equip[0].corpse == 0x4001
    assert len(obs.corpse_equip[0].entries) == 2
    assert obs.corpse_equip[0].entries[0].layer == 1
    assert obs.corpse_equip[0].entries[0].serial == 0x9001
    assert obs.corpse_equip[0].entries[1].layer == 7
    assert obs.corpse_equip[0].entries[1].serial == 0x9002
    assert Observation.from_dict({"player": {}}).corpse_equip == []


def test_observation_without_corpse_keys_still_parses():
    # Backwards compatible: an observation dict from before this change (no
    # corpse_of/corpse_equip keys at all) must still parse fine, exactly like
    # the shop_buy/shop_sell/popup backwards-compat test above.
    obs = Observation.from_dict({"player": {"serial": 1}, "items": []})
    assert obs.corpse_of == []
    assert obs.corpse_equip == []


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
