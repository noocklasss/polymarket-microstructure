"""Data pipeline: recordings (NDJSON) -> Parquet partitions -> DuckDB models -> data checks.

pmlab pipeline --data-dir data --warehouse warehouse --outcomes outcomes.json
pmlab sql --warehouse warehouse "SELECT * FROM daily_summary"
"""
