"""MockBody item physics — the substrate the offline two-Life simulation stands on."""

from anima2.contract import ItemView, PlayerView, Position
from anima2.mock_body import MockBody



# --- item conservation across PickUp/Drop (audit #7) --------------------------------
#
# The mock used to DELETE on PickUp and ignore Drop entirely, which made the exact
# mechanism both live supply chains run on — deliver (Drop to ground) and fetch
# (PickUp, then Drop into the pack) — unrepresentable offline. These pin conservation:
# lift, split, place; never create or destroy.

from anima2.contract import Drop as _Drop
from anima2.contract import PickUp as _PickUp


def _world_total(body, graphic):
    total = sum(i.amount for i in body.items.values() if i.graphic == graphic)
    if body.held is not None and body.held.graphic == graphic:
        total += body.held.amount
    return total


def test_pickup_lifts_to_the_cursor_and_drop_places_on_the_ground():
    body = MockBody()
    body.items[0x10] = ItemView(serial=0x10, graphic=0x1BD7, amount=20, pos=Position(3, 3, 0),
                                container=None, layer=0, distance=0)
    body.act(_PickUp(serial=0x10, amount=20))
    assert body.held is not None and body.held.amount == 20
    assert 0x10 not in body.items
    body.act(_Drop(serial=0x10, x=7, y=7, z=0, container=0xFFFFFFFF))
    assert body.held is None
    ground = [i for i in body.items.values() if i.container is None]
    assert len(ground) == 1 and ground[0].amount == 20
    assert (ground[0].pos.x, ground[0].pos.y) == (7, 7)


def test_drop_into_a_container_moves_the_item_into_it():
    body = MockBody()
    body.items[0x10] = ItemView(serial=0x10, graphic=0x1BD7, amount=5, pos=Position(3, 3, 0),
                                container=None, layer=0, distance=0)
    body.act(_PickUp(serial=0x10, amount=5))
    body.act(_Drop(serial=0x10, container=0x50))
    assert body.held is None
    item = next(i for i in body.items.values() if i.graphic == 0x1BD7)
    assert item.container == 0x50


def test_a_partial_pickup_splits_the_stack_the_way_the_server_does():
    """ServUO's `Mobile.LiftItemDupe` (`Server/Mobile.cs`) gives the NEW item
    `oldAmount - amount` and leaves it in the parent container, then sets
    `oldItem.Amount = amount` and lifts the ORIGINAL onto the cursor. The serial the
    caller asked for is the serial that MOVES.

    This mock had it backwards while its comment claimed to mirror the server, and the
    direction is load-bearing: `BankGold`'s achievement proof requires every manifest
    serial to be GONE from the pack (`cap_bank_start_piles_cleared`). Reversed, the first
    offline bank trip this project could run banked its gold correctly and still reported
    a FAILURE the shard does not produce.
    """
    body = MockBody()
    body.items[0x10] = ItemView(serial=0x10, graphic=0x1BD7, amount=20, pos=Position(3, 3, 0),
                                container=None, layer=0, distance=0)
    body.act(_PickUp(serial=0x10, amount=7))
    # The ORIGINAL serial is on the cursor, carrying the lifted amount...
    assert body.held is not None and body.held.serial == 0x10 and body.held.amount == 7
    assert 0x10 not in body.items
    # ...and the remainder is a fresh item, in the place the stack was.
    remainder = [i for i in body.items.values() if i.graphic == 0x1BD7]
    assert len(remainder) == 1 and remainder[0].amount == 13
    assert remainder[0].serial != 0x10
    assert remainder[0].container is None and (remainder[0].pos.x, remainder[0].pos.y) == (3, 3)
    assert _world_total(body, 0x1BD7) == 20


def test_a_full_cursor_denies_a_second_pickup():
    body = MockBody()
    for serial in (0x10, 0x11):
        body.items[serial] = ItemView(serial=serial, graphic=0x1BD7, amount=1,
                                      pos=Position(3, 3, 0), container=None, layer=0,
                                      distance=0)
    body.act(_PickUp(serial=0x10, amount=1))
    body.act(_PickUp(serial=0x11, amount=1))  # denied: the hand is full
    assert body.held is not None and body.held.serial == 0x10
    assert 0x11 in body.items
    assert _world_total(body, 0x1BD7) == 2


def test_two_bodies_sharing_one_items_dict_inhabit_one_world():
    # The property the two-Life simulation stands on: a ground drop by one agent is
    # visible to the other, exactly like the live shard.
    shared: dict = {}
    a = MockBody(player=PlayerView(serial=0x1, name="A"), items=shared)
    b = MockBody(player=PlayerView(serial=0x2, name="B", pos=Position(5, 5, 0)),
                 items=shared)
    shared[0x10] = ItemView(serial=0x10, graphic=0x1BD7, amount=19, pos=Position(0, 0, 0),
                            container=None, layer=0, distance=0)
    a.act(_PickUp(serial=0x10, amount=19))
    a.act(_Drop(serial=0x10, x=5, y=6, z=0, container=0xFFFFFFFF))
    seen_by_b = [i for i in b.observe().items if i.graphic == 0x1BD7 and i.container is None]
    assert len(seen_by_b) == 1 and seen_by_b[0].distance == 1
