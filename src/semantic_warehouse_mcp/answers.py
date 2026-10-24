"""Answers as the agent reads them: compact JSON, numbers exact, capped in size — and a cut answer says so."""

from __future__ import annotations

import base64
import datetime
import decimal
import json
import math
import uuid
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


def render(answer: dict[str, Any], key: str, items: list[Any], max_characters: int, hint: str) -> str:
    """The answer with as many of its `key` items as fit in `max_characters`; when some are left out, it says so."""
    full = dumps({**answer, key: items})
    if len(full) <= max_characters:
        return full
    low, high = 0, len(items)  # the largest count that fits lies in [low, high)
    while high - low > 1:
        middle = (low + high) // 2
        if len(dumps(_cut(answer, key, items, middle, max_characters, hint))) <= max_characters:
            low = middle
        else:
            high = middle
    return dumps(_cut(answer, key, items, low, max_characters, hint))


def _cut(answer: dict[str, Any], key: str, items: list[Any], keep: int, max_characters: int, hint: str) -> dict:
    note = f"Only the first {keep} of {len(items)} {key} fit in the answer ({max_characters} characters)."
    note = f"{note} {hint}" if hint else note
    cut = {**answer, key: items[:keep], "truncated": True}
    if "row_count" in answer:
        cut["row_count"] = keep
    cut["truncated_by"] = [*answer.get("truncated_by", []), "answer_characters"]
    cut["note"] = f"{answer['note']} {note}" if answer.get("note") else note
    return cut
