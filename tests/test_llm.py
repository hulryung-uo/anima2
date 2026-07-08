"""Cognition cost tiering + prompt caching (Phase 4 item 2): `build_tiered_clients`'s
degrade-never-crash provider selection, `AnthropicClient`'s cache-control shape, and
the usage-log sink's best-effort token accounting.

Every test that could plausibly touch the network guards against it explicitly
(`urllib.request.urlopen` raises if called) — this file's whole point is proving an
unconfigured process never dials out, so a test that merely *hopes* no call happens
isn't good enough.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from types import SimpleNamespace

import pytest

from anima2 import llm
from anima2.llm import StubLLMClient


def _no_dial_out(*_args, **_kwargs):
    raise AssertionError("urllib.request.urlopen must not be called in this path")


def _stub_anthropic_module(capture: dict, *, usage: SimpleNamespace | None = None) -> SimpleNamespace:
    """A stand-in `anthropic` module: `Anthropic(...)` constructs trivially,
    `.messages.create(**kwargs)` records the kwargs into `capture` and returns a
    canned response — no network, no real SDK involved."""
    if usage is None:
        usage = SimpleNamespace(input_tokens=42, output_tokens=7, cache_read_input_tokens=3)

    class _Messages:
        def create(self, **kwargs):
            capture.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], usage=usage)

    class _Client:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.messages = _Messages()

    return SimpleNamespace(Anthropic=_Client)


# --- build_tiered_clients: degrade, never crash --------------------------------


def test_build_tiered_clients_degrades_with_nothing_configured(monkeypatch, tmp_path):
    """The literal PHASE4.md scenario: no ANTHROPIC_API_KEY, no anthropic package,
    no v1 config.yaml, no REPLICATE_API_TOKEN either — a fully unconfigured
    process. Must still return a real, usable (if uncredentialed) degraded
    single-Replicate mapping, never crash, and never dial out."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    monkeypatch.setattr(llm, "_V1_CONFIG", tmp_path / "no-such-config.yaml")
    monkeypatch.setitem(sys.modules, "anthropic", None)  # forces `import anthropic` to raise
    monkeypatch.setattr(urllib.request, "urlopen", _no_dial_out)

    tiered = llm.build_tiered_clients(usage_log=tmp_path / "usage.jsonl")

    assert tiered.degraded is True
    assert set(tiered) == {"cheap", "standard", "heavy"}
    # One Replicate instance reused for all three tiers — never `None`.
    assert tiered["cheap"].client is tiered["standard"].client is tiered["heavy"].client
    assert isinstance(tiered["cheap"].client, llm.ReplicateClient)


def test_build_tiered_clients_degrades_to_configured_replicate(monkeypatch, tmp_path):
    """Same absence of Anthropic, but a real Replicate token via env — the
    degraded form should pick it up (not just construct an empty-key stub)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_V1_CONFIG", tmp_path / "no-such-config.yaml")
    monkeypatch.setitem(sys.modules, "anthropic", None)
    monkeypatch.setenv("REPLICATE_API_TOKEN", "fake-replicate-token")
    monkeypatch.setattr(urllib.request, "urlopen", _no_dial_out)

    tiered = llm.build_tiered_clients(usage_log=tmp_path / "usage.jsonl")

    assert tiered.degraded is True
    assert tiered["standard"].client.api_key == "fake-replicate-token"


def test_build_tiered_clients_uses_anthropic_when_available(monkeypatch, tmp_path):
    """With a fake key + a stubbed `anthropic` module, three distinct model ids
    land on cheap/standard/heavy and `degraded` is `False`."""
    capture: dict = {}
    monkeypatch.setitem(sys.modules, "anthropic", _stub_anthropic_module(capture))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    tiered = llm.build_tiered_clients(usage_log=tmp_path / "usage.jsonl")

    assert tiered.degraded is False
    models = {tiered[tier].client.model for tier in ("cheap", "standard", "heavy")}
    assert models == {llm.HAIKU_MODEL, llm.DEFAULT_MODEL, llm.OPUS_MODEL}


def test_build_tiered_clients_provider_replicate_bypasses_anthropic(monkeypatch, tmp_path):
    """`provider="replicate"` forces the degraded form outright, even when
    Anthropic construction would otherwise succeed — proves the live gate's
    `--llm-tiers replicate` really is provider-forced, not just "whatever auto
    happens to pick today"."""

    def _must_not_construct(**_kwargs):
        raise AssertionError("AnthropicClient must not be constructed under provider='replicate'")

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=_must_not_construct))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setenv("REPLICATE_API_TOKEN", "fake-replicate-token")

    tiered = llm.build_tiered_clients(provider="replicate", usage_log=tmp_path / "usage.jsonl")

    assert tiered.degraded is True
    assert isinstance(tiered["cheap"].client, llm.ReplicateClient)


def test_build_tiered_clients_provider_anthropic_propagates_failure(monkeypatch, tmp_path):
    """`provider="anthropic"` is an explicit ask — a construction failure must
    surface, not silently degrade (that's what `provider="auto"`, the default,
    is for)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with pytest.raises(ImportError):
        llm.build_tiered_clients(provider="anthropic", usage_log=tmp_path / "usage.jsonl")


def test_build_tiered_clients_provider_stub_is_fully_offline(monkeypatch, tmp_path):
    monkeypatch.setattr(urllib.request, "urlopen", _no_dial_out)

    tiered = llm.build_tiered_clients(provider="stub", usage_log=tmp_path / "usage.jsonl")

    assert tiered.degraded is True
    assert tiered["cheap"].client is tiered["standard"].client is tiered["heavy"].client
    assert isinstance(tiered["cheap"].client, StubLLMClient)
    assert "idle" in tiered["cheap"].complete("sys", "user")


# --- ROLE_TIER / role-per-tier logging ------------------------------------------


def test_role_tier_covers_the_four_specified_call_sites():
    assert llm.ROLE_TIER == {
        "chatter": "cheap",
        "reflection": "standard",
        "wiki_judge": "standard",
        "curriculum_pick": "standard",
    }


def test_primary_role_for_tier_matches_todays_wired_call_sites():
    # Cost discipline: adding a future role is a one-line ROLE_TIER edit, not a
    # new per-class decision — these are the only two call sites wired today.
    assert llm._primary_role_for_tier("cheap") == "chatter"
    assert llm._primary_role_for_tier("standard") == "reflection"
    assert llm._primary_role_for_tier("heavy") == "heavy"  # no role wired to it yet


def test_build_tiered_clients_wraps_with_correct_role_and_tier(tmp_path):
    tiered = llm.build_tiered_clients(provider="stub", usage_log=tmp_path / "usage.jsonl")
    assert tiered["cheap"].role == "chatter" and tiered["cheap"].tier == "cheap"
    assert tiered["standard"].role == "reflection" and tiered["standard"].tier == "standard"


# --- AnthropicClient: prompt-caching shape ---------------------------------------


def test_anthropic_client_caches_a_long_system_prompt():
    capture: dict = {}
    with _swap_anthropic(capture):
        client = llm.AnthropicClient(model=llm.DEFAULT_MODEL)
        long_system = "You are a miner, plainspoken and steady. " * 250  # well over the gate
        client.complete(long_system, "hello")

    assert isinstance(capture["system"], list)
    assert capture["system"] == [
        {"type": "text", "text": long_system, "cache_control": {"type": "ephemeral"}}
    ]
    assert client.last_usage.cache_read_input_tokens == 3


def test_anthropic_client_skips_caching_a_short_system_prompt():
    capture: dict = {}
    with _swap_anthropic(capture):
        client = llm.AnthropicClient(model=llm.DEFAULT_MODEL)
        client.complete("short persona blurb", "hello")

    assert capture["system"] == "short persona blurb"


def test_anthropic_client_cache_system_false_never_caches():
    capture: dict = {}
    with _swap_anthropic(capture):
        client = llm.AnthropicClient(model=llm.DEFAULT_MODEL, cache_system=False)
        long_system = "You are a miner, plainspoken and steady. " * 100
        client.complete(long_system, "hello")

    assert capture["system"] == long_system


class _swap_anthropic:
    """Context-manager form of the `sys.modules["anthropic"] = stub` trick, for
    tests that don't take the `monkeypatch` fixture as a parameter."""

    def __init__(self, capture: dict) -> None:
        self._capture = capture
        self._had_key = "anthropic" in sys.modules
        self._prior = sys.modules.get("anthropic")

    def __enter__(self):
        sys.modules["anthropic"] = _stub_anthropic_module(self._capture)
        return self

    def __exit__(self, *exc):
        if self._had_key:
            sys.modules["anthropic"] = self._prior
        else:
            del sys.modules["anthropic"]


# --- Usage sink: `_UsageLoggingClient` ------------------------------------------


def test_usage_logging_client_omits_fields_the_client_does_not_report(tmp_path):
    log_path = tmp_path / "usage.jsonl"
    stub = StubLLMClient("hi there")
    wrapped = llm._UsageLoggingClient(client=stub, role="chatter", tier="cheap", usage_log=log_path)

    result = wrapped.complete("sys", "user")

    assert result == "hi there"
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["role"] == "chatter"
    assert record["tier"] == "cheap"
    assert record["ok"] is True
    assert "latency_s" in record
    # StubLLMClient has no `last_usage` / `model` — never a KeyError/AttributeError,
    # just an omitted field (or `None` for `model`, which `getattr` always finds).
    assert "prompt_tokens" not in record
    assert "completion_tokens" not in record
    assert "cache_read_input_tokens" not in record
    assert record["model"] is None


def test_usage_logging_client_logs_a_failed_call_and_reraises(tmp_path):
    """Live-caught (PHASE4.md item 2's own gate): an earlier version only logged
    on a clean return, so a flaky/timed-out provider silently produced *no* line
    for that attempt — a live `village.py --llm-tiers replicate` run showed the
    script's own call counter (41) drift from the persisted ledger's line count
    (24) once some Replicate calls failed. `complete()` must still raise (callers
    like `ThreadedCognition`/`ReflectingCognition` already catch it), but a line
    must land regardless — `ok: false`, no usage fields."""

    class _FlakyClient:
        model = "flaky-model"

        def complete(self, system: str, user: str) -> str:
            raise TimeoutError("simulated provider timeout")

    log_path = tmp_path / "usage.jsonl"
    wrapped = llm._UsageLoggingClient(client=_FlakyClient(), role="chatter", tier="cheap", usage_log=log_path)

    with pytest.raises(TimeoutError):
        wrapped.complete("sys", "user")

    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["ok"] is False
    assert record["model"] == "flaky-model"
    assert "prompt_tokens" not in record


def test_usage_logging_client_does_not_misattribute_stale_usage_on_failure(tmp_path):
    """A client that fails on its *second* call must not have the first call's
    `last_usage` (still sitting on the instance — nothing clears it on failure)
    misattributed to the failed attempt's log line."""

    class _SucceedsThenFails:
        model = "sometimes-flaky"

        def __init__(self) -> None:
            self.calls = 0
            self.last_usage = None

        def complete(self, system: str, user: str) -> str:
            self.calls += 1
            if self.calls == 1:
                self.last_usage = SimpleNamespace(input_tokens=100, output_tokens=10,
                                                   cache_read_input_tokens=0)
                return "first call ok"
            raise RuntimeError("second call fails, last_usage still set from call 1")

    log_path = tmp_path / "usage.jsonl"
    client = _SucceedsThenFails()
    wrapped = llm._UsageLoggingClient(client=client, role="chatter", tier="cheap", usage_log=log_path)

    wrapped.complete("sys", "user")
    with pytest.raises(RuntimeError):
        wrapped.complete("sys", "user")

    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(records) == 2
    assert records[0]["ok"] is True and records[0]["prompt_tokens"] == 100
    assert records[1]["ok"] is False
    assert "prompt_tokens" not in records[1]  # not the stale value from call 1


def test_usage_logging_client_includes_token_counts_from_anthropic_client(tmp_path):
    capture: dict = {}
    log_path = tmp_path / "usage.jsonl"
    with _swap_anthropic(capture):
        inner = llm.AnthropicClient(model=llm.DEFAULT_MODEL)
        wrapped = llm._UsageLoggingClient(
            client=inner, role="reflection", tier="standard", usage_log=log_path
        )
        wrapped.complete("short system", "hello")

    record = json.loads(log_path.read_text().splitlines()[0])
    assert record["prompt_tokens"] == 42
    assert record["completion_tokens"] == 7
    assert record["cache_read_input_tokens"] == 3
    assert record["model"] == llm.DEFAULT_MODEL


def test_usage_logging_client_survives_repeated_calls(tmp_path):
    """Multiple `complete()` calls append, not overwrite — the log is a growing
    ledger, matching item 3's `skill_ledger.jsonl` convention this mirrors."""
    log_path = tmp_path / "usage.jsonl"
    stub = StubLLMClient("ok")
    wrapped = llm._UsageLoggingClient(client=stub, role="chatter", tier="cheap", usage_log=log_path)

    for _ in range(3):
        wrapped.complete("sys", "user")

    assert len(log_path.read_text().splitlines()) == 3


def test_usage_logging_client_creates_data_dir_lazily(tmp_path):
    log_path = tmp_path / "nested" / "data" / "llm_usage.jsonl"
    assert not log_path.parent.exists()
    wrapped = llm._UsageLoggingClient(
        client=StubLLMClient("ok"), role="chatter", tier="cheap", usage_log=log_path
    )
    wrapped.complete("sys", "user")
    assert log_path.exists()
