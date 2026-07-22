"""A fake anima-agent bridge for testing IpcBody without a server.

Speaks the same NDJSON protocol as anima-net/src/bin/agent.rs over stdin/stdout:
emits a ready event, then answers observe/act/pump/quit. Maintains a trivial
moving player so a test can see `act(Walk)` take effect via `observe`.
"""

import json
import sys

DELTAS = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    px, py = 100, 100
    acted = []
    emit(
        {
            "event": "ready",
            "schema_version": 16,
            "player": {
                "serial": 1,
                "name": "Fake",
                "pos": {"x": px, "y": py, "z": 0},
            },
        }
    )
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        cmd = msg.get("cmd")
        if cmd == "observe":
            emit(
                {
                    "ok": True,
                    "obs": {
                        "player": {
                            "serial": 1,
                            "name": "Fake",
                            "pos": {"x": px, "y": py, "z": 0},
                            "hits": 80,
                            "hits_max": 80,
                        },
                        "mobiles": [],
                        "items": [],
                        "new_journal": [{"name": "System", "text": f"acted {len(acted)}"}],
                    },
                }
            )
        elif cmd == "act":
            a = msg["action"]
            acted.append(a)
            if a.get("type") == "Walk":
                dx, dy = DELTAS[a["dir"] & 7]
                px, py = px + dx, py + dy
            emit({"ok": True})
        elif cmd == "pump":
            emit({"ok": True, "applied": 0})
        elif cmd == "quit":
            emit({"ok": True, "bye": True})
            return
        else:
            emit({"ok": False, "error": f"unknown cmd {cmd}"})


if __name__ == "__main__":
    main()
