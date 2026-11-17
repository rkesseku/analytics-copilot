"""Tests for the Databricks backend using a fake connection (no network)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

import pytest

from analytics_copilot.db.backend import Backend
from analytics_copilot.db.databricks import DatabricksBackend
from analytics_copilot.db.factory import create_backend, resolve_backend_name

Responder = Callable[[str], tuple[list[str], list[tuple[Any, ...]]]]


class FakeCursor:
    def __init__(self, parent: FakeConnection) -> None:
        self._parent = parent
        self.description: list[tuple[str, ...]] | None = None
        self._rows: list[tuple[Any, ...]] = []
        self.closed = False

    def execute(self, sql: str, params: Any = None) -> None:
        self._parent.executed.append((sql, params))
        columns, rows = self._parent.responder(sql)
        self.description = [(c,) for c in columns]
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.executed: list[tuple[str, Any]] = []
        self.cursors: list[FakeCursor] = []
        self.closed = False

    def cursor(self) -> FakeCursor:
        cur = FakeCursor(self)
        self.cursors.append(cur)
        return cur

    def close(self) -> None:
        self.closed = True


def make_backend(responder: Responder, **kwargs: Any) -> tuple[DatabricksBackend, FakeConnection]:
    con = FakeConnection(responder)
    backend = DatabricksBackend(
        server_hostname="example.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/abc",
        access_token="dummy-token",
        connection_factory=lambda: con,
        **kwargs,
    )
    return backend, con


class TestDatabricksBackend:
    def test_implements_backend_protocol_and_dialect(self) -> None:
        backend, _ = make_backend(lambda sql: ([], []))
        assert isinstance(backend, Backend)
        assert backend.dialect == "databricks"

    def test_query_returns_columns_and_rows(self) -> None:
        backend, con = make_backend(lambda sql: (["n", "name"], [(1, "a"), (2, "b")]))
        with backend:
            result = backend.query("SELECT n, name FROM customers")
        assert result.columns == ["n", "name"]
        assert result.rows == [(1, "a"), (2, "b")]
        assert result.row_count == 2
        assert con.cursors[0].closed
        assert con.closed

    def test_query_before_connect_raises(self) -> None:
        backend, _ = make_backend(lambda sql: ([], []))
        with pytest.raises(RuntimeError):
            backend.query("SELECT 1")

    def test_params_forwarded(self) -> None:
        backend, con = make_backend(lambda sql: (["x"], [(1,)]))
        backend.connect()
        backend.query("SELECT x FROM t WHERE y = ?", [5])
        assert con.executed[-1] == ("SELECT x FROM t WHERE y = ?", [5])

    def test_list_columns_uses_catalog_information_schema(self) -> None:
        def responder(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
            return (
                ["table_name", "column_name", "data_type"],
                [("orders", "order_id", "INT"), ("orders", "status", "STRING"), ("c", "id", "INT")],
            )

        backend, con = make_backend(responder, catalog="main", schema="sales")
        backend.connect()
        cols = backend.list_columns()
        sql = con.executed[-1][0]
        assert "main.information_schema.columns" in sql
        assert "table_schema = 'sales'" in sql
        assert cols == {
            "orders": [("order_id", "INT"), ("status", "STRING")],
            "c": [("id", "INT")],
        }

    def test_list_columns_without_catalog_uses_current_schema(self) -> None:
        backend, con = make_backend(lambda sql: (["a", "b", "c"], []))
        backend.connect()
        backend.list_columns()
        sql = con.executed[-1][0]
        assert "FROM information_schema.columns" in sql
        assert "current_schema()" in sql

    @pytest.mark.parametrize("bad", ["x; DROP TABLE t", "a.b", "has space", "1abc"])
    def test_rejects_unsafe_identifiers(self, bad: str) -> None:
        with pytest.raises(ValueError):
            DatabricksBackend(server_hostname="h", http_path="p", access_token="t", catalog=bad)
        with pytest.raises(ValueError):
            DatabricksBackend(server_hostname="h", http_path="p", access_token="t", schema=bad)


class TestFromEnvAndFactory:
    ENV: ClassVar[dict[str, str]] = {
        "DATABRICKS_SERVER_HOSTNAME": "example.cloud.databricks.com",
        "DATABRICKS_HTTP_PATH": "/sql/1.0/warehouses/abc",
        "DATABRICKS_TOKEN": "dummy-token",
        "DATABRICKS_CATALOG": "main",
        "DATABRICKS_SCHEMA": "sales",
    }

    def test_from_env(self) -> None:
        backend = DatabricksBackend.from_env(self.ENV)
        assert backend.dialect == "databricks"

    def test_missing_env_lists_names(self) -> None:
        with pytest.raises(RuntimeError, match="DATABRICKS_TOKEN"):
            DatabricksBackend.from_env({"DATABRICKS_SERVER_HOSTNAME": "h"})

    def test_resolve_backend_name_precedence(self) -> None:
        assert resolve_backend_name(None, {}) == "duckdb"
        assert resolve_backend_name(None, {"ANALYTICS_BACKEND": "Databricks"}) == "databricks"
        assert resolve_backend_name("duckdb", {"ANALYTICS_BACKEND": "databricks"}) == "duckdb"

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(ValueError):
            resolve_backend_name("oracle", {})

    def test_create_databricks_backend(self) -> None:
        backend = create_backend("databricks", self.ENV)
        assert isinstance(backend, DatabricksBackend)
