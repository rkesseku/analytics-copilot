"""
db/backend.py

Backend-neutral database interface.

Every SQL engine the copilot can talk to (DuckDB, Databricks SQL, ...) is
reached through the small `Backend` protocol defined here. The executor,
pipeline and eval harness depend only on this protocol, never on a concrete
driver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Result of executing a SQL query."""

    columns: list[str]
    rows: list[tuple[Any, ...]]
    row_count: int


@runtime_checkable
class Backend(Protocol):
    """Minimal interface a SQL engine adapter must provide."""

    @property
    def dialect(self) -> str:
        """sqlglot dialect name used to parse and render SQL for this engine."""
        ...

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def query(self, sql: str, params: list[Any] | None = None) -> QueryResult: ...

    def list_columns(self) -> dict[str, list[tuple[str, str]]]:
        """Introspect the schema: {table_name: [(column_name, data_type), ...]}."""
        ...
