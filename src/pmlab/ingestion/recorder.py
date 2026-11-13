"""Record every market of one family (e.g. BTC 5-minute Up/Down) from the Polymarket market WebSocket.

For each market the recorder writes ``<data_dir>/<family>_data/<slug>.ndjson``: a ``meta`` row, then ``book``,
``trade``, ``resync``, ``tick_size`` and 1-second ``snapshot`` rows (schema v2, see
:mod:`pmlab.ingestion.stream_capture`).
Next to it go ``<slug>.meta.json`` (the raw Gamma market payload) and, about two minutes after the close,
``settlements/<slug>.json`` with the official winner. A recording-quality report is written when a market closes.

Five loops run side by side:
  - discovery: every 10 s, look up the current and next markets by slug; close finished ones
  - websocket: stream book and trade events; reconnect on silence (60 s), errors, and every 5 minutes regardless
  - snapshot:  once a second, write pending book changes, a heartbeat every few seconds, and a snapshot row
  - flush / stats: flush files to disk every 10 s, log a status line every 30 s
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

from pmlab.data.quality import save_log_quality
from pmlab.ingestion.stream_capture import (
    DEPTH_LEVELS,
    SCHEMA_VERSION,
    MarketBook,
    _levels,
    _microprice,
    book_row,
    capture_market_event,
    flush_market_changes,
    public_session,
)
from pmlab.ingestion.tracked_market import TrackedMarket

GAMMA_URL = "https://gamma-api.polymarket.com"
WSS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
ET = ZoneInfo("America/New_York")
TIMEFRAME_MINUTES = {"5m": 5, "15m": 15, "1h": 60}

DISCOVERY_INTERVAL = 10  # seconds between market look-ups
STATS_INTERVAL = 30
FLUSH_INTERVAL = 10
RESUBSCRIBE_INTERVAL = 30  # re-send the subscription to keep it alive server-side
STALE_TIMEOUT = 60  # reconnect when no event for our markets arrives for this long
HARD_RECONNECT = 300  # reconnect at least this often as a safety net
CLOSE_GRACE = 60  # seconds after the end time before a market's file is closed
SETTLEMENT_DELAY = 120  # seconds after closing before the official result is fetched

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ configuration


@dataclass(frozen=True)
class MarketFamily:
    name: str
    slug_prefix: str
    timeframe: str
    heartbeat_seconds: int = 5
    tail_seconds: int = 55
    full_snapshots: bool = True
    slots_ahead: int = 3

    @property
    def slot_minutes(self) -> int:
        return TIMEFRAME_MINUTES[self.timeframe]

    @property
    def duration_seconds(self) -> int:
        return self.slot_minutes * 60

    def slug_at(self, moment: datetime) -> str:
        """Slug of the market running at ``moment``: ``<prefix>-updown-<tf>-<unix start of its slot>``."""
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ET)
        moment = moment.astimezone(ET)
        minute = (moment.minute // self.slot_minutes) * self.slot_minutes if self.slot_minutes < 60 else 0
        start = moment.replace(minute=minute, second=0, microsecond=0)
        return f"{self.slug_prefix}-updown-{self.timeframe}-{int(start.timestamp())}"

    def upcoming_slugs(self, now: datetime) -> list[str]:
        return [self.slug_at(now + timedelta(minutes=self.slot_minutes * i)) for i in range(self.slots_ahead)]


def load_families(path: Path | None = None) -> dict[str, MarketFamily]:
    """Read market families from a TOML file (default: the packaged ``pmlab/config/markets.toml``)."""
    if path is None:
        text = resources.files("pmlab.config").joinpath("markets.toml").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    config = tomllib.loads(text)
    defaults = config.get("defaults", {})
    families = {}
    for name, values in config["families"].items():
        family = MarketFamily(name=name, **{**defaults, **values})
        if family.timeframe not in TIMEFRAME_MINUTES:
            raise ValueError(f"{name}: unknown timeframe {family.timeframe!r}")
        families[name] = family
    return families


# ------------------------------------------------------------------ pure helpers (unit-tested)


def _json_list(value) -> list:
    return json.loads(value) if isinstance(value, str) else list(value or [])


def _iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def pick_market(event: dict, slug: str) -> dict | None:
    """The market of ``slug`` inside a Gamma event; events can hold several markets, so never assume the first."""
    markets = event.get("markets") or []
    if not markets:
        return None
    market = markets[0]
    if market.get("slug") and market.get("slug") != slug:
        market = next((m for m in markets if m.get("slug") == slug), market)
    return market


def meta_row(slug: str, market: dict, sidecar_name: str) -> dict:
    """First row of a recording: what the market is, its tokens, rules and the price when recording began."""
    tokens = _json_list(market.get("clobTokenIds", "[]"))
    prices = _json_list(market.get("outcomePrices", '["0.5","0.5"]'))
    return {
        "v": SCHEMA_VERSION,
        "type": "meta",
        "slug": slug,
        "question": market.get("question", slug),
        "condition_id": market.get("conditionId", ""),
        "yes_token_id": tokens[0],
        "no_token_id": tokens[1],
        "end_time": market.get("endDate", ""),
        "event_start_time": market.get("eventStartTime", ""),
        "up_price": float(prices[0]),
        "down_price": float(prices[1]),
        "logged_at": datetime.now(tz=ET).isoformat(),
        "tick_size": market.get("orderPriceMinTickSize"),
        "minimum_order_size": market.get("orderMinSize"),
        "neg_risk": market.get("negRisk"),
        "fee_rate_bps": market.get("feeRateBps") or market.get("fee"),
        "accepting_orders": market.get("acceptingOrders"),
        "resolution_source": market.get("resolutionSource"),
        "uma_status": market.get("umaResolutionStatus"),
        "closed_time": market.get("closedTime"),
        "meta_file": sidecar_name,
    }


def settlement_winner(market: dict) -> str | None:
    """'up' / 'down' once the market has resolved (one outcome at >= 0.99, the other at <= 0.01), else None."""
    try:
        prices = [float(p) for p in _json_list(market.get("outcomePrices", "[]"))]
    except (TypeError, ValueError):
        return None
    outcomes = _json_list(market.get("outcomes", "[]"))
    if not prices or not outcomes or max(prices) < 0.99 or min(prices) > 0.01:
        return None
    winner = outcomes[prices.index(max(prices))]
    return winner.lower() if isinstance(winner, str) and winner.lower() in ("up", "down") else winner


def side_state(book: MarketBook) -> dict:
    """Compact state of one side for the 1-second snapshot (top 5 levels)."""
    bid, ask = book.top()
    bid_size, ask_size = book.bids.get(bid, 0.0), book.asks.get(ask, 0.0)
    micro = _microprice(bid, ask, bid_size, ask_size)
    return {
        "bid": bid,
        "ask": ask,
        "mid": round((bid + ask) / 2, 4) if bid > 0 else ask,
        "micro": round(micro, 4) if micro is not None else None,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "bid_total": round(sum(book.bids.values()), 2),
        "ask_total": round(sum(book.asks.values()), 2),
        "imbalance": round((bid_size - ask_size) / (bid_size + ask_size), 4) if bid_size + ask_size > 0 else None,
        "bids": _levels(book.bids, "bid", 5),
        "asks": _levels(book.asks, "ask", 5),
        "initialized": book.initialized,
    }


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ the recorder


class Recorder:
    def __init__(self, family: MarketFamily, data_dir: Path):
        self.family = family
        self.data_dir = Path(data_dir)
        self.output_dir = self.data_dir / f"{family.name}_data"
        self.settlements_dir = self.output_dir / "settlements"
        self.markets: dict[str, TrackedMarket] = {}
        self.token_to_slug: dict[str, str] = {}
        self.ws = None
        self.running = False
        self.conn_id = 0  # incremented on every WebSocket (re)connect
        self.total_rows = 0
        self.markets_completed = 0
        self.wss_messages = 0
        self.last_matched_time = time.time()
        self._last_trade_key: dict[tuple, tuple] = {}
        self._background: set[asyncio.Task] = set()

    # ---------------------------------------------------------- discovery

    def track(self, slug: str, event: dict) -> TrackedMarket | None:
        """Start recording ``slug`` from its Gamma event; None if it is closed or malformed."""
        market = pick_market(event, slug)
        if market is None or market.get("closed"):
            return None
        tokens = _json_list(market.get("clobTokenIds", "[]"))
        if len(tokens) < 2:
            return None
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = self.output_dir / f"{slug}.ndjson"
        sidecar = filename.with_suffix(".meta.json")
        payload = {
            "v": SCHEMA_VERSION,
            "slug": slug,
            "fetched_at": datetime.now(tz=ET).isoformat(),
            "event": event,
            "market": market,
        }
        _write_atomic(sidecar, json.dumps(payload, default=str))
        meta = meta_row(slug, market, sidecar.name)
        tracked = TrackedMarket(
            slug=slug,
            question=meta["question"],
            condition_id=meta["condition_id"],
            yes_token_id=tokens[0],
            no_token_id=tokens[1],
            end_time=_iso(meta["end_time"]),
            event_start_time=_iso(meta["event_start_time"]),
            filename=str(filename),
            up_last_price=meta["up_price"],
            down_last_price=meta["down_price"],
        )
        tracked.write_row(meta)
        self.markets[slug] = tracked
        self.token_to_slug.update({tokens[0]: slug, tokens[1]: slug})
        ends = tracked.end_time.astimezone(ET).strftime("%H:%M ET") if tracked.end_time else "?"
        log.info("RECORDING %s | ends %s | -> %s", tracked.question, ends, filename)
        return tracked

    async def discover(self) -> int:
        new = 0
        async with public_session() as session:
            for slug in self.family.upcoming_slugs(datetime.now(tz=ET)):
                if slug in self.markets:
                    continue
                try:
                    async with session.get(
                        f"{GAMMA_URL}/events", params={"slug": slug}, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        events = await resp.json() if resp.status == 200 else None
                except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                    log.debug("look-up %s failed: %s", slug, exc)
                    continue
                try:
                    if events and self.track(slug, events[0]):
                        new += 1
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    log.warning("market %s skipped, unexpected Gamma payload: %s", slug, exc)
        return new

    # ---------------------------------------------------------- websocket

    def handle_message(self, data: dict) -> None:
        self.wss_messages += 1
        event = data.get("event_type", "")
        now_ms = int(time.time() * 1000)
        if event in ("book", "price_change"):
            self.total_rows += capture_market_event(self, data, now_ms)
            return
        token = data.get("asset_id", "")
        slug = self.token_to_slug.get(token)
        market = self.markets.get(slug) if slug else None
        if market is None:
            return
        self.last_matched_time = time.time()
        if market.closed:
            return
        base = {"v": SCHEMA_VERSION, "ts": now_ms, "ts_mono": time.monotonic_ns(), "conn": self.conn_id}
        if event == "tick_size_change":
            market.write_row(
                {
                    **base,
                    "type": "tick_size",
                    "asset": token,
                    "old_tick_size": data.get("old_tick_size"),
                    "new_tick_size": data.get("new_tick_size"),
                    "source_ts": data.get("timestamp"),
                }
            )
            self.total_rows += 1
            return
        if event != "last_trade_price":
            return
        price, size = float(data.get("price") or 0), float(data.get("size") or 0)
        side = "up" if token == market.yes_token_id else "down"
        try:
            source_ts = int(float(data["timestamp"])) if data.get("timestamp") is not None else None
        except (TypeError, ValueError):
            source_ts = None
        # last_trade_price is a state event: the exchange repeats the same print on later updates.
        # Drop consecutive duplicates so the rows can be summed as traded volume.
        key = (source_ts, round(price, 4), round(size, 4), side)
        if self._last_trade_key.get((slug, side)) == key:
            return
        self._last_trade_key[(slug, side)] = key
        market.write_row(
            {
                **base,
                "type": "trade",
                "asset": token,
                "side": side,  # outcome side
                "taker_side": data.get("side"),  # BUY / SELL, from the taker's point of view
                "price": price,
                "size": size,
                "fee_bps": data.get("fee_rate_bps"),
                "source_ts": source_ts,
                "outcome_up": round(market.up_mid, 4),
                "outcome_down": round(market.down_mid, 4),
            }
        )
        self.total_rows += 1

    def _active(self) -> list[TrackedMarket]:
        return [m for m in self.markets.values() if not m.closed]

    async def _subscribe(self, ws) -> None:
        tokens = [t for m in self._active() for t in (m.yes_token_id, m.no_token_id)]
        if tokens:
            await ws.send_json({"assets_ids": tokens, "type": "market"})
            log.info("subscribed to %d tokens (%d markets)", len(tokens), len(tokens) // 2)

    async def websocket_loop(self) -> None:
        while self.running:
            try:
                async with public_session() as session, session.ws_connect(WSS_URL, heartbeat=30) as ws:
                    self.ws = ws
                    self.conn_id += 1
                    for market in self.markets.values():  # a new connection starts every book from scratch
                        for book in market.books.values():
                            book.reset()
                    connected = last_subscribe = self.last_matched_time = time.time()
                    log.info("websocket connected (connection %d)", self.conn_id)
                    await self._subscribe(ws)
                    async for msg in ws:
                        now = time.time()
                        if not self.running:
                            break
                        if now - last_subscribe > RESUBSCRIBE_INTERVAL:
                            await self._subscribe(ws)
                            last_subscribe = now
                        if self._active() and now - self.last_matched_time > STALE_TIMEOUT:
                            log.warning("websocket silent for %ds, reconnecting", now - self.last_matched_time)
                            break
                        if now - connected > HARD_RECONNECT:
                            break
                        if msg.type != aiohttp.WSMsgType.TEXT or msg.data.lstrip()[:1] not in ("[", "{"):
                            continue  # control frames are plain text; only JSON carries events
                        try:
                            data = json.loads(msg.data)
                            for item in data if isinstance(data, list) else [data]:
                                if isinstance(item, dict):
                                    self.handle_message(item)
                        except (ValueError, KeyError, TypeError) as exc:
                            log.warning("websocket message skipped: %s", exc)
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                log.error("websocket error: %s", exc)
            self.ws = None
            if self.running:
                await asyncio.sleep(2)

    # ---------------------------------------------------------- per-second rows

    def write_second(self, tick: int, now_dt: datetime, now_ms: int) -> None:
        """Pending book changes, a heartbeat every ``heartbeat_seconds`` and one snapshot row per live market."""
        feed_alive = self.ws is not None and not self.ws.closed
        heartbeat = self.family.heartbeat_seconds
        for market in self._active():
            if market.event_start_time and now_dt < market.event_start_time:
                continue
            if market.end_time and now_dt > market.end_time + timedelta(seconds=self.family.tail_seconds):
                continue
            self.total_rows += flush_market_changes(market, self.conn_id)
            if feed_alive and heartbeat and tick % heartbeat == 0:
                # Books are reset on every reconnect, so an open socket and an initialised book is the current state.
                for side, book in market.books.items():
                    if book.initialized:
                        row = book_row(market, side, book, "heartbeat", self.conn_id)
                        row["age_ms"], row["ts"] = now_ms - row["ts"], now_ms
                        market.write_row(row)
                        self.total_rows += 1
            snapshot = {
                "v": SCHEMA_VERSION,
                "ts": now_ms,
                "ts_mono": time.monotonic_ns(),
                "conn": self.conn_id,
                "type": "snapshot",
                "outcome_up": round(market.up_mid, 4),
                "outcome_down": round(market.down_mid, 4),
                "up_age_ms": now_ms - market.up_last_update_ms if market.up_last_update_ms else None,
                "down_age_ms": now_ms - market.down_last_update_ms if market.down_last_update_ms else None,
            }
            if self.family.full_snapshots:
                snapshot["up"], snapshot["down"] = side_state(market.books["up"]), side_state(market.books["down"])
            market.write_row(snapshot)
            self.total_rows += 1

    async def snapshot_loop(self) -> None:
        tick = 0
        while self.running:
            self.write_second(tick, datetime.now(tz=ET), int(time.time() * 1000))
            tick += 1
            await asyncio.sleep(1)

    # ---------------------------------------------------------- closing markets

    def _audit(self, market: TrackedMarket) -> None:
        try:
            q = save_log_quality(market.filename, self.data_dir)
        except (OSError, ValueError) as exc:
            log.warning("quality audit unavailable for %s: %s", market.slug, exc)
            return
        report = log.info if q["healthy_capture"] else log.warning
        report(
            "QUALITY %s: %s | usable seconds %d/%d (%.1f%%) | longest gap %ds | fresh %.1f%%",
            "PASS" if q["passes_book_filter"] else "FAIL",
            market.slug,
            q["fresh_both_seconds"],
            q["duration_seconds"],
            100 * q["coverage"],
            q["longest_invalid_gap_seconds"],
            100 * q["fresh_recording_coverage"],
        )

    async def write_settlement(self, market: TrackedMarket, delay: float = SETTLEMENT_DELAY) -> None:
        """Fetch the official result after the close and store it beside the recording."""
        await asyncio.sleep(delay)
        try:
            async with (
                public_session() as session,
                session.get(
                    f"{GAMMA_URL}/events", params={"slug": market.slug}, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp,
            ):
                if resp.status != 200:
                    raise RuntimeError(f"Gamma HTTP {resp.status}")
                events = await resp.json()
            matches = events[0].get("markets", []) if events else []
            result = next(
                (m for m in matches if m.get("conditionId") == market.condition_id), matches[0] if matches else {}
            )
            winner = settlement_winner(result)
            payload = {
                "v": SCHEMA_VERSION,
                "slug": market.slug,
                "condition_id": market.condition_id,
                "question": market.question,
                "closed": result.get("closed"),
                "winner": winner,
                "outcomes": _json_list(result.get("outcomes", "[]")),
                "outcome_prices": _json_list(result.get("outcomePrices", "[]")),
                "uma_status": result.get("umaResolutionStatus"),
                "closed_time": result.get("closedTime"),
                "resolution_source": result.get("resolutionSource"),
                "fetched_at": datetime.now(tz=ET).isoformat(),
            }
            self.settlements_dir.mkdir(parents=True, exist_ok=True)
            _write_atomic(self.settlements_dir / f"{market.slug}.json", json.dumps(payload, indent=2, default=str))
            log.info("SETTLEMENT %s -> %s", market.slug, winner or "unresolved")
        except (aiohttp.ClientError, TimeoutError, RuntimeError, ValueError, IndexError) as exc:
            log.warning("settlement failed for %s: %s", market.slug, exc)

    async def close_finished(self) -> None:
        now = datetime.now(tz=ET)
        for market in self._active():
            if market.end_time and now > market.end_time + timedelta(seconds=CLOSE_GRACE):
                market.closed = True
                market.close_file()
                self.markets_completed += 1
                log.info("COMPLETED %s | %d rows", market.question, market.rows_written)
                await asyncio.to_thread(self._audit, market)  # never block the event stream
                task = asyncio.create_task(self.write_settlement(market))
                self._background.add(task)
                task.add_done_callback(self._background.discard)

    # ---------------------------------------------------------- main loops

    async def discovery_loop(self) -> None:
        while self.running:
            try:
                if await self.discover() and self.ws is not None and not self.ws.closed:
                    await self._subscribe(self.ws)
                await self.close_finished()
            except Exception:  # keep recording whatever goes wrong in one look-up
                log.exception("discovery error")
            await asyncio.sleep(DISCOVERY_INTERVAL)

    async def flush_loop(self) -> None:
        while self.running:
            await asyncio.sleep(FLUSH_INTERVAL)
            for market in self._active():
                if market.file_handle:
                    market.file_handle.flush()

    async def stats_loop(self) -> None:
        while self.running:
            await asyncio.sleep(STATS_INTERVAL)
            log.info(
                "STATS %d active | %d completed | %d rows | %d messages",
                len(self._active()),
                self.markets_completed,
                self.total_rows,
                self.wss_messages,
            )
            for m in self._active():
                log.info("  %s UP=%.2f DOWN=%.2f | %d rows", m.slug, m.up_mid, m.down_mid, m.rows_written)

    async def run(self) -> None:
        self.running = True
        log.info(
            "recording %s (schema v%d, depth %d) -> %s", self.family.name, SCHEMA_VERSION, DEPTH_LEVELS, self.output_dir
        )
        await self.discover()
        try:
            await asyncio.gather(
                self.websocket_loop(), self.discovery_loop(), self.snapshot_loop(), self.flush_loop(), self.stats_loop()
            )
        finally:
            self.running = False
            for task in list(self._background):
                task.cancel()
            for market in self.markets.values():
                market.close_file()
            log.info("stopped: %d markets, %d rows", self.markets_completed, self.total_rows)
