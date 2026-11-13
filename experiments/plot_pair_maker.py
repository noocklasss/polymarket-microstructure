"""Chart for reports/pair_maker_btc_5m.md, from the study's JSON files. usage: python experiments/plot_pair_maker.py"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPORTS = Path(__file__).resolve().parents[1] / "reports"
SURFACE, TEXT, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
TRAIN, VALID = "#2a78d6", "#eb6834"  # categorical slots 1 and 2


def main() -> None:
    s = json.loads((REPORTS / "pair_maker_btc_5m.json").read_text())
    cross = json.loads((REPORTS / "pair_maker_btc_5m_crosscheck.json").read_text())
    versions = s["versions"]
    labels = [
        f"{v['params']['pair_max']:.2f} · {v['params']['shares']} sh · {v['params']['max_ratio']}x" for v in versions
    ]

    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.edgecolor": GRID,
            "axes.labelcolor": MUTED,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "text.color": TEXT,
            "font.family": "DejaVu Sans",
        }
    )
    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 5.6), gridspec_kw={"width_ratios": [1.5, 1]}, facecolor=SURFACE)
    for ax in (a, b):
        ax.set_facecolor(SURFACE)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.axvline(0, color=MUTED, linewidth=1)

    y = range(len(versions))
    h = 0.36
    a.barh(
        [i - h / 2 - 0.02 for i in y],
        [100 * v["train_roi_net"] for v in versions],
        height=h,
        color=TRAIN,
        label="Train (Aug 13 - Oct 29)",
    )
    a.barh(
        [i + h / 2 + 0.02 for i in y],
        [100 * v["valid_roi_net"] for v in versions],
        height=h,
        color=VALID,
        label="Validation (Oct 30 - Nov 6)",
    )
    a.set_yticks(list(y), labels)
    a.invert_yaxis()
    a.set_xlabel("Profit as % of money spent")
    a.set_title(
        "All 12 versions lose on both blocks\n(max pair cost · bid size · max lopsidedness)",
        loc="left",
        fontsize=12,
        color=TEXT,
        pad=30,
    )
    a.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, labelcolor=TEXT, borderaxespad=0.2)
    a.set_xlim(-3.2, 0.4)

    names = ["Realistic queue\n(the result)", "Prints-only fills\n(no ask rule)", "Always first in line\n(upper bound)"]
    vals = [100 * s["exam"]["roi_net"], 100 * s["exam_prints_only"]["roi_net"], 100 * cross["roi_net"]]
    b.barh(names, vals, height=0.5, color=TRAIN)
    for i, v in enumerate(vals):
        b.text(v - 0.06, i, f"{v:+.2f}%", va="center", ha="right", color=TEXT, fontsize=10)
    b.invert_yaxis()
    b.set_xlim(-3.2, 0.4)
    b.set_xlabel("Profit as % of money spent")
    b.set_title("Exam, Nov 7 - 11 (never seen):\nstill negative", loc="left", fontsize=12, color=TEXT, pad=30)

    fig.suptitle(
        "Two-sided pair maker on BTC 5-minute markets: 7,699 markets, no fees on resting fills",
        x=0.01,
        ha="left",
        fontsize=13,
        color=TEXT,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(REPORTS / "pair_maker_btc_5m.png", dpi=150, facecolor=SURFACE)


if __name__ == "__main__":
    main()
