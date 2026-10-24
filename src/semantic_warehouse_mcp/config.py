"""The configuration file and its check: a wrong entry stops the server, naming it (docs/reference/configuration.md)."""

from __future__ import annotations

import difflib
import pathlib
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .adapters import ENGINES, Adapter, AdapterError, adapter_class
from .context import ContextError, documents

# A readable pattern or a large table: schema.table, either part may hold * or ? globs.
TWO_PARTS = re.compile(r"^[^.\s]+\.[^.\s]+$")


class ConfigError(Exception):
    """Every problem found, one per line, each naming its entry."""

    def __init__(self, source: str, problems: list[str]) -> None:
        self.source = source
        self.problems = problems
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


class ServerLimits(_Strict):
    answer_characters: Annotated[int, Field(ge=1000, le=1_000_000)] = 40_000


class ConfigFile(_Strict):
    connections: Annotated[list[ConnectionEntry], Field(min_length=1)]
    limits: ServerLimits = ServerLimits()
    instructions: str | None = None
    context: list[str] = []


@dataclass(frozen=True)
class Connection:
    name: str
    engine: str
    description: str | None
    readable: tuple[str, ...]
    large_tables: tuple[LargeTableEntry, ...]
    limits: ConnectionLimits
    adapter: Adapter
    instructions: str | None = None
    context: tuple[pathlib.Path, ...] = ()


@dataclass(frozen=True)
class Config:
    source: str
    connections: dict[str, Connection]
    limits: ServerLimits
    instructions: str | None = None
    context: tuple[pathlib.Path, ...] = ()


class _NoDuplicateKeysLoader(yaml.SafeLoader):
    """YAML keeps the last of two equal keys silently; a configuration must not."""


def _mapping_without_duplicates(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"the key {key!r} appears twice in one mapping", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node)


_NoDuplicateKeysLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping_without_duplicates)


def load(path: str | pathlib.Path) -> Config:
    """Read and check a configuration file, then reach every connection. Raises ConfigError naming each problem."""
    path = pathlib.Path(path)
    source = str(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(source, [f"cannot read the file: {error.strerror or error}"]) from error
    try:
        data = yaml.load(text, Loader=_NoDuplicateKeysLoader)
    except yaml.YAMLError as error:
        raise ConfigError(source, [f"not valid YAML: {error}"]) from error
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
        problems.extend(_describe(error, data))

    # A connection's keys are the common ones and its engine's: checked here, so a typo is named with the rest.
    raw_connections = data.get("connections")
    settings: list[Any] = []
    for index, raw in enumerate(raw_connections if isinstance(raw_connections, list) else []):
        if not isinstance(raw, dict):
            settings.append(None)
            continue
        where = _location(("connections", index), data)
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
            problems.extend(f"{where} → {line}" for line in _describe(error, raw))
            settings.append(None)
    if parsed is None or problems:
        raise ConfigError(source, problems)

    connections: dict[str, Connection] = {}
    for index, entry in enumerate(parsed.connections):
        where = _location(("connections", index), data)
        if entry.name in connections:
            problems.append(f"{where}: another connection has this name")
            continue
        table_problems = _check_tables(entry)
        context_paths, context_problems = _context(entry.context, base_dir)
        if table_problems or context_problems:
            problems.extend(f"{where} → {problem}" for problem in table_problems + context_problems)
            continue
        adapter = adapter_class(entry.engine)(settings[index], base_dir)
        if reach:
            try:
                adapter.check()
            except AdapterError as error:
                problems.append(f"{where}: {error}")
                continue
        connections[entry.name] = Connection(
            name=entry.name,
            engine=entry.engine,
            description=entry.description,
            readable=tuple(entry.readable),
            large_tables=tuple(entry.large_tables),
            limits=entry.limits,
            adapter=adapter,
            instructions=entry.instructions,
            context=context_paths,
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


# A value under one of these keys may be a secret written by mistake: an error never repeats it.
SECRET_KEY = re.compile(r"(_env$|password|secret|token|url)", re.IGNORECASE)  # a URL may hold a password


def _describe(error: ValidationError, data: Any) -> list[str]:
    """Pydantic's errors in the configuration's own terms: where, what is wrong, and a likely fix for a typo."""
    lines = []
    for item in error.errors():
        location = _location(item["loc"], data)
        kind = item["type"]
        if kind == "extra_forbidden":
            key = str(item["loc"][-1])
            known = _known_keys(item["loc"][:-1])
            hint = difflib.get_close_matches(key, known, n=1)
            suffix = f" — did you mean {hint[0]!r}?" if hint else (f" (known: {', '.join(known)})" if known else "")
            lines.append(f"{location}: unknown key{suffix}")
        elif kind == "missing":
            lines.append(f"{location}: required")
        else:
            message = item["msg"][0].lower() + item["msg"][1:]
            secret = any(isinstance(step, str) and SECRET_KEY.search(step) for step in item["loc"])
            lines.append(f"{location}: {message}" + ("" if secret else f" (got {item['input']!r})"))
    return lines


def _location(loc: tuple[Any, ...], data: Any) -> str:
    parts: list[str] = []
    node = data
    for step in loc:
        if isinstance(step, int):
            item = node[step] if isinstance(node, list) and step < len(node) else None
            name = item.get("name") if isinstance(item, dict) else None
            parts[-1] = f"{parts[-1]}[{step}]" + (f' "{name}"' if isinstance(name, str) else "")
            node = item
        else:
            parts.append(str(step))
            node = node.get(step) if isinstance(node, dict) else None
    return " → ".join(parts) if parts else "the file"


def _known_keys(loc: tuple[Any, ...]) -> list[str]:
    model: Any = ConfigFile
    for step in loc:
        if isinstance(step, int):
            continue
        field = model.model_fields.get(step)
        if field is None:
            return []
        annotation = field.annotation
        args = getattr(annotation, "__args__", ())
        model = next((a for a in (annotation, *args) if isinstance(a, type) and issubclass(a, BaseModel)), None)
        if model is None:
            return []
    return list(model.model_fields)
