"""Recording-quality audit: how many seconds of a recorded market have a fresh, usable book on both sides."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from pmlab.data.loaders import ASK_SIZE, STAMP, load_books, quote_recorded, quote_valid

COIN_NAMES = {"xau": "gold", "xag": "silver"}
TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600}
SLUG_RE = re.compile(r"^([a-z0-9]+)-updown-(\d+[mh])-", re.I)

POLICY_BUYABLE = "buyable_both_v1"  # both sides must have shares on the ask
POLICY_RECORDED = "fresh_recording_v2"  # fresh book on both sides; explicitly empty levels count


def family_and_duration(path) -> tuple[str, int]:
    """'btc-updown-5m-...' -> ('btc_5m', 300); 'xau-updown-15m-...' -> ('gold_15m', 900)."""
    stem = Path(path).stem
    found = SLUG_RE.match(stem)
    if found:
        coin, tf = found.group(1).lower(), found.group(2).lower()
        coin = COIN_NAMES.get(coin, coin)
        duration = TF_SECONDS.get(tf)
        if duration:
            return f"{coin}_{tf}", duration
    # Renamed files: fall back to the folder name and the 5-minute duration.
    return Path(path).parent.name.replace("_data", ""), 300


def longest_gap(valid) -> int:
    """Length of the longest run of False values."""
    longest = current = 0
    for good in valid:
        current = 0 if good else current + 1
        longest = max(longest, current)
    return longest


def audit_log(
    path,
    max_age: float = 5,
    minimum_coverage: float = 0.8,
    data_policy: str = POLICY_BUYABLE,
    duration_seconds: int | None = None,
) -> dict:
    """Score one recorded market file. ``passes_book_filter`` says whether research may use it."""
    path = Path(path)
    family, inferred = family_and_duration(path)
    duration = int(duration_seconds or inferred)
    allow_empty = data_policy == POLICY_RECORDED
    _, books = load_books(path, preserve_empty=allow_empty, duration_seconds=duration)
    valid = np.array([[quote_valid(books[t, side], t, max_age) for side in range(2)] for t in range(duration)])
    both = valid.all(axis=1)
    ages = np.arange(duration)[:, None] - books[:, :, STAMP]
    fresh = np.isfinite(ages) & (ages >= 0) & (ages <= max_age)
    captured = fresh.all(axis=1)
    recorded = np.array([[quote_recorded(books[t, s], t, max_age) for s in range(2)] for t in range(duration)]).all(
        axis=1
    )
    selected = recorded if allow_empty else both
    coverage = float(selected.mean())
    no_asks = fresh & np.isfinite(books[:, :, ASK_SIZE]) & (books[:, :, ASK_SIZE] == 0)
    return {
        "slug": path.stem,
        "family": family,
        "duration_seconds": duration,
        "fresh_both_seconds": int(selected.sum()),
        "coverage": coverage,
        "buyable_both_coverage": float(both.mean()),
        "data_policy": data_policy,
        "fresh_recording_coverage": float(captured.mean()),
        "fresh_but_empty_ask_seconds": int(no_asks.any(axis=1).sum()),
        "stale_or_missing_recording_seconds": int((~captured).sum()),
        "up_coverage": float(valid[:, 0].mean()),
        "down_coverage": float(valid[:, 1].mean()),
        "longest_invalid_gap_seconds": longest_gap(selected),
        "first_valid_second": int(np.flatnonzero(selected)[0]) if selected.any() else None,
        "last_valid_second": int(np.flatnonzero(selected)[-1]) if selected.any() else None,
        "max_quote_age_seconds": max_age,
        "minimum_coverage": minimum_coverage,
        "passes_book_filter": coverage >= minimum_coverage,
        "healthy_capture": float(captured.mean()) >= 0.95,
    }


def save_log_quality(
    path, output_root: Path, max_age: float = 5, minimum_coverage: float = 0.8, data_policy: str = POLICY_RECORDED
) -> dict:
    """Audit ``path`` and write ``<output_root>/<family>_logger_quality/<slug>.json`` and ``latest.json`` atomically."""
    report = audit_log(path, max_age, minimum_coverage, data_policy)
    folder = Path(output_root) / f"{report['family']}_logger_quality"
    folder.mkdir(parents=True, exist_ok=True)
    for filename in (report["slug"] + ".json", "latest.json"):
        destination = folder / filename
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(destination)
    return report
