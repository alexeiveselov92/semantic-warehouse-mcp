"""ClickHouse over HTTP: every request a GET, which ClickHouse runs read-only, whoever the user is."""

from __future__ import annotations

import decimal
import json
import os
import pathlib
import re
from typing import Annotated

import httpx
from pydantic import Field

from . import Adapter, AdapterError, Column, EngineReading, EngineSettings, Result, Table, TimedOut

# The name of an environment variable — never its value.
EnvName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]

# Every limit is sent with its overflow mode, and every mode refuses (`throw`): a mode that cuts silently — `break`,
# or `any` for GROUP BY — makes a wrong number look like a right one. The one exception is the result's rows: the
# server asks for one row more than it shows, so a cut there is always seen (`more_rows`), and the bytes limit that
# could cut below that row is switched off.
OVERFLOW_MODES = {
    name: "throw"
    for name in (
        "read_overflow_mode",
        "read_overflow_mode_leaf",
        "group_by_overflow_mode",
        "sort_overflow_mode",
        "distinct_overflow_mode",
        "set_overflow_mode",
        "join_overflow_mode",
        "transfer_overflow_mode",
        "timeout_overflow_mode",
        "timeout_overflow_mode_leaf",
    )
}

FORMAT = "JSONCompactEachRowWithNamesAndTypes"
TIMEOUT_CODE = "159"  # TIMEOUT_EXCEEDED
READONLY_CODE = "164"  # READONLY: here, a setting the user may not change
# A user with `readonly = 1` may change no setting, so none of the limits above reaches its queries: refused, with
# the fix. `readonly = 2` lets it take them and still write nothing — its grants, and every request a GET, hold that.
READONLY_ONE = (
    "the user has readonly = 1, which lets it change no setting: the server cannot give its queries their limits "
    "(time, and the overflow modes that refuse rather than cut). Give the user readonly = 2 — it still cannot write: "
    "every request is a GET, which ClickHouse runs read-only, and its grants hold the tables it may read"
)

# Tables ClickHouse reads by itself: `system.one` is what a SELECT without FROM reads.
IMPLICIT_TABLES = {("system", "one")}

TABLE_IN_TREE = re.compile(r"^\s*TABLE id: \d+, (?:alias: [^,]+, )?table_name: (\S+)\s*$", re.MULTILINE)
# A name the analyzer has not resolved into a table, a column or a function. Before 24.3 (the analyzer off), ClickHouse
# explains a query tree without resolving it: no table is named in it — so a tree holding one is read as no reading.
UNRESOLVED_IN_TREE = re.compile(r"^\s*IDENTIFIER id: \d+", re.MULTILINE)
UNRESOLVED = (
    "ClickHouse explains queries without resolving their names (its analyzer, on from 24.3): it cannot say which "
    "tables a query reads, so the server cannot check them — 24.3 or newer is needed"
)


class ClickHouseSettings(EngineSettings):
    url: Annotated[str, Field(pattern=r"^https?://[^/@\s]+(:\d+)?/?$")]  # scheme, host and port; no credentials
    database: str = "default"  # the database an unqualified table name resolves to
    user_env: EnvName | None = None  # the variable holding the user's name; ClickHouse's `default` when not given
    password_env: EnvName | None = None  # the variable holding the password


class ClickHouseAdapter(Adapter):
    engine = "clickhouse"
    dialect = "clickhouse"
    settings_model = ClickHouseSettings
    allowed_table_functions = frozenset({"numbers", "generateseries", "generate_series"})
    # A dictionary or a Join table is read by its name in the first argument: checked against `readable`.
    table_reading_functions = (("dictget*", 0), ("dicthas", 0), ("dictisin", 0), ("joinget*", 0))

    def __init__(self, settings: ClickHouseSettings, base_dir: pathlib.Path) -> None:
        super().__init__(settings, base_dir)
        self.url = settings.url.rstrip("/") + "/"

    @property
    def default_schema(self) -> str:
        return self.settings.database

    def _headers(self) -> dict[str, str]:
        headers = {}
        for header, variable in (
            ("X-ClickHouse-User", self.settings.user_env),
            ("X-ClickHouse-Key", self.settings.password_env),
        ):
            if variable is None:
                continue
            value = os.environ.get(variable)
            if value is None:
                raise AdapterError(f"the variable {variable} is not set")
            headers[header] = value
        return headers

    def _get(self, query: str, seconds: float, settings: dict[str, str | int] | None = None) -> str:
        """One query, as a GET (read-only by ClickHouse's own rule), within `seconds`; the body as text."""
        params: dict[str, str | int] = {
            "query": query,
            "database": self.settings.database,
            "max_execution_time": max(1, round(seconds)),
            **OVERFLOW_MODES,
            "wait_end_of_query": 1,  # an error reaches us as an error, not after rows already sent
            "cancel_http_readonly_queries_on_client_close": 1,
            **(settings or {}),
        }
        try:
            response = httpx.get(self.url, params=params, headers=self._headers(), timeout=seconds + 10)
        except httpx.TimeoutException as error:
            raise TimedOut(f"stopped after {seconds:g} s") from error
        except httpx.HTTPError as error:
            raise AdapterError(f"cannot reach {self.url}: {type(error).__name__}") from error
        if response.status_code != 200:
            code = response.headers.get("X-ClickHouse-Exception-Code", "")
            message = re.sub(r"\s*\(version [^)]*\)\s*$", "", response.text.strip())
            if code == TIMEOUT_CODE:
                raise TimedOut(f"stopped after {seconds:g} s")
            if code == READONLY_CODE and "setting in readonly mode" in message:
                raise AdapterError(READONLY_ONE)
            raise AdapterError(message)
        return response.text

    def check(self) -> None:
        try:
            self._get("SELECT 1", seconds=10)
        except AdapterError as error:
            if "UNKNOWN_SETTING" not in str(error):
                raise
            # A server older than the limits the server sends with every query (before 24.3, some are unknown).
            raise AdapterError(f"ClickHouse does not know a setting the server sends with every query (24.3 or newer "
                               f"is needed): {error}") from error  # fmt: skip
        try:
            tree = self._get("EXPLAIN QUERY TREE SELECT 1", seconds=10, settings={"default_format": "TSVRaw"})
        except AdapterError as error:
            raise AdapterError(f"ClickHouse cannot explain a query tree (24.3 or newer is needed): {error}") from error
        if "system.one" not in TABLE_IN_TREE.findall(tree) or UNRESOLVED_IN_TREE.search(tree):
            raise AdapterError(UNRESOLVED)  # a resolved tree names the table a SELECT without FROM reads

    def engine_reading(self, sql: str, seconds: float) -> EngineReading:
        try:
            tree = self._get(f"EXPLAIN QUERY TREE {sql}", seconds=seconds, settings={"default_format": "TSVRaw"})
        except TimedOut:
            raise
        except AdapterError as error:
            return EngineReading(refusal=f"ClickHouse cannot read it as a query: {error}")
        first = tree.lstrip().split(" ", 1)[0]
        if first not in ("QUERY", "UNION"):
            return EngineReading(refusal=f"ClickHouse reads it as {first}, not a SELECT")
        if UNRESOLVED_IN_TREE.search(tree):
            return EngineReading(refusal=UNRESOLVED)
        tables = []
        for name in TABLE_IN_TREE.findall(tree):
            schema, _, table = name.partition(".")
            if (schema, table) not in IMPLICIT_TABLES:
                tables.append((None, schema, table))
        return EngineReading(tables=tuple(tables))

    def run(self, sql: str, max_rows: int, seconds: float) -> Result:
        body = self._get(
            sql,
            seconds=seconds,
            settings={
                "default_format": FORMAT,
                "max_result_rows": max_rows + 1,
                "result_overflow_mode": "break",
                "max_result_bytes": 0,
                "output_format_json_quote_64bit_integers": 0,
                "output_format_json_quote_decimals": 0,
            },
        )
        lines = body.splitlines()
        if len(lines) < 2:
            raise AdapterError("ClickHouse answered without column names and types")
        names, types = json.loads(lines[0]), json.loads(lines[1])
        rows = [tuple(json.loads(line, parse_float=decimal.Decimal)) for line in lines[2 : max_rows + 3]]
        columns = [Column(name=name, type=type_) for name, type_ in zip(names, types, strict=True)]
        return Result(columns=columns, rows=rows[:max_rows], more_rows=len(rows) > max_rows)

    def _rows(self, query: str, **params: str) -> list[list]:
        settings = {"default_format": "JSONCompactEachRow", **{f"param_{k}": v for k, v in params.items()}}
        body = self._get(query, seconds=30, settings=settings)
        return [json.loads(line) for line in body.splitlines() if line]

    def tables(self) -> list[Table]:
        rows = self._rows(
            """
            SELECT database, name,
                   multiIf(engine LIKE '%View', 'view', engine = 'Dictionary', 'dictionary', 'table'), comment
            FROM system.tables
            WHERE NOT is_temporary AND database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema')
            ORDER BY database, name
            """
        )
        return [Table(schema=row[0], name=row[1], kind=row[2], comment=row[3] or None) for row in rows]

    def columns(self, schema: str, table: str) -> list[Column]:
        rows = self._rows(
            """
            SELECT name, type, comment FROM system.columns
            WHERE database = {schema:String} AND table = {table:String}
            ORDER BY position
            """,
            schema=schema,
            table=table,
        )
        return [
            Column(name=row[0], type=row[1], nullable=row[1].startswith("Nullable("), comment=row[2] or None)
            for row in rows
        ]
