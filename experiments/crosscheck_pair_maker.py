"""Second, independent simulator for the pair maker: deliberately simple and optimistic (an upper bound).

It shares no fill code with :mod:`pmlab.backtest.resting_engine`. Decisions are made once a second from the as-of book
array of :mod:`pmlab.data.loaders`; a bid placed at second ``t`` is filled IN FULL if during ``(t, t + 1]`` any trade
prints at or through its price (on its side, or the mirrored price on the other side) or the side's best ask at
``t + 1`` is at or under it. There is no queue: we are always first in line. If the strategy loses even here, the loss
does not come from the queue model.

usage: python experiments/crosscheck_pair_maker.py --data-dir DATA --outcomes OUTCOMES.json
       (reads the chosen version and the EXAM markets from reports/pair_maker_btc_5m.json)
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from pmlab.backtest.report import format_card, report_card
from pmlab.data.loaders import load_books
from pmlab.data.outcomes import load_outcomes, winner_of
from pmlab.strategies.pair_maker import PairMaker

REPORTS = Path(__file__).resolve().parents[1] / "reports"


def prints_by_second(path: Path, start: int) -> dict[int, list[tuple[int, float]]]:
    out: dict[int, list[tuple[int, float]]] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if '"type": "trade"' not in line:
                continue
            row = json.loads(line)
            if row.get("price") is None or not row.get("size"):
                continue
            second = int(np.ceil(row["ts"] / 1000 - start))  # a print at 10.3 s belongs to (10, 11]
            out.setdefault(second, []).append((0 if row["side"] == "up" else 1, float(row["price"])))
    return out


def simulate(args: tuple[Path, int, dict, dict]) -> dict | None:
    path, start, params, outcome = args
    winner = winner_of(outcome)
    if winner is None:
        return None
    _, books = load_books(path, duration_seconds=300)
    prints = prints_by_second(path, start)
    strategy = PairMaker(**params)
    sh, cost = [0.0, 0.0], [0.0, 0.0]
    for t in range(max(0, int(strategy.t0)), min(int(strategy.t1), 298) + 1):
        book = {s: [None if np.isnan(x) else float(x) for x in books[t, s, :4]] for s in (0, 1)}
        bids, _ = strategy.step(dict(t=t, book=book, sh=sh, cost=cost))
        for side, price, shares in bids:
            touched = any(
                (s == side and p <= price + 1e-9) or (s != side and p >= 1 - price - 1e-9)
                for s, p in prints.get(t + 1, [])
            )
            next_ask = books[t + 1, side, 1]
            if touched or (np.isfinite(next_ask) and next_ask <= price + 1e-9):
                sh[side] += shares
                cost[side] += shares * price
    return {"start": start, "cost": sum(cost), "gross": sh[winner] - sum(cost), "fee": 0.0}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--outcomes", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    summary = json.loads((REPORTS / "pair_maker_btc_5m.json").read_text())
    params = summary["versions"][summary["chosen"]]["params"]
    exam = pd.read_csv(REPORTS / "pair_maker_btc_5m_exam_markets.csv")
    outcomes = load_outcomes(args.outcomes)
    jobs = []
    for start in exam["start"]:
        path = args.data_dir / "btc_5m_data" / f"btc-updown-5m-{start}.ndjson"
        with open(path, encoding="utf-8") as handle:
            cid = json.loads(handle.readline()).get("condition_id")
        jobs.append((path, int(start), params, outcomes.get(cid)))
    with ProcessPoolExecutor(args.workers) as pool:
        rows = pd.DataFrame([r for r in pool.map(simulate, jobs, chunksize=16) if r])
    card = report_card(rows)
    (REPORTS / "pair_maker_btc_5m_crosscheck.json").write_text(
        json.dumps({k: v for k, v in card.items() if k != "per_day"}, indent=2, default=float)
    )
    print(format_card(card, "EXAM, optimistic cross-check simulator (no queue, 1-second decisions)"))


if __name__ == "__main__":
    main()
