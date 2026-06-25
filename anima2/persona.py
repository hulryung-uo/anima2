"""Persona — identity, profession, and sociability that shape goals and voice.

Loads the same YAML format as anima v1 (``../anima/personas/*.yaml``) so those
hand-authored personas are reused directly (DESIGN.md §8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Persona:
    name: str
    title: str = ""
    background: str = ""
    personality: str = ""
    speech_style: str = ""
    interests: str = ""
    dislikes: str = ""
    # 0.0 (silent) .. 1.0 (chatty) — the measured sociability axis.
    talkativeness: float = 0.3
    combat_disposition: str = "neutral"
    # Anything else in the YAML is preserved here.
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Persona:
        known = set(cls.__dataclass_fields__) - {"extra"}
        extra = {k: v for k, v in d.items() if k not in known}
        return cls(
            name=d.get("name", "Anima"),
            title=d.get("title", ""),
            background=d.get("background", ""),
            personality=d.get("personality", ""),
            speech_style=d.get("speech_style", ""),
            interests=d.get("interests", ""),
            dislikes=d.get("dislikes", ""),
            talkativeness=float(d.get("talkativeness", 0.3)),
            combat_disposition=d.get("combat_disposition", "neutral"),
            extra=extra,
        )

    @classmethod
    def load(cls, path: str | Path) -> Persona:
        data = yaml.safe_load(Path(path).read_text())
        return cls.from_dict(data or {})
