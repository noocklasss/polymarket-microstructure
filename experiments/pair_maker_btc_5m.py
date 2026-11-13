"""Does the textbook two-sided pair maker make money on BTC 5-minute markets? Writes ``reports/pair_maker_btc_5m.md``.

The protocol is fixed before any result is seen:

1. Markets: every recorded BTC 5m market in the research window with a final result and a recording that passes the
   quality audit. Split in time order into TRAIN (earliest ~60%), VALID (next ~20%) and EXAM (latest ~20%), on whole
   days.
2. Versions: the 12 combinations of ``GRID`` below, all run on TRAIN and VALID.
3. Choice: the 3 best versions on TRAIN (profit after fees) are ranked on VALID; the best one there is chosen.
4. Exam: the chosen version is run once on EXAM. Its report card is the result.
5. Sensitivity: the chosen version on EXAM again with fills from trade prints only (the kinder fill rule).

usage: python experiments/pair_maker_btc_5m.py --data-dir DATA --outcomes OUTCOMES.json [--workers 8]
"""

from __future__ import annotations

import argparse
import itertools
import json
import time
from pathlib import Path

import pandas as pd

from pmlab.backtest.report import format_card, report_card
from pmlab.backtest.resting_engine import EngineConfig
from pmlab.backtest.runner import market_files, run_many
from pmlab.backtest.splits import market_day, time_split
from pmlab.data.outcomes import load_outcomes
from pmlab.strategies.pair_maker import PairMaker

GRID = {"pair_max": (0.94, 0.97, 1.00), "shares": (10, 20), "max_ratio": (1.3, 2.0)}
BUDGET_PER_SHARE = 30  # budget = 30 x shares a bid
CONFIG = EngineConfig(dt=0.25, cross=True, queue="last", duration=300)
OUT = Path(__file__).resolve().parents[1] / "reports"


def versions() -> list[dict]:
    keys = list(GRID)
    out = []
    for values in itertools.product(*GRID.values()):
        params = dict(zip(keys, values, strict=True))
        params["budget"] = BUDGET_PER_SHARE * params["shares"]
        out.append(params)
    return out


def name_of(p: dict) -> str:
    return f"pair_max {p['pair_max']:.2f}, {p['shares']} shares, max ratio {p['max_ratio']}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--outcomes", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    t0 = time.time()
    outcomes = load_outcomes(args.outcomes)
    files = market_files(args.data_dir, "btc_5m_data")
    grid = versions()

    # 1. usable markets (the first version's run also audits every recording)
    first, skipped = run_many(files, files, PairMaker, grid[0], outcomes, CONFIG, args.workers, check_quality=True)
    usable = sorted(first["start"])
    blocks = time_split(usable)
    print(f"{len(files)} recordings, {len(usable)} usable, left out: {dict(skipped)}")

    # 2. every version on TRAIN and VALID
    results: dict[tuple[str, int], pd.DataFrame] = {}
    for i, p in enumerate(grid):
        if i == 0:
            rows = first
        else:
            rows, _ = run_many(
                files,
                blocks["train"] + blocks["valid"],
                PairMaker,
                p,
                outcomes,
                CONFIG,
                args.workers,
                check_quality=False,
            )
        for block in ("train", "valid"):
            results[(block, i)] = rows[rows["start"].isin(blocks[block])]
        print(f"version {i + 1}/{len(grid)} done ({time.time() - t0:.0f} s)")
    cards = {key: report_card(df) for key, df in results.items()}

    # 3. choose: top 3 on TRAIN, best of those on VALID
    shortlist = sorted(range(len(grid)), key=lambda i: cards[("train", i)]["net"], reverse=True)[:3]
    chosen = max(shortlist, key=lambda i: cards[("valid", i)]["net"])

    # 4. the exam, once; 5. the same with prints-only fills
    exam, _ = run_many(files, blocks["exam"], PairMaker, grid[chosen], outcomes, CONFIG, args.workers, False)
    kind = EngineConfig(**{**CONFIG.__dict__, "cross": False})
    exam_kind, _ = run_many(files, blocks["exam"], PairMaker, grid[chosen], outcomes, kind, args.workers, False)
    exam_card, kind_card = report_card(exam), report_card(exam_kind)

    OUT.mkdir(exist_ok=True)
    summary = {
        "blocks": {b: [market_day(min(s)), market_day(max(s)), len(s)] for b, s in blocks.items()},
        "left_out": dict(skipped),
        "versions": [
            {
                "params": grid[i],
                **{f"{b}_{k}": cards[(b, i)][k] for b in ("train", "valid") for k in ("net", "roi_net")},
            }
            for i in range(len(grid))
        ],
        "shortlist": shortlist,
        "chosen": chosen,
        "exam": {k: v for k, v in exam_card.items() if k != "per_day"},
        "exam_prints_only": {k: v for k, v in kind_card.items() if k != "per_day"},
        "exam_per_day": exam_card["per_day"].round(2).to_dict(),
    }
    (OUT / "pair_maker_btc_5m.json").write_text(json.dumps(summary, indent=2, default=float))
    exam.assign(day=exam["start"].map(market_day)).to_csv(OUT / "pair_maker_btc_5m_exam_markets.csv", index=False)
    print(format_card(exam_card, f"EXAM, chosen version: {name_of(grid[chosen])}"))
    print(format_card(kind_card, "EXAM, same version, prints-only fills"))
    print(f"done in {time.time() - t0:.0f} s -> {OUT}")


if __name__ == "__main__":
    main()
