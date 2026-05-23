"""Tests for the eval harness itself.

We test the *scorer*, not the LLM. If the scorer is broken, all eval numbers
become meaningless, so this is the most important test file for the harness.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from analytics_copilot.db.database import Database
from analytics_copilot.db.schema import create_and_seed
from evals.scorer import (
    GoldenCase,
    load_golden_set,
    rows_match,
    run_eval,
    score_case,
)

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "evals" / "golden_sets" / "shop.yaml"

# Must match evals/run.py and evals/build_golden_set.py
TODAY = date(2025, 6, 1)
SEED = 42
N_ORDERS = 120


@pytest.fixture(scope="module")
def db():
    """A seeded DuckDB connection matching the golden set's data."""
    database = Database()
    database.connect()
    create_and_seed(database.connection, n_orders=N_ORDERS, seed=SEED, today=TODAY)
    yield database.connection
    database.close()


# ----- rows_match -------------------------------------------------------------


class TestRowsMatch:
    def test_exact_match_unordered(self):
        assert rows_match([[1], [2]], [[2], [1]], ordered=False)

    def test_exact_match_ordered(self):
        assert rows_match([[1], [2]], [[1], [2]], ordered=True)

    def test_ordered_mismatch_fails(self):
        assert not rows_match([[1], [2]], [[2], [1]], ordered=True)

    def test_float_tolerance(self):
        # Floats are rounded to 2 decimals before comparison
        assert rows_match([[1.001]], [[1.0]], ordered=False)

    def test_mismatched_lengths_fail(self):
        assert not rows_match([[1]], [[1], [2]], ordered=False)


# ----- score_case -------------------------------------------------------------


class TestScoreCase:
    def test_passing_case(self, db):
        case = GoldenCase(id="t1", question="count customers", expected_rows=[[10]])
        result = score_case(case, "SELECT COUNT(*) FROM customers", db)
        assert result.passed, result.reason
        assert result.reason == "ok"

    def test_row_mismatch(self, db):
        case = GoldenCase(id="t2", question="count customers", expected_rows=[[999]])
        result = score_case(case, "SELECT COUNT(*) FROM customers", db)
        assert not result.passed
        assert result.reason == "row_mismatch"

    def test_sql_error_caught(self, db):
        case = GoldenCase(id="t3", question="bad query", expected_rows=[[1]])
        result = score_case(case, "SELECT * FROM nonexistent_table", db)
        assert not result.passed
        assert "sql_error" in result.reason


# ----- end-to-end -------------------------------------------------------------


class TestEndToEnd:
    def test_reference_sql_scores_100_percent(self, db):
        """The golden set's reference_sql values must score 100% against the
        same seeded DB they were generated from. If this fails, either the
        seed changed or expected_rows is stale — regenerate the YAML with
        `python -m evals.build_golden_set`."""
        cases = load_golden_set(GOLDEN_PATH)
        reference_lookup = {c.question: c.reference_sql for c in cases}

        def reference_pipeline(q: str) -> str:
            sql = reference_lookup[q]
            assert sql is not None, f"No reference_sql for {q!r}"
            return sql

        report = run_eval(reference_pipeline, db, GOLDEN_PATH)
        assert report.pass_rate == 1.0, (
            f"Reference SQL only scored {report.pass_rate:.1%}. "
            f"Failures: {[(r.case_id, r.reason) for r in report.results if not r.passed]}"
        )

    def test_golden_set_loads(self):
        cases = load_golden_set(GOLDEN_PATH)
        assert len(cases) >= 5, "Golden set is suspiciously small"
        assert all(c.id.startswith("q") for c in cases)
        assert all(c.expected_rows for c in cases)
