"""The database importer: a draft of a semantic model from a connection's own catalog — its readable tables and views,
their columns and the comments the database holds — written as an Apache Ossie document for a person or an agent to
give meaning to (docs/reference/database-importer.md). For a database no dbt project describes.

What it writes is only what the database says: names, its comments as descriptions, and a date or time column marked as
one by its type. Nothing is guessed, and an edited draft is never overwritten."""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
from collections import Counter
from fnmatch import fnmatchcase
from typing import Any

from .. import files, guard
from ..adapters import AdapterError, Table
from ..config import ConfigError, Connection, parse
from ..server import CONFIG_VARIABLE
from .dbt import OSSIE_VERSION, TWO_PARTS, UNREADABLE_NAME, ImportProblem, identifier, write_document

# A column's type that holds a date or a time, as each engine names it: Date, DateTime64(3), timestamp with time zone…
TIME_TYPE = re.compile(r"\b(date|datetime|datetime64|date32|timestamp|timestamptz|time|timetz)\b", re.IGNORECASE)
# What a connection may hold that the draft does not need: the models it is to be bound to may not exist yet.
NOT_NEEDED = ("semantic_models", "context", "skills", "missing_tables")


def connection(config_path: str, name: str | None) -> Connection:
    """One connection of a server's configuration, reached — without its semantic models, context or skills, so a model
    the configuration already names need not exist. Raises ImportProblem naming each problem."""
    path = pathlib.Path(config_path)
    try:
        data = files.load_file(path)
    except files.FileProblem as problem:
        raise ImportProblem(config_path, [str(problem)]) from problem
    entries = data.get("connections") if isinstance(data, dict) else None
    named = [e.get("name") for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
    if not named:
        raise ImportProblem(config_path, ["the file must hold a mapping with `connections:` at the top"])
    if name is None and len(named) > 1:
        raise ImportProblem(config_path, [f"name the connection to draft: --connection ({', '.join(map(str, named))})"])
    wanted = name if name is not None else named[0]
    if wanted not in named:
        raise ImportProblem(config_path, [f"no connection is named {wanted!r} ({', '.join(map(str, named))})"])
    entry = next(e for e in entries if isinstance(e, dict) and e.get("name") == wanted)
    alone = {"connections": [{key: value for key, value in entry.items() if key not in NOT_NEEDED}]}
    try:
        config = parse(alone, source=config_path, base_dir=path.resolve().parent, reach=True)
    except ConfigError as error:
        raise ImportProblem(config_path, error.problems) from error
    return config.connections[wanted]


def draft(chosen: Connection, model_name: str, patterns: list[str], fields: str) -> tuple[dict[str, Any], Counter[str]]:
    """The Ossie document of a connection's readable tables (those `patterns` name, when given), and what it holds."""
    problems = [f"--tables {pattern!r} is not schema.table (either part may hold * or ?)" for pattern in patterns]
    problems = [problem for problem, pattern in zip(problems, patterns, strict=True) if not TWO_PARTS.match(pattern)]
    if problems:
        raise ImportProblem(chosen.name, problems)
    try:
        listed = chosen.adapter.tables()
    except AdapterError as error:
        raise ImportProblem(chosen.name, [f"its tables cannot be listed: {error}"]) from error
    readable = [table for table in listed if guard.is_readable(table.schema, table.name, chosen.rules)]
    hit = [False] * len(patterns)
    tables: list[Table] = []
    for table in readable:
        hits = [i for i, pattern in enumerate(patterns) if _matches(table, pattern)]
        for index in hits:
            hit[index] = True
        if hits or not patterns:
            tables.append(table)
    problems += [
        f"--tables {p!r} names none of the tables readable here"
        for p, found in zip(patterns, hit, strict=True)
        if not found
    ]
    if not tables and not problems:
        problems.append(f"{chosen.name} holds no table `readable` lets agents read: there is nothing to draft")
    counts: Counter[str] = Counter()
    seen = Counter(table.name for table in tables)
    datasets = []
    for table in sorted(tables, key=lambda t: (t.schema, t.name)):
        try:
            columns = chosen.adapter.columns(table.schema, table.name)
        except AdapterError as error:
            problems.append(f"{table.schema}.{table.name}: its columns cannot be read: {error}")
            continue
        if odd := [n for n in (table.schema, table.name, *(c.name for c in columns)) if UNREADABLE_NAME.search(n)]:
            problems += [
                f"{table.schema}.{table.name}: the name {n!r} holds a backslash or a control character, which each "
                "database reads its own way — leave the table out with --tables"
                for n in odd
            ]
            continue
        name = table.name if seen[table.name] == 1 else f"{table.schema}__{table.name}"
        dataset: dict[str, Any] = {"name": name, "source": f"{identifier(table.schema)}.{identifier(table.name)}"}
        if table.comment:
            dataset["description"] = table.comment
            counts["described dataset"] += 1
        drafted = []
        for column in columns:
            if fields == "described" and not column.comment:
                continue
            field: dict[str, Any] = {
                "name": column.name,
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": identifier(column.name)}]},
            }
            if TIME_TYPE.search(column.type):
                field["dimension"] = {"is_time": True}
                counts["time column"] += 1
            if column.comment:
                field["description"] = column.comment
                counts["described field"] += 1
            drafted.append(field)
        if drafted:
            dataset["fields"] = drafted
        counts["field"] += len(drafted)
        counts[table.kind] += 1
        datasets.append(dataset)
    if problems:
        raise ImportProblem(chosen.name, problems)
    return {"version": OSSIE_VERSION, "name": model_name, "datasets": datasets}, counts


def _matches(table: Table, pattern: str) -> bool:
    pattern_schema, pattern_table = pattern.split(".", 1)
    return fnmatchcase(table.schema, pattern_schema) and fnmatchcase(table.name, pattern_table)


def main(argv: list[str]) -> None:
    """`semantic-warehouse-mcp import-database`: write the draft. Exit 2 on any problem."""
    parser = argparse.ArgumentParser(
        prog="semantic-warehouse-mcp import-database",
        description="Draft an Apache Ossie document from a connection's own catalog: its readable tables, columns and "
        "comments.",
    )
    parser.add_argument("--config", help=f"the server's configuration file (or set {CONFIG_VARIABLE})")
    parser.add_argument("--connection", help="the connection to draft (needed when the configuration holds several)")
    parser.add_argument("--output", required=True, help="the Ossie document written")
    parser.add_argument("--name", help="the model's name; the connection's by default")
    parser.add_argument("--tables", action="append", default=[], help="schema.table patterns to draft (repeatable)")
    parser.add_argument("--fields", choices=("all", "described"), default="all", help="every column, or the commented")
    parser.add_argument("--overwrite", action="store_true", help="write over an existing file")
    args = parser.parse_args(argv)
    try:
        _run(args)
    except ImportProblem as problem:
        print(f"semantic-warehouse-mcp import-database: {problem}", file=sys.stderr)
        sys.exit(2)
    except Exception as error:  # a defect of the importer's own: named
        said = files.redact(f"{type(error).__name__}: {error}")
        print(
            f"semantic-warehouse-mcp import-database: an unexpected error — please report it: {said}", file=sys.stderr
        )
        sys.exit(2)


def _run(args: argparse.Namespace) -> None:
    config_path = args.config or os.environ.get(CONFIG_VARIABLE)
    if not config_path:
        raise ImportProblem("import-database", [f"give the configuration: --config, or set {CONFIG_VARIABLE}"])
    output = pathlib.Path(args.output)
    if output.exists() and not args.overwrite:
        raise ImportProblem(
            args.output, ["exists — a draft may have been edited since: give --overwrite to write over it"]
        )
    chosen = connection(config_path, args.connection)
    document, counts = draft(chosen, args.name or chosen.name, args.tables, args.fields)
    header = (
        f"# Drafted by semantic-warehouse-mcp import-database from {chosen.name}'s catalog: names, the database's\n"
        "# comments, time columns by their type. Give it meaning — what each table holds, how to count — and keep it.\n"
    )
    text = write_document(document, header, args.output, args.output)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(text.encode("utf-8"))
    except OSError as error:
        raise ImportProblem(args.output, [f"cannot be written: {error.strerror or error}"]) from error
    datasets = len(document["datasets"])
    kinds = ", ".join(f"{counts[kind]} {kind}{'s' * (counts[kind] != 1)}" for kind in ("table", "view") if counts[kind])
    print(f"Wrote {args.output} — the Ossie model {document['name']}, a draft:")
    print(f"  {datasets} datasets ({kinds}), {counts['field']} fields, {counts['time column']} time columns")
    print(f"  the database's comments on {counts['described dataset']} datasets and {counts['described field']} fields")
    # As the configuration reads it: relative to its folder, or whole where the draft is outside it.
    named = os.path.relpath(output.resolve(), pathlib.Path(config_path).resolve().parent)
    named = str(output.resolve()) if named.startswith("..") else named
    print(f"  serve it: semantic_models: [{named}] under {chosen.name}, then --check")
