import json

import pytest

from pmlab.ingestion.stream_capture import capture_market_event, flush_market_changes
from pmlab.ingestion.tracked_market import TrackedMarket


class Recorder:
    """The minimal state a recorder exposes to :func:`capture_market_event`."""

    def __init__(self, conn_id=None):
        self.markets, self.token_to_slug, self.conn_id = {}, {}, conn_id
        self.total_rows, self.last_matched_time = 0, 0.0

    def add(self, market):
        self.markets[market.slug] = market
        self.token_to_slug.update({market.yes_token_id: market.slug, market.no_token_id: market.slug})

    def handle(self, message, received_ms=0):
        self.total_rows += capture_market_event(self, message, received_ms)


@pytest.fixture
def market(tmp_path):
    m = TrackedMarket(
        "btc-updown-5m-1785556800",
        "test",
        "condition",
        "up-token",
        "down-token",
        None,
        None,
        str(tmp_path / "market.ndjson"),
    )
    yield m
    m.close_file()


def rows_of(market):
    market.close_file()
    with open(market.filename, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def book(asset, bids, asks, ts="1000"):
    return {
        "event_type": "book",
        "asset_id": asset,
        "timestamp": ts,
        "bids": [{"price": p, "size": s} for p, s in bids],
        "asks": [{"price": p, "size": s} for p, s in asks],
    }


def changes(*items, ts="2000"):
    keys = ("asset_id", "side", "price", "size", "best_bid", "best_ask")
    items = [dict(zip(keys, item, strict=True)) for item in items]
    return {"event_type": "price_change", "timestamp": ts, "price_changes": items}


def test_level_changes_update_best_prices_and_sizes(market):
    rec = Recorder()
    rec.add(market)
    rec.handle(book("up-token", [("0.40", "11"), ("0.49", "7")], [("0.70", "99"), ("0.51", "8")]))
    rec.handle(book("down-token", [("0.48", "10")], [("0.50", "12")]))
    rec.handle(
        changes(
            ("up-token", "SELL", "0.51", "0", "0.49", "0.50"),
            ("up-token", "SELL", "0.50", "6", "0.49", "0.50"),
            ("down-token", "BUY", "0.48", "0", "0.47", "0.50"),
            ("down-token", "BUY", "0.47", "9", "0.47", "0.50"),
        )
    )
    rec.total_rows += flush_market_changes(market)
    rows = rows_of(market)
    assert len(rows) == rec.total_rows == 4
    assert (rows[0]["best_ask"], rows[0]["best_ask_size"], rows[0]["best_bid_size"]) == (0.51, 8, 7)
    assert rows[2]["source_event"] == "price_change"
    assert (rows[2]["best_ask"], rows[2]["best_ask_size"]) == (0.50, 6)
    assert rows[3]["best_bid"] == 0.47


def test_inconsistent_change_never_creates_a_tradable_quote(market):
    rec = Recorder()
    rec.add(market)
    rec.handle(book("up-token", [("0.40", "8")], [("0.60", "9")]))
    # The exchange says the best ask is 0.55, a level we never saw: we are out of sync.
    rec.handle(changes(("up-token", "SELL", "0.60", "0", "0.40", "0.55")))
    assert flush_market_changes(market) == 0
    assert not market.books["up"].initialized


def test_levels_eaten_without_removal_are_pruned_and_recording_continues(market):
    rec = Recorder()
    rec.add(market)
    rec.handle(book("up-token", [("0.40", "8"), ("0.49", "50")], [("0.57", "23"), ("0.72", "5"), ("0.73", "9")]))
    # A trade took the 0.49 bid and the 0.57 + 0.72 asks without removal messages; the next change shows the true top.
    rec.handle(changes(("up-token", "BUY", "0.41", "3", "0.41", "0.73")))
    up = market.books["up"]
    assert up.initialized
    assert up.top() == (0.41, 0.73)
    assert up.pruned == 3
    assert flush_market_changes(market) == 1
    last = rows_of(market)[-1]
    assert (last["best_bid"], last["best_ask"], last["pruned_levels"]) == (0.41, 0.73, 3)


def test_book_row_has_depth_microprice_and_connection(market):
    rec = Recorder(conn_id=7)
    rec.add(market)
    rec.handle(book("up-token", [("0.40", "8"), ("0.49", "50")], [("0.57", "23"), ("0.72", "5")]))
    row = rows_of(market)[0]
    assert (row["v"], row["conn"]) == (2, 7)
    assert isinstance(row["ts_mono"], int)
    assert row["bids"] == [[0.49, 50.0], [0.4, 8.0]]
    assert row["asks"] == [[0.57, 23.0], [0.72, 5.0]]
    assert row["micro"] == round((0.49 * 23 + 0.57 * 50) / 73, 4)
    assert row["book_imbalance"] == round((50 - 23) / 73, 4)


def test_pruned_level_writes_an_explicit_resync_row(market):
    rec = Recorder(conn_id=3)
    rec.add(market)
    rec.handle(book("up-token", [("0.40", "8"), ("0.49", "50")], [("0.57", "23"), ("0.72", "5")]))
    rec.handle(changes(("up-token", "BUY", "0.41", "3", "0.41", "0.73")))
    resync = [r for r in rows_of(market) if r["type"] == "resync"]
    assert len(resync) == 1
    assert (resync[0]["reason"], resync[0]["dropped"], resync[0]["conn"]) == ("stale_levels", 3, 3)


def test_events_for_unknown_or_closed_markets_are_ignored(market):
    rec = Recorder()
    rec.add(market)
    rec.handle(book("someone-else", [("0.40", "8")], [("0.60", "9")]))
    market.closed = True
    rec.handle(book("up-token", [("0.40", "8")], [("0.60", "9")]))
    assert rec.total_rows == 0
