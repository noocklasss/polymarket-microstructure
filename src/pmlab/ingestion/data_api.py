"""Polymarket Data API v1-style calls served by Data API v2 (v1 was retired on 2 Nov 2026).

`get(url, params, timeout)` takes a v1 url and v1 params and returns a requests-like response whose .json() is what v1
returned (a bare list of rows with camelCase fields: proxyWallet, conditionId, usdcSize, outcomeIndex, transactionHash,
asset, ...), so a script only swaps `requests.get(TRADES_URL, ...)` for `data_api_get(TRADES_URL, ...)`.
- routes: /trades, /activity, /positions -> same in /v2; /closed-positions -> /v2/positions?status=CLOSED;
  others -> /v2/<path>
- params: market -> condition, takerOnly -> taker_only, sortBy / sortDirection -> sort_by / sort_direction,
  sizeThreshold -> filter_amount; offset (rejected by v2) is emulated: v2 is walked with its cursor and rows
  [offset, offset + limit) are served from a small per-query buffer, so a v1 offset loop costs one v2 page per
  1,000 rows and has no 3,000 / 10,000-row cap any more.
- errors keep their HTTP status (v2 answers a bad parameter with 400), so the scripts' own error handling still works.
v2 rows were checked against v1 caches on 3 Nov 2026: same rows, same numbers, only the field names differ."""

import re
import threading
import time
from collections import OrderedDict

import requests

V2 = "https://data-api.polymarket.com/v2"
ROUTES = {"/closed-positions": ("/positions", {"status": "CLOSED"})}
PARAMS = {
    "market": "condition",
    "takerOnly": "taker_only",
    "sortBy": "sort_by",
    "sortDirection": "sort_direction",
    "sizeThreshold": "filter_amount",
}
FIELDS = {"token_id": "asset", "last_event_at": "timestamp"}
CLOSED = {"percentPnl": "percentRealizedPnl", "totalBought": "totalSize"}  # v1 closed-positions names
PAGE = 1000  # v2 maximum page size
S = requests.Session()
_buf, _lock = OrderedDict(), threading.Lock()


def v1_row(x):
    """v2 row -> v1 field names (snake_case -> camelCase; token_id -> asset)."""
    if not isinstance(x, dict):
        return x
    return {FIELDS.get(k) or re.sub(r"_([a-z])", lambda m: m.group(1).upper(), k): v for k, v in x.items()}


class Response:
    def __init__(self, status_code, body, text=""):
        self.status_code, self._body, self.text = status_code, body, text
        self.ok = status_code < 400

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code}: {self.text[:200]}", response=self)


def _call(path, params, timeout):
    for a in range(6):
        r = S.get(V2 + path, params=params, timeout=timeout)
        if r.status_code != 429:
            return r
        time.sleep(float(r.headers.get("Retry-After") or 2 + 3 * a))
    return r


def get(url, params=None, timeout=30, **_):
    path = re.sub(r"^https?://data-api\.polymarket\.com", "", url.split("?")[0]).rstrip("/")
    path = re.sub(r"^/v[12](?=/)", "", path)
    path, extra = ROUTES.get(path, (path, {}))
    p = {}
    for k, v in params.items() if isinstance(params, dict) else params or []:
        k = PARAMS.get(k, k)
        p[k] = f"{p[k]},{v}" if k in p else v
    p.update(extra)
    if isinstance(p.get("taker_only"), bool):
        p["taker_only"] = str(p["taker_only"]).lower()
    offset, limit = int(p.pop("offset", 0) or 0), int(p.pop("limit", 100) or 100)
    if path not in ("/trades", "/activity", "/positions"):  # not paginated (/value, /oi, ...)
        r = _call(path, p, timeout)
        if not r.ok:
            return Response(r.status_code, None, r.text)
        d = r.json().get("data")
        return Response(200, [v1_row(x) for x in d] if isinstance(d, list) else v1_row(d), r.text)
    key = (path, tuple(sorted((k, str(v)) for k, v in p.items())))
    with _lock:
        b = _buf.pop(key, None) or {"rows": [], "cursor": None, "done": False, "lock": threading.Lock()}
        _buf[key] = b  # most recent last
        while len(_buf) > 32:
            _buf.popitem(last=False)
    with b["lock"]:
        while len(b["rows"]) < offset + limit and not b["done"]:
            q = dict(p, cursor=b["cursor"]) if b["cursor"] else dict(p, limit=PAGE)
            r = _call(path, q, timeout)
            if not r.ok:
                return Response(r.status_code, None, r.text)
            d = r.json()
            rows = [v1_row(x) for x in d.get("data") or []]
            if p.get("status") == "CLOSED":
                for x in rows:
                    x.update({k: x[v] for k, v in CLOSED.items() if v in x})
            b["rows"].extend(rows)
            b["cursor"] = (d.get("pagination") or {}).get("next_cursor")
            b["done"] = not b["cursor"]
        return Response(200, b["rows"][offset : offset + limit])
