"""What the server needs from a database engine. An engine's specifics live in its adapter, nowhere else.

The guard and the tools are engine-neutral: they ask an adapter for its SQL dialect, its default schema, the table
functions it allows and the functions it denies, its own reading of a query where the engine has one, and to run a
checked query within the connection's limits.
"""

from __future__ import annotations

import importlib
import pathlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

# The engines a configuration may name, and where each adapter lives — imported only when a connection uses it.
ENGINES: dict[str, str] = {
    "clickhouse": "semantic_warehouse_mcp.adapters.clickhouse:ClickHouseAdapter",
    "duckdb": "semantic_warehouse_mcp.adapters.duckdb:DuckDBAdapter",
    "postgres": "semantic_warehouse_mcp.adapters.postgres:PostgresAdapter",
}


class EngineSettings(BaseModel):
    """The keys of a connection that belong to its engine (a file path, a URL, the variables holding secrets)."""

    model_config = ConfigDict(extra="forbid", strict=True)


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool | None = None
    comment: str | None = None


@dataclass(frozen=True)
class Table:
    schema: str
    name: str
    kind: str  # "table" or "view"
    comment: str | None = None


@dataclass(frozen=True)
class EngineReading:
    """How the engine's own parser reads a query: why it is not one SELECT (`refusal`), else the tables it reads as
    (catalog, schema, table) — catalog None for the connection's own — or None when the engine cannot say."""

    refusal: str | None = None
    tables: tuple[tuple[str | None, str, str], ...] | None = None


@dataclass(frozen=True)
class Result:
    columns: list[Column]
    rows: list[tuple[Any, ...]]
    more_rows: bool  # the query returned more rows than were read


class AdapterError(Exception):
    """The engine refused or failed; the message is the engine's own, for the agent to read."""


class TimedOut(AdapterError):
    """The query ran past the connection's time limit and was stopped."""


def missing_driver(engine: str, package: str) -> AdapterError:
    """An engine's driver comes with an extra of its own: what to install when it is not there."""
    return AdapterError(
        f"the {engine} driver ({package}) is not installed: pip install 'semantic-warehouse-mcp[{engine}]' — or [all] "
        "for every engine's"
    )


class Adapter(ABC):
    engine: ClassVar[str]
    dialect: ClassVar[str]  # sqlglot's name for the engine's SQL dialect
    settings_model: ClassVar[type[EngineSettings]]
    # Table functions a query may read from (lower case); every other table function is refused.
    allowed_table_functions: ClassVar[frozenset[str]] = frozenset()
    # Functions a query may not call anywhere (lower case, globs for a family: dictget*): those that reach files,
    # the network, other servers, or read a table by a name the guard cannot check.
    denied_functions: ClassVar[frozenset[str]] = frozenset()
    # Functions that read a table named in one of their arguments (lower-case glob → the argument's position): the name
    # must be a string the guard checks against `readable`, like a table in FROM.
    table_reading_functions: ClassVar[tuple[tuple[str, int], ...]] = ()

    def __init__(self, settings: EngineSettings, base_dir: pathlib.Path) -> None:
        self.settings = settings
        self.base_dir = base_dir
        # What `check()` found worth the attention of whoever runs the server, without stopping it.
        self.notes: list[str] = []

    @property
    @abstractmethod
    def default_schema(self) -> str:
        """The schema an unqualified table name resolves to."""

    @property
    def catalog(self) -> str | None:
        """The connection's own catalog where the engine has catalogs; a query naming another one is refused."""
        return None

    @abstractmethod
    def check(self) -> None:
        """At start: reach the database and confirm it is read-only. Raises AdapterError naming what is wrong."""

    def granted_tables(self) -> list[tuple[str, str]] | None:
        """The tables and views the connection's user may read, as (schema, table) — where the engine can say. The
        configuration names those `readable` leaves out: a function defined in the database reads with the user's
        grants, where the guard cannot see."""
        return None

    def engine_reading(self, sql: str, seconds: float, named: tuple[str, ...] = ()) -> EngineReading:
        """The engine's own reading of a query the guard passed, where the engine has a parser to ask. `named`: the
        tables the guard found it reads (schema.table) — for an engine whose reading shows a view's own tables."""
        return EngineReading()

    @abstractmethod
    def run(self, sql: str, max_rows: int, seconds: float) -> Result:
        """Run a checked query: read at most `max_rows` rows, and stop it after `seconds` (raising TimedOut)."""

    @abstractmethod
    def tables(self) -> list[Table]:
        """Every table and view of the connection's catalog — the caller keeps the readable ones."""

    @abstractmethod
    def columns(self, schema: str, table: str) -> list[Column]:
        """A table's columns in order; an empty list when there is no such table."""


def adapter_class(engine: str) -> type[Adapter] | None:
    """The adapter for an engine named in a configuration, or None when the engine is not supported."""
    target = ENGINES.get(engine)
    if target is None:
        return None
    module_name, class_name = target.split(":")
    return getattr(importlib.import_module(module_name), class_name)
