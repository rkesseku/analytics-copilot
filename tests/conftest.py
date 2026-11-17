"""Shared pytest configuration.

Some test modules need third-party packages at import time (DuckDB for the
real database, sqlglot for the SQL validator). When a package is missing
(e.g. a minimal local environment) those modules are skipped instead of
erroring at collection; CI installs everything, so nothing is skipped there.
"""

from __future__ import annotations

import importlib.util

_REQUIRES: dict[str, tuple[str, ...]] = {
    "test_database.py": ("duckdb",),
    "test_executor.py": ("duckdb", "sqlglot"),
    "test_pipeline.py": ("duckdb", "sqlglot"),
    "test_schema.py": ("duckdb",),
    "test_validator.py": ("sqlglot",),
}

collect_ignore = [
    name
    for name, modules in _REQUIRES.items()
    if any(importlib.util.find_spec(m) is None for m in modules)
]
