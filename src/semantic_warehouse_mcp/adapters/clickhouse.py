"""ClickHouse over HTTP: every request a GET, which ClickHouse runs read-only, whoever the user is."""

from __future__ import annotations

import decimal
import json
import os
import pathlib
import re
import time
import uuid
from typing import Annotated, Any

import httpx
from pydantic import Field

from .. import files
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
# Each limit a profile may set, with the mode that says what happens past it.
LIMITS = (
    ("max_rows_to_read", "read_overflow_mode"),
    ("max_bytes_to_read", "read_overflow_mode"),
    ("max_rows_to_read_leaf", "read_overflow_mode_leaf"),
    ("max_bytes_to_read_leaf", "read_overflow_mode_leaf"),
    ("max_rows_to_group_by", "group_by_overflow_mode"),
    ("max_rows_to_sort", "sort_overflow_mode"),
    ("max_bytes_to_sort", "sort_overflow_mode"),
    ("max_rows_in_distinct", "distinct_overflow_mode"),
    ("max_bytes_in_distinct", "distinct_overflow_mode"),
    ("max_rows_in_set", "set_overflow_mode"),
    ("max_bytes_in_set", "set_overflow_mode"),
    ("max_rows_in_join", "join_overflow_mode"),
    ("max_bytes_in_join", "join_overflow_mode"),
    ("max_rows_to_transfer", "transfer_overflow_mode"),
    ("max_bytes_to_transfer", "transfer_overflow_mode"),
    ("max_result_rows", "result_overflow_mode"),
    ("max_result_bytes", "result_overflow_mode"),
    ("max_execution_time", "timeout_overflow_mode"),
    ("max_execution_time_leaf", "timeout_overflow_mode_leaf"),
)
PROFILE_SETTINGS = sorted(
    {"readonly", "cancel_http_readonly_queries_on_client_close", *(n for pair in LIMITS for n in pair)}
)
# What a `readonly = 1` user may still send (measured on 22.11 and 26.3): the answer's format, the query's id, its
# parameters — no limit, no overflow mode.
FIXED_PARAMS = (
    "query",
    "database",
    "default_format",
    "output_format_json_quote_decimals",
    "wait_end_of_query",
    "query_id",
)

FORMAT = "JSONCompactEachRowWithNamesAndTypes"
TIMEOUT_CODE = "159"  # TIMEOUT_EXCEEDED
ERROR_IN_BODY = re.compile(r"Code: \d+\. DB::Exception: .*")
# An error written as a row, as 25.x writes one met after rows were sent: `["Code: 395. DB::Exception: … (version …)"]`.
ERROR_AS_VALUE = re.compile(r"Code: \d+\. DB::Exception: .* \(version .*\)", re.DOTALL)
BIG_INTEGER = re.compile(r"^U?Int(64|128|256)$")

# Tables ClickHouse reads by itself: `system.one` is what a SELECT without FROM reads.
IMPLICIT_TABLES = {("system", "one")}

TABLE_IN_TREE = re.compile(r"^\s*TABLE id: \d+, (?:alias: [^,]+, )?table_name: (\S+)\s*$", re.MULTILINE)
# A name the analyzer has not resolved into a table, a column or a function: a tree holding one names no table for
# it, so it is read as no reading at all. `run_passes = 1` asks for the resolved tree — the default from 24.3, and what
# makes the analyzer of older servers (22.x, 23.x) resolve it.
UNRESOLVED_IN_TREE = re.compile(r"^\s*IDENTIFIER id: \d+", re.MULTILINE)
EXPLAIN = "EXPLAIN QUERY TREE run_passes = 1"
UNRESOLVED = (
    "ClickHouse explained the query without resolving its names: it cannot say which tables the query reads, so the "
    "server cannot check them"
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
        # Learnt by `check()` from the user's own settings. A `readonly = 1` user may change none: its queries keep
        # its profile's limits (checked never to cut silently), and the server bounds them itself — reading at most
        # one row more than it shows, and stopping the query (`KILL QUERY`, its own) past its time.
        self.fixed_limits = False
        self.known: set[str] = set(PROFILE_SETTINGS)

    @property
    def default_schema(self) -> str:
        return self.settings.database

    def _headers(self) -> dict[str, str]:
        headers = {}
        for header, variable, key in (
            ("X-ClickHouse-User", self.settings.user_env, "`user_env`"),
            ("X-ClickHouse-Key", self.settings.password_env, "`password_env`"),
        ):
            if variable is None:
                continue
            value = os.environ.get(variable)
            if value is None:
                raise AdapterError(f"the variable {files.variable(variable, key)} is not set")
            headers[header] = value
        return headers

    def _params(self, query: str, seconds: float, settings: dict[str, Any] | None) -> dict[str, Any]:
        params: dict[str, Any] = {"query": query, "database": self.settings.database, "query_id": str(uuid.uuid4())}
        if self.fixed_limits:
            given = {k: v for k, v in (settings or {}).items() if k in FIXED_PARAMS or k.startswith("param_")}
            return {**params, "wait_end_of_query": 1, **given}
        limits = {
            "max_execution_time": max(1, round(seconds)),
            **OVERFLOW_MODES,
            "cancel_http_readonly_queries_on_client_close": 1,
        }
        known = {k: v for k, v in limits.items() if k in self.known}  # an older server lacks some: they cannot apply
        return {**params, **known, "wait_end_of_query": 1, **(settings or {})}

    def _get(self, query: str, seconds: float, settings: dict[str, Any] | None = None) -> str:
        """One query, as a GET (read-only by ClickHouse's own rule), within `seconds`; the body as text."""
        params = self._params(query, seconds, settings)
        try:
            response = httpx.get(self.url, params=params, headers=self._headers(), timeout=seconds + 10)
        except httpx.TimeoutException as error:
            self._stop(params["query_id"])
            raise TimedOut(f"stopped after {seconds:g} s") from error
        except httpx.HTTPError as error:
            raise AdapterError(f"cannot reach {self.url}: {type(error).__name__}") from error
        if response.status_code != 200:
            raise self._error(response.headers.get("X-ClickHouse-Exception-Code", ""), response.text, seconds)
        return response.text

    def _error(self, code: str, text: str, seconds: float) -> AdapterError:
        if code == TIMEOUT_CODE:
            return TimedOut(f"stopped after {seconds:g} s")
        found = ERROR_IN_BODY.search(text)
        message = found.group(0) if found else text.strip()
        return AdapterError(re.sub(r"\s*\(version [^)]*\)\s*$", "", message.strip()))

    def _may_stop(self) -> bool:
        """Whether the user may stop its own queries: asked by stopping one that does not exist."""
        params = {"query": f"KILL QUERY WHERE query_id = '{uuid.uuid4()}' ASYNC", "database": self.settings.database}
        try:
            return httpx.get(self.url, params=params, headers=self._headers(), timeout=10).status_code == 200
        except httpx.HTTPError:
            return False

    def _stop(self, query_id: str) -> None:
        """Stop a query the server no longer waits for: its own, by its id — a user with `readonly = 1` may."""
        params = {"query": f"KILL QUERY WHERE query_id = '{query_id}' ASYNC", "database": self.settings.database}
        try:
            httpx.get(self.url, params=params, headers=self._headers(), timeout=10)
        except httpx.HTTPError:
            pass

    def check(self) -> None:
        own = self._own_settings()
        self.known = set(own)
        self.fixed_limits = own.get("readonly") == "1"
        if self.fixed_limits:
            cutting = [
                f"{limit} = {own[limit]} with {mode} = {own[mode]!r}"
                for limit, mode in LIMITS
                if own.get(limit, "0") not in ("0", "0.") and own.get(mode, "throw") != "throw"
            ]
            if cutting:
                raise AdapterError(
                    "the user has readonly = 1, so its queries keep its profile's limits — and the profile cuts "
                    f"silently past some: {'; '.join(cutting)}. Set those modes to 'throw' in the profile, or give the "
                    "user readonly = 2, which lets the server set its own"
                )
            if own.get("max_execution_time", "0") in ("0", "0.") and not self._may_stop():
                raise AdapterError(
                    "the user has readonly = 1, its profile sets no time limit, and it may not stop its own queries "
                    "(KILL QUERY needs SELECT on system.processes): a query past its time would run on. Grant it "
                    "that, set max_execution_time in its profile, or give the user readonly = 2"
                )
        else:
            self._get("SELECT 1", seconds=10)  # its limits taken (a profile's constraints may refuse one)
        try:
            tree = self._get(f"{EXPLAIN} SELECT 1", seconds=10, settings={"default_format": "TSVRaw"})
        except AdapterError as error:
            raise AdapterError(f"ClickHouse cannot explain a query tree: {error}") from error
        if "system.one" not in TABLE_IN_TREE.findall(tree) or UNRESOLVED_IN_TREE.search(tree):
            raise AdapterError(UNRESOLVED)  # a resolved tree names the table a SELECT without FROM reads

    def _own_settings(self) -> dict[str, str]:
        """The user's own settings that bear on its queries, asked with none sent: any user, any version answers."""
        names = ", ".join(f"'{name}'" for name in PROFILE_SETTINGS)
        params = {
            "query": f"SELECT name, value FROM system.settings WHERE name IN ({names}) FORMAT JSONCompactEachRow",
            "database": self.settings.database,
        }
        try:
            response = httpx.get(self.url, params=params, headers=self._headers(), timeout=20)
        except httpx.HTTPError as error:
            raise AdapterError(f"cannot reach {self.url}: {type(error).__name__}") from error
        if response.status_code != 200:
            raise self._error(response.headers.get("X-ClickHouse-Exception-Code", ""), response.text, 20)
        return {row[0]: row[1] for row in map(json.loads, response.text.splitlines()) if row}

    def engine_reading(self, sql: str, seconds: float, named: tuple[str, ...] = ()) -> EngineReading:
        try:
            tree = self._get(f"{EXPLAIN} {sql}", seconds=seconds, settings={"default_format": "TSVRaw"})
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
        if self.fixed_limits:
            lines, more = self._stream(sql, max_rows, seconds)
        else:
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
            lines, more = body.splitlines()[: max_rows + 3], False
        if len(lines) < 2:
            raise AdapterError("ClickHouse answered without column names and types")
        names, types = json.loads(lines[0]), json.loads(lines[1])
        rows = [
            tuple(_typed(v, t) for v, t in zip(json.loads(line, parse_float=decimal.Decimal), types, strict=True))
            for line in lines[2:]
        ]
        columns = [Column(name=name, type=type_) for name, type_ in zip(names, types, strict=True)]
        return Result(columns=columns, rows=rows[:max_rows], more_rows=more or len(rows) > max_rows)

    def _stream(self, sql: str, max_rows: int, seconds: float) -> tuple[list[str], bool]:
        """The answer's first lines as they come — its names, types and one row more than shown — then the query
        stopped, as it is past `seconds`: the server's own bounds, where the user's profile allows it none."""
        params = self._params(sql, seconds, {"default_format": FORMAT, "output_format_json_quote_decimals": 0})
        params["wait_end_of_query"] = 0  # rows as they come, so the rest is never read
        deadline = time.monotonic() + seconds
        lines: list[str] = []
        more = False
        try:
            with httpx.stream("GET", self.url, params=params, headers=self._headers(), timeout=seconds) as response:
                if response.status_code != 200:
                    raise self._error(response.headers.get("X-ClickHouse-Exception-Code", ""), response.read().decode(
                        errors="replace"), seconds)  # fmt: skip
                streamed = response.iter_lines()
                for line in streamed:
                    if time.monotonic() > deadline:
                        raise httpx.ReadTimeout("past its time")
                    if not line.strip():
                        continue
                    if _not_a_row(line, lines):  # an error, met after rows were already sent
                        rest = "\n".join([line, *(text for _, text in zip(range(20), streamed, strict=False))])
                        raise self._error("", rest, seconds)
                    lines.append(line)
                    if len(lines) > max_rows + 2:
                        more = True
                        break
        except httpx.TimeoutException as error:
            self._stop(params["query_id"])
            raise TimedOut(f"stopped after {seconds:g} s") from error
        except httpx.HTTPError as error:
            raise AdapterError(f"cannot reach {self.url}: {type(error).__name__}") from error
        if more:
            self._stop(params["query_id"])  # the rest is never read: the query need not go on
        return lines, more

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


def _not_a_row(line: str, before: list[str]) -> bool:
    """Whether a streamed line is an error rather than the names, the types or a row: each ClickHouse writes it its own
    way — a line of text (22.x), a row of one error message (25.x), an `__exception__` block (26.x)."""
    if not line.lstrip().startswith("["):
        return True
    if len(before) < 2:
        return False
    try:
        values = json.loads(line)
    except ValueError:
        return True
    width = len(json.loads(before[0]))
    if len(values) != width:
        return True
    return width == 1 and isinstance(values[0], str) and ERROR_AS_VALUE.fullmatch(values[0]) is not None


def _typed(value: Any, type_: str) -> Any:
    """A value as its type says — a 64-bit integer a server quoted as text (ClickHouse before 24, by default) read back
    as the integer it is."""
    inner = type_
    for wrapper in ("Nullable(", "LowCardinality("):
        while inner.startswith(wrapper) and inner.endswith(")"):
            inner = inner[len(wrapper) : -1]
    if isinstance(value, str) and BIG_INTEGER.match(inner):
        return int(value)
    if isinstance(value, list) and inner.startswith("Array(") and inner.endswith(")"):
        return [_typed(item, inner[len("Array(") : -1]) for item in value]
    return value
