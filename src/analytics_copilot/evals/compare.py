"""
evals/compare.py

Execution-accuracy comparison: do two result sets contain the same data?

Rules:
  - Row order is ignored (results are compared as multisets of rows).
  - Column names are ignored; columns are compared positionally.
  - Numbers (int / float / Decimal) are compared with a tolerance.
  - Dates and timestamps compare by ISO string; strings compare after strip().
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _normalize(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if _is_number(value):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, str):
        return value.strip()
    return str(value)


def _sort_key(row: tuple[Any, ...]) -> tuple[Any, ...]:
    """Type-tagged key so mixed/None cells sort without TypeError; floats rounded."""
    key: list[tuple[int, Any]] = []
    for cell in row:
        if cell is None:
            key.append((0, 0))
        elif isinstance(cell, bool):
            key.append((1, int(cell)))
        elif isinstance(cell, float):
            key.append((2, round(cell, 2)))
        else:
            key.append((3, str(cell)))
    return tuple(key)


def _cells_equal(a: Any, b: Any, rel_tol: float, abs_tol: float) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol)
    return bool(a == b)


def results_match(
    expected: Sequence[Sequence[Any]],
    actual: Sequence[Sequence[Any]],
    *,
    rel_tol: float = 1e-6,
    abs_tol: float = 0.01,
) -> bool:
    """True when `actual` has the same rows as `expected`, ignoring row order."""
    if len(expected) != len(actual):
        return False
    exp_rows = sorted((tuple(_normalize(c) for c in r) for r in expected), key=_sort_key)
    act_rows = sorted((tuple(_normalize(c) for c in r) for r in actual), key=_sort_key)
    for e_row, a_row in zip(exp_rows, act_rows):
        if len(e_row) != len(a_row):
            return False
        if not all(_cells_equal(e, a, rel_tol, abs_tol) for e, a in zip(e_row, a_row)):
            return False
    return True
