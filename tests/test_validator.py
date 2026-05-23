"""Tests for sql.validator (SQL safety validation)."""

from __future__ import annotations

import pytest

from analytics_copilot.sql.validator import SQLValidationError, SQLValidator


class TestStatementType:
    def test_select_allowed(self) -> None:
        v = SQLValidator()
        result = v.validate("SELECT * FROM customers")
        assert "customers" in result.tables_referenced

    def test_select_with_cte_allowed(self) -> None:
        v = SQLValidator()
        sql = """
            WITH recent_orders AS (
                SELECT * FROM orders WHERE order_date > DATE '2025-01-01'
            )
            SELECT COUNT(*) FROM recent_orders
        """
        result = v.validate(sql)
        assert "orders" in result.tables_referenced

    @pytest.mark.parametrize(
        "bad_sql",
        [
            "DROP TABLE customers",
            "DELETE FROM customers WHERE customer_id = 1",
            "UPDATE customers SET name = 'x' WHERE customer_id = 1",
            "INSERT INTO customers VALUES (99, 'x', 'x@x.com', 'X', '2024-01-01')",
            "CREATE TABLE foo (x INTEGER)",
            "ALTER TABLE customers ADD COLUMN x INTEGER",
            "TRUNCATE TABLE customers",
        ],
    )
    def test_mutation_statements_rejected(self, bad_sql: str) -> None:
        v = SQLValidator()
        with pytest.raises(SQLValidationError):
            v.validate(bad_sql)


class TestTableAllowlist:
    def test_unknown_table_rejected(self) -> None:
        v = SQLValidator()
        with pytest.raises(SQLValidationError, match="disallowed table"):
            v.validate("SELECT * FROM admin_users")

    def test_join_with_unknown_table_rejected(self) -> None:
        v = SQLValidator()
        sql = """
            SELECT c.name, s.secret
            FROM customers c
            JOIN secrets s ON c.customer_id = s.customer_id
        """
        with pytest.raises(SQLValidationError):
            v.validate(sql)

    def test_case_insensitive_match(self) -> None:
        v = SQLValidator()
        result = v.validate("SELECT * FROM CUSTOMERS")
        assert "customers" in result.tables_referenced


class TestRowLimit:
    def test_limit_injected_when_missing(self) -> None:
        v = SQLValidator(default_row_limit=100)
        result = v.validate("SELECT * FROM customers")
        assert result.row_limit_added is True
        assert "LIMIT 100" in result.sql.upper()

    def test_existing_limit_preserved(self) -> None:
        v = SQLValidator(default_row_limit=100)
        result = v.validate("SELECT * FROM customers LIMIT 5")
        assert result.row_limit_added is False
        assert "LIMIT 5" in result.sql.upper()

    def test_limit_disabled(self) -> None:
        v = SQLValidator(default_row_limit=None)
        result = v.validate("SELECT * FROM customers")
        assert result.row_limit_added is False
        assert "LIMIT" not in result.sql.upper()


class TestEdgeCases:
    def test_empty_sql_rejected(self) -> None:
        v = SQLValidator()
        with pytest.raises(SQLValidationError):
            v.validate("")

    def test_whitespace_only_sql_rejected(self) -> None:
        v = SQLValidator()
        with pytest.raises(SQLValidationError):
            v.validate("   \n\t  ")

    def test_gibberish_rejected(self) -> None:
        v = SQLValidator()
        with pytest.raises(SQLValidationError):
            v.validate("this is not sql at all")

    def test_select_without_tables_rejected(self) -> None:
        """A SELECT that references no tables (e.g. SELECT 1) is rejected."""
        v = SQLValidator()
        with pytest.raises(SQLValidationError):
            v.validate("SELECT 1")