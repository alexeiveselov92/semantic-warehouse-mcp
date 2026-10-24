"""A team's own metadata, declared: which keys a vendor's `custom_extensions` hold, on which levels of a semantic
model, of which type, and the closed lists of values with their definitions (dev/DECISIONS.md, "Your own metadata").

The server knows no team's keys — every one is declared by the team, in its vocabulary file — and checks a model
against it at start: an unknown key or value stops the server, naming where it is.
"""

from __future__ import annotations

import difflib
import pathlib
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import files

# The places of an Ossie model that hold `custom_extensions`.
LEVELS = ("model", "dataset", "field", "metric", "relationship")

Level = Literal["model", "dataset", "field", "metric", "relationship"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class KeyEntry(_Strict):
    description: str | None = None
    levels: Annotated[list[Level], Field(min_length=1)] | None = None  # left out: every level
    type: Literal["string", "list", "mapping", "number", "boolean", "any"] = "string"
    # A closed list: each value with its definition, or the values alone. A string must be one; a list's items too.
    # Its shape is checked in `read`, to name the problem in the vocabulary's own terms.
    values: Any = None
    in_lists: bool = False  # shown in list_datasets and list_metrics, not only in describe_*


class VocabularyFile(_Strict):
    vendor_name: Annotated[str, Field(min_length=1)]
    unknown_keys: Literal["refuse", "allow"] = "refuse"
    keys: Annotated[dict[str, KeyEntry], Field(min_length=1)]


@dataclass(frozen=True)
class Vocabulary:
    vendor_name: str
    file: str  # as the configuration names it
    unknown_keys: str
    keys: dict[str, KeyEntry]

    def check(self, data: Any, level: str) -> list[str]:
        """What is wrong with a vendor's data on one level of a model, one line per problem, each naming its key."""
        if not isinstance(data, dict):
            return [f"the data must be a mapping of the keys {self.vendor_name}'s vocabulary declares ({self.file})"]
        problems = []
        for key, value in data.items():
            entry = self.keys.get(key)
            if entry is None:
                if self.unknown_keys == "refuse":
                    problems.append(f"{key}: not a key of {self.vendor_name}'s vocabulary{_hint(key, self.keys)}")
                continue
            if entry.levels is not None and level not in entry.levels:
                allowed = ", ".join(entry.levels)
                problems.append(f"{key}: not used on a {level} (the vocabulary allows it on: {allowed})")
                continue
            secret = bool(files.SECRET_KEY.search(key))  # its value may be a secret: never repeated
            problems.extend(f"{key}: {problem}" for problem in _check_value(value, entry, secret))
        return problems

    def definitions(self, data: Any) -> dict[str, dict[str, Any]]:
        """What the vocabulary says of the keys and values one data uses: each key's description, and the definitions
        of the values it holds — for an agent to read what a value means, not only its name."""
        if not isinstance(data, dict):
            return {}
        found: dict[str, dict[str, Any]] = {}
        for key, value in data.items():
            entry = self.keys.get(key)
            if entry is None:
                continue
            said: dict[str, Any] = {}
            if entry.description:
                said["description"] = entry.description
            if isinstance(entry.values, dict):
                used = [item for item in (value if isinstance(value, list) else [value]) if isinstance(item, str)]
                defined = {item: entry.values[item] for item in used if entry.values.get(item)}
                if defined:
                    said["values"] = defined
            if said:
                found[key] = said
        return found


def _check_value(value: Any, entry: KeyEntry, secret: bool) -> list[str]:
    kind = entry.type
    expected = {
        "string": lambda v: isinstance(v, str),
        "list": lambda v: isinstance(v, list),
        "mapping": lambda v: isinstance(v, dict),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "any": lambda v: True,
    }[kind]
    if not expected(value):
        return [f"must be a {kind}, got {_kind_of(value, secret)}"]
    if entry.values is None:
        return []
    allowed = list(entry.values)
    items = value if kind == "list" else [value]
    return [
        ("a value is not one of its values" if secret else f"{item!r} is not one of its values{_hint(item, allowed)}")
        for item in items
        if not isinstance(item, str) or item not in allowed
    ]


def _hint(word: Any, known: Any) -> str:
    """The likely one for a typo; else the whole list when it is short enough to read."""
    known = list(known)
    hint = difflib.get_close_matches(str(word), known, n=1)
    if hint:
        return f" — did you mean {hint[0]!r}?"
    return f" (it has: {', '.join(known)})" if len(known) <= 12 else ""


def _kind_of(value: Any, secret: bool) -> str:
    if value is None:
        return "nothing"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string" if secret else f"a string ({value!r})"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    return type(value).__name__


def read(path: pathlib.Path, file: str) -> tuple[Vocabulary | None, list[str]]:
    """One vocabulary file, checked. `file` is how the configuration names it, for the problems."""
    try:
        data = files.load_file(path)
    except files.FileProblem as problem:
        return None, [f"{file}: {problem}"]
    if not isinstance(data, dict):
        return None, [f"{file}: a vocabulary is a mapping with `vendor_name` and `keys` at the top"]
    try:
        parsed = VocabularyFile.model_validate(data)
    except ValidationError as error:
        return None, [f"{file} → {line}" for line in files.describe(error, data, VocabularyFile)]
    problems = []
    for key, entry in parsed.keys.items():
        if entry.values is None:
            continue
        values = entry.values
        listed = isinstance(values, list) and all(isinstance(value, str) for value in values)
        defined = isinstance(values, dict) and all(
            isinstance(value, str) and (definition is None or isinstance(definition, str))
            for value, definition in values.items()
        )
        if not values or not (listed or defined):
            problems.append(
                f"{file} → keys → {key} → values: a list of strings, or a mapping of each value to its definition"
            )
        elif entry.type not in ("string", "list"):
            problems.append(f"{file} → keys → {key}: `values` close a string or a list, not a {entry.type}")
    if problems:
        return None, problems
    return Vocabulary(parsed.vendor_name, file, parsed.unknown_keys, dict(parsed.keys)), []
