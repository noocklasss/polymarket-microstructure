"""Polymarket fees on these markets.

A taker (market) buy pays ``TAKER_FEE_RATE x p x (1 - p)`` per share at price ``p``: about 1.75c a share at 50c and
almost nothing near 0c or 100c. Resting (maker) fills pay no fee. Accounts that trade enough get part of the fee back;
results are reported with the full fee, which is what a new account pays.
"""

from __future__ import annotations

TAKER_FEE_RATE = 0.07


def taker_fee(price: float, shares: float) -> float:
    return TAKER_FEE_RATE * price * (1 - price) * shares


def after_fees(gross: float, fee: float, rebate: float = 0.0) -> float:
    """Profit after fees, with ``rebate`` (0..1) of the fee returned."""
    return gross - (1 - rebate) * fee
