"""Research universe: only markets that start on or after 1 August 2026, 00:00 America/New_York.

Every fit, parameter search and score must go through this filter first, so that no result mixes in markets from
before the cutoff. A market's start time is read from its slug (``btc-updown-5m-<unix start>``), from explicit start
fields, or, for legacy exports, from an "Up or Down - <Month> <day>" title.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

MARKET_TIMEZONE = ZoneInfo("America/New_York")
CUTOFF = datetime(2026, 8, 1, tzinfo=MARKET_TIMEZONE)
CUTOFF_EPOCH = int(CUTOFF.timestamp())
SLUG = re.compile(r"[a-z0-9]+-updown-\d+[mh]-(\d{10})(?!\d)", re.I)
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

_SLUG_KEYS = ("slug", "market_slug", "eventSlug", "market_session", "event_slug")
_START_KEYS = ("start", "start_ts", "market_start", "start_time", "startTime")
_TEXT_KEYS = ("title", "market_title", "market", "name", "filename", "path")


def _plausible_epoch(raw: Any) -> bool:
    return isinstance(raw, (int, float)) and 1_500_000_000 < raw < 3_000_000_000


def market_start(value: Any) -> float | None:
    """Return the market's start as a unix timestamp, or None when it cannot be determined."""
    if isinstance(value, dict):
        for key in _SLUG_KEYS:
            stamp = market_start(value.get(key))
            if stamp is not None:
                return stamp
        for key in _START_KEYS:
            raw = value.get(key)
            if isinstance(raw, str) and raw.isdigit():
                raw = int(raw)
            if _plausible_epoch(raw):
                return float(raw)
            if isinstance(raw, str) and re.match(r"^20\d\d-\d\d-\d\d", raw):
                try:
                    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return parsed.timestamp()
        # A trade row (it names a market) carries its own timestamp.
        if any(key in value for key in ("conditionId", "condition_id", "asset")):
            raw = value.get("timestamp")
            if _plausible_epoch(raw):
                return float(raw)
        for key in _TEXT_KEYS:
            stamp = market_start(value.get(key))
            if stamp is not None:
                return stamp
        return None
    if not isinstance(value, str):
        return None
    found = SLUG.search(value)
    if found:
        return int(found[1])
    if re.search(r"up[ _-]+or[ _-]+down", value, re.I):
        for month, name in enumerate(MONTHS, 1):
            found = re.search(r"(?:^|[ _-])" + name + r"[ _-]+(\d{1,2})(?!\d)", value, re.I)
            if found:
                year = re.search(r"(?:^|[ _-])(20\d{2})(?:[ _-]|$)", value)
                try:
                    return datetime(
                        int(year[1]) if year else 2026, month, int(found[1]), tzinfo=MARKET_TIMEZONE
                    ).timestamp()
                except ValueError:
                    return None
    return None


def eligible(market: Any) -> bool:
    """True if the market starts on or after the cutoff. Raises if its start cannot be determined (fail closed)."""
    stamp = market_start(market)
    if stamp is None:
        raise ValueError("Market has no trustworthy start date")
    return stamp >= CUTOFF_EPOCH


def retain(books, markets):
    """Keep only eligible markets, filtering a book array and its metadata list with the same mask."""
    import numpy as np

    if len(books) != len(markets):
        raise ValueError("Book/market metadata length mismatch")
    keep = np.array([eligible(m) for m in markets], dtype=bool)
    return books[keep], [m for m, yes in zip(markets, keep, strict=True) if yes]


DROP = object()
"""Sentinel returned by :func:`filter_metadata` for a value that is itself a pre-cutoff market."""


def filter_metadata(value: Any) -> tuple[Any, int]:
    """Recursively remove pre-cutoff markets from nested JSON-like metadata.

    Drops list items and dict entries that are pre-cutoff markets, and dict keys that name one. Returns
    ``(filtered, removed_count)``; ``filtered`` is :data:`DROP` when ``value`` itself is a pre-cutoff market.
    """
    stamp = market_start(value)
    if stamp is not None and stamp < CUTOFF_EPOCH:
        return DROP, 1
    count = 0
    if isinstance(value, list):
        items = []
        for item in value:
            item, n = filter_metadata(item)
            count += n
            if item is not DROP:
                items.append(item)
        return items, count
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            stamp = market_start(key)
            if stamp is not None and stamp < CUTOFF_EPOCH:
                count += 1
                continue
            item, n = filter_metadata(item)
            count += n
            if item is not DROP:
                result[key] = item
        return result, count
    return value, 0
