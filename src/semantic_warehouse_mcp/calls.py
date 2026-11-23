"""A log of the tools' calls, for the people who run the server (docs/reference/running.md, "A log of calls").

One JSON line per call, written to standard error when the server is started with `--log-calls`: which tool, with
what arguments, for whom (where a gateway names the caller), how long it took and how it ended. The answer itself is
never written — only its size and, where it says them, how many rows it holds and whether it was cut."""

from __future__ import annotations

import datetime
import functools
import json
import sys
import time
from collections.abc import Callable
from typing import Any

from .adapters import CALLER

Writer = Callable[[dict[str, Any]], None]

ARGUMENT_CHARACTERS = 20_000  # a query's text is kept whole up to here; a longer value is cut, and says so
ERROR_CHARACTERS = 1_000
SAID_BY_THE_ANSWER = ("connection", "row_count", "truncated")  # copied from an answer that states them


def to_standard_error(record: dict[str, Any]) -> None:
    print(json.dumps(record, ensure_ascii=False, default=str), file=sys.stderr, flush=True)


def _argument(value: Any) -> Any:
    if isinstance(value, str) and len(value) > ARGUMENT_CHARACTERS:
        return value[:ARGUMENT_CHARACTERS] + f"… ({len(value)} characters, cut)"
    return value


def _of_the_answer(answer: Any) -> dict[str, Any]:
    """What a record keeps of an answer: its size, and the few facts it states about itself."""
    if isinstance(answer, str):
        said: dict[str, Any] = {"answer_characters": len(answer)}
        if answer.startswith("{"):
            try:
                stated = json.loads(answer)
            except ValueError:
                return said
            said.update({key: stated[key] for key in SAID_BY_THE_ANSWER if key in stated})
        return said
    return {"answer_characters": len(json.dumps(answer, ensure_ascii=False, default=str))}


def logged(function: Callable[..., Any], write: Writer) -> Callable[..., Any]:
    """`function`, as it is for the SDK (its name, description and parameters), with a record written for each call
    — after the call, whether it answered or raised. A writer that fails never fails the call."""

    @functools.wraps(function)
    def call(*args: Any, **kwargs: Any) -> Any:
        record: dict[str, Any] = {
            "event": "tool_call",
            "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds"),
            "caller": CALLER.get(),
            "tool": function.__name__,
            "arguments": {name: _argument(value) for name, value in kwargs.items()},
        }
        start = time.monotonic()
        try:
            answer = function(*args, **kwargs)
        except Exception as error:
            record.update(outcome="error", error=f"{type(error).__name__}: {error}"[:ERROR_CHARACTERS])
            raise
        else:
            record.update(outcome="answered", **_of_the_answer(answer))
            return answer
        finally:
            record["seconds"] = round(time.monotonic() - start, 3)
            try:
                write(record)
            except Exception:  # noqa: BLE001 - a full disk or a closed stream must not turn an answer into an error
                pass

    return call
