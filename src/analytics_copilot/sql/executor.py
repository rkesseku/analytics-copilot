"""
sql/executor.py

Public entry point for executing LLM-generated SQL.

Workflow per call:
  1. Validator inspects the SQL (parse, statement-type, table allow-list).
  2. Validator may inject a row LIMIT if missing.
  3. The (possibly rewritten) SQL is executed against the database.
  4. Results are returned along with metadata about what was executed.

Any code that takes untrusted SQL should go through this class -- never call
Backend.query() directly with LLM output.
"""

from __future__ import annotations

from dataclasses import dataclass

from analytics_copilot.db.backend import Backend, QueryResult
from analytics_copilot.sql.validator import (
    SQLValidator,
    ValidationResult,
)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Outcome of safely executing a SQL query."""

    original_sql: str
    executed_sql: str  # may differ from original if LIMIT was added
    result: QueryResult
    tables_referenced: tuple[str, ...]
    row_limit_added: bool


class SQLExecutor:
    """Validates then executes SQL against the database."""

    def __init__(self, *, database: Backend, validator: SQLValidator | None = None) -> None:
        self._db = database
        # Default validator parses SQL in the backend's own dialect.
        self._validator = validator or SQLValidator(dialect=database.dialect)

    def execute(self, sql: str) -> ExecutionResult:
        """Validate and execute `sql`. Raises SQLValidationError on rejection."""
        validation: ValidationResult = self._validator.validate(sql)
        result = self._db.query(validation.sql)

        return ExecutionResult(
            original_sql=sql,
            executed_sql=validation.sql,
            result=result,
            tables_referenced=validation.tables_referenced,
            row_limit_added=validation.row_limit_added,
        )
