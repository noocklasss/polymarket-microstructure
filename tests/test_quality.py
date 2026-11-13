import json

import pytest

from pmlab.data.quality import POLICY_RECORDED, audit_log, family_and_duration, save_log_quality

START = 1785556800  # 2026-08-01 00:00 America/New_York


@pytest.fixture
def audit(tmp_path):
    def run(event_seconds, snapshot_seconds=(), ask_size=10, data_policy="buyable_both_v1"):
        path = tmp_path / f"btc-updown-5m-{START}.ndjson"
        rows = [{"type": "meta"}]
        for t in event_seconds:
            for side in ("up", "down"):
                rows.append(
                    {
                        "type": "book",
                        "ts": (START + t) * 1000,
                        "side": side,
                        "best_bid": 0.49,
                        "best_ask": 0.5,
                        "best_bid_size": 10,
                        "best_ask_size": ask_size,
                    }
                )
        rows.extend(
            {"type": "snapshot", "ts": (START + t) * 1000, "outcome_up": 0.5, "outcome_down": 0.5}
            for t in snapshot_seconds
        )
        path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        return audit_log(path, data_policy=data_policy)

    return run


def test_fresh_unchanged_books_pass(audit):
    report = audit(range(300))
    assert report["fresh_both_seconds"] == 300
    assert report["healthy_capture"]


def test_price_snapshots_do_not_make_a_stale_book_fresh(audit):
    report = audit([0], range(300))
    assert report["fresh_both_seconds"] == 6  # second 0 plus the 5-second freshness window
    assert report["longest_invalid_gap_seconds"] == 294
    assert not report["passes_book_filter"]


def test_exact_80_percent_boundary(audit):
    assert audit(range(235))["passes_book_filter"]
    assert not audit(range(234))["passes_book_filter"]


def test_empty_asks_are_not_a_capture_outage(audit):
    report = audit(range(300), ask_size=0)
    assert report["healthy_capture"]
    assert not report["passes_book_filter"]
    assert report["fresh_but_empty_ask_seconds"] == 300
    assert report["stale_or_missing_recording_seconds"] == 0


def test_recorded_policy_accepts_empty_asks(audit):
    report = audit(range(300), ask_size=0, data_policy=POLICY_RECORDED)
    assert report["passes_book_filter"]
    assert report["coverage"] == 1.0
    assert report["buyable_both_coverage"] == 0.0


@pytest.mark.parametrize(
    "slug, expected",
    [
        ("btc-updown-5m-1785556800", ("btc_5m", 300)),
        ("eth-updown-15m-1785556800", ("eth_15m", 900)),
        ("xau-updown-15m-1785556800", ("gold_15m", 900)),
        ("xag-updown-1h-1785556800", ("silver_1h", 3600)),
    ],
)
def test_family_and_duration(slug, expected):
    assert family_and_duration(slug) == expected


def test_save_log_quality_writes_report_and_latest(audit, tmp_path):
    audit(range(300))
    out = tmp_path / "reports"
    report = save_log_quality(tmp_path / f"btc-updown-5m-{START}.ndjson", out)
    folder = out / "btc_5m_logger_quality"
    assert json.loads((folder / "latest.json").read_text()) == report
    assert (folder / f"btc-updown-5m-{START}.json").exists()
    assert not list(folder.glob("*.tmp"))
