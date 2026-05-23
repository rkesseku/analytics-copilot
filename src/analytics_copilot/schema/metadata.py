"""
schema/metadata.py

Structured metadata describing the database schema.

This module is what the LLM "knows" about the database. It's hand-written
on purpose: the LLM-facing descriptions matter more than the raw DDL,
because they encode business meaning ("which date column should I use for
'last quarter'?") that raw column types can't express.

In a production system this metadata would be:
  - Generated from a data catalog (DataHub, Atlan, Collibra)
  - Or auto-introspected then enriched by hand
  - And version-controlled like code
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    """One column's metadata."""

    name: str
    sql_type: str
    description: str
    examples: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TableInfo:
    """One table's metadata, including all columns."""

    name: str
    description: str
    columns: tuple[ColumnInfo, ...]
    primary_key: str
    foreign_keys: tuple[tuple[str, str], ...] = ()  # (column, references "table.column")

    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def get_column(self, name: str) -> ColumnInfo | None:
        for c in self.columns:
            if c.name.lower() == name.lower():
                return c
        return None

    def to_llm_text(self) -> str:
        """Render this table as a compact, LLM-friendly description block."""
        lines = [f"TABLE {self.name}: {self.description}"]
        lines.append(f"  Primary key: {self.primary_key}")
        if self.foreign_keys:
            fk_strs = [f"{col} -> {ref}" for col, ref in self.foreign_keys]
            lines.append(f"  Foreign keys: {', '.join(fk_strs)}")
        lines.append("  Columns:")
        for c in self.columns:
            ex = f" (examples: {', '.join(c.examples)})" if c.examples else ""
            lines.append(f"    - {c.name} {c.sql_type}: {c.description}{ex}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Schema definition for our toy e-commerce database
# ---------------------------------------------------------------------------

CUSTOMERS_TABLE = TableInfo(
    name="customers",
    description="Registered customers who place orders.",
    primary_key="customer_id",
    columns=(
        ColumnInfo("customer_id", "INTEGER", "Unique customer identifier."),
        ColumnInfo("name", "VARCHAR", "Customer's full name."),
        ColumnInfo("email", "VARCHAR", "Email address. Unique across customers."),
        ColumnInfo(
            "country",
            "VARCHAR",
            "Country of the customer.",
            examples=("USA", "Mexico", "Nigeria", "Italy", "Japan"),
        ),
        ColumnInfo("signup_date", "DATE", "Date the customer registered."),
    ),
)


PRODUCTS_TABLE = TableInfo(
    name="products",
    description="Catalog of products available for purchase.",
    primary_key="product_id",
    columns=(
        ColumnInfo("product_id", "INTEGER", "Unique product identifier."),
        ColumnInfo("name", "VARCHAR", "Product name."),
        ColumnInfo(
            "category",
            "VARCHAR",
            "Product category.",
            examples=("Electronics", "Apparel", "Home Goods", "Books", "Outdoor"),
        ),
        ColumnInfo(
            "unit_price",
            "DECIMAL(10,2)",
            "Current list price per unit, in USD.",
        ),
        ColumnInfo(
            "is_active",
            "BOOLEAN",
            "TRUE if the product is currently available for sale.",
        ),
    ),
)


ORDERS_TABLE = TableInfo(
    name="orders",
    description="Customer purchase orders. One row per order.",
    primary_key="order_id",
    foreign_keys=(("customer_id", "customers.customer_id"),),
    columns=(
        ColumnInfo("order_id", "INTEGER", "Unique order identifier."),
        ColumnInfo("customer_id", "INTEGER", "Which customer placed the order."),
        ColumnInfo(
            "order_date",
            "DATE",
            "Date the order was placed. Use this for time-window filters.",
        ),
        ColumnInfo(
            "status",
            "VARCHAR",
            "Order lifecycle status. Only 'delivered' orders count as completed revenue.",
            examples=("pending", "shipped", "delivered", "cancelled"),
        ),
    ),
)


ORDER_ITEMS_TABLE = TableInfo(
    name="order_items",
    description=(
        "Line items within an order. One row per (order, product) pair. "
        "Revenue = SUM(quantity * unit_price) across this table."
    ),
    primary_key="order_item_id",
    foreign_keys=(
        ("order_id", "orders.order_id"),
        ("product_id", "products.product_id"),
    ),
    columns=(
        ColumnInfo("order_item_id", "INTEGER", "Unique line-item identifier."),
        ColumnInfo("order_id", "INTEGER", "Which order this line belongs to."),
        ColumnInfo("product_id", "INTEGER", "Which product was purchased."),
        ColumnInfo("quantity", "INTEGER", "Units purchased on this line. Always >= 1."),
        ColumnInfo(
            "unit_price",
            "DECIMAL(10,2)",
            "Price per unit AT THE TIME OF ORDER (may differ from current products.unit_price).",
        ),
    ),
)


# Authoritative list of all tables in the schema.
ALL_TABLES: tuple[TableInfo, ...] = (
    CUSTOMERS_TABLE,
    PRODUCTS_TABLE,
    ORDERS_TABLE,
    ORDER_ITEMS_TABLE,
)


def get_table(name: str) -> TableInfo | None:
    """Look up a table by name (case-insensitive). Returns None if not found."""
    name_lc = name.lower()
    for t in ALL_TABLES:
        if t.name.lower() == name_lc:
            return t
    return None


def full_schema_text() -> str:
    """Render the entire schema as one LLM-friendly block. Useful for small schemas."""
    return "\n\n".join(t.to_llm_text() for t in ALL_TABLES)