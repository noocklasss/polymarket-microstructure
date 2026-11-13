"""PairMaker: the textbook "buy both sides for less than $1" idea, as resting orders. A demo, not an edge.

Exactly one of UP and DOWN pays $1, so one share of each bought for a combined 97c locks in 3c whatever happens.
PairMaker rests ``shares``-share buys on both sides at the best bid (1c above it when the spread is 2c or more), from
``t_from`` to ``t_to`` seconds:

* a side is bid only while its new average price plus the other side's average stays at or under ``pair_max``, unless
  it is the lighter side (buying it completes pairs). With nothing held the other side counts as 50c, so the first
  buy waits for a side at or under ``pair_max`` - 50c;
* no side may hold more than ``max_ratio`` x the other side's shares + ``slack`` shares;
* at most ``budget`` dollars a market. Everything is held to the end; resting fills pay no fee.

Why it is expected to fail (adverse selection): a resting bid fills when someone wants to sell to it. In a market that
is moving, the side being sold is the side that is losing, so the losing side fills and the winning side does not; the
pair is rarely completed at a good price and the leftover shares sit on the loser. The report in ``reports/`` tests
whether this holds once the queue and fills are modelled honestly.
"""

from __future__ import annotations

from dataclasses import dataclass

from pmlab.strategies.base import Bid, State, Take


@dataclass
class PairMaker:
    shares: float = 10
    t_from: float = -10
    t_to: float = 270
    pair_max: float = 0.97
    max_ratio: float = 1.3
    slack: float = 30
    budget: float = 300
    improve: bool = True

    @property
    def t0(self) -> float:
        return self.t_from

    @property
    def t1(self) -> float:
        return self.t_to

    def step(self, state: State) -> tuple[list[Bid], list[Take]]:
        sh, cost, book = state["sh"], state["cost"], state["book"]
        if cost[0] + cost[1] >= self.budget:
            return [], []
        bids = []
        for s in (0, 1):
            other = 1 - s
            bid, ask = book[s][0], book[s][1]
            if bid is None or ask is None:
                continue
            price = round(bid + 0.01, 2) if self.improve and ask - bid >= 0.0199 else bid
            if sh[s] > self.max_ratio * sh[other] + self.slack:
                continue  # already too lopsided on this side
            other_avg = cost[other] / sh[other] if sh[other] else 0.5
            new_avg = (cost[s] + self.shares * price) / (sh[s] + self.shares)
            if sh[s] >= sh[other] and new_avg + other_avg > self.pair_max:
                continue  # buying more here would make the pair cost too much
            bids.append((s, price, self.shares))
        return bids, []
