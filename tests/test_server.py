"""The server through the SDK's own client, in-process — what any MCP client sees."""

import decimal
import json

import duckdb
import pytest
from mcp import Client

from semantic_warehouse_mcp import answers
from semantic_warehouse_mcp.server import build_server

pytestmark = pytest.mark.anyio


async def call(config, tool: str, **arguments):
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    return (text, True) if result.is_error else (json.loads(text), False)


async def test_every_tool_says_it_is_read_only(shop_config):
    async with Client(build_server(shop_config())) as client:
        tools = (await client.list_tools()).tools
    assert {tool.name for tool in tools} >= {"about", "list_connections", "list_tables", "describe_table", "run_query"}
    for tool in tools:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name


async def test_about_answers():
    async with Client(build_server()) as client:
        result = await client.call_tool("about", {})
    assert not result.is_error
    answer = json.loads(result.content[0].text)
    assert answer["name"] == "semantic-warehouse-mcp"
    assert answer["read_only"] is True


async def test_list_connections(shop_config):
    answer, _ = await call(shop_config(), "list_connections")
    assert answer["connections"] == [
        {
            "name": "shop",
            "engine": "duckdb",
            "description": "A made-up shop.",
            "readable": ["marts.*", "raw.events"],
            "large_tables": [{"table": "raw.events", "bound_by": "event_date"}],
            "limits": {"rows": 5, "seconds": 10},
        }
    ]


async def test_list_tables_shows_only_the_readable(shop_config):
    answer, _ = await call(shop_config(), "list_tables")
    assert answer == {
        "connection": "shop",
        "tables": [
            {"table": "marts.customers", "kind": "table"},
            {"table": "marts.daily", "kind": "view"},
            {"table": "marts.orders", "kind": "table", "comment": "One row per paid order."},
            {"table": "raw.events", "kind": "table", "bound_by": "event_date"},
        ],
    }


async def test_describe_table(shop_config):
    answer, _ = await call(shop_config(), "describe_table", table="marts.orders")
    assert answer["table"] == "marts.orders"
    assert answer["comment"] == "One row per paid order."
    assert answer["columns"][4] == {
        "name": "amount",
        "type": "DECIMAL(12,2)",
        "nullable": True,
        "comment": "Paid, before refunds.",
    }
    events, _ = await call(shop_config(), "describe_table", table="raw.events", connection="shop")
    assert events["bound_by"] == "event_date"


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("secret.salaries", "Refused (table_not_readable): secret.salaries is not readable here"),
        ("raw.logs", "Refused (table_not_readable)"),
        ("marts.nothing", "No table marts.nothing on shop"),
    ],
)
async def test_describe_table_refuses(shop_config, table, expected):
    text, is_error = await call(shop_config(), "describe_table", table=table)
    assert is_error
    assert expected in text


async def test_run_query_answers_with_its_provenance(shop_config, shop_path):
    sql = "SELECT currency, sum(amount - refunded_amount) AS revenue FROM marts.orders GROUP BY currency ORDER BY 1"
    answer, is_error = await call(shop_config(), "run_query", sql=sql + ";")
    assert not is_error
    assert answer["connection"] == "shop"
    assert answer["sql"] == sql
    assert answer["started_at"].endswith("+00:00")
    assert answer["seconds"] >= 0
    assert answer["columns"] == [{"name": "currency", "type": "VARCHAR"}, {"name": "revenue", "type": "DECIMAL(38,2)"}]
    assert answer["rows"] == [["EUR", 25.55], ["USD", 30.3]]
    assert answer["row_count"] == 2
    assert answer["truncated"] is False
    # The same number by a direct query, past the server.
    with duckdb.connect(str(shop_path), read_only=True) as direct:
        assert direct.execute(sql).fetchall() == [("EUR", decimal.Decimal("25.55")), ("USD", decimal.Decimal("30.30"))]


async def test_numbers_are_exact(shop_config):
    answer, _ = await call(
        shop_config(), "run_query", sql="SELECT sum(amount) FROM marts.orders WHERE order_id IN ('o4', 'o5')"
    )
    assert answer["rows"] == [[0.3]]  # DECIMAL: 0.10 + 0.20 is 0.30, not 0.30000000000000004
    answer, _ = await call(shop_config(), "run_query", sql="SELECT 12345678901234567.25::DECIMAL(38, 2) AS big")
    assert answer["rows"] == [["12345678901234567.25"]]  # more digits than a JSON number holds: a string


async def test_rows_past_the_limit_are_cut_and_it_says_so(shop_config):
    answer, _ = await call(shop_config(rows=5), "run_query", sql="SELECT * FROM range(100)")
    assert answer["row_count"] == 5
    assert answer["truncated"] is True
    assert answer["truncated_by"] == ["rows"]
    assert "more than 5 rows" in answer["note"]


async def test_a_long_answer_is_cut_to_fit_and_says_so(shop_config):
    config = shop_config(rows=1000, answer_characters=2000)
    async with Client(build_server(config)) as client:
        result = await client.call_tool(
            "run_query", {"sql": "SELECT range AS n, repeat('x', 50) AS pad FROM range(100)"}
        )
    text = result.content[0].text
    assert len(text) <= 2000
    answer = json.loads(text)
    assert 0 < answer["row_count"] < 100
    assert answer["row_count"] == len(answer["rows"])
    assert answer["truncated_by"] == ["answer_characters"]
    assert f"Only the first {answer['row_count']} of 100 rows fit" in answer["note"]


def test_long_values_are_cut_to_fit_and_a_cut_that_cannot_fit_ends():
    cut = json.loads(answers.render({"values": ["z" * 300] * 10}, "items", [], 1000, ""))
    assert len(answers.dumps(cut)) <= 1000 and cut["truncated_by"] == ["long_values"]
    assert all(value == "z" * 300 or value.endswith("…") for value in cut["values"]) and "z" * 300 in cut["values"]
    # values already as short as a cut leaves them cannot be cut shorter: the answer says so, and the cutting ends
    too_many = {"values": ["x" * 40 + "…"] * 100, "long": "y" * 500}
    assert json.loads(answers.render(too_many, "items", [], 1000, "")) == {
        "truncated": True,
        "truncated_by": ["answer_characters"],
        "note": "The answer does not fit in 1000 characters, even cut. Ask for less.",
    }


async def test_a_large_table_must_be_bounded(shop_config):
    text, is_error = await call(shop_config(), "run_query", sql="SELECT count(*) FROM raw.events")
    assert is_error
    assert "Refused by the guard (large_table_unbounded)" in text
    answer, is_error = await call(
        shop_config(),
        "run_query",
        sql="SELECT count(*) FROM raw.events WHERE event_date >= DATE '2026-03-01' AND event_name = 'cart_abandoned'",
    )
    assert not is_error
    assert answer["rows"] == [[2]]


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DROP TABLE marts.orders", "not_a_select"),
        ("SELECT * FROM secret.salaries", "table_not_readable"),
        ("SELECT * FROM read_csv('/etc/hostname')", "table_function"),
        ("SELECT 1; SELECT 2", "not_one_statement"),
    ],
)
async def test_refusals_reach_the_agent(shop_config, sql, code):
    text, is_error = await call(shop_config(), "run_query", sql=sql)
    assert is_error
    assert f"Refused by the guard ({code})" in text


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1; SELECT 2", "Refused by the guard (engine_parse): DuckDB reads 2 statements"),
        ("SELECT * FROM (secret.salaries) x", "(table_not_readable): the database reads secret.salaries"),
        ("SELECT * FROM marts.orders, raw.events", "(large_table_unbounded): the database reads raw.events"),
    ],
)
async def test_the_engines_own_parser_has_the_last_word(shop_config, monkeypatch, sql, expected):
    from semantic_warehouse_mcp import guard

    # Were sqlglot ever to pass what it should not, DuckDB's own reading of the query still refuses it.
    def lenient(sql, rules):
        return guard.Checked(sql=sql, tables=())

    monkeypatch.setattr(guard, "check", lenient)
    text, is_error = await call(shop_config(), "run_query", sql=sql)
    assert is_error
    assert expected in text


async def test_database_errors_and_timeouts_reach_the_agent(shop_config):
    text, is_error = await call(shop_config(), "run_query", sql="SELECT no_such_column FROM marts.orders")
    assert is_error
    assert "The database (shop) returned an error" in text
    assert "no_such_column" in text
    text, is_error = await call(
        shop_config(seconds=0.5),
        "run_query",
        sql="SELECT sum(hash(a * b)) FROM range(100000000) x(a), range(1000) y(b)",
    )
    assert is_error
    assert "Stopped after 0.5 s, the time limit of shop" in text


async def test_connections_by_name(shop_config):
    text, is_error = await call(shop_config(), "run_query", sql="SELECT 1", connection="nowhere")
    assert is_error
    assert "No connection named 'nowhere'. Connections: shop." in text
