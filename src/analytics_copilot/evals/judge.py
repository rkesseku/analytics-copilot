"""
evals/judge.py

LLM-as-judge scoring of the written commentary against the query result.

The judge sees the question, the executed SQL, a preview of the result rows
and the commentary under review, and returns a structured JSON verdict that is
parsed and validated with pydantic.
"""

from __future__ import annotations

import json
import re
from textwrap import dedent
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from analytics_copilot.llm.client import LLMClient

JUDGE_SYSTEM_PROMPT = dedent("""
    You are a strict reviewer grading a written analytics answer against the
    data it was written from.

    Score each criterion as an integer from 1 (very poor) to 5 (excellent):
      - faithfulness: every number and claim in the commentary is supported by
        the RESULT. Penalize invented, misquoted or unsupported figures heavily.
      - relevance: the commentary directly answers the QUESTION.
      - clarity: concise, plain prose an executive can read quickly; caveats
        are noted where they matter.

    Respond with valid JSON ONLY, no markdown, in exactly this shape:
    {"faithfulness": <1-5>, "relevance": <1-5>, "clarity": <1-5>,
     "reasoning": "<one or two sentences>"}
""").strip()

MAX_PREVIEW_ROWS = 20


class JudgeVerdict(BaseModel):
    """Structured judge output."""

    model_config = ConfigDict(extra="ignore")

    faithfulness: int = Field(..., ge=1, le=5)
    relevance: int = Field(..., ge=1, le=5)
    clarity: int = Field(..., ge=1, le=5)
    reasoning: str = ""

    @property
    def score(self) -> float:
        """Mean rubric score normalized to 0..1 (1 -> 0.0, 5 -> 1.0)."""
        mean = (self.faithfulness + self.relevance + self.clarity) / 3
        return (mean - 1) / 4


def parse_verdict(text: str) -> JudgeVerdict:
    """Parse the judge's raw text into a verdict, tolerating code fences / extra prose."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"Judge returned no JSON object: {text!r}")
    try:
        return JudgeVerdict.model_validate(json.loads(cleaned[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"Judge returned invalid verdict: {text!r}") from exc


def _fmt_cell(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


class CommentaryJudge:
    """Grades commentary quality using an LLM."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def judge(
        self,
        *,
        question: str,
        sql: str,
        columns: list[str],
        rows: list[tuple[Any, ...]],
        commentary: str,
    ) -> JudgeVerdict:
        """Score `commentary` for the given question and result."""
        preview = "\n".join(" | ".join(_fmt_cell(c) for c in r) for r in rows[:MAX_PREVIEW_ROWS])
        user = dedent(f"""
            QUESTION:
            {question}

            SQL EXECUTED:
            {sql}

            RESULT ({min(len(rows), MAX_PREVIEW_ROWS)} of {len(rows)} rows shown):
            {" | ".join(columns)}
            {preview}

            COMMENTARY TO GRADE:
            {commentary}
        """).strip()
        completion = self._llm.complete(
            system=JUDGE_SYSTEM_PROMPT, user=user, temperature=0.0, max_tokens=300
        )
        return parse_verdict(completion.text)
