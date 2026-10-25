"""Build the made-up shop the examples describe: made-up names and numbers, the same on every run.

    uv run python examples/data/make_shop.py                     # → examples/data/shop.duckdb
    uv run python examples/data/make_shop.py /some/where.duckdb
    uv run python examples/data/make_shop.py --clickhouse http://localhost:8123
    uv run python examples/data/make_shop.py --postgres postgresql://postgres:…@localhost:5432/postgres
    uv run python examples/data/make_shop.py --mysql mysql://root:…@localhost:3306

Half a year of a small online shop: customers in a few countries, paid orders (prices rose on 2026-03-01, some orders
refunded), and the raw events behind them — the large table a query must bound by its date.

For ClickHouse, a throwaway server: it creates the databases `marts`, `raw` and `dictionaries` and replaces the shop's
tables in them, as the user in CLICKHOUSE_USER with the password in CLICKHOUSE_PASSWORD.

For PostgreSQL, a throwaway server too, as a user that may create schemas and roles: it replaces the schemas `marts`,
`raw` and `dictionaries`, and makes the role the example connects as — `shop_reader`, password `shop_reader`, which
may only read them (the server refuses a superuser).

For MySQL, a throwaway server too, as root: it replaces the databases `marts`, `raw` and `dictionaries`, and makes the
user the example connects as — `shop_reader`, password `shop_reader`, which may only read them.
"""

import datetime
import json
import os
import pathlib
import random
import sys

import duckdb
import httpx

COUNTRIES = [
    ("DE", "Germany", "EUR"),
    ("FR", "France", "EUR"),
    ("ES", "Spain", "EUR"),
    ("NL", "Netherlands", "EUR"),
    ("US", "United States", "USD"),
]
FIRST_DAY = datetime.date(2026, 1, 1)
DAYS = 181  # to 2026-06-30
PRICE_RISE = datetime.date(2026, 3, 1)

COMMENTS = {
    "dictionaries.countries": "Countries the shop sells to, with the currency each pays in.",
    "marts.customers": "One row per customer.",
    "marts.orders": "One row per paid order. Test orders are removed.",
    "marts.orders.amount": "What the customer paid, before refunds, in the order's currency.",
    "marts.orders.refunded_amount": "What was refunded; 0 when nothing was.",
    "raw.events": "Every event the shop's site sends, as sent. Large: bound event_date.",
}

# DuckDB and PostgreSQL both read it.
SCHEMA = """
CREATE SCHEMA marts;
CREATE SCHEMA raw;
CREATE SCHEMA dictionaries;
CREATE TABLE dictionaries.countries (country VARCHAR, name VARCHAR, currency VARCHAR);
CREATE TABLE marts.customers (customer_id VARCHAR, country VARCHAR, signed_up_on DATE);
CREATE TABLE marts.orders (
    order_id VARCHAR, order_date DATE, customer_id VARCHAR, currency VARCHAR,
    amount DECIMAL(12, 2), refunded_amount DECIMAL(12, 2)
);
CREATE TABLE raw.events (
    event_date DATE, event_time TIMESTAMP, event_name VARCHAR, customer_id VARCHAR, order_id VARCHAR
);
"""

CLICKHOUSE_SCHEMA = [
    "CREATE DATABASE IF NOT EXISTS marts",
    "CREATE DATABASE IF NOT EXISTS raw",
    "CREATE DATABASE IF NOT EXISTS dictionaries",
    """CREATE OR REPLACE TABLE dictionaries.countries (country String, name String, currency String)
       ENGINE = MergeTree ORDER BY country COMMENT '{dictionaries.countries}'""",
    """CREATE OR REPLACE TABLE marts.customers (customer_id String, country LowCardinality(String), signed_up_on Date)
       ENGINE = MergeTree ORDER BY customer_id COMMENT '{marts.customers}'""",
    """CREATE OR REPLACE TABLE marts.orders (
         order_id String, order_date Date, customer_id String, currency LowCardinality(String),
         amount Decimal(12, 2) COMMENT '{marts.orders.amount}',
         refunded_amount Decimal(12, 2) COMMENT '{marts.orders.refunded_amount}'
       ) ENGINE = MergeTree ORDER BY (order_date, order_id) COMMENT '{marts.orders}'""",
    """CREATE OR REPLACE TABLE raw.events (
         event_date Date, event_time DateTime, event_name LowCardinality(String), customer_id String,
         order_id Nullable(String)
       ) ENGINE = MergeTree PARTITION BY toYYYYMM(event_date) ORDER BY (event_date, event_time)
       COMMENT '{raw.events}'""",
]


def shop() -> dict[str, list[tuple]]:
    """The shop's rows, by table."""
    rng = random.Random(20260301)
    customers, orders, events = [], [], []
    for number in range(1, 2001):
        country = rng.choices(COUNTRIES, weights=[30, 20, 15, 10, 25])[0]
        signed_up = FIRST_DAY + datetime.timedelta(days=rng.randrange(DAYS))
        customer_id = f"c{number:05d}"
        customers.append((customer_id, country[0], signed_up))
        day = signed_up
        while True:
            day += datetime.timedelta(days=int(rng.expovariate(1 / 18)) + 1)
            if (day - FIRST_DAY).days >= DAYS:
                break
            at = datetime.datetime.combine(day, datetime.time(rng.randrange(24), rng.randrange(60)))
            events.append((day, at, "page_view", customer_id, None))
            if rng.random() < 0.35:
                events.append((day, at + datetime.timedelta(minutes=3), "cart_abandoned", customer_id, None))
                continue
            order_id = f"o{len(orders) + 1:06d}"
            price = rng.choice([9.90, 14.90, 19.90, 29.90, 49.90]) * rng.randint(1, 3)
            if day >= PRICE_RISE:
                price *= 1.1
            amount = round(price, 2)
            refunded = amount if rng.random() < 0.05 else 0
            orders.append((order_id, day, customer_id, country[2], amount, refunded))
            events.append((day, at + datetime.timedelta(minutes=5), "order_paid", customer_id, order_id))
    return {
        "dictionaries.countries": COUNTRIES,
        "marts.customers": customers,
        "marts.orders": orders,
        "raw.events": events,
    }


def build(path: pathlib.Path) -> None:
    tables = shop()
    path.unlink(missing_ok=True)
    with duckdb.connect(str(path)) as connection:
        connection.execute(SCHEMA)
        for name, comment in COMMENTS.items():
            kind = "COLUMN" if name.count(".") == 2 else "TABLE"
            connection.execute(f"COMMENT ON {kind} {name} IS '{comment.replace(chr(39), chr(39) * 2)}'")
        for table, rows in tables.items():
            columns = [list(column) for column in zip(*rows, strict=True)]
            unnested = ", ".join(f"unnest(${n})" for n in range(1, len(columns) + 1))
            connection.execute(f"INSERT INTO {table} SELECT {unnested}", columns)
    print(f"{path}: " + ", ".join(f"{table} {len(rows)}" for table, rows in tables.items()))


def build_clickhouse(url: str) -> None:
    auth = (os.environ.get("CLICKHOUSE_USER", "default"), os.environ.get("CLICKHOUSE_PASSWORD", ""))

    def post(sql: str, body: str = "") -> None:
        response = httpx.post(url, params={"query": sql}, content=body, auth=auth, timeout=120)
        response.raise_for_status()

    escaped = {name: comment.replace("'", "\\'") for name, comment in COMMENTS.items()}
    for statement in CLICKHOUSE_SCHEMA:
        for name, comment in escaped.items():
            statement = statement.replace("{" + name + "}", comment)
        post(statement)
    tables = shop()
    for table, rows in tables.items():
        lines = "\n".join(json.dumps(row, default=_iso) for row in rows)
        post(f"INSERT INTO {table} FORMAT JSONCompactEachRow", lines)
    print(f"{url}: " + ", ".join(f"{table} {len(rows)}" for table, rows in tables.items()))


def build_postgres(url: str) -> None:
    import psycopg

    tables = shop()
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS marts, raw, dictionaries CASCADE")
        connection.execute(SCHEMA)
        for name, comment in COMMENTS.items():
            kind = "COLUMN" if name.count(".") == 2 else "TABLE"
            connection.execute(f"COMMENT ON {kind} {name} IS '{comment.replace(chr(39), chr(39) * 2)}'")
        for table, rows in tables.items():
            with connection.cursor().copy(f"COPY {table} FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname = 'shop_reader'").fetchone():
            connection.execute("CREATE ROLE shop_reader LOGIN PASSWORD 'shop_reader'")
        connection.execute("GRANT USAGE ON SCHEMA marts, raw, dictionaries TO shop_reader")
        connection.execute("GRANT SELECT ON ALL TABLES IN SCHEMA marts, raw, dictionaries TO shop_reader")
    print("PostgreSQL: " + ", ".join(f"{table} {len(rows)}" for table, rows in tables.items()))


MYSQL_SCHEMA = [
    "CREATE TABLE dictionaries.countries (country VARCHAR(2), name VARCHAR(40), currency VARCHAR(3)) "
    "COMMENT '{dictionaries.countries}'",
    "CREATE TABLE marts.customers (customer_id VARCHAR(10), country VARCHAR(2), signed_up_on DATE) "
    "COMMENT '{marts.customers}'",
    """CREATE TABLE marts.orders (
         order_id VARCHAR(10), order_date DATE, customer_id VARCHAR(10), currency VARCHAR(3),
         amount DECIMAL(12, 2) COMMENT '{marts.orders.amount}',
         refunded_amount DECIMAL(12, 2) COMMENT '{marts.orders.refunded_amount}'
       ) COMMENT '{marts.orders}'""",
    """CREATE TABLE raw.events (
         event_date DATE, event_time DATETIME, event_name VARCHAR(20), customer_id VARCHAR(10), order_id VARCHAR(10)
       ) COMMENT '{raw.events}'""",
]


def build_mysql(url: str) -> None:
    import urllib.parse

    import pymysql

    parsed = urllib.parse.urlsplit(url)
    connection = pymysql.connect(
        host=parsed.hostname, port=parsed.port or 3306, user=urllib.parse.unquote(parsed.username or "root"),
        password=urllib.parse.unquote(parsed.password or ""), autocommit=True,
    )  # fmt: skip
    tables = shop()
    escaped = {name: comment.replace("'", "''") for name, comment in COMMENTS.items()}
    with connection.cursor() as cursor:
        for database in ("marts", "raw", "dictionaries"):
            cursor.execute(f"DROP DATABASE IF EXISTS {database}")
            cursor.execute(f"CREATE DATABASE {database}")
        for statement in MYSQL_SCHEMA:
            for name, comment in escaped.items():
                statement = statement.replace("{" + name + "}", comment)
            cursor.execute(statement)
        for table, rows in tables.items():
            marks = ", ".join(["%s"] * len(rows[0]))
            cursor.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
        cursor.execute("CREATE USER IF NOT EXISTS 'shop_reader'@'%' IDENTIFIED BY 'shop_reader'")
        for database in ("marts", "raw", "dictionaries"):
            cursor.execute(f"GRANT SELECT ON {database}.* TO 'shop_reader'@'%'")
    connection.close()
    print("MySQL: " + ", ".join(f"{table} {len(rows)}" for table, rows in tables.items()))


def _iso(value: datetime.date) -> str:
    return value.isoformat(sep=" ") if isinstance(value, datetime.datetime) else value.isoformat()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--clickhouse":
        build_clickhouse(sys.argv[2])
    elif len(sys.argv) > 2 and sys.argv[1] == "--postgres":
        build_postgres(sys.argv[2])
    elif len(sys.argv) > 2 and sys.argv[1] == "--mysql":
        build_mysql(sys.argv[2])
    else:
        build(
            pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent / "shop.duckdb"
        )
