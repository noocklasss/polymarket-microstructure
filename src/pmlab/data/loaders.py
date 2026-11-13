"""Load a recorded market (one NDJSON file) into a per-second, as-of book array.

The array has shape ``(duration, 2, 5)``: for each whole second ``t`` of the market and each side (0 = UP, 1 = DOWN),
the last book seen at or before ``t`` as ``[best_bid, best_ask, bid_size, ask_size, seen_at]``. Rows are applied in
time order and only up to ``t``, so a later row can never change what the array says about an earlier second: the
array is free of look-ahead by construction.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from pmlab.data.dataset_policy import eligible, market_start

SIDES = ("UP", "DOWN")
BOOK_FIELDS = ("bid", "ask", "bid_size", "ask_size", "stamp")
BID, ASK, BID_SIZE, ASK_SIZE, STAMP = range(5)


def load_books(path: Path, preserve_empty: bool = False, duration_seconds: int | None = None):
    """Return ``(meta, books)`` for one recorded market file.

    ``preserve_empty`` keeps explicitly empty levels (size 0) as observed states: an empty bid becomes price 0 and an
    empty ask price 1, with size 0 so that no simulated fill can happen there. Without it, any row that does not form
    a valid book is recorded as unknown (NaN).
    """
    path = Path(path)
    start = market_start(path.stem)
    if not eligible(path.stem):
        raise ValueError("Outside the permitted dataset")
    duration = int(duration_seconds or 300)
    meta, events = {}, []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") == "meta":
                meta.update(row)
            if row.get("type") != "book" or row.get("side") not in ("up", "down"):
                continue
            try:
                ts = float(row["ts"])
                ts = ts / 1000 if ts > 1e10 else ts
                t = ts - start
                if not -duration <= t < duration:
                    continue
                side = 0 if row["side"] == "up" else 1
                bid, ask = float(row["best_bid"]), float(row["best_ask"])
                bid_size, ask_size = float(row.get("best_bid_size", 0)), float(row.get("best_ask_size", 0))
                if preserve_empty:
                    if "best_bid_size" not in row or "best_ask_size" not in row:
                        bid_size = ask_size = np.nan
                    if bid_size == 0:
                        bid = 0.0
                    if ask_size == 0:
                        ask = 1.0
                if not (0 <= bid <= ask <= 1 and ask > 0 and bid_size >= 0 and ask_size >= 0):
                    bid = ask = bid_size = ask_size = np.nan
                events.append((t, side, [bid, ask, bid_size, ask_size, t]))
            except (KeyError, ValueError, TypeError):
                continue
    books = np.full((duration, 2, 5), np.nan)
    current = np.full((2, 5), np.nan)
    events.sort(key=lambda event: event[0])
    index = 0
    for t in range(duration):
        while index < len(events) and events[index][0] <= t:
            _, side, current[side] = events[index]
            index += 1
        books[t] = current
    return meta, books


def quote_valid(book, t: float, max_age: float) -> bool:
    """A buyable quote at second ``t``: a sane book, seen within ``max_age`` seconds, with shares on the ask."""
    return bool(
        np.isfinite(book).all()
        and 0 <= book[BID] <= book[ASK] <= 1
        and book[ASK] > 0
        and 0 <= t - book[STAMP] <= max_age
        and book[ASK_SIZE] > 0
    )


def quote_recorded(book, t: float, max_age: float) -> bool:
    """A fresh recorded quote at second ``t``; unlike :func:`quote_valid`, an empty ask still counts."""
    return bool(
        np.isfinite(book).all()
        and 0 <= book[BID] <= book[ASK] <= 1
        and book[BID_SIZE] >= 0
        and book[ASK_SIZE] >= 0
        and 0 <= t - book[STAMP] <= max_age
    )
