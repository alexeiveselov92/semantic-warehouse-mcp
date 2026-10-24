"""The guard, refusal by refusal, in DuckDB's dialect and ClickHouse's; no database (docs/reference/guard.md)."""

import pytest
from sqlglot import exp

from semantic_warehouse_mcp import guard
from semantic_warehouse_mcp.guard import (
    REFUSALS,
    LargeTable,
    Refused,
    Rules,
    check,
    is_readable,
    large_table_bound,
    resolve_table,
)

DUCKDB = Rules(
    dialect="duckdb",
    default_schema="main",
    readable=("marts.*", "raw.events"),
    large_tables=(LargeTable("raw.events", "event_date"),),
    catalog="shop",
    allowed_table_functions=frozenset({"range", "generate_series", "unnest"}),
)

CLICKHOUSE = Rules(
    dialect="clickhouse",
    default_schema="default",
    readable=("marts.*", "raw.*"),
    large_tables=(LargeTable("raw.*", "event_date"),),
    allowed_table_functions=frozenset({"numbers"}),
    denied_functions=frozenset({"joinget*", "dictget*", "dicthas", "dictishierarchy*"}),
)


REFUSED = [
    # (rules, sql, code)
    (DUCKDB, "", "empty"),
    (DUCKDB, "  ;  ", "empty"),
    (DUCKDB, "-- only a comment", "empty"),
    (DUCKDB, "SELECT FROM WHERE", "unparsable"),
    (DUCKDB, "SELECT 'unterminated", "unparsable"),
    (DUCKDB, "SELECT 1; SELECT 2", "not_one_statement"),
    (DUCKDB, "SELECT 1; DROP TABLE marts.orders", "not_one_statement"),
    (DUCKDB, r"SELECT 'a\'; DROP TABLE marts.orders; --'", "not_one_statement"),
    (DUCKDB, "INSERT INTO marts.orders VALUES (1)", "not_a_select"),
    (DUCKDB, "UPDATE marts.orders SET amount = 0", "not_a_select"),
    (DUCKDB, "DELETE FROM marts.orders", "not_a_select"),
    (DUCKDB, "DROP TABLE marts.orders", "not_a_select"),
    (DUCKDB, "CREATE TABLE marts.x AS SELECT 1", "not_a_select"),
    (DUCKDB, "ATTACH 'other.duckdb' AS other", "not_a_select"),
    (DUCKDB, "COPY marts.orders TO 'out.csv'", "not_a_select"),
    (DUCKDB, "SET enable_external_access = true", "not_a_select"),
    (DUCKDB, "PRAGMA version", "not_a_select"),
    (DUCKDB, "EXPLAIN SELECT 1", "not_a_select"),
    (DUCKDB, "DESCRIBE marts.orders", "not_a_select"),
    (DUCKDB, "SUMMARIZE marts.orders", "not_a_select"),
    (DUCKDB, "WITH d AS (DELETE FROM marts.orders RETURNING *) SELECT * FROM d", "not_read_only"),
    (DUCKDB, "SELECT * INTO marts.copy FROM marts.orders", "not_read_only"),
    (DUCKDB, "SELECT * FROM marts.orders FOR UPDATE", "not_read_only"),
    (DUCKDB, "SELECT * FROM read_csv('/etc/passwd')", "table_function"),
    (DUCKDB, "SELECT * FROM read_parquet('s3://bucket/x.parquet')", "table_function"),
    (DUCKDB, "SELECT * FROM query_table('secret.salaries')", "table_function"),
    (DUCKDB, "SELECT * FROM duckdb_secrets()", "table_function"),
    (DUCKDB, "SELECT * FROM secret.salaries", "table_not_readable"),
    (DUCKDB, "SELECT * FROM raw.logs", "table_not_readable"),
    (DUCKDB, "SELECT * FROM orders", "table_not_readable"),  # main.orders: the default schema is not readable
    (DUCKDB, "SELECT * FROM other.marts.orders", "table_not_readable"),
    (DUCKDB, 'SELECT * FROM "marts.secret".salaries', "table_not_readable"),  # a pattern's * stays in its part
    (DUCKDB, "SELECT * FROM marts.orders UNION ALL SELECT * FROM secret.salaries", "table_not_readable"),
    (DUCKDB, "SELECT (SELECT max(amount) FROM secret.salaries) FROM marts.orders", "table_not_readable"),
    (
        DUCKDB,
        "SELECT * FROM marts.orders WHERE customer_id IN (SELECT name FROM secret.salaries)",
        "table_not_readable",
    ),
    (DUCKDB, "SELECT * FROM marts.orders, LATERAL (SELECT * FROM secret.salaries) s", "table_not_readable"),
    (DUCKDB, "WITH s AS (SELECT * FROM secret.salaries) SELECT * FROM s", "table_not_readable"),
    (DUCKDB, "SELECT count(*) FROM raw.events", "large_table_unbounded"),
    (DUCKDB, "SELECT count(*) FROM raw.events WHERE event_date <= DATE '2026-03-01'", "large_table_unbounded"),
    (DUCKDB, "SELECT count(*) FROM raw.events WHERE event_date >= DATE '2026-03-01' OR true", "large_table_unbounded"),
    (DUCKDB, "SELECT count(*) FROM raw.events WHERE event_date >= event_date - 1", "large_table_unbounded"),
    (DUCKDB, "SELECT count(*) FROM raw.events WHERE NOT (event_date > DATE '2026-03-01')", "large_table_unbounded"),
    (DUCKDB, "SELECT count(*) FROM raw.events WHERE NOT (event_date >= DATE '2026-03-01')", "large_table_unbounded"),
    (
        DUCKDB,
        "SELECT count(*) FROM raw.events WHERE event_date >= (SELECT min(order_date) FROM marts.orders)",
        "large_table_unbounded",
    ),
    (
        DUCKDB,
        "SELECT count(*) FROM raw.events WHERE CAST(event_date AS DATE) >= DATE '2026-03-01'",
        "large_table_unbounded",
    ),
    (DUCKDB, "SELECT * FROM (SELECT * FROM raw.events) WHERE event_date >= DATE '2026-03-01'", "large_table_unbounded"),
    (
        DUCKDB,
        "WITH e AS (SELECT * FROM raw.events) SELECT * FROM e WHERE event_date >= DATE '2026-03-01'",
        "large_table_unbounded",
    ),
    (
        DUCKDB,
        "SELECT * FROM raw.events e JOIN marts.orders o ON e.customer_id = o.customer_id "
        "WHERE event_date >= DATE '2026-03-01'",  # unqualified, two tables: whose column?
        "large_table_unbounded",
    ),
    (
        DUCKDB,
        "SELECT * FROM raw.events e JOIN marts.orders o ON e.customer_id = o.customer_id "
        "WHERE o.order_date >= DATE '2026-03-01'",  # another table's column bounds nothing
        "large_table_unbounded",
    ),
    (
        DUCKDB,
        "SELECT * FROM raw.events a JOIN raw.events b ON a.customer_id = b.customer_id "
        "WHERE a.event_date >= DATE '2026-03-01'",  # each reading of a large table is bounded on its own
        "large_table_unbounded",
    ),
    (DUCKDB, "SELECT * FROM (secret.salaries) x", "table_not_readable"),  # a table in parentheses is still read
    (DUCKDB, "SELECT * FROM marts.orders, (secret.salaries)", "table_not_readable"),
    (DUCKDB, "SELECT * FROM marts.orders JOIN (secret.salaries) s ON true", "table_not_readable"),
    (DUCKDB, "WITH salaries AS (SELECT 1) SELECT * FROM secret.salaries", "table_not_readable"),  # not the CTE
    (DUCKDB, "SELECT * FROM marts.orders, (raw.events)", "large_table_unbounded"),
    (DUCKDB, "SELECT * FROM range(3) WHERE 1 IN (SELECT 1 FROM raw.events)", "large_table_unbounded"),
    (CLICKHOUSE, "SELECT count() FROM raw.events", "large_table_unbounded"),
    (CLICKHOUSE, "SELECT count() FROM raw.sessions WHERE toDate(event_date) >= today()", "large_table_unbounded"),
    (CLICKHOUSE, "SELECT * FROM marts.orders SETTINGS max_result_rows = 0", "query_settings"),
    (CLICKHOUSE, "SELECT * FROM marts.orders SETTINGS readonly = 0", "query_settings"),
    (CLICKHOUSE, "SELECT * FROM marts.orders FORMAT JSONEachRow", "query_settings"),
    (CLICKHOUSE, "SELECT * FROM url('http://example.com/x.csv', CSV)", "table_function"),
    (CLICKHOUSE, "SELECT * FROM s3('https://example.com/bucket/x.parquet')", "table_function"),
    (CLICKHOUSE, "SELECT * FROM remote('db.example.com', marts.orders)", "table_function"),
    (CLICKHOUSE, "SELECT * FROM file('/etc/passwd', 'LineAsString')", "table_function"),
    (CLICKHOUSE, "SELECT joinGet('marts.lookup', 'value', 1)", "function_denied"),
    (CLICKHOUSE, "SELECT joinGetOrNull('secret.lookup', 'value', 1)", "function_denied"),  # a family, by glob
    (CLICKHOUSE, "SELECT dictGet('secret.dictionary', 'value', 1)", "function_denied"),
    (CLICKHOUSE, "SELECT dictGetStringOrDefault('secret.dictionary', 'value', 1, '')", "function_denied"),
    (CLICKHOUSE, "SELECT dictHas('secret.dictionary', 1)", "function_denied"),
    (CLICKHOUSE, "SELECT * FROM system.users", "table_not_readable"),
    (CLICKHOUSE, "SELECT * FROM Marts.orders", "table_not_readable"),  # ClickHouse names are case-sensitive
    (CLICKHOUSE, "SELECT * FROM marts.orders WHERE id GLOBAL IN (SELECT id FROM system.users)", "table_not_readable"),
    (DUCKDB, "SELECT * FROM marts.orders AS x, marts.customers AS x", "unsupported"),  # sqlglot's scopes fail
    (CLICKHOUSE, "SELECT * FROM marts.orders WHERE customer_id IN raw.events", "unsupported"),  # IN a table
    (CLICKHOUSE, "SELECT * FROM marts.orders WHERE customer_id GLOBAL IN secret.ids", "unsupported"),
    (CLICKHOUSE, "INSERT INTO marts.orders SELECT 1", "not_a_select"),
    (CLICKHOUSE, "SYSTEM DROP DNS CACHE", "not_a_select"),
]


@pytest.mark.parametrize(("rules", "sql", "code"), REFUSED, ids=[f"{r.dialect}:{s[:60]}" for r, s, _ in REFUSED])
def test_refused(rules, sql, code):
    with pytest.raises(Refused) as refusal:
        check(sql, rules)
    assert refusal.value.code == code, refusal.value.message


def test_every_refusal_has_a_case():
    # engine_parse comes from the engine's own parser: tests/test_duckdb.py, tests/test_server.py.
    assert {code for _, _, code in REFUSED} | {"engine_parse"} == set(REFUSALS)


ALLOWED = [
    # (rules, sql, the tables it reads)
    (DUCKDB, "SELECT * FROM marts.orders", ["marts.orders"]),
    (DUCKDB, "select * from MARTS.Orders;", ["marts.orders"]),  # DuckDB names are case-insensitive
    (DUCKDB, "SELECT * FROM shop.marts.orders", ["marts.orders"]),  # its own catalog
    (DUCKDB, "FROM marts.orders", ["marts.orders"]),
    (
        DUCKDB,
        "SELECT o.order_date, sum(o.amount) FROM marts.orders o JOIN marts.customers c USING (customer_id) "
        "WHERE c.country = 'DE' GROUP BY ALL ORDER BY 1",
        ["marts.customers", "marts.orders"],
    ),
    (DUCKDB, "WITH x AS (SELECT * FROM marts.orders) SELECT * FROM x", ["marts.orders"]),
    (DUCKDB, "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 3) SELECT * FROM r", []),
    (DUCKDB, "SELECT 1 UNION ALL SELECT 2", []),
    (
        DUCKDB,
        "SELECT * FROM marts.orders WHERE customer_id IN (SELECT customer_id FROM marts.customers)",
        ["marts.customers", "marts.orders"],
    ),
    (DUCKDB, "SELECT * FROM range(10)", []),
    (DUCKDB, "WITH orders AS (SELECT * FROM marts.orders) SELECT * FROM orders", ["marts.orders"]),
    (DUCKDB, "WITH a AS (SELECT 1 AS x), b AS (SELECT * FROM a) SELECT * FROM b JOIN a USING (x)", []),
    (DUCKDB, "SELECT * FROM (raw.events) e WHERE e.event_date >= DATE '2026-03-01'", ["raw.events"]),
    (DUCKDB, "SELECT * FROM generate_series(DATE '2026-03-01', DATE '2026-03-31', INTERVAL 1 DAY)", []),
    (DUCKDB, "SELECT * FROM unnest([1, 2, 3])", []),
    (DUCKDB, "SELECT * FROM (VALUES (1), (2)) t(x)", []),
    (DUCKDB, "SELECT * EXCLUDE (amount) FROM marts.orders QUALIFY row_number() OVER () = 1", ["marts.orders"]),
    (DUCKDB, "SELECT count(*) FROM raw.events WHERE event_date >= DATE '2026-03-01'", ["raw.events"]),
    (
        DUCKDB,
        "SELECT count(*) FROM raw.events WHERE DATE '2026-03-01' < event_date AND event_name = 'x'",
        ["raw.events"],
    ),
    (DUCKDB, "SELECT count(*) FROM raw.events WHERE (event_date >= current_date - 7)", ["raw.events"]),
    (DUCKDB, "SELECT count(*) FROM raw.events WHERE event_date = DATE '2026-03-01'", ["raw.events"]),
    (DUCKDB, "SELECT count(*) FROM raw.events WHERE NOT (event_date < DATE '2026-03-01')", ["raw.events"]),
    (
        DUCKDB,
        "SELECT count(*) FROM raw.events WHERE event_date BETWEEN DATE '2026-03-01' AND DATE '2026-03-07'",
        ["raw.events"],
    ),
    (
        DUCKDB,
        "SELECT count(*) FROM raw.events WHERE event_date IN (DATE '2026-03-01', DATE '2026-03-02')",
        ["raw.events"],
    ),
    (DUCKDB, "SELECT count(*) FROM raw.events e WHERE e.event_date >= DATE '2026-03-01'", ["raw.events"]),
    (
        DUCKDB,
        "SELECT * FROM raw.events e JOIN marts.orders o ON e.customer_id = o.customer_id "
        "WHERE e.event_date >= DATE '2026-03-01'",
        ["marts.orders", "raw.events"],
    ),
    (
        DUCKDB,
        "SELECT * FROM marts.orders o LEFT JOIN raw.events e "
        "ON e.customer_id = o.customer_id AND e.event_date >= DATE '2026-03-01'",
        ["marts.orders", "raw.events"],
    ),
    (
        DUCKDB,
        "SELECT * FROM marts.orders o WHERE EXISTS (SELECT 1 FROM raw.events e "
        "WHERE e.customer_id = o.customer_id AND e.event_date >= DATE '2026-03-01')",
        ["marts.orders", "raw.events"],
    ),
    (CLICKHOUSE, "SELECT count() FROM raw.events PREWHERE event_date >= today() - 7", ["raw.events"]),
    (CLICKHOUSE, "SELECT count() FROM raw.sessions WHERE event_date >= today() - INTERVAL 7 DAY", ["raw.sessions"]),
    (CLICKHOUSE, "SELECT count() FROM raw.events FINAL WHERE event_date = yesterday()", ["raw.events"]),
    (CLICKHOUSE, "SELECT count() FROM raw.events SAMPLE 0.1 WHERE event_date >= toDate('2026-03-01')", ["raw.events"]),
    (
        CLICKHOUSE,
        "SELECT e.event_name FROM raw.events e ARRAY JOIN e.tags AS tag WHERE e.event_date >= today()",
        ["raw.events"],
    ),
    (CLICKHOUSE, "SELECT number FROM numbers(10)", []),
    (
        CLICKHOUSE,
        "SELECT * FROM marts.orders o GLOBAL ANY LEFT JOIN marts.customers c USING (customer_id)",
        ["marts.customers", "marts.orders"],
    ),
    (CLICKHOUSE, "SELECT uniqExact(customer_id) FROM marts.orders LIMIT 1 BY currency", ["marts.orders"]),
]


@pytest.mark.parametrize(("rules", "sql", "tables"), ALLOWED, ids=[f"{r.dialect}:{s[:60]}" for r, s, _ in ALLOWED])
def test_allowed(rules, sql, tables):
    assert list(check(sql, rules).tables) == tables


def test_the_query_runs_as_written():
    sql = "select o.order_id  -- a comment stays\nfrom marts.orders o ;; \n"
    assert check(sql, DUCKDB).sql == "select o.order_id  -- a comment stays\nfrom marts.orders o"


def test_a_refusal_says_how_to_fix_it():
    with pytest.raises(Refused) as refusal:
        check("SELECT * FROM raw.events e JOIN marts.orders o USING (customer_id)", DUCKDB)
    assert "e.event_date >= " in refusal.value.message
    with pytest.raises(Refused) as refusal:
        check("SELECT * FROM secret.salaries", DUCKDB)
    assert "readable: marts.*, raw.events" in refusal.value.message


def test_names_as_stored_and_as_written():
    assert is_readable("marts", "orders", DUCKDB)
    assert is_readable("MARTS", "Orders", DUCKDB)  # DuckDB: case-insensitive
    assert not is_readable("Marts", "orders", CLICKHOUSE)  # ClickHouse: case-sensitive
    assert not is_readable("secret", "salaries", DUCKDB)
    assert large_table_bound("raw", "events", DUCKDB) == "event_date"
    assert large_table_bound("raw", "anything", CLICKHOUSE) == "event_date"
    assert large_table_bound("marts", "orders", DUCKDB) is None
    assert resolve_table("marts.orders", DUCKDB) == ("marts", "orders")
    with pytest.raises(Refused) as refusal:
        resolve_table("orders", DUCKDB)
    assert refusal.value.code == "table_not_readable"
    with pytest.raises(Refused):
        resolve_table("read_csv('x')", DUCKDB)


def test_a_parse_error_names_where_it_stopped():
    with pytest.raises(Refused) as refusal:
        check("SELECT FROM WHERE", DUCKDB)
    assert "at line 1, column 17 near 'WHERE'" in refusal.value.message
    assert "<Token" not in refusal.value.message


# The guard trusts sqlglot's scopes only to find where a table is selected and which names are CTEs'. Were they ever to
# misplace a table, it is still checked: these tests stand in for sqlglot being wrong.


def test_a_table_the_scopes_do_not_place_is_still_checked(monkeypatch):
    monkeypatch.setattr(guard, "traverse_scope", lambda statement: [])
    with pytest.raises(Refused) as refusal:
        check("SELECT * FROM secret.salaries", DUCKDB)
    assert refusal.value.code == "table_not_readable"
    with pytest.raises(Refused) as refusal:
        check("SELECT count(*) FROM raw.events WHERE event_date >= DATE '2026-03-01'", DUCKDB)
    assert refusal.value.code == "large_table_unbounded"  # its bound cannot be seen, so it is not taken on trust


class FakeScope:
    def __init__(self, expression, selected_sources, is_cte=False):
        self.expression = expression
        self.selected_sources = selected_sources
        self.is_cte = is_cte


def test_a_qualified_name_is_never_taken_for_a_cte(monkeypatch):
    def scopes_that_take_it_for_a_cte(statement):
        cte = FakeScope(statement.args["with_"].expressions[0].this, {}, is_cte=True)
        table = next(node for node in statement.find_all(exp.Table) if node.db == "secret")
        return [cte, FakeScope(statement, {"salaries": (table, cte)})]

    monkeypatch.setattr(guard, "Scope", FakeScope)
    monkeypatch.setattr(guard, "traverse_scope", scopes_that_take_it_for_a_cte)
    with pytest.raises(Refused) as refusal:
        check("WITH salaries AS (SELECT 1) SELECT * FROM secret.salaries", DUCKDB)
    assert refusal.value.code == "table_not_readable"


def test_the_engines_tables_are_held_to_the_same_rules():
    checked = check("SELECT * FROM marts.orders", DUCKDB)
    guard.check_engine_tables(((None, "marts", "orders"),), checked, DUCKDB)
    for tables, code in [
        (((None, "secret", "salaries"),), "table_not_readable"),
        ((("other", "marts", "orders"),), "table_not_readable"),  # another database
        (((None, "raw", "events"),), "large_table_unbounded"),  # a large table the guard did not see
    ]:
        with pytest.raises(Refused) as refusal:
            guard.check_engine_tables(tables, checked, DUCKDB)
        assert refusal.value.code == code
