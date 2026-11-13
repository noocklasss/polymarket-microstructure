import pytest

from pmlab.ingestion import data_api as D


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text, self.ok, self.headers = status, body, str(body), status < 400, {}

    def json(self):
        return self._body


class FakeSession:
    """A v2 feed of ``n`` rows served in cursor pages of 1,000; records every request."""

    def __init__(self, n=2500):
        self.n, self.calls = n, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params)))
        if "offset" in params or "market" in params:  # v1-only parameters: v2 rejects them
            return FakeResp(400, {"error": "rejected"})
        i = int(params.get("cursor") or 0)
        rows = [
            {"proxy_wallet": "0xw", "condition_id": "0xc", "usdc_size": k, "token_id": "t", "transaction_hash": f"h{k}"}
            for k in range(i, min(i + 1000, self.n))
        ]
        nxt = str(i + 1000) if i + 1000 < self.n else None
        return FakeResp(200, {"data": rows, "pagination": {"next_cursor": nxt, "has_more": nxt is not None}})


@pytest.fixture
def session(monkeypatch):
    D._buf.clear()
    fake = FakeSession()
    monkeypatch.setattr(D, "S", fake)
    return fake


def test_v1_offset_loop_is_served_from_v2_cursor_pages(session):
    rows, offset = [], 0
    while True:
        page = D.get(
            "https://data-api.polymarket.com/activity",
            {"user": "0xw", "limit": 500, "offset": offset, "sortDirection": "ASC"},
        ).json()
        rows += page
        offset += 500
        if len(page) < 500:
            break
    assert [r["usdcSize"] for r in rows] == list(range(2500))
    assert len(session.calls) == 3  # one v2 page per 1,000 rows
    url, params = session.calls[0]
    assert url == D.V2 + "/activity"
    assert params["sort_direction"] == "ASC"
    assert "offset" not in params
    assert set(rows[0]) == {"proxyWallet", "conditionId", "usdcSize", "asset", "transactionHash"}


def test_params_and_routes_are_translated(session):
    D.get("https://data-api.polymarket.com/trades", {"market": "0xc", "takerOnly": "false", "limit": 10, "offset": 0})
    D.get("https://data-api.polymarket.com/v1/closed-positions", {"user": "0xw", "limit": 10})
    (u1, p1), (u2, p2) = session.calls
    assert (u1, p1["condition"], p1["taker_only"]) == (D.V2 + "/trades", "0xc", "false")
    assert (u2, p2["status"]) == (D.V2 + "/positions", "CLOSED")


def test_error_status_is_passed_through(session, monkeypatch):
    monkeypatch.setattr(session, "get", lambda url, params=None, timeout=None: FakeResp(400, {"error": "bad"}))
    response = D.get("https://data-api.polymarket.com/trades", {"user": "x"})
    assert response.status_code == 400
    assert not response.ok
