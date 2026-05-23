"""
db/schema.py

Schema definition + seed-data generator for the analytics copilot's toy
e-commerce database.

Tables:
  - customers       (10 customers across 4 countries)
  - products        (15 products across 5 categories)
  - orders          (~120 orders over the last 18 months)
  - order_items     (1-4 items per order)

The schema is small enough to seed quickly, realistic enough to require joins,
groupings, and date filters when answering business questions.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

import duckdb


# ---------------------------------------------------------------------------
# DDL: schema definition
# ---------------------------------------------------------------------------

SCHEMA_DDL: list[str] = [
    """
    CREATE TABLE customers (
        customer_id   INTEGER       PRIMARY KEY,
        name          VARCHAR       NOT NULL,
        email         VARCHAR       NOT NULL UNIQUE,
        country       VARCHAR       NOT NULL,
        signup_date   DATE          NOT NULL
    );
    """,
    """
    CREATE TABLE products (
        product_id    INTEGER       PRIMARY KEY,
        name          VARCHAR       NOT NULL,
        category      VARCHAR       NOT NULL,
        unit_price    DECIMAL(10,2) NOT NULL,
        is_active     BOOLEAN       NOT NULL DEFAULT TRUE
    );
    """,
    """
    CREATE TABLE orders (
        order_id      INTEGER       PRIMARY KEY,
        customer_id   INTEGER       NOT NULL REFERENCES customers(customer_id),
        order_date    DATE          NOT NULL,
        status        VARCHAR       NOT NULL CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled'))
    );
    """,
    """
    CREATE TABLE order_items (
        order_item_id INTEGER       PRIMARY KEY,
        order_id      INTEGER       NOT NULL REFERENCES orders(order_id),
        product_id    INTEGER       NOT NULL REFERENCES products(product_id),
        quantity      INTEGER       NOT NULL CHECK (quantity > 0),
        unit_price    DECIMAL(10,2) NOT NULL  -- captured at time of order
    );
    """,
]


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _Customer:
    name: str
    email: str
    country: str
    signup_days_ago: int


@dataclass(frozen=True, slots=True)
class _Product:
    name: str
    category: str
    unit_price: Decimal


_CUSTOMERS: list[_Customer] = [
    _Customer("Alice Chen",      "alice@example.com",   "USA",       540),
    _Customer("Bob Martinez",    "bob@example.com",     "USA",       480),
    _Customer("Carla Diaz",      "carla@example.com",   "Mexico",    420),
    _Customer("Daniel Okoye",    "daniel@example.com",  "Nigeria",   380),
    _Customer("Elena Rossi",     "elena@example.com",   "Italy",     330),
    _Customer("Felix Wong",      "felix@example.com",   "USA",       290),
    _Customer("Grace Adeyemi",   "grace@example.com",   "Nigeria",   250),
    _Customer("Hiro Tanaka",     "hiro@example.com",    "Japan",     180),
    _Customer("Isabel Santos",   "isabel@example.com",  "Mexico",    140),
    _Customer("Jamal Williams",  "jamal@example.com",   "USA",        90),
]


_PRODUCTS: list[_Product] = [
    # Electronics
    _Product("Wireless Earbuds Pro",  "Electronics", Decimal("129.99")),
    _Product("Smart Home Hub",        "Electronics", Decimal("89.50")),
    _Product("4K Action Camera",      "Electronics", Decimal("249.00")),
    # Apparel
    _Product("Merino Wool Sweater",   "Apparel",     Decimal("78.00")),
    _Product("Athletic Sneakers",     "Apparel",     Decimal("110.00")),
    _Product("Rain Jacket",           "Apparel",     Decimal("145.00")),
    # Home Goods
    _Product("Espresso Machine",      "Home Goods",  Decimal("349.99")),
    _Product("Memory Foam Pillow",    "Home Goods",  Decimal("42.50")),
    _Product("Kitchen Knife Set",     "Home Goods",  Decimal("189.00")),
    # Books
    _Product("Atomic Habits",         "Books",       Decimal("18.99")),
    _Product("Dune (Hardcover)",      "Books",       Decimal("24.50")),
    _Product("Python for Data Science","Books",      Decimal("39.99")),
    # Outdoor
    _Product("Camping Tent (2P)",     "Outdoor",     Decimal("210.00")),
    _Product("Trail Running Shoes",   "Outdoor",     Decimal("135.00")),
    _Product("Insulated Water Bottle","Outdoor",     Decimal("28.50")),
]


_STATUSES: tuple[str, ...] = ("pending", "shipped", "delivered", "cancelled")
_STATUS_WEIGHTS: tuple[int, ...] = (5, 15, 75, 5)  # ~75% delivered


def _seed_orders(
    rng: random.Random,
    today: date,
    n_orders: int,
) -> list[tuple[int, int, date, str, list[tuple[int, int]]]]:
    """Generate (order_id, customer_id, order_date, status, [(product_id, qty), ...])."""
    out: list[tuple[int, int, date, str, list[tuple[int, int]]]] = []
    for order_id in range(1, n_orders + 1):
        customer_id = rng.randint(1, len(_CUSTOMERS))
        days_ago = rng.randint(0, 540)
        order_date = today - timedelta(days=days_ago)
        status = rng.choices(_STATUSES, weights=_STATUS_WEIGHTS, k=1)[0]
        n_items = rng.randint(1, 4)
        # Sample without replacement so an order doesn't have duplicate products
        product_ids = rng.sample(range(1, len(_PRODUCTS) + 1), k=n_items)
        items = [(pid, rng.randint(1, 3)) for pid in product_ids]
        out.append((order_id, customer_id, order_date, status, items))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_and_seed(
    con: duckdb.DuckDBPyConnection,
    *,
    n_orders: int = 120,
    seed: int = 42,
    today: date | None = None,
) -> None:
    """Create the schema and populate it with deterministic seed data.

    Parameters
    ----------
    con : DuckDB connection
        Target connection. Existing tables with the same names will be dropped.
    n_orders : int
        How many orders to generate.
    seed : int
        RNG seed; same seed produces identical data (essential for tests).
    today : date, optional
        Reference date for relative timestamps. Defaults to the actual today.
    """
    rng = random.Random(seed)
    today = today or date.today()

    # Drop in reverse dependency order to avoid FK issues if rerunning
    for table in ("order_items", "orders", "products", "customers"):
        con.execute(f"DROP TABLE IF EXISTS {table};")

    for ddl in SCHEMA_DDL:
        con.execute(ddl)

    # customers
    for cid, c in enumerate(_CUSTOMERS, start=1):
        signup = today - timedelta(days=c.signup_days_ago)
        con.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?);",
            [cid, c.name, c.email, c.country, signup],
        )

    # products
    for pid, p in enumerate(_PRODUCTS, start=1):
        con.execute(
            "INSERT INTO products VALUES (?, ?, ?, ?, ?);",
            [pid, p.name, p.category, p.unit_price, True],
        )

    # orders + order_items
    order_item_id = 1
    for order_id, customer_id, order_date, status, items in _seed_orders(rng, today, n_orders):
        con.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?);",
            [order_id, customer_id, order_date, status],
        )
        for product_id, qty in items:
            unit_price = _PRODUCTS[product_id - 1].unit_price
            con.execute(
                "INSERT INTO order_items VALUES (?, ?, ?, ?, ?);",
                [order_item_id, order_id, product_id, qty, unit_price],
            )
            order_item_id += 1