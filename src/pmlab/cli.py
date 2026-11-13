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

    args = parser.parse_args(argv)
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


if __name__ == "__main__":
    raise SystemExit(main())
