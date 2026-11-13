"""The bundled sample (data/sample/) must load, pass the quality audit, and reproduce the study's exam rows exactly."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd
import pytest

from pmlab.backtest.resting_engine import EngineConfig, load_events, run_market
from pmlab.backtest.runner import market_files
from pmlab.data.files import market_slug, open_recording
from pmlab.data.loaders import load_books
from pmlab.data.outcomes import load_outcomes, winner_of
from pmlab.data.quality import audit_log
from pmlab.strategies.pair_maker import PairMaker

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"
FILES = market_files(SAMPLE, "btc_5m_data")
CONFIG = EngineConfig(dt=0.25, cross=True, queue="last", duration=300)  # as in experiments/pair_maker_btc_5m.py


def test_sample_is_there():
    assert len(FILES) == 4
    assert all(p.name.endswith(".ndjson.gz") for p in FILES.values())


def test_market_slug_and_gzip_reading(tmp_path):
    assert market_slug("x/btc-updown-5m-1794027600.ndjson.gz") == "btc-updown-5m-1794027600"
    assert market_slug("x/btc-updown-5m-1794027600.ndjson") == "btc-updown-5m-1794027600"
    plain, packed = tmp_path / "a.ndjson", tmp_path / "a.ndjson.gz"
    plain.write_text('{"type": "meta"}\n', encoding="utf-8")
    with gzip.open(packed, "wt", encoding="utf-8") as handle:
        handle.write('{"type": "meta"}\n')
    for path in (plain, packed):
        with open_recording(path) as handle:
            assert json.loads(handle.readline()) == {"type": "meta"}


@pytest.mark.parametrize("start", sorted(FILES))
def test_sample_loads_and_passes_the_audit(start):
    _, books = load_books(FILES[start], duration_seconds=300)
    assert books.shape == (300, 2, 5)
    assert audit_log(FILES[start], data_policy="fresh_recording_v2")["passes_book_filter"]


@pytest.mark.parametrize("start", sorted(FILES))
def test_sample_reproduces_the_exam_rows(start):
    summary = json.loads((ROOT / "reports" / "pair_maker_btc_5m.json").read_text())
    params = summary["versions"][summary["chosen"]]["params"]
    expected = pd.read_csv(ROOT / "reports" / "pair_maker_btc_5m_exam_markets.csv").set_index("start").loc[start]
    outcomes = load_outcomes(SAMPLE / "outcomes.json")
    condition_id, events = load_events(FILES[start], start)
    result = run_market(events, winner_of(outcomes[condition_id]), PairMaker(**params), CONFIG)
    assert result["fills"] == expected["fills"]
    assert result["cost"] == pytest.approx(expected["cost"], abs=1e-6)
    assert result["gross"] == pytest.approx(expected["gross"], abs=1e-6)
