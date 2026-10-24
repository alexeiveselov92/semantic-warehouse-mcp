"""The configuration file and its check: a wrong entry stops the server, naming it (docs/reference/configuration.md)."""

from __future__ import annotations

import difflib
import pathlib
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import files, guard, semantic, vocabulary
from .adapters import ENGINES, Adapter, AdapterError, adapter_class
from .context import ContextError, documents
from .semantic import Model
from .vocabulary import Vocabulary

# A readable pattern or a large table: schema.table, either part may hold * or ? globs.
TWO_PARTS = re.compile(r"^[^.\s]+\.[^.\s]+$")


class ConfigError(Exception):
    """Every problem found, one per line, each naming its entry — a password written inside a URL left out."""

    def __init__(self, source: str, problems: list[str]) -> None:
        self.source = source
        self.problems = [files.redact(problem) for problem in problems]
        problems = self.problems
        lines = "\n".join(f"  - {problem}" for problem in problems)
        noun = "problem" if len(problems) == 1 else "problems"
        super().__init__(f"the configuration {source} has {len(problems)} {noun}:\n{lines}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LargeTableEntry(_Strict):
    table: str
    bound_by: str


class ConnectionLimits(_Strict):
    rows: Annotated[int, Field(gt=0, le=100_000)] = 500
    seconds: Annotated[float, Field(gt=0, le=3600)] = 30


class ConnectionEntry(BaseModel):
    # The engine's own keys (a path, a URL, …) are checked against its adapter's settings, in `parse`.
    model_config = ConfigDict(extra="ignore", strict=True)

    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
    engine: str
    description: str | None = None
    readable: Annotated[list[str], Field(min_length=1)]
    large_tables: list[LargeTableEntry] = []
    limits: ConnectionLimits = ConnectionLimits()
    instructions: str | None = None
    context: list[str] = []
    semantic_models: list[str] = []


class ServerLimits(_Strict):
    answer_characters: Annotated[int, Field(ge=1000, le=1_000_000)] = 40_000


class ConfigFile(_Strict):
    connections: Annotated[list[ConnectionEntry], Field(min_length=1)]
    limits: ServerLimits = ServerLimits()
    instructions: str | None = None
    context: list[str] = []
    vocabularies: list[str] = []


@dataclass(frozen=True)
class Connection:
    name: str
    engine: str
    description: str | None
    readable: tuple[str, ...]
    large_tables: tuple[LargeTableEntry, ...]
    limits: ConnectionLimits
    adapter: Adapter
    rules: guard.Rules
    instructions: str | None = None
    context: tuple[pathlib.Path, ...] = ()
    models: tuple[Model, ...] = ()


@dataclass(frozen=True)
class Config:
    source: str
    connections: dict[str, Connection]
    limits: ServerLimits
    instructions: str | None = None
    context: tuple[pathlib.Path, ...] = ()
    # By vendor name, one for the whole server: a vendor's keys mean the same in every model that uses them.
    vocabularies: dict[str, Vocabulary] = field(default_factory=dict)


def load(path: str | pathlib.Path) -> Config:
    """Read and check a configuration file, then reach every connection. Raises ConfigError naming each problem."""
    path = pathlib.Path(path)
    source = str(path)
    try:
        data = files.load_file(path)
    except files.FileProblem as problem:
        raise ConfigError(source, [str(problem)]) from problem
    return parse(data, source=source, base_dir=path.resolve().parent)


def parse(data: Any, *, source: str, base_dir: pathlib.Path, reach: bool = True) -> Config:
    """Check configuration data already read from YAML; with `reach`, also open every connection."""
    if not isinstance(data, dict):
        raise ConfigError(source, ["the file must hold a mapping with `connections:` at the top"])
    problems: list[str] = []
    parsed: ConfigFile | None = None
    try:
        parsed = ConfigFile.model_validate(data)
    except ValidationError as error:
        problems.extend(files.describe(error, data, ConfigFile))

    # A connection's keys are the common ones and its engine's: checked here, so a typo is named with the rest.
    raw_connections = data.get("connections")
    settings: list[Any] = []
    for index, raw in enumerate(raw_connections if isinstance(raw_connections, list) else []):
        if not isinstance(raw, dict):
            settings.append(None)
            continue
        where = files.location(("connections", index), data)
        cls = adapter_class(raw["engine"]) if isinstance(raw.get("engine"), str) else None
        engine_keys = list(cls.settings_model.model_fields) if cls else []
        known = list(ConnectionEntry.model_fields) + engine_keys
        for key in raw:
            if key not in known:
                hint = difflib.get_close_matches(str(key), known, n=1)
                suffix = f" — did you mean {hint[0]!r}?" if hint else f" (known: {', '.join(known)})"
                problems.append(f"{where} → {key}: unknown key{suffix}")
        if isinstance(raw.get("engine"), str) and cls is None:
            supported = ", ".join(sorted(ENGINES))
            problems.append(f"{where} → engine: {raw['engine']!r} is not supported (supported: {supported})")
        if cls is None:
            settings.append(None)
            continue
        try:
            settings.append(cls.settings_model.model_validate({k: v for k, v in raw.items() if k in engine_keys}))
        except ValidationError as error:
            problems.extend(f"{where} → {line}" for line in files.describe(error, raw, cls.settings_model))
            settings.append(None)
    if parsed is None or problems:
        raise ConfigError(source, problems)

    vocabularies, vocabulary_problems = _vocabularies(parsed.vocabularies, base_dir)
    problems.extend(vocabulary_problems)
    connections: dict[str, Connection] = {}
    for index, entry in enumerate(parsed.connections):
        where = files.location(("connections", index), data)
        if entry.name in connections:
            problems.append(f"{where}: another connection has this name")
            continue
        table_problems = _check_tables(entry)
        context_paths, context_problems = _context(entry.context, base_dir)
        models, model_problems = _models(entry.semantic_models, base_dir)
        entry_problems = table_problems + context_problems + model_problems
        if entry_problems:
            problems.extend(f"{where} → {problem}" for problem in entry_problems)
            continue
        adapter = adapter_class(entry.engine)(settings[index], base_dir)
        if reach:
            try:
                adapter.check()
            except AdapterError as error:
                problems.append(f"{where}: {error}")
                continue
        rules = _rules(entry, adapter)  # after `check`, which may learn the connection's own catalog
        binding_problems = _bind(models, rules, adapter if reach else None) + _own_metadata(models, vocabularies)
        if binding_problems:
            problems.extend(f"{where} → semantic_models → {problem}" for problem in binding_problems)
            continue
        connections[entry.name] = Connection(
            name=entry.name,
            engine=entry.engine,
            description=entry.description,
            readable=tuple(entry.readable),
            large_tables=tuple(entry.large_tables),
            limits=entry.limits,
            adapter=adapter,
            rules=rules,
            instructions=entry.instructions,
            context=context_paths,
            models=tuple(models),
        )
    server_context, server_context_problems = _context(parsed.context, base_dir)
    problems.extend(server_context_problems)
    if problems:
        raise ConfigError(source, problems)
    return Config(
        source=source,
        connections=connections,
        limits=parsed.limits,
        instructions=parsed.instructions,
        context=server_context,
        vocabularies=vocabularies,
    )


def _rules(entry: ConnectionEntry, adapter: Adapter) -> guard.Rules:
    """What the guard enforces on a connection: its configuration, and its engine's dialect and functions."""
    return guard.Rules(
        dialect=adapter.dialect,
        default_schema=adapter.default_schema,
        readable=tuple(entry.readable),
        large_tables=tuple(guard.LargeTable(large.table, large.bound_by) for large in entry.large_tables),
        catalog=adapter.catalog,
        allowed_table_functions=adapter.allowed_table_functions,
        denied_functions=adapter.denied_functions,
        table_reading_functions=adapter.table_reading_functions,
    )


def _context(entries: list[str], base_dir: pathlib.Path) -> tuple[tuple[pathlib.Path, ...], list[str]]:
    """Context files and folders, relative to the configuration's folder; each must exist, and no two documents may
    share a name."""
    paths = tuple((base_dir / entry).resolve() for entry in entries)
    problems = [f"context: {entry!r} does not exist ({path})" for entry, path in zip(entries, paths, strict=True)
                if not path.exists()]  # fmt: skip
    if not problems:
        try:
            documents(paths)
        except ContextError as error:
            problems.append(f"context: {error}")
    return paths, problems


def _vocabularies(entries: list[str], base_dir: pathlib.Path) -> tuple[dict[str, Vocabulary], list[str]]:
    """Vocabulary files (and folders of them) relative to the configuration's folder, by vendor name. A vendor declared
    twice is refused."""
    found: dict[str, Vocabulary] = {}
    problems: list[str] = []
    for path, file in _files(entries, base_dir, "vocabularies", problems):
        read, read_problems = vocabulary.read(path, file)
        problems.extend(f"vocabularies → {problem}" for problem in read_problems)
        if read is None:
            continue
        if read.vendor_name in found:
            problems.append(
                f"vocabularies: {file} declares the vendor {read.vendor_name!r}, as {found[read.vendor_name].file} "
                "does — one vocabulary per vendor"
            )
            continue
        found[read.vendor_name] = read
    return found, problems


def _models(entries: list[str], base_dir: pathlib.Path) -> tuple[list[Model], list[str]]:
    """A connection's semantic models: files and folders relative to the configuration's folder, each checked against
    its version's schema. No two models of a connection may share a name — agents name a model by it."""
    models: list[Model] = []
    problems: list[str] = []
    for path, file in _files(entries, base_dir, "semantic_models", problems):
        read, read_problems = semantic.read(path, file)
        problems.extend(f"semantic_models → {problem}" for problem in read_problems)
        for model in read:
            same = [other.where for other in models if other.name == model.name]
            if same:
                problems.append(f"semantic_models: two models are named {model.name!r} ({same[0]}; {model.where})")
            else:
                models.append(model)
    return models, problems


def _files(entries: list[str], base_dir: pathlib.Path, key: str, problems: list[str]) -> list[tuple[pathlib.Path, str]]:
    """The files of a list of files and folders, each with the name it goes by in a problem (its path as written in
    the configuration, and inside a folder). A missing entry is a problem, and so is a folder with no file to read."""
    found = []
    for entry in entries:
        path = (base_dir / entry).resolve()
        if not path.exists():
            problems.append(f"{key}: {entry!r} does not exist ({path})")
            continue
        inside = semantic.files_of((path,))
        if not inside:
            problems.append(f"{key}: {entry!r} holds no .yaml, .yml or .json file ({path})")
        for file in inside:
            name = entry if file == path else f"{entry.rstrip('/')}/{file.relative_to(path).as_posix()}"
            found.append((file, name))
    return found


def _bind(models: list[Model], rules: guard.Rules, adapter: Adapter | None) -> list[str]:
    """Each dataset's source on its connection: a table it may read (and, with `adapter`, one that exists), or a query
    the guard passes."""
    problems = []
    stored: set[tuple[str, str]] | None = None
    for model in models:
        for index, dataset in enumerate(model.document["datasets"]):
            where = f"{model.where} → {files.location(('datasets', index), model.document)} → source"
            source = dataset["source"]
            if not semantic.is_query(source) and not semantic.is_name(source):
                problems.append(
                    f"{where}: {source[:80]!r} is neither a table name (schema.table) nor a query starting with SELECT "
                    "or WITH"
                )
                continue
            try:
                if semantic.is_query(source):
                    guard.check(source, rules)
                    continue
                schema, table = guard.resolve_table(source, rules)
            except guard.Refused as refusal:
                problems.append(f"{where}: {refusal.message} ({refusal.code})")
                continue
            if adapter is None:
                continue
            if stored is None:
                try:
                    stored = {
                        (guard.stored_name(t.schema, rules), guard.stored_name(t.name, rules)) for t in adapter.tables()
                    }
                except AdapterError as error:
                    return [f"the tables of the connection cannot be listed: {error}"]
            if (schema, table) not in stored:
                problems.append(f"{where}: there is no table or view {schema}.{table} on the connection")
    return problems


def _own_metadata(models: list[Model], vocabularies: dict[str, Vocabulary]) -> list[str]:
    """Each custom extension of a vendor with a vocabulary, checked against it; others pass as written — but not one
    whose vendor name differs from a declared one only in case or spaces, which would pass unchecked by a slip."""
    problems = []
    loosely = {vendor.casefold().strip(): vendor for vendor in vocabularies}
    for model in models:
        for place, level, extension in semantic.extensions(model.document):
            vendor = extension["vendor_name"]
            declared = vocabularies.get(vendor)
            if declared is None:
                meant = loosely.get(vendor.casefold().strip())
                if meant is not None:
                    where = f"{model.where} → {files.location(place, model.document)} → vendor_name"
                    problems.append(
                        f"{where}: {vendor!r} is not {meant!r}, the vendor a vocabulary declares — write it exactly, "
                        "or its data passes unchecked"
                    )
                continue
            where = f"{model.where} → {files.location(place, model.document)} ({declared.vendor_name})"
            data = extension["data"]
            if isinstance(data, str):
                problems.append(f"{where}: the data is not JSON, so {declared.file} cannot check it")
                continue
            problems.extend(f"{where} → {problem}" for problem in declared.check(data, level))
    return problems


def _check_tables(entry: ConnectionEntry) -> list[str]:
    problems = []
    for pattern in entry.readable:
        if not TWO_PARTS.match(pattern):
            problems.append(f"readable: {pattern!r} must be schema.table (globs allowed: marts.*, *.*)")
    for large in entry.large_tables:
        if not TWO_PARTS.match(large.table):
            problems.append(f"large_tables: {large.table!r} must be schema.table")
            continue
        schema, table = large.table.split(".")
        if not any(
            fnmatchcase(schema, p.split(".")[0]) and fnmatchcase(table, p.split(".")[1])
            for p in entry.readable
            if TWO_PARTS.match(p)
        ):
            problems.append(f"large_tables: {large.table!r} is not covered by any readable pattern")
    return problems
