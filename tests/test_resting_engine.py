import json

import pytest

from pmlab.backtest.fees import after_fees, taker_fee
from pmlab.backtest.resting_engine import BOOK, TRADE, EngineConfig, load_events, run_market
from pmlab.data.outcomes import winner_of

UP, DOWN = 0, 1


def book(t, side, bid, ask, bid_size=100, ask_size=100):
    return (t, BOOK, side, bid, ask, bid_size, ask_size)


def trade(t, side, price, shares):
    return (t, TRADE, side, price, shares, 0, 0)


class Fixed:
    """Rests the same bids from t0 to t1 and optionally sends market buys once."""

    def __init__(self, bids=(), takes=(), t0=0.0, t1=300.0):
        self.bids, self.takes, self.t0, self.t1 = list(bids), list(takes), t0, t1
        self.states = []

    def step(self, state):
        self.states.append((state["t"], len(state["prints"]), len(state["mids"])))
        takes, self.takes = self.takes, []
        return self.bids, takes


def run(events, strategy, winner=UP, **cfg):
    return run_market(events, winner, strategy, EngineConfig(dt=0.25, **cfg))


def test_bid_at_best_waits_behind_the_shares_shown():
    events = [
        book(0, UP, 0.60, 0.62, bid_size=30),
        book(1, UP, 0.60, 0.62, bid_size=30),
        trade(2, UP, 0.60, 20),
        trade(3, UP, 0.60, 15),
    ]
    r = run(events, Fixed([(UP, 0.60, 10)], t1=1))
    # 30 ahead: the first 20 traded leave 10 ahead, the next 15 use those 10 and fill 5 of ours.
    assert (r["up"], r["cost"], r["fills"]) == (5, 3.0, 1)
    assert r["log"][0][4] == "queue"


def test_bid_above_best_is_first_and_a_print_through_it_fills_in_full():
    events = [book(0, UP, 0.60, 0.65), book(1, UP, 0.60, 0.65), trade(2, UP, 0.58, 3)]
    r = run(events, Fixed([(UP, 0.61, 10)], t1=1))
    assert r["up"] == 10 and r["log"][0][4] == "through"  # a sale at 58c went through our 61c bid


def test_buying_the_other_side_fills_our_bid_through_the_mirror():
    events = [
        book(0, UP, 0.60, 0.62, bid_size=0),
        book(0, DOWN, 0.38, 0.40),
        book(1, UP, 0.60, 0.62, bid_size=0),
        trade(2, DOWN, 0.40, 7),
    ]  # a taker BUYS DOWN at 40c = sells UP at 60c
    r = run(events, Fixed([(UP, 0.60, 10)], t1=1))
    assert r["up"] == 7


def test_ask_coming_down_to_our_bid_fills_only_with_the_cross_rule():
    events = [book(0, UP, 0.60, 0.62), book(1, UP, 0.60, 0.62), book(2, UP, 0.58, 0.60, ask_size=4)]
    assert run(events, Fixed([(UP, 0.60, 10)], t1=1), cross=True)["up"] == 4
    assert run(events, Fixed([(UP, 0.60, 10)], t1=1), cross=False)["up"] == 0


def test_order_is_not_live_before_its_latency():
    events = [book(0, UP, 0.60, 0.65), trade(0.05, UP, 0.55, 50)]
    assert run(events, Fixed([(UP, 0.61, 10)], t1=0))["up"] == 0  # placed at 0, live at 0.13


def test_market_buy_pays_the_ask_and_the_fee_and_takes_only_shown_shares():
    events = [book(0, UP, 0.60, 0.62, ask_size=6), book(1, UP, 0.60, 0.62, ask_size=6)]
    r = run(events, Fixed(takes=[(UP, 10, 0.64)], t1=0))
    assert r["up"] == 6 and r["cost"] == pytest.approx(6 * 0.62)
    assert r["fee"] == pytest.approx(taker_fee(0.62, 6))
    assert r["gross"] == pytest.approx(6 - 6 * 0.62)


def test_losing_side_is_worth_nothing():
    events = [book(0, UP, 0.60, 0.65), book(1, UP, 0.60, 0.65), trade(2, UP, 0.58, 3)]
    r = run(events, Fixed([(UP, 0.61, 10)], t1=1), winner=DOWN)
    assert r["gross"] == pytest.approx(-6.1)


def test_decisions_never_see_later_events():
    events = [book(i * 0.4, UP, 0.50, 0.52) for i in range(20)] + [trade(i * 0.4 + 0.1, UP, 0.50, 1) for i in range(20)]
    events.sort(key=lambda e: (e[0], e[1]))
    strat = Fixed(t1=7.0)
    run(events, strat)
    for t, n_prints, n_mids in strat.states:
        assert n_prints == sum(1 for e in events if e[1] == TRADE and e[0] < t)
        assert n_mids == sum(1 for e in events if e[1] == BOOK and e[0] < t)


def test_load_events_reads_books_and_trades_in_time_order(tmp_path):
    start = 1785556800
    rows = [
        {"type": "meta", "condition_id": "0xc"},
        {"type": "trade", "ts": (start + 2) * 1000, "side": "down", "price": 0.4, "size": 5},
        {
            "type": "book",
            "ts": (start + 1) * 1000,
            "side": "up",
            "best_bid": 0.6,
            "best_ask": 0.62,
            "best_bid_size": 10,
            "best_ask_size": 0,
        },
    ]
    path = tmp_path / "m.ndjson"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    cid, events = load_events(path, start)
    assert cid == "0xc"
    assert events == [(1.0, BOOK, UP, 0.6, 0.62, 10.0, 0.0), (2.0, TRADE, DOWN, 0.4, 5.0, 0, 0)]


@pytest.mark.parametrize(
    "payouts, expected", [([1, 0], UP), ([0.0, 1.0], DOWN), ([0.995, 0.005], None), ([0.5, 0.5], None), (None, None)]
)
def test_only_final_results_count(payouts, expected):
    assert winner_of(payouts) == expected


def test_fees():
    assert taker_fee(0.5, 100) == pytest.approx(1.75)
    assert after_fees(10, 2) == 8 and after_fees(10, 2, rebate=0.32) == pytest.approx(8.64)
