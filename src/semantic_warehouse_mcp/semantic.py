"""Semantic models in Apache Ossie, each bound to its connection: read from YAML or JSON files, checked against the
schema of the document's own version, served as written (dev/DESIGN.md, "The semantic layer — Apache Ossie").

A model differs from strict Ossie in one place only: a `custom_extensions` entry's `data` may be written as a YAML
mapping, where Ossie requires a JSON string. It is checked as the JSON string it stands for, and served as data.
"""

from __future__ import annotations

import copy
import decimal
import functools
import json
import math
import pathlib
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from typing import Any

import jsonschema
import sqlglot
import yaml
from sqlglot import exp
from sqlglot.errors import SqlglotError

from . import files

# The versions this server reads, each checked against its own schema (schemas/README.md says where each is from).
SCHEMA_FILES = {"0.2.0.dev0": "ossie-0.2.0.dev0.json", "0.1.1": "osi-0.1.1.json"}
# Versions that hold their models in a root `semantic_model` list, rather than being one model.
WRAPPED = {"0.1.1"}

DOCUMENT_SUFFIXES = {".yaml", ".yml", ".json"}

# A broken document can fail its schema in hundreds of places; the first ones are enough to start fixing.
MAX_PROBLEMS_PER_FILE = 20

# A dataset's `source` is a query when it starts as one; otherwise it names a table or a view.
QUERY_SOURCE = re.compile(r"^\s*\(?\s*(select|with)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Model:
    name: str
    version: str
    where: str  # the file, and the model's place in it when the file holds several: for problems
    file: str  # the file, as the configuration names it
    # The model as written, with each custom extension's `data` read: a YAML mapping as it is, a JSON string decoded
    # (a string that is not JSON stays the string).
    document: dict[str, Any]


@functools.cache
def _validator(version: str) -> jsonschema.Draft202012Validator:
    text = resources.files("semantic_warehouse_mcp").joinpath("schemas", SCHEMA_FILES[version]).read_text("utf-8")
    return jsonschema.Draft202012Validator(json.loads(text))


def files_of(paths: tuple[pathlib.Path, ...]) -> list[pathlib.Path]:
    """The model files of a list of files and folders: a folder's `.yaml`, `.yml` and `.json` files at any depth,
    hidden files and folders left out."""
    found: list[pathlib.Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(
                file
                for file in sorted(path.rglob("*"))
                if file.is_file()
                and file.suffix.lower() in DOCUMENT_SUFFIXES
                and not any(part.startswith(".") for part in file.relative_to(path).parts)
            )
        else:
            found.append(path)
    return list(dict.fromkeys(found))


def read(path: pathlib.Path, file: str) -> tuple[list[Model], list[str]]:
    """The models of one file, checked against the schema of its version and by Ossie's own rules beyond the schema
    (unique names, relationships between datasets that exist). `file` names it in the problems."""
    try:
        data = files.load_file(path)
    except files.FileProblem as problem:
        return [], [f"{file}: {problem}"]
    return check(data, file)


def check(data: Any, file: str) -> tuple[list[Model], list[str]]:
    """The models of a document already read, checked as `read` checks a file's."""
    if not isinstance(data, dict):
        return [], [f"{file}: an Ossie document is a mapping with `version` at the top"]
    version = data.get("version")
    supported = ", ".join(SCHEMA_FILES)
    if version is None:
        return [], [f"{file}: no `version` — an Ossie document names the version it is written in ({supported})"]
    if not isinstance(version, str) or version not in SCHEMA_FILES:
        return [], [f"{file} → version: {version!r} is not a version this server reads (it reads {supported})"]
    problems: list[str] = []
    data = _as_data(data, (), data, problems)
    if problems:
        return [], [f"{file} → {problem}" for problem in problems]

    problems = _schema_problems(strict_copy(data), version, file)
    if problems:
        return [], problems

    models = []
    for prefix, model in _models_of(data, version):
        where = f"{file} → {files.location(prefix, data)}" if prefix else file
        problems.extend(f"{where} → {problem}" for problem in _rule_problems(model))
        for place, _, extension in extensions(model):
            extension["data"] = _read_data(extension["data"])
            if problem := _unicode(extension["data"]):
                problems.append(f"{where} → {files.location(place, model)} → data: {problem}")
        models.append(Model(name=model["name"], version=version, where=where, file=file, document=model))
    if not models and not problems:
        problems.append(f"{file}: holds no model")
    return ([], problems) if problems else (models, [])


def strict_copy(document: dict[str, Any]) -> dict[str, Any]:
    """The document as strict Ossie writes it: each `custom_extensions` data written as a mapping turned into the JSON
    string it stands for."""
    strict = copy.deepcopy(document)
    for _, _, extension in _extensions_of_document(strict, strict["version"]):
        if isinstance(extension.get("data"), dict):
            extension["data"] = json.dumps(extension["data"], ensure_ascii=False, allow_nan=False)
    return strict


def _as_data(node: Any, place: tuple[Any, ...], document: Any, problems: list[str]) -> Any:
    """A document as JSON data, so that it is served as read: a key that is not a string made one, as JSON makes it
    (`1` → "1", `true` → "true"); refused, naming where — two keys that become one, and a value JSON cannot hold
    (a YAML !!set or !!binary, a number that is not finite)."""
    if isinstance(node, dict):
        found: dict[str, Any] = {}
        for key, value in node.items():
            name = _json_key(key)
            if name is None:
                problems.append(f"{files.location(place, document)}: the key {key!r} is not a plain value")
                continue
            if name in found:
                problems.append(f"{files.location(place, document)}: two keys read as {name!r} — write one")
                continue
            found[name] = _as_data(value, (*place, key), document, problems)
        return found
    if isinstance(node, list):
        return [_as_data(item, (*place, index), document, problems) for index, item in enumerate(node)]
    if isinstance(node, float) and not math.isfinite(node):
        problems.append(f"{files.location(place, document)}: {node!r} is a number JSON cannot hold")
        return None
    if node is None or isinstance(node, (str, bool, int, float)):
        return node
    problems.append(f"{files.location(place, document)}: a {type(node).__name__} is a value JSON cannot hold")
    return None


def _json_key(key: Any) -> str | None:
    if isinstance(key, str):
        return key
    if isinstance(key, bool):
        return "true" if key else "false"
    if key is None:
        return "null"
    if isinstance(key, (int, float)):
        return json.dumps(key)
    return None


def _schema_problems(strict: dict[str, Any], version: str, file: str) -> list[str]:
    errors = sorted(_validator(version).iter_errors(strict), key=lambda error: list(map(str, error.absolute_path)))
    problems = []
    for error in errors:
        path = tuple(error.absolute_path)
        where = files.location(path, strict) if path else "the document"
        message = error.message  # it may repeat a value: a password in a URL is left out (files.redact)
        if path[-1:] == ("data",) and error.validator == "type":
            message = "must be a JSON string (as Ossie writes it) or a YAML mapping"
        elif error.validator == "oneOf" and path[-1:] == ("ai_context",):
            message = "must be a string, or a mapping of `instructions`, `synonyms`, `examples`"
        problems.append(f"{file} → {where}: {_short(message)}")
    if len(problems) > MAX_PROBLEMS_PER_FILE:
        more = len(problems) - MAX_PROBLEMS_PER_FILE
        problems = [*problems[:MAX_PROBLEMS_PER_FILE], f"{file}: and {more} more problems against its schema"]
    return problems


def _short(message: str, limit: int = 300) -> str:
    return message if len(message) <= limit else message[: limit - 1] + "…"


def _rule_problems(model: dict[str, Any]) -> list[str]:
    """Ossie's rules a JSON schema cannot hold — those its own validator checks: names unique where they are looked
    up, a relationship between datasets of the model, as many columns on each side."""
    problems = []
    datasets = model.get("datasets", [])
    problems.extend(f"two datasets are named {name!r}" for name in _repeated(d["name"] for d in datasets))
    for index, dataset in enumerate(datasets):
        where = files.location(("datasets", index), model)
        field_names = [field["name"] for field in dataset.get("fields", [])]
        metric_names = [metric["name"] for metric in dataset.get("metrics", [])]
        problems.extend(f"{where}: two fields are named {name!r}" for name in _repeated(field_names))
        problems.extend(f"{where}: two metrics are named {name!r}" for name in _repeated(metric_names))
        problems.extend(
            f"{where}: {name!r} is both a field and a metric — a dataset's fields and metrics share one namespace"
            for name in sorted(set(field_names) & set(metric_names))
        )
    metrics = model.get("metrics", [])
    problems.extend(f"two metrics are named {name!r}" for name in _repeated(m["name"] for m in metrics))
    relationships = model.get("relationships", [])
    problems.extend(f"two relationships are named {name!r}" for name in _repeated(r["name"] for r in relationships))
    names = {dataset["name"] for dataset in datasets}
    for index, relationship in enumerate(relationships):
        where = files.location(("relationships", index), model)
        for side in ("from", "to"):
            if relationship[side] not in names:
                problems.append(f"{where} → {side}: no dataset named {relationship[side]!r} in the model")
        if len(relationship["from_columns"]) != len(relationship["to_columns"]):
            problems.append(f"{where}: `from_columns` and `to_columns` must name as many columns each")
    return problems


def _repeated(names: Any) -> list[str]:
    return sorted(name for name, count in Counter(names).items() if count > 1)


def _read_data(data: Any) -> Any:
    """A custom extension's data as served: a mapping as written; a JSON string decoded — its numbers as written, as
    the answers keep them — or kept as the string when it is not JSON (NaN and Infinity are not)."""
    if not isinstance(data, str):
        return data
    try:
        return json.loads(data, parse_float=_json_number, parse_constant=_not_json)
    except (ValueError, RecursionError):
        return data


def _json_number(text: str) -> float | str:
    """A JSON number with a point or an exponent: the number it is (1.0 stays 1.0) — or its text, when no float is
    exactly it, never rounded."""
    as_float = float(text)
    exact = math.isfinite(as_float) and decimal.Decimal(repr(as_float)) == decimal.Decimal(text)
    return as_float if exact else text


def _not_json(constant: str) -> None:
    raise ValueError(f"{constant} is not JSON")


def _unicode(node: Any) -> str | None:
    """What is wrong when a value holds text that is not Unicode (a lone surrogate) — or None."""
    texts = [node] if isinstance(node, str) else [*node.keys(), *node.values()] if isinstance(node, dict) else node
    for text in texts if isinstance(texts, list) else []:
        if isinstance(text, str):
            try:
                files.unicode_problem(text)
            except yaml.YAMLError as error:
                return str(error)
        elif isinstance(text, (dict, list)) and (problem := _unicode(text)):
            return problem
    return None


def _models_of(document: dict[str, Any], version: str) -> Iterator[tuple[tuple[Any, ...], dict[str, Any]]]:
    """Each model of a document with its place in it: the document itself, or each of a wrapped version's models."""
    if version in WRAPPED:
        for index, model in enumerate(document.get("semantic_model") or []):
            if isinstance(model, dict):
                yield ("semantic_model", index), model
    else:
        yield (), document


def _extensions_of_document(document: dict[str, Any], version: str) -> Iterator[tuple[tuple[Any, ...], str, dict]]:
    for _, model in _models_of(document, version):
        yield from extensions(model)


def extensions(model: dict[str, Any]) -> Iterator[tuple[tuple[Any, ...], str, dict[str, Any]]]:
    """Every custom extension of a model: (its place in the model, the level it is on, the entry). Each level is named
    — the model, a dataset, its fields and metrics, the model's metrics, a relationship — so nothing else inside a
    model (an `ai_context`, which may hold anything) is ever taken for one."""

    def of(node: Any, place: tuple[Any, ...], level: str) -> Iterator[tuple[tuple[Any, ...], str, dict[str, Any]]]:
        if not isinstance(node, dict) or not isinstance(node.get("custom_extensions"), list):
            return
        for index, entry in enumerate(node["custom_extensions"]):
            if isinstance(entry, dict):
                yield (*place, "custom_extensions", index), level, entry

    def items(node: Any, key: str) -> list[tuple[int, Any]]:
        value = node.get(key) if isinstance(node, dict) else None
        return list(enumerate(value)) if isinstance(value, list) else []

    yield from of(model, (), "model")
    for i, dataset in items(model, "datasets"):
        yield from of(dataset, ("datasets", i), "dataset")
        for j, field in items(dataset, "fields"):
            yield from of(field, ("datasets", i, "fields", j), "field")
        for j, metric in items(dataset, "metrics"):
            yield from of(metric, ("datasets", i, "metrics", j), "metric")
    for i, metric in items(model, "metrics"):
        yield from of(metric, ("metrics", i), "metric")
    for i, relationship in items(model, "relationships"):
        yield from of(relationship, ("relationships", i), "relationship")


def is_query(source: str) -> bool:
    """Whether a dataset's `source` is a query rather than the name of a table or a view."""
    return bool(QUERY_SOURCE.match(source))


def is_name(source: str) -> bool:
    """Whether a dataset's `source` reads as the name of a table: parts joined by dots, nothing but a quoted part
    holding spaces or brackets — not a query that does not start with SELECT or WITH (`FROM t`, `VALUES (1)`)."""
    unquoted = re.sub(r'"[^"]*"|`[^`]*`|\[[^\]]*\]', "x", source.strip())
    return bool(unquoted) and not re.search(r"[\s();,'/*-]", unquoted)


def expression(node: dict[str, Any]) -> str | None:
    """A field's or a metric's expression in ANSI SQL — the one every engine's dialect is read from — or the first one
    written when there is none in ANSI SQL."""
    dialects = node.get("expression", {}).get("dialects", [])
    ansi = [item["expression"] for item in dialects if item.get("dialect") == "ANSI_SQL"]
    return ansi[0] if ansi else (dialects[0]["expression"] if dialects else None)


def bare_column(node: dict[str, Any]) -> str | None:
    """The column a field's expression is, when it is nothing but one column's name (`amount`, not `amount - x`)."""
    text = expression(node)
    if text is None:
        return None
    try:
        parsed = sqlglot.parse_one(text)
    except (SqlglotError, RecursionError):
        return None
    return parsed.name if isinstance(parsed, exp.Column) and not parsed.table else None


def fields_read(metric: dict[str, Any], model: dict[str, Any], home: dict[str, Any] | None) -> dict[str, list[dict]]:
    """The fields a metric's expression reads, by dataset, in the order it names them: `dataset.field` in a model's
    metric, a bare field name in a dataset's own (`home`). A name that is no field of the model is left out."""
    text = expression(metric)
    if text is None:
        return {}
    try:
        parsed = sqlglot.parse_one(text)
    except (SqlglotError, RecursionError):
        return {}
    datasets = {dataset["name"]: dataset for dataset in model["datasets"]}
    found: dict[str, list[dict]] = {}
    for column in parsed.find_all(exp.Column):
        dataset = datasets.get(column.table) if column.table else home
        if dataset is None:
            continue
        field = next((f for f in dataset.get("fields", []) if f["name"] == column.name), None)
        if field is not None and field not in found.setdefault(dataset["name"], []):
            found[dataset["name"]].append(field)
    return found


def first_line(text: str | None, limit: int = 200) -> str | None:
    """The start of a description, for a list: its first line, cut at `limit` characters; `…` when there is more."""
    if not text:
        return None
    lines = text.strip().splitlines()
    line = lines[0].strip() if lines else ""
    shown = line + ("…" if len(lines) > 1 else "")
    return shown if len(shown) <= limit else line[: limit - 1] + "…"


# What a dataset or a metric says of itself, before its fields and its own metadata: where a search word weighs most.
HEAD_KEYS = ("name", "source", "label", "description", "ai_context")


def rank(node: Any, words: list[str]) -> tuple[int, int] | None:
    """How closely a dataset or a metric matches a search, every word of which (in any case) must appear in it as
    written — or None when one does not. Lower is closer: first where the words are — 0, all in its name; 1, all in
    what it says of itself (its name, source, label, description, `ai_context`); 2, anywhere (its fields, its own
    metadata too) — then how many of them its name holds."""
    if not words:
        return (0, 0)
    folded = [word.casefold() for word in words]

    def holds(strings: Iterator[str]) -> bool:
        text = " ".join(strings).casefold()
        return all(word in text for word in folded)

    in_name = sum(word in str(node.get("name", "")).casefold() for word in folded)
    if in_name == len(folded):
        return (0, -in_name)
    if holds(_strings([node.get(key) for key in HEAD_KEYS])):
        return (1, -in_name)
    return (2, -in_name) if holds(_strings(node)) else None


def fields_holding(node: dict[str, Any], words: list[str]) -> list[str]:
    """The names of a dataset's fields that hold every word of a search, in any case, anywhere in them as written."""
    folded = [word.casefold() for word in words]
    return [
        field["name"]
        for field in node.get("fields", [])
        if folded and all(word in " ".join(_strings(field)).casefold() for word in folded)
    ]


def held(node: dict[str, Any], wanted: dict[str, list[Any]]) -> set[str]:
    """The keys of `wanted` a node's own metadata holds a wanted value of, in any vendor's data: a list holding one of
    the values, or a value equal to one — a boolean only to a boolean, a number only to a number."""
    found: set[str] = set()
    for extension in node.get("custom_extensions", []):
        data = extension.get("data")
        if not isinstance(data, dict):
            continue
        for key, values in wanted.items():
            value = data.get(key)
            items = value if isinstance(value, list) else [value]
            if key in data and any(_same(item, one) for item in items for one in values):
                found.add(key)
    return found


def metadata_match(node: dict[str, Any], wanted: dict[str, list[Any]]) -> list[str] | None:
    """Whether a dataset or a metric holds, for every key of `wanted`, one of its values — itself, or one of its
    fields: None when it does not; else the names of the fields that hold all it does not hold itself (none when it
    holds everything itself)."""
    missing = {key: values for key, values in wanted.items() if key not in held(node, wanted)}
    if not missing:
        return []
    named = [field["name"] for field in node.get("fields", []) if held(field, missing) == set(missing)]
    return named or None


def _same(value: Any, wanted: Any) -> bool:
    if isinstance(value, (dict, list)) or isinstance(value, bool) != isinstance(wanted, bool):
        return False
    return isinstance(value, str) == isinstance(wanted, str) and value == wanted


def _strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):  # the values only: every dataset has a `fields` key, and a search would find them all
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)
    elif node is not None:
        yield str(node)
