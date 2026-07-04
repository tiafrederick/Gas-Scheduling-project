"""DuckDB canonical store (DDL-001/011).

The store is a local, single-file DuckDB database created from
schema/canonical.sql. It is derived (rebuildable from raw + samples), so it is
gitignored; git carries the schema and loaders instead.
"""
from __future__ import annotations

import os

import duckdb

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCHEMA_SQL = os.path.join(REPO, "schema", "canonical.sql")
DEFAULT_DB = os.path.join(REPO, "data", "warehouse", "nge.duckdb")


def create_db(db_path: str = DEFAULT_DB, fresh: bool = True) -> duckdb.DuckDBPyConnection:
    """Create (or open) the canonical store and apply the schema."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if fresh and os.path.exists(db_path):
        os.remove(db_path)
    con = duckdb.connect(db_path)
    with open(SCHEMA_SQL, encoding="utf-8") as fh:
        con.execute(fh.read())
    return con


def table_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()
    return {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for (t,) in rows}
