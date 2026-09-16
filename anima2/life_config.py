"""Portable Life thresholds, with profession identity outside the knob namespace."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Mapping


def life_class(role: str) -> type:
    from .carpenter_life import CarpenterLife
    from .mage_life import MageLife
    from .tinker_life import TinkerLife
    from .warrior_life import WarriorLife
    from .woodsman_life import WoodsmanLife

    classes = {"swordsman": WarriorLife, "mage": MageLife, "woodsman": WoodsmanLife,
               "carpenter": CarpenterLife, "tinker": TinkerLife}
    if type(role) is not str or role not in classes:
        raise ValueError(f"unknown Life role {role!r}; choose from {sorted(classes)}")
    return classes[role]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate profile field {key!r}")
        result[key] = value
    return result


@dataclass(frozen=True)
class LifeProfile:
    role: str
    knobs: Mapping[str, int]

    def __post_init__(self):
        cls = life_class(self.role)
        if not isinstance(self.knobs, Mapping):
            raise ValueError("profile knobs must be an object")
        values = dict(self.knobs)
        for key, value in values.items():
            if type(key) is not str or key not in cls.KNOBS:
                raise ValueError(f"{self.role}: {key!r} is not a Life knob")
            if type(value) is not int or value < 0:
                raise ValueError(f"{key}: expected a non-negative integer")
        object.__setattr__(self, "knobs", MappingProxyType(values))

    def as_dict(self) -> dict:
        return {"schema": 1, "role": self.role, "knobs": dict(self.knobs)}

    @classmethod
    def load(cls, path) -> "LifeProfile":
        raw = Path(path).read_text(encoding="utf-8")
        if len(raw) > 65536:
            raise ValueError("Life profile exceeds 64 KiB")
        data = json.loads(raw, object_pairs_hook=_unique_object)
        if (type(data) is not dict or set(data) != {"schema", "role", "knobs"}
                or type(data["schema"]) is not int or data["schema"] != 1):
            raise ValueError("expected Life profile schema 1 with role and knobs")
        return cls(data["role"], data["knobs"])

    def save(self, path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                              prefix=f".{path.name}.", delete=False) as out:
                temporary = Path(out.name)
                out.write(json.dumps(self.as_dict(), indent=2) + "\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
