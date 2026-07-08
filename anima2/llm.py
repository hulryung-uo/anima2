"""LLM provider abstraction for the slow cognition loop.

Kept behind a tiny `LLMClient` protocol so the rest of anima2 never imports a
vendor SDK directly (DESIGN.md A7). Default deployments use `AnthropicClient`
(latest Claude family, tiered); tests use `StubLLMClient`; offline runs skip the
LLM entirely via `HeuristicCognition` (see cognition.py).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

# Sensible default model tiers (ids confirmed current via the `claude-api` skill
# at Phase 4 item 2's landing — re-consult that skill if they've since drifted):
#   reflection/planning → Sonnet; cheap/frequent → Haiku; hardest → Opus.
HAIKU_MODEL = "claude-haiku-4-5"
OPUS_MODEL = "claude-opus-4-8"
DEFAULT_MODEL = "claude-sonnet-5"


class LLMClient(Protocol):
    """A minimal single-turn completion interface."""

    def complete(self, system: str, user: str) -> str:
        """Return the model's text response to `user` under `system` guidance."""
        ...


class StubLLMClient:
    """Deterministic client for tests: returns a canned response, records calls."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


# Minimum prompt-caching-eligible prefix, in tokens, by model (`claude-api` skill,
# "Prompt Caching" reference table, as of Phase 4 item 2's landing). A model not
# listed here defaults to the most conservative value in the table (4096) — caching
# a block that's actually too short to be eligible is a wasted write, not a win, so
# under-caching (missing a real eligible block) is the safer failure than over-caching.
# `DEFAULT_MODEL` (Sonnet 5) isn't itself listed in the skill's cached table yet;
# treated here as no more permissive than its immediate predecessor, Sonnet 4.6
# (2048) — a documented approximation, not a confirmed number.
_CACHE_MIN_TOKENS: dict[str, int] = {
    HAIKU_MODEL: 4096,
    OPUS_MODEL: 4096,
    DEFAULT_MODEL: 2048,  # claude-sonnet-5 — approximated from claude-sonnet-4-6, see above
    "claude-sonnet-4-6": 2048,
}
_CACHE_MIN_TOKENS_DEFAULT = 4096


def _approx_tokens(text: str) -> int:
    """Rough chars-per-token estimate (~4:1, the commonly-cited ballpark for English
    prose) used only to gate whether a system prompt clears the cacheable-prefix
    floor — never billed against, never sent to the API. Exact enough for this: the
    gate only needs to distinguish "clearly short" (a one-line persona blurb) from
    "clearly long enough to be worth a cache write", not count precisely."""
    return len(text) // 4


class AnthropicClient:
    """Claude-backed client. Requires `anthropic` installed and an API key.

    Install with the `llm` extra (`pip install -e ".[llm]"`); the key is read from
    `ANTHROPIC_API_KEY` unless passed explicitly. Never called from the fast loop —
    only from the (threaded) cognition slow loop.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        max_tokens: int = 512,
        api_key: str | None = None,
        cache_system: bool = True,
    ) -> None:
        import anthropic  # optional dependency, imported lazily

        self.model = model
        self.max_tokens = max_tokens
        self.cache_system = cache_system
        self._client = anthropic.Anthropic(api_key=api_key)
        #: The SDK response's `usage` object from the most recent `complete()` call
        #: (`None` until the first call) — `_UsageLoggingClient` reads this,
        #: best-effort, for token/cache-read counts. Not present on
        #: `ReplicateClient`/`StubLLMClient`, which don't report usage at all.
        #: Known limitation: this is a plain instance attribute, not thread-local —
        #: if the *same* `AnthropicClient` instance is called concurrently from two
        #: threads (possible once a tier's client is shared across agents, as
        #: `build_tiered_clients` does), a usage line can occasionally be attributed
        #: to the wrong call. Logged usage numbers are advisory/aggregate, so this
        #: is a minor accuracy nit, not a correctness risk — noted rather than fixed
        #: here to avoid widening `LLMClient`'s surface for it.
        self.last_usage: Any = None

    def complete(self, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self._system_param(system),
            messages=[{"role": "user", "content": user}],
        )
        self.last_usage = getattr(msg, "usage", None)
        return "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")

    def _system_param(self, system: str) -> str | list[dict[str, Any]]:
        """Plain string by default; a single cache-marked block when `cache_system`
        is on and `system` clears this model's minimum cacheable-prefix size
        (`_CACHE_MIN_TOKENS`) — DESIGN.md §7's "cache aggressively", made real."""
        min_tokens = _CACHE_MIN_TOKENS.get(self.model, _CACHE_MIN_TOKENS_DEFAULT)
        if self.cache_system and _approx_tokens(system) >= min_tokens:
            return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        return system


_V1_CONFIG = Path.home() / "dev" / "uo" / "anima" / "config.yaml"


class ReplicateClient:
    """Replicate-hosted LLM (no SDK — `urllib` against the predictions API).

    Used because anima v1's config ships a Replicate key + model (qwen3). `complete`
    POSTs with `Prefer: wait` to get the result synchronously. Configure via
    `ReplicateClient.from_v1_config()` or env (`REPLICATE_API_TOKEN`, `REPLICATE_MODEL`).
    """

    def __init__(self, model: str, api_key: str, *, max_tokens: int = 400,
                 temperature: float = 0.7) -> None:
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature

    @classmethod
    def from_v1_config(cls) -> ReplicateClient | None:
        """Build from env or anima v1's `config.yaml` `llm:` section (Replicate only)."""
        key = os.environ.get("REPLICATE_API_TOKEN")
        model = os.environ.get("REPLICATE_MODEL", "qwen/qwen3-235b-a22b-instruct-2507")
        temp = 0.7
        if not key and _V1_CONFIG.exists():
            llm = (yaml.safe_load(_V1_CONFIG.read_text()) or {}).get("llm", {})
            if llm.get("provider") == "replicate":
                key = llm.get("api_key", "")
                model = llm.get("model", model)
                temp = float(llm.get("temperature", temp))
        return cls(model, key, temperature=temp) if key else None

    def complete(self, system: str, user: str) -> str:
        url = f"https://api.replicate.com/v1/models/{self.model}/predictions"
        body = json.dumps({
            "input": {
                "prompt": user,
                "system_prompt": system,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
            }
        }).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Prefer": "wait",
            },
        )
        with urllib.request.urlopen(req, timeout=90) as r:  # noqa: S310 (trusted host)
            resp = json.loads(r.read().decode())
        out = resp.get("output", "")
        return "".join(out) if isinstance(out, list) else (out or "")


# --- Cost tiering (Phase 4 item 2, DESIGN.md §7) --------------------------------
#
# `ROLE_TIER` is the single auditable table every LLM call site looks itself up
# in — adding a future call site is a one-line addition here, never a new
# per-class tier decision. `build_tiered_clients()` is the one place that decides
# *which concrete client* backs each tier; nothing else in this file (or
# `cognition.py`) needs to know whether tiering is real or degraded.

#: `{role: tier}` — `LLMCognition`'s frequent in-character chatter is cheap and
#: frequent; `LLMReflection` and item 1's `LLMWikiReportProducer` (both not yet
#: wired to a tier as of this item) and item 5's curriculum picker are occasional
#: and worth a better model. `wiki_judge`/`curriculum_pick` have no call site yet
#: (items 1/5 land later) — present here so they're tiered from birth per
#: PHASE4.md's own dependency-order note.
ROLE_TIER: dict[str, str] = {
    "chatter": "cheap",
    "reflection": "standard",
    "wiki_judge": "standard",
    "curriculum_pick": "standard",
}

#: `{tier: model}` — the model each tier resolves to when Anthropic is available.
_TIER_MODEL: dict[str, str] = {"cheap": HAIKU_MODEL, "standard": DEFAULT_MODEL, "heavy": OPUS_MODEL}

#: `data/llm_usage.jsonl` relative to the process's cwd (matches how every
#: `live_*.py`/`village.py` script in this repo is already invoked — from the
#: repo root). `data/` is created lazily on first write, never committed (see
#: `.gitignore`). Tests must always pass an explicit `usage_log=` (a `tmp_path`)
#: rather than relying on this default, so the suite never touches the real file.
_DEFAULT_USAGE_LOG = Path("data") / "llm_usage.jsonl"

#: Guards concurrent appends to a single usage-log file across threads (village.py
#: runs each agent's tick loop, and each agent's `ThreadedCognition`/
#: `ReflectingCognition` background reconsider, on its own thread — several can
#: call the *same* shared tier client's `complete()` around the same time). A
#: single global lock is enough: LLM calls are infrequent relative to the fast
#: loop, so contention is a non-issue, and it keeps this simple rather than
#: keyed-per-path for a single-process, single-log-file repo.
_usage_log_lock = threading.Lock()


def _primary_role_for_tier(tier: str) -> str:
    """The first `ROLE_TIER` entry mapped to `tier`, in table order — correct for
    every role actually wired to a call site today (`chatter`→cheap,
    `reflection`→standard). `build_tiered_clients()` only knows tiers, not which
    role is calling `complete()` on a given call (the `LLMClient` Protocol's fixed
    `complete(system, user)` shape carries no role) — this is the simplest
    role label available without widening that Protocol. Once item 1/5 land
    `wiki_judge`/`curriculum_pick` (also `standard`-tier), those call sites would
    log as `"reflection"` too under this scheme; a caller wanting role-accurate
    logging for a *second* role on an already-populated tier can re-wrap
    `clients[tier].client` with an explicit `_UsageLoggingClient(..., role=...)` —
    no new API needed, since `role` is already a plain constructor arg."""
    for role, mapped_tier in ROLE_TIER.items():
        if mapped_tier == tier:
            return role
    return tier  # no role wired to this tier yet (e.g. "heavy", today)


@dataclass
class _UsageLoggingClient:
    """Wraps an `LLMClient`, appending one JSON line to `usage_log` per `complete()`
    *call* — `{ts, role, tier, model, latency_s, ok}` always, plus best-effort
    `prompt_tokens`/`completion_tokens`/`cache_read_input_tokens` on a successful
    call whose client exposes a `last_usage` attribute (only `AnthropicClient`
    does — absent on `ReplicateClient`/`StubLLMClient`, so those fields are just
    omitted, never a crash). A broken/unwritable log degrades silently — the same
    "never break cognition over a logging failure" discipline as the rest of this
    file.

    Logs on `finally`, not only on a clean return: a flaky/timed-out provider
    (Replicate's own `urlopen(timeout=90)`, in particular) still gets one line —
    "one JSON line per complete() call" means every *attempt*, not just the ones
    that returned cleanly. `ok=False` on a raised exception, and usage fields are
    only ever read on `ok=True` — reading `last_usage` after a failed call would
    silently attribute a *prior* successful call's token counts to this one
    (`last_usage` isn't cleared on failure, only overwritten on the next success).
    Caught live (PHASE4.md item 2's own gate): an earlier version without this
    `finally` logged only on success, and a live `village.py` run surfaced a real
    gap (41 attempted chatter calls, 24 logged) once some Replicate calls timed
    out — exactly what the live gate's counter-vs-ledger cross-check exists to
    catch."""

    client: LLMClient
    role: str
    tier: str
    usage_log: Path

    def complete(self, system: str, user: str) -> str:
        start = time.monotonic()
        ok = False
        try:
            result = self.client.complete(system, user)
            ok = True
            return result
        finally:
            self._log(time.monotonic() - start, ok=ok)

    def _log(self, latency_s: float, *, ok: bool) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "role": self.role,
            "tier": self.tier,
            "model": getattr(self.client, "model", None),
            "latency_s": round(latency_s, 4),
            "ok": ok,
        }
        if ok:
            usage = getattr(self.client, "last_usage", None)
            if usage is not None:
                for field, attr in (
                    ("prompt_tokens", "input_tokens"),
                    ("completion_tokens", "output_tokens"),
                    ("cache_read_input_tokens", "cache_read_input_tokens"),
                ):
                    value = getattr(usage, attr, None)
                    if value is not None:
                        record[field] = value
        try:
            with _usage_log_lock:
                self.usage_log.parent.mkdir(parents=True, exist_ok=True)
                with self.usage_log.open("a") as f:
                    f.write(json.dumps(record) + "\n")
        except OSError:
            pass


class TieredClients(dict):
    """`dict[str, LLMClient]` keyed `"cheap"`/`"standard"`/`"heavy"`, plus a
    `degraded` flag: `False` means three distinct `AnthropicClient` models;
    `True` means one `ReplicateClient` (or `StubLLMClient`) instance answering
    for all three tiers — a documented no-op, not a crash, when Anthropic isn't
    provisioned. A small subclass rather than a 4th `"degraded"` dict key, so
    `clients[tier]` indexing (what every call site actually does) is unchanged."""

    def __init__(self, clients: dict[str, LLMClient], *, degraded: bool) -> None:
        super().__init__(clients)
        self.degraded = degraded


def _replicate_tiers() -> dict[str, LLMClient]:
    """One `ReplicateClient` (from env/`config.yaml`, or an empty-key instance if
    neither is configured — never `None`, so a caller always gets a real
    `LLMClient` to hand to `LLMCognition`/`LLMReflection` even in a fully offline
    environment; an empty-key `complete()` call fails at call time, which every
    existing consumer already tolerates: `ThreadedCognition`/`ReflectingCognition`
    catch and fall back to the current goal, `LLMReflection` falls back to
    `HeuristicReflection`) reused for all three tiers."""
    replicate = ReplicateClient.from_v1_config() or ReplicateClient(
        model=os.environ.get("REPLICATE_MODEL", "qwen/qwen3-235b-a22b-instruct-2507"),
        api_key=os.environ.get("REPLICATE_API_TOKEN", ""),
    )
    return dict.fromkeys(_TIER_MODEL, replicate)


def build_tiered_clients(*, provider: str = "auto", usage_log: Path | None = None) -> TieredClients:
    """Build the `{"cheap", "standard", "heavy"}` client mapping DESIGN.md §7's
    tiering describes, wrapped for usage logging (`_UsageLoggingClient`).

    `provider`:
      - `"auto"` (default) — try `AnthropicClient` for all three tiers; on *any*
        construction failure (no `anthropic` package, no resolvable API key, …)
        fall back to the degraded single-`ReplicateClient` form silently.
      - `"anthropic"` — same Anthropic attempt, but a construction failure is
        **not** swallowed: the caller explicitly asked for real tiering, so
        finding out it's unavailable is more useful than a silent downgrade.
      - `"replicate"` — force the degraded single-`ReplicateClient` form outright,
        regardless of whether `ANTHROPIC_API_KEY` happens to be set (used by
        `village.py --llm-tiers replicate`'s provider-agnostic live gate, which
        wants to prove the *routing* plumbing without needing a live Anthropic
        key: Replicate's one qwen3 model answers identically for every tier, so
        differing per-tier behavior can only come from the routing itself).
      - `"stub"` — every tier is the same `StubLLMClient` (canned response,
        records calls) — for smoke-testing the wiring with zero network at all.

    Never touches the network itself: constructing a client (Anthropic SDK client,
    `ReplicateClient`, `StubLLMClient`) does no I/O — only a later `complete()`
    call does.
    """
    log_path = usage_log if usage_log is not None else _DEFAULT_USAGE_LOG

    if provider == "stub":
        stub = StubLLMClient('{"say": "(stub)", "goal": "idle"}')
        clients: dict[str, LLMClient] = dict.fromkeys(_TIER_MODEL, stub)
        degraded = True
    elif provider == "replicate":
        clients = _replicate_tiers()
        degraded = True
    else:
        try:
            clients = {tier: AnthropicClient(model=model) for tier, model in _TIER_MODEL.items()}
            degraded = False
        except Exception:
            if provider == "anthropic":
                raise
            clients = _replicate_tiers()
            degraded = True

    wrapped = {
        tier: _UsageLoggingClient(
            client=client, role=_primary_role_for_tier(tier), tier=tier, usage_log=log_path
        )
        for tier, client in clients.items()
    }
    return TieredClients(wrapped, degraded=degraded)
