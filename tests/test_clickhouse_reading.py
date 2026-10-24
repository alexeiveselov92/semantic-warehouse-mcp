"""ClickHouse's own reading of a query, from the trees servers answer — no server needed. Before 24.3, a tree comes
unresolved: it names no table, so it must never pass for a reading that found none."""

import pathlib
import re

import pytest

from semantic_warehouse_mcp.adapters import AdapterError
from semantic_warehouse_mcp.adapters.clickhouse import (
    FIXED_PARAMS,
    PROFILE_SETTINGS,
    UNRESOLVED,
    ClickHouseAdapter,
    ClickHouseSettings,
    _not_a_row,
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
    adapter = answering(RESOLVED_SELECT_ONE)
    asked = []
    adapter._get = lambda query, seconds, settings=None: asked.append(query) or RESOLVED_SELECT_ONE
    adapter.check()
    adapter.engine_reading("SELECT 1", seconds=5)
    explained = [query for query in asked if query.startswith("EXPLAIN")]
    assert explained and all(query.startswith("EXPLAIN QUERY TREE run_passes = 1 ") for query in explained)


def test_an_unresolved_tree_is_never_a_reading_that_found_no_table(answering):
    reading = answering(UNRESOLVED_IN_TABLE).engine_reading("SELECT id FROM m.t WHERE id IN s.secret", seconds=5)
    assert reading.refusal == UNRESOLVED and not reading.tables
