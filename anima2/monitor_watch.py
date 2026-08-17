"""Watch `--monitor` ports without Chrome — poll scene.json, keep a durable log.

The bridge already serves a read-only UO view on loopback (`docs/MONITORING.md`).
Opening that URL in Chrome was the old habit; this module is the project-owned
watcher: it does not log in (so it cannot kick an agent), it prints a compact
status line, and it appends every sample to a log file under `.logs/` (or
`$ANIMA_LOG_DIR`).

Usage (while a village run has `--monitor` up)::

    uv run python -m anima2.monitor_watch --ports 8801,8802

Prefer launching via ``scripts/run_forge_pair.sh``, which starts the forge day
and this watcher together and opens Safari (not Chrome) on the monitor URLs
when a GUI is available.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def _default_log_dir() -> Path:
    env = Path(__import__("os").environ.get("ANIMA_LOG_DIR", "")).expanduser()
    if env.parts:
        return env
    # Workspace-local first — agent sandboxes often cannot write ~/anima-logs.
    here = Path(__file__).resolve().parents[1] / ".logs"
    home = Path.home() / "anima-logs"
    for candidate in (home, here):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except OSError:
            continue
    here.mkdir(parents=True, exist_ok=True)
    return here


def _scene(port: int, timeout: float = 2.0) -> dict | None:
    url = f"http://127.0.0.1:{port}/scene.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _one_line(port: int, scene: dict | None) -> str:
    if scene is None:
        return f"{port}: down"
    player = scene.get("player") or {}
    xy = (player.get("x"), player.get("y"))
    gold = player.get("gold")
    hp = player.get("hits")
    mx = player.get("hitsMax")
    name = player.get("name") or "?"
    journal = scene.get("journal") or []
    last = ""
    for entry in reversed(journal[-8:]):
        if not isinstance(entry, dict):
            continue
        text = (entry.get("text") or "").strip()
        if text:
            last = text[:60]
            break
    return f"{port}:{name} @{xy} hp={hp}/{mx} gold={gold} | {last}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--ports",
        default="8801,8802",
        help="comma-separated monitor ports (default: 8801,8802)",
    )
    ap.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="seconds between polls (default: 2)",
    )
    ap.add_argument(
        "--log",
        default="",
        help="log path (default: $ANIMA_LOG_DIR/monitor-TIMESTAMP.log)",
    )
    ap.add_argument(
        "--once",
        action="store_true",
        help="one sample then exit (for smoke checks)",
    )
    args = ap.parse_args(argv)
    ports = [int(p.strip()) for p in args.ports.split(",") if p.strip()]
    if not ports:
        print("no ports", file=sys.stderr)
        return 2

    log_dir = _default_log_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    log_path = Path(args.log).expanduser() if args.log else log_dir / f"monitor-{stamp}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"monitor_watch log={log_path}", flush=True)
    print(f"polling ports={ports} every {args.interval}s (Ctrl-C to stop)", flush=True)

    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"# monitor_watch start {datetime.now().isoformat()} ports={ports}\n")
        log.flush()
        while True:
            now = datetime.now().strftime("%H:%M:%S")
            lines = []
            for port in ports:
                scene = _scene(port)
                line = _one_line(port, scene)
                lines.append(line)
                log.write(f"{now}\t{line}\n")
            log.flush()
            print(f"{now}  " + "  ||  ".join(lines), flush=True)
            if args.once:
                return 0
            time.sleep(max(0.2, args.interval))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nmonitor_watch stopped", flush=True)
        raise SystemExit(0)
