"""IpcBody — a `Body` backed by the anima-net `anima-agent` bridge subprocess.

`IpcBody` is one Rust bridge session; production runners own it through the
stable `ResilientIpcBody` supervisor. The bridge connects to the UO server,
logs in, and is driven over newline-delimited JSON (NDJSON) on stdin/stdout.
The brain stays identical whether it talks to this or to `MockBody`.

Wire protocol (one JSON object per line), defined by `anima-net/src/bin/agent.rs`:
  → {"cmd":"observe"}            ← {"ok":true,"obs":{...}}
  → {"cmd":"act","action":{...}} ← {"ok":true}
  → {"cmd":"pump","ms":N}        ← {"ok":true,"applied":N}
  → {"cmd":"quit"}               ← {"ok":true,"bye":true}
First line emitted by the bridge:
{"event":"ready","schema_version":7,"player":{...}}.
"""

from __future__ import annotations

import hashlib
import json
import queue
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import fcntl

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


class IpcTransportError(IpcError):
    """The bridge process or its NDJSON transport stopped responding."""

    def __init__(self, message: str, *, request_sent: bool | None = None) -> None:
        super().__init__(message)
        self.request_sent = request_sent


class IpcRemoteError(IpcError):
    """The live bridge rejected a structurally valid RPC request."""


class IpcProtocolError(IpcError):
    """The bridge responded, but the request/schema itself is invalid."""


class IpcRecoveryExhausted(IpcTransportError):
    """A resilient body could not establish a replacement bridge in budget."""


class IpcOwnershipError(IpcProtocolError):
    """Another resilient supervisor already owns this character login."""


class _AccountLease:
    def __init__(self, host: str, port: int, username: str) -> None:
        identity = f"{host}:{port}:{username}".encode()
        digest = hashlib.sha256(identity).hexdigest()[:24]
        self.path = Path(tempfile.gettempdir()) / f"anima2-ipc-{digest}.lock"
        self._file = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as exc:
            self._file.close()
            if isinstance(exc, BlockingIOError):
                raise IpcOwnershipError(
                    f"another supervisor owns {host}:{port}/{username}"
                ) from exc
            raise
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()


_EOF = object()


class IpcBody:
    """Drives a live UO character via the anima-agent bridge subprocess."""

    def __init__(
        self,
        proc: subprocess.Popen,
        *,
        pump_ms: int = 200,
        response_timeout_s: float = 30.0,
    ) -> None:
        if response_timeout_s <= 0:
            raise ValueError("response_timeout_s must be positive")
        self._proc = proc
        self._pump_ms = pump_ms
        self._response_timeout_s = response_timeout_s
        self._deadline: float | None = None
        assert proc.stdin is not None and proc.stdout is not None
        self._stdin: IO[str] = proc.stdin
        self._stdout: IO[str] = proc.stdout
        self._lines: queue.Queue[object] = queue.Queue()
        self._reader = threading.Thread(
            target=self._read_stdout,
            name=f"anima-ipc-reader-{proc.pid}",
            daemon=True,
        )
        self._reader.start()
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
        response_timeout_s: float = 30.0,
    ) -> IpcBody:
        cmd = [str(bridge or default_bridge_path()), host, str(port), username, password]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
        )
        try:
            return cls(
                proc,
                pump_ms=pump_ms,
                response_timeout_s=response_timeout_s,
            )
        except BaseException:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            for pipe in (proc.stdin, proc.stdout):
                if pipe is not None:
                    pipe.close()
            raise

    # --- Body protocol ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._proc.poll() is None

    def observe(self) -> Observation:
        # Ingest whatever the server has sent since last time, then snapshot.
        self._rpc({"cmd": "pump", "ms": self._pump_ms})
        reply = self._rpc({"cmd": "observe"})
        try:
            return Observation.from_dict(reply["obs"])
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            raise IpcProtocolError("bridge returned an invalid observation") from exc

    def act(self, action: Action) -> None:
        self._rpc({"cmd": "act", "action": action.to_dict()})

    # --- lifecycle / transport -------------------------------------------------

    def close(self) -> None:
        if self.connected:
            try:
                self._rpc({"cmd": "quit"})
            except (IpcError, BrokenPipeError, OSError):
                self.abort()
        self._reap()
        self._close_pipes()
        self._reader.join(timeout=1)

    def abort(self) -> None:
        """Abruptly stop this bridge, as an OS crash would, without UO logout."""
        if self.connected:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
        self._reap()

    def set_deadline(self, deadline: float | None) -> None:
        """Apply an absolute monotonic deadline to subsequent RPC reads."""
        self._deadline = deadline

    def __enter__(self) -> IpcBody:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _await_ready(self) -> None:
        msg = self._read_line()
        if msg.get("event") != "ready":
            raise IpcProtocolError(f"expected ready event, got: {msg}")
        version = msg.get("schema_version")
        if version != SUPPORTED_SCHEMA_VERSION:
            raise IpcProtocolError(
                f"unsupported bridge schema {version}; expected {SUPPORTED_SCHEMA_VERSION}"
            )
        self.ready = msg

    def _rpc(self, obj: dict[str, Any]) -> dict[str, Any]:
        self._write_line(obj)
        try:
            reply = self._read_line()
        except IpcTransportError as exc:
            # `_write_line` returned only after flush completed. The shard may
            # therefore have applied this request even if its ACK was lost.
            exc.request_sent = True
            raise
        if not reply.get("ok", False):
            raise IpcRemoteError(reply.get("error", f"bridge error: {reply}"))
        return reply

    def _write_line(self, obj: dict[str, Any]) -> None:
        if not self.connected:
            raise IpcTransportError(
                "bridge process is not running", request_sent=False
            )
        try:
            self._stdin.write(json.dumps(obj) + "\n")
            self._stdin.flush()
        except (BrokenPipeError, ConnectionError, OSError) as exc:
            raise IpcTransportError(
                f"bridge write failed: {exc}", request_sent=None
            ) from exc

    def _read_line(self) -> dict[str, Any]:
        timeout = self._response_timeout_s
        if self._deadline is not None:
            timeout = min(timeout, max(0.0, self._deadline - time.monotonic()))
        try:
            value = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise IpcTransportError(
                f"bridge response timed out after {timeout:g}s"
            ) from None
        if value is _EOF:
            raise IpcTransportError("bridge closed the connection (EOF)")
        if isinstance(value, BaseException):
            raise IpcTransportError(f"bridge read failed: {value}") from value
        assert isinstance(value, str)
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise IpcProtocolError("bridge emitted malformed NDJSON") from exc
        if not isinstance(decoded, dict):
            raise IpcProtocolError(f"bridge reply must be an object, got: {decoded!r}")
        return decoded

    def _read_stdout(self) -> None:
        try:
            while True:
                line = self._stdout.readline()
                if not line:
                    break
                self._lines.put(line)
        except BaseException as exc:  # reader failures are surfaced by `_read_line`
            self._lines.put(exc)
        finally:
            self._lines.put(_EOF)

    def _reap(self) -> None:
        try:
            self._proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
            self._proc.wait(timeout=5)

    def _close_pipes(self) -> None:
        for pipe in (self._stdin, self._stdout):
            try:
                pipe.close()
            except OSError:
                pass


@dataclass(frozen=True)
class RestartPolicy:
    """Bounded exponential restart schedule for one bridge outage."""

    max_attempts: int = 6
    initial_backoff_s: float = 0.25
    max_backoff_s: float = 4.0
    max_outage_s: float = 30.0
    immediate_first: bool = True
    stable_after_s: float = 10.0

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if self.initial_backoff_s < 0:
            raise ValueError("initial_backoff_s cannot be negative")
        if self.max_backoff_s < self.initial_backoff_s:
            raise ValueError("max_backoff_s must be >= initial_backoff_s")
        if self.max_outage_s <= 0:
            raise ValueError("max_outage_s must be positive")
        if self.stable_after_s < 0:
            raise ValueError("stable_after_s cannot be negative")

    def delay(self, attempt: int) -> float:
        if attempt <= 0:
            raise ValueError("attempt must be positive")
        if self.immediate_first and attempt == 1:
            return 0.0
        exponent = attempt - 2 if self.immediate_first else attempt - 1
        return min(self.initial_backoff_s * (2 ** exponent), self.max_backoff_s)


_RECOVERABLE = (IpcTransportError, BrokenPipeError, ConnectionError, OSError)


def _dispose_body(body: Any, *, abrupt: bool) -> list[str]:
    """Best-effort child cleanup that never hides the lifecycle transition."""
    errors: list[str] = []
    if abrupt:
        try:
            body.abort()
        except BaseException as exc:
            errors.append(f"abort: {type(exc).__name__}: {exc}")
    try:
        body.close()
    except BaseException as exc:
        errors.append(f"close: {type(exc).__name__}: {exc}")
    return errors


class ResilientIpcBody:
    """Stable Body identity that replaces a failed `IpcBody` in place.

    The Agent owns this wrapper, so its goal, memory, episodic history, and tick
    counter survive a bridge process crash. An outgoing Action whose reply is
    lost is deliberately not replayed: it may already have reached the shard.
    The next Agent tick observes the reconnected world and replans safely.
    """

    def __init__(
        self,
        inner: IpcBody,
        factory: Callable[[], IpcBody],
        *,
        policy: RestartPolicy | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        lease: _AccountLease | None = None,
        timed_factory: Callable[[float], IpcBody] | None = None,
    ) -> None:
        self._inner = inner
        self._factory = factory
        self.policy = policy or RestartPolicy()
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._lease = lease
        self._timed_factory = timed_factory
        self._operation_lock = threading.RLock()
        self._close_requested = threading.Event()
        self._interruptible_sleep = sleeper is time.sleep
        self._closed = False
        self._exhausted = False
        self.generation = 1
        self.restart_count = 0
        self.restart_attempts = 0
        self._attempts_since_stable = 0
        self._last_recovery_at: float | None = None
        # An action can reach the shard even when its acknowledgement is lost.
        # Never replay it automatically; expose the ambiguity for operations.
        self.uncertain_actions = 0
        self.last_error: str | None = None
        self.expected_serial = self._ready_serial(inner)

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
        response_timeout_s: float = 8.0,
        policy: RestartPolicy | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> ResilientIpcBody:
        lease = _AccountLease(host, port, username)

        def factory() -> IpcBody:
            return IpcBody.spawn(
                host,
                port,
                username,
                password,
                bridge=bridge,
                pump_ms=pump_ms,
                response_timeout_s=response_timeout_s,
            )

        def timed_factory(remaining_s: float) -> IpcBody:
            return IpcBody.spawn(
                host,
                port,
                username,
                password,
                bridge=bridge,
                pump_ms=pump_ms,
                response_timeout_s=min(response_timeout_s, max(0.001, remaining_s)),
            )

        inner: IpcBody | None = None
        try:
            inner = factory()
            return cls(
                inner,
                factory,
                policy=policy,
                sleeper=sleeper,
                monotonic=monotonic,
                lease=lease,
                timed_factory=timed_factory,
            )
        except BaseException:
            if inner is not None:
                _dispose_body(inner, abrupt=True)
            lease.close()
            raise

    @property
    def ready(self) -> dict[str, Any]:
        return self._inner.ready

    @property
    def connected(self) -> bool:
        # A dead child is recoverable. Callers such as fleet/village must keep
        # ticking so observe() gets the opportunity to replace it.
        return not self._closed and not self._exhausted

    @property
    def bridge_connected(self) -> bool:
        return self._inner.connected

    @property
    def current_pid(self) -> int:
        return self._inner._proc.pid

    def observe(self) -> Observation:
        with self._operation_lock:
            self._ensure_usable()
            try:
                observation = self._inner.observe()
            except _RECOVERABLE as exc:
                return self._recover(exc)
            self._record_stable_success()
            return observation

    def act(self, action: Action) -> None:
        with self._operation_lock:
            self._ensure_usable()
            while True:
                try:
                    self._inner.act(action)
                except _RECOVERABLE as exc:
                    request_sent = getattr(exc, "request_sent", None)
                    if request_sent is not False:
                        self.uncertain_actions += 1
                    self._recover(exc)
                    if request_sent is False:
                        # The child was already dead before the write. Retrying
                        # cannot duplicate an action that was never sent.
                        continue
                    return
                self._record_stable_success()
                return

    def abort_current_bridge(self) -> None:
        """Operational failure injection: abruptly kill only the child bridge."""
        with self._operation_lock:
            self._ensure_usable()
            self._inner.abort()

    def close(self) -> None:
        # Set this before taking the operation lock so a production backoff can
        # wake immediately and a re-entrant close cannot be followed by spawn.
        self._close_requested.set()
        with self._operation_lock:
            if self._closed:
                return
            self._closed = True
            errors = _dispose_body(self._inner, abrupt=False)
            if errors:
                self.last_error = "; ".join(errors)
            self._release_lease()

    def __enter__(self) -> ResilientIpcBody:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _recover(self, cause: BaseException) -> Observation:
        self.last_error = f"{type(cause).__name__}: {cause}"
        errors = [self.last_error]
        outage_started = self._monotonic()
        deadline = outage_started + self.policy.max_outage_s
        errors.extend(_dispose_body(self._inner, abrupt=True))
        attempts_made = 0
        attempts_left = self.policy.max_attempts - self._attempts_since_stable
        for _ in range(attempts_left):
            attempt = self._attempts_since_stable + 1
            delay = self.policy.delay(attempt)
            if self._monotonic() + delay >= deadline:
                break
            self.restart_attempts += 1
            self._attempts_since_stable += 1
            attempts_made += 1
            if self._wait_backoff(delay):
                break
            if self._monotonic() >= deadline or self._close_requested.is_set():
                break
            candidate: IpcBody | None = None
            try:
                remaining = deadline - self._monotonic()
                candidate = (
                    self._timed_factory(remaining)
                    if self._timed_factory is not None
                    else self._factory()
                )
                if self._close_requested.is_set():
                    errors.extend(_dispose_body(candidate, abrupt=True))
                    candidate = None
                    break
                if hasattr(candidate, "set_deadline"):
                    candidate.set_deadline(deadline)
                candidate_serial = self._ready_serial(candidate)
                if candidate_serial != self.expected_serial:
                    raise IpcProtocolError(
                        "replacement bridge selected player serial "
                        f"{candidate_serial}, expected {self.expected_serial}"
                    )
                observation = candidate.observe()
                if observation.player.serial != self.expected_serial:
                    raise IpcProtocolError(
                        "replacement observation player serial "
                        f"{observation.player.serial}, expected {self.expected_serial}"
                    )
                if self._monotonic() >= deadline:
                    raise IpcTransportError("replacement exceeded outage deadline")
                if self._close_requested.is_set():
                    errors.extend(_dispose_body(candidate, abrupt=True))
                    candidate = None
                    break
            except IpcProtocolError:
                if candidate is not None:
                    errors.extend(_dispose_body(candidate, abrupt=True))
                self._fail_closed()
                raise
            except (IpcRemoteError, *_RECOVERABLE) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                if candidate is not None:
                    errors.extend(_dispose_body(candidate, abrupt=True))
                if self._monotonic() >= deadline:
                    break
                continue
            except BaseException:
                if candidate is not None:
                    errors.extend(_dispose_body(candidate, abrupt=True))
                self._fail_closed()
                raise
            if hasattr(candidate, "set_deadline"):
                candidate.set_deadline(None)
            self._inner = candidate
            self.generation += 1
            self.restart_count += 1
            self._last_recovery_at = self._monotonic()
            self.last_error = None
            return observation

        self._fail_closed()
        detail = "; ".join(errors[-3:])
        raise IpcRecoveryExhausted(
            "bridge recovery exhausted after "
            f"{attempts_made} attempts/{self.policy.max_outage_s:g}s budget: {detail}"
        ) from cause

    def _ensure_usable(self) -> None:
        if self._closed:
            raise IpcTransportError("resilient body is closed")
        if self._exhausted:
            raise IpcRecoveryExhausted("bridge recovery budget is exhausted")

    def _release_lease(self) -> None:
        if self._lease is not None:
            self._lease.close()
            self._lease = None

    def _fail_closed(self) -> None:
        self._exhausted = True
        self._release_lease()

    def _record_stable_success(self) -> None:
        if self._last_recovery_at is None:
            return
        if self._monotonic() - self._last_recovery_at < self.policy.stable_after_s:
            return
        self._attempts_since_stable = 0
        self._last_recovery_at = None

    def _wait_backoff(self, delay: float) -> bool:
        if self._interruptible_sleep:
            return self._close_requested.wait(delay)
        self._sleeper(delay)
        return self._close_requested.is_set()

    @staticmethod
    def _ready_serial(body: IpcBody) -> int:
        serial = body.ready.get("player", {}).get("serial")
        if type(serial) is not int or serial <= 0:
            raise IpcProtocolError(f"bridge ready event has invalid player serial: {serial!r}")
        return serial
