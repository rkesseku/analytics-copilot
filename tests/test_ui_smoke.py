"""Smoke test for the Streamlit UI.

We can't easily render the UI in CI, but we can confirm:
  - The module imports without error (with streamlit stubbed)
  - The main() function exists
  - Sample questions are non-empty strings
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch


def test_streamlit_app_imports():
    """The UI module must import even when streamlit isn't installed."""
    # Stub streamlit + the pipeline so we don't actually seed a DB or hit Groq.
    with patch.dict(
        "sys.modules",
        {
            "streamlit": MagicMock(),
        },
    ):
        # Also stub build_demo_copilot at import time so loading the module
        # doesn't trigger DB setup.
        with patch(
            "analytics_copilot.pipeline.build_demo_copilot",
            return_value=MagicMock(),
        ):
            module = importlib.import_module("analytics_copilot.ui.streamlit_app")
            assert hasattr(module, "main")
            assert callable(module.main)
            assert isinstance(module.SAMPLE_QUESTIONS, list)
            assert all(isinstance(q, str) and q for q in module.SAMPLE_QUESTIONS)


def test_sample_questions_are_distinct():
    with patch.dict("sys.modules", {"streamlit": MagicMock()}):
        with patch(
            "analytics_copilot.pipeline.build_demo_copilot",
            return_value=MagicMock(),
        ):
            module = importlib.import_module("analytics_copilot.ui.streamlit_app")
            assert len(set(module.SAMPLE_QUESTIONS)) == len(module.SAMPLE_QUESTIONS)
