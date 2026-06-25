"""LLM provider abstraction for the slow cognition loop.

Kept behind a tiny `LLMClient` protocol so the rest of anima2 never imports a
vendor SDK directly (DESIGN.md A7). Default deployments use `AnthropicClient`
(latest Claude family, tiered); tests use `StubLLMClient`; offline runs skip the
LLM entirely via `HeuristicCognition` (see cognition.py).
"""

from __future__ import annotations

from typing import Protocol

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
