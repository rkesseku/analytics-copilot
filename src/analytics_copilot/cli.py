"""
cli.py

Command-line entry point. Lets the user ask the analytics copilot a question
and prints SQL, results, and a natural-language summary.

Usage:
    python -m analytics_copilot.cli "What are the top 3 product categories by revenue?"
    python -m analytics_copilot.cli --interactive
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any

from analytics_copilot.pipeline import AnalyticsCopilot, AnswerResult, build_demo_copilot
from analytics_copilot.sql.validator import SQLValidationError


def _format_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _print_result_table(columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    """Render a query result as an aligned text table."""
    if not rows:
        print("(no rows)")
        return

    # Pre-format every cell as a string
    str_rows = [tuple(_format_value(c) for c in row) for row in rows]

    # Compute column widths
    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: tuple[str, ...]) -> str:
        return " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    sep = "-+-".join("-" * w for w in widths)
    print(fmt_row(tuple(columns)))
    print(sep)
    for row in str_rows:
        print(fmt_row(row))


def _print_answer(answer: AnswerResult) -> None:
    print("=" * 78)
    print(f"Question: {answer.question}")
    print("=" * 78)

    print("\nGenerated SQL")
    print("-" * 78)
    print(answer.generation.sql)
    print(
        f"\n  Tables used: {', '.join(answer.generation.schema_tables_used)}"
    )
    print(f"  Confidence:  {answer.generation.confidence:.2f}")
    print(f"  Rationale:   {answer.generation.rationale}")

    if answer.execution.row_limit_added:
        print(f"\n  (Note: a row LIMIT was injected by the safety validator.)")

    print("\nResult")
    print("-" * 78)
    _print_result_table(
        answer.execution.result.columns,
        answer.execution.result.rows,
    )
    print(f"\n  {answer.execution.result.row_count} row(s).")

    print("\nSummary")
    print("-" * 78)
    print(answer.summary)
    print()


def _run_one(copilot: AnalyticsCopilot, question: str, today: date | None) -> int:
    try:
        answer = copilot.ask(question, today=today)
    except SQLValidationError as exc:
        print(f"ERROR: generated SQL failed safety validation:\n  {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # surfaced cleanly to CLI users
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    _print_answer(answer)
    return 0


def _run_interactive(copilot: AnalyticsCopilot, today: date | None) -> int:
    print("Analytics Copilot — interactive mode (type 'quit' to exit).\n")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()  # newline before exit
            return 0
        if not question:
            continue
        if question.lower() in {"quit", "exit"}:
            return 0
        _run_one(copilot, question, today)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="analytics-copilot",
        description="Ask a business question; get SQL, results, and a summary.",
    )
    parser.add_argument(
        "question",
        nargs="?",
        help="The natural-language question to ask. Omit with --interactive.",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Drop into an interactive prompt instead of a single question.",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=None,
        help="Anchor 'today' to a specific date (YYYY-MM-DD), useful for reproducible demos.",
    )
    args = parser.parse_args(argv)

    if not args.interactive and not args.question:
        parser.error("Provide a question or use --interactive.")

    copilot = build_demo_copilot(today=args.today)

    if args.interactive:
        return _run_interactive(copilot, args.today)
    return _run_one(copilot, args.question, args.today)


if __name__ == "__main__":
    raise SystemExit(main())