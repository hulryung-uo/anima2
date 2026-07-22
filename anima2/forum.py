"""Forum posting — agents write about their day on the uotavern board.

Part of the "characters living in Britannia" loop (DESIGN.md §1): an agent turns
its episodic memory of the day's work into a short in-character post and shares it
on the companion forum (uotavern). A heuristic composer works offline; an LLM
composer (the slow cognition loop) can write richer prose.

The API key is read from `ANIMA_FORUM_API_KEY` or anima v1's `config.yaml`
(`forum.api_key`) — never hard-coded here.

PHASE6.md item 3 ("the forum as continuing chronicle") makes a post actually
*continuing* rather than an isolated daily blurb: `compose_post`/
`compose_post_llm` gain two optional, additive parameters — `yesterday` (a
single persisted `Insight`'s text, item 1's `memory.py::load_insights`) and
`chronicle_events` (this persona's own confirmed trade/market/hunt events this
session, item 2's `chronicle.py::ChronicleEvent`). Both default to `None`,
reproducing today's exact prompt/output byte-for-byte — the same "optional
collaborator, no-op by default" shape every prior phase's additions use.

The `chronicle_events` grounding sentence is always CODE-composed
(`_chronicle_grounding_line`, below) and spliced into the prompt/heuristic
body *before* any LLM call — the LLM only ever turns an already-true fact
into prose, exactly `cognition.py::LLMWikiReportProducer`'s `ReportDraft.page`
discipline (Phase 4 item 1): it is never the source of *which* event
happened or *who* it was with, so a hallucinated interaction partner is
structurally impossible to reach a post.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .chronicle import ChronicleEvent

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

# How each chronicle event `kind` reads in a first-person grounding sentence
# ("You {verb} today.") — `{who}` is substituted with the event's own
# `to_persona` for the two strictly agent-to-agent kinds; the three
# agent-to-world kinds (PHASE6.md item 2's `to_persona=None` events) have no
# `{who}` slot. An unrecognized future `kind` falls back to its own
# underscore-to-space spelling rather than a KeyError.
_CHRONICLE_VERB = {
    "delivered_ingots": "delivered ingots to {who}",
    "picked_up_ingots": "picked up ingots from {who}",
    "sold_to_vendor": "sold goods to a vendor",
    "banked_gold": "banked gold",
    "crafted_daggers": "crafted daggers",
    "looted_corpse": "looted a corpse",
}


def _times(n: int) -> str:
    if n <= 1:
        return ""
    if n == 2:
        return " twice"
    return f" {n} times"


def _chronicle_grounding_line(persona_name: str, chronicle_events: list[ChronicleEvent] | None) -> str:
    """A short, CODE-composed factual sentence naming this persona's OWN
    confirmed chronicle events this session — see the module docstring for
    why this is never LLM-sourced. Tallies events by `(kind, to_persona)` (in
    first-seen order, so the sentence is deterministic for a given event
    list) and reports a count only when it's more than one (e.g. "delivered
    ingots to Tormund3 twice"). Only events this persona is the ACTOR in
    (`from_persona == persona_name` — `village.py`'s worker thread always
    queues an event under the agent whose own tick detected it, so this is
    always "what I did", never "what happened to me") are considered.

    Returns `""` — the byte-for-byte no-op case — for `None`/an empty list,
    or a list with nothing this persona actually did.
    """
    if not chronicle_events:
        return ""
    mine = [e for e in chronicle_events if e.from_persona == persona_name]
    if not mine:
        return ""
    counts: dict[tuple[str, str | None], int] = {}
    for e in mine:
        key = (e.kind, e.to_persona)
        counts[key] = counts.get(key, 0) + 1
    phrases = []
    for (kind, to_persona), n in counts.items():
        template = _CHRONICLE_VERB.get(kind, kind.replace("_", " "))
        phrase = template.format(who=to_persona) if "{who}" in template else template
        phrases.append(phrase + _times(n))
    if len(phrases) == 1:
        return f"You {phrases[0]} today."
    return "You " + "; ".join(phrases) + " today."


def compose_post(persona, episodes, job: str = "adventurer", *,
                  yesterday: str | None = None,
                  chronicle_events: list[ChronicleEvent] | None = None) -> tuple[str, str]:
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
    grounding = _chronicle_grounding_line(persona.name, chronicle_events)
    if grounding:
        body += f" {grounding}"
    if yesterday:
        body += f" Yesterday I noted: {yesterday}"
    body += f"\n\n— {persona.name}, {persona.title or job}"
    return title, body


def compose_post_llm(llm, persona, episodes, job: str = "adventurer", *,
                      yesterday: str | None = None,
                      chronicle_events: list[ChronicleEvent] | None = None) -> tuple[str, str]:
    """LLM-written, in-character forum post. Falls back to the heuristic on any
    failure (bad/empty response, parse error, broken character) — the fallback
    threads `yesterday`/`chronicle_events` through to `compose_post` too, so
    the "continuing chronicle" property holds even off the LLM path."""
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
    grounding = _chronicle_grounding_line(persona.name, chronicle_events)
    if grounding:
        day += f" {grounding}"
    if yesterday:
        day += f" Yesterday you noted: {yesterday}"
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
    return compose_post(persona, episodes, job, yesterday=yesterday, chronicle_events=chronicle_events)


#: `data/forum_log.jsonl` relative to the process's cwd — mirrors
#: `chronicle.py`'s `_DEFAULT_CHRONICLE_LEDGER`/`memory.py`'s
#: `_DEFAULT_...` convention exactly (created lazily, gitignored; falls under
#: the repo's blanket `data/` ignore). Tests must always pass an explicit
#: `forum_log_path=` (a `tmp_path`) so the suite never touches the real file.
_DEFAULT_FORUM_LOG = Path("data") / "forum_log.jsonl"

#: Guards the actual disk write inside `_log_forum_post` — mirrors
#: `chronicle.py::_chronicle_log_lock`/`memory.py::_insights_log_lock`
#: exactly (module-level: several agents' worker threads could post around
#: the same wall-clock moment in a multi-agent `village.py` roster).
_forum_log_lock = threading.Lock()


def _log_forum_post(path: str | Path | None, *, persona: str, job: str, title: str,
                     content: str, remote_ok: bool) -> None:
    """Append one `{ts, persona, job, title, content, remote_ok}` JSON line —
    PHASE6.md item 3's local, cross-process-readable mirror of every
    ATTEMPTED post (whether or not the remote `client.post()` call itself
    succeeded), recorded before `post_day` returns. A write failure degrades
    silently, matching every other ledger in this codebase — a logging
    hiccup must never be the thing that breaks a village run.
    """
    target = Path(path) if path is not None else _DEFAULT_FORUM_LOG
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "persona": persona, "job": job, "title": title, "content": content, "remote_ok": remote_ok,
    }
    try:
        with _forum_log_lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a") as f:
                f.write(json.dumps(record) + "\n")
    except OSError:
        pass  # degrade silently — matches skill_library.py's own logging discipline


def post_day(agent, *, job: str = "adventurer", board: str = "tavern",
             client: ForumClient | None = None, llm=None,
             yesterday: str | None = None,
             chronicle_events: list[ChronicleEvent] | None = None,
             forum_log_path: str | Path | None = None) -> dict[str, Any] | None:
    """Compose and post `agent`'s day to the forum. Uses the LLM composer when an
    `llm` client is given (in-character prose), else the heuristic. `yesterday`/
    `chronicle_events` (PHASE6.md item 3, both optional) ground the post in a
    persisted insight and this session's own confirmed chronicle events — see
    the module docstring. Returns the response, or None if the forum isn't
    configured / posting failed.

    Every ATTEMPT (i.e. every call that gets far enough to actually compose a
    post — `client.configured` is true) is recorded to `data/forum_log.jsonl`
    (`forum_log_path` overrides, mainly for isolated test/live runs) BEFORE
    returning, `remote_ok` reflecting whether `client.post()` itself
    succeeded — a local, cross-process-readable record independent of any
    forum-side history-read API (`ForumClient` has none today).
    """
    client = client or ForumClient()
    if not client.configured:
        return None
    eps = agent.episodes.recent(50)
    if llm is not None:
        title, content = compose_post_llm(llm, agent.persona, eps, job=job,
                                          yesterday=yesterday, chronicle_events=chronicle_events)
    else:
        title, content = compose_post(agent.persona, eps, job=job,
                                      yesterday=yesterday, chronicle_events=chronicle_events)
    try:
        result = client.post(board, title, content)
        remote_ok = True
    except Exception:  # noqa: BLE001 — a forum hiccup shouldn't crash the village
        result = None
        remote_ok = False
    _log_forum_post(forum_log_path, persona=agent.persona.name, job=job, title=title,
                    content=content, remote_ok=remote_ok)
    return result
