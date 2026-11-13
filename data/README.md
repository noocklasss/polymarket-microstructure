# Data

Recordings are not kept in git: the full set is about 49 GB (about 3 MB per 5-minute market, 16 market families).
Record your own with `pmlab record <family>`; files land in `data/<family>_data/` as one NDJSON file per market.

## The bundled sample

`data/sample/` holds 4 real BTC 5-minute markets from November 7, 2026 (gzip-compressed, about 1 MB in total) and their
final results, so the tests and the notebook run without recording anything:

| Market start (ET) | File | Result | Pair maker in the study |
|---|---|---|---|
| 00:05 | `btc-updown-5m-1794027900.ndjson.gz` | DOWN | -$2.10 on $92 spent (a typical loss) |
| 02:55 | `btc-updown-5m-1794038100.ndjson.gz` | UP | -$28.84 on $315 (the worst market of the day) |
| 03:45 | `btc-updown-5m-1794041100.ndjson.gz` | UP | -$4.09 on $227 |
| 12:20 | `btc-updown-5m-1794072000.ndjson.gz` | UP | +$1.40 on $119 (one of the few wins) |

`tests/test_sample_data.py` checks that replaying these files gives exactly the rows of
`reports/pair_maker_btc_5m_exam_markets.csv`.

## File format (schema v2)

One JSON object a line, timestamps in milliseconds (`ts` is when the message was received):

| `type` | What it holds |
|---|---|
| `meta` | First line: slug, question, `condition_id`, token ids, start and end time, tick size, fee rate |
| `book` | Top of book for one side after any change: `side` (`up` / `down`), `best_bid`, `best_ask`, their sizes, spread |
| `snapshot` | Once a second: both sides, 5 levels deep |
| `trade` | A print: `side`, `price`, `size`, `taker_side` |
| `resync` | The book was rebuilt from a REST snapshot after a gap |

`outcomes.json` maps `condition_id` to the final payouts `[up, down]`: `[1, 0]` means UP won. Only exactly `1` / `0`
counts as final.

Only markets starting on or after August 1, 2026 (New York time) are used; `pmlab.data.dataset_policy` enforces this.
