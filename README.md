# pmlab

Order-book recording and look-ahead-free research tools for Polymarket 5- and 15-minute crypto Up/Down markets.

> Work in progress: the repository is being assembled step by step. The full README comes last.

## Quickstart (development)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## Recording

```bash
pmlab families              # the 16 market families (6 coins x 5m / 15m, gold and silver 15m / 1h)
pmlab record btc_5m         # record every BTC 5-minute market into ./data/btc_5m_data/ until Ctrl+C
```

## Layout so far

| Package | What it does |
|---|---|
| `pmlab.ingestion` | One recorder for every market family (`recorder`, families in `pmlab/config/markets.toml`), order-book sync from WebSocket events (`stream_capture`), the recorded-market object (`tracked_market`), Data API v2 client (`data_api`) |
| `pmlab.data` | Research-window filter (`dataset_policy`), as-of per-second book loader (`loaders`), recording-quality audit (`quality`) |
| `pmlab.features` | Price state (`price_state`) and causal per-second market features (`book_features`) |
| `pmlab.backtest` | Event-by-event replay with a queue-position model for resting orders (`resting_engine`), fees (`fees`) |
| `pmlab.strategies` | The strategy interface (`base`) and a demo strategy, the textbook two-sided pair maker (`pair_maker`) |
