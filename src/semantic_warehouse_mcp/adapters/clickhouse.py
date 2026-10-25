"""ClickHouse over HTTP: every request a GET, which ClickHouse runs read-only, whoever the user is."""

from __future__ import annotations

import decimal
import json
import os
import pathlib
import re
import time
import uuid
from dataclasses import dataclass, field
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
    {
        "readonly",
        "cancel_http_readonly_queries_on_client_close",
        "enable_analyzer",
        "allow_experimental_analyzer",
        *(n for pair in LIMITS for n in pair),
    }
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

# What a tree prints after a table's name: FINAL, SAMPLE. An alias comes before the name, written as it was given —
# a comma or a newline in it too — so a line is read from its end.
MODIFIERS = r"(?:, final: 1)?(?:, sample_size: [0-9 /.]+)?(?:, sample_offset: [0-9 /.]+)?"
PART = r"[^\s,.]+"  # a database's or a table's name; one with a dot in it is not told from two names
TABLE_LINE = re.compile(rf"TABLE id: \d+(?:, alias: .*)?, table_name: ({PART})\.({PART}){MODIFIERS}")
TABLE_FUNCTION_NAME = re.compile(r", table_function_name: (\w+)")
# A name the analyzer has not resolved into a table, a column or a function: a tree holding one names no table for
# it, so it is read as no reading at all. `run_passes = 1` asks for the resolved tree — the default from 24.3, and what
# makes the analyzer of older servers (22.x, 23.x) resolve it.
UNRESOLVED_IN_TREE = re.compile(r"^\s*IDENTIFIER id: \d+", re.MULTILINE)
EXPLAIN = "EXPLAIN QUERY TREE run_passes = 1"
UNRESOLVED = (
    "ClickHouse explained the query without resolving its names: it cannot say which tables the query reads, so the "
    "server cannot check them"
)

# Before 24.3 the analyzer that answers EXPLAIN QUERY TREE is not the interpreter that runs the query (measured on
# 22.11): it refuses queries the server runs — a GROUP BY over an ALIAS column fails with Code 215 — and resolves some
# names otherwise — a CTE named before it is defined is a table to the interpreter, a CTE to the analyzer. Where the
# analyzer does not run queries, the tree as ClickHouse parsed it (`run_passes = 0`) is read too, its names resolved by
# the interpreter's rules, measured on 22.11: a name in a FROM or a JOIN, or right of IN, is a table, unless it is a CTE
# of its query or of a query around it, defined before it when it is in the same WITH; right of IN, an alias the query
# gives comes first. Both readings are held to the rules; where the analyzer cannot resolve a query, the parsed one
# alone.
PARSED = "EXPLAIN QUERY TREE run_passes = 0"
ANALYZER_SETTINGS = ("enable_analyzer", "allow_experimental_analyzer")  # the newer name first
# The parsed tree is read as a tree, so a line no query could print — one a newline in a quoted name split — is
# seen: its nodes are numbered in order, a list holds as many as it says, and sections and nodes alternate.
NODE_LINE = re.compile(r"([A-Z][A-Z_]*) id: (\d+)(?:, .*)?")
SECTION_LINE = re.compile(r"[A-Z]+(?: [A-Z]+)*")
LIST_LINE = re.compile(r"LIST id: \d+(?:, nodes: (\d+))?")
NAME_LINE = re.compile(rf"IDENTIFIER id: \d+(?:, alias: .*)?, identifier: ({PART}(?:\.{PART})*){MODIFIERS}")
CTE_LINE = re.compile(rf"QUERY id: \d+, is_subquery: 1, is_cte: 1(?:, [a-z_]+: 1)*, cte_name: ({PART})")
CTE_UNION_LINE = re.compile(rf"UNION id: \d+, is_subquery: 1, is_cte: 1, cte_name: ({PART}), union_mode: [A-Z_]+")
# The field the tree prints after a node's alias. An alias is written as given — a quoted one may hold `, key: ` — so
# it is taken only from a line holding one alias and one such field.
AFTER_ALIAS = {
    "CONSTANT": "constant_value",
    "FUNCTION": "function_name",
    "IDENTIFIER": "identifier",
    "QUERY": "is_subquery",
}
FUNCTION_NAME = re.compile(r", function_name: (\w+)")
IN_FUNCTIONS = frozenset(
    {"in", "notin", "globalin", "globalnotin", "nullin", "notnullin", "globalnullin", "globalnotnullin"}
)
TABLE_PLACES = frozenset({"JOIN TREE", "LEFT TABLE EXPRESSION", "RIGHT TABLE EXPRESSION", "TABLE EXPRESSION"})
UNREADABLE_TREE = "ClickHouse's tree of the query cannot be read as a query's: a quoted name holds a line break?"


class ClickHouseSettings(EngineSettings):
    url: Annotated[str, Field(pattern=r"^https?://[^/@\s]+(:\d+)?/?$")]  # scheme, host and port; no credentials
    database: str = "default"  # the database an unqualified table name resolves to
    user_env: EnvName | None = None  # the variable holding the user's name; ClickHouse's `default` when not given
    password_env: EnvName | None = None  # the variable holding the password


class ClickHouseAdapter(Adapter):
    engine = "clickhouse"
    dialect = "clickhouse"
    # `prefer_column_name_to_alias` is 0 by default, on both analyzers: `SELECT toDate(event_time) AS event_date …
    # WHERE event_date >= …` filters the alias, and reads every partition.
    aliases_shadow_columns = True
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
        # Whether the analyzer runs the user's queries (from 24.3, by default) — else its tree is read beside the parsed
        # one, read by the rules of the interpreter that runs them.
        self.analyzer_runs = False

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
        self.analyzer_runs = next((own[name] for name in ANALYZER_SETTINGS if name in own), "0") == "1"
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
        reading = _resolved_reading(tree, self.allowed_table_functions)
        if reading.refusal or ("system", "one") not in [(s, t) for _, s, t in reading.tables or ()]:
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
            if self.analyzer_runs:
                return EngineReading(refusal=f"ClickHouse cannot read it as a query: {error}")
            tree = None  # the analyzer that cannot resolve it does not run it: the parsed tree is read alone
        tables: list[tuple[str | None, str, str]] = []
        if tree is not None:
            reading = _resolved_reading(tree, self.allowed_table_functions)
            if reading.refusal:
                return reading
            tables += reading.tables or ()
        if not self.analyzer_runs:
            try:
                parsed = self._get(f"{PARSED} {sql}", seconds=seconds, settings={"default_format": "TSVRaw"})
            except TimedOut:
                raise
            except AdapterError as error:
                return EngineReading(refusal=f"ClickHouse cannot read it as a query: {error}")
            reading, dotted, ctes = _parsed_reading(parsed, self.settings.database, self.allowed_table_functions)
            if reading.refusal:
                return reading
            if tree is None and (refusal := self._one_name_with_a_dot(dotted)):
                return EngineReading(refusal=refusal)
            # The analyzer of these servers takes a CTE for the table of its name where it holds the column (inside
            # `IN (SELECT …)`, measured on 22.11): a table of the connection's database a CTE of the query is named
            # after is read as the parsed tree reads it — where that names it, it is there.
            parsed_tables = reading.tables or ()
            database = self.settings.database
            tables = [t for t in tables if not (t[1] == database and t[2] in ctes)]
            tables += parsed_tables
        return EngineReading(tables=tuple(t for t in dict.fromkeys(tables) if t[1:] not in IMPLICIT_TABLES))

    def _one_name_with_a_dot(self, names: set[str]) -> str | None:
        """The parsed tree prints `a.b` for a table `a.b` of the connection's database as for table b of database a;
        where no resolved tree tells them apart, the database is asked whether it holds a table of such a name."""
        if not names:
            return None
        found = self._rows(
            "SELECT name FROM system.tables WHERE database = {database:String} AND has({names:Array(String)}, name)",
            database=self.settings.database,
            names="[" + ",".join("'" + n.replace("\\", "\\\\").replace("'", "\\'") + "'" for n in sorted(names)) + "]",
        )
        if found:
            return (
                f"{self.settings.database} holds a table named {found[0][0]!r}, and ClickHouse's parsed tree names "
                "it as it names a table of another database: the server cannot tell which one the query reads"
            )
        return None

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


def _table_functions_refused(line: str, allowed: frozenset[str]) -> str | None:
    """A table function's line: every name on it allowed, or why not — an alias before the name may hold another."""
    names = TABLE_FUNCTION_NAME.findall(line)
    refused = [name for name in names if name.lower() not in allowed]
    if refused or not names:
        return f"ClickHouse reads it with the table function {(refused or ['?'])[0]}, which is not allowed here"
    return None


def _resolved_reading(tree: str, allowed: frozenset[str]) -> EngineReading:
    """The tables a resolved tree names, every one: a table's line it cannot read refuses the query."""
    first = tree.lstrip().split(" ", 1)[0]
    if first not in ("QUERY", "UNION"):
        return EngineReading(refusal=f"ClickHouse reads it as {first}, not a SELECT")
    if UNRESOLVED_IN_TREE.search(tree):
        return EngineReading(refusal=UNRESOLVED)
    tables = []
    for line in (line.strip() for line in tree.splitlines()):
        if line.startswith("TABLE id: "):
            table = TABLE_LINE.fullmatch(line)
            if table is None:
                return EngineReading(refusal=f"ClickHouse names a table the server cannot read: {line[:200]}")
            tables.append((None, table.group(1), table.group(2)))
        elif line.startswith("TABLE_FUNCTION id: ") and (refusal := _table_functions_refused(line, allowed)):
            return EngineReading(refusal=refusal)
    return EngineReading(tables=tuple(tables))


class _Unreadable(Exception):
    """The parsed tree, or a name in it, cannot be read with certainty."""


@dataclass
class _Line:
    kind: str  # a node's kind (QUERY, LIST, IDENTIFIER, …) or a section's title (WITH, JOIN TREE, …)
    text: str
    node: bool
    children: list[_Line] = field(default_factory=list)


def _parsed_tree(text: str) -> _Line:
    """The parsed tree's lines as a tree — each line a node numbered next, or a section's title; a node's children
    sections, a section's or a list's children nodes, each a level deeper than its parent."""
    root: _Line | None = None
    stack: list[_Line] = []
    number = 0
    for raw in text.splitlines():
        line = raw.lstrip(" ")
        indent = len(raw) - len(line)
        node = NODE_LINE.fullmatch(line)
        if indent % 2 or not (node or SECTION_LINE.fullmatch(line)):
            raise _Unreadable(UNREADABLE_TREE)
        if node:
            if int(node.group(2)) != number:
                raise _Unreadable(UNREADABLE_TREE)
            number += 1
        item = _Line(node.group(1) if node else line, line, node is not None)
        depth = indent // 2
        if root is None:
            if depth or not item.node:
                raise _Unreadable(UNREADABLE_TREE)
            root = item
        else:
            if not 1 <= depth <= len(stack):
                raise _Unreadable(UNREADABLE_TREE)
            del stack[depth:]
            parent = stack[-1]
            if item.node == (parent.node and parent.kind != "LIST"):
                raise _Unreadable(UNREADABLE_TREE)
            parent.children.append(item)
        stack.append(item)
    if root is None:
        raise _Unreadable(UNREADABLE_TREE)
    return root


def _parsed_reading(text: str, database: str, allowed: frozenset[str]) -> tuple[EngineReading, set[str], set[str]]:
    """The tables the parsed tree names, its names resolved by the rules of the interpreter that runs the query; the
    names it printed with a dot, which a table of the connection's database could be named too; and its CTEs' names."""
    tables: list[tuple[str | None, str, str]] = []
    dotted: set[str] = set()
    named_ctes: set[str] = set()

    def table(item: _Line, ctes: frozenset[str], aliases: frozenset[str]) -> None:
        name = NAME_LINE.fullmatch(item.text)
        if name is None:
            raise _Unreadable(f"ClickHouse names a table the server cannot read on this version: {item.text[:200]}")
        parts = name.group(1).split(".")
        if len(parts) > 2:
            raise _Unreadable(f"ClickHouse names {name.group(1)}: a name with more than one dot")
        if len(parts) == 2:
            tables.append((None, parts[0], parts[1]))
            dotted.add(name.group(1))
        elif parts[0] not in ctes and parts[0] not in aliases:
            tables.append((None, database, parts[0]))

    def query(item: _Line, ctes: frozenset[str]) -> None:
        sections = item.children
        titles = [section.kind for section in sections]
        if "WITH" in titles[1:]:
            raise _Unreadable(UNREADABLE_TREE)
        own: list[str] = []
        aliases: set[str] = set()
        if titles[:1] == ["WITH"]:
            elements = _only_list(sections[0])
            for element in elements:
                cte = (CTE_LINE if element.kind == "QUERY" else CTE_UNION_LINE).fullmatch(element.text)
                if cte is None and "is_cte" in element.text:
                    raise _Unreadable(UNREADABLE_TREE)
                walk(element, ctes | frozenset(own), frozenset())  # a CTE sees those defined before it
                if cte:
                    own.append(cte.group(1))
                    named_ctes.add(cte.group(1))
                elif alias := _alias(element):
                    aliases.add(alias)
            sections = sections[1:]
        projection = [section for section in sections if section.kind == "PROJECTION"]
        if projection:
            aliases.update(alias for element in _only_list(projection[0]) if (alias := _alias(element)))
        for section in sections:
            walk(section, ctes | frozenset(own), frozenset(aliases))

    def walk(item: _Line, ctes: frozenset[str], aliases: frozenset[str]) -> None:
        if item.node:
            if item.kind == "LIST":
                _listed(item)
            elif len({child.kind for child in item.children}) != len(item.children):
                raise _Unreadable(UNREADABLE_TREE)  # a section twice
            if item.kind == "QUERY":
                query(item, ctes)
                return
            if item.kind == "TABLE_FUNCTION" and (refusal := _table_functions_refused(item.text, allowed)):
                raise _Unreadable(refusal)
            if item.kind == "FUNCTION" and {n.lower() for n in FUNCTION_NAME.findall(item.text)} & IN_FUNCTIONS:
                arguments = [child for child in item.children if child.kind == "ARGUMENTS"]
                values = _only_list(arguments[0]) if arguments else []
                if len(values) > 1 and values[1].kind == "IDENTIFIER":
                    table(values[1], ctes, aliases)  # right of IN, a name is a table's — a column's is an error
        elif item.kind in TABLE_PLACES:
            for child in item.children:
                if child.kind == "IDENTIFIER":
                    table(child, ctes, frozenset())
                else:
                    walk(child, ctes, aliases)
            return
        for child in item.children:
            walk(child, ctes, aliases)

    try:
        root = _parsed_tree(text)
        if root.kind not in ("QUERY", "UNION"):
            return EngineReading(refusal=f"ClickHouse reads it as {root.kind}, not a SELECT"), dotted, named_ctes
        walk(root, frozenset(), frozenset())
    except _Unreadable as error:
        return EngineReading(refusal=str(error)), dotted, named_ctes
    return EngineReading(tables=tuple(tables)), dotted, named_ctes


def _alias(item: _Line) -> str | None:
    """The alias a node's line gives, where the line says it with certainty: a bare word, the line holding one alias and
    one of the field that follows it — a quoted alias holding `, key: ` makes two."""
    after = AFTER_ALIAS.get(item.kind)
    alias = re.match(rf"{item.kind} id: \d+, alias: ([A-Za-z_][A-Za-z0-9_]*), {after}: ", item.text) if after else None
    if alias is None or item.text.count(", alias: ") != 1 or item.text.count(f", {after}: ") != 1:
        return None
    return alias.group(1)


def _listed(item: _Line) -> list[_Line]:
    """A list's nodes, as many as it says it holds."""
    listed = LIST_LINE.fullmatch(item.text)
    if listed is None or int(listed.group(1) or 0) != len(item.children):
        raise _Unreadable(UNREADABLE_TREE)
    return item.children


def _only_list(section: _Line) -> list[_Line]:
    """The nodes of a section holding one list."""
    if len(section.children) != 1 or section.children[0].kind != "LIST":
        raise _Unreadable(UNREADABLE_TREE)
    return _listed(section.children[0])


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
