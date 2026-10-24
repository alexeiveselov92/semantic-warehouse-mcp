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

from . import __version__, answers, context, guard, semantic, serving
from .adapters import AdapterError, TimedOut
from .config import Config, ConfigError, Connection, load
from .semantic import Model

# Every tool of this server carries these annotations; a test holds it.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

CONFIG_VARIABLE = "SEMANTIC_WAREHOUSE_MCP_CONFIG"

INSTRUCTIONS = (
    "semantic-warehouse-mcp answers questions about data in SQL databases, and never changes them. "
    "Start with `list_connections`. Where a connection has a semantic model, find the data in it first: "
    "`list_datasets` and `describe_dataset` (what each table means, its fields, keys and relationships, the team's own "
    "metadata with what its values mean), `list_metrics` and `describe_metric` (how a number is defined) — use a "
    "metric's definition rather than writing your own. `list_tables` and `describe_table` show every readable table "
    "as the database holds it. `run_query` runs one read-only SELECT. Each query is checked first: a refusal says why "
    "and how to fix it. A large table must be bounded by its column (shown in `list_connections`, `describe_dataset` "
    "and `describe_table`). "
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


def build_server(config: Config | None = None) -> MCPServer:
    """The server with its tools — built fresh, so tests and transports each get their own."""
    server = MCPServer(name="semantic-warehouse-mcp", version=__version__, instructions=instructions(config))
    connections = config.connections if config else {}
    server_context = config.context if config else ()
    vocabularies = config.vocabularies if config else {}
    answer_characters = config.limits.answer_characters if config else 40_000
    connection_rules = {name: connection.rules for name, connection in connections.items()}

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
            "status": "pre-alpha: tables, read-only queries, semantic models in Apache Ossie; metrics are being built",
            "read_only": True,
        }

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_connections() -> str:
        """The databases this server reads. For each connection: its name, engine and description; the team's
        instructions for it, as written; which tables are readable (schema.table patterns); which large tables a query
        must bound by which column; the limits on rows and seconds per query; the names of its semantic models (their
        datasets in `list_datasets`) and of its context documents (read them with `get_context`)."""
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
            if connection.models:
                item["semantic_models"] = [model.name for model in connection.models]
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

    # --- the semantic models ------------------------------------------------------------------------------------------

    def models_of(connection: str | None, model: str | None) -> list[tuple[Connection, Model]]:
        """The models a semantic tool looks in: of the connection named, or of every one; of the model named, or all."""
        scope = [pick(connection)] if connection is not None else list(connections.values())
        found = [(c, m) for c in scope for m in c.models if model is None or m.name == model]
        if model is not None and not found:
            names = ", ".join(sorted({m.name for c in scope for m in c.models})) or "none"
            raise ToolError(f"No semantic model named {model!r}. Models: {names}.")
        return found

    def one(found: list[tuple], kind: str, name: str, lister: str) -> tuple:
        """The one (connection, model, dataset or None, node) found by a name, or an error naming every place."""
        if not found:
            raise ToolError(f"No {kind} named {name!r}. {lister} shows them.")
        if len(found) > 1:
            places = "; ".join(
                f"model {m.name} on {c.name}" + (f", dataset {home['name']}" if home is not None else "")
                for c, m, home, _ in found
            )
            raise ToolError(f"{name!r} is a {kind} of more than one place ({places}): name the one you mean.")
        return found[0]

    def definitions(nodes: list[dict]) -> dict:
        """What the vocabularies say of the own metadata these parts of a model use, by vendor: each key's description,
        and the definitions of the values used."""
        found: dict = {}
        for node in nodes:
            for extension in node.get("custom_extensions", []):
                declared = vocabularies.get(extension["vendor_name"])
                if declared is None:
                    continue
                for key, said in declared.definitions(extension["data"]).items():
                    slot = found.setdefault(declared.vendor_name, {}).setdefault(key, {})
                    if "description" in said:
                        slot["description"] = said["description"]
                    if "values" in said:
                        slot.setdefault("values", {}).update(said["values"])
        return found

    def summary(chosen: Connection, model: Model, node: dict, kind: str, **extra) -> dict:
        """A dataset or a metric for a list: where it is, its name, the first line of its description, its synonyms,
        and the own metadata its vocabulary shows in lists (`in_lists`), as written."""
        item = {"connection": chosen.name, "model": model.name, kind: node["name"], **extra}
        description = semantic.first_line(node.get("description"))
        if description:
            item["description"] = description
        ai_context = node.get("ai_context")
        if isinstance(ai_context, dict) and ai_context.get("synonyms"):
            item["synonyms"] = ai_context["synonyms"]
        shown = []
        for extension in node.get("custom_extensions", []):
            declared = vocabularies.get(extension["vendor_name"])
            data = extension["data"]
            if declared is None or not isinstance(data, dict):
                continue
            kept = {key: value for key, value in data.items() if key in declared.keys and declared.keys[key].in_lists}
            if kept:
                shown.append({"vendor_name": extension["vendor_name"], "data": kept})
        if shown:
            item["custom_extensions"] = shown
        return item

    def listing(key: str, items: list[dict], hint: str) -> str:
        answer: dict = {}
        if not any(c.models for c in connections.values()):
            answer["note"] = "No connection has a semantic model; list_tables and describe_table show the tables."

        def vocabulary_of(shown: list[dict]) -> dict:
            said = definitions(shown)
            return {"vocabulary": said} if said else {}

        return answers.render(answer, key, items, answer_characters, hint, beside=vocabulary_of)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_datasets(connection: str | None = None, search: str | None = None) -> str:
        """The datasets of the semantic models: the tables the team described for agents. For each: its connection
        and model, its name, its `source` table, the first line of its description (`…` when there is more), its
        synonyms, and the team's own metadata shown in lists — with what each value means in `vocabulary`. `search`
        keeps the datasets holding every one of its words, in any case, anywhere in the dataset as written (name,
        description, fields, own metadata). Read one in full with `describe_dataset`."""
        words = search.split() if search else []
        items = [
            summary(chosen, model, dataset, "dataset", source=dataset["source"])
            for chosen, model in models_of(connection, None)
            for dataset in model.document["datasets"]
            if semantic.matches(dataset, words)
        ]
        return listing("datasets", items, "Give `search` or `connection` to list fewer.")

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def describe_dataset(dataset: str, connection: str | None = None, model: str | None = None) -> str:
        """One dataset of a semantic model in full, as the team wrote it: its source, keys, description,
        `ai_context`, its own metadata (`custom_extensions`); its fields, each with its expression — and the live
        column's type and comment when a field is one column; its own metrics; its relationships. Beside them: the
        source table as the database holds it now (`table`, with `bound_by` when a query must bound it), its columns
        no field describes, and what the vocabulary says each own metadata value means. Give `connection` or `model`
        when two models have a dataset of that name."""
        found = [
            (chosen, owner, None, node)
            for chosen, owner in models_of(connection, model)
            for node in owner.document["datasets"]
            if node["name"] == dataset
        ]
        chosen, owner, _, node = one(found, "dataset", dataset, "list_datasets")
        answer: dict = {"connection": chosen.name, "model": owner.name, "dataset": node["name"]}
        answer |= {key: value for key, value in node.items() if key not in ("name", "fields", "metrics")}
        columns: list = []
        if semantic.is_query(node["source"]):
            answer["table"] = {"note": "the source is a query, not a table"}
        else:
            try:
                schema, name = guard.resolve_table(node["source"], chosen.rules)
                answer["table"], columns = live_table(chosen, schema, name)
                if not columns:
                    answer["table"]["note"] = "there is no such table on the connection now"
            except (guard.Refused, AdapterError) as error:
                answer["table"] = {"note": f"the source table cannot be read: {error}"}
        by_name = {guard.stored_name(column.name, chosen.rules): column for column in columns}
        fields, described = [], set()
        for field in node.get("fields", []):
            item = dict(field)
            name = semantic.bare_column(field)
            column = by_name.get(guard.stored_name(name, chosen.rules)) if name else None
            if column is not None:
                described.add(column.name)
                item["column"] = column_item(column, name=False)
            fields.append(item)
        if node.get("metrics"):
            answer["metrics"] = node["metrics"]
        relationships = [r for r in owner.document.get("relationships", []) if node["name"] in (r["from"], r["to"])]
        if relationships:
            answer["relationships"] = relationships
        other = [column_item(column) for column in columns if column.name not in described]
        if other:
            answer["other_columns"] = other
        said = definitions([node, *node.get("fields", []), *node.get("metrics", []), *relationships])
        if said:
            answer["vocabulary"] = said
        smaller = [
            ("other_columns", [column["name"] for column in other], "`other_columns` holds names only"),
            ("other_columns", None, "`other_columns` left out (describe_table shows them)"),
            ("relationships", None, "`relationships` left out"),
            ("metrics", None, "`metrics` left out (describe_metric shows them)"),
            ("vocabulary", None, "`vocabulary` left out"),
        ]
        return answers.render_fitting(answer, "fields", fields, answer_characters, "", smaller)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def list_metrics(connection: str | None = None, search: str | None = None) -> str:
        """The metrics of the semantic models: how the team defines its numbers. For each: its connection and model,
        its name, its `dataset` when it is a dataset's own metric, the first line of its description (`…` when there
        is more), its synonyms, and the team's own metadata shown in lists — with what each value means in
        `vocabulary`. `search` keeps the metrics holding every one of its words, in any case, anywhere in the metric as
        written. Read one in full — its expression — with `describe_metric`."""
        words = search.split() if search else []
        items = []
        for chosen, model in models_of(connection, None):
            items.extend(
                summary(chosen, model, metric, "metric")
                for metric in model.document.get("metrics", [])
                if semantic.matches(metric, words)
            )
            items.extend(
                summary(chosen, model, metric, "metric", dataset=dataset["name"])
                for dataset in model.document["datasets"]
                for metric in dataset.get("metrics", [])
                if semantic.matches(metric, words)
            )
        return listing("metrics", items, "Give `search` or `connection` to list fewer.")

    @server.tool(annotations=READ_ONLY, structured_output=False)
    def describe_metric(
        metric: str, connection: str | None = None, model: str | None = None, dataset: str | None = None
    ) -> str:
        """One metric in full, as the team wrote it: its expression in each SQL dialect written, description, data
        type, `ai_context` and own metadata (`custom_extensions`) — with what the vocabulary says each own metadata
        value means, and the datasets its expression reads: each one's source table, and the fields the expression
        names with their own expressions (a field may be a calculation over the table's columns, not a column).
        `dataset` names a dataset's own metric; give `connection` or `model` when two models define a metric of that
        name."""
        found = []
        for chosen, owner in models_of(connection, model):
            if dataset is None:
                found.extend((chosen, owner, None, m) for m in owner.document.get("metrics", []) if m["name"] == metric)
            for home in owner.document["datasets"]:
                if dataset is None or home["name"] == dataset:
                    found.extend((chosen, owner, home, m) for m in home.get("metrics", []) if m["name"] == metric)
        chosen, owner, home, node = one(found, "metric", metric, "list_metrics")
        answer: dict = {"connection": chosen.name, "model": owner.name, "metric": node["name"]}
        if home is not None:
            answer["dataset"] = home["name"]
        answer |= {key: value for key, value in node.items() if key != "name"}
        said = definitions([node])
        if said:
            answer["vocabulary"] = said
        sources = {d["name"]: d["source"] for d in owner.document["datasets"]}
        read = semantic.fields_read(node, owner.document, home)
        if home is not None:
            read.setdefault(home["name"], [])
        items = [
            {
                "dataset": name,
                "source": sources[name],
                "fields": [{"name": f["name"], "expression": f["expression"]} for f in fields],
            }
            for name, fields in read.items()
        ]
        return answers.render(answer, "datasets", items, answer_characters, "")

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
        try:
            schema, name = guard.resolve_table(table, connection_rules[chosen.name])
        except guard.Refused as refusal:
            raise ToolError(f"Refused ({refusal.code}): {refusal.message}.") from refusal
        try:
            facts, columns = live_table(chosen, schema, name)
        except AdapterError as error:
            raise ToolError(f"The database ({chosen.name}) returned an error: {error}") from error
        if not columns:
            raise ToolError(f"No table {schema}.{name} on {chosen.name}. list_tables shows the readable ones.")
        answer: dict = {"connection": chosen.name, "table": facts.pop("name"), **facts}
        return answers.render(answer, "columns", [column_item(c) for c in columns], answer_characters, "")

    def live_table(chosen: Connection, schema: str, name: str) -> tuple[dict, list]:
        """A readable table as the database holds it now: its name, kind, comment and `bound_by`, and its columns."""
        rule = connection_rules[chosen.name]
        columns = chosen.adapter.columns(schema, name)
        listed = [
            t
            for t in chosen.adapter.tables()
            if (guard.stored_name(t.schema, rule), guard.stored_name(t.name, rule)) == (schema, name)
        ]
        facts: dict = {"name": f"{schema}.{name}"}
        if listed:
            facts["kind"] = listed[0].kind
            if listed[0].comment:
                facts["comment"] = listed[0].comment
        bound = guard.large_table_bound(schema, name, rule)
        if bound:
            facts["bound_by"] = bound
        return facts, columns

    def column_item(column, name: bool = True) -> dict:
        item = {"name": column.name} if name else {}
        item["type"] = column.type
        if column.nullable is not None:
            item["nullable"] = column.nullable
        if column.comment:
            item["comment"] = column.comment
        return item

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
    """The console entry point: check the configuration, then serve over stdio — or, as `import-dbt`, the importer."""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["import-dbt"]:
        from .importers import dbt

        dbt.main(argv[1:])
        return
    parser = argparse.ArgumentParser(
        prog="semantic-warehouse-mcp",
        description="A read-only MCP server for SQL databases with their semantics. Serves over stdio, or HTTP.",
    )
    parser.add_argument("--config", help=f"the configuration file (or set {CONFIG_VARIABLE})")
    parser.add_argument("--check", action="store_true", help="check the configuration, reach every database, exit")
    parser.add_argument("--offline", action="store_true", help="with --check: reach no database (for CI)")
    parser.add_argument("--http", action="store_true", help="serve over Streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="with --http: the address to listen on (127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="with --http: the port (8000)")
    parser.add_argument("--path", default="/mcp", help="with --http: the MCP endpoint's path (/mcp)")
    parser.add_argument("--token-env", help="with --http: the variable holding the token every request must carry")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    path = args.config or os.environ.get(CONFIG_VARIABLE)
    if not path:
        parser.error(f"no configuration: pass --config <file> or set {CONFIG_VARIABLE}")
    if args.offline and not args.check:
        parser.error("--offline goes with --check: a server that serves reaches its databases")
    if args.http:
        try:  # before the databases are reached: a server that cannot be served safely never starts
            serving.token_of(args.token_env, args.host)
        except serving.HttpProblem as problem:
            print(f"semantic-warehouse-mcp: {problem}", file=sys.stderr)
            sys.exit(2)
    try:
        config = load(path, reach=not args.offline)
    except ConfigError as error:
        print(f"semantic-warehouse-mcp: {error}", file=sys.stderr)
        sys.exit(2)
    if args.check:
        offline = " without reaching its databases (sources held to `readable`, not looked up)" if args.offline else ""
        print(f"The configuration {config.source} is fine{offline}:")
        for connection in config.connections.values():
            readable = ", ".join(connection.readable)
            reached = "not reached" if args.offline else "reached"
            print(f"  - {connection.name} ({connection.engine}): {reached}; readable {readable}")
            for model in connection.models:
                datasets = model.document["datasets"]
                metrics = len(model.document.get("metrics", [])) + sum(len(d.get("metrics", [])) for d in datasets)
                counts = f"{len(datasets)} datasets, {metrics} metrics"
                print(f"    semantic model {model.name} (Ossie {model.version}): {counts}")
        for vendor, declared in config.vocabularies.items():
            print(f"  - vocabulary {vendor} ({declared.file}): {len(declared.keys)} keys")
        return
    if not args.http:
        build_server(config).run("stdio")
        return
    try:
        serving.serve(build_server(config), host=args.host, port=args.port, path=args.path, token_env=args.token_env)
    except serving.HttpProblem as problem:
        print(f"semantic-warehouse-mcp: {problem}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
