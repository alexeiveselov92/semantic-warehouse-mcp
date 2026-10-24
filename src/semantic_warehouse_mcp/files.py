"""The YAML files a team writes — the configuration, its semantic models, its vocabularies: read as written (a key
written twice refused, `no` and dates kept as the text they are), and a place in one named the way people read it —
never with a line of the file, which may hold a secret."""

from __future__ import annotations

import difflib
import pathlib
import re
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

# The most values a file may hold once its YAML aliases are expanded: a few lines of aliases can stand for billions.
MAX_VALUES = 1_000_000

_BOOL = "tag:yaml.org,2002:bool"
_TIMESTAMP = "tag:yaml.org,2002:timestamp"


class FileProblem(Exception):
    """What is wrong with reading a file, in one line — never a line of its content."""


class _TeamLoader(yaml.SafeLoader):
    """YAML 1.1 reads `on`, `no`, `yes`, `off` as booleans and `2026-03-01` as a date: a team's `country: NO` would be
    served as false, its times rewritten. Here only `true` and `false` are booleans, and a date stays the text written.
    A key written twice in one mapping is refused, where YAML would keep the last silently."""


_TeamLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag not in (_BOOL, _TIMESTAMP)]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_TeamLoader.add_implicit_resolver(_BOOL, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def _mapping_without_duplicates(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        if isinstance(key, (list, dict, set)):
            raise yaml.constructor.ConstructorError(
                None, None, "a key must be a plain value, not a list or a mapping", key_node.start_mark
            )
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"the key {key!r} appears twice in one mapping", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node)


_TeamLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping_without_duplicates)

_SURROGATE = re.compile("[\ud800-\udfff]")


def _text(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> str:
    """Text as written — a character escaped as a surrogate pair (`\\ud83d\\ude00`, as JSON writes it) read as that
    one character; a lone surrogate is left for the check to refuse."""
    text = loader.construct_scalar(node)
    if _SURROGATE.search(text):
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "surrogatepass")
    return text


_TeamLoader.add_constructor("tag:yaml.org,2002:str", _text)


def read_yaml(text: str) -> Any:
    """YAML (or JSON) text as data. Raises yaml.YAMLError: a key written twice, a value Python cannot hold, text that
    is not Unicode (an escaped lone surrogate, which no answer can carry), a file whose aliases expand past MAX_VALUES
    or refer to themselves."""
    try:
        data = yaml.load(text, Loader=_TeamLoader)
    except (ValueError, TypeError, RecursionError) as error:  # a number of thousands of digits, nesting past Python's
        raise yaml.YAMLError(f"a value that cannot be read: {error}") from error
    try:
        size = _values(data, {}, set())
    except RecursionError as error:
        raise yaml.YAMLError("nested too deep") from error
    if size > MAX_VALUES:
        raise yaml.YAMLError(f"its aliases expand to more than {MAX_VALUES:,} values")
    return data


def _values(node: Any, counted: dict[int, int], open_: set[int]) -> int:
    """How many values a node holds with its aliases expanded — each shared node counted once, in linear time."""
    if isinstance(node, str):
        unicode_problem(node)
    if not isinstance(node, (dict, list)):
        return 1
    key = id(node)
    if key in open_:
        raise yaml.YAMLError("an alias refers to the mapping or list that holds it")
    if key not in counted:
        open_.add(key)
        children = [*node.keys(), *node.values()] if isinstance(node, dict) else node
        counted[key] = 1 + sum(_values(child, counted, open_) for child in children)
        open_.discard(key)
    return counted[key]


def unicode_problem(text: str) -> None:
    """Raises yaml.YAMLError for text holding a lone surrogate (`\\ud800` escaped in YAML or JSON): not Unicode, it
    cannot be sent in an answer."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        found = f"\\u{ord(text[error.start]):04x}"
        raise yaml.YAMLError(
            f"text that is not Unicode — a lone surrogate, {found}: write the character meant"
        ) from None


def load_file(path: pathlib.Path) -> Any:
    """A team's YAML or JSON file as data. Raises FileProblem saying what is wrong, never quoting the file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise FileProblem(f"cannot read the file: {error.strerror or error}") from error
    except UnicodeDecodeError as error:
        raise FileProblem(f"not UTF-8 text (byte {error.start} is not)") from error
    try:
        return read_yaml(text)
    except yaml.YAMLError as error:
        raise FileProblem(f"not valid YAML: {yaml_problem(error)}") from error


def yaml_problem(error: yaml.YAMLError) -> str:
    """A YAML error without the lines around it, which may hold a secret: what is wrong, and where."""
    if isinstance(error, yaml.MarkedYAMLError):
        mark = error.problem_mark or error.context_mark
        what = " ".join(part for part in (error.context, error.problem) if part) or "not valid YAML"
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        return redact(what + where)
    return redact(str(error))


# A password written inside a URL: user:password@host — up to the last @, since a password may hold @ or /.
_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s'\"]*@")


def redact(text: str) -> str:
    """A problem's text with any password written inside a URL left out."""
    return _CREDENTIALS.sub(r"\1…@", text)


def nameable(name: str) -> bool:
    """Whether a file's name can be sent to an agent and asked for back: one that is not UTF-8 cannot."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


# A variable's name as people write one. Under an `_env` key anything else may be a secret pasted in its place.
VARIABLE_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def variable(name: str, where: str) -> str:
    """How a message names a variable: by its name — unless that does not look like one (A_NAME_LIKE_THIS), when it
    may be a secret pasted in its place, and is never repeated: then by `where` it is written."""
    if VARIABLE_NAME.match(name):
        return name
    return f"named in {where} (not repeated: it does not look like a variable's name, and may be a secret)"


# A value under one of these keys may be a secret written by mistake: an error never repeats it.
SECRET_KEY = re.compile(r"(_env$|password|secret|token|url)", re.IGNORECASE)  # a URL may hold a password


def describe(error: ValidationError, data: Any, model: type[BaseModel]) -> list[str]:
    """Pydantic's errors in the file's own terms: where, what is wrong, and a likely fix for a typo. `model` is the
    file's root, for the keys known where an unknown one is written."""
    lines = []
    for item in error.errors():
        loc = _without_tags(item["loc"], data)
        if loc is None:
            continue
        where = location(loc, data)
        kind = item["type"]
        if kind == "extra_forbidden":
            key = str(loc[-1])
            known = _known_keys(model, loc[:-1])
            hint = difflib.get_close_matches(key, known, n=1)
            suffix = f" — did you mean {hint[0]!r}?" if hint else (f" (known: {', '.join(known)})" if known else "")
            lines.append(f"{where}: unknown key{suffix}")
        elif kind == "missing":
            lines.append(f"{where}: required")
        else:
            message = item["msg"][0].lower() + item["msg"][1:]
            secret = any(isinstance(step, str) and SECRET_KEY.search(step) for step in loc)
            got = item["input"]
            if secret:
                lines.append(f"{where}: {message}")
            elif _holds_secret(got):
                lines.append(f"{where}: {message} (got a {'mapping' if isinstance(got, dict) else 'list'})")
            else:
                lines.append(f"{where}: {message} (got {got!r})")
    return lines


# How pydantic names the kind of value an entry that may be one of several was read as (`str | InRepository`).
_PLAIN_TAGS = {"str", "int", "float", "bool", "list", "dict"}


def _without_tags(loc: tuple[Any, ...], data: Any) -> tuple[Any, ...] | None:
    """An error's place without the steps pydantic adds for an entry that may be of several kinds (`str`, a model's
    name) — or None when the error is of a kind the entry was plainly not meant as: a mapping read as text, text read
    as a mapping. Keys of a file are lowercase, so a step not in the data and named by a type is such a step."""
    kept: list[Any] = []
    node = data
    for step in loc:
        if isinstance(step, str) and not (isinstance(node, dict) and step in node):
            if step in _PLAIN_TAGS:
                if isinstance(node, (dict, list)):
                    return None
                continue
            if step[:1].isupper():
                if not isinstance(node, dict):
                    return None
                continue
        kept.append(step)
        if isinstance(node, dict):
            node = node.get(step)
        elif isinstance(node, list) and isinstance(step, int) and step < len(node):
            node = node[step]
        else:
            node = None
    return tuple(kept)


def _holds_secret(value: Any) -> bool:
    """Whether a mapping or a list holds, at any depth, a key that may name a secret — its value never repeated."""
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if any(isinstance(key, str) and SECRET_KEY.search(key) for key in node):
                return True
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return False


def _known_keys(model: type[BaseModel], loc: tuple[Any, ...]) -> list[str]:
    current: Any = model
    a_mapping_key = False  # the step after a mapping of entries (`keys:`) is an entry's own name, not a key
    for step in loc:
        if isinstance(step, int) or a_mapping_key:
            a_mapping_key = False
            continue
        field = current.model_fields.get(step)
        if field is None:
            return []
        annotation = field.annotation
        a_mapping_key = getattr(annotation, "__origin__", None) is dict
        current = next((a for a in _types(annotation) if isinstance(a, type) and issubclass(a, BaseModel)), None)
        if current is None:
            return []
    return list(current.model_fields)


def _types(annotation: Any) -> list[Any]:
    """The types inside an annotation, at any depth: a list of text or a mapping holds the mapping's model."""
    found = [annotation]
    for inner in getattr(annotation, "__args__", ()):
        found += _types(inner)
    return found


def location(loc: tuple[Any, ...], data: Any) -> str:
    """A path into data as people read it: `connections[0] "shop" → limits → rows` — a list item with its name."""
    parts: list[str] = []
    node = data
    for step in loc:
        if isinstance(step, int):
            item = node[step] if isinstance(node, list) and step < len(node) else None
            name = item.get("name") if isinstance(item, dict) else None
            label = f"[{step}]" + (f' "{name}"' if isinstance(name, str) else "")
            if parts:
                parts[-1] += label
            else:
                parts.append(label)
            node = item
        else:
            parts.append(str(step))
            node = node.get(step) if isinstance(node, dict) else None
    return " → ".join(parts) if parts else "the file"
