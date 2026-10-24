"""The DuckDB adapter on its own, past the guard: the engine's own line holds, and the limits stop and cut honestly."""

import time

import pytest

from semantic_warehouse_mcp.adapters import AdapterError, TimedOut
from semantic_warehouse_mcp.adapters.duckdb import DuckDBAdapter, DuckDBSettings


@pytest.fixture
def adapter(shop_path):
    adapter = DuckDBAdapter(DuckDBSettings(path=shop_path.name), shop_path.parent)
    adapter.check()
    return adapter


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO marts.orders (order_id) VALUES ('x')",
        "DELETE FROM marts.orders",
        "DROP TABLE marts.orders",
        "CREATE TABLE marts.copy AS SELECT 1",
    ],
)
def test_the_file_is_opened_read_only(adapter, sql):
    with pytest.raises(AdapterError, match="read-only"):
        adapter.run(sql, max_rows=10, seconds=10)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('{folder}/elsewhere.csv')",
        "SELECT * FROM '{folder}/elsewhere.csv'",
        "COPY (SELECT 1) TO '{folder}/out.csv'",
        "ATTACH '{folder}/other.duckdb' AS other",
    ],
)
def test_files_and_the_network_are_out_of_reach(adapter, shop_path, sql):
    (shop_path.parent / "elsewhere.csv").write_text("a\n1\n")
    with pytest.raises(AdapterError):
        adapter.run(sql.format(folder=shop_path.parent), max_rows=10, seconds=10)
    assert not (shop_path.parent / "out.csv").exists()


def test_a_query_cannot_change_the_settings(adapter):
    with pytest.raises(AdapterError, match="configuration"):
        adapter.run("SET enable_external_access = true", max_rows=10, seconds=10)


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("enable_external_access", False),
        ("autoload_known_extensions", False),
        ("autoinstall_known_extensions", False),
        ("python_enable_replacements", False),  # Python variables are never read as tables
        ("lock_configuration", True),
    ],
)
def test_every_connection_carries_its_settings(adapter, setting, value):
    result = adapter.run(f"SELECT current_setting('{setting}')", max_rows=1, seconds=10)
    assert result.rows == [(value,)]


def test_rows_past_the_limit_are_not_read_and_it_says_so(adapter):
    cut = adapter.run("SELECT * FROM range(10)", max_rows=3, seconds=10)
    assert [row[0] for row in cut.rows] == [0, 1, 2]
    assert cut.more_rows is True
    whole = adapter.run("SELECT * FROM range(3)", max_rows=3, seconds=10)
    assert len(whole.rows) == 3
    assert whole.more_rows is False


def test_a_query_past_its_time_is_stopped(adapter):
    start = time.monotonic()
    with pytest.raises(TimedOut):
        adapter.run("SELECT sum(hash(a * b)) FROM range(100000000) x(a), range(1000) y(b)", max_rows=1, seconds=0.5)
    assert time.monotonic() - start < 10


def test_the_engines_own_reading(adapter):
    assert adapter.engine_reading("SELECT 1", seconds=10).refusal is None
    assert "2 statements" in adapter.engine_reading("SELECT 1; SELECT 2", seconds=10).refusal
    assert "EXPLAIN" in adapter.engine_reading("EXPLAIN SELECT 1", seconds=10).refusal
    assert "cannot parse" in adapter.engine_reading("SELEC 1", seconds=10).refusal


@pytest.mark.parametrize(
    ("sql", "tables"),
    [
        ("SELECT * FROM marts.orders", [(None, "marts", "orders")]),
        ("SELECT * FROM orders", [(None, "main", "orders")]),
        ("SELECT * FROM shop.marts.orders", [(None, "marts", "orders")]),
        ("SELECT * FROM other.marts.orders", [("other", "marts", "orders")]),
        ("SELECT * FROM marts.orders, (secret.salaries) x", [(None, "marts", "orders"), (None, "secret", "salaries")]),
        ("WITH x AS (SELECT * FROM raw.events) SELECT * FROM x", [(None, "raw", "events")]),
        ("SELECT * FROM range(3)", []),
    ],
)
def test_the_engine_names_the_tables_it_reads(adapter, sql, tables):
    assert sorted(adapter.engine_reading(sql, seconds=10).tables, key=str) == sorted(tables, key=str)


def test_tables_and_columns(adapter):
    assert adapter.catalog == "shop"
    tables = {(t.schema, t.name): t for t in adapter.tables()}
    assert tables["marts", "orders"].comment == "One row per paid order."
    assert tables["marts", "daily"].kind == "view"
    assert ("secret", "salaries") in tables  # the adapter lists all; the server keeps the readable ones
    columns = adapter.columns("marts", "orders")
    assert [c.name for c in columns] == [
        "order_id",
        "order_date",
        "customer_id",
        "currency",
        "amount",
        "refunded_amount",
    ]
    assert columns[4].type == "DECIMAL(12,2)"
    assert columns[4].comment == "Paid, before refunds."
    assert adapter.columns("MARTS", "Orders") == columns
    assert adapter.columns("marts", "nothing") == []


def test_a_missing_file_is_named(tmp_path):
    adapter = DuckDBAdapter(DuckDBSettings(path="nowhere.duckdb"), tmp_path)
    with pytest.raises(AdapterError, match="nowhere.duckdb does not exist"):
        adapter.check()
