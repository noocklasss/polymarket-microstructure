"""Command line: ``pmlab families`` lists what can be recorded, ``pmlab record btc_5m`` records one family."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from pathlib import Path

from pmlab.ingestion.recorder import Recorder, load_families


def _setup_logging(log_file: Path | None, verbose: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=handlers,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pmlab", description=__doc__)
    parser.add_argument("--config", type=Path, help="market families TOML (default: the packaged markets.toml)")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("families", help="list the market families that can be recorded")

    record = commands.add_parser("record", help="record every market of one family until stopped (Ctrl+C)")
    record.add_argument("family", help="e.g. btc_5m, eth_15m, gold_1h (see: pmlab families)")
    record.add_argument("--data-dir", type=Path, default=Path("data"), help="where recordings go (default: ./data)")
    record.add_argument("--log-file", type=Path, help="also write the log here")
    record.add_argument("-v", "--verbose", action="store_true")

    pipeline = commands.add_parser("pipeline", help="recordings -> Parquet -> DuckDB models -> data checks")
    pipeline.add_argument("--data-dir", type=Path, default=Path("data"), help="the recordings (default: ./data)")
    pipeline.add_argument("--warehouse", type=Path, default=Path("warehouse"), help="output (default: ./warehouse)")
    pipeline.add_argument("--family", action="append", help="only this family (repeatable), e.g. btc_5m")
    pipeline.add_argument("--since", help="first day to include, YYYY-MM-DD (New York date of the market start)")
    pipeline.add_argument("--until", help="last day to include, YYYY-MM-DD")
    pipeline.add_argument("--outcomes", type=Path, help="JSON of final results {condition_id: [up, down]}")
    pipeline.add_argument("--workers", type=int, default=4)
    pipeline.add_argument("--full", action="store_true", help="rebuild every day, not only new or changed ones")

    sql = commands.add_parser("sql", help='run a SQL query on the warehouse, e.g. "SELECT * FROM daily_summary"')
    sql.add_argument("query")
    sql.add_argument("--warehouse", type=Path, default=Path("warehouse"))

    args = parser.parse_args(argv)
    if args.command == "pipeline":
        return _pipeline(args)
    if args.command == "sql":
        from pmlab.pipeline.warehouse import connect

        with connect(args.warehouse, read_only=True) as con:
            con.sql(args.query).show(max_rows=50, max_width=200)
        return 0
    families = load_families(args.config)

    if args.command == "families":
        for f in families.values():
            print(f"{f.name:<12} {f.slug_prefix}-updown-{f.timeframe}-<start>   heartbeat {f.heartbeat_seconds}s")
        return 0

    if args.family not in families:
        parser.error(f"unknown family {args.family!r}; choose from: {', '.join(families)}")
    _setup_logging(args.log_file, args.verbose)
    with contextlib.suppress(KeyboardInterrupt):  # Ctrl+C is the normal way to stop
        asyncio.run(Recorder(families[args.family], args.data_dir).run())
    return 0


def _pipeline(args) -> int:
    from pmlab.pipeline.build import build
    from pmlab.pipeline.warehouse import connect, run_checks, run_models, write_outcomes

    report = build(args.data_dir, args.warehouse, args.family, args.since, args.until, args.workers, args.full)
    rows = {}
    for counts in report.built.values():
        for table, n in counts.items():
            rows[table] = rows.get(table, 0) + n
    print(f"extract: {len(report.built)} day(s) rebuilt, {report.unchanged} unchanged, {report.seconds:.1f} s")
    if rows:
        print("         rows written: " + ", ".join(f"{t} {n:,}" for t, n in rows.items()))
    for reason, n in sorted(report.skipped.items()):
        print(f"         left out: {n:,} recording(s), {reason}")
    if args.outcomes:
        print(f"outcomes: {write_outcomes(args.warehouse, args.outcomes):,} final results stored")
    with connect(args.warehouse) as con:
        counts = run_models(con)
        print("models: " + ", ".join(f"{t} {n:,}" for t, n in counts.items()))
        failed = False
        for check in run_checks(con):
            mark = "ok  " if not check.failures else ("FAIL" if check.severity == "error" else "warn")
            print(
                f"check {mark} {check.name}: {check.failures:,} row(s)"
                + (f", e.g. {check.examples[0]}" if check.failures else "")
            )
            failed |= check.failures > 0 and check.severity == "error"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
