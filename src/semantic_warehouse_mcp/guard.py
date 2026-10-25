"""The guard: reads a query before it runs and refuses what is not one read-only SELECT over readable tables.

The database's read-only user is the line that holds; the guard makes refusals clear and cheap, and bounds the
expensive tables (docs/reference/guard.md, dev/DESIGN.md "The guard"). It is engine-neutral: the dialect, the default
schema and the engine's allowed and denied functions come from the connection's adapter.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

# sqlglot warns when it falls back to an unparsed command; the guard refuses those with its own words.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

# Every refusal, by code. docs/reference/guard.md has an entry for each — a test holds it.
REFUSALS: dict[str, str] = {
    "empty": "the query is empty",
    "unparsable": "the query cannot be read in the connection's SQL dialect",
    "not_one_statement": "the query holds more than one statement",
    "not_a_select": "the statement is not a SELECT",
    "not_read_only": "the query writes, locks, or runs a command",
    "query_settings": "the query sets engine settings or an output format of its own",
    "function_denied": "the query calls a function that reaches outside the database",
    "table_function": "the query reads from a table function that is not allowed",
    "table_not_readable": "the query reads a table that is not readable on this connection",
    "large_table_unbounded": "the query reads a large table without bounding its column",
    "large_table_unnarrowed": "the query reads a large table without naming the values of a column it is narrowed by",
    "large_table_joined": "the query reads a large table beside another table in one SELECT",
    "unsupported": "the guard cannot follow the query's structure",
    "engine_parse": "the database's own parser reads the query differently",
}

# The statements a query may be: a SELECT, a set operation of SELECTs (UNION, …), one in parentheses.
ALLOWED_ROOTS = (exp.Select, exp.SetOperation, exp.Subquery)

# Nodes that write, lock, change the session or run a command — refused wherever they appear (a CTE included).
NOT_READ_ONLY = tuple(
    getattr(exp, name)
    for name in (
        "DML",  # INSERT, UPDATE, DELETE, MERGE
        "DDL",  # CREATE, DROP, ALTER, …
        "Command",  # what sqlglot does not parse: EXPLAIN, SYSTEM, …
        "Into",  # SELECT … INTO a table
        "Lock",  # FOR UPDATE / FOR SHARE
        "Set",
        "Pragma",
        "Copy",
        "Attach",
        "Detach",
        "Use",
        "Transaction",
        "Commit",
        "Rollback",
        "Kill",
        "Merge",
        "Grant",
        "Revoke",
        "LoadData",
        "Export",
        "Describe",
        "Summarize",
        "Show",
        "Analyze",
        "Execute",
        "Install",
    )
    if hasattr(exp, name)
)

# Clauses of a query that carry engine settings or an output format (ClickHouse SETTINGS / FORMAT, T-SQL OPTION).
SETTINGS_ARGS = ("settings", "format", "options")


class Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        assert code in REFUSALS, code
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class LargeTable:
    table: str  # schema.table, globs allowed
    bound_by: str
    narrowed_by: tuple[str, ...] = ()  # columns a query must hold to values it names: `= …`, `IN (…)`

    def needs(self) -> str:
        """What a query over the table must do, in the words of a refusal."""
        narrowed = " and ".join(self.narrowed_by)
        return f"bound {self.bound_by}" + (f" and name the values of {narrowed}" if narrowed else "")


@dataclass(frozen=True)
class Rules:
    """What the guard enforces for one connection."""

    dialect: str
    default_schema: str
    readable: tuple[str, ...]
    large_tables: tuple[LargeTable, ...] = ()
    catalog: str | None = None
    allowed_table_functions: frozenset[str] = frozenset()  # exact names, lower case
    denied_functions: frozenset[str] = frozenset()  # lower case, globs for a family: dictget*
    # Functions that read a table named in an argument: (lower-case glob, the argument's position).
    table_reading_functions: tuple[tuple[str, int], ...] = ()
    # Whether an alias the query gives hides a column of the same name in its WHERE (ClickHouse): then a bound or a
    # narrowing written with that name is the alias's, not the column's.
    aliases_shadow_columns: bool = False


@dataclass(frozen=True)
class Checked:
    sql: str  # the query as it will run: the text as written, without trailing semicolons
    tables: tuple[str, ...]  # schema.table of every table it reads


def check(sql: str, rules: Rules) -> Checked:
    """Pass a query that may run on the connection, or raise Refused saying why and how to fix it."""
    dialect = Dialect.get_or_raise(rules.dialect)
    text = re.sub(r"[\s;]+\Z", "", sql).strip()
    try:
        statements = [s for s in sqlglot.parse(text, read=dialect) if s is not None]
    except ParseError as error:
        raise Refused("unparsable", f"{_parse_error(error)} — the guard reads {rules.dialect} SQL") from error
    except (SqlglotError, RecursionError) as error:
        raise Refused("unparsable", f"{error} — the guard reads {rules.dialect} SQL") from error
    if not statements:
        raise Refused("empty", "send one SELECT statement")
    if len(statements) > 1:
        raise Refused("not_one_statement", f"it holds {len(statements)} statements; send one SELECT at a time")
    statement = statements[0]

    if not isinstance(statement, ALLOWED_ROOTS):
        raise Refused("not_a_select", f"it is {_kind(statement)}; only SELECT (with WITH, UNION, …) runs here")
    for node in statement.walk():
        if isinstance(node, NOT_READ_ONLY):
            raise Refused("not_read_only", f"it holds {_kind(node)}; only reading runs here")
        for arg in SETTINGS_ARGS:
            if isinstance(node, exp.Query) and node.args.get(arg) is not None:
                raise Refused(
                    "query_settings",
                    f"it carries {arg.upper()}; the connection's limits and the answer's format are the server's",
                )
        if isinstance(node, exp.In) and node.args.get("field") is not None:
            # ClickHouse's `x IN db.table` reads a table where sqlglot sees a column.
            raise Refused(
                "unsupported",
                f"{node.sql(dialect)} reads a table the guard cannot check; "
                "write it as x IN (SELECT <column> FROM <table>) — or, for a list a WITH names, x IN (name)",
            )
        if isinstance(node, exp.In) and (named := _table_in_list(node, statement, rules, dialect)):
            # ClickHouse reads `x IN (db.table)` as the table, where sqlglot sees a column.
            raise Refused(
                "unsupported",
                f"{node.sql(dialect)} names {named}, which no table of the query goes by; if it is a table, write "
                f"x IN (SELECT <column> FROM {named})",
            )
        if isinstance(node, exp.Func) and _denied(node, rules):
            raise Refused("function_denied", f"{_function_name(node)}() reaches outside the database; it is not run")

    tables = _check_tables(statement, rules, dialect) | _tables_named_in_functions(statement, rules, dialect)
    return Checked(sql=text, tables=tuple(sorted(tables)))


def check_engine_tables(tables: tuple[tuple[str | None, str, str], ...], checked: Checked, rules: Rules) -> None:
    """The tables the engine's own parser says a passed query reads — (catalog, schema, table), catalog None for the
    connection's own — held to the same rules. A large table the guard did not see has a bound it could not check."""
    dialect = Dialect.get_or_raise(rules.dialect)
    seen = set(checked.tables)
    for catalog, schema, table in tables:
        if catalog is not None:
            raise Refused("table_not_readable", f"the database reads {catalog}.{schema}.{table}, in another database")
        schema, table = _actual(schema, dialect), _actual(table, dialect)
        if not _matches(schema, table, rules.readable, dialect):
            readable = ", ".join(rules.readable)
            raise Refused(
                "table_not_readable",
                f"the database reads {schema}.{table}, which is not readable here (readable: {readable})",
            )
        large = _large(schema, table, rules, dialect)
        if large is not None and f"{schema}.{table}" not in seen:
            raise Refused(
                "large_table_unbounded",
                f"the database reads {schema}.{table} where the guard cannot see its bound; read it alone in a FROM "
                f"of its own and {large.needs()} there",
            )


def is_readable(schema: str, table: str, rules: Rules) -> bool:
    """Whether a table, named as the database stores it, is readable on the connection."""
    dialect = Dialect.get_or_raise(rules.dialect)
    return _matches(_actual(schema, dialect), _actual(table, dialect), rules.readable, dialect)


def large_table(schema: str, table: str, rules: Rules) -> LargeTable | None:
    """What a query over this table must bound and narrow, or None when it is not a large table."""
    dialect = Dialect.get_or_raise(rules.dialect)
    return _large(_actual(schema, dialect), _actual(table, dialect), rules, dialect)


def large_table_bound(schema: str, table: str, rules: Rules) -> str | None:
    """The column a query over this table must bound, or None when it is not a large table."""
    large = large_table(schema, table, rules)
    return large.bound_by if large else None


def stored_name(name: str, rules: Rules) -> str:
    """A name as the database stores it, normalized the way the guard compares names."""
    return _actual(name, Dialect.get_or_raise(rules.dialect))


def resolve_table(name: str, rules: Rules) -> tuple[str, str]:
    """A table name as an agent writes it (`orders`, `marts.orders`) → (schema, table), refused unless readable."""
    dialect = Dialect.get_or_raise(rules.dialect)
    try:
        node = exp.to_table(name, dialect=dialect)
    except SqlglotError as error:
        raise Refused("unparsable", f"{name!r} is not a table name") from error
    if not isinstance(node.this, exp.Identifier):
        raise Refused("unparsable", f"{name!r} is not a table name")
    return _physical_table(node, rules, dialect)


# --- tables and scopes --------------------------------------------------------------------------------------------


def _check_tables(statement: exp.Expression, rules: Rules, dialect: Dialect) -> set[str]:
    try:
        return _read_scopes(statement, rules, dialect)
    except (SqlglotError, RecursionError) as error:
        # sqlglot reads a query's structure lazily: a query it can parse may still fail here, and is refused.
        raise Refused("unsupported", f"{error}; write the query more plainly (CTEs, joins, subqueries)") from error


def _read_scopes(statement: exp.Expression, rules: Rules, dialect: Dialect) -> set[str]:
    # Where each table is selected (its scope, its alias, how many sources share that scope), and which names are
    # CTEs'. A name counts as a CTE's only when sqlglot resolves it to one and it is unqualified, as SQL resolves it.
    selected: dict[int, tuple[Scope, str, int]] = {}
    cte_names: set[int] = set()
    for scope in traverse_scope(statement):
        sources = {
            name: (node, source)
            for name, (node, source) in scope.selected_sources.items()
            if isinstance(source, (exp.Table, Scope))
        }
        for alias, (node, source) in sources.items():
            if not isinstance(node, exp.Table):
                continue
            if isinstance(source, Scope) and source.is_cte and not node.args.get("db") and not node.args.get("catalog"):
                cte_names.add(id(node))
            else:
                selected.setdefault(id(node), (scope, alias, len(sources)))

    # Every other table node is a table read — wherever sqlglot placed it.
    tables: set[str] = set()
    shadowed = _shadowing(statement, dialect) if rules.aliases_shadow_columns else {}
    for node in statement.find_all(exp.Table):
        if id(node) in cte_names:
            continue
        if not isinstance(node.this, exp.Identifier):
            _check_table_function(node, rules)
            continue
        schema, table = _physical_table(node, rules, dialect)
        tables.add(f"{schema}.{table}")
        large = _large(schema, table, rules, dialect)
        if large is None:
            continue
        if id(node) not in selected:
            raise Refused(
                "large_table_unbounded",
                f"{schema}.{table} is a large table read where the guard cannot see its bound; "
                f"read it alone in a FROM of its own and {large.needs()} there",
            )
        scope, alias, source_count = selected[id(node)]
        if source_count > 1:
            narrowed = "".join(f" AND {column} = <a value>" for column in large.narrowed_by)
            raise Refused(
                "large_table_joined",
                f"{schema}.{table} is a large table read beside another table in one SELECT: read it alone in a "
                f"subquery, {large.needs()} there, and join that — e.g. JOIN (SELECT … FROM {schema}.{table} WHERE "
                f"{large.bound_by} >= <a date>{narrowed}) AS {alias} ON … — a join's ON, or the WHERE, is not applied "
                "to the reading of a joined table by every database and version",
            )
        _check_large(scope, alias, large, f"{schema}.{table}", dialect, shadowed)
    return tables


def _tables_named_in_functions(statement: exp.Expression, rules: Rules, dialect: Dialect) -> set[str]:
    """Tables read by functions that name them in an argument (ClickHouse's dictGet('db.dictionary', …)): the name must
    be a string, and the table readable."""
    tables: set[str] = set()
    for node in statement.find_all(exp.Func):
        position = next(
            (
                index
                for pattern, index in rules.table_reading_functions
                for name in _names(node)
                if fnmatchcase(name, pattern)
            ),
            None,
        )
        if position is None:
            continue
        arguments = node.expressions if isinstance(node, exp.Anonymous) else []
        argument = arguments[position] if position < len(arguments) else None
        if not (isinstance(argument, exp.Literal) and argument.is_string):
            raise Refused(
                "unsupported",
                f"{_function_name(node)}() names the table it reads by an expression; write the name as a string",
            )
        try:
            named = exp.to_table(argument.this, dialect=dialect)
        except SqlglotError as error:
            raise Refused("unsupported", f"{_function_name(node)}() names {argument.this!r}, not a table") from error
        if not isinstance(named.this, exp.Identifier):
            raise Refused("unsupported", f"{_function_name(node)}() names {argument.this!r}, not a table")
        schema, table = _physical_table(named, rules, dialect)
        large = _large(schema, table, rules, dialect)
        if large is not None:
            raise Refused(
                "large_table_unbounded",
                f"{_function_name(node)}() reads {schema}.{table}, a large table, where a query cannot {large.needs()}",
            )
        tables.add(f"{schema}.{table}")
    return tables


def _table_in_list(node: exp.In, statement: exp.Expression, rules: Rules, dialect: Dialect) -> str | None:
    """A value of `x IN (…)` written `schema.table` for a readable table, its qualifier no table of the query goes by —
    a table to the engine that reads it so, a column to the guard."""
    names = {
        _norm(identifier, dialect)
        for table in statement.find_all(exp.Table, exp.Subquery)
        for identifier in (table.args.get("alias") and table.args["alias"].this, table.this)
        if isinstance(identifier, exp.Identifier)
    }
    for value in node.expressions:
        value = value.unnest()
        qualifier = value.args.get("table") if isinstance(value, exp.Column) else None
        if isinstance(qualifier, exp.Identifier) and _norm(qualifier, dialect) not in names:
            schema, table = _norm(qualifier, dialect), _norm(value.this, dialect)
            if _matches(schema, table, rules.readable, dialect):
                return f"{schema}.{table}"
    return None


def _physical_table(node: exp.Table, rules: Rules, dialect: Dialect) -> tuple[str, str]:
    catalog = node.args.get("catalog")
    db = node.args.get("db")
    table = _norm(node.this, dialect)
    schema = _norm(db, dialect) if isinstance(db, exp.Identifier) else _actual(rules.default_schema, dialect)
    if catalog is not None:
        own = _actual(rules.catalog, dialect) if rules.catalog else None
        if not isinstance(catalog, exp.Identifier) or _norm(catalog, dialect) != own:
            raise Refused(
                "table_not_readable",
                f"{node.sql(dialect)} is in another database; this connection reads only its own"
                + (f" ({rules.catalog})" if rules.catalog else ""),
            )
    if not _matches(schema, table, rules.readable, dialect):
        readable = ", ".join(rules.readable)
        raise Refused("table_not_readable", f"{schema}.{table} is not readable here (readable: {readable})")
    return schema, table


def _check_table_function(node: exp.Table, rules: Rules) -> None:
    function = node.this
    if isinstance(function, exp.Func) and _names(function) & rules.allowed_table_functions:
        return
    allowed = ", ".join(sorted(rules.allowed_table_functions)) or "none"
    shown = f"{_function_name(function)}()" if isinstance(function, exp.Func) else repr(function.sql())
    raise Refused("table_function", f"it reads from {shown}; table functions allowed here: {allowed}")


def _large(schema: str, table: str, rules: Rules, dialect: Dialect) -> LargeTable | None:
    for large in rules.large_tables:
        if _matches(schema, table, (large.table,), dialect):
            return large
    return None


def _check_large(
    scope: Scope, alias: str, large: LargeTable, name: str, dialect: Dialect, shadowed: dict[str, str]
) -> None:
    """A large table, read alone in its SELECT, must be bounded from below by its column, and held to named values of
    each column it is narrowed by, in a top-level AND of that SELECT's WHERE (or PREWHERE). A bound is `col >= …`,
    `col > …`, `col = …`, `col BETWEEN … AND …` or `col IN (…)`; a narrowing, `col = …` or `col IN (…)` — each against
    values that hold no column and no subquery. Where an alias hides a column (`shadowed`), the bare name is the
    alias's: only the column qualified by the table counts."""
    qualifier = _norm(exp.to_identifier(alias), dialect)  # the name its columns are qualified by, as selected

    def column_named(configured: str):
        column = _actual(configured, dialect)

        def is_column(expression: exp.Expression) -> bool:
            expression = expression.unnest()
            if not isinstance(expression, exp.Column) or _norm(expression.this, dialect) != column:
                return False
            table = expression.args.get("table")
            if table is None:
                return column not in shadowed
            return _norm(table, dialect) == qualifier

        return is_column

    conditions = [scope.expression.args.get(key) for key in ("where", "prewhere")]
    parts = [part for condition in conditions if condition is not None for part in _conjuncts(condition.this)]

    def hidden(configured: str) -> str:
        """Why a bound or a narrowing written with the column's bare name does not count, when an alias took it."""
        column = _actual(configured, dialect)
        if column not in shadowed:
            return ""
        return (
            f"; the query names {shadowed[column]} AS {configured}, and on this connection an alias hides the column "
            f"of its name — rename the alias, or qualify the column: {alias}.{configured}"
        )

    if not any(_bounds_from_below(part, column_named(large.bound_by)) for part in parts):
        narrowed = f"; name the values of {' and '.join(large.narrowed_by)} there too" if large.narrowed_by else ""
        raise Refused(
            "large_table_unbounded",
            f"{name} is a large table: bound {large.bound_by} from below in the WHERE of the query that reads it, "
            f"e.g. WHERE {large.bound_by} >= <a date> — the column itself, not a function of it"
            + hidden(large.bound_by)
            + narrowed,
        )
    for narrowed_by in large.narrowed_by:
        if not any(_names_values(part, column_named(narrowed_by)) for part in parts):
            raise Refused(
                "large_table_unnarrowed",
                f"{name} is a large table: name the values of {narrowed_by} in the WHERE of the query that reads it, "
                f"e.g. WHERE {narrowed_by} = <a value>, or {narrowed_by} IN (<values>) — the column itself, not a "
                "function of it" + hidden(narrowed_by),
            )


def _shadowing(statement: exp.Expression, dialect: Dialect) -> dict[str, str]:
    """The names a query's aliases give to something other than the column of that name — in a SELECT list, a scalar
    WITH, an ARRAY JOIN, inside an expression — each with what it names: where an alias hides a column, that name in a
    WHERE is the alias."""
    found: dict[str, str] = {}
    for node in statement.find_all(exp.Alias):
        name, inner = _norm(node.args["alias"], dialect), node.this.unnest()
        if not (isinstance(inner, exp.Column) and _norm(inner.this, dialect) == name):
            found.setdefault(name, inner.sql(dialect))
    for cte in statement.find_all(exp.CTE):
        if cte.args.get("scalar"):
            found.setdefault(_norm(cte.args["alias"].this, dialect), cte.this.sql(dialect))
    return found


def _conjuncts(condition: exp.Expression):
    condition = condition.unnest()
    if isinstance(condition, exp.And):
        yield from _conjuncts(condition.left)
        yield from _conjuncts(condition.right)
    else:
        yield condition


def _bounds_from_below(condition: exp.Expression, is_column) -> bool:
    if isinstance(condition, exp.Not):  # NOT (col < x) is col >= x
        inner = condition.this.unnest()
        if isinstance(inner, (exp.LT, exp.LTE)) and is_column(inner.left):
            return _is_value(inner.right)
        if isinstance(inner, (exp.GT, exp.GTE)) and is_column(inner.right):
            return _is_value(inner.left)
        return False
    if isinstance(condition, (exp.GTE, exp.GT, exp.EQ)) and is_column(condition.left):
        return _is_value(condition.right)
    if isinstance(condition, (exp.LTE, exp.LT, exp.EQ)) and is_column(condition.right):
        return _is_value(condition.left)
    if isinstance(condition, exp.Between) and is_column(condition.this):
        return _is_value(condition.args["low"])
    return _in_values(condition, is_column)


def _names_values(condition: exp.Expression, is_column) -> bool:
    """`col = <a value>` or `col IN (<values>)`: the column held to values the query names."""
    if isinstance(condition, exp.EQ):
        if is_column(condition.left):
            return _is_value(condition.right)
        return is_column(condition.right) and _is_value(condition.left)
    return _in_values(condition, is_column)


def _in_values(condition: exp.Expression, is_column) -> bool:
    if not isinstance(condition, exp.In) or not is_column(condition.this):
        return False
    values = condition.expressions
    return bool(values) and condition.args.get("query") is None and all(_is_value(v) for v in values)


def _is_value(expression: exp.Expression) -> bool:
    """A value fixed before the query reads a row: no column and no subquery in it."""
    return not any(isinstance(node, (exp.Column, exp.Query, exp.Subquery)) for node in expression.walk())


# --- names ---------------------------------------------------------------------------------------------------------


def _norm(identifier: exp.Identifier, dialect: Dialect) -> str:
    """An identifier as written in a query, normalized the way the dialect resolves it."""
    return dialect.normalize_identifier(identifier.copy()).name


def _actual(name: str, dialect: Dialect) -> str:
    """A name as the database stores it (a catalog's row, a configured pattern), normalized for comparison."""
    return dialect.normalize_identifier(exp.to_identifier(name, quoted=True)).name


def _matches(schema: str, table: str, patterns: tuple[str, ...], dialect: Dialect) -> bool:
    for pattern in patterns:
        pattern_schema, _, pattern_table = pattern.partition(".")
        if fnmatchcase(schema, _actual(pattern_schema, dialect)) and fnmatchcase(
            table, _actual(pattern_table, dialect)
        ):
            return True
    return False


def _names(function: exp.Func) -> set[str]:
    """Every name a function goes by (lower case): as written when sqlglot doesn't know it, else all of its names."""
    if isinstance(function, exp.Anonymous):
        return {function.name.lower()}
    return {name.lower() for name in type(function).sql_names()} | {type(function).key}


def _denied(function: exp.Func, rules: Rules) -> bool:
    return any(fnmatchcase(name, pattern) for name in _names(function) for pattern in rules.denied_functions)


def _function_name(function: exp.Func) -> str:
    return function.name if isinstance(function, exp.Anonymous) else function.sql_name().lower()


def _kind(node: exp.Expression) -> str:
    return str(node.this).upper() if isinstance(node, exp.Command) else type(node).key.upper()


def _parse_error(error: ParseError) -> str:
    if not error.errors:
        return str(error)
    first = error.errors[0]
    description = re.sub(r"<Token token_type: [^,]+, text: (.*?), line: .*?>", r"'\1'", first["description"])
    near = f" near {first['highlight']!r}" if first.get("highlight") else ""
    return f"{description} at line {first['line']}, column {first['col']}{near}"
