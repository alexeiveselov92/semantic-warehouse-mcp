"""MySQL, against a real server (tests/mysql_server.py): the read-only line holds past the guard, text MySQL runs that
reads as a comment is refused, every limit refuses or says it cut, the plan names the tables a query reads — through
the views it names — and the tools answer over it."""

import json
import time

import pytest
from conftest import write_config
from mcp import Client
from mysql_server import mysql  # noqa: F401 — the fixture

from semantic_warehouse_mcp.adapters import AdapterError, TimedOut
from semantic_warehouse_mcp.adapters import mysql as mysql_adapter
from semantic_warehouse_mcp.adapters.mysql import MySQLAdapter, MySQLSettings
from semantic_warehouse_mcp.config import ConfigError, load
from semantic_warehouse_mcp.server import build_server

pytestmark = pytest.mark.anyio

CONFIG = """
connections:
  - name: warehouse
    engine: mysql
    host: {host}
    port: {port}
    database: swm_marts
    user_env: SWM_TEST_MY_USER
    password_env: SWM_TEST_MY_PASSWORD
    tls: on
    readable: ["swm_marts.*", "swm_raw.events"]
    large_tables:
      - {{ table: swm_raw.events, bound_by: event_date }}
    limits: {{ rows: 3, seconds: 2 }}
"""

NINE_DIGITS = ", ".join(f"swm_marts.digits t{index}" for index in range(9))  # a billion rows


@pytest.fixture
def as_user(monkeypatch):
    def login(user, password=None):
        monkeypatch.setenv("SWM_TEST_MY_USER", user)
        monkeypatch.setenv("SWM_TEST_MY_PASSWORD", password if password is not None else user)

    login("swm_writer")  # a user who may write: the server must not, all the same
    return login


def settings_of(server) -> MySQLSettings:
    return MySQLSettings(host=server.host, port=server.port, database="swm_marts", user_env="SWM_TEST_MY_USER",
                         password_env="SWM_TEST_MY_PASSWORD", tls="on")  # fmt: skip


@pytest.fixture
def adapter(mysql, as_user, tmp_path):  # noqa: F811
    adapter = MySQLAdapter(settings_of(mysql), tmp_path)
    adapter.check()
    return adapter


@pytest.fixture
def warehouse(mysql, as_user, tmp_path):  # noqa: F811
    return load(write_config(tmp_path, CONFIG.format(host=mysql.host, port=mysql.port)))


async def call(config, tool, **arguments):
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    return (text, True) if result.is_error else (json.loads(text), False)


def orders(server) -> int:
    return server.run("SELECT COUNT(*) FROM swm_marts.orders")[0][0]


# --- the read-only line, past the guard -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT swm_marts.write_one()",  # a function that writes: the guard cannot see inside it
        "SELECT * FROM swm_marts.orders FOR UPDATE",
        "INSERT INTO swm_marts.orders (order_id) VALUES ('x')",
    ],
)
def test_nothing_is_written_whoever_the_user(adapter, mysql, sql):  # noqa: F811
    before = orders(mysql)
    with pytest.raises(AdapterError, match="READ ONLY transaction"):
        adapter.run(sql, max_rows=10, seconds=5)
    assert orders(mysql) == before


def test_each_layer_of_the_read_only_line(adapter):
    result = adapter.run("SELECT @@session.transaction_read_only, @@session.sql_select_limit", max_rows=4, seconds=5)
    assert result.rows == [(1, 5)]  # read-only; and the server asked for one row past the limit, no more


def test_a_server_that_does_not_make_the_session_read_only_is_refused(mysql, as_user, tmp_path, monkeypatch):  # noqa: F811
    import pymysql

    def writable(streaming=False):
        return pymysql.connect(host=mysql.host, port=mysql.port, user="swm_writer", password="swm_writer",
                               database="swm_marts", autocommit=True)  # fmt: skip

    adapter = MySQLAdapter(settings_of(mysql), tmp_path)
    monkeypatch.setattr(adapter, "_connect", writable)
    with pytest.raises(AdapterError, match="did not make the session read-only"):
        adapter.check()


def test_every_query_runs_in_a_read_only_transaction(adapter, mysql, monkeypatch):  # noqa: F811
    """Past the session's own setting: a transaction started READ ONLY stops a write on its own."""
    connect = adapter._connect

    def without_the_session_setting(streaming=False):
        connection = connect(streaming)
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ WRITE")
        return connection

    monkeypatch.setattr(adapter, "_connect", without_the_session_setting)
    before = orders(mysql)
    with pytest.raises(AdapterError, match="READ ONLY transaction"):
        adapter.run("SELECT swm_marts.write_one()", max_rows=10, seconds=5)
    assert orders(mysql) == before


def test_a_user_that_may_reach_the_servers_files_is_refused(mysql, as_user, tmp_path):  # noqa: F811
    as_user("swm_files")
    with pytest.raises(AdapterError, match="holds FILE on every database"):
        MySQLAdapter(settings_of(mysql), tmp_path).check()


def test_root_is_refused(mysql, as_user, tmp_path):  # noqa: F811
    as_user(*mysql.admin)
    with pytest.raises(AdapterError, match="holds FILE, SUPER on every database"):
        MySQLAdapter(settings_of(mysql), tmp_path).check()


def test_a_read_only_user_is_served(mysql, as_user, tmp_path):  # noqa: F811
    as_user("swm_reader")
    adapter = MySQLAdapter(settings_of(mysql), tmp_path)
    adapter.check()
    assert adapter.run("SELECT COUNT(*) FROM swm_marts.orders", max_rows=10, seconds=5).rows == [(6,)]


def test_a_wrong_password_is_named_never_repeated(mysql, as_user, tmp_path):  # noqa: F811
    as_user("swm_reader", "not-the-password-0451")
    with pytest.raises(AdapterError) as error:
        MySQLAdapter(settings_of(mysql), tmp_path).check()
    assert "Access denied for user 'swm_reader'" in str(error.value)
    assert "not-the-password-0451" not in str(error.value)


def test_the_driver_not_installed_names_its_extra(mysql, as_user, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(mysql_adapter, "pymysql", None)
    with pytest.raises(AdapterError, match=r"pip install 'semantic-warehouse-mcp\[mysql\]'"):
        MySQLAdapter(settings_of(mysql), tmp_path).check()


# --- text MySQL runs that reads as a comment ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT order_id /*! , (SELECT name FROM swm_secret.salaries LIMIT 1) */ FROM swm_marts.orders",
        "SELECT /*+ MAX_EXECUTION_TIME(1000000) */ order_id FROM swm_marts.orders",
        "SELECT /*+ SET_VAR(sql_select_limit = 1000000) */ order_id FROM swm_marts.orders",
    ],
)
async def test_a_comment_mysql_would_run_is_refused(warehouse, sql):
    text, is_error = await call(warehouse, "run_query", sql=sql)
    assert is_error
    assert "(engine_parse)" in text and "which MySQL runs though it reads as a comment" in text


@pytest.fixture
def server_mode(mysql):  # noqa: F811
    """The server's own sql_mode set by its administrator for a test — and put back after it."""
    before = mysql.run("SELECT @@GLOBAL.sql_mode")[0][0]

    def set_mode(*modes):
        mysql.run("SET GLOBAL sql_mode = %s", ",".join([before, *modes]))

    yield set_mode
    mysql.run("SET GLOBAL sql_mode = %s", before)


async def test_a_string_ends_where_the_guard_reads_it_whatever_the_servers_mode(warehouse, server_mode, adapter):
    """A server set to NO_BACKSLASH_ESCAPES would end `'a\\'` at the backslash: text the guard reads inside a string —
    a subquery on a secret table read by its key, which no plan names — would run. The session sets the mode off."""
    server_mode("NO_BACKSLASH_ESCAPES", "ANSI_QUOTES")
    sql = "SELECT 'a\\' AS x, (SELECT ssn FROM swm_secret.people WHERE id = 1) AS leaked -- '"
    answer, is_error = await call(warehouse, "run_query", sql=sql)
    assert "111-SECRET" not in json.dumps(answer)
    assert not is_error and len(answer["rows"]) == 1 and len(answer["rows"][0]) == 1  # one string, nothing run in it
    modes = adapter.run("SELECT @@SESSION.sql_mode", max_rows=1, seconds=5).rows[0][0].split(",")
    assert not {"NO_BACKSLASH_ESCAPES", "ANSI_QUOTES", "ANSI"} & set(modes)


def test_a_session_that_keeps_a_mode_changing_the_text_is_refused(mysql, as_user, tmp_path, server_mode, monkeypatch):  # noqa: F811
    import pymysql

    server_mode("NO_BACKSLASH_ESCAPES")

    def unpinned(streaming=False):
        return pymysql.connect(
            host=mysql.host, port=mysql.port, user="swm_reader", password="swm_reader", database="swm_marts",
            autocommit=True, init_command="SET SESSION TRANSACTION READ ONLY",
        )  # fmt: skip

    adapter = MySQLAdapter(settings_of(mysql), tmp_path)
    monkeypatch.setattr(adapter, "_connect", unpinned)
    with pytest.raises(AdapterError, match="NO_BACKSLASH_ESCAPES.*otherwise than the guard"):
        adapter.check()


def test_the_adapter_never_runs_one_either(adapter):
    with pytest.raises(AdapterError, match="is not run"):
        adapter.run("SELECT 1 /*! , 2 */", max_rows=1, seconds=5)


# --- limits ---------------------------------------------------------------------------------------------------------


def test_rows_past_the_limit_are_not_read(adapter, mysql):  # noqa: F811
    started = time.monotonic()
    result = adapter.run(f"SELECT t0.d FROM {NINE_DIGITS} LIMIT 1000000000", max_rows=3, seconds=30)
    assert len(result.rows) == 3
    assert result.more_rows is True
    assert time.monotonic() - started < 25
    time.sleep(0.5)
    running = mysql.run("SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE USER = 'swm_writer' AND "
                        "COMMAND = 'Query' AND INFO LIKE '%digits t8%'")  # fmt: skip
    assert running == [(0,)]  # stopped by the server, not left to run on


def test_a_query_past_its_time_is_stopped(adapter):
    started = time.monotonic()
    with pytest.raises(TimedOut, match="stopped after 1 s"):
        adapter.run(f"SELECT COUNT(*) FROM {NINE_DIGITS} WHERE t0.d + t8.d = 3", max_rows=3, seconds=1)
    assert time.monotonic() - started < 5


def test_a_query_is_stopped_by_the_connection_when_the_server_does_not(adapter, monkeypatch):
    monkeypatch.setattr(mysql_adapter, "_limit", lambda cursor, seconds: None)
    monkeypatch.setattr(mysql_adapter, "CANCEL_MARGIN_SECONDS", 1)
    started = time.monotonic()
    with pytest.raises(TimedOut):
        adapter.run(f"SELECT COUNT(*) FROM {NINE_DIGITS} WHERE t0.d + t8.d = 3", max_rows=3, seconds=1)
    assert time.monotonic() - started < 6


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
    assert reading.tables == ((None, "swm_marts", "orders"), (None, "swm_raw", "events"))


def test_a_view_named_is_read_through_and_its_own_tables_left_to_it(adapter):
    query = "SELECT name FROM swm_marts.payroll_names WHERE name <> ''"
    unnamed = adapter.engine_reading(query, seconds=5)
    assert unnamed.tables == ((None, "swm_secret", "salaries"),)  # what the guard would refuse, unless named
    reading = adapter.engine_reading(query, seconds=5, named=("swm_marts.payroll_names",))
    assert reading.tables == ()


def test_a_query_mysql_cannot_plan_is_refused(adapter):
    reading = adapter.engine_reading("SELECT nope FROM swm_marts.orders", seconds=5, named=("swm_marts.orders",))
    assert reading.refusal == "MySQL cannot plan it: Unknown column 'nope' in 'field list'"


# --- through the tools ----------------------------------------------------------------------------------------------


async def test_a_query_answers_with_exact_values(warehouse):
    answer, _ = await call(
        warehouse, "run_query",
        sql="SELECT order_id, order_date, amount, big, attrs FROM swm_marts.orders WHERE order_id = 'o1'",
    )  # fmt: skip
    assert answer["rows"] == [["o1", "2026-03-01", 10.1, 18446744073709551615, '{"gift": true}']]
    assert [column["type"] for column in answer["columns"]] == ["var_string", "date", "newdecimal", "longlong", "json"]


async def test_revenue_by_currency(warehouse):
    answer, _ = await call(
        warehouse, "run_query",
        sql="SELECT currency, SUM(amount - refunded_amount) AS revenue FROM swm_marts.orders GROUP BY currency "
        "ORDER BY currency",
    )  # fmt: skip
    assert answer["rows"] == [["EUR", 25.55], ["USD", 30.3]]


async def test_rows_past_the_limit_say_so(warehouse):
    answer, _ = await call(warehouse, "run_query", sql="SELECT order_id FROM swm_marts.orders ORDER BY order_id")
    assert answer["rows"] == [["o1"], ["o2"], ["o3"]]
    assert answer["truncated_by"] == ["rows"]


async def test_a_readable_view_over_a_secret_table_is_served(warehouse):
    answer, _ = await call(warehouse, "run_query", sql="SELECT total FROM swm_marts.payroll_total")
    assert answer["rows"] == [[3]]


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("SELECT * FROM swm_secret.salaries", "table_not_readable"),
        ("SELECT * FROM swm_raw.events", "large_table_unbounded"),
        ("SELECT LOAD_FILE('/etc/passwd')", "function_denied"),
        ("SELECT SLEEP(10)", "function_denied"),
        ("SELECT GET_LOCK('a', 0)", "function_denied"),
        ("SELECT BENCHMARK(100000000, MD5('a'))", "function_denied"),
        ("SELECT UUID_SHORT()", "function_denied"),
        ("SELECT SQL_CALC_FOUND_ROWS order_id FROM swm_marts.orders", "engine_parse"),
        ("SELECT order_id INTO @v FROM swm_marts.orders", "not_read_only"),
        ("SELECT * FROM swm_marts.orders INTO OUTFILE '/tmp/x'", "unparsable"),
        ("SELECT * FROM mysql.user", "table_not_readable"),
    ],
)
async def test_the_guard_refuses_over_mysql(warehouse, sql, code):
    text, is_error = await call(warehouse, "run_query", sql=sql)
    assert is_error
    assert f"({code})" in text


async def test_a_query_past_its_time_says_so(warehouse):
    text, is_error = await call(warehouse, "run_query", sql=f"SELECT COUNT(*) FROM {NINE_DIGITS} WHERE t0.d = 3")
    assert is_error
    assert "Stopped after 2 s, the time limit of warehouse" in text


async def test_tables_and_columns_as_the_database_holds_them(warehouse):
    listed, _ = await call(warehouse, "list_tables")
    assert listed["tables"] == [
        {"table": "swm_marts.daily", "kind": "view"},
        {"table": "swm_marts.digits", "kind": "table"},
        {"table": "swm_marts.orders", "kind": "table", "comment": "One row per paid order."},
        {"table": "swm_marts.payroll_names", "kind": "view"},
        {"table": "swm_marts.payroll_nested", "kind": "view"},
        {"table": "swm_marts.payroll_total", "kind": "view"},
        {"table": "swm_raw.events", "kind": "table", "bound_by": "event_date"},
    ]
    described, _ = await call(warehouse, "describe_table", table="swm_marts.orders")
    amount = next(column for column in described["columns"] if column["name"] == "amount")
    assert amount == {"name": "amount", "type": "decimal(12,2)", "nullable": True, "comment": "Paid, before refunds."}


def test_a_user_that_may_read_beyond_readable_is_noted(mysql, as_user, tmp_path):  # noqa: F811
    body = CONFIG.format(host=mysql.host, port=mysql.port)
    [note] = load(write_config(tmp_path, body)).connections["warehouse"].notes
    assert note.startswith("the user may read 2 tables `readable` leaves out (swm_secret.people, swm_secret.salaries)")
    as_user("swm_reader")
    assert load(write_config(tmp_path, body)).connections["warehouse"].notes == ()


def test_a_wrong_engine_key_is_named(mysql, as_user, tmp_path):  # noqa: F811
    body = CONFIG.format(host=mysql.host, port=mysql.port).replace("tls: on", "ssl: on")
    with pytest.raises(ConfigError, match="ssl: unknown key"):
        load(write_config(tmp_path, body))


@pytest.mark.parametrize("tls", ["off", "on"])
def test_reached_with_tls_or_without(mysql, as_user, tmp_path, tls):  # noqa: F811
    as_user("swm_reader")
    settings = settings_of(mysql).model_copy(update={"tls": tls})
    adapter = MySQLAdapter(settings, tmp_path)
    adapter.check()
    assert adapter.run("SELECT COUNT(*) FROM swm_marts.orders", max_rows=1, seconds=5).rows == [(6,)]


def test_verify_without_a_trusted_certificate_is_refused(mysql, as_user, tmp_path):  # noqa: F811
    settings = settings_of(mysql).model_copy(update={"tls": "verify"})
    with pytest.raises(AdapterError, match="cannot reach"):
        MySQLAdapter(settings, tmp_path).check()  # the server's own certificate is self-signed
