"""IpcBody — a `Body` backed by the anima-net `anima-agent` bridge subprocess.

This is the production body: it spawns the Rust bridge (which connects to the UO
server and logs in), then drives it over newline-delimited JSON (NDJSON) on
stdin/stdout. The brain stays identical whether it talks to this or to `MockBody`.

Wire protocol (one JSON object per line), defined by `anima-net/src/bin/agent.rs`:
  → {"cmd":"observe"}            ← {"ok":true,"obs":{...}}
  → {"cmd":"act","action":{...}} ← {"ok":true}
  → {"cmd":"pump","ms":N}        ← {"ok":true,"applied":N}
  → {"cmd":"quit"}               ← {"ok":true,"bye":true}
First line emitted by the bridge:
{"event":"ready","schema_version":7,"player":{...}}.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import IO, Any

from .contract import Action, Observation

SUPPORTED_SCHEMA_VERSION = 7


def default_bridge_path() -> Path:
    """Locate the built `anima-agent` binary in the sibling anima-client repo."""
    root = Path(__file__).resolve().parents[2] / "anima-client" / "target"
    for profile in ("release", "debug"):
        candidate = root / profile / "anima-agent"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "anima-agent not built — run `cargo build -p anima-net` in ../anima-client"
    )


class IpcError(RuntimeError):
    pass


class IpcBody:
    """Drives a live UO character via the anima-agent bridge subprocess."""

    def __init__(self, proc: subprocess.Popen, *, pump_ms: int = 200) -> None:
        self._proc = proc
        self._pump_ms = pump_ms
        assert proc.stdin is not None and proc.stdout is not None
        self._stdin: IO[str] = proc.stdin
        self._stdout: IO[str] = proc.stdout
        self.ready: dict[str, Any] = {}
        self._await_ready()

    @classmethod
    def spawn(
        cls,
        host: str = "127.0.0.1",
        port: int = 2594,
        username: str = "animatest",
        password: str = "animatest",
        *,
        bridge: str | Path | None = None,
        pump_ms: int = 200,
    ) -> IpcBody:
        cmd = [str(bridge or default_bridge_path()), host, str(port), username, password]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
        )
        return cls(proc, pump_ms=pump_ms)

    # --- Body protocol ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._proc.poll() is None

    def observe(self) -> Observation:
        # Ingest whatever the server has sent since last time, then snapshot.
        self._rpc({"cmd": "pump", "ms": self._pump_ms})
        reply = self._rpc({"cmd": "observe"})
        return Observation.from_dict(reply["obs"])

    def act(self, action: Action) -> None:
        self._rpc({"cmd": "act", "action": action.to_dict()})

    # --- lifecycle / transport -------------------------------------------------

    def close(self) -> None:
        if self.connected:
            try:
                self._rpc({"cmd": "quit"})
            except (IpcError, BrokenPipeError):
                pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()

    def __enter__(self) -> IpcBody:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _await_ready(self) -> None:
        msg = self._read_line()
        if msg.get("event") != "ready":
            raise IpcError(f"expected ready event, got: {msg}")
        version = msg.get("schema_version")
        if version != SUPPORTED_SCHEMA_VERSION:
            raise IpcError(
                f"unsupported bridge schema {version}; expected {SUPPORTED_SCHEMA_VERSION}"
            )
        self.ready = msg

    def _rpc(self, obj: dict[str, Any]) -> dict[str, Any]:
        self._write_line(obj)
        reply = self._read_line()
        if not reply.get("ok", False):
            raise IpcError(reply.get("error", f"bridge error: {reply}"))
        return reply

    def _write_line(self, obj: dict[str, Any]) -> None:
        if not self.connected:
            raise IpcError("bridge process is not running")
        self._stdin.write(json.dumps(obj) + "\n")
        self._stdin.flush()

    def _read_line(self) -> dict[str, Any]:
        line = self._stdout.readline()
        if not line:
            raise IpcError("bridge closed the connection (EOF)")
        return json.loads(line)
