"""Tests for KeywordSchemaRetriever."""

from __future__ import annotations

import pytest

from analytics_copilot.schema.retrieval import KeywordSchemaRetriever


class TestRetriever:
    def test_question_about_customers_ranks_customers_first(self) -> None:
        r = KeywordSchemaRetriever()
        result = r.retrieve("how many customers do we have", top_k=2)
        assert result.tables[0].name == "customers"

    def test_question_about_orders_ranks_orders_first(self) -> None:
        r = KeywordSchemaRetriever()
        result = r.retrieve("show me orders from last month", top_k=2)
        assert result.tables[0].name == "orders"

    def test_example_value_matches(self) -> None:
        """A question that names a country should rank customers via example-value match."""
        r = KeywordSchemaRetriever()
        result = r.retrieve("customers in Nigeria", top_k=2)
        assert result.tables[0].name == "customers"
        assert result.scores[0] > 0

    def test_top_k_caps_result_size(self) -> None:
        r = KeywordSchemaRetriever()
        result = r.retrieve("revenue", top_k=2)
        assert len(result.tables) == 2

    def test_empty_question_raises(self) -> None:
        r = KeywordSchemaRetriever()
        with pytest.raises(ValueError):
            r.retrieve("")

    def test_zero_top_k_raises(self) -> None:
        r = KeywordSchemaRetriever()
        with pytest.raises(ValueError):
            r.retrieve("customers", top_k=0)

    def test_returns_something_even_for_unrelated_question(self) -> None:
        """Avoids empty schema context that would break downstream LLM calls."""
        r = KeywordSchemaRetriever()
        result = r.retrieve("the weather in Singapore", top_k=2)
        assert len(result.tables) == 2
        # All scores will be 0, but we still get tables
        assert all(s == 0 for s in result.scores)

    def test_to_llm_text_includes_tables(self) -> None:
        r = KeywordSchemaRetriever()
        result = r.retrieve("orders by customer", top_k=2)
        text = result.to_llm_text()
        assert "TABLE" in text
        assert "Columns:" in text