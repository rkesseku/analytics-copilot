"""
llm/text_to_sql.py

Generate SQL from a natural-language question using an LLM, with retrieved
schema context.

Workflow:
  1. Retrieve relevant tables for the question (SchemaRetriever).
  2. Build a structured prompt: schema + question + output-format instructions.
  3. Call the LLM, get back JSON.
  4. Parse with Pydantic for type-safe access.

What this module does NOT do:
  - Execute the SQL (that's SQLExecutor's job; it validates first).
  - Evaluate whether the SQL is correct (a downstream concern).
"""

from __future__ import annotations

import json
import re
from datetime import date
from textwrap import dedent

from pydantic import BaseModel, ConfigDict, Field

from analytics_copilot.llm.client import LLMClient
from analytics_copilot.schema.retrieval import SchemaRetriever


SYSTEM_PROMPT_TEMPLATE = dedent("""
    You are an expert SQL analyst working on a DuckDB database. Your job is to
    convert a business question into a correct, safe, read-only SQL query.

    Rules you MUST follow:
      1. Use ONLY the tables and columns listed in the SCHEMA below.
      2. Generate exactly ONE SELECT statement. No DDL, no DML, no comments.
      3. When the question mentions revenue, use SUM(order_items.quantity * order_items.unit_price)
         from the order_items table (NOT from products), and filter to orders.status = 'delivered'
         unless the question explicitly says otherwise.
      4. When matching categorical values, use the exact case from the column's examples
         (e.g. 'Electronics', not 'electronics').
      5. For relative date filters ("last quarter", "last year"), compute dates relative
         to today's date: {today}.
      6. Include a LIMIT clause unless the question requires returning all rows.

    Respond with valid JSON ONLY, in this exact shape (no markdown, no prose):
    {{
      "sql": "<the SQL query as one line, no trailing semicolon>",
      "rationale": "<one short sentence explaining the approach>",
      "confidence": <float 0.0-1.0 reflecting your confidence in correctness>
    }}
""").strip()


class SQLGenerationOutput(BaseModel):
    """Structured output from the text-to-SQL LLM call."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(..., min_length=1, description="The generated SQL query.")
    rationale: str = Field(..., description="Short explanation of the query approach.")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Self-reported confidence.")
    schema_tables_used: tuple[str, ...] = Field(
        default=(),
        description="Tables the retriever surfaced as relevant for this question.",
    )


class TextToSQLGenerator:
    """Generates SQL from natural-language questions using an LLM + schema RAG."""

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        retriever: SchemaRetriever,
        top_k: int = 4,
    ) -> None:
        self._llm = llm_client
        self._retriever = retriever
        self._top_k = top_k

    def generate(self, question: str, *, today: date | None = None) -> SQLGenerationOutput:
        """Convert a natural-language question into a structured SQL output."""
        if not question or not question.strip():
            raise ValueError("Empty question.")

        retrieval = self._retriever.retrieve(question, top_k=self._top_k)
        schema_block = retrieval.to_llm_text()
        retrieved_table_names = tuple(t.name for t in retrieval.tables)

        today_str = (today or date.today()).isoformat()
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(today=today_str)
        user_prompt = dedent(f"""
            SCHEMA:
            {schema_block}

            QUESTION:
            {question}
        """).strip()

        completion = self._llm.complete(
            system=system_prompt,
            user=user_prompt,
            temperature=0.0,
            max_tokens=400,
        )

        raw_output = self._parse_json(completion.text)
        return SQLGenerationOutput(
            sql=raw_output["sql"].strip().rstrip(";"),
            rationale=str(raw_output.get("rationale", "")).strip(),
            confidence=float(raw_output["confidence"]),
            schema_tables_used=retrieved_table_names,
        )

    @staticmethod
    def _parse_json(text: str) -> dict[str, object]:
        """Extract a JSON object from the LLM's response, tolerating code fences."""
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Generator returned non-JSON output: {text!r}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"Generator output is not a JSON object: {text!r}")

        for required in ("sql", "confidence"):
            if required not in data:
                raise ValueError(f"Generator output missing '{required}': {data!r}")

        return data
