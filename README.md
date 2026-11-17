# Analytics Copilot

[![CI](https://github.com/rkesseku/analytics-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/rkesseku/analytics-copilot/actions/workflows/ci.yml)

A conversational analytics copilot. Ask business questions in plain English; get back governed SQL, executed results, an optional chart, and a written summary. Runs on DuckDB by default; a Databricks SQL Warehouse adapter is included, and a held-out query bank with an LLM-as-judge harness guards against regressions.

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
- ✅ **Pluggable SQL backends** behind a small `Backend` protocol (`db/backend.py`): DuckDB is the default; a **Databricks SQL Warehouse adapter** (`db/databricks.py`) is selectable via `--backend databricks` or `ANALYTICS_BACKEND`. The validator parses in the backend's dialect. The adapter uses the optional `databricks-sql-connector` extra, reads `DATABRICKS_*` env vars, and supports Unity Catalog `catalog.schema` schema introspection via `information_schema`. It is unit-tested against a fake connection; it has not been exercised against a live workspace in CI.
- ✅ **Charts**: `viz.py` picks a chart from the result shape (date + number -> line, category + number -> bar, single value -> none), renders a PNG with matplotlib (Agg), and is exposed as `AnswerResult.chart_path` and the CLI `--chart out.png` flag
- ✅ **Regression harness** (`analytics_copilot.evals`): a versioned held-out query bank (`evals/query_bank.yaml`, 25 questions with gold SQL against the demo schema). Each run scores (a) execution accuracy, comparing result sets against the gold SQL (order-insensitive, tolerant numeric compare) and (b) an LLM-as-judge rubric score for the written commentary (faithfulness / relevance / clarity, structured JSON parsed with pydantic). It writes JSON + Markdown reports and exits non-zero below a configurable accuracy threshold. A CI job runs it only when a `GROQ_API_KEY` secret is configured. Harness logic is tested with stub LLM clients.
- ✅ **Result summarizer** producing executive-friendly prose with caveats
- ✅ **CLI** with single-question and interactive modes
- ✅ **Unit tests** covering every layer, including end-to-end pipeline tests using fake LLM clients (zero network in CI)

## Roadmap

- [ ] Embedding-based schema retriever for large schemas
- [ ] Self-correction loop: if SQL fails to execute, feed error back to the LLM
- [x] Eval harness: held-out query bank + execution accuracy + LLM-as-judge commentary scoring
- [x] Chart output (PNG) chosen from the result shape
- [x] Databricks SQL Warehouse backend via adapter (tested with a fake connection; live-workspace validation still to do)
- [ ] Publish a baseline eval report once a run has been recorded
- [ ] Streamlit UI for non-CLI demos
- [ ] Additional LLM providers (OpenAI, Anthropic, Ollama)
- [ ] More database backends (Postgres, Snowflake, BigQuery) behind the same `Backend` protocol

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

# Charts: save a PNG when the result shape supports one
python -m analytics_copilot.cli "Monthly revenue in 2025" --chart revenue.png

# Databricks SQL Warehouse (optional extra; set DATABRICKS_* vars in .env)
pip install -e ".[databricks]"
python -m analytics_copilot.cli "Top 3 categories by revenue" --backend databricks

# Regression harness over the held-out query bank (needs GROQ_API_KEY)
python -m analytics_copilot.evals --min-accuracy 0.8   # reports in evals/reports/
python -m analytics_copilot.evals --no-judge --limit 5  # quick run, no judge calls

# Run tests
pytest -v
```

On Databricks the warehouse must already contain the allow-listed tables
(`customers`, `products`, `orders`, `order_items`); nothing is seeded there, and
the schema metadata the LLM sees is still the hand-curated demo metadata.

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
│   ├── backend.py         # Backend protocol + QueryResult
│   ├── database.py        # DuckDB backend (default)
│   ├── databricks.py      # Databricks SQL Warehouse backend (optional extra)
│   ├── factory.py         # Backend selection (--backend / ANALYTICS_BACKEND)
│   └── schema.py          # Seed-data generator
├── viz.py                 # Chart selection + matplotlib PNG rendering
├── evals/
│   ├── bank.py            # Query bank loader (pydantic-validated YAML)
│   ├── compare.py         # Order-insensitive, tolerant result-set comparison
│   ├── judge.py           # LLM-as-judge rubric + verdict parsing
│   └── runner.py          # Harness, JSON/Markdown reports, CLI
├── pipeline.py            # AnalyticsCopilot facade (ties it all together)
└── cli.py                 # Command-line entry point

evals/query_bank.yaml      # Versioned held-out questions + gold SQL

tests/                     # Unit tests, no network calls in CI
```

### Design notes

- **Trusted boundary at the validator.** The LLM never executes SQL directly. Every SQL string passes through `sql.validator` first — parse with sqlglot, reject non-SELECT, enforce table allow-list, inject row limit. Even prompt injection that successfully manipulates the LLM can't reach the database.
- **`Protocol` everywhere.** `LLMClient`, `SchemaRetriever`, and `Database` are structurally typed. Tests inject fake implementations and run with zero network calls in well under a second per test.
- **Structured LLM outputs.** Both the SQL generator and the summarizer are constrained to JSON/prose with explicit shape. Pydantic enforces the contract; the parser is defensive against markdown fences.
- **Hand-written schema metadata is intentional.** Auto-introspected schemas miss the business semantics (`unit_price` is "price at time of order, not current") that make text-to-SQL actually work. A production system would pull from a data catalog with these same fields.

## License

MIT