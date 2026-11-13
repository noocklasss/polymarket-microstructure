"""Incremental build: find which (family, day) partitions are new or changed, and re-extract only those.

A manifest (``<warehouse>/_manifest.json``) remembers, for every partition, the name, size and modification time of
each recording it was built from. A partition is rebuilt when that list changes (a new market finished, a file grew);
otherwise it is skipped, so running the build twice in a row does nothing the second time. Markets that have not
finished yet (end + ``grace`` seconds still in the future) are left for the next run. The manifest is written after
the partitions, so an interrupted run is simply redone next time.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from pmlab.data.dataset_policy import eligible, market_start
from pmlab.data.files import SUFFIXES, market_slug
from pmlab.data.quality import family_and_duration
from pmlab.pipeline.extract import TABLES, market_day, write_partition

MANIFEST = "_manifest.json"


@dataclass
class BuildReport:
    built: dict[str, dict[str, int]] = field(default_factory=dict)  # "family/day" -> rows per table
    unchanged: int = 0
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    seconds: float = 0.0


def discover(
    data_dir: Path,
    families: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    now: float | None = None,
    grace: float = 120,
    skipped: dict[str, int] | None = None,
) -> dict[tuple[str, str], list[Path]]:
    """Finished, in-window recordings under ``data_dir/<family>_data/``, grouped by (family, day)."""
    now = time.time() if now is None else now
    skipped = defaultdict(int) if skipped is None else skipped
    groups: dict[tuple[str, str], list[Path]] = defaultdict(list)
    folders = sorted(Path(data_dir).glob("*_data"))
    for folder in folders:
        family = folder.name.removesuffix("_data")
        if families and family not in families:
            continue
        for path in folder.iterdir():
            if not path.name.endswith(SUFFIXES) or "-updown-" not in path.name:
                continue
            slug = market_slug(path)
            start = market_start(slug)
            if start is None or not eligible(slug):
                skipped["before the research window"] += 1
                continue
            _, duration = family_and_duration(path)
            if start + duration + grace > now:
                skipped["not finished yet"] += 1
                continue
            day = market_day(start)
            if (since and day < since) or (until and day > until):
                continue
            groups[(family, day)].append(path)
    return dict(groups)


def _signature(paths: list[Path]) -> list[list]:
    return [[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in sorted(paths)]


def _build_one(job: tuple[str, str, str, list[str]]) -> tuple[str, dict[str, int]]:
    warehouse, family, day, paths = job
    return f"{family}/{day}", write_partition(Path(warehouse), family, day, [Path(p) for p in paths])


def build(
    data_dir: Path,
    warehouse: Path,
    families: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    workers: int = 4,
    full: bool = False,
    now: float | None = None,
) -> BuildReport:
    """Bring the Parquet partitions up to date with the recordings. ``full`` rebuilds every partition found."""
    t0 = time.time()
    warehouse = Path(warehouse)
    warehouse.mkdir(parents=True, exist_ok=True)
    manifest_path = warehouse / MANIFEST
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    report = BuildReport()
    groups = discover(data_dir, families, since, until, now=now, skipped=report.skipped)
    jobs = []
    signatures = {}
    for (family, day), paths in sorted(groups.items()):
        key = f"{family}/{day}"
        signatures[key] = _signature(paths)
        partitions_exist = all(
            (warehouse / t / f"family={family}" / f"day={day}" / "part.parquet").exists() for t in TABLES
        )
        if not full and partitions_exist and manifest.get(key) == signatures[key]:
            report.unchanged += 1
            continue
        jobs.append((str(warehouse), family, day, [str(p) for p in paths]))
    if jobs:
        if workers > 1 and len(jobs) > 1:
            with ProcessPoolExecutor(min(workers, len(jobs))) as pool:
                results = list(pool.map(_build_one, jobs))
        else:
            results = [_build_one(job) for job in jobs]
        for key, counts in results:
            report.built[key] = counts
            manifest[key] = signatures[key]
        tmp = manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, indent=0, sort_keys=True))
        os.replace(tmp, manifest_path)
    report.seconds = time.time() - t0
    return report
