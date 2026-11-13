"""The pipeline on the bundled sample: complete, incremental, identical to the Python loader, and its checks bite."""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from pmlab.backtest.runner import market_files
from pmlab.cli import main
from pmlab.data.loaders import load_books
from pmlab.pipeline.build import build
from pmlab.pipeline.extract import TABLES, parse_recording
from pmlab.pipeline.warehouse import connect, run_checks, run_models, write_outcomes

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"
FILES = market_files(SAMPLE, "btc_5m_data")


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    out = tmp_path_factory.mktemp("warehouse")
    build(SAMPLE, out, workers=1)
    write_outcomes(out, SAMPLE / "outcomes.json")
    con = connect(out)
    run_models(con)
    yield out, con
    con.close()


def _copy_sample(tmp_path: Path, starts) -> Path:
    data = tmp_path / "data"
    (data / "btc_5m_data").mkdir(parents=True)
    for start in starts:
        shutil.copy(FILES[start], data / "btc_5m_data")
    return data


def test_every_line_lands_in_a_table():
    for path in FILES.values():
        rows = parse_recording(path)
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            kinds = [json.loads(line)["type"] for line in handle]
        assert len(rows["markets"]) == 1 and rows["markets"][0]["lines"] == len(kinds)
        for table, kind in (("trades", "trade"), ("book", "book"), ("snapshots", "snapshot"), ("resyncs", "resync")):
            assert len(rows[table]) == kinds.count(kind)


def test_rerun_does_nothing_and_new_markets_rebuild_their_day(tmp_path):
    starts = sorted(FILES)
    data = _copy_sample(tmp_path, starts[:3])
    out = tmp_path / "warehouse"
    first = build(data, out, workers=1)
    assert list(first.built) == ["btc_5m/2026-11-07"] and first.built["btc_5m/2026-11-07"]["markets"] == 3
    assert build(data, out, workers=1).built == {}
    shutil.copy(FILES[starts[3]], data / "btc_5m_data")
    third = build(data, out, workers=1)
    assert third.built["btc_5m/2026-11-07"]["markets"] == 4
    with connect(out) as con:
        assert con.execute("SELECT count(*) FROM raw_markets").fetchall()[0][0] == 4  # replaced, not appended


def test_unfinished_markets_wait_for_the_next_run(tmp_path):
    data = _copy_sample(tmp_path, FILES)
    last_end = max(FILES) + 300
    report = build(data, tmp_path / "warehouse", workers=1, now=last_end + 60)  # last market ended 60 s ago
    assert report.built["btc_5m/2026-11-07"]["markets"] == 3
    assert report.skipped["not finished yet"] == 1


def test_book_seconds_equal_the_python_loader(warehouse):
    _, con = warehouse
    for path in FILES.values():
        _, books = load_books(path, duration_seconds=300)
        rows = con.execute(
            "SELECT bid, ask, bid_size, ask_size, seen_at FROM book_seconds WHERE slug = ? "
            "ORDER BY side = 'down', second",
            [path.name.split(".")[0]],
        ).fetchall()
        sql = np.array([[np.nan if v is None else v for v in row] for row in rows], float)
        assert np.array_equal(sql.reshape(2, 300, 5).transpose(1, 0, 2), books, equal_nan=True)


def test_market_summary_matches_the_raw_file(warehouse):
    _, con = warehouse
    for path in FILES.values():
        trades = [t for t in parse_recording(path)["trades"] if t["t"] >= 0]
        got = con.execute(
            "SELECT trades, volume_usd FROM market_summary WHERE slug = ?", [path.name.split(".")[0]]
        ).fetchall()[0]
        assert got[0] == len(trades)
        assert got[1] == pytest.approx(sum(t["price"] * t["size"] for t in trades))


def test_winners_come_from_final_results_only(warehouse):
    _, con = warehouse
    winners = dict(con.execute("SELECT slug, winner FROM markets").fetchall())
    assert winners["btc-updown-5m-1794027900"] == "down" and winners["btc-updown-5m-1794072000"] == "up"


def test_checks_pass_on_the_sample(warehouse):
    _, con = warehouse
    assert {c.name: c.failures for c in run_checks(con)} == dict.fromkeys(
        [
            "market_has_meta",
            "markets_unique",
            "recording_coverage",
            "research_window",
            "results_final",
            "trade_values",
            "unreadable_lines",
        ],
        0,
    )


def test_a_bad_trade_fails_the_run(tmp_path, capsys):
    data = _copy_sample(tmp_path, sorted(FILES)[:1])
    start = 1794200000 - 1794200000 % 300  # a finished market inside the research window
    lines = [
        {"type": "meta", "v": 2, "slug": f"btc-updown-5m-{start}", "condition_id": "0xabc"},
        {"v": 2, "ts": (start + 1) * 1000, "type": "trade", "side": "up", "price": 1.5, "size": 10},
        "this line is cut off {",
    ]
    text = "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines) + "\n"
    (data / "btc_5m_data" / f"btc-updown-5m-{start}.ndjson").write_text(text)
    code = main(["pipeline", "--data-dir", str(data), "--warehouse", str(tmp_path / "w"), "--workers", "1"])
    printed = capsys.readouterr().out
    assert code == 1
    assert "check FAIL trade_values: 1 row(s)" in printed
    assert "check warn unreadable_lines: 1 row(s)" in printed


def test_every_table_has_a_partition(warehouse):
    out, _ = warehouse
    for table in TABLES:
        assert (out / table / "family=btc_5m" / "day=2026-11-07" / "part.parquet").exists()
