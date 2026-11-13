"""Replay one recorded market event by event and simulate resting buy orders (and optional market buys) in it.

The recording has every top-of-book change of each side and every trade print. UP and DOWN are one mirrored book: an UP
bid at ``p`` is a DOWN offer at ``1 - p``. A resting BUY of side ``s`` at price ``p`` lives like this:

* **Latency.** Placed at time T, it is in the book at T + 0.13 s; a cancel takes effect 0.11 s after it is sent.
* **Place in the queue.** Above the best bid: first in line. At the best bid: behind every share shown there. Below it
  (``queue="last"``): behind every share shown at ``p`` when ``p`` becomes the best bid, the pessimistic case.
* **Moving up.** The line ahead shrinks when trades print at ``p``, and by our proportional share when shares at ``p``
  disappear without a trade (cancellations). If every share at ``p`` is gone, we are first.
* **Fills from prints.** A taker selling ``s`` at ``p``, or buying the other side at ``1 - p``, fills us after the line
  ahead is used up; a print beyond our price means our level was swept, so we fill in full.
* **Fills from the ask** (``cross=True``). When the side's best ask comes down to our price, the seller would have met
  our order first even if no trade was recorded (we were not really there), so we fill at our price, up to the shares
  offered. This rule adds the fills that tend to lose, so it is on by default; prints-only results are too kind.

Market buys go at the best ask 0.3 s after the decision, only for the shares shown there, and pay the taker fee. Our
orders are assumed too small to change what others did. Resting fills pay no fee.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pmlab.backtest.fees import taker_fee
from pmlab.data.files import open_recording
from pmlab.strategies.base import RestingStrategy

EPS = 1e-9
BOOK, TRADE = 0, 1

# An event: (seconds since open, kind, side, a, b, c, d)
#   book : a = best bid, b = best ask, c = bid size, d = ask size
#   trade: a = price, b = shares
Event = tuple[float, int, int, float | None, float | None, float, float]


@dataclass(frozen=True)
class EngineConfig:
    dt: float = 0.25  # seconds between strategy decisions
    cross: bool = True  # also fill when the best ask comes down to our price (see module docs)
    queue: str = (
        "last"  # place in line below the best bid: "last", "first" or "frac:X" (X of the shares shown are ahead)
    )
    duration: float = 300  # market length in seconds
    latency_new: float = 0.13
    latency_cancel: float = 0.11
    latency_take: float = 0.30

    def queue_fraction(self) -> float:
        return float(self.queue.split(":", 1)[1]) if self.queue.startswith("frac:") else 1.0


def load_events(path: Path, start: int) -> tuple[str | None, list[Event]]:
    """``(condition_id, events)`` from a recording, events sorted by time (books before trades at the same time)."""
    events: list[Event] = []
    condition_id = None
    with open_recording(path, encoding="utf-8-sig") as handle:
        for line in handle:
            if line.startswith('{"type": "meta"'):
                condition_id = json.loads(line).get("condition_id")
                continue
            if '"type": "book"' in line:
                e = json.loads(line)
                side = 0 if e["side"] == "up" else 1
                events.append(
                    (
                        e["ts"] / 1000 - start,
                        BOOK,
                        side,
                        e.get("best_bid"),
                        e.get("best_ask"),
                        float(e.get("best_bid_size") or 0),
                        float(e.get("best_ask_size") or 0),
                    )
                )
            elif '"type": "trade"' in line:
                e = json.loads(line)
                side = 0 if e["side"] == "up" else 1
                if e.get("price") is not None and e.get("size"):
                    events.append((e["ts"] / 1000 - start, TRADE, side, float(e["price"]), float(e["size"]), 0, 0))
    events.sort(key=lambda x: (x[0], x[1]))
    return condition_id, events


class Order:
    __slots__ = ("side", "price", "left", "ahead", "live", "dead", "placed")

    def __init__(self, side: int, price: float, shares: float, now: float, latency: float):
        self.side, self.price, self.left = side, round(price, 2), shares
        self.ahead: float | None = -1.0  # -1 = not in the book yet; None = waiting for its price to become the best bid
        self.live, self.dead, self.placed = now + latency, 9e9, now


def run_market(
    events: list[Event], winner: int, strategy: RestingStrategy, config: EngineConfig = EngineConfig()
) -> dict:
    """Simulate ``strategy`` on one market. Returns shares, cost, gross profit, fees and the fill log."""
    cfg, qfrac = config, config.queue_fraction()
    book = {0: [None, None, 0.0, 0.0], 1: [None, None, 0.0, 0.0]}
    orders: list[Order] = []
    sh, cost = [0.0, 0.0], [0.0, 0.0]
    fee = 0.0
    fills: list[tuple] = []
    pending_takes: list[tuple] = []
    traded_at: dict[tuple[int, float], float] = {}  # shares traded at (side, price) since the last book change
    mids: list[tuple[float, float]] = []
    prints: list[tuple] = []
    next_decision, t_end = strategy.t0, strategy.t1

    def activate(o: Order) -> None:
        bid = book[o.side][0]
        if bid is None or o.price > bid + EPS:
            o.ahead = 0.0
        elif abs(o.price - bid) < EPS:
            o.ahead = book[o.side][2]
        else:
            o.ahead = None if cfg.queue == "last" or cfg.queue.startswith("frac:") else 0.0

    def fill(o: Order, q: float, now: float, why: str) -> None:
        q = min(q, o.left)
        if q <= EPS:
            return
        o.left -= q
        sh[o.side] += q
        cost[o.side] += q * o.price
        fills.append((now, o.side, o.price, q, why))

    def hit(side: int, price: float, volume: float, now: float) -> None:
        """Takers used ``volume`` shares of the bids of ``side`` at ``price`` (and everything better)."""
        traded_at[(side, round(price, 2))] = traded_at.get((side, round(price, 2)), 0.0) + volume
        for o in orders:
            if o.side != side or o.left <= 0 or now < o.live or now >= o.dead:
                continue
            if o.ahead is not None and o.ahead < 0:
                activate(o)
            if o.price > price + EPS:
                fill(o, o.left, now, "through")
            elif abs(o.price - price) < EPS:
                if o.ahead is None:
                    at_best = book[side][0] is not None and abs(book[side][0] - price) < EPS
                    o.ahead = qfrac * book[side][2] if at_best else 0.0
                take = max(0.0, volume - o.ahead)
                o.ahead = max(0.0, o.ahead - volume)
                fill(o, take, now, "queue")

    for t, kind, s, a, b, c, d in events:
        while next_decision <= min(t, t_end):  # strategy decisions every dt seconds, using only what came before
            now = next_decision
            for o in orders:
                if o.ahead is not None and o.ahead < 0 and now >= o.live:
                    activate(o)
            state = dict(
                t=now,
                book=book,
                sh=sh,
                cost=cost,
                orders=[o for o in orders if o.left > 0 and now < o.dead],
                mids=mids,
                prints=prints,
                fills=fills,
            )
            bids, takes = strategy.step(state)
            want = {(s_, round(p_, 2)): q_ for s_, p_, q_ in bids if q_ >= 1 and 0.01 <= p_ <= 0.99}
            for o in orders:
                if o.dead > now + cfg.latency_cancel and o.left > 0 and (o.side, o.price) not in want:
                    o.dead = now + cfg.latency_cancel
            have = {(o.side, o.price) for o in orders if o.left > 0 and o.dead > now + cfg.latency_cancel}
            for (s_, p_), q_ in want.items():
                if (s_, p_) not in have:
                    orders.append(Order(s_, p_, q_, now, cfg.latency_new))
            for s_, q_, max_price in takes:
                pending_takes.append((now + cfg.latency_take, s_, q_, max_price))
            orders = [o for o in orders if o.left > 0 and o.dead > now - 1]
            next_decision += cfg.dt
        for item in [x for x in pending_takes if x[0] <= t]:  # market buys: best ask at arrival, shown shares only
            pending_takes.remove(item)
            _, s_, q_, max_price = item
            ask, ask_size = book[s_][1], book[s_][3]
            if ask is not None and ask <= max_price and ask < 0.99 and ask_size > 0:
                q = min(q_, ask_size)
                sh[s_] += q
                cost[s_] += q * ask
                fee += taker_fee(ask, q)
                fills.append((t, s_, ask, q, "taker"))
        for o in orders:
            if o.ahead is not None and o.ahead < 0 and t >= o.live:
                activate(o)
        if kind == BOOK:
            prev_bid, prev_size = book[s][0], book[s][2]
            book[s] = [a, b, c, d]
            if cfg.cross and b is not None and d > 0:  # the ask came down to our bid: that seller meets us first
                left = d
                crossed = (
                    o for o in orders if o.side == s and o.left > 0 and o.live <= t < o.dead and o.price >= b - EPS
                )
                for o in sorted(crossed, key=lambda o: -o.price):
                    q_ = min(o.left, left)
                    if q_ > EPS:
                        fill(o, q_, t, "cross")
                        left -= q_
            for o in orders:
                if o.side != s or o.left <= 0 or t < o.live or t >= o.dead or (o.ahead is None and a is None):
                    continue
                if o.ahead is None:
                    if a is not None and abs(a - o.price) < EPS:
                        o.ahead = qfrac * c
                    continue
                if o.ahead < 0:
                    continue
                if a is None or a < o.price - EPS:
                    o.ahead = 0.0  # every share at our price is gone: we are first
                elif (
                    abs(a - o.price) < EPS and prev_bid is not None and abs(prev_bid - o.price) < EPS and prev_size > 0
                ):
                    gone = max(0.0, prev_size - c - traded_at.get((s, o.price), 0.0))  # cancelled, not traded
                    o.ahead = max(0.0, o.ahead - gone * min(1.0, o.ahead / prev_size))
            traded_at.clear()
            if s == 0 and a is not None and b is not None:
                mids.append((t, (a + b) / 2))
        else:
            price, volume = a, b
            ob = book[s]
            prints.append((t, s, price, volume))
            # Direction of the print, from that side's book just before it.
            if ob[1] is not None and price >= ob[1] - 0.0051:
                hit(1 - s, round(1 - price, 2), volume, t)  # a market BUY of s uses the other side's bids at 1 - price
            elif ob[0] is not None and price <= ob[0] + 0.0051:
                hit(s, price, volume, t)  # a market SELL of s uses s's bids at price
            elif ob[0] is not None and ob[1] is not None:
                if price >= (ob[0] + ob[1]) / 2:
                    hit(1 - s, round(1 - price, 2), volume, t)
                else:
                    hit(s, price, volume, t)
        if t > cfg.duration:
            break
    return dict(
        win=winner,
        up=sh[0],
        down=sh[1],
        cost_up=cost[0],
        cost_down=cost[1],
        cost=cost[0] + cost[1],
        gross=sh[winner] - cost[0] - cost[1],
        fee=fee,
        fills=len(fills),
        taker_fills=sum(1 for f in fills if f[4] == "taker"),
        through=sum(1 for f in fills if f[4] == "through"),
        log=fills,
    )
