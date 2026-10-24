"""Answers as the agent reads them: compact JSON, numbers exact, capped in size — and a cut answer says so."""

from __future__ import annotations

import base64
import copy
import datetime
import decimal
import json
import math
import uuid
from collections.abc import Callable
from typing import Any


def value(item: Any) -> Any:
    """A database value as JSON. A number JSON cannot hold exactly comes as a string, never rounded."""
    if item is None or isinstance(item, (bool, int, str)):
        return item
    if isinstance(item, float):
        return item if math.isfinite(item) else str(item)
    if isinstance(item, decimal.Decimal):
        if not item.is_finite():
            return str(item)
        if item == item.to_integral_value():
            return int(item)
        as_float = float(item)
        return as_float if decimal.Decimal(repr(as_float)) == item else str(item)
    if isinstance(item, (datetime.datetime, datetime.date, datetime.time)):
        return item.isoformat()
    if isinstance(item, datetime.timedelta):
        return str(item)
    if isinstance(item, uuid.UUID):
        return str(item)
    if isinstance(item, (bytes, bytearray, memoryview)):
        return "base64:" + base64.b64encode(bytes(item)).decode("ascii")
    if isinstance(item, dict):
        return {str(key): value(inner) for key, inner in item.items()}
    if isinstance(item, (list, tuple, set, frozenset)):
        return [value(inner) for inner in item]
    return str(item)


def dumps(answer: dict[str, Any]) -> str:
    return json.dumps(answer, ensure_ascii=False, separators=(",", ":"))


def render(
    answer: dict[str, Any],
    key: str,
    items: list[Any],
    max_characters: int,
    hint: str,
    beside: Callable[[list[Any]], dict[str, Any]] | None = None,
) -> str:
    """The answer with as many of its `key` items as fit in `max_characters`; when some are left out, it says so.
    `beside` gives what the answer says of the items it holds (what their values mean), for the items kept only."""

    def around(kept: list[Any]) -> dict[str, Any]:
        return {**answer, **beside(kept)} if beside else answer

    full = dumps({**around(items), key: items})
    if len(full) <= max_characters:
        return full
    if not items:
        return _fit({**around(items), key: items}, max_characters)
    low, high = 0, len(items)  # the largest count that fits lies in [low, high)
    while high - low > 1:
        middle = (low + high) // 2
        if len(dumps(_cut(around(items[:middle]), key, items, middle, max_characters, hint))) <= max_characters:
            low = middle
        else:
            high = middle
    return _fit(_cut(around(items[:low]), key, items, low, max_characters, hint), max_characters)


# Kept whole when long values are cut: what says the answer was cut.
_SAYS_IT_WAS_CUT = ("note", "truncated", "truncated_by")


def _fit(answer: dict[str, Any], max_characters: int) -> str:
    """The last resort, when the answer is too long with none of its items: its longest values cut, each ending in `…`,
    until it fits — and it says so. An answer of many short values that still does not fit says only that."""
    rendered = dumps(answer)
    if len(rendered) <= max_characters:
        return rendered
    answer = copy.deepcopy(answer)
    answer["truncated"] = True
    if "long_values" not in answer.get("truncated_by", []):
        answer["truncated_by"] = [*answer.get("truncated_by", []), "long_values"]
    said = "Long values were cut to fit the answer; each cut one ends in …."
    answer["note"] = f"{answer['note']} {said}" if answer.get("note") else said
    rendered = dumps(answer)
    strings = _strings(answer)
    could_save = sum(_saved(holder[place], _KEPT) for holder, place in strings)
    while len(rendered) > max_characters:
        longest = max(strings, key=lambda found: len(found[0][found[1]]), default=None)
        if len(rendered) - could_save > max_characters or longest is None:
            said = f"The answer does not fit in {max_characters} characters, even cut. Ask for less."
            return dumps({"truncated": True, "truncated_by": ["answer_characters"], "note": said})
        holder, place = longest
        text = holder[place]
        keep = max(_KEPT, len(text) - (len(rendered) - max_characters) - 1)
        could_save -= _saved(text, _KEPT) - _saved(text[:keep] + "…", _KEPT)
        holder[place] = text[:keep] + "…"
        rendered = dumps(answer)
    return rendered


# A cut value keeps this many characters before its `…`.
_KEPT = 40


def _saved(text: str, keep: int) -> int:
    """How many characters of the answer cutting `text` to `keep` characters and `…` saves: none when it is short."""
    if len(text) <= keep + 1:
        return 0
    return len(json.dumps(text, ensure_ascii=False)) - len(json.dumps(text[:keep] + "…", ensure_ascii=False))


def _strings(node: Any, top: bool = True) -> list[tuple[Any, Any]]:
    """Every string value in an answer as (its holder, its place in it), but those that say the answer was cut."""
    found: list[tuple[Any, Any]] = []
    places = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else []
    for place, value in places:
        if top and place in _SAYS_IT_WAS_CUT:
            continue
        if isinstance(value, str):
            found.append((node, place))
        else:
            found.extend(_strings(value, top=False))
    return found


def render_fitting(
    answer: dict[str, Any],
    key: str,
    items: list[Any],
    max_characters: int,
    hint: str,
    smaller: list[tuple[str, Any, str]],
) -> str:
    """`render`, but before any of the `key` items is left out, parts of the answer are made smaller in turn: each
    `(part, its smaller form — None to leave it out, what the note says)`. The note says what was made smaller."""
    current = dict(answer)
    notes: list[str] = []
    for part, form, note in [("", None, ""), *smaller]:
        if part:
            if part not in current:
                continue
            if form is None:
                current.pop(part)
            else:
                current[part] = form
            notes.append(note)
        if notes:
            said = f"To fit the answer ({max_characters} characters): {'; '.join(notes)}."
            current["note"] = f"{answer['note']} {said}" if answer.get("note") else said
        full = dumps({**current, key: items})
        if len(full) <= max_characters:
            return full
    return render(current, key, items, max_characters, hint)


def in_parts(answer: dict[str, Any], text: str, offset: int, max_characters: int) -> str:
    """A text from `offset` on, in the answer beside `characters` (the whole text's) and `offset` — as much of it as
    fits; when the rest does not, `next_offset` says where the next part starts."""
    answer = {**answer, "characters": len(text), "offset": offset}
    size = len(text) - offset
    while True:
        part = {**answer, "text": text[offset : offset + size]}
        if offset + size < len(text):
            part["next_offset"] = offset + size
        rendered = dumps(part)
        if len(rendered) <= max_characters or size <= 1:
            return rendered
        size = size * 9 // 10


def _cut(answer: dict[str, Any], key: str, items: list[Any], keep: int, max_characters: int, hint: str) -> dict:
    note = f"Only the first {keep} of {len(items)} {key} fit in the answer ({max_characters} characters)."
    note = f"{note} {hint}" if hint else note
    cut = {**answer, key: items[:keep], "truncated": True}
    if "row_count" in answer:
        cut["row_count"] = keep
    cut["truncated_by"] = [*answer.get("truncated_by", []), "answer_characters"]
    cut["note"] = f"{answer['note']} {note}" if answer.get("note") else note
    return cut
