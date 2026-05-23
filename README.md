# Analytics Copilot

[![CI](https://github.com/rkesseku/analytics-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/rkesseku/analytics-copilot/actions/workflows/ci.yml)

A conversational analytics copilot. Ask business questions in plain English; get back governed SQL, executed results, and a written summary.

## Why this exists

Most organizations have far more data questions than data analysts. "Text-to-SQL" tools promise to close that gap, but a naive LLM-to-database pipeline is a security and correctness disaster. This project demonstrates the **architecture that makes text-to-SQL safe and trustworthy in production**:

- An LLM proposes SQL using retrieved schema context.
- A **separate validation layer** rejects anything that isn't a safe SELECT against an allow-listed set of tables.
- The validated SQL is executed against the database.
- A second LLM call turns results into an executive-friendly summary, with caveats.

The LLM never touches the database directly. The validator is the trusted boundary.

## Demo

```bash
$ python -m analytics_copilot.cli "What are the top 3 product categories by revenue for delivered orders?" --today 2026-05-22

==============================================================================
Question: What are the top 3 product categories by revenue for delivered orders?
==============================================================================

Generated SQL
------------------------------------------------------------------------------
SELECT p.category, SUM(oi.quantity * oi.unit_price) AS revenue
FROM order_items oi
JOIN orders o ON oi.order_id = o.order_id
JOIN products p ON oi.product_id = p.product_id
WHERE o.status = 'delivered'
GROUP BY p.category
ORDER BY revenue DESC
LIMIT 3

  Tables used: products, orders, order_items, customers
  Confidence:  0.99
  Rationale:   Join order_items with orders and products to calculate revenue by category.

Result
------------------------------------------------------------------------------
category    | revenue
------------+--------
Home Goods  | 23,305.03
Outdoor     | 10,987.50
Electronics | 10,024.32

  3 row(s).

Summary
------------------------------------------------------------------------------
Home Goods led delivered-order revenue at $23.3K, followed by Outdoor ($11K)
and Electronics ($10K). Note: these figures reflect delivered orders only and
exclude pending or cancelled orders.
```

## What's implemented today

- ✅ **Hand-curated schema metadata** with business-meaning descriptions and example values, formatted for LLM consumption
- ✅ **Keyword-based schema RAG** behind a `SchemaRetriever` protocol (embedding-based retrievers can be swapped in without changes)
- ✅ **LLM text-to-SQL generator** with structured (Pydantic) output and confidence scoring
- ✅ **Three-layer SQL safety validator**: parse → statement-type → table allow-list, with CTE awareness and auto LIMIT injection
- ✅ **DuckDB execution layer** with context-manager-safe connection lifecycle
- ✅ **Result summarizer** producing executive-friendly prose with caveats
- ✅ **CLI** with single-question and interactive modes
- ✅ **52 unit tests** covering every layer, including end-to-end pipeline tests using fake LLM clients (zero network in CI)

## Roadmap

- [ ] Embedding-based schema retriever for large schemas
- [ ] Self-correction loop: if SQL fails to execute, feed error back to the LLM
- [ ] Eval integration with [llm-eval-harness](https://github.com/rkesseku/llm-eval-harness) for SQL correctness scoring
- [ ] Streamlit UI for non-CLI demos
- [ ] Additional LLM providers (OpenAI, Anthropic, Ollama)
- [ ] Multi-database support (Postgres, Snowflake, BigQuery)

## Quick start

```bash
# Clone and set up
git clone git@github.com:rkesseku/analytics-copilot.git
cd analytics-copilot
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# source .venv/bin/activate      # Mac / Linux

# Install (with dev tools: pytest, ruff, mypy)
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env and add your GROQ_API_KEY (free at https://console.groq.com)

# Run the demo
python -m analytics_copilot.cli "What are the top 3 product categories by revenue?"

# Or interactively
python -m analytics_copilot.cli --interactive

# Run tests
pytest -v
```

## Architecture

```
src/analytics_copilot/
├── schema/
│   ├── metadata.py        # Hand-curated table & column metadata
│   └── retrieval.py       # SchemaRetriever protocol + KeywordSchemaRetriever
├── llm/
│   ├── client.py          # LLMClient protocol (Groq + fake-for-tests)
│   ├── text_to_sql.py     # Structured-output SQL generation
│   └── summarizer.py      # Result summarization in prose
├── sql/
│   ├── validator.py       # Three-layer SQL safety check
│   └── executor.py        # Validator + database executor
├── db/
│   ├── database.py        # DuckDB connection wrapper
│   └── schema.py          # Seed-data generator
├── pipeline.py            # AnalyticsCopilot facade (ties it all together)
└── cli.py                 # Command-line entry point

tests/                     # 52 unit tests, no network calls in CI
```

### Design notes

- **Trusted boundary at the validator.** The LLM never executes SQL directly. Every SQL string passes through `sql.validator` first — parse with sqlglot, reject non-SELECT, enforce table allow-list, inject row limit. Even prompt injection that successfully manipulates the LLM can't reach the database.
- **`Protocol` everywhere.** `LLMClient`, `SchemaRetriever`, and `Database` are structurally typed. Tests inject fake implementations and run with zero network calls in well under a second per test.
- **Structured LLM outputs.** Both the SQL generator and the summarizer are constrained to JSON/prose with explicit shape. Pydantic enforces the contract; the parser is defensive against markdown fences.
- **Hand-written schema metadata is intentional.** Auto-introspected schemas miss the business semantics (`unit_price` is "price at time of order, not current") that make text-to-SQL actually work. A production system would pull from a data catalog with these same fields.

## License

MIT# README additions

Append the following sections to your existing `README.md` (or merge into the
corresponding sections if they already exist).

---

## Governance

Every LLM-generated SQL query passes through `app/governance/enforce()` before
hitting the database. The default policy:

- **Read-only.** Only `SELECT` (and `WITH ... SELECT`) statements are allowed.
  Any `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `PRAGMA`, etc. raises
  `GovernanceError`.
- **Single statement.** Multi-statement payloads (`SELECT ...; DROP TABLE ...`)
  are rejected.
- **Table allowlist.** Queries may only reference `customers`, `products`,
  `orders`, `order_items`.
- **PII column block.** Any reference to `email` (in `SELECT`, `WHERE`,
  anywhere) is rejected.
- **Row cap.** Results are capped at 1000 rows. A missing `LIMIT` clause is
  injected; an existing `LIMIT` higher than the cap is lowered.

Tune the policy in `app/governance/policy.py`. Add new guardrails by
extending `enforce()` and the corresponding test cases.

## Eval harness

Reproducible, executable evals over a SQLite fixture. Each case in
`evals/golden_sets/shop.yaml` defines a natural-language question and the
result rows the answer must return.

```bash
# Smoke test the harness (no LLM calls)
python -m evals.run --dry-run

# Live eval against Groq
export GROQ_API_KEY=...
python -m evals.run --verbose

# Gate CI on a minimum pass rate
python -m evals.run --min-pass-rate 0.8
```

The scorer compares **result rows**, not SQL strings — semantically equivalent
queries (different aliases, JOIN order, subqueries vs CTEs) all count as
correct as long as they return the same data.

Add new cases by appending to `shop.yaml`. Keep `id` values stable so
historical eval runs stay comparable across PRs.

## UI

```bash
streamlit run app/ui/streamlit_app.py
```

Type a question; see the generated SQL, the rows, and a plain-English summary.
Governance errors surface as visible blocks rather than silent failures.
# README additions

Append the following sections to your existing `README.md` (or merge into the
corresponding sections if they already exist).

---

## Eval harness

Reproducible, executable evals over the seeded DuckDB fixture. Each case in
`evals/golden_sets/shop.yaml` defines a natural-language question and the
result rows the answer must return.

The golden set is **generated programmatically** from the actual seed data —
don't hand-edit `expected_rows`. To add or change questions, edit
`evals/build_golden_set.py` and run:

```bash
python -m evals.build_golden_set
```

Then run the eval suite:

```bash
# Smoke test the harness (no LLM calls — uses reference SQL)
python -m evals.run --dry-run

# Live eval against Groq
export GROQ_API_KEY=...
python -m evals.run --verbose

# Gate CI on a minimum pass rate
python -m evals.run --min-pass-rate 0.8
```

The scorer compares **result rows**, not SQL strings — semantically equivalent
queries (different aliases, JOIN order, subqueries vs CTEs) all count as
correct as long as they return the same data.

Add new cases by editing `evals/build_golden_set.py`. Keep `id` values stable
so historical eval runs stay comparable across PRs.

## UI

```bash
streamlit run src/analytics_copilot/ui/streamlit_app.py
```

Type a question; see the validated SQL, the rows, and a plain-English summary.
Validation errors (mutations, disallowed tables, etc.) surface as visible
blocks rather than silent failures.

## Adding new dependencies

The eval harness and UI add two runtime dependencies:

- `pyyaml` — for golden-set serialization
- `streamlit` — for the UI

Add these to `[project] dependencies` in `pyproject.toml`, or install them
explicitly with `pip install pyyaml streamlit`.
