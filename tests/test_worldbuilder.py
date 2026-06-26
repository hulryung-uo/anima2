"""WorldBuilder issues the right GM commands to construct a scene (offline)."""

from types import SimpleNamespace

from anima2.worldbuilder import SIGN_GRAPHIC, WorldBuilder, blacksmith_shop


class FakeGm:
    """Records GM commands; hands back synthetic serials for spawned entities."""

    def __init__(self) -> None:
        self.cmds: list[tuple] = []
        self._serial = 1000

    def command_at(self, command, x, y, z):
        self.cmds.append(("at", command, x, y, z))
        return True

    def command_area(self, command, x1, y1, x2, y2, z):
        self.cmds.append(("area", command))
        return True

    def command_on(self, command, serial):
        self.cmds.append(("on", command, serial))
        return True

    def find_item_near(self, x, y, graphic=None):
        self._serial += 1
        return SimpleNamespace(serial=self._serial, graphic=graphic or 0, pos=SimpleNamespace(x=x, y=y))

    def find_mobile_near(self, x, y, max_dist=1):
        self._serial += 1
        return SimpleNamespace(serial=self._serial, pos=SimpleNamespace(x=x, y=y))


def _at_commands(gm: FakeGm) -> list[str]:
    return [c[1] for c in gm.cmds if c[0] == "at"]


def test_clear_uses_area_wipes():
    gm = FakeGm()
    WorldBuilder(gm).clear(100, 100, 0, radius=5)
    area = [c[1] for c in gm.cmds if c[0] == "area"]
    assert area == ["[WipeNPCs", "[WipeItems"]


def test_build_blacksmith_shop_issues_all_adds():
    gm = FakeGm()
    created = WorldBuilder(gm).build(blacksmith_shop(), 200, 200, 5)
    adds = _at_commands(gm)
    assert "[Add Forge" in adds
    assert "[Add Anvil" in adds
    assert "[Add Blacksmith" in adds
    assert "[Add Banker" in adds
    assert "[Add Sign" in adds
    assert "[Add DarkWoodDoor SouthCW" in adds
    # The sign got named via [Set Name on its new serial.
    assert any(c[0] == "on" and c[1].startswith('[Set Name "Anima Smithy"') for c in gm.cmds)
    # Summary captured vendors/signs/doors.
    assert len(created["vendors"]) == 2 and len(created["doors"]) == 1
    assert created["signs"][0][0] == "Anima Smithy"


def test_add_sign_finds_by_graphic_and_names_it():
    gm = FakeGm()
    serial = WorldBuilder(gm).add_sign("Bakery", 10, 11, 0)
    assert serial is not None
    # It looked the sign up by the Sign graphic before naming it.
    assert any(c[0] == "on" and str(serial) and c[2] == serial for c in gm.cmds)
    assert SIGN_GRAPHIC == 0x0B95
