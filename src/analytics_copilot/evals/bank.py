"""
evals/bank.py

Load and validate the versioned held-out query bank (YAML).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DEFAULT_BANK_PATH = Path("evals") / "query_bank.yaml"


class BankQuestion(BaseModel):
    """One question with its gold SQL."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    gold_sql: str = Field(..., min_length=1)
    category: str = "general"


class QueryBank(BaseModel):
    """A versioned collection of held-out questions."""

    model_config = ConfigDict(extra="forbid")

    version: int
    anchor_date: date
    questions: list[BankQuestion]

    @field_validator("questions")
    @classmethod
    def _unique_ids(cls, value: list[BankQuestion]) -> list[BankQuestion]:
        ids = [q.id for q in value]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"Duplicate question ids: {dupes}")
        if not value:
            raise ValueError("Query bank has no questions.")
        return value


def load_bank(path: str | Path = DEFAULT_BANK_PATH) -> QueryBank:
    """Parse and validate a query bank file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    try:
        return QueryBank.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid query bank {path}: {exc}") from exc
