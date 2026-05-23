"""
schema/retrieval.py

Retrieves the subset of schema tables relevant to a user's natural-language
question. This is the "RAG" layer of our text-to-SQL pipeline.

For our small toy schema, keyword matching is sufficient. The `SchemaRetriever`
Protocol lets a more sophisticated retriever (e.g. embedding-based) be swapped
in without touching downstream code.

Production note:
  Real-world data warehouses have hundreds-to-thousands of tables. There you'd
  use embedding similarity (sentence-transformers, OpenAI embeddings, etc.)
  against a vector store, possibly combined with keyword/BM25 hybrid retrieval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from analytics_copilot.schema.metadata import ALL_TABLES, TableInfo


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Result of retrieving relevant tables for a question."""

    tables: tuple[TableInfo, ...]
    scores: tuple[float, ...]  # parallel to `tables`; higher = more relevant

    def to_llm_text(self) -> str:
        """Render retrieved tables as a single LLM-ready schema block."""
        return "\n\n".join(t.to_llm_text() for t in self.tables)


@runtime_checkable
class SchemaRetriever(Protocol):
    """Protocol for any module that retrieves relevant tables given a question."""

    def retrieve(self, question: str, *, top_k: int = 4) -> RetrievalResult: ...


class KeywordSchemaRetriever:
    """Score tables by keyword overlap between the question and each table's metadata.

    A table accumulates points when the user's question mentions:
      - the table name (heavy weight)
      - one of its column names (medium weight)
      - any of the column example values (light weight)
      - words that appear in the description (light weight)

    Tables are returned sorted by descending score. When `top_k` is large enough
    to include zero-score tables, those tables are also returned to keep joins
    feasible (a query may need a "join-only" table not directly named).
    """

    NAME_WEIGHT: float = 3.0
    COLUMN_WEIGHT: float = 2.0
    EXAMPLE_WEIGHT: float = 1.0
    DESCRIPTION_WEIGHT: float = 0.5

    def __init__(self, tables: tuple[TableInfo, ...] = ALL_TABLES) -> None:
        self._tables = tables

    def retrieve(self, question: str, *, top_k: int = 4) -> RetrievalResult:
        if not question or not question.strip():
            raise ValueError("Empty question.")
        if top_k <= 0:
            raise ValueError("top_k must be >= 1.")

        tokens = self._tokenize(question)
        scored: list[tuple[TableInfo, float]] = [(t, self._score(t, tokens)) for t in self._tables]

        # Sort by score descending, with stable ordering by table name as tiebreak
        scored.sort(key=lambda x: (-x[1], x[0].name))

        # Always return at least one table even if all scores are zero,
        # to avoid returning an empty schema context.
        top = scored[:top_k]
        return RetrievalResult(
            tables=tuple(t for t, _ in top),
            scores=tuple(s for _, s in top),
        )

    # ----- internals -----

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Lowercase, split on non-alphanumerics, drop very short tokens."""
        words = re.split(r"[^a-z0-9]+", text.lower())
        return {w for w in words if len(w) >= 3}

    def _score(self, table: TableInfo, question_tokens: set[str]) -> float:
        score = 0.0

        # Table name (and simple singular form for plural table names)
        name_tokens = {table.name.lower(), table.name.lower().rstrip("s")}
        if name_tokens & question_tokens:
            score += self.NAME_WEIGHT

        # Columns
        for col in table.columns:
            col_tokens = self._tokenize(col.name)
            if col_tokens & question_tokens:
                score += self.COLUMN_WEIGHT

            # Example values (case-insensitive whole-token match)
            for ex in col.examples:
                if ex.lower() in question_tokens:
                    score += self.EXAMPLE_WEIGHT

        # Description words
        desc_tokens = self._tokenize(table.description)
        if desc_tokens & question_tokens:
            score += self.DESCRIPTION_WEIGHT

        return score
