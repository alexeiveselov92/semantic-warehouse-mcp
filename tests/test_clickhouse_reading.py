"""ClickHouse's own reading of a query, from the trees servers answer — no server needed. Before 24.3, a tree comes
unresolved: it names no table, so it must never pass for a reading that found none."""

import pathlib

import pytest

from semantic_warehouse_mcp.adapters import AdapterError
from semantic_warehouse_mcp.adapters.clickhouse import UNRESOLVED, ClickHouseAdapter, ClickHouseSettings

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
    """An adapter whose server answers each query with the tree given."""

    def make(tree):
        settings = ClickHouseSettings(url="http://clickhouse.example.com:8123", database="m")
        adapter = ClickHouseAdapter(settings, pathlib.Path())
        adapter._get = lambda query, seconds, settings=None: "1\n" if query == "SELECT 1" else tree
        return adapter

    return make


def test_a_server_that_resolves_its_trees_is_read(answering):
    answering(RESOLVED_SELECT_ONE).check()


def test_a_server_that_explains_without_resolving_is_refused_at_start(answering):
    with pytest.raises(AdapterError) as refused:
        answering(UNRESOLVED_SELECT_ONE).check()
    assert str(refused.value) == UNRESOLVED


def test_a_server_older_than_the_settings_sent_is_refused_saying_so(answering):
    adapter = answering(RESOLVED_SELECT_ONE)

    def older(query, seconds, settings=None):
        raise AdapterError("Code: 115. DB::Exception: Unknown setting timeout_overflow_mode_leaf. (UNKNOWN_SETTING)")

    adapter._get = older
    with pytest.raises(AdapterError, match=r"^ClickHouse does not know a setting the server sends with every query "
                                           r"\(24.3 or newer is needed\): Code: 115"):  # fmt: skip
        adapter.check()


def test_an_unresolved_tree_is_never_a_reading_that_found_no_table(answering):
    reading = answering(UNRESOLVED_IN_TABLE).engine_reading("SELECT id FROM m.t WHERE id IN s.secret", seconds=5)
    assert reading.refusal == UNRESOLVED and not reading.tables
