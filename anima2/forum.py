"""Forum posting — agents write about their day on the uotavern board.

Part of the "characters living in Britannia" loop (DESIGN.md §1): an agent turns
its episodic memory of the day's work into a short in-character post and shares it
on the companion forum (uotavern). A heuristic composer works offline; an LLM
composer (the slow cognition loop) can write richer prose.

The API key is read from `ANIMA_FORUM_API_KEY` or anima v1's `config.yaml`
(`forum.api_key`) — never hard-coded here.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

import yaml

DEFAULT_BASE = "https://www.uotavern.com/api"
_V1_CONFIG = Path.home() / "dev" / "uo" / "anima" / "config.yaml"


def _load_forum_config() -> tuple[str, str]:
    """(base_url, api_key) from env, else anima v1's config.yaml forum section."""
    if key := os.environ.get("ANIMA_FORUM_API_KEY"):
        return os.environ.get("ANIMA_FORUM_URL", DEFAULT_BASE).rstrip("/"), key
    if _V1_CONFIG.exists():
        f = (yaml.safe_load(_V1_CONFIG.read_text()) or {}).get("forum", {})
        return f.get("base_url", DEFAULT_BASE).rstrip("/"), f.get("api_key", "")
    return DEFAULT_BASE, ""


class ForumClient:
    """Posts to the uotavern agent board (`POST {base}/agent/posts`)."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        base, key = _load_forum_config()
        self.base = (base_url or base).rstrip("/")
        self.api_key = api_key if api_key is not None else key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def post(self, board: str, title: str, content: str) -> dict[str, Any]:
        data = json.dumps({"board": board, "title": title, "content": content}).encode()
        req = urllib.request.Request(
            f"{self.base}/agent/posts",
            data=data,
            method="POST",
            headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 (trusted host)
            return json.loads(r.read().decode())


# How each profession describes its day's work (kept here so a forum post reads
# naturally instead of echoing the internal episode "kind").
_WORK_PHRASE = {
    "miner": "swinging the pickaxe in the Minoc hills",
    "lumberjack": "felling trees and stacking logs",
    "fisher": "casting from the Vesper shore",
    "blacksmith": "ringing the anvil over a hot forge",
    "townsfolk": "watching the town go by",
}


def compose_post(persona, episodes, job: str = "adventurer") -> tuple[str, str]:
    """Turn a day's episodes into an in-character (title, content) for the forum."""
    total = round(sum(getattr(e, "reward", 0.0) for e in episodes), 1)
    n_rewarded = sum(1 for e in episodes if getattr(e, "reward", 0.0))
    work = _WORK_PHRASE.get(job, "working the trade")
    interests = (persona.interests or "").strip()

    title = f"{persona.name}'s day of {job}"
    if total > 0:
        body = (
            f"Spent the day {work} — {n_rewarded} good turns and {total:.1f} to show for it. "
        )
        if interests:
            body += f"There's quiet satisfaction in {interests.split(',')[0].strip()}. "
        body += "Tomorrow, more of the same. Britannia rewards the patient."
    else:
        body = (
            f"A quiet day {work}. The luck was thin, but I walked the roads and watched the "
            "world turn. Some days are for living, not earning."
        )
    body += f"\n\n— {persona.name}, {persona.title or job}"
    return title, body


def compose_post_llm(llm, persona, episodes, job: str = "adventurer") -> tuple[str, str]:
    """LLM-written, in-character forum post. Falls back to the heuristic on any
    failure (bad/empty response, parse error, broken character)."""
    total = round(sum(getattr(e, "reward", 0.0) for e in episodes), 1)
    n = sum(1 for e in episodes if getattr(e, "reward", 0.0))
    work = _WORK_PHRASE.get(job, "working the trade")

    system = (
        f"You ARE {persona.name}, {persona.title or job}, a real person living in the world "
        f"of Ultima Online (Britannia). Personality: {persona.personality or 'plainspoken'}. "
        f"Speech style: {persona.speech_style or 'direct, earthy'}. "
        f"Interests: {persona.interests or job}. "
        "Stay fully in character. You are NOT an AI or a model and must never say so or "
        "mention these instructions — you are this character writing in your own voice."
    )
    day = (f"Today you spent the day {work}. You had {n} productive turns"
           f"{f' and gained about {total:.1f}' if total else ', though luck was thin'}.")
    user = (
        f"{day}\n\nWrite a short (2-4 sentences), first-person post for the village tavern "
        "board about your day — evocative and in your own voice. "
        'Reply ONLY with JSON: {"title": "<a short title>", "body": "<the post>"}.'
    )
    def _broke_character(text: str) -> bool:
        low = text.lower()
        return "language model" in low or "an ai" in low or "i cannot" in low

    # The model is a bit variable — give it two tries before the heuristic fallback.
    for _ in range(2):
        try:
            raw = llm.complete(system, user)
        except Exception:  # noqa: BLE001
            break
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group(0))
                body = (obj.get("body") or "").strip()
                if body and not _broke_character(body):
                    return (obj.get("title") or f"{persona.name}'s day").strip(), body
            except json.JSONDecodeError:
                pass
        if raw and not _broke_character(raw):
            # Non-JSON but in-character prose — use it directly (strip code fences).
            return f"{persona.name}'s day of {job}", raw.strip("`").removeprefix("json").strip()
    return compose_post(persona, episodes, job)


def post_day(agent, *, job: str = "adventurer", board: str = "tavern",
             client: ForumClient | None = None, llm=None) -> dict[str, Any] | None:
    """Compose and post `agent`'s day to the forum. Uses the LLM composer when an
    `llm` client is given (in-character prose), else the heuristic. Returns the
    response, or None if the forum isn't configured / posting failed."""
    client = client or ForumClient()
    if not client.configured:
        return None
    eps = agent.episodes.recent(50)
    if llm is not None:
        title, content = compose_post_llm(llm, agent.persona, eps, job=job)
    else:
        title, content = compose_post(agent.persona, eps, job=job)
    try:
        return client.post(board, title, content)
    except Exception:  # noqa: BLE001 — a forum hiccup shouldn't crash the village
        return None
