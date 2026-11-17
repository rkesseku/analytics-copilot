"""
db/database.py

Thin wrapper around DuckDB connection management.

We expose a single `Database` class that owns a connection, can be used as a
context manager, and offers a `query()` method returning structured results.
This is the only file in the codebase that imports `duckdb` directly (lazily,
in `connect()`). `Database` implements the `Backend` protocol from
`db.backend`; other engines live in sibling modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from analytics_copilot.db.backend import QueryResult

if TYPE_CHECKING:  # duckdb is imported lazily in connect()
    import duckdb

__all__ = ["Database", "QueryResult"]


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

    @property
    def dialect(self) -> str:
        return "duckdb"

    def __enter__(self) -> Database:  # noqa: PYI034 - py3.10 has no typing.Self
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
            import duckdb

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

    def list_columns(self) -> dict[str, list[tuple[str, str]]]:
        """Introspect tables in the main schema via information_schema."""
        result = self.query(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position"
        )
        out: dict[str, list[tuple[str, str]]] = {}
        for table, column, dtype in result.rows:
            out.setdefault(str(table), []).append((str(column), str(dtype)))
        return out
