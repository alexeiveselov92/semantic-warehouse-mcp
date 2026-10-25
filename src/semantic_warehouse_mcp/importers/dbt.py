"""The dbt importer: a dbt project's tables, their descriptions, `meta` and tags written as an Apache Ossie document the
server serves like any model (docs/reference/dbt-importer.md).

What is the team's — which tables (by name, or by dbt's own selection: tags, paths, config), the vendor its `meta` goes
under, which of its keys stand for Ossie's own places, the keys dbt's own tags, lineage and SQL are written under — is
the importer's configuration. The code reads only what is dbt's own: descriptions, `meta` and tags as wholes, the
primary key dbt infers, `relationships` tests, a model's `event_time`, the tables a model reads and its SQL as
written."""

from __future__ import annotations

import argparse
import copy
import json
import math
import pathlib
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Annotated, Any, Literal

import sqlglot
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlglot import exp
from sqlglot.errors import SqlglotError

from .. import files, semantic

OSSIE_VERSION = "0.2.0.dev0"
# The manifests read: dbt 1.6 (v10) to 1.10 and later (v12) hold what is read here alike.
MANIFEST_VERSIONS = ("v10", "v11", "v12")
RESOURCES = ("model", "seed", "snapshot", "source")
# dbt's own field of a node and a column holding dbt's tags — the manifest's, not a team's key.
MANIFEST_TAGS = "tags"
TWO_PARTS = re.compile(r"^[^.\s]+\.[^.\s]+$")
PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# dbt's node selection (`dbt run --select`), the methods read here — each matched as dbt matches it, `*` and `?` too.
SELECTION_METHODS = ("tag", "path", "config.<key>", "source", "fqn", "resource_type")
GRAPH_OPERATORS = re.compile(r"^(\d*\+|@)|\+\d*$")
# A name each database reads its own way, inside quotes or not: a backslash, a control character.
UNREADABLE_NAME = re.compile(r"[\\\x00-\x1f\x7f]")
# How dbt writes the table a test names: ref('name'), ref('package', 'name'), ref('name', v=2); source('a', 'b').
REF = re.compile(r"""\bref\(\s*(?:['"][^'"]*['"]\s*,\s*)?['"]([^'"]+)['"]""")
SOURCE = re.compile(r"""\bsource\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)""")
# Deeper than this, a team's meta is refused: no catalog nests so, and writing it would exhaust the stack.
MAX_DEPTH = 64
MAX_PROBLEMS = 20
# Line breaks YAML reads as its own: text holding one is written in double quotes, where each stays as it is.
YAML_BREAKS = ("\r", "\x85", " ", " ")


class ImportProblem(Exception):
    """Every problem found, one per line, each naming where it is."""

    def __init__(self, source: str, problems: list[str]) -> None:
        if len(problems) > MAX_PROBLEMS:
            problems = [*problems[:MAX_PROBLEMS], f"and {len(problems) - MAX_PROBLEMS} more"]
        self.problems = [files.redact(problem) for problem in problems]
        lines = "\n".join(f"  - {problem}" for problem in self.problems)
        noun = "problem" if len(self.problems) == 1 else "problems"
        super().__init__(f"the import {source} has {len(self.problems)} {noun}:\n{lines}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Mark(_Strict):
    """What marks a column: its meta's `meta_key` is `value` or a list holding it — or it has dbt's tag `dbt_tag`."""

    meta_key: str | None = None
    value: Any = True
    dbt_tag: str | None = None


class KeyOf(_Strict):
    """The key of a table's meta that names its primary key: a column, or a list of them."""

    meta_key: str


class Places(_Strict):
    time_column: list[Mark] = []
    primary_key: KeyOf | None = None

    @field_validator("time_column", mode="before")
    @classmethod
    def _one_or_more(cls, value: Any) -> Any:
        return [value] if isinstance(value, dict) else value


class ModelEntry(_Strict):
    name: Annotated[str, Field(min_length=1)]
    description: str | None = None
    ai_context: str | dict[str, Any] | None = None


class ImportFile(_Strict):
    manifest: str
    output: str
    model: ModelEntry
    tables: Annotated[list[str], Field(min_length=1)] = ["*.*"]
    select: list[Annotated[str, Field(min_length=1)]] = []
    exclude: list[Annotated[str, Field(min_length=1)]] = []
    resources: Annotated[list[Literal["model", "seed", "snapshot", "source"]], Field(min_length=1)] = list(RESOURCES)
    vendor_name: Annotated[str, Field(min_length=1)] | None = None
    dbt_tags: Annotated[str, Field(min_length=1)] | None = None
    dbt_depends_on: Annotated[str, Field(min_length=1)] | None = None
    dbt_code: Annotated[str, Field(min_length=1)] | None = None
    places: Places = Places()


# What of dbt's own each key of the configuration writes into the vendor's data, in the words of a problem.
DBT_OWN = {"dbt_tags": "dbt's tags", "dbt_depends_on": "the tables it reads", "dbt_code": "its SQL"}


# The shape of what is read from a manifest, as dbt writes it — checked first, so an odd one is named, not a crash.
class _Shape(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class _ColumnShape(_Shape):
    description: str | None = None
    meta: dict[str, Any] | None = None
    tags: list[Any] | None = None
    config: dict[str, Any] | None = None
    quote: bool | None = None


class _DependsOnShape(_Shape):
    nodes: list[str] | None = None


class _TableShape(_Shape):
    unique_id: str
    resource_type: str
    name: str
    package_name: str | None = None
    schema_: str = Field(alias="schema")
    alias: str | None = None
    identifier: str | None = None
    source_name: str | None = None
    database: str | None = None
    description: str | None = None
    config: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    tags: list[Any] | None = None
    columns: dict[str, _ColumnShape] | None = None
    primary_key: list[Any] | None = None
    version: str | int | float | None = None
    depends_on: _DependsOnShape | None = None
    raw_code: str | None = None
    original_file_path: str | None = None
    fqn: list[str] | None = None


class _TestMetadataShape(_Shape):
    name: str | None = None
    namespace: str | None = None
    kwargs: dict[str, Any] | None = None


class _TestShape(_Shape):
    unique_id: str
    package_name: str | None = None
    attached_node: str | None = None
    column_name: str | None = None
    test_metadata: _TestMetadataShape | None = None
    depends_on: _DependsOnShape | None = None


@dataclass(frozen=True)
class Import:
    """An importer's configuration, checked, its paths resolved against its folder."""

    source: str
    entry: ImportFile
    manifest: pathlib.Path
    output: pathlib.Path


@dataclass
class Result:
    document: dict[str, Any]
    dbt_version: str
    counts: Counter[str] = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)


def load(path: str | pathlib.Path) -> Import:
    """Read and check an importer's configuration. Raises ImportProblem naming each problem."""
    path = pathlib.Path(path)
    source = str(path)
    try:
        data = files.load_file(path)
    except files.FileProblem as problem:
        raise ImportProblem(source, [str(problem)]) from problem
    if not isinstance(data, dict):
        raise ImportProblem(source, ["the file must hold a mapping with `manifest`, `output` and `model` at the top"])
    try:
        entry = ImportFile.model_validate(data)
    except ValidationError as error:
        raise ImportProblem(source, files.describe(error, data, ImportFile)) from error
    problems = [
        f"tables[{index}]: {pattern!r} is not schema.table (either part may hold * or ?: marts.*)"
        for index, pattern in enumerate(entry.tables)
        if not TWO_PARTS.match(pattern)
    ]
    for index, mark in enumerate(entry.places.time_column):
        where = f"places → time_column[{index}]"
        if (mark.meta_key is None) == (mark.dbt_tag is None):
            problems.append(f"{where}: give one of `meta_key` (with its `value`) or `dbt_tag`")
        elif mark.dbt_tag is not None and "value" in mark.model_fields_set:
            problems.append(f"{where}: `value` goes with `meta_key`, not with `dbt_tag`")
    for setting in ("select", "exclude"):
        for index, item in enumerate(getattr(entry, setting)):
            problems += [f"{setting}[{index}]: {said}" for said in _selector_problems(item)]
    for key, what in DBT_OWN.items():
        if getattr(entry, key) and not entry.vendor_name:
            problems.append(f"{key}: needs `vendor_name` — it writes {what} into that vendor's data")
    named = [getattr(entry, key) for key in DBT_OWN if getattr(entry, key)]
    for key in sorted({name for name in named if named.count(name) > 1}):
        keys = ", ".join(f"`{own}`" for own in DBT_OWN if getattr(entry, own) == key)
        problems.append(f"{keys}: each names {key!r} — give each its own key")
    base = path.resolve().parent
    manifest, output = base / entry.manifest, base / entry.output
    try:
        if not manifest.is_file():
            problems.append(
                f"manifest: {entry.manifest} does not exist (dbt writes it: dbt parse → target/manifest.json)"
            )
        if output.resolve() in (path.resolve(), manifest.resolve()):
            problems.append(f"output: {entry.output} is the importer's own configuration or its manifest")
        elif output.is_dir():
            problems.append(f"output: {entry.output} is a folder, not a file")
    except OSError as error:
        problems.append(f"manifest, output: cannot be reached: {error.strerror or error}")
    if problems:
        raise ImportProblem(source, problems)
    return Import(source=source, entry=entry, manifest=manifest, output=output)


def read_manifest(imported: Import) -> Any:
    try:
        return json.loads(imported.manifest.read_text(encoding="utf-8"))
    except RecursionError as error:
        raise ImportProblem(imported.source, [f"manifest: {imported.entry.manifest} is nested too deep"]) from error
    except (OSError, UnicodeDecodeError, ValueError) as error:
        problem = f"manifest: {imported.entry.manifest} cannot be read: {error}"
        raise ImportProblem(imported.source, [problem]) from error


def build(imported: Import, manifest: Any) -> Result:
    """The Ossie document of a manifest's tables, as the configuration says. Raises ImportProblem naming each one."""
    entry = imported.entry
    if not isinstance(manifest, dict) or not all(isinstance(manifest.get(key), dict) for key in ("nodes", "sources")):
        raise ImportProblem(imported.source, [f"manifest: {entry.manifest} is not a dbt manifest (no nodes, sources)"])
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    version = str(metadata.get("dbt_schema_version", "")).rsplit("/", 1)[-1].removesuffix(".json")
    if version not in MANIFEST_VERSIONS:
        read = ", ".join(MANIFEST_VERSIONS)
        named = repr(version) if version else "no version"
        raise ImportProblem(imported.source, [f"manifest: {named} is not a manifest version read here ({read})"])
    shape_problems = _shape_problems(manifest)
    if shape_problems:
        raise ImportProblem(imported.source, shape_problems)
    project = metadata.get("project_name")
    result = Result(document={}, dbt_version=str(metadata.get("dbt_version", "")))
    problems: list[str] = []

    every = [*manifest["nodes"].values(), *manifest["sources"].values()]
    own = [node for node in every if not project or node.get("package_name") == project]
    home = _home_database(own)
    by_unique_id = {node["unique_id"]: node for node in every}
    matched = [False] * len(entry.tables)
    chosen = {setting: [False] * len(getattr(entry, setting)) for setting in ("select", "exclude")}
    datasets: dict[str, dict[str, Any]] = {}
    by_id: dict[str, str] = {}
    columns_of: dict[str, dict[str, Any]] = {}
    for node in own:
        resource = node.get("resource_type")
        config = node.get("config") or {}
        if resource not in entry.resources or config.get("enabled") is False:
            continue
        if config.get("materialized") == "ephemeral":  # no table: a model dbt writes into the queries that use it
            continue
        schema, table = _relation(node)
        hits = [index for index, pattern in enumerate(entry.tables) if _matches(schema, table, pattern)]
        selection = {
            setting: [index for index, item in enumerate(getattr(entry, setting)) if _selected(node, item)]
            for setting in chosen
        }
        for setting, indexes in selection.items():
            for index in indexes:
                chosen[setting][index] = True
        if not hits:
            continue
        for index in hits:
            matched[index] = True
        if (entry.select and not selection["select"]) or selection["exclude"]:
            result.counts["left out by the selection"] += 1
            continue
        if node.get("database") and home and node["database"] != home:  # the connection reads its own database
            result.counts["table of another database"] += 1
            continue
        where = node["unique_id"]
        if unreadable := [
            name for name in (schema, table, *(node.get("columns") or {})) if UNREADABLE_NAME.search(name)
        ]:
            said = "holds a backslash or a control character, which each database reads its own way"
            problems.extend(
                f"{where}: the name {name!r} {said} — rename it, or leave the table out" for name in unreadable
            )
            continue
        name = _dataset_name(node)
        if name in datasets:
            problems.append(f"{where}: makes the dataset {name!r}, as another table does — leave one out")
            continue
        source = f"{identifier(schema)}.{identifier(table)}"
        reads = _reads(node, by_unique_id, home) if entry.dbt_depends_on else []
        datasets[name] = _dataset(node, name, source, reads, entry, result, problems)
        by_id[where] = name
        columns_of[where] = node.get("columns") or {}
        result.counts[resource] += 1
    kinds = ", ".join(f"{kind}s" for kind in entry.resources)
    for index, pattern in enumerate(entry.tables):
        if not matched[index]:
            problems.append(f"tables[{index}]: {pattern!r} matches none of the manifest's {kinds}")
    for setting, found in chosen.items():
        problems += [
            f"{setting}[{index}]: {getattr(entry, setting)[index]!r} selects none of the manifest's {kinds}"
            for index, hit in enumerate(found)
            if not hit
        ]

    relationships = _relationships(manifest["nodes"], every, project, by_id, columns_of, result)
    if problems:
        raise ImportProblem(imported.source, problems)

    document: dict[str, Any] = {"version": OSSIE_VERSION, "name": entry.model.name}
    if entry.model.description:
        document["description"] = entry.model.description
    if entry.model.ai_context is not None:
        document["ai_context"] = copy.deepcopy(entry.model.ai_context)
    document["datasets"] = [datasets[name] for name in sorted(datasets)]
    if relationships:
        document["relationships"] = [relationships[name] for name in sorted(relationships)]
    result.document = document
    result.counts["relationship"] = len(relationships)
    if manifest.get("semantic_models"):
        result.notes.append(
            f"the manifest holds {len(manifest['semantic_models'])} MetricFlow semantic models: Ossie's own converter "
            "turns them and their metrics into Ossie (ossie-dbt msi-to-ossie)"
        )
    return result


def _shape_problems(manifest: dict[str, Any]) -> list[str]:
    """Where the manifest is not as dbt writes it, in the parts read here."""
    problems = []
    if manifest.get("semantic_models") is not None and not isinstance(manifest["semantic_models"], dict):
        problems.append("manifest → semantic_models: not a mapping, as dbt writes it")
    for part in ("nodes", "sources"):
        for key, node in manifest[part].items():
            where = f'manifest → {part} → "{key}"'
            if not isinstance(node, dict) or not isinstance(node.get("resource_type"), str):
                problems.append(f"{where}: not a node as dbt writes one (a mapping with its resource_type)")
                continue
            kind = node["resource_type"]
            shape = _TableShape if kind in RESOURCES else _TestShape if kind == "test" else None
            if shape is None:
                continue
            try:
                shape.model_validate(node)
            except ValidationError as error:
                problems.extend(f"{where} → {line}" for line in files.describe(error, node, shape))
                continue
            if node["resource_type"] == "source" and not all(
                isinstance(node.get(key), str) for key in ("source_name", "identifier")
            ):
                problems.append(f"{where}: a source table needs its source_name and identifier, as dbt writes them")
            if any(not isinstance(column, dict) for column in (node.get("columns") or {}).values()):
                problems.append(f"{where} → columns: each column a mapping, as dbt writes it")
    return problems


def _home_database(nodes: list[dict[str, Any]]) -> str | None:
    """The database the project's models are built in — its target's: the one most of them name."""
    named = [node["database"] for node in nodes if node.get("resource_type") == "model" and node.get("database")]
    named = named or [node["database"] for node in nodes if node.get("database")]
    counted = Counter(named)
    return min(counted, key=lambda database: (-counted[database], database)) if counted else None


def _relation(node: dict[str, Any]) -> tuple[str, str]:
    """The schema and the table a node is in the database: a source's identifier, a model's alias, else its name."""
    table = node.get("identifier") if node.get("resource_type") == "source" else node.get("alias")
    return node["schema"], table if isinstance(table, str) else node["name"]


def _reads(node: dict[str, Any], by_unique_id: dict[str, dict[str, Any]], home: str | None) -> list[str]:
    """The tables a model or a snapshot reads, as dbt's `depends_on` names them: each `schema.table` — with its database
    first when it is not the project's — and through an ephemeral model, the tables that one reads."""
    found: set[str] = set()
    seen: set[str] = set()
    waiting = list((node.get("depends_on") or {}).get("nodes") or [])
    while waiting:
        unique_id = waiting.pop()
        if unique_id in seen:
            continue
        seen.add(unique_id)
        other = by_unique_id.get(unique_id)
        if other is None or other.get("resource_type") not in RESOURCES:
            continue
        if (other.get("config") or {}).get("materialized") == "ephemeral":  # written into this one's query
            waiting.extend((other.get("depends_on") or {}).get("nodes") or [])
            continue
        schema, table = _relation(other)
        name = f"{identifier(schema)}.{identifier(table)}"
        database = other.get("database")
        found.add(f"{identifier(database)}.{name}" if database and home and database != home else name)
    return sorted(found)


def _selector_problems(item: str) -> list[str]:
    """What a selection item holds that is not read here — each criterion of it, dbt's way: `method:value`, joined by
    commas (all of them) — said before any manifest is read."""
    problems = []
    for criterion in item.split(","):
        method, colon, value = criterion.strip().partition(":") if ":" in criterion else ("", "", criterion.strip())
        if not criterion.strip() or (colon and not value):
            problems.append(f"{item!r}: an empty criterion")
        elif any(char.isspace() for char in criterion.strip()):
            problems.append(f"{item!r}: one criterion per list item — a space joins two in dbt's own syntax")
        elif GRAPH_OPERATORS.search(criterion.strip()):
            problems.append(f"{item!r}: dbt's graph operators (+, @) are not read here — name what to import")
        elif (
            method
            and method not in ("tag", "path", "source", "fqn", "resource_type")
            and not (method.startswith("config.") and len(method) > len("config."))
        ):
            problems.append(f"{item!r}: the method {method!r} is not read here ({', '.join(SELECTION_METHODS)})")
        elif method == "resource_type" and value not in RESOURCES:
            problems.append(f"{item!r}: a resource_type is one of {', '.join(RESOURCES)}")
    return problems


def _selected(node: dict[str, Any], item: str) -> bool:
    """Whether a selection item — criteria joined by commas, as dbt writes them — selects the node: every criterion."""
    return all(_criterion(node, criterion.strip()) for criterion in item.split(","))


def _criterion(node: dict[str, Any], criterion: str) -> bool:
    """One criterion of dbt's selection, matched as dbt matches it; a bare one is a path when it reads like one, else
    a name in the project's tree (`fqn`)."""
    method, _, value = criterion.partition(":") if ":" in criterion else ("", "", criterion)
    if not method:
        method = "path" if "/" in value or value.endswith((".sql", ".py", ".csv", ".yml", ".yaml")) else "fqn"
    if method == "tag":
        return any(isinstance(tag, str) and fnmatchcase(tag, value) for tag in node.get(MANIFEST_TAGS) or ())
    if method == "path":
        path, wanted = str(node.get("original_file_path") or ""), value.rstrip("/")
        return path == wanted or path.startswith(wanted + "/") or fnmatchcase(path, wanted)
    if method == "resource_type":
        return node.get("resource_type") == value
    if method == "source":
        if node.get("resource_type") != "source":
            return False
        source, _, table = value.partition(".")
        return fnmatchcase(str(node.get("source_name")), source) and (
            not table or fnmatchcase(str(node.get("name")), table)
        )
    if method == "fqn":
        fqn, parts = [str(part) for part in node.get("fqn") or ()], value.split(".")
        if len(parts) == 1 and fqn and fnmatchcase(fqn[-1], value):
            return True
        return any(  # the project's name first or not
            len(fqn[start : start + len(parts)]) == len(parts)
            and all(fnmatchcase(part, want) for part, want in zip(fqn[start:], parts, strict=False))
            for start in (0, 1)
        )
    held = (node.get("config") or {}).get(method.removeprefix("config."))
    return any(
        fnmatchcase(str(one).lower() if isinstance(one, bool) else str(one), value)
        for one in (held if isinstance(held, list) else [held])
        if one is not None
    )


def _matches(schema: str, table: str, pattern: str) -> bool:
    pattern_schema, _, pattern_table = pattern.partition(".")
    return fnmatchcase(schema, pattern_schema) and fnmatchcase(table, pattern_table)


def _dataset_name(node: dict[str, Any]) -> str:
    """A model's, seed's or snapshot's name (a version's with `_v<version>`); a source table's `<source>__<table>`."""
    if node.get("resource_type") == "source":
        return f"{node['source_name']}__{node['name']}"
    version = node.get("version")
    return f"{node['name']}_v{version}" if version is not None else node["name"]


def _dataset(
    node: dict[str, Any],
    name: str,
    source: str,
    reads: list[str],
    entry: ImportFile,
    result: Result,
    problems: list[str],
) -> dict[str, Any]:
    where = node["unique_id"]
    columns = node.get("columns") or {}
    meta = _meta(node)
    dataset: dict[str, Any] = {"name": name, "source": source}
    primary_key = _primary_key(node, meta, columns, entry, where, problems)
    if primary_key:
        dataset["primary_key"] = primary_key
        result.counts["primary key"] += 1
    if node.get("description"):
        dataset["description"] = node["description"]
    event_time = (node.get("config") or {}).get("event_time")
    fields = []
    for column_name, column in columns.items():
        column_meta, column_tags = _meta(column), _tags(column)
        sql = identifier(column_name, quote=bool(column.get("quote")))
        field_ = {"name": column_name, "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}}
        if (
            column_name == event_time
            or column.get("granularity")
            or any(_marked(mark, column_meta, column_tags) for mark in entry.places.time_column)
        ):
            field_["dimension"] = {"is_time": True}
            result.counts["time column"] += 1
        if column.get("description"):
            field_["description"] = column["description"]
        place = f"{where} → columns → {column_name}"
        extensions = _extensions(column_meta, {"dbt_tags": column_tags}, entry, place, result, problems)
        if extensions:
            field_["custom_extensions"] = extensions
            result.counts["field with data"] += 1
        fields.append(field_)
        result.counts["field"] += 1
    if fields:
        dataset["fields"] = fields
    code = node.get("raw_code") if entry.dbt_code else None
    own = {"dbt_tags": _tags(node), "dbt_depends_on": reads, "dbt_code": code}
    extensions = _extensions(meta, own, entry, where, result, problems)
    if extensions:
        dataset["custom_extensions"] = extensions
        result.counts["dataset with data"] += 1
    return dataset


def _meta(node: dict[str, Any]) -> dict[str, Any]:
    """A node's or a column's meta as dbt resolved it (its config's), or as written where a manifest has no config."""
    config = node.get("config")
    meta = config.get("meta") if isinstance(config, dict) and isinstance(config.get("meta"), dict) else node.get("meta")
    return meta if isinstance(meta, dict) else {}


def _tags(node: dict[str, Any]) -> list[str]:
    """dbt's own tags of a node or a column, as dbt resolved them."""
    config = node.get("config")
    tags = config.get(MANIFEST_TAGS) if isinstance(config, dict) else None
    tags = tags if isinstance(tags, list) else node.get(MANIFEST_TAGS)
    return [tag for tag in tags if isinstance(tag, str)] if isinstance(tags, list) else []


def _extensions(
    meta: dict[str, Any], own: dict[str, Any], entry: ImportFile, where: str, result: Result, problems: list[str]
) -> list[dict[str, Any]]:
    """The vendor's data: the meta as written, and what is dbt's own — its tags, the tables it reads, its SQL — each
    under the key the configuration names for it (`dbt_tags`, `dbt_depends_on`, `dbt_code`), when it has any."""
    if found := _value_problem(meta):
        place, problem = found
        problems.append(f"{where} → meta{''.join(f' → {step}' for step in place)}: {problem}")
        return []
    data = copy.deepcopy(meta)
    for setting, value in own.items():
        key = getattr(entry, setting)
        if not key or not value:
            continue
        if key in meta:
            problems.append(f"{where} → meta: holds {key!r} already, the key `{setting}` writes {DBT_OWN[setting]} as")
        data[key] = copy.deepcopy(value)
        result.counts[setting] += 1
    if not data:
        return []
    if not entry.vendor_name:
        result.counts["meta left out"] += 1
        return []
    return [{"vendor_name": entry.vendor_name, "data": data}]


def _value_problem(node: Any) -> tuple[tuple[Any, ...], str] | None:
    """The first place in a value that an Ossie document cannot hold, and why: a number JSON cannot hold (NaN,
    infinity), text that is not Unicode, nesting past MAX_DEPTH. Read without recursion, however deep it is."""
    stack: list[tuple[tuple[Any, ...], Any]] = [((), node)]
    while stack:
        place, value = stack.pop()
        if len(place) > MAX_DEPTH:
            return place[:MAX_DEPTH], f"nested deeper than {MAX_DEPTH} levels"
        if isinstance(value, float) and not math.isfinite(value):
            return place, f"{value} is a number JSON cannot hold"
        texts = [value] if isinstance(value, str) else list(value) if isinstance(value, dict) else []
        for text in (text for text in texts if isinstance(text, str)):
            try:
                files.unicode_problem(text)
            except yaml.YAMLError as error:
                return place, str(error)
        if isinstance(value, dict):
            stack.extend(((*place, key), inner) for key, inner in reversed(value.items()))
        elif isinstance(value, list):
            stack.extend(((*place, index), inner) for index, inner in reversed(list(enumerate(value))))
    return None


def _marked(mark: Mark, meta: dict[str, Any], tags: list[str]) -> bool:
    if mark.dbt_tag is not None:
        return mark.dbt_tag in tags
    if mark.meta_key not in meta:
        return False
    value = meta[mark.meta_key]
    return _same(value, mark.value) or (isinstance(value, list) and any(_same(item, mark.value) for item in value))


def _same(one: Any, other: Any) -> bool:
    """Equal, and both booleans or neither: `true` marks nothing written as 1."""
    return one == other and isinstance(one, bool) == isinstance(other, bool)


def _primary_key(
    node: dict[str, Any], meta: dict[str, Any], columns: dict[str, Any], entry: ImportFile, where: str, problems: list
) -> list[str] | None:
    """The columns the team's key names, when the configuration names one and the table's meta has it; else the
    primary key dbt infers (from constraints, or a column tested `unique` and `not_null`)."""
    key = entry.places.primary_key.meta_key if entry.places.primary_key else None
    if key is not None and key in meta:
        named = meta[key]
        names = [named] if isinstance(named, str) else named
        if (
            isinstance(names, list)
            and names
            and all(isinstance(item, str) and (item in columns or PLAIN.match(item)) for item in names)
        ):
            return list(names)
        shown = json.dumps(named, ensure_ascii=False) if isinstance(named, (str, list)) else "a mapping or a number"
        shown = shown if len(shown) <= 80 else shown[:79] + "…"
        said = f"{shown} is not a column's name, nor a list of them (places → primary_key reads it)"
        problems.append(f"{where} → meta → {key}: {said}")
        return None
    inferred = node.get("primary_key")
    if not isinstance(inferred, list):
        return None
    return [_unquoted(item, columns) for item in inferred if isinstance(item, str)] or None


def _unquoted(name: str, columns: dict[str, Any]) -> str:
    """A column as dbt writes it in a test or a key — in quotes, for a column it quotes — as the column's own name."""
    for quote in ('"', "`"):
        if len(name) >= 2 and name[0] == name[-1] == quote:
            inner = name[1:-1].replace(quote * 2, quote)
            if inner in columns:
                return inner
    return name


def _relationships(
    nodes: dict[str, Any],
    every: list[dict[str, Any]],
    project: str | None,
    by_id: dict[str, str],
    columns_of: dict[str, dict[str, Any]],
    result: Result,
) -> dict[str, dict[str, Any]]:
    """dbt's `relationships` tests between two tables imported, as Ossie relationships: from the tested table's column
    to the column of the table it must be found in — one per pair of columns, whatever order dbt wrote the tests in."""
    refs = {n["name"]: n["unique_id"] for n in every if n.get("resource_type") in ("model", "seed", "snapshot")}
    sources = {(n["source_name"], n["name"]): n["unique_id"] for n in every if n.get("resource_type") == "source"}
    found: dict[str, dict[str, Any]] = {}
    for test in nodes.values():
        test_metadata = test.get("test_metadata") or {}
        if test.get("resource_type") != "test" or test_metadata.get("name") != "relationships":
            continue
        if test_metadata.get("namespace") not in (None, "dbt") or (project and test.get("package_name") != project):
            continue
        kwargs = test_metadata.get("kwargs") or {}
        child = test.get("attached_node") or _named(kwargs.get("model"), refs, sources)
        others = [n for n in (test.get("depends_on") or {}).get("nodes") or [] if n != child]
        parent = others[0] if len(others) == 1 else child if not others else _named(kwargs.get("to"), refs, sources)
        from_column, to_column = kwargs.get("column_name") or test.get("column_name"), kwargs.get("field")
        if child not in by_id or parent not in by_id:
            result.counts["relationship to a table left out"] += 1
            continue
        if not isinstance(from_column, str) or not isinstance(to_column, str):
            result.counts["relationship not read"] += 1
            continue
        from_column, to_column = _unquoted(from_column, columns_of[child]), _unquoted(to_column, columns_of[parent])
        name = f"{by_id[child]}__{from_column}__{by_id[parent]}"
        if to_column != from_column:  # another column of the same table: another relationship
            name += f"__{to_column}"
        found[name] = {
            "name": name,
            "from": by_id[child],
            "to": by_id[parent],
            "from_columns": [from_column],
            "to_columns": [to_column],
        }
    return found


def _named(text: Any, refs: dict[str, str], sources: dict[tuple[str, str], str]) -> str | None:
    """The node `ref('…')` or `source('…', '…')` names in a test's arguments."""
    if not isinstance(text, str):
        return None
    if found := SOURCE.search(text):
        return sources.get((found.group(1), found.group(2)))
    if found := REF.search(text):
        return refs.get(found.group(1))
    return None


def identifier(name: str, quote: bool = False) -> str:
    """A name as SQL reads it: as it is when it reads back as that one name, else in double quotes."""
    if not quote and PLAIN.match(name):
        try:
            parsed = sqlglot.parse_one(name)
        except (SqlglotError, RecursionError):
            parsed = None
        if isinstance(parsed, exp.Column) and not parsed.table and parsed.name == name:
            return name
    return '"' + name.replace('"', '""') + '"'


class _Writer(yaml.SafeDumper):
    """YAML a person reads: multi-line text as a block, a short list or mapping of plain values on one line, lists
    indented under their key, keys in Ossie's order, no anchors."""

    def ignore_aliases(self, data: Any) -> bool:
        return True

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def _text(dumper: yaml.SafeDumper, text: str) -> yaml.ScalarNode:
    style = '"' if any(mark in text for mark in YAML_BREAKS) else "|" if "\n" in text else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", text, style=style)


def _one_line(values: list[Any]) -> bool:
    plain = all(isinstance(value, (str, int, float, bool)) or value is None for value in values)
    return plain and not any(isinstance(v, str) and "\n" in v for v in values) and len(json.dumps(values)) <= 80


def _list(dumper: yaml.SafeDumper, items: list[Any]) -> yaml.SequenceNode:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", items, flow_style=_one_line(items))


def _mapping(dumper: yaml.SafeDumper, mapping: dict[Any, Any]) -> yaml.MappingNode:
    return dumper.represent_mapping("tag:yaml.org,2002:map", mapping, flow_style=_one_line(list(mapping.values())))


_Writer.add_representer(str, _text)
_Writer.add_representer(list, _list)
_Writer.add_representer(dict, _mapping)


def render(imported: Import, result: Result, strict: bool = False) -> str:
    """The document as the file written: its YAML read back exactly as built, and checked as the server checks a
    model. With `strict`, `custom_extensions` data is written as JSON strings, as strict Ossie writes it."""
    if found := _value_problem(result.document):
        place, problem = found
        raise ImportProblem(imported.source, [f"{files.location(place, result.document)}: {problem}"])
    document = semantic.strict_copy(result.document) if strict else result.document
    dbt = f" (dbt {result.dbt_version})" if result.dbt_version else ""
    header = (
        f"# Written by semantic-warehouse-mcp import-dbt from {imported.entry.manifest}{dbt}.\n"
        "# Edit the dbt project or the importer's configuration and import again: an edit here is overwritten.\n"
    )
    return write_document(document, header, imported.source, imported.entry.output)


def write_document(document: dict[str, Any], header: str, source: str, output: str) -> str:
    """An Ossie document as the file an importer writes: its YAML read back exactly as built, and checked as the server
    checks a model — else ImportProblem, naming where."""
    text = header + yaml.dump(document, Dumper=_Writer, sort_keys=False, allow_unicode=True, width=120)
    try:
        read_back = files.read_yaml(text)
    except yaml.YAMLError as error:
        problem = f"the document written cannot be read back: {files.yaml_problem(error)} — please report it"
        raise ImportProblem(source, [problem]) from error
    if (place := _difference(document, read_back)) is not None:
        where = files.location(place, document)
        raise ImportProblem(source, [f"the document written would read back otherwise at {where} — please report it"])
    _, problems = semantic.check(read_back, output)
    if problems:
        raise ImportProblem(source, problems)
    return text


def _difference(built: Any, read: Any, place: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
    """The first place where what was read differs from what was built — its type too: 1.0 is not 1 — or None."""
    if isinstance(built, dict) and isinstance(read, dict):
        for key, value in built.items():
            if key not in read:
                return (*place, key)
            if (inner := _difference(value, read[key], (*place, key))) is not None:
                return inner
        return None if len(built) == len(read) else place
    if isinstance(built, list) and isinstance(read, list):
        for index, (value, other) in enumerate(zip(built, read, strict=False)):
            if (inner := _difference(value, other, (*place, index))) is not None:
                return inner
        return None if len(built) == len(read) else place
    return None if type(built) is type(read) and built == read else place


def summary(imported: Import, result: Result) -> list[str]:
    counts = result.counts
    kinds = ", ".join(f"{counts[kind]} {kind}s" for kind in RESOURCES if counts[kind])
    datasets = sum(counts[kind] for kind in RESOURCES)
    lines = [
        f"{datasets} datasets ({kinds}), {counts['field']} fields, {counts['relationship']} relationships",
        f"{counts['primary key']} primary keys, {counts['time column']} time columns",
    ]
    if imported.entry.vendor_name:
        lines.append(
            f"{imported.entry.vendor_name}'s data on {counts['dataset with data']} datasets and "
            f"{counts['field with data']} fields"
        )
    written = {"dbt_depends_on": "the tables they read", "dbt_code": "their SQL"}
    said = [
        f"{counts[setting]} tables with {what}, under {getattr(imported.entry, setting)!r}"
        for setting, what in written.items()
        if getattr(imported.entry, setting)
    ]
    if said:
        lines.append("; ".join(said))
    left_out = {
        "meta left out": "the meta of {} tables and columns — no `vendor_name` to hold it",
        "table of another database": "{} tables of another database than the project's — a connection reads its own",
        "left out by the selection": "{} tables `select` / `exclude` leave out",
        "relationship to a table left out": "{} relationships tests to or from a table not imported",
        "relationship not read": "{} relationships tests whose columns are not names",
    }
    lines += [f"left out: {said.format(counts[kind])}" for kind, said in left_out.items() if counts[kind]]
    return lines + [f"note: {note}" for note in result.notes]


def main(argv: list[str]) -> None:
    """`semantic-warehouse-mcp import-dbt <configuration>`: write the Ossie document; `--check`: write nothing, and
    exit 1 when the file is not what the import writes now. Exit 2 on any problem — never 1, "not current"."""
    parser = argparse.ArgumentParser(
        prog="semantic-warehouse-mcp import-dbt",
        description="Write a dbt project's tables, descriptions, meta and tags as an Apache Ossie document.",
    )
    parser.add_argument("config", help="the importer's configuration file")
    parser.add_argument("--strict", action="store_true", help="write custom_extensions data as JSON strings")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 when the output is not current")
    args = parser.parse_args(argv)
    try:
        current = _run(args)
    except ImportProblem as problem:
        print(f"semantic-warehouse-mcp import-dbt: {problem}", file=sys.stderr)
        sys.exit(2)
    except Exception as error:  # a defect of the importer's own: named, and never exit 1
        said = files.redact(f"{type(error).__name__}: {error}")
        print(f"semantic-warehouse-mcp import-dbt: an unexpected error — please report it: {said}", file=sys.stderr)
        sys.exit(2)
    if current is False:
        sys.exit(1)


def _run(args: argparse.Namespace) -> bool | None:
    """The import the command asks for. With `--check`, whether the output is current; else None, once written."""
    imported = load(args.config)
    result = build(imported, read_manifest(imported))
    text = render(imported, result, strict=args.strict)
    output = imported.entry.output
    try:
        if args.check:
            current = imported.output.read_bytes() if imported.output.is_file() else None
            if current != text.encode("utf-8"):
                print(f"{output} is not what the import writes now: run it without --check", file=sys.stderr)
                return False
            print(f"{output} is current.")
            return True
        imported.output.parent.mkdir(parents=True, exist_ok=True)
        imported.output.write_bytes(text.encode("utf-8"))
    except OSError as error:
        raise ImportProblem(
            imported.source, [f"output: {output} cannot be written: {error.strerror or error}"]
        ) from error
    print(f"Wrote {output} — the Ossie model {imported.entry.model.name}:")
    for line in summary(imported, result):
        print(f"  {line}")
    return None
