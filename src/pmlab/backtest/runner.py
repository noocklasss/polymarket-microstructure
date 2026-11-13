"""Run a strategy over many recorded markets in parallel, one row per market.

A market is used only if it is inside the research window, its result is final, and its recording passes the quality
audit (fresh books on both sides for at least 80% of the market). The reasons markets were left out are counted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from pmlab.backtest.resting_engine import EngineConfig, load_events, run_market
from pmlab.data.dataset_policy import eligible
from pmlab.data.files import market_slug
from pmlab.data.outcomes import winner_of
from pmlab.data.quality import audit_log


@dataclass(frozen=True)
class Job:
    path: Path
    start: int
    strategy: type
    params: dict
    config: EngineConfig
    check_quality: bool = True


_OUTCOMES: dict = {}


def _init(outcomes: dict) -> None:
    global _OUTCOMES
    _OUTCOMES = outcomes


def _run(job: Job) -> dict:
    if not eligible(market_slug(job.path)):
        return {"start": job.start, "skip": "before the research window"}
    if job.check_quality:
        try:
            ok = audit_log(job.path, data_policy="fresh_recording_v2")["passes_book_filter"]
        except (ValueError, OSError):
            ok = False
        if not ok:
            return {"start": job.start, "skip": "recording quality"}
    condition_id, events = load_events(job.path, job.start)
    winner = winner_of(_OUTCOMES.get(condition_id))
    if winner is None:
        return {"start": job.start, "skip": "no final result"}
    if not events:
        return {"start": job.start, "skip": "empty recording"}
    r = run_market(events, winner, job.strategy(**job.params), job.config)
    r.pop("log")
    return {"start": job.start, **r}


def market_files(data_dir: Path, family_dir: str) -> dict[int, Path]:
    folder = Path(data_dir) / family_dir
    paths = [*folder.glob("*-updown-*.ndjson"), *folder.glob("*-updown-*.ndjson.gz")]
    return {int(market_slug(p).rsplit("-", 1)[1]): p for p in paths}


def run_many(
    files: dict[int, Path],
    starts,
    strategy: type,
    params: dict,
    outcomes: dict,
    config: EngineConfig | None = None,
    workers: int = 8,
    check_quality: bool = True,
):
    """Return ``(rows, skipped)``: one row per market run, and a Counter of why markets were left out."""
    config = config or EngineConfig()
    jobs = [Job(files[s], s, strategy, params, config, check_quality) for s in sorted(starts)]
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(outcomes,)) as pool:
        results = list(pool.map(_run, jobs, chunksize=16))
    skipped = Counter(r["skip"] for r in results if "skip" in r)
    rows = pd.DataFrame([r for r in results if "skip" not in r])
    return rows, skipped
