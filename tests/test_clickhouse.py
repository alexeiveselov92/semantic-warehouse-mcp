"""ClickHouse, against a real server (tests/clickhouse_server.py): the read-only line holds past the guard, every limit
refuses or says it cut, the engine names the tables it reads — and the tools answer over it."""

import decimal
import json
import time

import pytest
from clickhouse_server import admin, clickhouse_url  # noqa: F401 — the fixture
from conftest import write_config
from mcp import Client

from semantic_warehouse_mcp.adapters import AdapterError, TimedOut
from semantic_warehouse_mcp.adapters.clickhouse import READONLY_ONE, ClickHouseAdapter, ClickHouseSettings
from semantic_warehouse_mcp.config import ConfigError, load
from semantic_warehouse_mcp.server import build_server

CONFIG = """
connections:
  - name: warehouse
    engine: clickhouse
    url: {url}
    database: swm_marts
    user_env: SWM_TEST_USER
    password_env: SWM_TEST_PASSWORD
    readable: ["swm_marts.*", "swm_raw.events", "swm_dictionaries.*"]
    large_tables:
      - {{ table: swm_raw.events, bound_by: event_date }}
    limits: {{ rows: 3, seconds: 5 }}
"""


@pytest.fixture
def as_user(monkeypatch):
    def login(user):
        monkeypatch.setenv("SWM_TEST_USER", user)
        monkeypatch.setenv("SWM_TEST_PASSWORD", user)

    login("swm_writer")  # a user who may write: the server must not, all the same
    return login


@pytest.fixture
def adapter(clickhouse_url, as_user, tmp_path):  # noqa: F811
    settings = ClickHouseSettings(
        url=clickhouse_url, database="swm_marts", user_env="SWM_TEST_USER", password_env="SWM_TEST_PASSWORD"
    )
    adapter = ClickHouseAdapter(settings, tmp_path)
    adapter.check()
    return adapter


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO swm_marts.orders (order_id) VALUES ('x')",
        "ALTER TABLE swm_marts.orders DELETE WHERE 1",
        "TRUNCATE TABLE swm_marts.orders",
        "DROP TABLE swm_marts.orders",
        "CREATE TABLE swm_marts.copy (a UInt8) ENGINE = Memory",
        "CREATE DATABASE swm_elsewhere",
    ],
)
def test_a_user_who_may_write_cannot_through_the_server(adapter, clickhouse_url, sql):  # noqa: F811
    with pytest.raises(AdapterError, match="readonly"):
        adapter.run(sql, max_rows=10, seconds=10)
    assert admin(clickhouse_url, "SELECT count() FROM swm_marts.orders").strip() == "6"


def test_a_profile_that_cuts_silently_cannot_cut_through_the_server(adapter, as_user):
    # Its profile: read at most 2 rows, and on more, stop and answer with what was read — a sum over part of the table,
    # looking like the whole (seen on a larger table: a sum over 8,198 of 100,006 rows, no warning).
    as_user("swm_cutting")
    with pytest.raises(AdapterError, match="Limit for rows"):
        adapter.run("SELECT sum(amount), count() FROM swm_marts.orders WHERE order_id != ''", max_rows=10, seconds=10)


def test_a_user_with_readonly_one_is_refused_naming_the_fix(adapter, as_user):
    as_user("swm_readonly_one")
    with pytest.raises(AdapterError) as refused:
        adapter.check()
    assert str(refused.value) == READONLY_ONE
    assert "readonly = 2" in str(refused.value)


def test_a_user_with_readonly_two_takes_the_limits_and_writes_nothing(adapter, as_user, clickhouse_url):  # noqa: F811
    as_user("swm_readonly_two")
    adapter.check()
    start = time.monotonic()
    with pytest.raises(TimedOut):  # the server's own time limit reached its query
        adapter.run("SELECT count() FROM numbers(1000000000000)", max_rows=1, seconds=1)
    assert time.monotonic() - start < 5
    with pytest.raises(AdapterError, match="readonly"):
        adapter.run("INSERT INTO swm_marts.orders (order_id) VALUES ('x')", max_rows=3, seconds=10)
    assert admin(clickhouse_url, "SELECT count() FROM swm_marts.orders").strip() == "6"


def test_a_profile_capping_bytes_cannot_hide_a_cut(adapter, as_user):
    as_user("swm_small_answers")  # its profile: answers of 1 byte, in blocks of 2 rows
    result = adapter.run("SELECT number FROM numbers(10)", max_rows=3, seconds=10)
    assert result.rows == [(0,), (1,), (2,)]
    assert result.more_rows is True


def test_rows_past_the_limit_are_not_shown_and_it_says_so(adapter):
    cut = adapter.run("SELECT number FROM numbers(100000)", max_rows=3, seconds=10)
    assert cut.rows == [(0,), (1,), (2,)]
    assert cut.more_rows is True
    whole = adapter.run("SELECT number FROM numbers(3)", max_rows=3, seconds=10)
    assert len(whole.rows) == 3
    assert whole.more_rows is False


def test_a_query_past_its_time_is_stopped(adapter):
    start = time.monotonic()
    with pytest.raises(TimedOut):
        adapter.run("SELECT count() FROM numbers(1000000000000)", max_rows=1, seconds=1)
    assert time.monotonic() - start < 5  # stopped by ClickHouse itself, not only by the client giving up waiting


def test_values_come_exact(adapter):
    result = adapter.run(
        "SELECT order_id, order_date, amount, big, note FROM swm_marts.orders WHERE order_id = 'o1'", 10, 10
    )
    assert [c.type for c in result.columns] == ["String", "Date", "Decimal(12, 2)", "UInt64", "Nullable(String)"]
    assert result.rows == [("o1", "2026-03-01", decimal.Decimal("10.1"), 18446744073709551615, None)]


@pytest.mark.parametrize(
    ("sql", "tables"),
    [
        ("SELECT 1", []),
        ("SELECT * FROM swm_marts.orders", [(None, "swm_marts", "orders")]),
        ("SELECT * FROM orders", [(None, "swm_marts", "orders")]),  # the connection's database
        (
            "SELECT * FROM orders WHERE customer_id IN (swm_secret.salaries)",  # a table where sqlglot sees a column
            [(None, "swm_marts", "orders"), (None, "swm_secret", "salaries")],
        ),
        (
            "SELECT * FROM orders WHERE customer_id IN (SELECT name FROM swm_secret.salaries)",
            [(None, "swm_marts", "orders"), (None, "swm_secret", "salaries")],
        ),
        ("SELECT number FROM numbers(3)", []),
    ],
)
def test_the_engine_names_the_tables_it_reads(adapter, sql, tables):
    reading = adapter.engine_reading(sql, seconds=10)
    assert reading.refusal is None
    assert sorted(reading.tables) == sorted(tables)


def test_the_engine_refuses_what_is_not_a_select(adapter):
    assert "cannot read it" in adapter.engine_reading("INSERT INTO orders SELECT * FROM orders", seconds=10).refusal
    assert "cannot read it" in adapter.engine_reading("SELECT 1; SELECT 2", seconds=10).refusal


def test_tables_and_columns(adapter):
    tables = {(t.schema, t.name): t for t in adapter.tables()}
    assert tables["swm_marts", "orders"].comment == "One row per paid order."
    assert tables["swm_marts", "daily"].kind == "view"
    assert tables["swm_dictionaries", "countries"].kind == "dictionary"
    columns = adapter.columns("swm_marts", "orders")
    assert [c.name for c in columns][:3] == ["order_id", "order_date", "customer_id"]
    assert columns[4].comment == "Paid, before refunds."
    assert columns[7].nullable is True
    assert adapter.columns("swm_marts", "nothing") == []


# --- through the tools ------------------------------------------------------------------------------------------------


@pytest.fixture
def warehouse(clickhouse_url, as_user, tmp_path):  # noqa: F811
    return load(write_config(tmp_path, CONFIG.format(url=clickhouse_url)))


async def call(config, tool: str, **arguments):
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    return (text, True) if result.is_error else (json.loads(text), False)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_list_tables_and_describe_table(warehouse):
    answer, _ = await call(warehouse, "list_tables")
    names = [t["table"] for t in answer["tables"]]
    assert names == [
        "swm_dictionaries.countries",
        "swm_dictionaries.country_src",
        "swm_marts.daily",
        "swm_marts.orders",
        "swm_raw.events",
    ]
    described, _ = await call(warehouse, "describe_table", table="orders")
    assert described["table"] == "swm_marts.orders"
    assert described["comment"] == "One row per paid order."


@pytest.mark.anyio
async def test_run_query_answers_and_the_number_holds(warehouse, clickhouse_url):  # noqa: F811
    sql = "SELECT currency, sum(amount - refunded_amount) AS revenue FROM orders GROUP BY currency ORDER BY currency"
    answer, is_error = await call(warehouse, "run_query", sql=sql)
    assert not is_error, answer
    assert answer["sql"] == sql
    assert answer["rows"] == [["EUR", 25.55], ["USD", 30.3]]
    direct = admin(clickhouse_url, sql.replace("FROM orders", "FROM swm_marts.orders") + " FORMAT TSV")
    assert direct == "EUR\t25.55\nUSD\t30.3\n"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM swm_secret.salaries", "(table_not_readable)"),
        ("SELECT dictGet('swm_secret.lookup_dict', 'v', toUInt64(1))", "(table_not_readable)"),
        ("SELECT joinGetOrNull('swm_secret.lookup', 'v', 1)", "(table_not_readable)"),
        ("SELECT dictGet(concat('swm_secret.', 'lookup_dict'), 'v', toUInt64(1))", "(unsupported)"),
        ("SELECT count() FROM swm_raw.events", "(large_table_unbounded)"),
        ("SELECT * FROM url('http://example.com/x.csv', CSV)", "(table_function)"),
        ("SELECT * FROM orders SETTINGS max_result_rows = 0", "(query_settings)"),
    ],
)
async def test_refusals(warehouse, sql, expected):
    text, is_error = await call(warehouse, "run_query", sql=sql)
    assert is_error
    assert expected in text


@pytest.mark.anyio
async def test_a_readable_dictionary_is_read(warehouse):
    answer, is_error = await call(warehouse, "run_query", sql="SELECT dictGet('swm_dictionaries.countries', 'name', 1)")
    assert not is_error, answer
    assert answer["rows"] == [["Germany"]]


@pytest.mark.anyio
async def test_a_large_table_bounded_is_read(warehouse):
    sql = "SELECT count() FROM swm_raw.events PREWHERE event_date >= '2026-03-01' WHERE event_name = 'cart_abandoned'"
    answer, is_error = await call(warehouse, "run_query", sql=sql)
    assert not is_error, answer
    assert answer["rows"] == [[2]]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM orders WHERE customer_id IN (swm_secret.salaries)", "the database reads swm_secret.salaries"),
        ("SELECT * FROM orders WHERE customer_id IN (swm_raw.events)", "the database reads swm_raw.events"),
        ("INSERT INTO orders SELECT * FROM orders", "(engine_parse): ClickHouse cannot read it"),
    ],
)
async def test_clickhouses_own_reading_has_the_last_word(warehouse, monkeypatch, sql, expected):
    from semantic_warehouse_mcp import guard

    monkeypatch.setattr(guard, "check", lambda sql, rules: guard.Checked(sql=sql, tables=()))  # were sqlglot to miss it
    text, is_error = await call(warehouse, "run_query", sql=sql)
    assert is_error
    assert expected in text


@pytest.mark.anyio
async def test_rows_past_the_limit(warehouse):
    answer, _ = await call(warehouse, "run_query", sql="SELECT number FROM numbers(10)")
    assert answer["rows"] == [[0], [1], [2]]
    assert answer["truncated_by"] == ["rows"]


def test_a_missing_variable_is_named_never_a_value(clickhouse_url, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SWM_TEST_USER", "swm_writer")
    monkeypatch.delenv("SWM_TEST_PASSWORD", raising=False)
    with pytest.raises(ConfigError, match="the variable SWM_TEST_PASSWORD is not set"):
        load(write_config(tmp_path, CONFIG.format(url=clickhouse_url)))


def test_a_url_holding_a_password_is_refused_unrepeated(tmp_path):
    body = CONFIG.format(url="http://someone:hunter2@db.example.com:8123")
    with pytest.raises(ConfigError) as error:
        load(write_config(tmp_path, body))
    assert "url: string should match pattern" in str(error.value)
    assert "hunter2" not in str(error.value)


def test_the_example_configuration_holds():
    import pathlib

    import yaml

    from semantic_warehouse_mcp.config import parse

    path = pathlib.Path(__file__).resolve().parent.parent / "examples" / "config.clickhouse.example.yaml"
    config = parse(yaml.safe_load(path.read_text()), source=str(path), base_dir=path.parent, reach=False)
    assert config.connections["warehouse"].adapter.default_schema == "marts"
    assert [model.name for model in config.connections["warehouse"].models] == ["shop"]


MODEL = """
version: "0.2.0.dev0"
name: warehouse
datasets:
  - name: orders
    source: {source}
    fields:
      - name: order_id
        expression: {{dialects: [{{dialect: ANSI_SQL, expression: order_id}}]}}
      - name: Order_ID
        expression: {{dialects: [{{dialect: ANSI_SQL, expression: Order_ID}}]}}
"""


@pytest.mark.anyio
async def test_a_semantic_model_over_clickhouse(clickhouse_url, as_user, tmp_path):  # noqa: F811
    config = CONFIG.format(url=clickhouse_url) + "    semantic_models: [model.yaml]\n"
    (tmp_path / "model.yaml").write_text(MODEL.format(source="orders"))  # the connection's default database
    answer, _ = await call(load(write_config(tmp_path, config)), "describe_dataset", dataset="orders")
    assert answer["table"] == {"name": "swm_marts.orders", "kind": "table", "comment": "One row per paid order."}
    order_id, other = answer["fields"]
    assert order_id["column"] == {"type": "String", "nullable": False, "comment": "The order."}
    assert "column" not in other  # ClickHouse's names are case-sensitive: Order_ID is not order_id
    assert answer["other_columns"][0]["name"] == "order_date"

    (tmp_path / "model.yaml").write_text(MODEL.format(source="swm_marts.nothing"))
    with pytest.raises(ConfigError) as error:
        load(write_config(tmp_path, config))
    assert error.value.problems[0].endswith("source: there is no table or view swm_marts.nothing on the connection")
