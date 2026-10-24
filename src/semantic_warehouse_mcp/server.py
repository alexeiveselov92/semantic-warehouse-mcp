"""The MCP server. Every tool is read-only and says so (dev/DESIGN.md, "The guard")."""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, answers, context, guard
from .adapters import AdapterError, TimedOut
from .config import Config, ConfigError, Connection, load

# Every tool of this server carries these annotations; a test holds it.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

CONFIG_VARIABLE = "SEMANTIC_WAREHOUSE_MCP_CONFIG"

INSTRUCTIONS = (
    "semantic-warehouse-mcp answers questions about data in SQL databases, and never changes them. "
    "Start with `list_connections`, then `list_tables` and `describe_table` to see what a connection holds; "
    "`run_query` runs one read-only SELECT. Each query is checked first: a refusal says why and how to fix it. "
    "A large table must be bounded by its column (shown in `list_connections` and `describe_table`). "
    "The team that runs this server may give instructions (below, and in `list_connections`) and context documents "
    "(`list_context`, `get_context`) — read the ones that bear on the question before querying. "
    "Every answer carries the SQL as it ran and the time — cite them."
)


def instructions(config: Config | None) -> str:
    """The server's instructions, and the team's own after them, as written: for every connection, then for each."""
    parts = [INSTRUCTIONS]
    if config and config.instructions:
        parts.append(f"Instructions for every connection:\n{config.instructions.strip()}")
    for connection in config.connections.values() if config else ():
        if connection.instructions:
            parts.append(f"Instructions for the connection `{connection.name}`:\n{connection.instructions.strip()}")
    return "\n\n".join(parts)


def rules(connection: Connection) -> guard.Rules:
    """What the guard enforces on a connection: its configuration, and its engine's dialect and functions."""
    adapter = connection.adapter
    return guard.Rules(
        dialect=adapter.dialect,
        default_schema=adapter.default_schema,
        readable=connection.readable,
        large_tables=tuple(guard.LargeTable(large.table, large.bound_by) for large in connection.large_tables),
        catalog=adapter.catalog,
        allowed_table_functions=adapter.allowed_table_functions,
        denied_functions=adapter.denied_functions,
        table_reading_functions=adapter.table_reading_functions,
    )


def build_server(config: Config | None = None) -> MCPServer:
    """The server with its tools — built fresh, so tests and transports each get their own."""
    server = MCPServer(name="semantic-warehouse-mcp", version=__version__, instructions=instructions(config))
    connections = config.connections if config else {}
    server_context = config.context if config else ()
    answer_characters = config.limits.answer_characters if config else 40_000
    connection_rules = {name: rules(connection) for name, connection in connections.items()}

    def pick(name: str | None) -> Connection:
        names = ", ".join(connections) or "none configured"
        if name is None:
            if len(connections) == 1:
                return next(iter(connections.values()))
            raise ToolError(f"Name the connection: {names}.")
        if name not in connections:
            raise ToolError(f"No connection named {name!r}. Connections: {names}.")
        return connections[name]

    @server.tool(annotations=READ_ONLY)
    def about() -> dict:
        """What this server is, its version and status. Call it to check the connection."""
        return {
            "name": "semantic-warehouse-mcp",
            "version": __version__,
            "status": "pre-alpha: tables and read-only queries; the semantic layer is being built",
            "read_only": True,
        }

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_connections() -> str:
        """The databases this server reads. For each connection: its name, engine and description; the team's
        instructions for it, as written; which tables are readable (schema.table patterns); which large tables a query
        must bound by which column; the limits on rows and seconds per query; the names of its context documents
        (read them with `get_context`)."""
        items = []
        for connection in connections.values():
            item = {"name": connection.name, "engine": connection.engine}
            if connection.description:
                item["description"] = connection.description
            if connection.instructions:
                item["instructions"] = connection.instructions
            item["readable"] = list(connection.readable)
            if connection.large_tables:
                item["large_tables"] = [
                    {"table": large.table, "bound_by": large.bound_by} for large in connection.large_tables
                ]
            item["limits"] = {"rows": connection.limits.rows, "seconds": connection.limits.seconds}
            if connection.context:
                item["context"] = list(context_documents(connection.name))
            items.append(item)
        answer = {}
        if config and config.instructions:
            answer["instructions"] = config.instructions
        if server_context:
            answer["context"] = list(context_documents(None))
        return answers.render(answer, "connections", items, answer_characters, "")

    def context_documents(connection: str | None) -> dict:
        """A scope's documents by name — the whole server's (None) or a connection's — read anew from disk."""
        paths = server_context if connection is None else connections[connection].context
        try:
            return context.documents(paths)
        except context.ContextError as error:
            raise ToolError(f"The context documents cannot be read: {error}") from error

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_context(connection: str | None = None) -> str:
        """The context documents the team wrote for agents — rules, definitions, how its data is made — with each
        document's title and length. Those for the whole server have no `connection`; give `connection` to list only
        the whole server's and that connection's."""
        if connection is not None:
            pick(connection)
        items = []
        for scope in [None, *connections]:
            if connection is not None and scope not in (None, connection):
                continue
            for name, path in context_documents(scope).items():
                text = path.read_text(encoding="utf-8", errors="replace")
                item = {"name": name, "title": context.title(text), "characters": len(text)}
                if scope is not None:
                    item["connection"] = scope
                items.append(item)
        return answers.render({}, "documents", items, answer_characters, "Give `connection` to list fewer.")

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def get_context(name: str, connection: str | None = None, offset: int = 0) -> str:
        """One context document, as the team wrote it. `name` as `list_context` shows it; `connection` for a
        connection's document (left out, the whole server's — or the only document of that name). A long document
        comes in parts: call again with `offset` set to the answer's `next_offset`."""
        scopes = [connection] if connection is not None else [None, *connections]
        if connection is not None:
            pick(connection)
        found = [(scope, context_documents(scope)[name]) for scope in scopes if name in context_documents(scope)]
        if not found:
            raise ToolError(f"No context document named {name!r}. list_context shows them.")
        if len(found) > 1 and found[0][0] is not None:
            owners = ", ".join(scope for scope, _ in found)
            raise ToolError(f"{name!r} is a document of more than one connection ({owners}): give `connection`.")
        scope, path = found[0]
        text = path.read_text(encoding="utf-8", errors="replace")
        if not 0 <= offset <= len(text):
            raise ToolError(f"`offset` must be between 0 and {len(text)}, the document's length.")
        answer: dict = {"name": name}
        if scope is not None:
            answer["connection"] = scope
        answer |= {"characters": len(text), "offset": offset}
        size = len(text) - offset
        while True:
            part = {**answer, "text": text[offset : offset + size]}
            if offset + size < len(text):
                part["next_offset"] = offset + size
            rendered = answers.dumps(part)
            if len(rendered) <= answer_characters or size <= 1:
                return rendered
            size = size * 9 // 10

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_tables(connection: str | None = None) -> str:
        """The readable tables and views of a connection, with their comments as the database holds them. A large
        table shows `bound_by`: the column a query over it must bound from below. `connection` may be left out when
        there is only one."""
        chosen = pick(connection)
        connection_rule = connection_rules[chosen.name]
        try:
            tables = chosen.adapter.tables()
        except AdapterError as error:
            raise ToolError(f"The database ({chosen.name}) returned an error: {error}") from error
        items = []
        for table in tables:
            if not guard.is_readable(table.schema, table.name, connection_rule):
                continue
            item = {"table": f"{table.schema}.{table.name}", "kind": table.kind}
            if table.comment:
                item["comment"] = table.comment
            bound = guard.large_table_bound(table.schema, table.name, connection_rule)
            if bound:
                item["bound_by"] = bound
            items.append(item)
        return answers.render(
            {"connection": chosen.name}, "tables", items, answer_characters, "Name a table in describe_table."
        )

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def describe_table(table: str, connection: str | None = None) -> str:
        """A readable table's columns in order — name, type, whether it can be null, and the column's comment as the
        database holds it — with the table's own comment, and `bound_by` when it is a large table. `table` is
        `schema.table`, or a table of the default schema."""
        chosen = pick(connection)
        connection_rule = connection_rules[chosen.name]
        try:
            schema, name = guard.resolve_table(table, connection_rule)
        except guard.Refused as refusal:
            raise ToolError(f"Refused ({refusal.code}): {refusal.message}.") from refusal
        try:
            columns = chosen.adapter.columns(schema, name)
            listed = [
                t
                for t in chosen.adapter.tables()
                if guard.stored_name(t.schema, connection_rule) == schema
                and guard.stored_name(t.name, connection_rule) == name
            ]
        except AdapterError as error:
            raise ToolError(f"The database ({chosen.name}) returned an error: {error}") from error
        if not columns:
            raise ToolError(f"No table {schema}.{name} on {chosen.name}. list_tables shows the readable ones.")
        answer: dict = {"connection": chosen.name, "table": f"{schema}.{name}"}
        if listed:
            answer["kind"] = listed[0].kind
            if listed[0].comment:
                answer["comment"] = listed[0].comment
        bound = guard.large_table_bound(schema, name, connection_rule)
        if bound:
            answer["bound_by"] = bound
        items = []
        for column in columns:
            item = {"name": column.name, "type": column.type}
            if column.nullable is not None:
                item["nullable"] = column.nullable
            if column.comment:
                item["comment"] = column.comment
            items.append(item)
        return answers.render(answer, "columns", items, answer_characters, "")

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def run_query(sql: str, connection: str | None = None) -> str:
        """Run one read-only SELECT (WITH, UNION, joins, subqueries allowed) in the connection's SQL dialect.

        The query is checked before it runs: only readable tables, a large table bounded from below by its column,
        no writes, settings or file and network functions. A refusal says why and how to fix it.

        Answers `columns` (name, type), `rows` as arrays in column order, and the provenance: `connection`, `sql` as
        it ran, `started_at` (UTC), `seconds`, `row_count`, `truncated`. Rows past the connection's limit are not
        read — `truncated` is then true and `note` says so: aggregate, filter or add LIMIT. Numbers are exact; a
        decimal JSON cannot hold exactly comes as a string. `connection` may be left out when there is only one."""
        chosen = pick(connection)
        try:
            checked = guard.check(sql, connection_rules[chosen.name])
            reading = chosen.adapter.engine_reading(checked.sql, seconds=chosen.limits.seconds)
            if reading.refusal:
                raise guard.Refused("engine_parse", reading.refusal)
            if reading.tables is not None:
                guard.check_engine_tables(reading.tables, checked, connection_rules[chosen.name])
        except AdapterError as error:
            raise ToolError(f"The database ({chosen.name}) could not read the query: {error}") from error
        except guard.Refused as refusal:
            raise ToolError(f"Refused by the guard ({refusal.code}): {refusal.message}.") from refusal

        limits = chosen.limits
        started_at = datetime.datetime.now(datetime.UTC)
        start = time.monotonic()
        try:
            result = chosen.adapter.run(checked.sql, max_rows=limits.rows, seconds=limits.seconds)
        except TimedOut as error:
            raise ToolError(
                f"Stopped after {limits.seconds:g} s, the time limit of {chosen.name}. Narrow the query: fewer days, "
                "fewer columns, an aggregate."
            ) from error
        except AdapterError as error:
            elapsed = time.monotonic() - start
            raise ToolError(f"The database ({chosen.name}) returned an error after {elapsed:.3f} s: {error}") from error
        elapsed = time.monotonic() - start

        answer: dict = {
            "connection": chosen.name,
            "sql": checked.sql,
            "started_at": started_at.isoformat(timespec="seconds"),
            "seconds": round(elapsed, 3),
            "columns": [{"name": column.name, "type": column.type} for column in result.columns],
            "row_count": len(result.rows),
            "truncated": result.more_rows,
        }
        if result.more_rows:
            answer["truncated_by"] = ["rows"]
            answer["note"] = (
                f"The query returned more than {limits.rows} rows, the limit of {chosen.name}; only the first "
                f"{limits.rows} were read. Aggregate, filter, or add LIMIT."
            )
        rows = [[answers.value(item) for item in row] for row in result.rows]
        return answers.render(answer, "rows", rows, answer_characters, "Select fewer columns, aggregate, or add LIMIT.")

    return server


def main(argv: list[str] | None = None) -> None:
    """The console entry point: check the configuration, then serve over stdio."""
    parser = argparse.ArgumentParser(
        prog="semantic-warehouse-mcp",
        description="A read-only MCP server for SQL databases with their semantics. Serves over stdio.",
    )
    parser.add_argument("--config", help=f"the configuration file (or set {CONFIG_VARIABLE})")
    parser.add_argument("--check", action="store_true", help="check the configuration, reach every database, exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    path = args.config or os.environ.get(CONFIG_VARIABLE)
    if not path:
        parser.error(f"no configuration: pass --config <file> or set {CONFIG_VARIABLE}")
    try:
        config = load(path)
    except ConfigError as error:
        print(f"semantic-warehouse-mcp: {error}", file=sys.stderr)
        sys.exit(2)
    if args.check:
        print(f"The configuration {config.source} is fine:")
        for connection in config.connections.values():
            readable = ", ".join(connection.readable)
            print(f"  - {connection.name} ({connection.engine}): reached; readable {readable}")
        return
    build_server(config).run("stdio")


if __name__ == "__main__":
    main()
