"""Price state shared by the recorders and charts."""

from __future__ import annotations


def _price(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 <= parsed <= 1 else None


def held_book_price(bid, ask, previous=0.50) -> float:
    """Best available price estimate from the top of book, keeping the previous one when the book is empty.

    Both sides quoted -> mid; only an ask -> ask; only a bid -> bid; neither -> ``previous`` (default 0.50).
    An ask of 0.99 or more counts as "no ask" (the exchange's empty-book placeholder).
    """
    bid = _price(bid)
    ask = _price(ask)
    previous = _price(previous)

    has_bid = bid is not None and bid > 0
    has_ask = ask is not None and ask < 0.99
    if has_bid and has_ask:
        return (bid + ask) / 2
    if has_ask:
        return ask
    if has_bid:
        return bid
    return previous if previous is not None else 0.50
