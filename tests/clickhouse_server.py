"""A real ClickHouse for the tests: the server named by SWM_TEST_CLICKHOUSE_URL (a throwaway one — CI's service), or a
container started for the session when Docker is there. Its databases are prefixed `swm_` and rebuilt on every run."""

import os
import shutil
import socket
import subprocess
import time
import uuid

import httpx
import pytest

IMAGE = "clickhouse/clickhouse-server:25.8"
ADMIN = (os.environ.get("SWM_TEST_CLICKHOUSE_USER", "admin"), os.environ.get("SWM_TEST_CLICKHOUSE_PASSWORD", "admin"))

SHOP = [
    "DROP DATABASE IF EXISTS swm_marts",
    "DROP DATABASE IF EXISTS swm_raw",
    "DROP DATABASE IF EXISTS swm_secret",
    "DROP DATABASE IF EXISTS swm_dictionaries",
    "CREATE DATABASE swm_marts",
    "CREATE DATABASE swm_raw",
    "CREATE DATABASE swm_secret",
    "CREATE DATABASE swm_dictionaries",
    """CREATE TABLE swm_marts.orders (
        order_id String COMMENT 'The order.', order_date Date, customer_id String, currency LowCardinality(String),
        amount Decimal(12, 2) COMMENT 'Paid, before refunds.', refunded_amount Decimal(12, 2),
        big UInt64, note Nullable(String)
    ) ENGINE = MergeTree ORDER BY order_id COMMENT 'One row per paid order.'""",
    """INSERT INTO swm_marts.orders VALUES
        ('o1', '2026-03-01', 'c1', 'EUR', 10.10, 0, 18446744073709551615, NULL),
        ('o2', '2026-03-01', 'c2', 'EUR', 20.20, 5.05, 1, 'x'),
        ('o3', '2026-03-02', 'c1', 'USD', 30.30, 0, 2, NULL),
        ('o4', '2026-03-03', 'c3', 'EUR', 0.10, 0, 3, NULL),
        ('o5', '2026-03-03', 'c3', 'EUR', 0.20, 0, 4, NULL),
        ('o6', '2026-03-04', 'c2', 'USD', 99.99, 99.99, 5, NULL)""",
    "CREATE VIEW swm_marts.daily AS SELECT order_date, count() AS orders FROM swm_marts.orders GROUP BY order_date",
    """CREATE TABLE swm_raw.events (event_date Date, event_name String, customer_id String)
        ENGINE = MergeTree PARTITION BY toYYYYMM(event_date) ORDER BY event_date""",
    """INSERT INTO swm_raw.events VALUES
        ('2026-02-27', 'cart_abandoned', 'c1'), ('2026-03-01', 'cart_abandoned', 'c2'),
        ('2026-03-02', 'cart_abandoned', 'c2'), ('2026-03-02', 'order_paid', 'c1')""",
    "CREATE TABLE swm_secret.salaries (name String, amount UInt32) ENGINE = MergeTree ORDER BY name",
    "INSERT INTO swm_secret.salaries VALUES ('someone', 1)",
    "CREATE TABLE swm_secret.lookup (id UInt64, v String) ENGINE = MergeTree ORDER BY id",
    "INSERT INTO swm_secret.lookup VALUES (1, 'SECRET')",
    """CREATE DICTIONARY swm_secret.lookup_dict (id UInt64, v String) PRIMARY KEY id
        SOURCE(CLICKHOUSE(TABLE 'lookup' DB 'swm_secret' USER '{user}' PASSWORD '{password}'))
        LAYOUT(FLAT()) LIFETIME(0)""",
    "CREATE TABLE swm_dictionaries.country_src (id UInt64, name String) ENGINE = MergeTree ORDER BY id",
    "INSERT INTO swm_dictionaries.country_src VALUES (1, 'Germany'), (2, 'France')",
    """CREATE DICTIONARY swm_dictionaries.countries (id UInt64, name String) PRIMARY KEY id
        SOURCE(CLICKHOUSE(TABLE 'country_src' DB 'swm_dictionaries' USER '{user}' PASSWORD '{password}'))
        LAYOUT(FLAT()) LIFETIME(0)""",
    # A user who may write — to show the server cannot, whoever it connects as.
    "DROP USER IF EXISTS swm_writer",
    "CREATE USER swm_writer IDENTIFIED BY 'swm_writer'",
    "GRANT ALL ON swm_marts.* TO swm_writer",
    "GRANT ALL ON swm_raw.* TO swm_writer",
    "GRANT ALL ON swm_secret.* TO swm_writer",
    "GRANT ALL ON swm_dictionaries.* TO swm_writer",
    "GRANT CREATE DATABASE ON *.* TO swm_writer",
    # A user whose profile would cut reading silently — the server's own overflow modes must win.
    "DROP USER IF EXISTS swm_cutting",
    "CREATE USER swm_cutting IDENTIFIED BY 'swm_cutting' SETTINGS max_rows_to_read = 2, read_overflow_mode = 'break'",
    "GRANT SELECT ON swm_marts.* TO swm_cutting",
    # A user whose profile caps an answer's bytes — it must not cut below the row that shows an answer was cut.
    "DROP USER IF EXISTS swm_small_answers",
    "CREATE USER swm_small_answers IDENTIFIED BY 'swm_small_answers' SETTINGS max_result_bytes = 1, max_block_size = 2",
    "GRANT SELECT ON swm_marts.* TO swm_small_answers",
    # Read-only users the usual ways: `readonly = 1` may change no setting, so it cannot take the server's limits;
    # `readonly = 2` may, and still writes nothing.
    "DROP USER IF EXISTS swm_readonly_one",
    "CREATE USER swm_readonly_one IDENTIFIED BY 'swm_readonly_one' SETTINGS readonly = 1",
    "GRANT SELECT ON swm_marts.* TO swm_readonly_one",
    "DROP USER IF EXISTS swm_readonly_two",
    "CREATE USER swm_readonly_two IDENTIFIED BY 'swm_readonly_two' SETTINGS readonly = 2",
    "GRANT SELECT ON swm_marts.* TO swm_readonly_two",
]


def admin(url: str, sql: str) -> str:
    response = httpx.post(url, content=sql, auth=ADMIN, timeout=60)
    assert response.status_code == 200, response.text
    return response.text


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait(url: str) -> None:
    for _ in range(120):
        try:
            if httpx.get(url + "ping", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"ClickHouse at {url} did not start")


@pytest.fixture(scope="session")
def clickhouse_url():
    url = os.environ.get("SWM_TEST_CLICKHOUSE_URL")
    container = None
    if not url:
        if shutil.which("docker") is None:
            pytest.skip("no ClickHouse: set SWM_TEST_CLICKHOUSE_URL or install Docker")
        port = _free_port()
        container = f"swm-test-clickhouse-{uuid.uuid4().hex[:8]}"
        started = subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", container, "-p", f"127.0.0.1:{port}:8123",
             "-e", f"CLICKHOUSE_USER={ADMIN[0]}", "-e", f"CLICKHOUSE_PASSWORD={ADMIN[1]}",
             "-e", "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1", IMAGE],
            capture_output=True, text=True,
        )  # fmt: skip
        if started.returncode != 0:
            pytest.skip(f"no ClickHouse: docker run failed: {started.stderr.strip()[:200]}")
        url = f"http://127.0.0.1:{port}"
    url = url.rstrip("/") + "/"
    try:
        _wait(url)
        for statement in SHOP:
            admin(url, statement.format(user=ADMIN[0], password=ADMIN[1]))
        yield url
    finally:
        if container:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
