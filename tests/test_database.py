"""Tests for db.database (Database connection wrapper)."""

from __future__ import annotations

import pytest

from analytics_copilot.db.database import Database


class TestDatabase:
    def test_in_memory_db_works(self) -> None:
        with Database() as db:
            db.connection.execute("CREATE TABLE t (x INTEGER)")
            db.connection.execute("INSERT INTO t VALUES (1), (2), (3)")
            result = db.query("SELECT x FROM t ORDER BY x")
            assert result.rows == [(1,), (2,), (3,)]
            assert result.columns == ["x"]
            assert result.row_count == 3

    def test_query_returns_empty_for_no_rows(self) -> None:
        with Database() as db:
            db.connection.execute("CREATE TABLE t (x INTEGER)")
            result = db.query("SELECT x FROM t WHERE x > 100")
            assert result.rows == []
            assert result.row_count == 0

    def test_parameterized_query(self) -> None:
        with Database() as db:
            db.connection.execute("CREATE TABLE t (x INTEGER, y VARCHAR)")
            db.connection.execute("INSERT INTO t VALUES (1, 'a'), (2, 'b')")
            result = db.query("SELECT y FROM t WHERE x = ?", [2])
            assert result.rows == [("b",)]

    def test_access_before_connect_raises(self) -> None:
        db = Database()
        with pytest.raises(RuntimeError):
            _ = db.connection

    def test_context_manager_closes_on_exit(self) -> None:
        db = Database()
        with db:
            assert db.connection is not None
        # After exiting, connection should be cleared
        with pytest.raises(RuntimeError):
            _ = db.connection

    def test_double_close_safe(self) -> None:
        db = Database()
        db.connect()
        db.close()
        db.close()  # should not raise
