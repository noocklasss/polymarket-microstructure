import pandas as pd
import pytest

from pmlab.backtest.report import REBATE, format_card, report_card
from pmlab.backtest.splits import market_day, time_split

DAY = 86400
T0 = 1785556800 + 3600  # 1 Aug 2026, 01:00 New York


def test_split_is_in_time_order_on_whole_days():
    starts = [T0 + d * DAY + m * 300 for d in range(10) for m in range(10)]
    blocks = time_split(list(reversed(starts)))
    assert [len(blocks[b]) for b in ("train", "valid", "exam")] == [60, 20, 20]
    assert max(blocks["train"]) < min(blocks["valid"]) and max(blocks["valid"]) < min(blocks["exam"])
    for b in blocks.values():  # no day split across blocks
        days = {market_day(s) for s in b}
        assert all(market_day(s) in days for s in b)
    assert len({market_day(s) for s in blocks["train"]} & {market_day(s) for s in blocks["exam"]}) == 0


def test_split_rejects_bad_fractions():
    with pytest.raises(ValueError):
        time_split([T0], fractions=(0.5, 0.2, 0.2))


def rows():
    return pd.DataFrame(
        {
            "start": [T0, T0 + 300, T0 + 600, T0 + DAY, T0 + DAY + 300],
            "cost": [10.0, 0.0, 10.0, 10.0, 10.0],
            "gross": [5.0, 0.0, -10.0, -4.0, 2.0],
            "fee": [1.0, 0.0, 0.0, 0.0, 0.0],
        }
    )


def test_report_card_numbers():
    c = report_card(rows())
    assert (c["markets"], c["entered"], c["spent"], c["avg_spend"]) == (5, 4, 40.0, 10.0)
    assert c["gross"] == -7 and c["net"] == -8
    assert c["net_rebate"] == pytest.approx(-7 - (1 - REBATE))
    assert c["roi_net"] == pytest.approx(-8 / 40)
    assert (c["won"], c["lost"]) == (2, 2)
    assert (c["days"], c["days_up"]) == (2, 0)  # day 1: 4 - 10 = -6, day 2: -2
    # equity 0, 4, 4, -6, -10, -8 -> worst fall from the high of 4 is 14, 1.4x the 10-dollar average spend
    assert c["worst_stretch"] == pytest.approx(14) and c["worst_x_spend"] == pytest.approx(1.4)
    assert c["best_day_share"] is None


def test_format_card_is_markdown():
    text = format_card(report_card(rows()), "Example")
    assert text.startswith("**Example**") and "| Markets entered | 4 of 5 |" in text
