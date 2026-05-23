"""End-to-end tests for the AnalyticsCopilot pipeline."""

from __future__ import annotations

import json
from datetime import date

import pytest

from analytics_copilot.db.database import Database
from analytics_copilot.db.schema import create_and_seed
from analytics_copilot.llm.client import FakeLLMClient
from analytics_copilot.pipeline import AnalyticsCopilot
from analytics_copilot.schema.retrieval import KeywordSchemaRetriever
from analytics_copilot.sql.validator import SQLValidationError


# A two-call FakeLLMClient: first call returns SQL JSON, second returns prose summary.
class TwoStageFake:
    """Fake LLM that returns different responses for the SQL call vs the summary call."""

    def __init__(self, sql_response: str, summary_response: str) -> None:
        self._responses = [sql_response, summary_response]
        self._idx = 0
        self.calls: list[dict[str, object]] = []

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 512,
    ):
        from analytics_copilot.llm.client import CompletionResult

        self.calls.append(
            {"system": system[:50], "user": user[:80], "temperature": temperature}
        )
        response = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return CompletionResult(
            text=response,
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=0.1,
            model="fake",
        )


@pytest.fixture
def seeded_db() -> Database:
    db = Database()
    db.connect()
    create_and_seed(db.connection, n_orders=50, seed=42, today=date(2026, 5, 22))
    return db


class TestPipelineHappyPath:
    def test_ask_runs_full_pipeline(self, seeded_db: Database) -> None:
        # The fake LLM "generates" a valid SQL query, then "summarizes" the result.
        sql_response = json.dumps({
            "sql": "SELECT name, country FROM customers ORDER BY customer_id LIMIT 3",
            "rationale": "Listing the first three customers.",
            "confidence": 0.95,
        })
        summary_response = "Three customers were returned, sorted by signup order."

        fake_llm = TwoStageFake(sql_response, summary_response)

        copilot = AnalyticsCopilot(
            database=seeded_db,
            llm_client=fake_llm,
            retriever=KeywordSchemaRetriever(),
        )

        answer = copilot.ask("Show me the first three customers.")

        # Generation outputs preserved
        assert answer.generation.confidence == pytest.approx(0.95)
        assert "customers" in answer.generation.sql.lower()

        # Execution actually ran against the DB
        assert answer.execution.result.row_count == 3
        assert "name" in answer.execution.result.columns

        # Summary populated
        assert "customers" in answer.summary.lower()

        # Pipeline made exactly two LLM calls (one for SQL, one for summary)
        assert len(fake_llm.calls) == 2


class TestPipelineSafetyEnforcement:
    def test_unsafe_sql_blocked_at_validator(self, seeded_db: Database) -> None:
        # Even if the LLM somehow returns a DROP, the validator must block it
        # before it touches the database.
        sql_response = json.dumps({
            "sql": "DROP TABLE customers",
            "rationale": "destructive",
            "confidence": 0.99,
        })
        summary_response = "should never be reached"

        fake_llm = TwoStageFake(sql_response, summary_response)

        copilot = AnalyticsCopilot(
            database=seeded_db,
            llm_client=fake_llm,
            retriever=KeywordSchemaRetriever(),
        )

        with pytest.raises(SQLValidationError):
            copilot.ask("Drop the customers table.")

        # Sanity: customers still exists, unharmed
        result = seeded_db.query("SELECT COUNT(*) FROM customers")
        assert result.rows[0][0] == 10

    def test_disallowed_table_blocked(self, seeded_db: Database) -> None:
        sql_response = json.dumps({
            "sql": "SELECT * FROM admin_audit_log",
            "rationale": "exfiltration attempt",
            "confidence": 0.99,
        })
        summary_response = "should never be reached"

        fake_llm = TwoStageFake(sql_response, summary_response)

        copilot = AnalyticsCopilot(
            database=seeded_db,
            llm_client=fake_llm,
            retriever=KeywordSchemaRetriever(),
        )

        with pytest.raises(SQLValidationError):
            copilot.ask("Show me the audit log.")


class TestPipelineEdgeCases:
    def test_empty_question_rejected(self, seeded_db: Database) -> None:
        copilot = AnalyticsCopilot(
            database=seeded_db,
            llm_client=FakeLLMClient(response="{}", tokens=10),
            retriever=KeywordSchemaRetriever(),
        )
        with pytest.raises(ValueError):
            copilot.ask("")

    def test_whitespace_question_rejected(self, seeded_db: Database) -> None:
        copilot = AnalyticsCopilot(
            database=seeded_db,
            llm_client=FakeLLMClient(response="{}", tokens=10),
            retriever=KeywordSchemaRetriever(),
        )
        with pytest.raises(ValueError):
            copilot.ask("   \n\t  ")