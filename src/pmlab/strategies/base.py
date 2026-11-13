"""What a strategy sees and what it returns.

The engine calls ``strategy.step(state)`` every ``dt`` seconds from ``strategy.t0`` to ``strategy.t1`` (seconds since
the market opened). ``state`` holds only what has happened up to ``state["t"]``:

    t       seconds since the market opened
    book    {0: UP, 1: DOWN} -> [best_bid, best_ask, bid_size, ask_size]  (price None = no quote)
    sh      [UP shares, DOWN shares] held
    cost    [UP dollars, DOWN dollars] spent
    orders  our live resting orders
    mids    [(t, UP mid)] history
    prints  [(t, side, price, shares)] every trade print so far (no direction: the engine infers it from the book)
    fills   [(t, side, price, shares, why)] our fills so far

It returns ``(bids, takes)``:
    bids    [(side, price, shares)] the resting buys it wants live now. Returning the same side and price as a live
            order keeps that order and its place in the queue; leaving one out cancels it.
    takes   [(side, shares, max_price)] market buys, executed at the best ask shortly after (shares shown there only).
"""

from __future__ import annotations

from typing import Any, Protocol

Bid = tuple[int, float, float]
Take = tuple[int, float, float]
State = dict[str, Any]


class RestingStrategy(Protocol):
    t0: float  # first decision time (may be before trading starts, to warm up history)
    t1: float  # last decision time

    def step(self, state: State) -> tuple[list[Bid], list[Take]]: ...
