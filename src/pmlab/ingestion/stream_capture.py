"""Keep each market's order book in sync from WebSocket events and turn it into recorded rows (schema v2)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

# Schema v2 keeps every v1 field and adds depth, monotonic time and connection
# ids.  Old readers keep working; new readers can rebuild queue / slippage.
SCHEMA_VERSION = 2
DEPTH_LEVELS = int(os.environ.get("PMLAB_DEPTH_LEVELS", "10"))


def public_session():
    import aiohttp

    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    return aiohttp.ClientSession(
        connector=connector, trust_env=True, headers={"User-Agent": "Mozilla/5.0 MarketLogger/1.0"}
    )


def _stamp_ms(data, fallback):
    try:
        stamp = int(float(data.get("timestamp", 0)))
    except (TypeError, ValueError):
        return fallback
    return stamp if stamp > 0 else fallback


@dataclass
class MarketBook:
    bids: dict = field(default_factory=dict)
    asks: dict = field(default_factory=dict)
    stamp_ms: int = 0
    received_ms: int = 0
    initialized: bool = False
    pending: bool = False
    pruned: int = 0  # stale levels dropped by confirm_top (cumulative)

    def reset(self):
        self.bids.clear()
        self.asks.clear()
        self.stamp_ms = 0
        self.initialized = False
        self.pending = False

    def snapshot(self, data, received_ms):
        stamp = _stamp_ms(data, received_ms)
        if stamp < self.stamp_ms:
            return False
        try:
            bids = {
                float(level["price"]): float(level["size"])
                for level in data.get("bids", [])
                if float(level["size"]) > 0
            }
            asks = {
                float(level["price"]): float(level["size"])
                for level in data.get("asks", [])
                if float(level["size"]) > 0
            }
        except (KeyError, TypeError, ValueError):
            return False
        self.bids, self.asks = bids, asks
        self.stamp_ms, self.received_ms = stamp, received_ms
        self.initialized = True
        self.pending = False
        return True

    def delta(self, change, stamp, received_ms):
        if not self.initialized or stamp < self.stamp_ms:
            return False
        try:
            price, size = float(change["price"]), float(change["size"])
        except (KeyError, TypeError, ValueError):
            return False
        if change.get("side") == "BUY":
            levels = self.bids
        elif change.get("side") == "SELL":
            levels = self.asks
        else:
            return False
        if size > 0:
            levels[price] = size
        else:
            levels.pop(price, None)
        self.stamp_ms, self.received_ms = stamp, received_ms
        self.pending = True
        return True

    def confirm_top(self, change):
        """The exchange's best bid/ask after a message must match ours. Levels better than the exchange's top were
        taken by trades without a removal message: drop them and stay in sync (before Oct 21 2026 the whole book was
        dropped here, freezing the recording until the next full book). A better level than any we hold means we
        missed it: unsynced until the next full book."""
        if change.get("best_bid") is not None:
            best = float(change["best_bid"])
            stale = [p for p in self.bids if p > best + 1e-7]
            for p in stale:
                del self.bids[p]
            self.pruned += len(stale)
            if abs(best - max(self.bids, default=0.0)) > 1e-7:
                self.initialized = False
                self.pending = False
                return False
        if change.get("best_ask") is not None:
            best = float(change["best_ask"])
            stale = [p for p in self.asks if p < best - 1e-7] if best > 0 else []
            for p in stale:
                del self.asks[p]
            self.pruned += len(stale)
            if abs(best - min(self.asks, default=1.0)) > 1e-7:
                self.initialized = False
                self.pending = False
                return False
        return True

    def top(self):
        bid = max(self.bids, default=0.0)
        ask = min(self.asks, default=0.99)
        return bid, ask


def _levels(levels, side, limit=DEPTH_LEVELS):
    """Top-of-book depth as [[price, size], ...] in price order (bids desc, asks asc)."""
    if side == "bid":
        items = sorted(levels.items(), key=lambda kv: -kv[0])
    else:
        items = sorted(levels.items(), key=lambda kv: kv[0])
    return [[round(float(price), 4), round(float(size), 2)] for price, size in items[:limit] if size > 0]


def _microprice(bid, ask, bid_size, ask_size):
    total = bid_size + ask_size
    if total > 0:
        return (bid * ask_size + ask * bid_size) / total
    return None


def book_row(market, side, book, source, conn_id=None):
    bid, ask = book.top()
    market.update_book(side, bid, ask, book.received_ms)
    bid_size = book.bids.get(bid, 0.0)
    ask_size = book.asks.get(ask, 0.0)
    depth_bid = _levels(book.bids, "bid")
    depth_ask = _levels(book.asks, "ask")
    micro = _microprice(bid, ask, bid_size, ask_size)
    return {
        "v": SCHEMA_VERSION,
        "ts": book.received_ms,
        "source_ts": book.stamp_ms,
        "ts_mono": time.monotonic_ns(),
        "conn": conn_id,
        "type": "book",
        "source_event": source,
        "side": side,
        "best_bid": bid,
        "best_ask": ask,
        "spread": round(ask - bid, 4),
        "mid": round((bid + ask) / 2, 4) if bid > 0 else ask,
        "micro": round(micro, 4) if micro is not None else None,
        "bid_size_total": round(sum(book.bids.values()), 2),
        "ask_size_total": round(sum(book.asks.values()), 2),
        "best_bid_size": bid_size,
        "best_ask_size": ask_size,
        "book_imbalance": (
            round((bid_size - ask_size) / (bid_size + ask_size), 4) if bid_size + ask_size > 0 else None
        ),
        "bid_levels": len(book.bids),
        "ask_levels": len(book.asks),
        "bids": depth_bid,
        "asks": depth_ask,
        "pruned_levels": book.pruned,
        "initialized": book.initialized,
        "outcome_up": round(market.up_mid, 4),
        "outcome_down": round(market.down_mid, 4),
    }


def capture_market_event(logger, data, received_ms):
    """Apply a full book or a batch of level changes; return rows written."""
    conn_id = getattr(logger, "conn_id", None)
    event = data.get("event_type")
    if event == "book":
        token = data.get("asset_id")
        slug = logger.token_to_slug.get(token)
        market = logger.markets.get(slug)
        if market is None or market.closed:
            return 0
        side = "up" if token == market.yes_token_id else "down"
        book = market.books[side]
        if not book.snapshot(data, received_ms):
            return 0
        logger.last_matched_time = time.time()
        market.write_row(book_row(market, side, book, "book", conn_id))
        return 1

    if event != "price_change":
        return 0
    stamp = _stamp_ms(data, received_ms)
    changed = {}
    for change in data.get("price_changes", []):
        token = change.get("asset_id")
        slug = logger.token_to_slug.get(token)
        market = logger.markets.get(slug)
        if market is None or market.closed:
            continue
        side = "up" if token == market.yes_token_id else "down"
        book = market.books[side]
        if book.delta(change, stamp, received_ms):
            changed[(slug, side)] = change
            logger.last_matched_time = time.time()
    written = 0
    for (slug, side), change in changed.items():
        market = logger.markets[slug]
        book = market.books[side]
        pruned_before = book.pruned
        in_sync = book.confirm_top(change)
        dropped = book.pruned - pruned_before
        if dropped or not in_sync:
            # Explicit gap marker: the old logger dropped stale levels silently,
            # which is why frozen stretches had to be inferred months later.
            market.write_row(
                {
                    "v": SCHEMA_VERSION,
                    "ts": received_ms,
                    "source_ts": stamp,
                    "ts_mono": time.monotonic_ns(),
                    "conn": conn_id,
                    "type": "resync",
                    "side": side,
                    "reason": "stale_levels" if dropped else "unsynced",
                    "dropped": dropped,
                }
            )
            written += 1
        if in_sync:
            bid, ask = book.top()
            market.update_book(side, bid, ask, received_ms)
    return written


def flush_market_changes(market, conn_id=None):
    """Persist the latest changed top per side at most once per snapshot tick."""
    written = 0
    for side, book in market.books.items():
        if book.initialized and book.pending:
            market.write_row(book_row(market, side, book, "price_change", conn_id))
            book.pending = False
            written += 1
    return written
