"""Tests for db.schema (seed data generation)."""

from __future__ import annotations

from datetime import date

import pytest

from analytics_copilot.db.database import Database
from analytics_copilot.db.schema import create_and_seed


@pytest.fixture
def seeded_db() -> Database:
    db = Database()
    db.connect()
    create_and_seed(db.connection, n_orders=50, seed=42, today=date(2026, 5, 22))
    return db


class TestSchema:
    def test_customers_table_populated(self, seeded_db: Database) -> None:
        result = seeded_db.query("SELECT COUNT(*) FROM customers")
        assert result.rows[0][0] == 10

    def test_products_table_populated(self, seeded_db: Database) -> None:
        result = seeded_db.query("SELECT COUNT(*) FROM products")
        assert result.rows[0][0] == 15

    def test_orders_table_populated(self, seeded_db: Database) -> None:
        result = seeded_db.query("SELECT COUNT(*) FROM orders")
        assert result.rows[0][0] == 50

    def test_order_items_table_populated(self, seeded_db: Database) -> None:
        result = seeded_db.query("SELECT COUNT(*) FROM order_items")
        assert result.rows[0][0] > 0  # >= n_orders, since each order has >= 1 item

    def test_seed_is_deterministic(self) -> None:
        """Two databases seeded with the same args produce identical data."""
        db1 = Database()
        db2 = Database()
        db1.connect()
        db2.connect()
        create_and_seed(db1.connection, n_orders=30, seed=7, today=date(2026, 1, 1))
        create_and_seed(db2.connection, n_orders=30, seed=7, today=date(2026, 1, 1))

        rows1 = db1.query("SELECT * FROM orders ORDER BY order_id").rows
        rows2 = db2.query("SELECT * FROM orders ORDER BY order_id").rows
        assert rows1 == rows2

    def test_seed_changes_with_different_seed(self) -> None:
        db1 = Database()
        db2 = Database()
        db1.connect()
        db2.connect()
        create_and_seed(db1.connection, n_orders=30, seed=1, today=date(2026, 1, 1))
        create_and_seed(db2.connection, n_orders=30, seed=2, today=date(2026, 1, 1))

        rows1 = db1.query("SELECT * FROM orders ORDER BY order_id").rows
        rows2 = db2.query("SELECT * FROM orders ORDER BY order_id").rows
        assert rows1 != rows2

    def test_foreign_keys_intact(self, seeded_db: Database) -> None:
        """Every order_items row references a real order and product."""
        result = seeded_db.query("""
            SELECT COUNT(*)
            FROM order_items oi
            LEFT JOIN orders o ON oi.order_id = o.order_id
            LEFT JOIN products p ON oi.product_id = p.product_id
            WHERE o.order_id IS NULL OR p.product_id IS NULL
        """)
        assert result.rows[0][0] == 0

    def test_status_values_are_valid(self, seeded_db: Database) -> None:
        result = seeded_db.query("SELECT DISTINCT status FROM orders ORDER BY status")
        actual = {row[0] for row in result.rows}
        assert actual.issubset({"pending", "shipped", "delivered", "cancelled"})