"""Tests for the eval harness (query bank, comparison, judge, runner) with stub LLMs."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from analytics_copilot.db.backend import QueryResult
from analytics_copilot.evals.bank import DEFAULT_BANK_PATH, load_bank
from analytics_copilot.evals.compare import results_match
from analytics_copilot.evals.judge import CommentaryJudge, parse_verdict
from analytics_copilot.evals.runner import main, run_eval, to_markdown
from analytics_copilot.llm.client import FakeLLMClient

REPO_ROOT = Path(__file__).resolve().parent.parent
BANK_PATH = REPO_ROOT / DEFAULT_BANK_PATH

GOOD_VERDICT = json.dumps(
    {"faithfulness": 5, "relevance": 5, "clarity": 4, "reasoning": "Matches the data."}
)


class TestResultsMatch:
    def test_order_insensitive(self) -> None:
        assert results_match([("a", 1), ("b", 2)], [("b", 2), ("a", 1)])

    def test_numeric_tolerance_and_decimal(self) -> None:
        assert results_match([(Decimal("10.50"),)], [(10.5000001,)])
        assert results_match([(1,)], [(1.0,)])

    def test_different_values_fail(self) -> None:
        assert not results_match([("a", 1)], [("a", 2)])

    def test_row_count_mismatch_fails(self) -> None:
        assert not results_match([(1,), (2,)], [(1,)])

    def test_column_count_mismatch_fails(self) -> None:
        assert not results_match([(1, 2)], [(1,)])

    def test_dates_and_nulls(self) -> None:
        assert results_match([(date(2025, 1, 1), None)], [(date(2025, 1, 1), None)])
        assert not results_match([(date(2025, 1, 1),)], [(date(2025, 1, 2),)])

    def test_duplicates_matter(self) -> None:
        assert not results_match([(1,), (1,)], [(1,), (2,)])


class TestJudge:
    def test_parse_plain_and_fenced(self) -> None:
        assert parse_verdict(GOOD_VERDICT).faithfulness == 5
        fenced = parse_verdict(f"```json\n{GOOD_VERDICT}\n```")
        assert fenced.clarity == 4

    def test_score_normalized(self) -> None:
        low = parse_verdict('{"faithfulness":1,"relevance":1,"clarity":1}')
        high = parse_verdict('{"faithfulness":5,"relevance":5,"clarity":5}')
        assert low.score == 0.0
        assert high.score == 1.0

    @pytest.mark.parametrize(
        "bad",
        ["not json", '{"faithfulness": 9, "relevance": 1, "clarity": 1}', '{"relevance": 3}'],
    )
    def test_invalid_verdict_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_verdict(bad)

    def test_judge_prompt_contains_inputs(self) -> None:
        llm = FakeLLMClient(response=GOOD_VERDICT)
        verdict = CommentaryJudge(llm).judge(
            question="How many customers?",
            sql="SELECT COUNT(*) AS n FROM customers",
            columns=["n"],
            rows=[(10,)],
            commentary="There are 10 customers.",
        )
        assert verdict.score == pytest.approx((14 / 3 - 1) / 4)
        user = str(llm.calls[0]["user"])
        assert "How many customers?" in user
        assert "There are 10 customers." in user
        assert llm.calls[0]["temperature"] == 0.0


class FakeGoldDB:
    """Backend stub: returns canned gold results keyed by SQL text."""

    dialect = "duckdb"

    def __init__(self, gold: dict[str, list[tuple[Any, ...]]]) -> None:
        self._gold = gold

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def list_columns(self) -> dict[str, list[tuple[str, str]]]:
        return {}

    def query(self, sql: str, params: list[Any] | None = None) -> QueryResult:
        if sql not in self._gold:
            raise RuntimeError("no such gold query")
        rows = self._gold[sql]
        return QueryResult(columns=["c"], rows=rows, row_count=len(rows))


class FakeCopilot:
    """Copilot stub: returns canned rows per question (or raises)."""

    def __init__(self, rows_by_question: dict[str, list[tuple[Any, ...]] | Exception]) -> None:
        self._rows = rows_by_question

    def ask(self, question: str, *, today: date | None = None) -> Any:
        outcome = self._rows[question]
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            summary=f"Answer for {question}",
            execution=SimpleNamespace(
                executed_sql="SELECT 1",
                result=SimpleNamespace(columns=["c"], rows=outcome),
            ),
        )


BANK_YAML = """
version: 3
anchor_date: "2026-05-22"
questions:
  - {id: q1, question: "Q one?", gold_sql: "GOLD1"}
  - {id: q2, question: "Q two?", gold_sql: "GOLD2"}
  - {id: q3, question: "Q three?", gold_sql: "GOLD3"}
  - {id: q4, question: "Q four?", gold_sql: "GOLD4"}
"""


@pytest.fixture
def bank_file(tmp_path: Path) -> Path:
    path = tmp_path / "bank.yaml"
    path.write_text(BANK_YAML, encoding="utf-8")
    return path


@pytest.fixture
def gold_db() -> FakeGoldDB:
    return FakeGoldDB(
        {"GOLD1": [(1,)], "GOLD2": [(2,), (3,)], "GOLD3": [(Decimal("5.00"),)], "GOLD4": [(9,)]}
    )


@pytest.fixture
def copilot() -> FakeCopilot:
    return FakeCopilot(
        {
            "Q one?": [(1,)],  # correct
            "Q two?": [(3,), (2,)],  # correct, different order
            "Q three?": [(6,)],  # wrong
            "Q four?": RuntimeError("boom"),  # pipeline failure
        }
    )


class TestRunner:
    def test_scores_and_threshold(
        self, bank_file: Path, gold_db: FakeGoldDB, copilot: FakeCopilot
    ) -> None:
        judge = CommentaryJudge(FakeLLMClient(response=GOOD_VERDICT))
        report = run_eval(
            load_bank(bank_file), copilot=copilot, gold_db=gold_db, judge=judge, min_accuracy=0.5
        )
        assert (report.total, report.correct) == (4, 2)
        assert report.execution_accuracy == 0.5
        assert report.passed is True
        assert report.judged == 3  # the failed pipeline case has nothing to judge
        assert report.mean_judge_score == pytest.approx((14 / 3 - 1) / 4)
        failed = {c.id: c for c in report.cases}["q4"]
        assert failed.error is not None and "boom" in failed.error

        strict = run_eval(
            load_bank(bank_file), copilot=copilot, gold_db=gold_db, judge=None, min_accuracy=0.75
        )
        assert strict.passed is False
        assert strict.mean_judge_score is None

    def test_judge_failure_is_recorded_not_fatal(
        self, bank_file: Path, gold_db: FakeGoldDB, copilot: FakeCopilot
    ) -> None:
        judge = CommentaryJudge(FakeLLMClient(response="garbage"))
        report = run_eval(load_bank(bank_file), copilot=copilot, gold_db=gold_db, judge=judge)
        assert report.judged == 0
        assert all(c.judge_error for c in report.cases if c.execution_ok)

    def test_markdown_report(
        self, bank_file: Path, gold_db: FakeGoldDB, copilot: FakeCopilot
    ) -> None:
        report = run_eval(load_bank(bank_file), copilot=copilot, gold_db=gold_db, min_accuracy=0.5)
        md = to_markdown(report)
        assert "# Eval report" in md
        assert "| q1 |" in md
        assert "PASS" in md

    def test_main_writes_reports_and_exit_codes(
        self, bank_file: Path, gold_db: FakeGoldDB, copilot: FakeCopilot, tmp_path: Path
    ) -> None:
        out = tmp_path / "reports"
        common = ["--bank", str(bank_file), "--out-dir", str(out)]
        stub = FakeLLMClient(response=GOOD_VERDICT)

        code = main(
            [*common, "--min-accuracy", "0.5"],
            judge_client=stub,
            copilot=copilot,
            gold_db=gold_db,
        )
        assert code == 0
        data = json.loads((out / "report.json").read_text(encoding="utf-8"))
        assert data["correct"] == 2 and data["bank_version"] == 3
        assert (out / "report.md").exists()

        code = main(
            [*common, "--min-accuracy", "0.9", "--no-judge"],
            copilot=copilot,
            gold_db=gold_db,
        )
        assert code == 1

    def test_main_missing_bank_exits_2(self, tmp_path: Path) -> None:
        assert main(["--bank", str(tmp_path / "nope.yaml")]) == 2


class TestQueryBankFile:
    def test_bank_loads_and_is_sized_as_documented(self) -> None:
        bank = load_bank(BANK_PATH)
        assert 20 <= len(bank.questions) <= 30
        assert bank.anchor_date == date(2026, 5, 22)

    def test_duplicate_ids_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "dupe.yaml"
        path.write_text(
            "version: 1\nanchor_date: '2026-05-22'\nquestions:\n"
            "  - {id: a, question: x, gold_sql: y}\n  - {id: a, question: x2, gold_sql: y2}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_bank(path)


class TestGoldSqlAgainstDemoData:
    """Every gold query must be valid and executable on the seeded DuckDB demo data."""

    def test_gold_sql_validates_and_executes(self) -> None:
        pytest.importorskip("duckdb")
        pytest.importorskip("sqlglot")
        from analytics_copilot.db.database import Database
        from analytics_copilot.db.schema import create_and_seed
        from analytics_copilot.sql.validator import SQLValidator

        bank = load_bank(BANK_PATH)
        validator = SQLValidator()
        with Database() as db:
            create_and_seed(db.connection, seed=42, today=bank.anchor_date)
            for item in bank.questions:
                validator.validate(item.gold_sql)
                result = db.query(item.gold_sql)
                assert result.columns, item.id
