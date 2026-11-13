"""The report card: the same numbers for every strategy, every time.

Input is one row per market (``start``, ``cost``, ``gross``, ``fee``, ``fills``). Reported:

* dollars and ROI on money spent: before fees, after the full fee, and with ``REBATE`` of the fee returned;
* markets entered out of available, markets won / lost, days up out of days;
* the worst stretch: the largest fall of cumulative profit from a previous high, in dollars and as a multiple of the
  average spend in a market entered (a stretch of 30x means 30 markets' worth of money lost in a row);
* how much of the total the best day carries (one lucky day should not be the whole result).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pmlab.backtest.fees import after_fees
from pmlab.backtest.splits import market_day

REBATE = 0.32  # fee share an account gets back after about a month of volume; the full-fee number is the honest one


def report_card(rows: pd.DataFrame) -> dict:
    df = rows.sort_values("start").reset_index(drop=True)
    entered = df[df["cost"] > 0]
    net = after_fees(df["gross"], df["fee"])
    spend = float(df["cost"].sum())
    by_day = net.groupby(df["start"].map(market_day)).sum()
    equity = np.concatenate([[0.0], net.cumsum().to_numpy()])
    worst = float((np.maximum.accumulate(equity) - equity).max())
    avg_spend = float(entered["cost"].mean()) if len(entered) else 0.0
    total = float(net.sum())
    best_day = float(by_day.max()) if len(by_day) else 0.0

    def roi(x: float) -> float:
        return x / spend if spend else 0.0

    gross, fee = float(df["gross"].sum()), float(df["fee"].sum())
    return {
        "markets": len(df),
        "entered": len(entered),
        "spent": spend,
        "avg_spend": avg_spend,
        "gross": gross,
        "roi_gross": roi(gross),
        "fees": fee,
        "net": total,
        "roi_net": roi(total),
        "net_rebate": after_fees(gross, fee, REBATE),
        "roi_net_rebate": roi(after_fees(gross, fee, REBATE)),
        "won": int((net[df["cost"] > 0] > 1e-9).sum()),
        "lost": int((net[df["cost"] > 0] < -1e-9).sum()),
        "days": len(by_day),
        "days_up": int((by_day > 0).sum()),
        "worst_stretch": worst,
        "worst_x_spend": worst / avg_spend if avg_spend else 0.0,
        "best_day_share": best_day / total if total > 0 else None,
        "per_day": by_day,
    }


def format_card(card: dict, title: str) -> str:
    """Markdown table of one report card."""
    c = card
    share = f"{100 * c['best_day_share']:.0f}%" if c["best_day_share"] is not None else "n/a (not profitable)"
    lines = [
        f"**{title}**",
        "",
        "| | |",
        "|---|---|",
        f"| Markets entered | {c['entered']:,} of {c['markets']:,} |",
        f"| Money spent | ${c['spent']:,.0f} (${c['avg_spend']:,.2f} a market entered) |",
        f"| Profit before fees | ${c['gross']:+,.0f} ({100 * c['roi_gross']:+.2f}%) |",
        f"| Profit after the full fee | ${c['net']:+,.0f} ({100 * c['roi_net']:+.2f}%) |",
        f"| With {100 * REBATE:.0f}% of the fee back | ${c['net_rebate']:+,.0f} ({100 * c['roi_net_rebate']:+.2f}%) |",
        f"| Markets won / lost | {c['won']:,} / {c['lost']:,} |",
        f"| Days up | {c['days_up']} of {c['days']} |",
        f"| Worst stretch | ${c['worst_stretch']:,.0f} ({c['worst_x_spend']:.1f}x the average spend) |",
        f"| Best day's share of the profit | {share} |",
    ]
    return "\n".join(lines)
