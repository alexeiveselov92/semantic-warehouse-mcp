"""PostgreSQL, against a real server (tests/postgres_server.py): the read-only line holds past the guard, every limit
refuses or says it cut, the engine's plan names the tables a query reads — through the views it names — and the tools
answer over it."""

import decimal
import json
import time

import pytest
from conftest import write_config
from mcp import Client
from postgres_server import postgres  # noqa: F401 — the fixture

from semantic_warehouse_mcp import files
from semantic_warehouse_mcp.adapters import AdapterError, TimedOut
from semantic_warehouse_mcp.adapters import postgres as postgres_adapter
from semantic_warehouse_mcp.adapters.postgres import PostgresAdapter, PostgresSettings
from semantic_warehouse_mcp.config import ConfigError, load
from semantic_warehouse_mcp.server import build_server, main

pytestmark = pytest.mark.anyio

CONFIG = """
connections:
  - name: warehouse
    engine: postgres
    host: {host}
    port: {port}
    database: {database}
    user_env: SWM_TEST_PG_USER
    password_env: SWM_TEST_PG_PASSWORD
    sslmode: disable
    readable: ["swm_marts.*", "swm_raw.events"]
    large_tables:
      - {{ table: swm_raw.events, bound_by: event_date }}
    limits: {{ rows: 3, seconds: 2 }}
"""


@pytest.fixture
def as_user(monkeypatch):
    def login(user, password=None):
        monkeypatch.setenv("SWM_TEST_PG_USER", user)
        monkeypatch.setenv("SWM_TEST_PG_PASSWORD", password if password is not None else user)

    login("swm_writer")  # a user who may write: the server must not, all the same
    return login


def settings_of(server) -> PostgresSettings:
    return PostgresSettings(
        host=server.host, port=server.port, database=server.database, user_env="SWM_TEST_PG_USER",
        password_env="SWM_TEST_PG_PASSWORD", sslmode="disable",
    )  # fmt: skip


@pytest.fixture
def adapter(postgres, as_user, tmp_path):  # noqa: F811
    adapter = PostgresAdapter(settings_of(postgres), tmp_path)
    adapter.check()
    return adapter


@pytest.fixture
def warehouse(postgres, as_user, tmp_path):  # noqa: F811
    body = CONFIG.format(host=postgres.host, port=postgres.port, database=postgres.database)
    return load(write_config(tmp_path, body))


async def call(config, tool, **arguments):
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    return (text, True) if result.is_error else (json.loads(text), False)


def orders(postgres) -> int:  # noqa: F811
    return postgres.run("SELECT count(*) FROM swm_marts.orders")[0][0]


# --- the read-only line, past the guard -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT swm_marts.write_one()",  # a function that writes: the guard cannot see inside it
        "SELECT nextval('swm_marts.ids')",
        "SELECT * FROM swm_marts.orders FOR UPDATE",
        "WITH gone AS (DELETE FROM swm_marts.orders RETURNING *) SELECT * FROM gone",
    ],
)
def test_nothing_is_written_whoever_the_user(adapter, postgres, sql):  # noqa: F811
    before = orders(postgres)
    with pytest.raises(AdapterError):
        adapter.run(sql, max_rows=10, seconds=5)
    assert orders(postgres) == before


def test_the_transaction_is_read_only_even_when_asked_otherwise(adapter, postgres):  # noqa: F811
    with pytest.raises(AdapterError, match="read-only transaction"):
        adapter.run("SELECT swm_marts.write_one()", max_rows=10, seconds=5)
    assert postgres.run("SELECT count(*) FROM swm_marts.orders WHERE order_id = 'written'") == [(0,)]


def test_each_layer_of_the_read_only_line(adapter):
    """The session's default is read-only, and so is each transaction the adapter opens — each on its own."""
    result = adapter.run(
        "SELECT current_setting('default_transaction_read_only'), current_setting('transaction_read_only')",
        max_rows=1, seconds=5,
    )  # fmt: skip
    assert result.rows == [("on", "on")]
    with adapter._connect() as connection:
        assert connection.read_only is True


def test_a_server_that_does_not_open_a_read_only_transaction_is_refused(postgres, as_user, tmp_path, monkeypatch):  # noqa: F811
    import psycopg

    def writable():
        return psycopg.connect(host=postgres.host, port=postgres.port, dbname=postgres.database, user="swm_writer",
                               password="swm_writer")  # fmt: skip

    adapter = PostgresAdapter(settings_of(postgres), tmp_path)
    monkeypatch.setattr(adapter, "_connect", writable)
    with pytest.raises(AdapterError, match="did not open a read-only transaction"):
        adapter.check()


def test_a_query_is_stopped_by_the_connection_when_the_server_does_not(adapter, monkeypatch):
    monkeypatch.setattr(postgres_adapter, "_limit", lambda connection, seconds: None)  # no statement timeout
    monkeypatch.setattr(postgres_adapter, "CANCEL_MARGIN_SECONDS", 1)
    started = time.monotonic()
    with pytest.raises(TimedOut):
        adapter.run("SELECT count(*) FROM generate_series(1, 10000000000)", max_rows=3, seconds=1)
    assert time.monotonic() - started < 5


def test_a_superuser_is_refused(postgres, as_user, tmp_path):  # noqa: F811
    as_user(*postgres.admin)
    with pytest.raises(AdapterError, match="is a superuser"):
        PostgresAdapter(settings_of(postgres), tmp_path).check()


def test_a_user_that_may_read_the_servers_files_is_refused(postgres, as_user, tmp_path):  # noqa: F811
    as_user("swm_files")
    with pytest.raises(AdapterError, match="holds pg_read_server_files"):
        PostgresAdapter(settings_of(postgres), tmp_path).check()


def test_a_user_that_owns_anything_is_refused(postgres, as_user, tmp_path):  # noqa: F811
    as_user("swm_owner")
    with pytest.raises(AdapterError, match=r"owns swm_owned.notes.*an owner may maintain what it owns"):
        PostgresAdapter(settings_of(postgres), tmp_path).check()


def test_a_user_that_may_read_beyond_readable_is_noted(postgres, as_user, tmp_path):  # noqa: F811
    body = CONFIG.format(host=postgres.host, port=postgres.port, database=postgres.database)
    as_user("swm_writer")
    [note] = load(write_config(tmp_path, body)).connections["warehouse"].notes
    assert note == (
        "the user may read 1 table `readable` leaves out (swm_secret.salaries): queries are held to `readable`, but a "
        "function defined in the database reads with the user's grants — grant the user only what agents may read"
    )
    as_user("swm_reader")
    assert load(write_config(tmp_path, body)).connections["warehouse"].notes == ()


@pytest.mark.parametrize(("user", "held"), [("swm_reads_all", "pg_read_all_data"), ("swm_bypass", "BYPASSRLS")])
def test_a_user_that_reads_past_the_grants_is_noted(postgres, as_user, tmp_path, user, held):  # noqa: F811
    as_user(user)
    adapter = PostgresAdapter(settings_of(postgres), tmp_path)
    adapter.check()
    assert adapter.notes == [
        f"the user {user} holds {held}: it reads every table and every row, whatever the database's grants and row "
        "security say — a function defined in the database reads with them, past `readable`"
    ]


def test_a_read_only_role_is_served(postgres, as_user, tmp_path):  # noqa: F811
    as_user("swm_reader")
    adapter = PostgresAdapter(settings_of(postgres), tmp_path)
    adapter.check()
    assert (adapter.catalog, adapter.default_schema) == (postgres.database, "public")
    result = adapter.run("SELECT count(*) FROM swm_marts.orders", max_rows=10, seconds=5)
    assert result.rows == [(6,)]


def test_a_wrong_password_is_named_never_repeated(postgres, as_user, tmp_path):  # noqa: F811
    as_user("swm_reader", "not-the-password-0451")
    with pytest.raises(AdapterError) as error:
        PostgresAdapter(settings_of(postgres), tmp_path).check()
    assert "password authentication failed" in str(error.value)
    assert "not-the-password-0451" not in str(error.value)


def test_a_variable_not_set_is_named(postgres, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.delenv("SWM_TEST_PG_USER", raising=False)
    with pytest.raises(AdapterError, match="the variable SWM_TEST_PG_USER is not set"):
        PostgresAdapter(settings_of(postgres), tmp_path).check()


def test_the_driver_not_installed_names_its_extra(postgres, as_user, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(postgres_adapter, "psycopg", None)
    with pytest.raises(AdapterError, match=r"pip install 'semantic-warehouse-mcp\[postgres\]'"):
        PostgresAdapter(settings_of(postgres), tmp_path).check()


# --- limits ---------------------------------------------------------------------------------------------------------


def test_rows_past_the_limit_are_never_read(adapter):
    # Ten orders joined to each other: 6^10 = 60 million rows a client fetching all would wait on, or run out of
    # memory with — read through a cursor, the server makes only the four asked for.
    joined = ", ".join(f"swm_marts.orders t{index}" for index in range(10))
    started = time.monotonic()
    result = adapter.run(f"SELECT t0.order_id FROM {joined}", max_rows=3, seconds=30)
    assert len(result.rows) == 3
    assert result.more_rows is True
    assert time.monotonic() - started < 3


def test_a_query_past_its_time_is_stopped(adapter):
    started = time.monotonic()
    with pytest.raises(TimedOut, match="stopped after 1 s"):
        adapter.run("SELECT count(*) FROM generate_series(1, 10000000000)", max_rows=3, seconds=1)
    assert time.monotonic() - started < 5


def test_text_with_percent_and_braces_runs_as_written(adapter):
    result = adapter.run("SELECT note FROM swm_marts.orders WHERE note LIKE '%{sale}%'", max_rows=3, seconds=5)
    assert result.rows == [("50% off {sale}",)]


# --- the engine's own reading ---------------------------------------------------------------------------------------


def test_the_plan_names_the_tables_read(adapter):
    reading = adapter.engine_reading(
        "SELECT o.order_id FROM swm_marts.orders o JOIN swm_raw.events e ON e.customer_id = o.customer_id "
        "WHERE e.event_date >= '2026-03-01'",
        seconds=5,
        named=("swm_marts.orders", "swm_raw.events"),
    )
    assert reading.refusal is None
    assert reading.tables == ((None, "swm_marts", "orders"), (None, "swm_raw", "events"))  # partitions: the table


def test_a_view_named_is_read_through_and_its_own_tables_left_to_it(adapter):
    reading = adapter.engine_reading("SELECT doubled FROM swm_marts.payroll_nested", seconds=5,
                                     named=("swm_marts.payroll_nested",))  # fmt: skip
    assert reading.tables == ()
    unnamed = adapter.engine_reading("SELECT doubled FROM swm_marts.payroll_nested", seconds=5)
    assert unnamed.tables == ((None, "swm_secret", "salaries"),)  # what the guard would refuse, unless named


def test_a_query_postgres_cannot_plan_is_refused(adapter):
    reading = adapter.engine_reading("SELECT nope FROM swm_marts.orders", seconds=5, named=("swm_marts.orders",))
    assert reading.refusal == (
        'PostgreSQL cannot plan it: column "nope" does not exist — Perhaps you meant to reference the column '
        '"orders.note".'
    )


# --- through the tools ----------------------------------------------------------------------------------------------


async def test_a_query_answers_with_exact_values(warehouse):
    answer, _ = await call(
        warehouse, "run_query",
        sql="SELECT order_id, order_date, amount, big, attrs FROM swm_marts.orders WHERE order_id = 'o1'",
    )  # fmt: skip
    assert answer["rows"] == [["o1", "2026-03-01", 10.1, 18446744073709551615, {"gift": True}]]
    assert [column["type"] for column in answer["columns"]] == ["text", "date", "numeric", "numeric", "jsonb"]
    assert decimal.Decimal(str(answer["rows"][0][2])) == decimal.Decimal("10.10")


async def test_revenue_by_currency(warehouse):
    answer, _ = await call(
        warehouse, "run_query",
        sql="SELECT currency, sum(amount - refunded_amount) AS revenue FROM swm_marts.orders GROUP BY currency "
        "ORDER BY currency",
    )  # fmt: skip
    assert answer["rows"] == [["EUR", 25.55], ["USD", 30.3]]


async def test_rows_past_the_limit_say_so(warehouse):
    answer, _ = await call(warehouse, "run_query", sql="SELECT order_id FROM swm_marts.orders ORDER BY order_id")
    assert answer["rows"] == [["o1"], ["o2"], ["o3"]]
    assert answer["truncated_by"] == ["rows"]


async def test_a_readable_view_over_a_secret_table_is_served(warehouse):
    answer, _ = await call(warehouse, "run_query", sql="SELECT total FROM swm_marts.payroll_total")
    assert answer["rows"] == [[1]]


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("SELECT * FROM swm_secret.salaries", "table_not_readable"),
        ("SELECT * FROM swm_raw.events", "large_table_unbounded"),
        ("SELECT * FROM swm_raw.events_2026_03", "table_not_readable"),
        ("SELECT query_to_xml('SELECT * FROM swm_secret.salaries', true, false, '')", "function_denied"),
        ("SELECT pg_read_file('/etc/passwd')", "function_denied"),
        ("SELECT set_config('transaction_read_only', 'off', true)", "function_denied"),
        ("SELECT pg_sleep(10)", "function_denied"),
        ("SELECT lo_import('/etc/passwd')", "function_denied"),
        ("SELECT brin_summarize_new_values('swm_owned.notes_brin')", "function_denied"),
        ("SELECT brin_desummarize_range('swm_owned.notes_brin', 0)", "function_denied"),
        ("SELECT gin_clean_pending_list('swm_owned.notes_gin')", "function_denied"),
        ("SELECT pg_stat_get_live_tuples('swm_secret.salaries'::regclass)", "function_denied"),
        ("SELECT pg_export_snapshot()", "function_denied"),
        ("SELECT * FROM pg_ls_dir('.')", "function_denied"),
        ("SELECT * FROM pg_stat_get_activity(NULL)", "function_denied"),
        ("SELECT * FROM regexp_matches('a', 'a')", "table_function"),
        ("SELECT * INTO swm_marts.copy FROM swm_marts.orders", "not_read_only"),
        ("SELECT * FROM other_db.swm_marts.orders", "table_not_readable"),
    ],
)
async def test_the_guard_refuses_over_postgres(warehouse, sql, code):
    text, is_error = await call(warehouse, "run_query", sql=sql)
    assert is_error
    assert f"({code})" in text


async def test_a_table_function_allowed(warehouse):
    answer, _ = await call(warehouse, "run_query", sql="SELECT key FROM jsonb_each('{\"a\": 1}'::jsonb)")
    assert answer["rows"] == [["a"]]


async def test_a_query_past_its_time_says_so(warehouse):
    text, is_error = await call(warehouse, "run_query", sql="SELECT count(*) FROM generate_series(1, 10000000000)")
    assert is_error
    assert "Stopped after 2 s, the time limit of warehouse" in text


async def test_tables_and_columns_as_the_database_holds_them(warehouse):
    listed, _ = await call(warehouse, "list_tables")
    assert listed["tables"] == [
        {"table": "swm_marts.daily", "kind": "view"},
        {"table": "swm_marts.orders", "kind": "table", "comment": "One row per paid order."},
        {"table": "swm_marts.payroll_nested", "kind": "view"},
        {"table": "swm_marts.payroll_total", "kind": "view"},
        {"table": "swm_raw.events", "kind": "table", "bound_by": "event_date"},
    ]  # no partition listed, no table of another schema
    described, _ = await call(warehouse, "describe_table", table="swm_marts.orders")
    amount = next(column for column in described["columns"] if column["name"] == "amount")
    assert amount == {"name": "amount", "type": "numeric(12,2)", "nullable": True, "comment": "Paid, before refunds."}


def test_a_wrong_engine_key_is_named(postgres, as_user, tmp_path):  # noqa: F811
    body = CONFIG.format(host=postgres.host, port=postgres.port, database=postgres.database).replace(
        "sslmode: disable", "ssl_mode: disable"
    )
    with pytest.raises(ConfigError, match="ssl_mode: unknown key — did you mean 'sslmode'"):
        load(write_config(tmp_path, body))


def test_a_draft_of_the_model_is_written_from_the_databases_catalog(postgres, as_user, tmp_path, capsys):  # noqa: F811
    """A database no dbt project describes: its tables, their comments and columns, read by `import-database`."""
    path = write_config(tmp_path, CONFIG.format(host=postgres.host, port=postgres.port, database=postgres.database))
    draft = tmp_path / "draft.yaml"
    main(["import-database", "--config", str(path), "--output", str(draft), "--tables", "swm_marts.orders"])
    (orders,) = files.read_yaml(draft.read_text(encoding="utf-8"))["datasets"]
    assert orders["source"] == "swm_marts.orders" and orders["description"] == "One row per paid order."
    fields = {field["name"]: field for field in orders["fields"]}
    assert fields["amount"]["description"] == "Paid, before refunds."
    assert fields["order_date"]["dimension"] == {"is_time": True} and "dimension" not in fields["note"]
    served = CONFIG.replace("    limits:", f"    semantic_models: [{draft.name}]\n    limits:")
    load(write_config(tmp_path, served.format(host=postgres.host, port=postgres.port, database=postgres.database)))
