"""Build the golden eval set from the actual seeded database.

Why programmatic generation: the eval depends on the seeder producing
deterministic rows. Instead of hard-coding expected_rows and risking drift
when the seed or schema changes, we *run* reference queries against the real
seeded DB and serialize the result rows. The YAML this writes is then the
golden set committed to the repo.

Run this:
    - Once, after creating it, to produce evals/golden_sets/shop.yaml
    - Again whenever the schema or seed changes (and review the diff)

Usage:
    python -m evals.build_golden_set
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from analytics_copilot.db.database import Database
from analytics_copilot.db.schema import create_and_seed

GOLDEN_PATH = Path(__file__).parent / "golden_sets" / "shop.yaml"

# Fixed "today" so date-relative queries are reproducible across machines.
# Pick any stable past date that gives a meaningful 18-month window.
TODAY = date(2025, 6, 1)
SEED = 42
N_ORDERS = 120


# ----- candidate questions ---------------------------------------------------
#
# Each entry: a natural-language question + the reference SQL that answers it.
# The expected_rows field is filled in by running the reference SQL against
# the seeded DB — do NOT hand-write expected_rows for new entries.
#
# Tips when adding cases:
#   - Use lowercase table/column names (matches the schema).
#   - For top-N questions, set ordered: true so row order matters.
#   - Prefer aggregate questions (counts, sums, ranks) over questions that
#     return many rows — they're easier to verify by eye in CI logs.

CANDIDATES: list[dict[str, Any]] = [
    {
        "id": "q001",
        "question": "How many customers do we have?",
        "tags": ["count", "single-table"],
        "difficulty": "easy",
        "reference_sql": "SELECT COUNT(*) FROM customers;",
        "ordered": False,
    },
    {
        "id": "q002",
        "question": "How many orders are in the delivered status?",
        "tags": ["count", "filter"],
        "difficulty": "easy",
        "reference_sql": "SELECT COUNT(*) FROM orders WHERE status = 'delivered';",
        "ordered": False,
    },
    {
        "id": "q003",
        "question": "List all product categories.",
        "tags": ["distinct"],
        "difficulty": "easy",
        "reference_sql": "SELECT DISTINCT category FROM products ORDER BY category;",
        "ordered": True,
    },
    {
        "id": "q004",
        "question": "What is the total revenue from delivered orders?",
        "tags": ["aggregation", "join", "filter"],
        "difficulty": "medium",
        "reference_sql": """
            SELECT ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            WHERE o.status = 'delivered';
        """,
        "ordered": False,
    },
    {
        "id": "q005",
        "question": "Which country has the most customers?",
        "tags": ["group-by", "order-by", "limit"],
        "difficulty": "medium",
        "reference_sql": """
            SELECT country, COUNT(*) AS n
            FROM customers
            GROUP BY country
            ORDER BY n DESC, country ASC
            LIMIT 1;
        """,
        "ordered": True,
    },
    {
        "id": "q006",
        "question": "How much revenue did each product category generate from delivered orders?",
        "tags": ["group-by", "join", "aggregation", "filter"],
        "difficulty": "medium",
        "reference_sql": """
            SELECT p.category, ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue
            FROM order_items oi
            JOIN orders   o ON oi.order_id   = o.order_id
            JOIN products p ON oi.product_id = p.product_id
            WHERE o.status = 'delivered'
            GROUP BY p.category
            ORDER BY p.category;
        """,
        "ordered": True,
    },
    {
        "id": "q007",
        "question": "Who are the top 3 customers by total spend on delivered orders?",
        "tags": ["join", "group-by", "order-by", "limit"],
        "difficulty": "hard",
        "reference_sql": """
            SELECT c.name, ROUND(SUM(oi.quantity * oi.unit_price), 2) AS spend
            FROM customers c
            JOIN orders      o  ON c.customer_id = o.customer_id
            JOIN order_items oi ON o.order_id    = oi.order_id
            WHERE o.status = 'delivered'
            GROUP BY c.customer_id, c.name
            ORDER BY spend DESC, c.name ASC
            LIMIT 3;
        """,
        "ordered": True,
    },
    {
        "id": "q008",
        "question": "How many orders were cancelled?",
        "tags": ["count", "filter"],
        "difficulty": "easy",
        "reference_sql": "SELECT COUNT(*) FROM orders WHERE status = 'cancelled';",
        "ordered": False,
    },
    {
        "id": "q009",
        "question": "What is the average order value among delivered orders?",
        "tags": ["aggregation", "join", "subquery"],
        "difficulty": "hard",
        "reference_sql": """
            SELECT ROUND(AVG(order_total), 2) AS avg_order_value
            FROM (
                SELECT o.order_id, SUM(oi.quantity * oi.unit_price) AS order_total
                FROM orders o
                JOIN order_items oi ON o.order_id = oi.order_id
                WHERE o.status = 'delivered'
                GROUP BY o.order_id
            ) t;
        """,
        "ordered": False,
    },
    {
        "id": "q010",
        "question": "Which product category has the most items in the catalog?",
        "tags": ["group-by", "order-by", "limit"],
        "difficulty": "medium",
        "reference_sql": """
            SELECT category, COUNT(*) AS n
            FROM products
            GROUP BY category
            ORDER BY n DESC, category ASC
            LIMIT 1;
        """,
        "ordered": True,
    },
]


def _to_serializable(value: Any) -> Any:
    """Coerce DuckDB result types to YAML-friendly Python primitives."""
    # DuckDB returns Decimal for DECIMAL columns; convert to float for YAML.
    if hasattr(value, "__float__") and not isinstance(value, (int, float, bool)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)
    return value


def _normalize_rows(rows: list[tuple]) -> list[list]:
    return [[_to_serializable(v) for v in row] for row in rows]


def build() -> Path:
    """Generate the YAML golden set against a freshly seeded DB."""
    db = Database()
    db.connect()
    try:
        create_and_seed(db.connection, n_orders=N_ORDERS, seed=SEED, today=TODAY)
        entries = []
        for cand in CANDIDATES:
            sql = cand["reference_sql"].strip()
            rows = db.connection.execute(sql).fetchall()
            entries.append(
                {
                    "id": cand["id"],
                    "question": cand["question"],
                    "tags": cand["tags"],
                    "difficulty": cand["difficulty"],
                    "reference_sql": sql,
                    "expected_rows": _normalize_rows(rows),
                    "ordered": cand["ordered"],
                }
            )
    finally:
        db.close()

    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Golden eval set for the analytics copilot.\n"
        "#\n"
        "# AUTO-GENERATED by `python -m evals.build_golden_set`. Do not hand-edit\n"
        "# expected_rows — regenerate the file after changing seed data or schema.\n"
        "# Hand-edit questions/reference_sql freely, then regenerate.\n\n"
    )
    GOLDEN_PATH.write_text(header + yaml.safe_dump(entries, sort_keys=False))
    return GOLDEN_PATH


if __name__ == "__main__":
    out = build()
    print(f"Wrote {out}")
    with open(out) as f:
        for i, line in enumerate(f, 1):
            if i > 60:
                print("  ... (truncated)")
                break
            print(f"  {line.rstrip()}")
