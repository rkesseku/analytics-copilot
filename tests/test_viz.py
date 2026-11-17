"""Tests for viz (chart selection and rendering)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from analytics_copilot.viz import choose_chart, render_chart

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class TestChooseChart:
    def test_single_number_no_chart(self) -> None:
        assert choose_chart(["total"], [(42,)]) is None

    def test_empty_result_no_chart(self) -> None:
        assert choose_chart(["a", "b"], []) is None

    def test_date_and_number_is_line(self) -> None:
        rows = [(date(2025, 1, 1), 10.0), (date(2025, 2, 1), 12.5)]
        spec = choose_chart(["month", "revenue"], rows)
        assert spec is not None
        assert spec.kind == "line"
        assert spec.x_index == 0
        assert spec.y_indexes == (1,)

    def test_category_and_number_is_bar(self) -> None:
        rows = [("Books", Decimal("10.50")), ("Outdoor", Decimal("20.00"))]
        spec = choose_chart(["category", "revenue"], rows)
        assert spec is not None
        assert spec.kind == "bar"
        assert spec.y_indexes == (1,)

    def test_text_only_no_chart(self) -> None:
        assert choose_chart(["name", "country"], [("a", "x"), ("b", "y")]) is None

    def test_booleans_are_not_numeric(self) -> None:
        assert choose_chart(["name", "flag"], [("a", True), ("b", False)]) is None

    def test_single_date_row_falls_back_to_no_chart(self) -> None:
        assert choose_chart(["d", "n"], [(date(2025, 1, 1), 3)]) is None


class TestRenderChart:
    def test_renders_png_for_bar(self, tmp_path: Path) -> None:
        out = render_chart(
            ["category", "revenue"], [("Books", 10.0), ("Outdoor", 20.0)], tmp_path / "bar.png"
        )
        assert out is not None
        assert out.read_bytes().startswith(PNG_MAGIC)

    def test_renders_png_for_line_and_creates_parent(self, tmp_path: Path) -> None:
        rows = [(date(2025, 2, 1), 5), (date(2025, 1, 1), 3), (date(2025, 3, 1), None)]
        out = render_chart(["month", "n"], rows, tmp_path / "sub" / "line.png", title="Orders")
        assert out is not None
        assert out.read_bytes().startswith(PNG_MAGIC)

    def test_returns_none_and_writes_nothing_without_chart(self, tmp_path: Path) -> None:
        target = tmp_path / "none.png"
        assert render_chart(["total"], [(7,)], target) is None
        assert not target.exists()

    def test_many_categories_are_capped(self, tmp_path: Path) -> None:
        rows = [(f"c{i}", float(i)) for i in range(40)]
        assert render_chart(["c", "v"], rows, tmp_path / "many.png") is not None
