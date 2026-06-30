"""LLM provider abstraction for the slow cognition loop.

Kept behind a tiny `LLMClient` protocol so the rest of anima2 never imports a
vendor SDK directly (DESIGN.md A7). Default deployments use `AnthropicClient`
(latest Claude family, tiered); tests use `StubLLMClient`; offline runs skip the
LLM entirely via `HeuristicCognition` (see cognition.py).
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Protocol

import yaml

# Sensible default model tiers (ids from the running environment, 2026):
#   reflection/planning → Sonnet; cheap/frequent → Haiku; hardest → Opus.
DEFAULT_MODEL = "claude-sonnet-4-6"


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
    ) -> None:
        import anthropic  # optional dependency, imported lazily

        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(self, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")


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
