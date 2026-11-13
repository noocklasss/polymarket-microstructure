from datetime import datetime

import numpy as np
import pytest

from pmlab.data.dataset_policy import (
    CUTOFF_EPOCH,
    DROP,
    MARKET_TIMEZONE,
    eligible,
    filter_metadata,
    market_start,
    retain,
)


def test_cutoff_is_midnight_eastern_on_1_august():
    assert not eligible({"slug": f"btc-updown-5m-{CUTOFF_EPOCH - 300}"})
    for coin in ("btc", "eth", "sol", "xrp"):
        assert eligible({"slug": f"{coin}-updown-5m-{CUTOFF_EPOCH}"})


def test_title_dates_and_non_markets():
    assert not eligible("Bitcoin_Up_or_Down_-_July_31_1155PM-1200AM_ET.png")
    assert eligible("Solana_Up_or_Down_-_August_1_1200AM-1205AM_ET.png")
    assert market_start("Screenshot 2026-04-06.png") is None
    assert market_start("requirements.txt") is None


def test_unknown_start_fails_closed():
    with pytest.raises(ValueError):
        eligible({"price": 0.5})


def test_retain_keeps_books_and_metadata_aligned():
    rows = [{"start": CUTOFF_EPOCH - 300}, {"start": CUTOFF_EPOCH}, {"start": CUTOFF_EPOCH + 300}]
    books, markets = retain(np.array([[10], [20], [30]]), rows)
    assert books.tolist() == [[20], [30]]
    assert markets == rows[1:]


def test_retain_rejects_misaligned_inputs():
    with pytest.raises(ValueError):
        retain(np.array([[1], [2]]), [{"start": CUTOFF_EPOCH}])


def test_filter_metadata_recurses_into_lists_and_keys():
    old = f"eth-updown-5m-{CUTOFF_EPOCH - 300}"
    new = f"eth-updown-5m-{CUTOFF_EPOCH}"
    value, removed = filter_metadata(
        {"markets": [{"slug": old}, {"slug": new}], "outcomes": {old: 1, new: 0}, "budget": 40}
    )
    assert removed == 2
    assert value == {"markets": [{"slug": new}], "outcomes": {new: 0}, "budget": 40}
    assert filter_metadata({"slug": old}) == (DROP, 1)


def test_trade_row_timestamp_beats_yearless_legacy_title():
    old = datetime(2025, 12, 21, tzinfo=MARKET_TIMEZONE).timestamp()
    assert not eligible({"title": "Bitcoin Up or Down - December 21, 10AM ET", "asset": "x", "timestamp": old})
