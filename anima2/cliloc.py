"""Cliloc resolution — turn localized message ids (0xC1/0xCC) into English text.

anima-core stays zero-dep and only carries the cliloc *id* + raw args on a
`JournalEntry`; the big `Cliloc.enu` table lives with the client data, so
resolution happens here (brain-side), reading the user's UO install.

`Cliloc.enu` format: 6-byte header, then records of
`[number:int32 LE][flag:byte][length:int16 LE][text: UTF-8 * length]`.
Base strings contain `~1_LABEL~`-style placeholders filled from the tab-separated
args (by the embedded index, matching ClassicUO's ClilocLoader).
"""

from __future__ import annotations

import re
import struct
from functools import lru_cache
from pathlib import Path

DEFAULT_CLILOC = Path.home() / "dev" / "uo" / "uo-resource" / "Cliloc.enu"
_PLACEHOLDER = re.compile(r"~(\d+)(?:_[^~]*)?~")


@lru_cache(maxsize=1)
def _table(path: str | None = None) -> dict[int, str]:
    p = Path(path) if path else DEFAULT_CLILOC
    data = p.read_bytes()
    table: dict[int, str] = {}
    i = 6  # skip header
    n = len(data)
    while i + 7 <= n:
        num = struct.unpack_from("<i", data, i)[0]
        length = struct.unpack_from("<h", data, i + 5)[0]
        i += 7
        if length < 0 or i + length > n:
            break
        table[num] = data[i : i + length].decode("utf-8", errors="replace")
        i += length
    return table


def resolve(cliloc: int, args: str = "", *, path: str | None = None) -> str:
    """Resolve a cliloc id + tab-separated args to display text.

    Falls back to ``[cliloc N]`` if the id is unknown or the table is missing.
    """
    try:
        base = _table(path).get(cliloc)
    except (OSError, struct.error):
        base = None
    if base is None:
        return f"[cliloc {cliloc}]" + (f" {args}" if args else "")

    parts = args.split("\t") if args else []

    def _sub(m: re.Match[str]) -> str:
        idx = int(m.group(1)) - 1
        return parts[idx] if 0 <= idx < len(parts) else ""

    return _PLACEHOLDER.sub(_sub, base).strip()


def resolve_entry(entry) -> str:
    """Display text for a `JournalEntry`: resolve clilocs, pass plain speech through."""
    if getattr(entry, "cliloc", 0):
        return resolve(entry.cliloc, entry.text)
    return entry.text
