"""
viz.py

Pick a sensible chart for a query result and render it to a PNG.

Chart selection heuristics (deliberately simple and predictable):
  - date/time column + numeric column(s)  -> line chart
  - categorical column + numeric column   -> bar chart
  - a single value (1 row x 1 numeric)    -> no chart
  - anything else                         -> no chart

matplotlib is imported lazily and the non-interactive Agg backend is used, so
this works headless (CI, servers).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

ChartKind = Literal["line", "bar"]

MAX_BARS = 25


@dataclass(frozen=True, slots=True)
class ChartSpec:
    """What to plot: chart type, x column index, and numeric y column indexes."""

    kind: ChartKind
    x_index: int
    y_indexes: tuple[int, ...]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _is_temporal(value: Any) -> bool:
    return isinstance(value, (date, datetime))


def _column_kind(values: Sequence[Any]) -> str:
    """Classify a column as 'temporal', 'numeric', 'categorical' or 'empty'."""
    present = [v for v in values if v is not None]
    if not present:
        return "empty"
    if all(_is_temporal(v) for v in present):
        return "temporal"
    if all(_is_number(v) for v in present):
        return "numeric"
    return "categorical"


def choose_chart(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> ChartSpec | None:
    """Decide which chart (if any) suits the result. Returns None for no chart."""
    if not columns or not rows:
        return None
    if len(rows) == 1 and len(columns) == 1:
        return None

    kinds = [_column_kind([r[i] for r in rows]) for i in range(len(columns))]
    numeric = tuple(i for i, k in enumerate(kinds) if k == "numeric")
    if not numeric:
        return None

    temporal = [i for i, k in enumerate(kinds) if k == "temporal"]
    if temporal and len(rows) >= 2:
        return ChartSpec("line", temporal[0], numeric)

    categorical = [i for i, k in enumerate(kinds) if k == "categorical"]
    if categorical:
        return ChartSpec("bar", categorical[0], numeric[:1])
    return None


def render_chart(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    path: str | Path,
    *,
    title: str | None = None,
) -> Path | None:
    """Render the best-fit chart to `path` (PNG). Returns the path, or None if no chart fits."""
    spec = choose_chart(columns, rows)
    if spec is None:
        return None

    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    data = list(rows)
    if spec.kind == "line":
        data.sort(key=lambda r: (r[spec.x_index] is None, r[spec.x_index]))
    else:
        data = data[:MAX_BARS]

    x_values = [r[spec.x_index] for r in data]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    try:
        for y_index in spec.y_indexes:
            y_values = [float(r[y_index]) if r[y_index] is not None else float("nan") for r in data]
            if spec.kind == "line":
                ax.plot(x_values, y_values, marker="o", label=columns[y_index])
            else:
                ax.bar([str(x) for x in x_values], y_values, label=columns[y_index])
        ax.set_xlabel(columns[spec.x_index])
        ax.set_ylabel(columns[spec.y_indexes[0]] if len(spec.y_indexes) == 1 else "value")
        if len(spec.y_indexes) > 1:
            ax.legend()
        if title:
            ax.set_title(title if len(title) <= 80 else title[:77] + "...")
        ax.grid(axis="y", alpha=0.3)
        fig.autofmt_xdate(rotation=30)
        fig.tight_layout()

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, format="png", dpi=150)
    finally:
        plt.close(fig)
    return out
