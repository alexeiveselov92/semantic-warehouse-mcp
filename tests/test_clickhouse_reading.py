"""ClickHouse's own reading of a query, from the trees servers answer — no server needed. Before 24.3, a tree comes
unresolved: it names no table, so it must never pass for a reading that found none."""

import pathlib
import re

import pytest

from semantic_warehouse_mcp.adapters import AdapterError
from semantic_warehouse_mcp.adapters.clickhouse import (
    FIXED_PARAMS,
    PROFILE_SETTINGS,
    UNREADABLE_TREE,
    UNRESOLVED,
    ClickHouseAdapter,
    ClickHouseSettings,
    _not_a_row,
    _parsed_reading,
    _resolved_reading,
    _typed,
)

# As ClickHouse 26.3 answers EXPLAIN QUERY TREE (the analyzer on): names resolved, the tables named.
RESOLVED_SELECT_ONE = """QUERY id: 0
  PROJECTION COLUMNS
    1 UInt8
  PROJECTION
    LIST id: 1, nodes: 1
      CONSTANT id: 2, constant_value: UInt64_1, constant_value_type: UInt8
  JOIN TREE
    TABLE id: 3, alias: __table1, table_name: system.one
"""
# As ClickHouse 22.11 answers it: the tree as parsed, no name resolved — `s.secret` read by `IN` is no table here.
UNRESOLVED_SELECT_ONE = """QUERY id: 0
  PROJECTION
    LIST id: 1, nodes: 1
      CONSTANT id: 2, constant_value: UInt64_1, constant_value_type: UInt8
  JOIN TREE
    IDENTIFIER id: 3, identifier: system.one
"""
UNRESOLVED_IN_TABLE = """QUERY id: 0
  PROJECTION
    LIST id: 1, nodes: 1
      IDENTIFIER id: 2, identifier: id
  JOIN TREE
    IDENTIFIER id: 3, identifier: m.t
  WHERE
    FUNCTION id: 4, function_name: in, function_type: ordinary
      ARGUMENTS
        LIST id: 5, nodes: 2
          IDENTIFIER id: 6, identifier: id
          IDENTIFIER id: 7, identifier: s.secret
"""


@pytest.fixture
def answering():
    """An adapter whose server answers each query with the tree given, its user's settings those given."""

    def make(tree, own=None, may_stop=True):
        settings = ClickHouseSettings(url="http://clickhouse.example.com:8123", database="m")
        adapter = ClickHouseAdapter(settings, pathlib.Path())
        adapter._own_settings = lambda: {"readonly": "0", **(own or {})}
        adapter._may_stop = lambda: may_stop
        adapter._get = lambda query, seconds, settings=None: "1\n" if query == "SELECT 1" else tree
        return adapter

    return make


def test_a_server_that_resolves_its_trees_is_read(answering):
    answering(RESOLVED_SELECT_ONE).check()


def test_a_server_that_explains_without_resolving_is_refused_at_start(answering):
    with pytest.raises(AdapterError) as refused:
        answering(UNRESOLVED_SELECT_ONE).check()
    assert str(refused.value) == UNRESOLVED


def test_a_setting_the_server_does_not_know_is_not_sent(answering):
    """An older server lacks some limits (22.11: `timeout_overflow_mode_leaf`): it is sent the ones it has."""
    adapter = answering(RESOLVED_SELECT_ONE, own={name: "0" for name in PROFILE_SETTINGS if name != "readonly"})
    adapter.check()
    adapter.known.discard("timeout_overflow_mode_leaf")
    params = adapter._params("SELECT 1", seconds=5, settings=None)
    assert "timeout_overflow_mode_leaf" not in params and params["read_overflow_mode"] == "throw"


def test_a_readonly_one_user_is_sent_no_limit(answering):
    adapter = answering(RESOLVED_SELECT_ONE, own={"readonly": "1", "max_execution_time": "0"})
    adapter.check()
    assert adapter.fixed_limits is True
    params = adapter._params("SELECT 1", seconds=5, settings={"default_format": "TSV", "max_result_rows": 4})
    assert set(params) <= set(FIXED_PARAMS) and params["default_format"] == "TSV"


@pytest.mark.parametrize(
    ("own", "may_stop", "refused"),
    [
        ({"max_rows_to_read": "2", "read_overflow_mode": "break"}, True,
         "max_rows_to_read = 2 with read_overflow_mode = 'break'"),
        ({"max_rows_to_group_by": "100", "group_by_overflow_mode": "any"}, True,
         "max_rows_to_group_by = 100 with group_by_overflow_mode = 'any'"),
        ({"max_execution_time": "0"}, False, "a query past its time would run on"),
        ({"max_execution_time": "30"}, False, None),  # its profile's own time limit bounds it
        ({"max_rows_to_read": "0", "read_overflow_mode": "break"}, True, None),  # no limit: nothing to cut
    ],
)  # fmt: skip
def test_a_readonly_one_profile_is_checked_before_it_is_served(answering, own, may_stop, refused):
    adapter = answering(RESOLVED_SELECT_ONE, own={"readonly": "1", **own}, may_stop=may_stop)
    if refused is None:
        adapter.check()
        return
    with pytest.raises(AdapterError, match=re.escape(refused)):
        adapter.check()


NAMES, TYPES = '["number", "flag"]', '["UInt64", "UInt8"]'


@pytest.mark.parametrize(
    ("line", "error"),
    [
        ("[1, 0]", False),
        ('["Code: 1. not an error", 0]', False),
        ("Code: 395. DB::Exception: boom: while executing … (version 22.11.6.44 (official build))", True),  # 22.x
        ('["Code: 395. DB::Exception: boom: … (version 25.8.33.6 (official build))"]', True),  # 25.x
        ("__exception__", True),  # 26.x
        ("[1, 0, 2]", True),
        ("[1", True),
    ],
)
def test_an_error_met_after_rows_is_told_from_a_row(line, error):
    assert _not_a_row(line, [NAMES, TYPES]) is error
    single = ['["message"]', '["String"]']
    assert _not_a_row('["Code: 395. DB::Exception: x (version 25.8.1 (official build))"]', single) is True
    assert _not_a_row('["Code: 395 — a message about codes"]', single) is False


def test_a_64_bit_integer_quoted_as_text_is_read_as_the_integer():
    assert _typed("18446744073709551615", "UInt64") == 18446744073709551615
    assert _typed(["1", "2"], "Array(Nullable(Int64))") == [1, 2]
    assert _typed("12", "LowCardinality(String)") == "12"


def test_the_resolved_tree_is_asked_for(answering):
    """Servers before 24.3 resolve the tree only when asked (`run_passes = 1`); newer ones do it either way."""
    adapter = answering(RESOLVED_SELECT_ONE, own={"allow_experimental_analyzer": "1"})
    asked = []
    adapter._get = lambda query, seconds, settings=None: asked.append(query) or RESOLVED_SELECT_ONE
    adapter.check()
    adapter.engine_reading("SELECT 1", seconds=5)
    explained = [query for query in asked if query.startswith("EXPLAIN")]
    assert explained and all(query.startswith("EXPLAIN QUERY TREE run_passes = 1 ") for query in explained)


def test_an_unresolved_tree_is_never_a_reading_that_found_no_table(answering):
    reading = answering(UNRESOLVED_IN_TABLE).engine_reading("SELECT id FROM m.t WHERE id IN s.secret", seconds=5)
    assert reading.refusal == UNRESOLVED and not reading.tables


# --- before 24.3: the tree as parsed, read by the rules of the interpreter that runs the query ------------------------

# As ClickHouse 22.11 answers EXPLAIN QUERY TREE run_passes = 0 — the tree as parsed — for
# WITH a AS (SELECT * FROM b), b AS (SELECT 1 AS x) SELECT * FROM a WHERE x IN c
PARSED_LATER_CTE = """QUERY id: 0
  WITH
    LIST id: 1, nodes: 2
      QUERY id: 2, is_subquery: 1, is_cte: 1, cte_name: a
        PROJECTION
          LIST id: 3, nodes: 1
            MATCHER id: 4, matcher_type: ASTERISK
        JOIN TREE
          IDENTIFIER id: 5, identifier: b
      QUERY id: 6, is_subquery: 1, is_cte: 1, cte_name: b
        PROJECTION
          LIST id: 7, nodes: 1
            CONSTANT id: 8, alias: x, constant_value: UInt64_1, constant_value_type: UInt8
        JOIN TREE
          IDENTIFIER id: 9, identifier: system.one
  PROJECTION
    LIST id: 10, nodes: 1
      MATCHER id: 11, matcher_type: ASTERISK
  JOIN TREE
    IDENTIFIER id: 12, identifier: a
  WHERE
    FUNCTION id: 13, function_name: in, function_type: ordinary
      ARGUMENTS
        LIST id: 14, nodes: 2
          IDENTIFIER id: 15, identifier: x
          IDENTIFIER id: 16, identifier: c
"""
# WITH b AS (SELECT 1 AS x), a AS (SELECT * FROM b) SELECT * FROM a
PARSED_EARLIER_CTE = """QUERY id: 0
  WITH
    LIST id: 1, nodes: 2
      QUERY id: 2, is_subquery: 1, is_cte: 1, cte_name: b
        PROJECTION
          LIST id: 3, nodes: 1
            CONSTANT id: 4, alias: x, constant_value: UInt64_1, constant_value_type: UInt8
        JOIN TREE
          IDENTIFIER id: 5, identifier: system.one
      QUERY id: 6, is_subquery: 1, is_cte: 1, cte_name: a
        PROJECTION
          LIST id: 7, nodes: 1
            MATCHER id: 8, matcher_type: ASTERISK
        JOIN TREE
          IDENTIFIER id: 9, identifier: b
  PROJECTION
    LIST id: 10, nodes: 1
      MATCHER id: 11, matcher_type: ASTERISK
  JOIN TREE
    IDENTIFIER id: 12, identifier: a
"""
# SELECT * FROM (WITH b AS (SELECT 1 AS x) SELECT * FROM b) AS q, b — the second b is no CTE: it is out of its query
PARSED_CTE_OUT_OF_ITS_QUERY = """QUERY id: 0
  PROJECTION
    LIST id: 1, nodes: 1
      MATCHER id: 2, matcher_type: ASTERISK
  JOIN TREE
    JOIN id: 3, kind: COMMA
      LEFT TABLE EXPRESSION
        QUERY id: 4, alias: q, is_subquery: 1
          WITH
            LIST id: 5, nodes: 1
              QUERY id: 6, is_subquery: 1, is_cte: 1, cte_name: b
                PROJECTION
                  LIST id: 7, nodes: 1
                    CONSTANT id: 8, alias: x, constant_value: UInt64_1, constant_value_type: UInt8
                JOIN TREE
                  IDENTIFIER id: 9, identifier: system.one
          PROJECTION
            LIST id: 10, nodes: 1
              MATCHER id: 11, matcher_type: ASTERISK
          JOIN TREE
            IDENTIFIER id: 12, identifier: b
      RIGHT TABLE EXPRESSION
        IDENTIFIER id: 13, identifier: b
"""
# WITH u AS (SELECT 1 AS x UNION ALL SELECT 2) SELECT * FROM u JOIN v USING x
PARSED_UNION_CTE = """QUERY id: 0
  WITH
    LIST id: 1, nodes: 1
      UNION id: 2, is_subquery: 1, is_cte: 1, cte_name: u, union_mode: UNION_ALL
        QUERIES
          LIST id: 3, nodes: 2
            QUERY id: 4
              PROJECTION
                LIST id: 5, nodes: 1
                  CONSTANT id: 6, alias: x, constant_value: UInt64_1, constant_value_type: UInt8
              JOIN TREE
                IDENTIFIER id: 7, identifier: system.one
            QUERY id: 8
              PROJECTION
                LIST id: 9, nodes: 1
                  CONSTANT id: 10, constant_value: UInt64_2, constant_value_type: UInt8
              JOIN TREE
                IDENTIFIER id: 11, identifier: system.one
  PROJECTION
    LIST id: 12, nodes: 1
      MATCHER id: 13, matcher_type: ASTERISK
  JOIN TREE
    JOIN id: 14, strictness: ALL, kind: INNER
      LEFT TABLE EXPRESSION
        IDENTIFIER id: 15, identifier: u
      RIGHT TABLE EXPRESSION
        IDENTIFIER id: 16, identifier: v
      JOIN EXPRESSION
        LIST id: 17, nodes: 1
          IDENTIFIER id: 18, identifier: x
"""
# WITH [8] AS lst SELECT 1 FROM t WHERE 8 IN lst — right of IN, the alias, not a table
PARSED_IN_ALIAS = """QUERY id: 0
  WITH
    LIST id: 1, nodes: 1
      CONSTANT id: 2, alias: lst, constant_value: Array_[UInt64_8], constant_value_type: Array(UInt8)
  PROJECTION
    LIST id: 3, nodes: 1
      CONSTANT id: 4, constant_value: UInt64_1, constant_value_type: UInt8
  JOIN TREE
    IDENTIFIER id: 5, identifier: t
  WHERE
    FUNCTION id: 6, function_name: in, function_type: ordinary
      ARGUMENTS
        LIST id: 7, nodes: 2
          CONSTANT id: 8, constant_value: UInt64_8, constant_value_type: UInt8
          IDENTIFIER id: 9, identifier: lst
"""
# SELECT * FROM remote('h', db.t)
PARSED_REMOTE = """QUERY id: 0
  PROJECTION
    LIST id: 1, nodes: 1
      MATCHER id: 2, matcher_type: ASTERISK
  JOIN TREE
    TABLE_FUNCTION id: 3, table_function_name: remote
      ARGUMENTS
        LIST id: 4, nodes: 2
          CONSTANT id: 5, constant_value: \\'h\\', constant_value_type: String
          IDENTIFIER id: 6, identifier: db.t
"""
# SELECT 1 AS `a\nLIST id: 3, nodes: 0` FROM t — a line break in a quoted alias makes a line of the tree
PARSED_FORGED = """QUERY id: 0
  PROJECTION
    LIST id: 1, nodes: 1
      CONSTANT id: 2, alias: a
LIST id: 3, nodes: 0, constant_value: UInt64_1, constant_value_type: UInt8
  JOIN TREE
    IDENTIFIER id: 3, identifier: t
"""
ALLOWED = frozenset({"numbers"})


def parsed(tree):
    reading, _, _ = _parsed_reading(tree, "m", ALLOWED)
    return reading.refusal or sorted(reading.tables)


@pytest.mark.parametrize(
    ("tree", "tables"),
    [
        # a CTE named before it is defined is a table to the interpreter (measured on 22.11); right of IN, a table
        (PARSED_LATER_CTE, [(None, "m", "b"), (None, "m", "c"), (None, "system", "one")]),
        (PARSED_EARLIER_CTE, [(None, "system", "one")]),
        (PARSED_CTE_OUT_OF_ITS_QUERY, [(None, "m", "b"), (None, "system", "one")]),
        (PARSED_UNION_CTE, [(None, "m", "v"), (None, "system", "one"), (None, "system", "one")]),
    ],
)
def test_the_parsed_tree_is_read_by_the_rules_of_the_interpreter_that_runs_it(tree, tables):
    assert parsed(tree) == tables


def test_right_of_in_an_alias_comes_first_where_its_line_says_it_with_certainty():
    """A quoted alias may hold `, key: ` — the parsed tree prints it as written — so a line holding two is no alias."""
    assert parsed(PARSED_IN_ALIAS) == [(None, "m", "t")]
    forged = PARSED_IN_ALIAS.replace("alias: lst, ", "alias: lst, constant_value: 1, alias: x, ")  # `lst, …` quoted
    assert parsed(forged) == [(None, "m", "lst"), (None, "m", "t")]
    twice = PARSED_IN_ALIAS.replace("alias: lst, ", "alias: lst, constant_value: 1, ")
    assert parsed(twice) == [(None, "m", "lst"), (None, "m", "t")]
    in_from = PARSED_IN_ALIAS.replace("identifier: t\n", "identifier: lst\n")  # FROM lst: a table, alias or not
    assert parsed(in_from) == [(None, "m", "lst")]


def test_a_table_function_not_allowed_is_refused_in_either_tree():
    assert "table function remote" in parsed(PARSED_REMOTE)
    assert parsed(PARSED_REMOTE.replace("remote", "numbers")) == []
    resolved = RESOLVED_SELECT_ONE.replace(
        "TABLE id: 3, alias: __table1, table_name: system.one",
        "TABLE_FUNCTION id: 3, alias: __table1, table_function_name: remote",
    )
    assert "table function remote" in _resolved_reading(resolved, ALLOWED).refusal
    forged = resolved.replace("alias: __table1", "alias: x, table_function_name: numbers")  # an alias names another
    assert "table function remote" in _resolved_reading(forged, ALLOWED).refusal


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda t: t, "a line a quoted name broke"),
        (lambda t: t.replace("LIST id: 3, nodes: 0", "LIST id: 3"), "an unnumbered line"),
    ],
)
def test_a_tree_with_a_line_no_query_prints_is_refused(change, why):
    assert parsed(change(PARSED_FORGED)) == UNREADABLE_TREE, why


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("IDENTIFIER id: 16, identifier: c", "IDENTIFIER id: 17, identifier: c"),  # a node out of order
        ("LIST id: 14, nodes: 2", "LIST id: 14, nodes: 3"),  # a list holding fewer than it says
        ("        LIST id: 14", "          LIST id: 14"),  # two levels deeper than its parent
        ("  WHERE\n", "  WHERE\n    HAVING\n"),  # a section right under a section
        ("      ARGUMENTS\n", "      ARGUMENTS\n        WHERE\n"),  # a section under a section
        ("        LIST id: 14, nodes: 2\n", "        LIST id: 14, nodes: 2\n          WHERE\n"),  # under a list
        ("  WHERE\n", "  WITH\n"),  # a WITH not first: the tree prints it first
        ("  WHERE\n", "  PROJECTION\n"),  # a section twice
        ("QUERY id: 6, is_subquery: 1", "QUERY id: 6, alias: q, is_subquery: 1"),  # a CTE not as the tree prints one
        ("cte_name: b\n", "cte_name: b, is_subquery: 1\n"),  # a CTE's name an alias wrote
    ],
)
def test_a_tree_not_as_clickhouse_prints_one_is_refused(old, new):
    assert old in PARSED_LATER_CTE
    assert parsed(PARSED_LATER_CTE.replace(old, new)) == UNREADABLE_TREE


def test_a_with_is_read_only_where_the_tree_prints_it_first():
    moved = PARSED_LATER_CTE.replace("  WITH\n", "  QUALIFY\n").replace("  WHERE\n", "  WITH\n")
    assert parsed(moved) == UNREADABLE_TREE


def test_a_cte_of_a_union_is_read_only_as_the_tree_prints_one():
    forged = PARSED_UNION_CTE.replace("union_mode: UNION_ALL\n", "union_mode: X, union_mode: UNION_ALL\n", 1)
    assert parsed(forged) == UNREADABLE_TREE


def test_a_name_the_server_cannot_read_with_certainty_is_refused():
    assert "more than one dot" in parsed(PARSED_LATER_CTE.replace("identifier: c", "identifier: a.b.c"))
    assert "cannot read" in parsed(PARSED_LATER_CTE.replace("identifier: c", "identifier: my table"))


def test_a_resolved_tree_names_every_table_however_its_line_reads():
    """22.11 writes an alias as given, a comma in it too; FINAL and SAMPLE follow the name."""
    tree = RESOLVED_SELECT_ONE.replace(
        "TABLE id: 3, alias: __table1, table_name: system.one",
        "TABLE id: 3, alias: a, table_name: x, table_name: s.t, final: 1, sample_size: 1 / 2",
    )
    assert _resolved_reading(tree, ALLOWED).tables == ((None, "s", "t"),)
    unreadable = RESOLVED_SELECT_ONE.replace("table_name: system.one", "table_name: m.a.b")
    assert "cannot read" in _resolved_reading(unreadable, ALLOWED).refusal


def reading_adapter(own, answers, found=()):
    """An adapter on a server answering each EXPLAIN by its kind — an AdapterError raised as the server's refusal."""
    settings = ClickHouseSettings(url="http://clickhouse.example.com:8123", database="m")
    adapter = ClickHouseAdapter(settings, pathlib.Path())
    adapter._own_settings = lambda: {"readonly": "0", **own}
    asked = []

    def get(query, seconds, settings=None):
        asked.append(query)
        if query == "SELECT 1" or query.startswith("EXPLAIN QUERY TREE run_passes = 1 SELECT 1"):
            return "1\n" if query == "SELECT 1" else RESOLVED_SELECT_ONE
        answer = answers["parsed" if "run_passes = 0" in query else "resolved"]
        if isinstance(answer, Exception):
            raise answer
        return answer

    adapter._get = get
    adapter._rows = lambda query, **params: [[name] for name in found]
    adapter.check()
    return adapter, asked


RESOLVED_T = RESOLVED_SELECT_ONE.replace("table_name: system.one", "table_name: m.t")


@pytest.mark.parametrize(
    ("own", "runs"),
    [
        ({"allow_experimental_analyzer": "0"}, False),  # 22.11
        ({"allow_experimental_analyzer": "1", "enable_analyzer": "1"}, True),  # 24.3 on
        ({"allow_experimental_analyzer": "1", "enable_analyzer": "0"}, False),  # the newer name decides
        ({}, False),
    ],
)
def test_whether_the_analyzer_runs_queries_is_learnt_at_start(own, runs):
    adapter, _ = reading_adapter(own, {})
    assert adapter.analyzer_runs is runs


def test_where_the_analyzer_does_not_run_queries_both_trees_are_read():
    adapter, asked = reading_adapter(
        {"allow_experimental_analyzer": "0"}, {"resolved": RESOLVED_T, "parsed": PARSED_LATER_CTE}
    )
    reading = adapter.engine_reading("SELECT …", seconds=5)
    assert sorted(reading.tables) == [(None, "m", "b"), (None, "m", "c"), (None, "m", "t")]
    assert [q.split(" SELECT")[0] for q in asked[-2:]] == [
        "EXPLAIN QUERY TREE run_passes = 1",
        "EXPLAIN QUERY TREE run_passes = 0",
    ]


def test_where_the_analyzer_cannot_resolve_a_query_it_does_not_run_the_parsed_tree_is_read_alone():
    refused = AdapterError(
        "Code: 215. DB::Exception: Column payload is not under aggregate function (NOT_AN_AGGREGATE)"
    )
    adapter, _ = reading_adapter(
        {"allow_experimental_analyzer": "0"}, {"resolved": refused, "parsed": PARSED_EARLIER_CTE}
    )
    assert adapter.engine_reading("SELECT …", seconds=5).tables == ()  # system.one is read by itself
    runs, _ = reading_adapter({"enable_analyzer": "1"}, {"resolved": refused, "parsed": PARSED_EARLIER_CTE})
    assert "NOT_AN_AGGREGATE" in runs.engine_reading("SELECT …", seconds=5).refusal  # it would fail as it runs


def test_a_name_with_a_dot_is_asked_of_the_database_where_no_resolved_tree_tells():
    refused = AdapterError("Code: 215. NOT_AN_AGGREGATE")
    answers = {"resolved": refused, "parsed": PARSED_EARLIER_CTE.replace("identifier: a\n", "identifier: db.t\n")}
    held, _ = reading_adapter({"allow_experimental_analyzer": "0"}, answers, found=["db.t"])
    assert "a table named 'db.t'" in held.engine_reading("SELECT …", seconds=5).refusal
    free, _ = reading_adapter({"allow_experimental_analyzer": "0"}, answers, found=[])
    assert free.engine_reading("SELECT …", seconds=5).tables == ((None, "db", "t"),)


def test_a_table_the_analyzer_took_a_cte_for_is_read_as_the_parsed_tree_reads_it():
    """22.11's analyzer takes a CTE inside `IN (SELECT …)` for the table of its name (found by the second reader)."""
    resolved_b = RESOLVED_SELECT_ONE.replace("table_name: system.one", "table_name: m.b")
    took, _ = reading_adapter(
        {"allow_experimental_analyzer": "0"}, {"resolved": resolved_b, "parsed": PARSED_EARLIER_CTE}
    )
    assert took.engine_reading("SELECT …", seconds=5).tables == ()  # b is a CTE wherever the query names it
    read, _ = reading_adapter(
        {"allow_experimental_analyzer": "0"}, {"resolved": resolved_b, "parsed": PARSED_LATER_CTE}
    )
    assert (None, "m", "b") in read.engine_reading("SELECT …", seconds=5).tables  # read before its CTE: the table
    elsewhere = RESOLVED_SELECT_ONE.replace("table_name: system.one", "table_name: other.b")
    held, _ = reading_adapter(
        {"allow_experimental_analyzer": "0"}, {"resolved": elsewhere, "parsed": PARSED_EARLIER_CTE}
    )
    assert held.engine_reading("SELECT …", seconds=5).tables == ((None, "other", "b"),)  # not the connection's database
