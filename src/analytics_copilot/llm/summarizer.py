"""
llm/summarizer.py

Convert a SQL query's results into a short natural-language summary.

The summarizer takes the original user question, the executed SQL, and the
result rows, and produces a concise prose summary suitable for an executive
stakeholder who never sees the SQL.
"""

from __future__ import annotations

from textwrap import dedent
from typing import Any

from analytics_copilot.llm.client import LLMClient


SYSTEM_PROMPT = dedent("""
    You are a senior analyst summarizing a query result for a busy executive.

    Given a business question, the SQL that was run, and the result rows,
    write ONE short paragraph (1-3 sentences) that:
      - Directly answers the original question.
      - Cites the most important numbers from the result.
      - Notes any caveat that would be obvious to a careful analyst
        (e.g., "based on delivered orders only", "across all time periods").

    Do NOT explain the SQL. Do NOT use bullet points or headings. Write in
    natural prose, in the voice of a confident analyst.
""").strip()


class ResultSummarizer:
    """Generates natural-language summaries of query results."""

    MAX_PREVIEW_ROWS: int = 20
    MAX_PREVIEW_CHARS: int = 4000

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def summarize(
        self,
        *,
        question: str,
        sql: str,
        columns: list[str],
        rows: list[tuple[Any, ...]],
    ) -> str:
        """Produce a short prose summary of the query results."""
        if not question or not question.strip():
            raise ValueError("Empty question.")
        if not columns:
            raise ValueError("Empty columns.")

        if not rows:
            return "The query returned no rows."

        results_text = self._format_results(columns, rows)

        user_prompt = dedent(f"""
            QUESTION:
            {question}

            SQL EXECUTED:
            {sql}

            RESULT ({min(len(rows), self.MAX_PREVIEW_ROWS)} of {len(rows)} rows shown):
            {results_text}
        """).strip()

        completion = self._llm.complete(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            temperature=0.2,  # a touch of warmth in the writing
            max_tokens=200,
        )

        return completion.text.strip()

    def _format_results(
        self,
        columns: list[str],
        rows: list[tuple[Any, ...]],
    ) -> str:
        """Render result rows as a compact text table for the prompt."""
        preview_rows = rows[: self.MAX_PREVIEW_ROWS]

        header = " | ".join(columns)
        separator = "-+-".join("-" * len(c) for c in columns)
        body_lines = [
            " | ".join(self._fmt_cell(c) for c in row) for row in preview_rows
        ]
        table_text = "\n".join([header, separator, *body_lines])

        # Defensive cap on prompt size for very wide rows
        if len(table_text) > self.MAX_PREVIEW_CHARS:
            table_text = table_text[: self.MAX_PREVIEW_CHARS] + "\n... (truncated)"
        return table_text

    @staticmethod
    def _fmt_cell(value: Any) -> str:
        """Format one cell value for prompt-friendly display."""
        if value is None:
            return "NULL"
        if isinstance(value, float):
            return f"{value:.2f}"
        return str(value)
    