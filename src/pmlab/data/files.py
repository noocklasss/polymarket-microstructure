"""Recording files: plain ``.ndjson`` or gzip-compressed ``.ndjson.gz`` (the bundled sample is compressed)."""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import IO

SUFFIXES = (".ndjson.gz", ".ndjson")


def open_recording(path, encoding: str = "utf-8") -> IO[str]:
    """Open a recording for reading as text, whether or not it is gzip-compressed."""
    path = Path(path)
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding=encoding)
    return path.open(encoding=encoding)


def market_slug(path) -> str:
    """'data/btc_5m_data/btc-updown-5m-1794027600.ndjson.gz' -> 'btc-updown-5m-1794027600'."""
    name = Path(path).name
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return Path(path).stem
