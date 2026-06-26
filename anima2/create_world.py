"""Generate the world via the GM, then verify a town is populated.

Primary path: ServUO's built-in `[CreateWorld nogump` (Administrator) — generates
moongates, doors, signs, teleporters, decorations, and spawners across all facets.
Then teleport to Britain and observe the signs/doors/mobiles that appeared.

For *custom* scenario staging (a workplace for an agent) use anima2.worldbuilder
instead. Requires a running ServUO + the built bridge.
Usage: python -m anima2.create_world [--verify-x X --verify-y Y]
"""

from __future__ import annotations

import argparse

from .control import GmControl


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2594)
    # Britain bank / West Britain crossroads (Trammel/Felucca) — densely decorated.
    ap.add_argument("--verify-x", type=int, default=1416)
    ap.add_argument("--verify-y", type=int, default=1683)
    args = ap.parse_args()

    with GmControl.spawn(args.host, args.port) as gm:
        print("generating world via [CreateWorld nogump ...")
        lines = gm.create_world(pumps=200)
        for t in lines:
            print("  ", t)

        # Verify: teleport to a town and look around.
        gm.hide()
        x, y, z = gm.go(args.verify_x, args.verify_y)
        gm.body.observe()
        obs = gm.body.observe()
        signs = [i for i in obs.items if i.graphic in range(0x0B95, 0x0BAC)]
        doors = [i for i in obs.items if 0x0675 <= i.graphic <= 0x06F6]
        print(f"\nverify @ Britain ({x},{y},{z}): "
              f"{len(obs.items)} items, {len(obs.mobiles)} mobiles nearby; "
              f"signs≈{len(signs)}, doors≈{len(doors)}")


if __name__ == "__main__":
    main()
