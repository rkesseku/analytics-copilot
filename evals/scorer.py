"""Score generated SQL against a golden set by executing it.

Why result-set comparison instead of SQL-string matching: semantically equivalent
SQL can differ textually (table aliases, column order, JOIN vs subquery, etc.).
The only ground truth that matters is whether the query returns the right rows.

DB-agnostic: the scorer only needs an object with .execute(sql) -> cursor where
the cursor supports .fetchall(). Both sqlite3 and DuckDB connections qualify.

Public API:
    load_golden_set(path)        -> list[GoldenCase]
    score_case(case, sql, conn)  -> CaseResult
    run_eval(pipeline, conn, …)  -> EvalReport
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

import yaml


class _ExecutableConn(Protocol):
    """Duck-typed connection — anything with .execute(sql) -> cursor works.

    We accept the broader Protocol instead of binding to sqlite3.Connection
    so the harness works against DuckDB, SQLite, or any DB-API-2 driver.
    """

    def execute(self, sql: str) -> Any: ...


# ----- data classes -----------------------------------------------------------


@dataclass
class GoldenCase:
    id: str
    question: str
    expected_rows: list[list[Any]]
    difficulty: str = "medium"
    tags: list[str] = field(default_factory=list)
    reference_sql: str | None = None
    ordered: bool = False


@dataclass
class CaseResult:
    case_id: str
    question: str
    generated_sql: str
    passed: bool
    reason: str  # "ok" | "sql_error" | "row_mismatch" | "pipeline_error"
    expected_rows: list[list[Any]]
    actual_rows: list[list[Any]]
    latency_ms: float
    difficulty: str
    tags: list[str]


@dataclass
class EvalReport:
    results: list[CaseResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def by_difficulty(self) -> dict[str, tuple[int, int]]:
        """Returns {difficulty: (passed, total)} per difficulty bucket."""
        buckets: dict[str, list[CaseResult]] = {}
        for r in self.results:
            buckets.setdefault(r.difficulty, []).append(r)
        return {d: (sum(1 for r in rs if r.passed), len(rs)) for d, rs in buckets.items()}


# ----- loading ----------------------------------------------------------------


def load_golden_set(path: str | Path) -> list[GoldenCase]:
    """Parse a YAML golden set into GoldenCase objects."""
    raw = yaml.safe_load(Path(path).read_text())
    cases = []
    for entry in raw:
        if entry.get("skip_until_computed"):
            continue
        cases.append(
            GoldenCase(
                id=entry["id"],
                question=entry["question"],
                expected_rows=[list(row) for row in entry["expected_rows"]],
                difficulty=entry.get("difficulty", "medium"),
                tags=list(entry.get("tags", [])),
                reference_sql=entry.get("reference_sql"),
                ordered=bool(entry.get("ordered", False)),
            )
        )
    return cases


# ----- comparison -------------------------------------------------------------


def _normalize_row(row: Iterable[Any]) -> tuple[Any, ...]:
    """Round floats and coerce to a hashable tuple for stable comparison.

    DuckDB returns Decimal for DECIMAL columns; YAML loads those as floats.
    We coerce both sides to float at 2-dp precision so they compare equal.
    """
    out = []
    for v in row:
        if v is None:
            out.append(None)
        elif isinstance(v, bool):
            out.append(v)
        elif isinstance(v, (int, float)):
            out.append(round(float(v), 2) if isinstance(v, float) else v)
        else:
            # Decimal, datetime, date, etc. — fall back to float if numeric,
            # otherwise str (dates compare as ISO strings).
            try:
                out.append(round(float(v), 2))
            except (TypeError, ValueError):
                out.append(str(v))
    return tuple(out)


def rows_match(
    expected: list[list[Any]],
    actual: list[list[Any]],
    ordered: bool,
) -> bool:
    """Compare result sets with float tolerance.

    Order-insensitive by default; pass ordered=True when the question
    explicitly cares about row order (e.g. "top N by X").
    """
    exp = [_normalize_row(r) for r in expected]
    act = [_normalize_row(r) for r in actual]
    if ordered:
        return exp == act
    return sorted(exp, key=repr) == sorted(act, key=repr)


# ----- execution --------------------------------------------------------------


def score_case(
    case: GoldenCase,
    generated_sql: str,
    conn: _ExecutableConn,
) -> CaseResult:
    """Execute generated SQL and compare to expected rows.

    Catches SQL errors and reports them as failures (not exceptions) so a single
    broken query never aborts the whole eval run.
    """
    start = time.perf_counter()
    try:
        cursor = conn.execute(generated_sql)
        actual = [list(row) for row in cursor.fetchall()]
    except Exception as e:  # noqa: BLE001 — catch any DB driver's error type
        return CaseResult(
            case_id=case.id,
            question=case.question,
            generated_sql=generated_sql,
            passed=False,
            reason=f"sql_error: {e}",
            expected_rows=case.expected_rows,
            actual_rows=[],
            latency_ms=(time.perf_counter() - start) * 1000,
            difficulty=case.difficulty,
            tags=case.tags,
        )

    passed = rows_match(case.expected_rows, actual, case.ordered)
    return CaseResult(
        case_id=case.id,
        question=case.question,
        generated_sql=generated_sql,
        passed=passed,
        reason="ok" if passed else "row_mismatch",
        expected_rows=case.expected_rows,
        actual_rows=actual,
        latency_ms=(time.perf_counter() - start) * 1000,
        difficulty=case.difficulty,
        tags=case.tags,
    )


# ----- top-level runner -------------------------------------------------------


PipelineFn = Callable[[str], str]
"""A pipeline takes a natural-language question and returns SQL."""


def run_eval(
    pipeline: PipelineFn,
    conn: _ExecutableConn,
    golden_path: str | Path,
) -> EvalReport:
    """Run the full eval loop: for each case, call the pipeline, score the SQL."""
    cases = load_golden_set(golden_path)
    results: list[CaseResult] = []
    for case in cases:
        try:
            sql = pipeline(case.question)
        except Exception as e:  # noqa: BLE001 — surface any pipeline error as a fail
            results.append(
                CaseResult(
                    case_id=case.id,
                    question=case.question,
                    generated_sql="",
                    passed=False,
                    reason=f"pipeline_error: {e}",
                    expected_rows=case.expected_rows,
                    actual_rows=[],
                    latency_ms=0.0,
                    difficulty=case.difficulty,
                    tags=case.tags,
                )
            )
            continue
        results.append(score_case(case, sql, conn))
    return EvalReport(results=results)


# ----- pretty printer ---------------------------------------------------------


def format_report(report: EvalReport, *, verbose: bool = False) -> str:
    """Human-readable summary of an eval report."""
    lines = []
    lines.append(f"Pass rate: {report.passed}/{report.total} ({report.pass_rate:.1%})")
    lines.append("")
    lines.append("By difficulty:")
    for diff, (passed, total) in sorted(report.by_difficulty().items()):
        lines.append(f"  {diff:8s} {passed}/{total}")

    failures = [r for r in report.results if not r.passed]
    if failures:
        lines.append("")
        lines.append(f"Failures ({len(failures)}):")
        for r in failures:
            lines.append(f"  [{r.case_id}] {r.question}")
            lines.append(f"    reason: {r.reason}")
            if verbose:
                lines.append(f"    sql:      {r.generated_sql}")
                lines.append(f"    expected: {r.expected_rows}")
                lines.append(f"    actual:   {r.actual_rows}")
    return "\n".join(lines)
