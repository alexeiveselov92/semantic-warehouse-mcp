"""PostgreSQL: every query in a read-only transaction with a statement timeout, read through a cursor no further than
one row past what is shown; the engine's own plan names the tables a query reads."""

from __future__ import annotations

import json
import os
import pathlib
import threading
from typing import Annotated, Any, Literal

from pydantic import Field

from . import Adapter, AdapterError, Column, EngineReading, EngineSettings, Result, Table, TimedOut, missing_driver

try:
    import psycopg
    from psycopg import errors as pg_errors
except ImportError:  # the `postgres` extra is not installed: `check` says so
    psycopg = None

# The name of an environment variable — never its value.
EnvName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]

# Functions a query may not call (lower case, globs for a family). A read-only transaction stops every write to a
# table; these do what it does not stop: maintain an index (`brin_*`, `gin_clean_pending_list` write to it, read-only
# transaction or not), read the server's files, run SQL from a string past the guard (`query_to_xml`, `crosstab`,
# `ts_stat`, `dblink`), reach other servers, signal or stop other sessions, take locks, change settings, export a
# snapshot, show other sessions or the statistics of any table.
DENIED_FUNCTIONS = frozenset(
    {
        "pg_read_file", "pg_read_binary_file", "pg_ls_*", "pg_stat_file", "pg_file_*", "lo_*", "loread", "lowrite",
        "dblink*", "query_to_xml*", "table_to_xml*", "cursor_to_xml*", "schema_to_xml*", "database_to_xml*",
        "query_to_xmlschema", "table_to_xmlschema", "cursor_to_xmlschema", "crosstab*", "ts_stat",
        "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile*", "pg_switch_wal",
        "pg_promote", "pg_create_*", "pg_drop_*", "pg_replication_*", "pg_logical_*", "pg_notify", "pg_backup_*",
        "pg_start_backup", "pg_stop_backup", "pg_wal_replay_*", "pg_stat_reset*", "pg_log_backend_memory_contexts",
        "pg_import_system_collations", "pg_advisory*", "pg_try_advisory*", "pg_sleep*", "set_config", "nextval",
        "setval", "txid_current*", "pg_current_xact_id*", "pg_stat_get_*", "pg_stat_clear_snapshot",
        "pg_stat_force_next_flush", "pg_export_snapshot", "pg_log_*", "pg_sync_replication_slots", "brin_*",
        "gin_clean_pending_list", "pg_prewarm*", "bt_index_*", "verify_heapam", "pg_check_*",
        "pg_truncate_visibility_map",
    }
)  # fmt: skip

# Roles that read or write the server's files, or run programs on it: a connection's user may hold none of them.
SERVER_ROLES = ("pg_read_server_files", "pg_write_server_files", "pg_execute_server_program")

# The relations a role may read, outside the catalogs: a partition goes by the table it belongs to.
USER_RELATIONS = """
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition
      AND n.nspname <> 'information_schema' AND left(n.nspname, 3) <> 'pg_'
"""

# How long reaching the server may take, and the margin past a query's own time before the server stops waiting.
CONNECT_SECONDS = 10
CANCEL_MARGIN_SECONDS = 5


class PostgresSettings(EngineSettings):
    host: str
    port: Annotated[int, Field(gt=0, lt=65536)] = 5432
    database: str
    user_env: EnvName  # the variable holding the user's name
    password_env: EnvName | None = None  # the variable holding the password
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = "prefer"


class PostgresAdapter(Adapter):
    engine = "postgres"
    dialect = "postgres"
    settings_model = PostgresSettings
    allowed_table_functions = frozenset(
        {
            # sqlglot reads PostgreSQL's generate_series as exploding_generate_series
            "generate_series", "exploding_generate_series", "generate_subscripts", "unnest", "regexp_split_to_table",
            "json_each", "json_each_text", "jsonb_each", "jsonb_each_text", "json_array_elements",
            "json_array_elements_text", "jsonb_array_elements", "jsonb_array_elements_text",
        }
    )  # fmt: skip
    denied_functions = DENIED_FUNCTIONS

    def __init__(self, settings: PostgresSettings, base_dir: pathlib.Path) -> None:
        super().__init__(settings, base_dir)
        self._catalog: str | None = None
        self._schema = "public"

    @property
    def default_schema(self) -> str:
        return self._schema

    @property
    def catalog(self) -> str | None:
        return self._catalog

    def _connect(self):
        settings: PostgresSettings = self.settings  # type: ignore[assignment]
        user = os.environ.get(settings.user_env)
        if not user:
            raise AdapterError(f"the variable {_named(settings.user_env, '`user_env`')} is not set")
        password = None
        if settings.password_env is not None:
            password = os.environ.get(settings.password_env)
            if password is None:
                raise AdapterError(f"the variable {_named(settings.password_env, '`password_env`')} is not set")
        try:
            connection = psycopg.connect(
                host=settings.host, port=settings.port, dbname=settings.database, user=user, password=password,
                sslmode=settings.sslmode, connect_timeout=CONNECT_SECONDS, application_name="semantic-warehouse-mcp",
                # every transaction read-only from the first statement on, whatever a query asks
                options="-c default_transaction_read_only=on",
            )  # fmt: skip
        except psycopg.Error as error:
            where = f"{settings.host}:{settings.port}/{settings.database}"
            raise AdapterError(f"cannot reach {where}: {_said(error)}") from None
        connection.read_only = True
        return connection

    def check(self) -> None:
        if psycopg is None:
            raise missing_driver("postgres", "psycopg")
        with self._connect() as connection, connection.transaction():
            row = connection.execute(
                """
                SELECT current_database(), current_schema(), current_user, r.rolsuper,
                       current_setting('transaction_read_only'),
                       array(SELECT role FROM unnest(%s::text[]) AS role
                             WHERE pg_has_role(current_user, role, 'MEMBER')),
                       array(SELECT n.nspname || '.' || c.relname FROM pg_class c
                             JOIN pg_namespace n ON n.oid = c.relnamespace
                             WHERE pg_has_role(current_user, c.relowner, 'USAGE')
                               AND n.nspname <> 'information_schema' AND left(n.nspname, 3) <> 'pg_'
                             ORDER BY 1),
                       r.rolbypassrls, pg_has_role(current_user, 'pg_read_all_data', 'USAGE')
                FROM pg_roles r WHERE r.rolname = current_user
                """,
                [list(SERVER_ROLES)],
            ).fetchone()
        database, schema, user, superuser, read_only, server_roles, owned, bypass_rls, reads_all = row
        if superuser:
            raise AdapterError(
                f"the user {user} is a superuser: it may read the server's files and run programs there, which no "
                "query should reach — give the server a role that only reads (GRANT SELECT on what agents may read)"
            )
        if server_roles:
            raise AdapterError(
                f"the user {user} holds {', '.join(server_roles)}: it may reach the server's files or programs — give "
                "the server a role that only reads"
            )
        if owned:
            shown = ", ".join(owned[:3]) + (f" and {len(owned) - 3} more" if len(owned) > 3 else "")
            raise AdapterError(
                f"the user {user} owns {shown}: an owner may maintain what it owns even in a read-only transaction — "
                "give the server a role that owns nothing and only reads"
            )
        if read_only != "on":
            raise AdapterError("the server did not open a read-only transaction; it is not used")
        if reads_all or bypass_rls:
            held = " and ".join(name for name, has in (("pg_read_all_data", reads_all), ("BYPASSRLS", bypass_rls))
                                if has)  # fmt: skip
            self.notes.append(
                f"the user {user} holds {held}: it reads every table and every row, whatever the database's grants "
                "and row security say — a function defined in the database reads with them, past `readable`"
            )
        self._catalog = database
        self._schema = schema or "public"

    def granted_tables(self) -> list[tuple[str, str]]:
        with self._connect() as connection, connection.transaction():
            rows = connection.execute(
                f"SELECT n.nspname, c.relname {USER_RELATIONS} "
                "AND has_any_column_privilege(c.oid, 'SELECT') ORDER BY 1, 2"
            ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def engine_reading(self, sql: str, seconds: float, named: tuple[str, ...] = ()) -> EngineReading:
        """PostgreSQL's plan of the query (`EXPLAIN`, never run): the relations it reads. A view's own tables appear in
        its place — those of the views the query names, which the guard has held readable, are left out; a partition
        is read as the table it belongs to."""
        try:
            with self._connect() as connection, connection.transaction():
                _limit(connection, seconds)
                plan = connection.execute(f"EXPLAIN (VERBOSE, FORMAT JSON) {sql}").fetchone()[0]
                relations: set[tuple[str, str]] = set()
                _walk(plan if not isinstance(plan, str) else json.loads(plan), relations)
                through_views = _through_views(connection, named)
                tables = set()
                for schema, table in relations - through_views:
                    tables.add(_root(connection, schema, table))
        except pg_errors.QueryCanceled:
            raise TimedOut(f"planning stopped after {seconds:g} s") from None
        except psycopg.Error as error:
            return EngineReading(refusal=f"PostgreSQL cannot plan it: {_said(error)}")
        return EngineReading(tables=tuple(sorted((None, schema, table) for schema, table in tables)))

    def run(self, sql: str, max_rows: int, seconds: float) -> Result:
        with self._connect() as connection:
            timer = threading.Timer(seconds + CANCEL_MARGIN_SECONDS, connection.cancel_safe)
            timer.start()
            try:
                with connection.transaction():
                    _limit(connection, seconds)
                    # A cursor on the server: rows are read as they come, and no further than one past the limit.
                    with connection.cursor(name="swm_answer") as cursor:
                        cursor.execute(sql)
                        rows = cursor.fetchmany(max_rows + 1)
                        described = cursor.description or []
                        columns = [Column(name=item.name, type=_type(connection, item.type_code)) for item in described]
            except pg_errors.QueryCanceled:
                raise TimedOut(f"stopped after {seconds:g} s") from None
            except psycopg.Error as error:
                raise AdapterError(_said(error)) from None
            finally:
                timer.cancel()
        return Result(columns=columns, rows=[tuple(row) for row in rows[:max_rows]], more_rows=len(rows) > max_rows)

    def tables(self) -> list[Table]:
        with self._connect() as connection, connection.transaction():
            rows = connection.execute(
                "SELECT n.nspname, c.relname, CASE WHEN c.relkind IN ('v', 'm') THEN 'view' ELSE 'table' END, "
                f"obj_description(c.oid, 'pg_class') {USER_RELATIONS} ORDER BY 1, 2"
            ).fetchall()
        return [Table(schema=row[0], name=row[1], kind=row[2], comment=row[3]) for row in rows]

    def columns(self, schema: str, table: str) -> list[Column]:
        with self._connect() as connection, connection.transaction():
            rows = connection.execute(
                """
                SELECT a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull,
                       col_description(a.attrelid, a.attnum)
                FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum
                """,
                [schema, table],
            ).fetchall()
        return [Column(name=row[0], type=row[1], nullable=row[2], comment=row[3]) for row in rows]


def _limit(connection: Any, seconds: float) -> None:
    """The transaction's statement timeout: the server stops a query past the connection's time."""
    connection.execute("SELECT set_config('statement_timeout', %s, true)", [str(max(1, int(seconds * 1000)))])


def _walk(node: Any, found: set[tuple[str, str]]) -> None:
    """The relations a plan reads: every node naming one (with VERBOSE, its schema beside it)."""
    if isinstance(node, dict):
        if "Relation Name" in node:
            found.add((node.get("Schema", ""), node["Relation Name"]))
        for value in node.values():
            _walk(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk(value, found)


def _through_views(connection: Any, named: tuple[str, ...]) -> set[tuple[str, str]]:
    """The relations the views a query names read, at any depth — they appear in a plan in their views' place."""
    if not named:
        return set()
    rows = connection.execute(
        """
        WITH RECURSIVE reads(relation) AS (
            SELECT d.refobjid
            FROM unnest(%s::text[]) AS name
            JOIN pg_class v ON v.oid = to_regclass(name) AND v.relkind = 'v'
            JOIN pg_rewrite r ON r.ev_class = v.oid
            JOIN pg_depend d ON d.classid = 'pg_rewrite'::regclass AND d.objid = r.oid
                             AND d.refclassid = 'pg_class'::regclass AND d.refobjid <> v.oid
            UNION
            SELECT d.refobjid
            FROM reads
            JOIN pg_class v ON v.oid = reads.relation AND v.relkind = 'v'
            JOIN pg_rewrite r ON r.ev_class = v.oid
            JOIN pg_depend d ON d.classid = 'pg_rewrite'::regclass AND d.objid = r.oid
                             AND d.refclassid = 'pg_class'::regclass AND d.refobjid <> v.oid
        )
        SELECT n.nspname, c.relname FROM reads JOIN pg_class c ON c.oid = reads.relation
        JOIN pg_namespace n ON n.oid = c.relnamespace
        """,
        [[_quoted(name) for name in named]],
    ).fetchall()
    return {(row[0], row[1]) for row in rows}


def _root(connection: Any, schema: str, table: str) -> tuple[str, str]:
    """The table a partition (or an inheriting table) belongs to, at the top; a table of its own is itself."""
    row = connection.execute(
        """
        WITH RECURSIVE up(relation) AS (
            SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s
            UNION
            SELECT i.inhparent FROM up JOIN pg_inherits i ON i.inhrelid = up.relation
        )
        SELECT n.nspname, c.relname FROM up JOIN pg_class c ON c.oid = up.relation
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = up.relation)
        """,
        [schema, table],
    ).fetchone()
    return (row[0], row[1]) if row else (schema, table)


def _quoted(name: str) -> str:
    """`schema.table` as the guard names it, quoted for `to_regclass` so the name is taken as written."""
    schema, _, table = name.partition(".")
    return '"' + schema.replace('"', '""') + '"."' + table.replace('"', '""') + '"'


def _type(connection: Any, oid: int) -> str:
    info = connection.adapters.types.get(oid)
    return info.name if info is not None else str(oid)


def _said(error: Exception) -> str:
    """The server's own words for an error — its message and its hint (`Perhaps you meant …`)."""
    diagnostic = getattr(error, "diag", None)
    primary = getattr(diagnostic, "message_primary", None)
    hint = getattr(diagnostic, "message_hint", None)
    said = primary or str(error).strip().splitlines()[0]
    return f"{said} — {hint}" if hint else said


def _named(variable: str, where: str) -> str:
    from .. import files

    return files.variable(variable, where)
