"""Streamlit UI for the analytics copilot.

Run:
    streamlit run src/analytics_copilot/ui/streamlit_app.py

What this surfaces:
    - The natural-language question
    - The validated SQL that actually ran (with a note when the validator
      added or rewrote a row limit)
    - The returned rows (as a dataframe)
    - The plain-English summary

The pipeline is built once via build_demo_copilot() and cached for the session
so we don't reseed on every interaction. In production, swap the demo factory
for a real DB-connected copilot.
"""

from __future__ import annotations

import streamlit as st  # type: ignore

from analytics_copilot.pipeline import build_demo_copilot
from analytics_copilot.sql.validator import SQLValidationError


SAMPLE_QUESTIONS = [
    "How many customers do we have?",
    "What is the total revenue from delivered orders?",
    "Which country has the most customers?",
    "How much revenue did each product category generate from delivered orders?",
    "Who are the top 3 customers by total spend on delivered orders?",
]


@st.cache_resource
def get_copilot():
    """Build the copilot once per Streamlit session. Reseeding is expensive."""
    return build_demo_copilot()


def main() -> None:
    st.set_page_config(page_title="Analytics Copilot", layout="wide")
    st.title("Analytics Copilot")
    st.caption(
        "Ask a business question in plain English. The copilot generates SQL, "
        "validates it against the safety layer, executes it, and writes a summary."
    )

    # --- Sidebar: example questions + safety summary -------------------------
    with st.sidebar:
        st.header("Try a sample")
        for q in SAMPLE_QUESTIONS:
            if st.button(q, use_container_width=True):
                st.session_state["question"] = q

        st.divider()
        st.header("Safety layer")
        st.markdown(
            "- Only `SELECT` allowed (sqlglot-parsed)\n"
            "- No mutations even in subqueries\n"
            "- Table allowlist enforced\n"
            "- Default row limit injected if missing"
        )

    # --- Main: question input + run -----------------------------------------
    question = st.text_input(
        "Your question",
        value=st.session_state.get("question", ""),
        placeholder="e.g. What was last month's revenue by category?",
    )

    if not question:
        st.info("Enter a question above or pick a sample from the sidebar.")
        return

    if not st.button("Ask", type="primary"):
        return

    copilot = get_copilot()
    with st.spinner("Generating SQL, validating, running query, summarizing…"):
        try:
            answer = copilot.ask(question)
        except SQLValidationError as e:
            st.error(f"Query blocked by the safety layer: {e}")
            st.caption(
                "The generated SQL did not pass validation and was not executed. "
                "Try rephrasing your question."
            )
            return
        except ValueError as e:
            st.error(str(e))
            return
        except Exception as e:  # noqa: BLE001 — surface any pipeline error
            st.error(f"Pipeline error: {e}")
            return

    # --- Result panels ------------------------------------------------------
    st.subheader("Answer")
    st.write(answer.summary)

    # The validator sets row_limit_added on its ValidationResult; depending on
    # how the executor exposes it, it may live on execution or on a nested
    # validation attribute. Be tolerant about where we find it.
    row_limit_added = getattr(answer.execution, "row_limit_added", False) or getattr(
        getattr(answer.execution, "validation", None), "row_limit_added", False
    )
    if row_limit_added:
        st.warning("A row limit was added to the query for safety.")

    col1, col2 = st.columns([2, 3])
    with col1:
        st.subheader("Generated SQL")
        st.code(answer.execution.executed_sql, language="sql")
    with col2:
        st.subheader("Rows")
        if answer.execution.result.rows:
            # st.dataframe accepts a list of lists with columns separately
            st.dataframe(
                answer.execution.result.rows,
                use_container_width=True,
            )
            st.caption(
                f"{answer.execution.result.row_count} row(s) · "
                f"columns: {', '.join(answer.execution.result.columns)}"
            )
        else:
            st.caption("No rows returned.")


if __name__ == "__main__":
    main()
