"""
db/databricks.py

Databricks SQL Warehouse backend.

Uses the optional `databricks-sql-connector` package (install with
`pip install -e ".[databricks]"`). The connector is imported lazily so the
rest of the package works without it. A pre-built connection (or factory) can
be injected, which is how the unit tests run without any network access.

Configuration (environment variables, see `.env.example`):
  DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN,
  and optionally DATABRICKS_CATALOG / DATABRICKS_SCHEMA for Unity Catalog.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from typing import Any

from analytics_copilot.db.backend import QueryResult

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_identifier(value: str, what: str) -> str:
    """Reject anything that is not a plain SQL identifier (used before interpolation)."""
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(f"Invalid {what}: {value!r}. Use letters, digits and underscores only.")
    return value


class DatabricksBackend:
    """Backend for a Databricks SQL Warehouse, with optional Unity Catalog scoping."""

    def __init__(
        self,
        *,
        server_hostname: str,
        http_path: str,
        access_token: str,
        catalog: str | None = None,
        schema: str | None = None,
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._server_hostname = server_hostname
        self._http_path = http_path
        self._access_token = access_token
        self._catalog = _check_identifier(catalog, "catalog") if catalog else None
        self._schema = _check_identifier(schema, "schema") if schema else None
        self._connection_factory = connection_factory
        self._con: Any | None = None

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        connection_factory: Callable[[], Any] | None = None,
    ) -> DatabricksBackend:
        """Build a backend from DATABRICKS_* environment variables."""
        env = os.environ if env is None else env
        missing = [
            name
            for name in ("DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN")
            if not env.get(name)
        ]
        if missing:
            raise RuntimeError(f"Missing Databricks configuration: {', '.join(missing)}.")
        return cls(
            server_hostname=env["DATABRICKS_SERVER_HOSTNAME"],
            http_path=env["DATABRICKS_HTTP_PATH"],
            access_token=env["DATABRICKS_TOKEN"],
            catalog=env.get("DATABRICKS_CATALOG") or None,
            schema=env.get("DATABRICKS_SCHEMA") or None,
            connection_factory=connection_factory,
        )

    @property
    def dialect(self) -> str:
        return "databricks"

    def __enter__(self) -> DatabricksBackend:  # noqa: PYI034 - py3.10 has no typing.Self
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def connection(self) -> Any:
        if self._con is None:
            raise RuntimeError("Backend is not connected. Call connect() first.")
        return self._con

    def connect(self) -> None:
        if self._con is not None:
            return
        if self._connection_factory is not None:
            self._con = self._connection_factory()
            return
        try:
            from databricks import sql as dbsql
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "databricks-sql-connector is not installed. "
                'Install it with: pip install -e ".[databricks]"'
            ) from exc
        kwargs: dict[str, Any] = {
            "server_hostname": self._server_hostname,
            "http_path": self._http_path,
            "access_token": self._access_token,
        }
        if self._catalog:
            kwargs["catalog"] = self._catalog
        if self._schema:
            kwargs["schema"] = self._schema
        self._con = dbsql.connect(**kwargs)

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def query(self, sql: str, params: list[Any] | None = None) -> QueryResult:
        """Execute a (pre-validated) SELECT and return all rows."""
        cursor = self.connection.cursor()
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            raw_rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
        finally:
            cursor.close()
        rows = [tuple(r) for r in raw_rows]
        return QueryResult(columns=columns, rows=rows, row_count=len(rows))

    def list_columns(self) -> dict[str, list[tuple[str, str]]]:
        """Introspect columns via the Unity Catalog `information_schema`.

        Scoped to the configured catalog (`<catalog>.information_schema`) and
        schema when set; otherwise uses the session's current catalog/schema.
        """
        prefix = f"{self._catalog}." if self._catalog else ""
        schema_filter = (
            f"table_schema = '{self._schema}'" if self._schema else "table_schema = current_schema()"
        )
        result = self.query(
            "SELECT table_name, column_name, data_type "
            f"FROM {prefix}information_schema.columns "
            f"WHERE {schema_filter} ORDER BY table_name, ordinal_position"
        )
        out: dict[str, list[tuple[str, str]]] = {}
        for table, column, dtype in result.rows:
            out.setdefault(str(table), []).append((str(column), str(dtype)))
        return out
