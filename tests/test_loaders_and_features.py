import json

import numpy as np
import pytest

from pmlab.data.dataset_policy import CUTOFF_EPOCH
from pmlab.data.loaders import load_books
from pmlab.features.book_features import MARKET_FEATURES, market_features
from pmlab.features.price_state import held_book_price

START = CUTOFF_EPOCH


def write_market(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def book_row(t, side, bid, ask, bid_size=10, ask_size=10):
    return {
        "type": "book",
        "ts": (START + t) * 1000,
        "side": side,
        "best_bid": bid,
        "best_ask": ask,
        "best_bid_size": bid_size,
        "best_ask_size": ask_size,
    }


def random_market(rng, seconds=300):
    rows, up = [], 0.5
    for t in range(seconds):
        up = float(np.clip(up + rng.normal(0, 0.02), 0.05, 0.95))
        rows.append(book_row(t, "up", round(up - 0.01, 2), round(up + 0.01, 2)))
        rows.append(book_row(t, "down", round(1 - up - 0.01, 2), round(1 - up + 0.01, 2)))
    return rows


def test_books_are_as_of_each_second(tmp_path):
    path = write_market(
        tmp_path / f"btc-updown-5m-{START}.ndjson", [book_row(0, "up", 0.40, 0.42), book_row(10, "up", 0.60, 0.62)]
    )
    _, books = load_books(path)
    assert books.shape == (300, 2, 5)
    assert books[9, 0, 1] == 0.42  # still the second-0 book
    assert books[10, 0, 1] == 0.62
    assert np.isnan(books[0, 1]).all()  # DOWN never seen


def test_pre_cutoff_market_is_refused(tmp_path):
    path = write_market(tmp_path / f"btc-updown-5m-{START - 300}.ndjson", [])
    with pytest.raises(ValueError):
        load_books(path)


def test_preserve_empty_marks_empty_levels_unfillable(tmp_path):
    path = write_market(tmp_path / f"btc-updown-5m-{START}.ndjson", [book_row(0, "up", 0.40, 0.42, ask_size=0)])
    _, books = load_books(path, preserve_empty=True)
    assert (books[0, 0, 1], books[0, 0, 3]) == (1.0, 0.0)


def test_features_have_no_look_ahead(tmp_path):
    """Changing everything after second T must not change any feature at or before T."""
    rng = np.random.default_rng(7)
    rows = random_market(rng)
    _, books = load_books(write_market(tmp_path / f"btc-updown-5m-{START}.ndjson", rows))
    T = 150
    altered = rows[: 2 * (T + 1)] + random_market(np.random.default_rng(99))[2 * (T + 1) :]
    _, books_altered = load_books(write_market(tmp_path / f"eth-updown-5m-{START}.ndjson", altered))

    features, valid = market_features(books, max_age=5)
    features_altered, valid_altered = market_features(books_altered, max_age=5)
    assert features.shape == (300, len(MARKET_FEATURES))
    np.testing.assert_array_equal(features[: T + 1], features_altered[: T + 1])
    np.testing.assert_array_equal(valid[: T + 1], valid_altered[: T + 1])
    assert not np.array_equal(features[T + 1 :], features_altered[T + 1 :], equal_nan=True)


@pytest.mark.parametrize(
    "bid, ask, previous, expected",
    [
        (0.40, 0.44, 0.5, 0.42),  # both sides: mid
        (0.0, 0.44, 0.5, 0.44),  # ask only
        (0.40, 0.99, 0.5, 0.40),  # 0.99 ask = no ask
        (0.0, 0.99, 0.31, 0.31),  # empty book: keep the previous price
        ("x", None, None, 0.50),  # garbage in: neutral default
    ],
)
def test_held_book_price(bid, ask, previous, expected):
    assert held_book_price(bid, ask, previous) == pytest.approx(expected)
