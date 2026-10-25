"""MySQL: every query in a read-only transaction with its execution time limited, read as it comes and stopped one row
past what is shown; text MySQL runs that the guard reads as a comment (`/*! … */`, `/*+ … */`) refused."""

from __future__ import annotations

import json
import os
import pathlib
import ssl
import threading
from typing import Annotated, Any, Literal

from pydantic import Field

from . import Adapter, AdapterError, Column, EngineReading, EngineSettings, Result, Table, TimedOut, missing_driver

try:
    import pymysql
    import pymysql.cursors
    from pymysql.constants import FIELD_TYPE
except ImportError:  # the `mysql` extra is not installed: `check` says so
    pymysql = None

# The name of an environment variable — never its value.
EnvName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]

# Functions a query may not call (lower case). A read-only transaction stops every write to a table; these do what it
# does not stop: read the server's files (`load_file`), take or wait on locks, sleep or burn time, wait on replication,
# advance the server's own counter (`uuid_short`), run programs (`sys_exec` / `sys_eval`, which some servers add).
DENIED_FUNCTIONS = frozenset(
    {
        "load_file", "sleep", "benchmark", "get_lock", "release_lock", "release_all_locks", "is_free_lock",
        "is_used_lock", "master_pos_wait", "source_pos_wait", "wait_for_executed_gtid_set",
        "wait_until_sql_thread_after_gtids", "uuid_short", "sys_exec", "sys_eval",
    }
)  # fmt: skip

# The modes of `sql_mode` that change how MySQL reads a query's text — where a string ends (`NO_BACKSLASH_ESCAPES`),
# what a double quote holds (`ANSI_QUOTES`, and `ANSI`, which sets it). The guard reads a query as MySQL does with none
# of them, so each session sets them off: otherwise text the guard reads inside a string, MySQL could run.
TEXT_MODES = frozenset({"NO_BACKSLASH_ESCAPES", "ANSI_QUOTES", "ANSI"})

# Privileges a connection's user may not hold: FILE reads and writes the server's files, SUPER passes over its
# read-only settings.
REFUSED_PRIVILEGES = ("FILE", "SUPER")

# MySQL's own schemas: not the data's.
SYSTEM_SCHEMAS = ("mysql", "sys", "information_schema", "performance_schema")

CONNECT_SECONDS = 10
# Past a query's own time, how long before the server is told to stop it from another connection.
CANCEL_MARGIN_SECONDS = 5

# Text MySQL runs though it is written as a comment: `/*! … */` is SQL to MySQL (its content runs, unseen by the
# guard), `/*+ … */` sets a statement's limits (`MAX_EXECUTION_TIME`, `SET_VAR`).
RUN_AS_COMMENTS = ("/*!", "/*+")

# A query option that makes the server read every row past the limit, to count them.
READS_EVERY_ROW = "SQL_CALC_FOUND_ROWS"


class MySQLSettings(EngineSettings):
    host: str
    port: Annotated[int, Field(gt=0, lt=65536)] = 3306
    database: str  # the schema an unqualified table name resolves to
    user_env: EnvName  # the variable holding the user's name
    password_env: EnvName | None = None  # the variable holding the password
    tls: Literal["off", "on", "verify"] = "on"  # on: encrypted; verify: the server's certificate checked too
    # with `verify`: a CA file, relative to the configuration's folder; the system's otherwise
    tls_ca: str | None = None


class MySQLAdapter(Adapter):
    engine = "mysql"
    dialect = "mysql"
    settings_model = MySQLSettings
    allowed_table_functions = frozenset({"json_table", "jsontable"})
    denied_functions = DENIED_FUNCTIONS

    def __init__(self, settings: MySQLSettings, base_dir: pathlib.Path) -> None:
        super().__init__(settings, base_dir)
        self._plans_name_tables = False  # explain_json_format_version = 2 (MySQL 8.3+) names schema and table

    @property
    def default_schema(self) -> str:
        return self.settings.database  # type: ignore[attr-defined]

    def _connect(self, streaming: bool = False):
        settings: MySQLSettings = self.settings  # type: ignore[assignment]
        user = os.environ.get(settings.user_env)
        if not user:
            raise AdapterError(f"the variable {_named(settings.user_env, '`user_env`')} is not set")
        password = ""
        if settings.password_env is not None:
            password = os.environ.get(settings.password_env)
            if password is None:
                raise AdapterError(f"the variable {_named(settings.password_env, '`password_env`')} is not set")
        # A context of the server's own making: PyMySQL turns verification off for a dictionary without a CA file.
        tls: ssl.SSLContext | None = None
        if settings.tls == "on":
            tls = ssl.create_default_context()
            tls.check_hostname = False
            tls.verify_mode = ssl.CERT_NONE
        elif settings.tls == "verify":
            ca = str((self.base_dir / settings.tls_ca).resolve()) if settings.tls_ca else None
            tls = ssl.create_default_context(cafile=ca)  # the certificate and the host name checked
        try:
            connection = pymysql.connect(
                host=settings.host, port=settings.port, user=user, password=password, database=settings.database,
                connect_timeout=CONNECT_SECONDS, local_infile=False, autocommit=True, charset="utf8mb4",
                ssl=tls, ssl_disabled=settings.tls == "off",
                cursorclass=pymysql.cursors.SSCursor if streaming else pymysql.cursors.Cursor,
                # every transaction read-only, from the session's first statement on
                init_command="SET SESSION TRANSACTION READ ONLY",
            )  # fmt: skip
        except (pymysql.Error, OSError, AttributeError) as error:  # PyMySQL fails some handshakes with AttributeError
            where = f"{settings.host}:{settings.port}/{settings.database}"
            raise AdapterError(f"cannot reach {where}: {_said(error)}") from None
        try:
            with connection.cursor(pymysql.cursors.Cursor) as cursor:  # read whole, even on a streaming connection
                cursor.execute("SELECT @@SESSION.sql_mode")
                modes = [mode for mode in (cursor.fetchone()[0] or "").split(",") if mode and mode not in TEXT_MODES]
                cursor.execute("SET SESSION sql_mode = %s", [",".join(modes)])
        except pymysql.Error as error:
            _close(connection)
            raise AdapterError(f"the session's sql_mode could not be set: {_said(error)}") from None
        return connection

    def check(self) -> None:
        if pymysql is None:
            raise missing_driver("mysql", "pymysql")
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT CURRENT_USER(), @@session.transaction_read_only, @@SESSION.sql_mode")
                user, read_only, sql_mode = cursor.fetchone()
                cursor.execute("SHOW GRANTS")
                grants = [row[0] for row in cursor.fetchall()]
                try:
                    cursor.execute("SET SESSION explain_json_format_version = 2")
                    self._plans_name_tables = True
                except pymysql.Error:
                    self._plans_name_tables = False  # before 8.3: a plan names tables by their aliases only
        except pymysql.Error as error:
            raise AdapterError(_said(error)) from None
        finally:
            connection.close()
        held = _global_privileges(grants)
        refused = [privilege for privilege in REFUSED_PRIVILEGES if privilege in held or "ALL PRIVILEGES" in held]
        if refused:
            raise AdapterError(
                f"the user {user} holds {', '.join(refused)} on every database: FILE reads and writes the server's "
                "files, SUPER passes over its read-only settings — give the server a user that only reads (GRANT "
                "SELECT on what agents may read)"
            )
        if not read_only:
            raise AdapterError("the server did not make the session read-only; it is not used")
        if set((sql_mode or "").split(",")) & TEXT_MODES:
            raise AdapterError(f"the session kept {sql_mode}: MySQL would read a query's text otherwise than the guard")

    def granted_tables(self) -> list[tuple[str, str]]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                # information_schema shows the tables the user holds a privilege on
                cursor.execute(
                    "SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.TABLES "
                    f"WHERE TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS} ORDER BY 1, 2"
                )
                return [(row[0], row[1]) for row in cursor.fetchall()]
        finally:
            connection.close()

    def engine_reading(self, sql: str, seconds: float, named: tuple[str, ...] = ()) -> EngineReading:
        """MySQL's plan of the query (`EXPLAIN FORMAT=JSON`, never run). From MySQL 8.3 it names each table it reads —
        a view's own tables in its place (those of the views the query names are left to them); a table MySQL reads
        while planning (a single row, a lookup by a unique key) is not in it, the guard's reading holds that one.
        Before 8.3 a plan names aliases only: the reading says only whether MySQL can plan the query."""
        shown = next((mark for mark in RUN_AS_COMMENTS if mark in sql), None)
        if shown is not None:
            return EngineReading(
                refusal=f"it holds {shown} … */, which MySQL runs though it reads as a comment — write the query "
                "without it"
            )
        if READS_EVERY_ROW in sql.upper():
            return EngineReading(refusal=f"{READS_EVERY_ROW} makes MySQL read every row past the limit — leave it out")
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                _limit(cursor, seconds)
                if self._plans_name_tables:
                    cursor.execute("SET SESSION explain_json_format_version = 2")
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(f"EXPLAIN FORMAT=JSON {sql}")
                plan = json.loads(cursor.fetchone()[0])
                if not self._plans_name_tables:
                    return EngineReading()
                relations: set[tuple[str, str]] = set()
                _walk(plan, relations)
                through_views = _through_views(cursor, named, self.default_schema)
        except pymysql.err.OperationalError as error:
            if error.args and error.args[0] in (3024, 1317):
                raise TimedOut(f"planning stopped after {seconds:g} s") from None
            return EngineReading(refusal=f"MySQL cannot plan it: {_said(error)}")
        except pymysql.Error as error:
            return EngineReading(refusal=f"MySQL cannot plan it: {_said(error)}")
        finally:
            connection.close()
        tables = sorted((None, schema, table) for schema, table in relations - through_views)
        return EngineReading(tables=tuple(tables))

    def run(self, sql: str, max_rows: int, seconds: float) -> Result:
        if any(mark in sql for mark in RUN_AS_COMMENTS):  # the reading refuses it first; never run past it
            raise AdapterError("a query holding /*! … */ or /*+ … */ is not run")
        connection = self._connect(streaming=True)
        thread = connection.thread_id()
        timer = threading.Timer(seconds + CANCEL_MARGIN_SECONDS, self._stop, [thread])
        stopped_here = False
        try:
            with connection.cursor() as cursor:
                _limit(cursor, seconds)
                cursor.execute(f"SET SESSION sql_select_limit = {max_rows + 1}")
                cursor.execute("START TRANSACTION READ ONLY")
                timer.start()
                cursor.execute(sql)
                rows = cursor.fetchmany(max_rows + 1)
                columns = [Column(name=item[0], type=_type(item[1])) for item in cursor.description or []]
                if len(rows) > max_rows:  # more were coming: the server stops making them
                    stopped_here = True
                    self._stop(thread)
        except pymysql.err.OperationalError as error:
            code = error.args[0] if error.args else None
            if stopped_here and code == 1317:
                pass  # the rest of an answer cut on purpose
            elif code in (3024, 1317):
                raise TimedOut(f"stopped after {seconds:g} s") from None
            else:
                raise AdapterError(_said(error)) from None
        except pymysql.Error as error:
            raise AdapterError(_said(error)) from None
        finally:
            timer.cancel()
            _close(connection)
        return Result(columns=columns, rows=[tuple(row) for row in rows[:max_rows]], more_rows=len(rows) > max_rows)

    def _stop(self, thread: int) -> None:
        """Stop a query from another connection: its user may stop its own (`KILL QUERY`)."""
        try:
            connection = self._connect()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("KILL QUERY %s", [thread])
            finally:
                connection.close()
        except (AdapterError, pymysql.Error):
            pass  # the query has ended, or the server is gone: its own time limit stops it

    def tables(self) -> list[Table]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, TABLE_COMMENT FROM information_schema.TABLES "
                    f"WHERE TABLE_SCHEMA NOT IN {SYSTEM_SCHEMAS} ORDER BY 1, 2"
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        return [
            Table(schema=row[0], name=row[1], kind="view" if row[2] == "VIEW" else "table",
                  comment=row[3] if row[3] and row[2] != "VIEW" else None)
            for row in rows
        ]  # fmt: skip

    def columns(self, schema: str, table: str) -> list[Column]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE = 'YES', COLUMN_COMMENT "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
                    [schema, table],
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        return [Column(name=row[0], type=row[1], nullable=bool(row[2]), comment=row[3] or None) for row in rows]


def _limit(cursor: Any, seconds: float) -> None:
    """The session's limit on a SELECT's execution time: the server stops a query past the connection's time."""
    cursor.execute(f"SET SESSION max_execution_time = {max(1, int(seconds * 1000))}")


def _walk(node: Any, found: set[tuple[str, str]]) -> None:
    """The tables a plan (format 2) reads: every node naming one with its schema; derived tables (`<derived2>`) not."""
    if isinstance(node, dict):
        schema, table = node.get("schema_name"), node.get("table_name")
        if isinstance(schema, str) and isinstance(table, str) and not table.startswith("<"):
            found.add((schema, table))
        for value in node.values():
            _walk(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk(value, found)


def _through_views(cursor: Any, named: tuple[str, ...], default_schema: str) -> set[tuple[str, str]]:
    """The tables the views a query names read, at any depth — they appear in a plan in their views' place."""
    pending = [tuple(name.split(".", 1)) if "." in name else (default_schema, name) for name in named]
    seen: set[tuple[str, str]] = set()
    found: set[tuple[str, str]] = set()
    while pending:
        view = pending.pop()
        if view in seen:
            continue
        seen.add(view)
        cursor.execute(
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.VIEW_TABLE_USAGE "
            "WHERE VIEW_SCHEMA = %s AND VIEW_NAME = %s",
            list(view),
        )
        for schema, table in cursor.fetchall():
            found.add((schema, table))
            pending.append((schema, table))
    return found


def _global_privileges(grants: list[str]) -> set[str]:
    """The privileges `SHOW GRANTS` gives on every database (`ON *.*`), upper case."""
    held: set[str] = set()
    for grant in grants:
        head, on, _ = grant.partition(" ON ")
        if on and grant[len(head) + len(on) :].startswith("*.*") and head.upper().startswith("GRANT "):
            held |= {privilege.strip().upper() for privilege in head[len("GRANT ") :].split(",")}
    return held


def _type(code: int) -> str:
    names = {value: name for name, value in vars(FIELD_TYPE).items() if name.isupper() and isinstance(value, int)}
    return names.get(code, str(code)).lower()


def _said(error: Exception) -> str:
    """MySQL's own words for an error, without its code."""
    if error.args and len(error.args) > 1 and isinstance(error.args[1], str):
        return error.args[1]
    return str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__


def _close(connection: Any) -> None:
    """Close without reading what is left of an answer cut on purpose."""
    try:
        connection.close()
    except Exception:  # noqa: BLE001 — a connection whose answer was stopped may not close cleanly; it is gone
        pass


def _named(variable: str, where: str) -> str:
    from .. import files

    return files.variable(variable, where)
