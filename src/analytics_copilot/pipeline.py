"""
pipeline.py

End-to-end orchestrator for the analytics copilot.

Wires together every component:
  1. Schema retriever surfaces relevant tables for the question.
  2. Text-to-SQL generator produces structured SQL output.
  3. SQL executor validates and runs the query.
  4. Summarizer produces a natural-language answer.

Returns a structured AnswerResult so callers (CLI, tests, future UIs) can
display whichever parts they want.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from analytics_copilot.db.database import Database
from analytics_copilot.db.schema import create_and_seed
from analytics_copilot.llm.client import GroqClient, LLMClient
from analytics_copilot.llm.summarizer import ResultSummarizer
from analytics_copilot.llm.text_to_sql import SQLGenerationOutput, TextToSQLGenerator
from analytics_copilot.schema.retrieval import KeywordSchemaRetriever, SchemaRetriever
from analytics_copilot.sql.executor import ExecutionResult, SQLExecutor
from analytics_copilot.sql.validator import SQLValidationError, SQLValidator


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """Full result of answering one natural-language question."""

    question: str
    generation: SQLGenerationOutput
    execution: ExecutionResult
    summary: str
    total_tokens: int  # across all LLM calls in this answer
    total_latency_ms: float


@dataclass(slots=True)
class PipelineStats:
    """Cumulative LLM usage across all questions answered by a pipeline instance."""

    total_questions: int = 0
    total_tokens: int = 0
    total_latency_ms: float = 0.0
    errors: list[str] = field(default_factory=list)


class AnalyticsCopilot:
    """High-level facade: ask a question, get an answer."""

    def __init__(
        self,
        *,
        database: Database,
        llm_client: LLMClient | None = None,
        retriever: SchemaRetriever | None = None,
        validator: SQLValidator | None = None,
    ) -> None:
        self._db = database
        self._llm = llm_client or GroqClient()
        self._retriever = retriever or KeywordSchemaRetriever()
        self._executor = SQLExecutor(database=database, validator=validator)

        # Two distinct LLM "roles" using the same client (cheaper alternative
        # would be to pass a smaller model client for the summarizer).
        self._generator = TextToSQLGenerator(
            llm_client=self._llm,
            retriever=self._retriever,
        )
        self._summarizer = ResultSummarizer(llm_client=self._llm)
        self.stats = PipelineStats()

    def ask(self, question: str, *, today: date | None = None) -> AnswerResult:
        """Answer a natural-language question end-to-end.

        Raises
        ------
        ValueError
            If the question is empty.
        SQLValidationError
            If the LLM produced SQL that fails safety validation.
            (The validator's error is allowed to propagate; the caller decides
            whether to retry, log, or surface to the user.)
        """
        if not question or not question.strip():
            raise ValueError("Empty question.")

        try:
            # 1 + 2: retrieve schema + generate SQL
            generation = self._generator.generate(question, today=today)

            # 3: validate + execute
            execution = self._executor.execute(generation.sql)

            # 4: summarize results
            summary = self._summarizer.summarize(
                question=question,
                sql=execution.executed_sql,
                columns=execution.result.columns,
                rows=execution.result.rows,
            )
        except SQLValidationError as exc:
            self.stats.errors.append(f"validation: {exc}")
            raise

        # Stats accounting: only successful answers contribute (errors counted separately)
        self.stats.total_questions += 1
        # We don't currently track per-call tokens through the LLMClient protocol,
        # so we report 0 here. A future iteration will plumb usage through.
        self.stats.total_tokens += 0

        return AnswerResult(
            question=question,
            generation=generation,
            execution=execution,
            summary=summary,
            total_tokens=0,
            total_latency_ms=0.0,
        )


def build_demo_copilot(*, today: date | None = None) -> AnalyticsCopilot:
    """Convenience factory: builds a copilot with a fresh in-memory seeded DB."""
    db = Database()
    db.connect()
    create_and_seed(db.connection, seed=42, today=today or date.today())
    return AnalyticsCopilot(database=db)
