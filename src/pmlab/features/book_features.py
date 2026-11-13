"""Per-second market features from an as-of book array (see :mod:`pmlab.data.loaders`).

Every feature at second ``t`` uses only ``books[:t + 1]``, so features can be fed to a decision at ``t`` without
look-ahead. ``tests/test_loaders_and_features.py`` checks this by changing the future and comparing.
"""

from __future__ import annotations

import numpy as np

from pmlab.data.loaders import quote_recorded, quote_valid

MOVE_LAGS = (2, 5, 15, 30)
RANGE_WINDOWS = (10, 30)
LEADER_LEVEL = 0.60  # a side "leads" once its mid is at or above this

MARKET_FEATURES = ["elapsed", "remaining"]
for _side in ("up", "down"):
    MARKET_FEATURES += [
        f"{_side}_{x}"
        for x in (
            "bid",
            "ask",
            "spread",
            "quote_age",
            "depth_imbalance",
            *(f"move_{lag}s" for lag in MOVE_LAGS),
            *(f"{kind}_{window}s" for window in RANGE_WINDOWS for kind in ("rebound", "drawdown")),
        )
    ]
MARKET_FEATURES += ["ask_sum", "leader_switches", "since_switch"]


def market_features(books: np.ndarray, max_age: float, allow_empty: bool = False):
    """Return ``(features, valid)``: a ``(duration, len(MARKET_FEATURES))`` array and a per-second validity mask.

    - ``move_Ns``: change of each side's mid over the last N seconds.
    - ``rebound_Ws`` / ``drawdown_Ws``: mid minus the lowest / highest mid of the last W seconds.
    - ``ask_sum``: UP ask + DOWN ask (the price of buying one share of each side).
    - ``leader_switches``: how many times the leading side (mid >= 0.60) has changed so far.
    """
    duration = len(books)
    result = np.full((duration, len(MARKET_FEATURES)), np.nan)
    mids = books[:, :, :2].mean(axis=2)
    if allow_empty:
        mids[(books[:, :, 2] == 0) & (books[:, :, 3] == 0)] = np.nan
    check = quote_recorded if allow_empty else quote_valid
    valid = np.array([[check(books[t, s], t, max_age) for s in range(2)] for t in range(duration)])
    mids[~valid] = np.nan
    leader, switches, switched = -1, 0, 0
    for t in range(duration):
        row = [t / duration, (duration - t) / duration]
        for side in range(2):
            bid, ask, bid_size, ask_size, stamp = books[t, side]
            row += [bid, ask, ask - bid, t - stamp, (bid_size - ask_size) / max(bid_size + ask_size, 1)]
            row += [mids[t, side] - mids[max(0, t - lag), side] for lag in MOVE_LAGS]
            for window in RANGE_WINDOWS:
                recent = mids[max(0, t - window) : t + 1, side]
                finite = recent[np.isfinite(recent)]
                row += [mids[t, side] - finite.min(), mids[t, side] - finite.max()] if len(finite) else [np.nan, np.nan]
        if valid[t].all():
            new_leader = 0 if mids[t, 0] >= LEADER_LEVEL else 1 if mids[t, 1] >= LEADER_LEVEL else -1
            if new_leader >= 0 and new_leader != leader:
                switches += int(leader >= 0)
                leader, switched = new_leader, t
        row += [books[t, :, 1].sum(), switches, t - switched]
        result[t] = row
    return result, valid.all(axis=1)
