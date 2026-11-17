"""Dialect-aware validation and pipeline chart wiring (need sqlglot / duckdb)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("sqlglot")

from analytics_copilot.db.backend import QueryResult
from analytics_copilot.sql.executor import SQLExecutor
from analytics_copilot.sql.validator import SQLValidationError, SQLValidator


class FakeDatabricksLike:
    dialect = "databricks"

    def __init__(self) -> None:
        self.last_sql = ""

    def query(self, sql: str, params: list[object] | None = None) -> QueryResult:
        self.last_sql = sql
        return QueryResult(columns=["n"], rows=[(1,)], row_count=1)


class TestDialects:
    def test_databricks_dialect_parses_qualified_tables(self) -> None:
        v = SQLValidator(dialect="databricks")
        result = v.validate(
            "SELECT category, SUM(unit_price) AS total FROM main.sales.products GROUP BY category"
        )
        assert result.tables_referenced == ("products",)
        assert result.row_limit_added is True

    def test_databricks_rejects_non_select(self) -> None:
        with pytest.raises(SQLValidationError):
            SQLValidator(dialect="databricks").validate("DROP TABLE products")

    def test_executor_validator_follows_backend_dialect(self) -> None:
        backend = FakeDatabricksLike()
        out = SQLExecutor(database=backend).execute("SELECT COUNT(*) AS n FROM main.sales.customers")
        assert out.tables_referenced == ("customers",)
        assert backend.last_sql.upper().startswith("SELECT")


class _TwoStageFake:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._i = 0

    def complete(self, *, system: str, user: str, temperature: float = 0.2, max_tokens: int = 512):
        from analytics_copilot.llm.client import CompletionResult

        text = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return CompletionResult(text, 1, 1, 2, 0.1, "fake")


class TestPipelineChart:
    def test_chart_rendered_when_requested(self, tmp_path: Path) -> None:
        pytest.importorskip("duckdb")
        from analytics_copilot.db.database import Database
        from analytics_copilot.db.schema import create_and_seed
        from analytics_copilot.pipeline import AnalyticsCopilot

        db = Database()
        db.connect()
        create_and_seed(db.connection, n_orders=30, seed=42, today=date(2026, 5, 22))
        sql = json.dumps(
            {
                "sql": "SELECT country, COUNT(*) AS n FROM customers GROUP BY country",
                "rationale": "r",
                "confidence": 0.9,
            }
        )
        copilot = AnalyticsCopilot(database=db, llm_client=_TwoStageFake([sql, "Summary."]))
        out = tmp_path / "chart.png"
        answer = copilot.ask("Customers per country?", chart_path=out)
        assert answer.chart_path == out
        assert out.read_bytes().startswith(b"\x89PNG")

    def test_no_chart_by_default(self) -> None:
        pytest.importorskip("duckdb")
        from analytics_copilot.db.database import Database
        from analytics_copilot.db.schema import create_and_seed
        from analytics_copilot.pipeline import AnalyticsCopilot

        db = Database()
        db.connect()
        create_and_seed(db.connection, n_orders=30, seed=42, today=date(2026, 5, 22))
        sql = json.dumps({"sql": "SELECT COUNT(*) FROM customers", "rationale": "r", "confidence": 1})
        copilot = AnalyticsCopilot(database=db, llm_client=_TwoStageFake([sql, "Ten."]))
        assert copilot.ask("How many customers?").chart_path is None
