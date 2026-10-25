"""ClickHouse before 24.3 (22.11, tests/clickhouse_server.py): its analyzer answers EXPLAIN QUERY TREE but does not run
queries — so the server reads the tree as ClickHouse parsed it too, by the rules of the interpreter that runs them."""

import json

import pytest
from clickhouse_server import admin, old_clickhouse_url  # noqa: F401 — the fixture
from conftest import write_config
from mcp import Client

from semantic_warehouse_mcp.adapters import AdapterError
from semantic_warehouse_mcp.adapters.clickhouse import EXPLAIN, UNREADABLE_TREE, ClickHouseAdapter, ClickHouseSettings
from semantic_warehouse_mcp.config import load
from semantic_warehouse_mcp.server import build_server

CONFIG = """
connections:
  - name: warehouse
    engine: clickhouse
    url: {url}
    database: swm_raw
    user_env: SWM_TEST_USER
    password_env: SWM_TEST_PASSWORD
    readable: ["swm_raw.log", "swm_marts.*"]
    large_tables:
      - {{ table: swm_raw.log, bound_by: event_date, narrowed_by: [event] }}
    limits: {{ rows: 10, seconds: 10 }}
"""
# `device` is an ALIAS column: 22.11's analyzer refuses a GROUP BY over it (Code 215), the interpreter runs it.
GROUPED = (
    "SELECT device, count() AS events FROM swm_raw.log WHERE event_date >= '2026-03-01' AND event = 'opened' "
    "GROUP BY device ORDER BY device"
)
# A CTE named before it is defined is a table to the interpreter: this reads swm_secret.salaries.
LATER_CTE = "WITH a AS (SELECT * FROM salaries), salaries AS (SELECT 'cte' AS name) SELECT name FROM a"


@pytest.fixture
def login(monkeypatch):
    monkeypatch.setenv("SWM_TEST_USER", "swm_writer")  # a user who may write: the server must not, all the same
    monkeypatch.setenv("SWM_TEST_PASSWORD", "swm_writer")


def adapter_on(url, database, tmp_path):
    settings = ClickHouseSettings(
        url=url, database=database, user_env="SWM_TEST_USER", password_env="SWM_TEST_PASSWORD"
    )
    adapter = ClickHouseAdapter(settings, tmp_path)
    adapter.check()
    return adapter


def test_the_analyzer_answers_trees_it_does_not_run(old_clickhouse_url, login, tmp_path):  # noqa: F811
    adapter = adapter_on(old_clickhouse_url, "swm_raw", tmp_path)
    assert adapter.analyzer_runs is False
    with pytest.raises(AdapterError, match="NOT_AN_AGGREGATE"):
        adapter._get(f"{EXPLAIN} {GROUPED}", seconds=10)
    assert adapter.run(GROUPED, max_rows=10, seconds=10).rows == [("phone", 2), ("tablet", 1)]


def test_a_group_by_over_an_alias_column_is_read_where_the_analyzer_cannot(old_clickhouse_url, login, tmp_path):  # noqa: F811
    reading = adapter_on(old_clickhouse_url, "swm_raw", tmp_path).engine_reading(GROUPED, seconds=10)
    assert reading.refusal is None
    assert reading.tables == ((None, "swm_raw", "log"),)


def test_a_cte_named_before_it_is_defined_is_read_as_the_table_the_server_reads(old_clickhouse_url, login, tmp_path):  # noqa: F811
    adapter = adapter_on(old_clickhouse_url, "swm_secret", tmp_path)
    assert adapter.run(LATER_CTE, max_rows=10, seconds=10).rows == [("someone",)]  # the table, not the CTE
    assert (None, "swm_secret", "salaries") in adapter.engine_reading(LATER_CTE, seconds=10).tables


@pytest.mark.parametrize(
    "sql",
    [
        # right of IN, an alias of the query, a quoted name beside it (found by the second reader)
        "WITH ['c1', 'c2'] AS wanted SELECT count() AS \"Orders\" FROM swm_marts.orders WHERE customer_id IN (wanted)",
        "WITH 'c1' AS wanted SELECT count() AS `n` FROM swm_marts.orders WHERE customer_id IN (wanted)",
        # a CTE named as a table of the connection's database, read inside IN (SELECT …): 22.11's analyzer takes it
        # for the table, the interpreter reads the CTE
        "WITH events AS (SELECT customer_id FROM swm_marts.orders) SELECT customer_id FROM events "
        "WHERE customer_id IN (SELECT customer_id FROM events)",
    ],
)
def test_what_the_interpreter_reads_is_what_is_named(old_clickhouse_url, login, tmp_path, sql):  # noqa: F811
    adapter = adapter_on(old_clickhouse_url, "swm_raw", tmp_path)
    assert adapter.run(sql, max_rows=10, seconds=10).rows  # it runs, reading the orders
    reading = adapter.engine_reading(sql, seconds=10)
    assert reading.refusal is None and reading.tables == ((None, "swm_marts", "orders"),)


@pytest.mark.parametrize(
    ("sql", "refusal"),
    [
        (f"SELECT 1 AS `a\nLIST id: 3, nodes: 0` FROM ({GROUPED})", UNREADABLE_TREE),
        (f"SELECT * FROM ({GROUPED}) AS g, remote('127.0.0.1', swm_secret.salaries)", "table function remote"),
    ],
)
def test_what_the_parsed_tree_cannot_show_is_refused(old_clickhouse_url, login, tmp_path, sql, refusal):  # noqa: F811
    reading = adapter_on(old_clickhouse_url, "swm_raw", tmp_path).engine_reading(sql, seconds=10)
    assert refusal in reading.refusal


async def call(config, tool: str, **arguments):
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    return (text, True) if result.is_error else (json.loads(text), False)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_the_group_by_answers_through_the_server_and_the_number_holds(old_clickhouse_url, login, tmp_path):  # noqa: F811
    config = load(write_config(tmp_path, CONFIG.format(url=old_clickhouse_url)))
    answer, is_error = await call(config, "run_query", sql=GROUPED)
    assert not is_error, answer
    assert answer["rows"] == [["phone", 2], ["tablet", 1]]
    direct = admin(
        old_clickhouse_url,
        GROUPED.replace("SELECT device", "SELECT JSONExtractString(payload, 'device') AS d").replace(
            "device ORDER BY device", "d ORDER BY d"
        )
        + " FORMAT TSV",
    )
    assert direct == "phone\t2\ntablet\t1\n"  # the payload read directly: the same numbers


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sql", "code"),
    [
        # the guard refuses first; the engine's readings would too (above)
        (f"SELECT * FROM ({GROUPED}) AS g WHERE device IN swm_secret.salaries", "unsupported"),
        ("SELECT device, count() FROM swm_raw.log WHERE event_date >= '2026-03-01' GROUP BY device", "large_table"),
    ],
)
async def test_a_query_the_rules_refuse_is_refused_here_too(old_clickhouse_url, login, tmp_path, sql, code):  # noqa: F811
    config = load(write_config(tmp_path, CONFIG.format(url=old_clickhouse_url)))
    answer, is_error = await call(config, "run_query", sql=sql)
    assert is_error and f"({code}" in answer
