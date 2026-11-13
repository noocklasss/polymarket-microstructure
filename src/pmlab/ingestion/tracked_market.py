"""One Up/Down market being recorded: its two token ids, both order books and the NDJSON file it writes to."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from pmlab.features.price_state import held_book_price
from pmlab.ingestion.stream_capture import MarketBook


@dataclass
class TrackedMarket:
    slug: str
    question: str
    condition_id: str
    yes_token_id: str  # the UP outcome token
    no_token_id: str  # the DOWN outcome token
    end_time: datetime | None
    event_start_time: datetime | None
    filename: str
    file_handle: object = None
    rows_written: int = 0
    closed: bool = False
    up_bid: float = 0.0
    up_ask: float = 0.99
    down_bid: float = 0.0
    down_ask: float = 0.99
    up_last_price: float = 0.50
    down_last_price: float = 0.50
    up_last_update_ms: int = 0
    down_last_update_ms: int = 0
    books: dict = field(default_factory=lambda: {"up": MarketBook(), "down": MarketBook()})

    @property
    def up_mid(self) -> float:
        return self.up_last_price

    @property
    def down_mid(self) -> float:
        return self.down_last_price

    def update_book(self, side: str, bid: float, ask: float, now_ms: int) -> None:
        if side == "up":
            self.up_bid, self.up_ask = bid, ask
            self.up_last_price = held_book_price(bid, ask, self.up_last_price)
            self.up_last_update_ms = now_ms
        else:
            self.down_bid, self.down_ask = bid, ask
            self.down_last_price = held_book_price(bid, ask, self.down_last_price)
            self.down_last_update_ms = now_ms

    def open_file(self) -> None:
        if self.file_handle is None:
            self.file_handle = open(self.filename, "a", encoding="utf-8")  # noqa: SIM115 (kept open while recording)

    def write_row(self, row: dict) -> None:
        if self.file_handle is None:
            self.open_file()
        self.file_handle.write(json.dumps(row) + "\n")
        self.rows_written += 1

    def close_file(self) -> None:
        if self.file_handle:
            self.file_handle.flush()
            self.file_handle.close()
            self.file_handle = None
