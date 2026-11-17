"""
db/factory.py

Select a database backend by name (CLI flag or ANALYTICS_BACKEND env var).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from analytics_copilot.db.backend import Backend

BACKEND_ENV_VAR = "ANALYTICS_BACKEND"
SUPPORTED_BACKENDS: tuple[str, ...] = ("duckdb", "databricks")


def resolve_backend_name(name: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """Resolve the backend name: explicit arg > ANALYTICS_BACKEND env > 'duckdb'."""
    env = os.environ if env is None else env
    resolved = (name or env.get(BACKEND_ENV_VAR) or "duckdb").strip().lower()
    if resolved not in SUPPORTED_BACKENDS:
        raise ValueError(
            f"Unknown backend {resolved!r}. Supported: {', '.join(SUPPORTED_BACKENDS)}."
        )
    return resolved


def create_backend(name: str | None = None, env: Mapping[str, str] | None = None) -> Backend:
    """Instantiate (but do not connect) the requested backend."""
    resolved = resolve_backend_name(name, env)
    if resolved == "databricks":
        from analytics_copilot.db.databricks import DatabricksBackend

        return DatabricksBackend.from_env(env)
    from analytics_copilot.db.database import Database

    return Database()
