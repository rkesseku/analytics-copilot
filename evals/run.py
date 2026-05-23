"""Run the eval suite against the real text-to-SQL pipeline.

Usage:
    python -m evals.run                      # full eval, calls Groq
    python -m evals.run --verbose            # show failing SQL and rows
    python -m evals.run --dry-run            # use reference_sql, no LLM calls
    python -m evals.run --min-pass-rate 0.8  # exit non-zero below threshold

--dry-run lets CI prove the harness wiring on every PR without burning API
credits or requiring a GROQ_API_KEY secret.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from analytics_copilot.db.database import Database
from analytics_copilot.db.schema import create_and_seed
from evals.scorer import format_report, load_golden_set, run_eval

GOLDEN_PATH = Path(__file__).parent / "golden_sets" / "shop.yaml"

# Keep these constants in sync with evals/build_golden_set.py — same seed and
# date are what make expected_rows match across runs.
TODAY = date(2025, 6, 1)
SEED = 42
N_ORDERS = 120


def _reference_pipeline(question: str) -> str:
    """Dry-run pipeline: returns the golden set's reference SQL for the question.

    This is not 'cheating' — it's a smoke test that the harness end-to-end
    (load → execute → compare → report) works. The real pipeline gets
    swapped in when --dry-run is omitted.
    """
    cases = {c.question: c for c in load_golden_set(GOLDEN_PATH)}
    case = cases.get(question)
    if case is None or case.reference_sql is None:
        raise ValueError(f"No reference SQL for question: {question!r}")
    return case.reference_sql


def _live_pipeline_factory():
    """Build the live pipeline. Imported lazily so --dry-run doesn't need the LLM."""
    from analytics_copilot.pipeline import build_demo_copilot

    # We build the copilot once per run; ask() is called per case.
    # The copilot's internal DB is seeded with the SAME seed/today as the
    # golden set, so the LLM's queries run against identical data.
    copilot = build_demo_copilot(today=TODAY)

    def pipeline(question: str) -> str:
        # We only need the generated SQL for scoring — not execution, not
        # summarization. The scorer re-executes against its own connection
        # so it can apply timing and error capture consistently.
        result = copilot.ask(question)
        return result.generation.sql

    return pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the analytics-copilot eval suite")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use reference SQL instead of calling the LLM (for CI smoke tests)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show generated SQL and rows for failing cases",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=0.0,
        help="Exit non-zero if pass rate falls below this threshold (0.0–1.0)",
    )
    args = parser.parse_args(argv)

    # Set up the eval DB with the same seed/today that produced the golden set.
    # This is the connection the scorer executes generated SQL against.
    eval_db = Database()
    eval_db.connect()
    create_and_seed(eval_db.connection, n_orders=N_ORDERS, seed=SEED, today=TODAY)

    pipeline = _reference_pipeline if args.dry_run else _live_pipeline_factory()

    try:
        report = run_eval(pipeline, eval_db.connection, GOLDEN_PATH)
    finally:
        eval_db.close()

    print(format_report(report, verbose=args.verbose))

    if report.pass_rate < args.min_pass_rate:
        print(
            f"\nFAIL: pass rate {report.pass_rate:.1%} below threshold {args.min_pass_rate:.1%}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
