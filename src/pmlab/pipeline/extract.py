"""Extract: recorded NDJSON files -> one Parquet file per table, market family and day.

Each recording is read once and split into five tables:

=============  =====================================================================================================
``markets``    one row per market: slug, start, duration, condition id, tokens, and what the file contained
``trades``     every trade print: time, side, taker side, price, size
``book``       every top-of-book change of one side (the ``book`` rows): best bid / ask and their sizes
``snapshots``  the 1-second snapshot rows: both sides' top of book and 5 levels of depth
``resyncs``    every time the recorder rebuilt a book from a REST snapshot, and why
=============  =====================================================================================================

Every event row keeps ``seq`` (its line number in the file) and ``t`` (seconds since the market start, computed exactly
as :func:`pmlab.data.loaders.load_books` does), so the SQL models can replay events in the recorded order.

A partition is ``<warehouse>/<table>/family=<family>/day=<YYYY-MM-DD>/part.parquet`` (the day is the market's start
date in New York). Files are written to a temporary name and then renamed, so a partition is never half-written. The
recordings themselves are only read, never changed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from pmlab.data.dataset_policy import MARKET_TIMEZONE, market_start
from pmlab.data.files import market_slug, open_recording
from pmlab.data.quality import family_and_duration

LEVELS = pa.list_(pa.struct([("price", pa.float64()), ("size", pa.float64())]))

SCHEMAS: dict[str, pa.Schema] = {
    "markets": pa.schema(
        [
            ("slug", pa.string()),
            ("start", pa.int64()),
            ("duration_s", pa.int32()),
            ("condition_id", pa.string()),
            ("question", pa.string()),
            ("up_token", pa.string()),
            ("down_token", pa.string()),
            ("tick_size", pa.float64()),
            ("min_order_size", pa.float64()),
            ("file_name", pa.string()),
            ("file_bytes", pa.int64()),
            ("lines", pa.int32()),
            ("bad_lines", pa.int32()),
        ]
    ),
    "trades": pa.schema(
        [
            ("slug", pa.string()),
            ("seq", pa.int32()),
            ("ts_ms", pa.int64()),
            ("t", pa.float64()),
            ("side", pa.string()),
            ("taker_side", pa.string()),
            ("price", pa.float64()),
            ("size", pa.float64()),
            ("fee_bps", pa.float64()),
        ]
    ),
    "book": pa.schema(
        [
            ("slug", pa.string()),
            ("seq", pa.int32()),
            ("ts_ms", pa.int64()),
            ("t", pa.float64()),
            ("side", pa.string()),
            ("best_bid", pa.float64()),
            ("best_ask", pa.float64()),
            ("best_bid_size", pa.float64()),
            ("best_ask_size", pa.float64()),
            ("bid_levels", pa.int32()),
            ("ask_levels", pa.int32()),
        ]
    ),
    "snapshots": pa.schema(
        [
            ("slug", pa.string()),
            ("seq", pa.int32()),
            ("ts_ms", pa.int64()),
            ("t", pa.float64()),
            *[
                (f"{side}_{name}", kind)
                for side in ("up", "down")
                for name, kind in (
                    ("bid", pa.float64()),
                    ("ask", pa.float64()),
                    ("bid_size", pa.float64()),
                    ("ask_size", pa.float64()),
                    ("age_ms", pa.float64()),
                    ("bids", LEVELS),
                    ("asks", LEVELS),
                )
            ],
        ]
    ),
    "resyncs": pa.schema(
        [
            ("slug", pa.string()),
            ("seq", pa.int32()),
            ("ts_ms", pa.int64()),
            ("t", pa.float64()),
            ("side", pa.string()),
            ("reason", pa.string()),
        ]
    ),
}
TABLES = tuple(SCHEMAS)


def market_day(start: float) -> str:
    """The New York calendar date a market starts on: the partition it belongs to."""
    return datetime.fromtimestamp(start, MARKET_TIMEZONE).date().isoformat()


def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _levels(rows) -> list[dict] | None:
    if not isinstance(rows, list):
        return None
    out = []
    for level in rows:
        if isinstance(level, (list, tuple)) and len(level) == 2:
            out.append({"price": _num(level[0]), "size": _num(level[1])})
    return out


def parse_recording(path: Path) -> dict[str, list[dict]]:
    """All rows of one recording, by table. Lines that are not valid JSON are counted in ``markets.bad_lines``."""
    path = Path(path)
    slug = market_slug(path)
    start = market_start(slug)
    if start is None:
        raise ValueError(f"cannot read the market start from {path.name}")
    _, duration = family_and_duration(path)
    rows: dict[str, list[dict]] = {name: [] for name in TABLES}
    meta: dict = {}
    lines = bad = 0
    with open_recording(path) as handle:
        for seq, line in enumerate(handle):
            lines += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            kind = row.get("type")
            if kind == "meta":
                meta.update(row)
                continue
            ts = _num(row.get("ts"))
            if ts is None:
                continue
            ts_s = ts / 1000 if ts > 1e10 else ts
            base = {"slug": slug, "seq": seq, "ts_ms": int(round(ts_s * 1000)), "t": ts_s - start}
            if kind == "trade":
                rows["trades"].append(
                    base
                    | {
                        "side": row.get("side"),
                        "taker_side": row.get("taker_side"),
                        "price": _num(row.get("price")),
                        "size": _num(row.get("size")),
                        "fee_bps": _num(row.get("fee_bps")),
                    }
                )
            elif kind == "book":
                rows["book"].append(
                    base
                    | {
                        "side": row.get("side"),
                        "best_bid": _num(row.get("best_bid")),
                        "best_ask": _num(row.get("best_ask")),
                        "best_bid_size": _num(row.get("best_bid_size")),
                        "best_ask_size": _num(row.get("best_ask_size")),
                        "bid_levels": _int(row.get("bid_levels")),
                        "ask_levels": _int(row.get("ask_levels")),
                    }
                )
            elif kind == "snapshot":
                snap = dict(base)
                for side in ("up", "down"):
                    part = row.get(side) if isinstance(row.get(side), dict) else {}
                    snap |= {
                        f"{side}_bid": _num(part.get("bid")),
                        f"{side}_ask": _num(part.get("ask")),
                        f"{side}_bid_size": _num(part.get("bid_size")),
                        f"{side}_ask_size": _num(part.get("ask_size")),
                        f"{side}_age_ms": _num(row.get(f"{side}_age_ms")),
                        f"{side}_bids": _levels(part.get("bids")),
                        f"{side}_asks": _levels(part.get("asks")),
                    }
                rows["snapshots"].append(snap)
            elif kind == "resync":
                rows["resyncs"].append(base | {"side": row.get("side"), "reason": row.get("reason")})
    rows["markets"].append(
        {
            "slug": slug,
            "start": int(start),
            "duration_s": duration,
            "condition_id": meta.get("condition_id"),
            "question": meta.get("question"),
            "up_token": meta.get("yes_token_id"),
            "down_token": meta.get("no_token_id"),
            "tick_size": _num(meta.get("tick_size")),
            "min_order_size": _num(meta.get("minimum_order_size")),
            "file_name": path.name,
            "file_bytes": path.stat().st_size,
            "lines": lines,
            "bad_lines": bad,
        }
    )
    return rows


def write_partition(warehouse: Path, family: str, day: str, paths: Iterable[Path]) -> dict[str, int]:
    """Parse ``paths`` (one family, one day) and replace that day's partition of every table. Returns row counts.

    Each recording is written as soon as it is parsed (one row group per market), so memory holds one market at a time.
    """
    folders = {name: Path(warehouse) / name / f"family={family}" / f"day={day}" for name in TABLES}
    writers = {}
    counts = dict.fromkeys(TABLES, 0)
    try:
        for name, folder in folders.items():
            folder.mkdir(parents=True, exist_ok=True)
            writers[name] = pq.ParquetWriter(folder / "part.parquet.tmp", SCHEMAS[name], compression="zstd")
        for path in sorted(paths):
            for name, part in parse_recording(path).items():
                if part:
                    writers[name].write_table(pa.Table.from_pylist(part, schema=SCHEMAS[name]))
                    counts[name] += len(part)
    finally:
        for writer in writers.values():
            writer.close()
    for folder in folders.values():
        os.replace(folder / "part.parquet.tmp", folder / "part.parquet")
    return counts
