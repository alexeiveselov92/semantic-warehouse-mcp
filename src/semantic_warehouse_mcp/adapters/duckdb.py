"""DuckDB: a database file, opened read-only for every query, with file and network access switched off."""

from __future__ import annotations

import json
import pathlib
import threading

import duckdb

from . import Adapter, AdapterError, Column, EngineReading, EngineSettings, Result, Table, TimedOut

# The engine's own line: the file opened read-only, no reading of files or URLs, no extensions loaded on demand,
# no Python variables read as tables, and none of it changeable from a query.
CONNECTION_CONFIG = {
    "enable_external_access": False,
    "autoload_known_extensions": False,
    "autoinstall_known_extensions": False,
    "python_enable_replacements": False,
    "lock_configuration": True,
}


class DuckDBSettings(EngineSettings):
    path: str  # the database file; relative to the configuration file's folder


class DuckDBAdapter(Adapter):
    engine = "duckdb"
    dialect = "duckdb"
    settings_model = DuckDBSettings
    allowed_table_functions = frozenset({"range", "generate_series", "unnest"})

    def __init__(self, settings: DuckDBSettings, base_dir: pathlib.Path) -> None:
        super().__init__(settings, base_dir)
        self.path = (base_dir / settings.path).resolve()
        self._catalog: str | None = None

    @property
    def default_schema(self) -> str:
        return "main"

    @property
    def catalog(self) -> str | None:
        return self._catalog

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if not self.path.is_file():
            raise AdapterError(f"the database file {self.path} does not exist")
        try:
            return duckdb.connect(str(self.path), read_only=True, config=CONNECTION_CONFIG)
        except duckdb.Error as error:
            raise AdapterError(f"cannot open {self.path} read-only: {error}") from error

    def check(self) -> None:
        with self._connect() as connection:
            self._catalog = connection.execute("SELECT current_database()").fetchone()[0]

    def engine_reading(self, sql: str, seconds: float) -> EngineReading:
        try:
            with duckdb.connect(":memory:") as parser:
                statements = parser.extract_statements(sql)
                if len(statements) != 1:
                    return EngineReading(refusal=f"DuckDB reads {len(statements)} statements in it")
                if statements[0].type != duckdb.StatementType.SELECT:
                    return EngineReading(refusal=f"DuckDB reads it as {statements[0].type.name}, not SELECT")
                tree = json.loads(parser.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
        except duckdb.Error as error:
            return EngineReading(refusal=f"DuckDB cannot parse it: {error}")
        if tree.get("error"):
            return EngineReading(refusal=f"DuckDB cannot read it as a query: {tree.get('error_message', '')}")
        # A CTE's name is a table to DuckDB's parser too; an unqualified name a CTE of the query has is taken for it.
        found, cte_names = [], set()
        _walk(tree, found, cte_names)
        tables = []
        for catalog, schema, table in found:
            if not catalog and not schema and table.lower() in cte_names:
                continue
            own = not catalog or (self._catalog is not None and catalog.lower() == self._catalog.lower())
            tables.append((None if own else catalog, schema or self.default_schema, table))
        return EngineReading(tables=tuple(tables))

    def run(self, sql: str, max_rows: int, seconds: float) -> Result:
        with self._connect() as connection:
            timer = threading.Timer(seconds, connection.interrupt)
            timer.start()
            try:
                connection.execute(sql)
                description = connection.description or []
                rows = connection.fetchmany(max_rows + 1)
            except duckdb.InterruptException as error:
                raise TimedOut(f"stopped after {seconds:g} s") from error
            except duckdb.Error as error:
                raise AdapterError(str(error)) from error
            finally:
                timer.cancel()
        columns = [Column(name=item[0], type=str(item[1])) for item in description]
        return Result(columns=columns, rows=rows[:max_rows], more_rows=len(rows) > max_rows)

    def tables(self) -> list[Table]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT schema_name, table_name, 'table', comment FROM duckdb_tables()
                WHERE database_name = current_database() AND NOT internal AND NOT temporary
                UNION ALL
                SELECT schema_name, view_name, 'view', comment FROM duckdb_views()
                WHERE database_name = current_database() AND NOT internal AND NOT temporary
                ORDER BY 1, 2
                """
            ).fetchall()
        return [Table(schema=row[0], name=row[1], kind=row[2], comment=row[3]) for row in rows]

    def columns(self, schema: str, table: str) -> list[Column]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT column_name, data_type, is_nullable, comment FROM duckdb_columns()
                WHERE database_name = current_database()
                  AND lower(schema_name) = lower(?) AND lower(table_name) = lower(?)
                ORDER BY column_index
                """,
                [schema, table],
            ).fetchall()
        return [Column(name=row[0], type=row[1], nullable=row[2], comment=row[3]) for row in rows]


def _walk(node, found: list[tuple[str, str, str]], cte_names: set[str]) -> None:
    """The base tables in DuckDB's own syntax tree of a query, and the names of its CTEs."""
    if isinstance(node, dict):
        if node.get("type") == "BASE_TABLE":
            found.append((node.get("catalog_name", ""), node.get("schema_name", ""), node.get("table_name", "")))
        for entry in (node.get("cte_map") or {}).get("map") or []:
            if isinstance(entry, dict) and isinstance(entry.get("key"), str):
                cte_names.add(entry["key"].lower())
        for value in node.values():
            _walk(value, found, cte_names)
    elif isinstance(node, list):
        for value in node:
            _walk(value, found, cte_names)
