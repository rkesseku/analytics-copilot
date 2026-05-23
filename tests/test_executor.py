"""Tests for sql.executor (validated SQL execution)."""

from __future__ import annotations

from datetime import date

import pytest

from analytics_copilot.db.database import Database
from analytics_copilot.db.schema import create_and_seed
from analytics_copilot.sql.executor import SQLExecutor
from analytics_copilot.sql.validator import SQLValidationError


@pytest.fixture
def executor() -> SQLExecutor:
    db = Database()
    db.connect()
    create_and_seed(db.connection, n_orders=30, seed=42, today=date(2026, 5, 22))
    return SQLExecutor(database=db)


class TestExecutor:
    def test_valid_query_runs(self, executor: SQLExecutor) -> None:
        result = executor.execute("SELECT name FROM customers ORDER BY customer_id LIMIT 3")
        assert result.result.row_count == 3
        assert result.tables_referenced == ("customers",)

    def test_limit_added_when_missing(self, executor: SQLExecutor) -> None:
        result = executor.execute("SELECT name FROM customers")
        assert result.row_limit_added is True
        # With a 1000-row default limit and only 10 customers, we get 10 rows.
        assert result.result.row_count == 10

    def test_join_query(self, executor: SQLExecutor) -> None:
        result = executor.execute("""
            SELECT c.name, COUNT(o.order_id) AS order_count
            FROM customers c
            LEFT JOIN orders o ON c.customer_id = o.customer_id
            GROUP BY c.name
            ORDER BY order_count DESC
            LIMIT 5
        """)
        assert result.result.row_count == 5
        assert "customers" in result.tables_referenced
        assert "orders" in result.tables_referenced

    def test_mutation_rejected_before_execution(self, executor: SQLExecutor) -> None:
        with pytest.raises(SQLValidationError):
            executor.execute("DROP TABLE customers")
        # Verify the table is still there
        check = executor.execute("SELECT COUNT(*) FROM customers")
        assert check.result.rows[0][0] == 10

    def test_disallowed_table_rejected(self, executor: SQLExecutor) -> None:
        with pytest.raises(SQLValidationError):
            executor.execute("SELECT * FROM secret_audit_log")

    def test_original_sql_preserved(self, executor: SQLExecutor) -> None:
        original = "SELECT name FROM customers"
        result = executor.execute(original)
        assert result.original_sql == original
        # executed_sql differs because LIMIT was injected
        assert result.executed_sql != original
