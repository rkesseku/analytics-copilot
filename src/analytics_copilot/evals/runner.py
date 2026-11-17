"""
evals/runner.py

Regression harness: run the copilot over the held-out query bank and score it.

Per question we record:
  (a) execution accuracy -- does the generated SQL return the same result set
      as the gold SQL (order-insensitive, tolerant numeric compare)?
  (b) judge score -- an LLM grades the written commentary against the result
      (faithfulness / relevance / clarity, normalized to 0..1).

The process exits non-zero when execution accuracy is below a configurable
threshold, so it can gate CI. Reports are written as JSON and Markdown.

Usage:
    python -m analytics_copilot.evals --min-accuracy 0.8
    analytics-copilot-eval --bank evals/query_bank.yaml --out-dir evals/reports
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from analytics_copilot.db.backend import Backend
from analytics_copilot.evals.bank import DEFAULT_BANK_PATH, BankQuestion, QueryBank, load_bank
from analytics_copilot.evals.compare import results_match
from analytics_copilot.evals.judge import CommentaryJudge
from analytics_copilot.llm.client import LLMClient

DEFAULT_MIN_ACCURACY = 0.8
DEFAULT_OUT_DIR = Path("evals") / "reports"


class CopilotLike(Protocol):
    """The slice of AnalyticsCopilot the harness needs."""

    def ask(self, question: str, *, today: date | None = None) -> Any: ...


@dataclass(slots=True)
class CaseResult:
    """Outcome for one bank question."""

    id: str
    category: str
    question: str
    gold_sql: str
    generated_sql: str | None = None
    execution_ok: bool = False  # pipeline produced a result without raising
    execution_correct: bool = False  # result set matches gold
    error: str | None = None
    commentary: str | None = None
    judge_score: float | None = None
    judge_reasoning: str | None = None
    judge_error: str | None = None


@dataclass(slots=True)
class EvalReport:
    """Aggregate report for a harness run."""

    bank_version: int
    backend: str
    generated_at: str
    total: int
    correct: int
    execution_accuracy: float
    judged: int
    mean_judge_score: float | None
    min_accuracy: float
    min_judge_score: float | None
    passed: bool
    cases: list[CaseResult] = field(default_factory=list)


def run_case(
    item: BankQuestion,
    *,
    copilot: CopilotLike,
    gold_db: Backend,
    judge: CommentaryJudge | None,
    today: date,
) -> CaseResult:
    """Run one question through the pipeline and score it."""
    case = CaseResult(
        id=item.id, category=item.category, question=item.question, gold_sql=item.gold_sql
    )
    try:
        gold = gold_db.query(item.gold_sql)
    except Exception as exc:  # noqa: BLE001 - a broken gold query is a bank bug; surface it clearly
        case.error = f"gold SQL failed: {type(exc).__name__}: {exc}"
        return case

    try:
        answer = copilot.ask(item.question, today=today)
    except Exception as exc:  # noqa: BLE001 - any pipeline failure counts as a miss, not a crash
        case.error = f"{type(exc).__name__}: {exc}"
        return case

    execution = answer.execution
    case.execution_ok = True
    case.generated_sql = execution.executed_sql
    case.commentary = answer.summary
    case.execution_correct = results_match(gold.rows, execution.result.rows)

    if judge is not None:
        try:
            verdict = judge.judge(
                question=item.question,
                sql=execution.executed_sql,
                columns=execution.result.columns,
                rows=execution.result.rows,
                commentary=answer.summary,
            )
            case.judge_score = verdict.score
            case.judge_reasoning = verdict.reasoning
        except Exception as exc:  # noqa: BLE001 - judge failure must not abort the run
            case.judge_error = f"{type(exc).__name__}: {exc}"
    return case


def run_eval(
    bank: QueryBank,
    *,
    copilot: CopilotLike,
    gold_db: Backend,
    judge: CommentaryJudge | None = None,
    min_accuracy: float = DEFAULT_MIN_ACCURACY,
    min_judge_score: float | None = None,
    backend_name: str = "duckdb",
    limit: int | None = None,
) -> EvalReport:
    """Run every question in `bank` and aggregate an `EvalReport`."""
    items = bank.questions[:limit] if limit else bank.questions
    cases = [
        run_case(q, copilot=copilot, gold_db=gold_db, judge=judge, today=bank.anchor_date)
        for q in items
    ]
    total = len(cases)
    correct = sum(c.execution_correct for c in cases)
    accuracy = correct / total if total else 0.0
    scores = [c.judge_score for c in cases if c.judge_score is not None]
    mean_judge = sum(scores) / len(scores) if scores else None

    passed = accuracy >= min_accuracy
    if min_judge_score is not None:
        passed = passed and mean_judge is not None and mean_judge >= min_judge_score

    return EvalReport(
        bank_version=bank.version,
        backend=backend_name,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        total=total,
        correct=correct,
        execution_accuracy=accuracy,
        judged=len(scores),
        mean_judge_score=mean_judge,
        min_accuracy=min_accuracy,
        min_judge_score=min_judge_score,
        passed=passed,
        cases=cases,
    )


def to_markdown(report: EvalReport) -> str:
    """Render the report as a Markdown document."""
    judge = "n/a" if report.mean_judge_score is None else f"{report.mean_judge_score:.2f}"
    lines = [
        "# Eval report",
        "",
        f"- Query bank version: {report.bank_version}",
        f"- Backend: {report.backend}",
        f"- Generated: {report.generated_at}",
        (
            f"- Execution accuracy: {report.correct}/{report.total} "
            f"({report.execution_accuracy:.1%}), threshold {report.min_accuracy:.1%}"
        ),
        f"- Mean judge score (0-1): {judge} over {report.judged} judged answers",
        f"- Result: {'PASS' if report.passed else 'FAIL'}",
        "",
        "| id | category | executed | correct | judge | note |",
        "|----|----------|----------|---------|-------|------|",
    ]
    for c in report.cases:
        score = "-" if c.judge_score is None else f"{c.judge_score:.2f}"
        note = (c.error or c.judge_error or "").replace("|", "\\|").replace("\n", " ")[:120]
        lines.append(
            f"| {c.id} | {c.category} | {'yes' if c.execution_ok else 'no'} "
            f"| {'yes' if c.execution_correct else 'no'} | {score} | {note} |"
        )
    return "\n".join(lines) + "\n"


def write_reports(report: EvalReport, out_dir: str | Path) -> tuple[Path, Path]:
    """Write `report.json` and `report.md` into `out_dir`."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "report.json"
    md_path = out / "report.md"
    json_path.write_text(json.dumps(asdict(report), indent=2, default=str), encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analytics-copilot-eval",
        description="Run the held-out query bank and score execution accuracy + commentary.",
    )
    parser.add_argument("--bank", type=Path, default=DEFAULT_BANK_PATH, help="Query bank YAML.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Report directory.")
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=_env_float("EVAL_MIN_ACCURACY", DEFAULT_MIN_ACCURACY),
        help="Minimum execution accuracy (0-1); exit 1 below it. Env: EVAL_MIN_ACCURACY.",
    )
    parser.add_argument(
        "--min-judge-score",
        type=float,
        default=None,
        help="Optional minimum mean judge score (0-1).",
    )
    parser.add_argument("--no-judge", action="store_true", help="Skip the LLM judge.")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N questions.")
    parser.add_argument("--backend", default=None, help="duckdb (default) or databricks.")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    llm_client: LLMClient | None = None,
    judge_client: LLMClient | None = None,
    copilot: CopilotLike | None = None,
    gold_db: Backend | None = None,
) -> int:
    """CLI entry point. Collaborators are injectable so tests can pass stubs."""
    args = build_parser().parse_args(argv)

    try:
        bank = load_bank(args.bank)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    backend_name = args.backend or "duckdb"
    try:
        if copilot is None or gold_db is None:
            from analytics_copilot.db.database import Database
            from analytics_copilot.db.factory import create_backend, resolve_backend_name
            from analytics_copilot.db.schema import create_and_seed
            from analytics_copilot.llm.client import GroqClient
            from analytics_copilot.pipeline import AnalyticsCopilot

            backend_name = resolve_backend_name(args.backend)
            llm_client = llm_client or GroqClient()
            gold_db = create_backend(backend_name)
            gold_db.connect()
            if isinstance(gold_db, Database):
                create_and_seed(gold_db.connection, seed=42, today=bank.anchor_date)
            copilot = AnalyticsCopilot(database=gold_db, llm_client=llm_client)
        judge = None
        if not args.no_judge:
            judge_llm = judge_client or llm_client
            if judge_llm is None:
                from analytics_copilot.llm.client import GroqClient

                judge_llm = GroqClient()
            judge = CommentaryJudge(judge_llm)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    report = run_eval(
        bank,
        copilot=copilot,
        gold_db=gold_db,
        judge=judge,
        min_accuracy=args.min_accuracy,
        min_judge_score=args.min_judge_score,
        backend_name=backend_name,
        limit=args.limit,
    )
    json_path, md_path = write_reports(report, args.out_dir)
    print(
        f"Execution accuracy: {report.correct}/{report.total} ({report.execution_accuracy:.1%}); "
        f"threshold {report.min_accuracy:.1%}; {'PASS' if report.passed else 'FAIL'}"
    )
    print(f"Reports: {json_path}, {md_path}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
