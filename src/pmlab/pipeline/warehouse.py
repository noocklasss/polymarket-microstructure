"""The DuckDB side: views over the Parquet partitions, the SQL models, and the data checks.

``<warehouse>/warehouse.duckdb`` holds the models as tables; the raw tables stay in Parquet and are read through views
(``raw_markets``, ``raw_trades``, ``raw_book``, ``raw_snapshots``, ``raw_resyncs``, ``raw_outcomes``), each with the
``family`` and ``day`` of its partition. Models are the files in ``sql/models`` run in name order; checks are the files
in ``sql/checks``, each a query that returns the rows breaking a rule. A check marked ``-- severity: error`` fails the
run; ``warn`` only reports.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from pmlab.data.outcomes import load_outcomes
from pmlab.pipeline.extract import TABLES

DATABASE = "warehouse.duckdb"
SQL = resources.files("pmlab.pipeline") / "sql"
OUTCOMES_SCHEMA = pa.schema([("condition_id", pa.string()), ("payout_up", pa.float64()), ("payout_down", pa.float64())])


@dataclass
class CheckResult:
    name: str
    severity: str
    description: str
    failures: int
    examples: list[tuple]


def write_outcomes(warehouse: Path, outcomes_file: Path) -> int:
    """Store final results (``{condition_id: [payout_up, payout_down]}``) as ``outcomes/outcomes.parquet``."""
    rows = [
        {"condition_id": cid, "payout_up": pay[0], "payout_down": pay[1]}
        for cid, pay in load_outcomes(outcomes_file).items()
        if len(pay) == 2
    ]
    folder = Path(warehouse) / "outcomes"
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "outcomes.parquet.tmp"
    pq.write_table(pa.Table.from_pylist(rows, schema=OUTCOMES_SCHEMA), tmp)
    os.replace(tmp, folder / "outcomes.parquet")
    return len(rows)


def connect(warehouse: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the warehouse database and (re)create the views over the Parquet files."""
    warehouse = Path(warehouse)
    con = duckdb.connect(str(warehouse / DATABASE), read_only=read_only)
    if read_only:
        return con
    for table in TABLES:
        files = warehouse / table / "family=*" / "day=*" / "part.parquet"
        if not any(warehouse.glob(f"{table}/family=*/day=*/part.parquet")):
            raise FileNotFoundError(f"no {table} partitions in {warehouse}: run the build first")
        con.execute(
            f"CREATE OR REPLACE VIEW raw_{table} AS SELECT * FROM read_parquet('{_sql_path(files)}', "
            "hive_partitioning = true, hive_types = {'family': VARCHAR, 'day': DATE})"
        ).fetchall()
    outcomes = warehouse / "outcomes" / "outcomes.parquet"
    if outcomes.exists():
        con.execute(
            f"CREATE OR REPLACE VIEW raw_outcomes AS SELECT * FROM read_parquet('{_sql_path(outcomes)}')"
        ).fetchall()
    else:
        con.execute(
            "CREATE OR REPLACE VIEW raw_outcomes AS "
            "SELECT NULL::VARCHAR AS condition_id, NULL::DOUBLE AS payout_up, NULL::DOUBLE AS payout_down WHERE false"
        ).fetchall()
    return con


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _sql_files(kind: str) -> list[tuple[str, str]]:
    folder = SQL / kind
    return sorted((f.name, f.read_text(encoding="utf-8")) for f in folder.iterdir() if f.name.endswith(".sql"))


def run_models(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Run every model in order; returns the row count of the table each one creates."""
    counts = {}
    for name, sql in _sql_files("models"):
        con.execute(sql).fetchall()
        table = name.removesuffix(".sql").split("_", 1)[1]
        counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchall()[0][0]
    return counts


def _header(sql: str, key: str, default: str = "") -> str:
    for line in sql.splitlines():
        if line.startswith(f"-- {key}:"):
            return line.split(":", 1)[1].strip()
    return default


def run_checks(con: duckdb.DuckDBPyConnection, examples: int = 3) -> list[CheckResult]:
    results = []
    for name, sql in _sql_files("checks"):
        description = " ".join(
            line.removeprefix("--").strip()
            for line in sql.splitlines()
            if line.startswith("--") and not line.startswith("-- severity:")
        )
        query = sql.strip().rstrip(";")
        failures = con.execute(f"SELECT count(*) FROM ({query})").fetchall()[0][0]
        sample = (
            con.execute(f"SELECT COLUMNS(*)::VARCHAR FROM ({query}) LIMIT {examples}").fetchall() if failures else []
        )
        results.append(
            CheckResult(name.removesuffix(".sql"), _header(sql, "severity", "error"), description, failures, sample)
        )
    return results


def summary_json(warehouse: Path) -> str:
    """The daily summary as JSON (used by the CLI and handy for a quick look)."""
    with connect(warehouse, read_only=True) as con:
        rows = con.execute("SELECT * FROM daily_summary").fetchall()
        names = [d[0] for d in con.description]
    return json.dumps([dict(zip(names, r, strict=True)) for r in rows], default=str, indent=1)
