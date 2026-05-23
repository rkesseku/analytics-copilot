"""
db/database.py

Thin wrapper around DuckDB connection management.

We expose a single `Database` class that owns a connection, can be used as a
context manager, and offers a `query()` method returning structured results.
This is the only file in the codebase that imports `duckdb` directly -- every
other module talks to `Database`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Result of executing a SQL query."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    row_count: int


class Database:
    """Manages a DuckDB connection (file-backed or in-memory)."""

    def __init__(self, path: str | Path | None = None) -> None:
        """
        Parameters
        ----------
        path : str | Path | None
            File path to the DuckDB database. If None, uses an in-memory DB
            (perfect for tests).
        """
        self._path = str(path) if path is not None else ":memory:"
        self._con: duckdb.DuckDBPyConnection | None = None

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._con

    def connect(self) -> None:
        if self._con is None:
            self._con = duckdb.connect(self._path)

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def query(self, sql: str, params: list[Any] | None = None) -> QueryResult:
        """Execute a SELECT query and return the results.

        Parameters
        ----------
        sql : str
            The SQL to execute. Should be a single, validated SELECT statement
            in production -- but this method does NOT validate; that is the
            safety layer's job.
        params : list, optional
            Positional parameters for the query.
        """
        cursor = self.connection.execute(sql, params or [])
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        return QueryResult(columns=columns, rows=rows, row_count=len(rows))