import asyncio
import json
from datetime import datetime, timedelta

import pytest

from pmlab import cli
from pmlab.ingestion import recorder as R
from pmlab.ingestion.recorder import ET, MarketFamily, Recorder, load_families, pick_market, settlement_winner

START = 1785556800  # 2026-08-01 00:00 ET


def gamma_event(slug, closed=False, extra_markets=()):
    market = {
        "slug": slug,
        "question": "Bitcoin Up or Down?",
        "conditionId": "0xcond",
        "clobTokenIds": '["up-token", "down-token"]',
        "outcomePrices": '["0.52", "0.48"]',
        "endDate": datetime.fromtimestamp(START + 300, ET).isoformat(),
        "eventStartTime": datetime.fromtimestamp(START, ET).isoformat(),
        "orderPriceMinTickSize": 0.01,
        "closed": closed,
    }
    return {"slug": slug, "markets": [*extra_markets, market]}


@pytest.fixture
def family():
    return load_families()["btc_5m"]


@pytest.fixture
def rec(family, tmp_path):
    r = Recorder(family, tmp_path)
    yield r
    for m in r.markets.values():
        m.close_file()


def rows(market):
    market.close_file()
    with open(market.filename, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


# ---------------------------------------------------------------- configuration


def test_packaged_config_has_all_sixteen_families():
    families = load_families()
    assert len(families) == 16
    assert families["gold_1h"].slug_prefix == "xau" and families["gold_1h"].duration_seconds == 3600
    assert families["silver_15m"].slug_prefix == "xag"
    assert all(f.heartbeat_seconds == 5 for f in families.values())  # [defaults] applied


def test_family_overrides_and_bad_timeframe(tmp_path):
    path = tmp_path / "m.toml"
    path.write_text(
        '[defaults]\nheartbeat_seconds = 5\n[families.x]\nslug_prefix = "x"\ntimeframe = "5m"\nheartbeat_seconds = 0\n'
    )
    assert load_families(path)["x"].heartbeat_seconds == 0
    path.write_text('[families.x]\nslug_prefix = "x"\ntimeframe = "2m"\n')
    with pytest.raises(ValueError):
        load_families(path)


@pytest.mark.parametrize(
    "timeframe, expected_start",
    [
        ("5m", datetime(2026, 8, 1, 10, 5, tzinfo=ET)),
        ("15m", datetime(2026, 8, 1, 10, 0, tzinfo=ET)),
        ("1h", datetime(2026, 8, 1, 10, 0, tzinfo=ET)),
    ],
)
def test_slug_is_the_start_of_the_running_slot(timeframe, expected_start):
    family = MarketFamily("x", "btc", timeframe)
    moment = datetime(2026, 8, 1, 10, 7, 31, tzinfo=ET)
    assert family.slug_at(moment) == f"btc-updown-{timeframe}-{int(expected_start.timestamp())}"
    assert family.slug_at(moment.astimezone(ET).replace(tzinfo=None)) == family.slug_at(moment)  # naive = ET


def test_upcoming_slugs_are_consecutive_slots(family):
    slugs = family.upcoming_slugs(datetime.fromtimestamp(START + 10, ET))
    assert slugs == [f"btc-updown-5m-{START + 300 * i}" for i in range(3)]


# ---------------------------------------------------------------- pure helpers


def test_pick_market_finds_ours_in_a_multi_market_event():
    slug = f"btc-updown-5m-{START}"
    event = gamma_event(slug, extra_markets=[{"slug": "someone-else"}])
    assert pick_market(event, slug)["slug"] == slug
    assert pick_market({"markets": []}, slug) is None


@pytest.mark.parametrize(
    "prices, expected",
    [
        ('["1", "0"]', "up"),
        ('["0.0", "1.0"]', "down"),
        ('["0.6", "0.4"]', None),  # not resolved yet
        ("[]", None),
        ('["x", "y"]', None),
    ],
)
def test_settlement_winner(prices, expected):
    assert settlement_winner({"outcomePrices": prices, "outcomes": '["Up", "Down"]'}) == expected


# ---------------------------------------------------------------- recording


def test_track_writes_meta_row_and_raw_payload(rec):
    slug = f"btc-updown-5m-{START}"
    market = rec.track(slug, gamma_event(slug))
    assert rec.token_to_slug == {"up-token": slug, "down-token": slug}
    assert (market.up_mid, market.down_mid) == (0.52, 0.48)
    meta = rows(market)[0]
    assert (meta["type"], meta["yes_token_id"], meta["tick_size"]) == ("meta", "up-token", 0.01)
    sidecar = json.loads((rec.output_dir / f"{slug}.meta.json").read_text())
    assert sidecar["market"]["conditionId"] == "0xcond"


def test_closed_or_malformed_markets_are_not_tracked(rec):
    slug = f"btc-updown-5m-{START}"
    assert rec.track(slug, gamma_event(slug, closed=True)) is None
    bad = gamma_event(slug)
    bad["markets"][0]["clobTokenIds"] = '["only-one"]'
    assert rec.track(slug, bad) is None
    assert rec.markets == {}


def test_repeated_trade_prints_are_written_once(rec):
    slug = f"btc-updown-5m-{START}"
    market = rec.track(slug, gamma_event(slug))
    trade = {
        "event_type": "last_trade_price",
        "asset_id": "down-token",
        "price": "0.47",
        "size": "12",
        "side": "BUY",
        "timestamp": "1785556801000",
    }
    rec.handle_message(trade)
    rec.handle_message(dict(trade))  # the exchange repeats the last print
    rec.handle_message({**trade, "size": "5"})
    trades = [r for r in rows(market) if r["type"] == "trade"]
    assert [(t["side"], t["taker_side"], t["size"]) for t in trades] == [("down", "BUY", 12.0), ("down", "BUY", 5.0)]


def test_tick_size_change_is_recorded(rec):
    slug = f"btc-updown-5m-{START}"
    market = rec.track(slug, gamma_event(slug))
    rec.handle_message(
        {"event_type": "tick_size_change", "asset_id": "up-token", "old_tick_size": "0.01", "new_tick_size": "0.001"}
    )
    assert rows(market)[-1]["type"] == "tick_size"


class OpenSocket:
    closed = False


def test_each_second_writes_snapshot_and_periodic_heartbeat(rec):
    slug = f"btc-updown-5m-{START}"
    market = rec.track(slug, gamma_event(slug))
    rec.handle_message(
        {
            "event_type": "book",
            "asset_id": "up-token",
            "timestamp": "1000",
            "bids": [{"price": "0.50", "size": "10"}],
            "asks": [{"price": "0.53", "size": "7"}],
        }
    )
    rec.ws = OpenSocket()
    during = datetime.fromtimestamp(START + 30, ET)
    for tick in range(6):
        rec.write_second(tick, during, (START + 30 + tick) * 1000)
    written = rows(market)
    snapshots = [r for r in written if r["type"] == "snapshot"]
    heartbeats = [r for r in written if r.get("source_event") == "heartbeat"]
    assert len(snapshots) == 6
    assert snapshots[0]["up"]["ask"] == 0.53 and snapshots[0]["up"]["asks"] == [[0.53, 7.0]]
    assert len(heartbeats) == 2  # ticks 0 and 5, only the initialised UP book
    assert heartbeats[0]["ts"] == (START + 30) * 1000


def test_no_rows_before_start_or_after_the_tail(rec, family):
    slug = f"btc-updown-5m-{START}"
    market = rec.track(slug, gamma_event(slug))
    rec.write_second(1, datetime.fromtimestamp(START - 5, ET), 0)
    rec.write_second(1, market.end_time + timedelta(seconds=family.tail_seconds + 1), 0)
    assert [r["type"] for r in rows(market)] == ["meta"]


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession(FakeResponse):
    def __init__(self, body):
        self.body = body

    def get(self, url, params=None, timeout=None):
        return FakeResponse(self.body)


def test_settlement_file_is_written_after_close(rec, monkeypatch):
    slug = f"btc-updown-5m-{START}"
    market = rec.track(slug, gamma_event(slug))
    resolved = gamma_event(slug)
    resolved["markets"][0].update(outcomePrices='["0", "1"]', outcomes='["Up", "Down"]', closed=True)
    monkeypatch.setattr(R, "public_session", lambda: FakeSession([resolved]))
    asyncio.run(rec.write_settlement(market, delay=0))
    settlement = json.loads((rec.settlements_dir / f"{slug}.json").read_text())
    assert (settlement["winner"], settlement["closed"]) == ("down", True)


def test_cli_lists_families(capsys):
    assert cli.main(["families"]) == 0
    out = capsys.readouterr().out
    assert "btc_5m" in out and "xau-updown-1h" in out
