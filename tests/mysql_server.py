"""A real MySQL for the tests: the server named by SWM_TEST_MYSQL_URL (a throwaway one — CI's service, its user root),
or a container started for the session when Docker is there. Its databases are prefixed `swm_` and rebuilt on every
run, and so are its users."""

import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import uuid
from dataclasses import dataclass

import pymysql
import pytest

IMAGE = "mysql:8.4"

SHOP = [
    "DROP DATABASE IF EXISTS swm_marts",
    "DROP DATABASE IF EXISTS swm_raw",
    "DROP DATABASE IF EXISTS swm_secret",
    *[f"DROP USER IF EXISTS '{user}'@'%'" for user in ("swm_writer", "swm_reader", "swm_files")],
    "CREATE DATABASE swm_marts",
    "CREATE DATABASE swm_raw",
    "CREATE DATABASE swm_secret",
    """CREATE TABLE swm_marts.orders (
        order_id VARCHAR(10) PRIMARY KEY, order_date DATE, customer_id VARCHAR(10), currency VARCHAR(3),
        amount DECIMAL(12, 2) COMMENT 'Paid, before refunds.', refunded_amount DECIMAL(12, 2),
        big BIGINT UNSIGNED, note VARCHAR(40), attrs JSON
    ) COMMENT 'One row per paid order.'""",
    """INSERT INTO swm_marts.orders VALUES
        ('o1', '2026-03-01', 'c1', 'EUR', 10.10, 0, 18446744073709551615, NULL, '{"gift": true}'),
        ('o2', '2026-03-01', 'c2', 'EUR', 20.20, 5.05, 1, 'x', NULL),
        ('o3', '2026-03-02', 'c1', 'USD', 30.30, 0, 2, NULL, NULL),
        ('o4', '2026-03-03', 'c3', 'EUR', 0.10, 0, 3, NULL, NULL),
        ('o5', '2026-03-03', 'c3', 'EUR', 0.20, 0, 4, NULL, NULL),
        ('o6', '2026-03-04', 'c2', 'USD', 99.99, 99.99, 5, '50% off {sale}', NULL)""",
    "CREATE VIEW swm_marts.daily AS SELECT order_date, COUNT(*) AS orders FROM swm_marts.orders GROUP BY order_date",
    "CREATE TABLE swm_marts.digits (d INT)",
    "INSERT INTO swm_marts.digits VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9)",
    "CREATE TABLE swm_raw.events (event_date DATE, event_name VARCHAR(20), customer_id VARCHAR(10))",
    """INSERT INTO swm_raw.events VALUES
        ('2026-02-27', 'cart_abandoned', 'c1'), ('2026-03-01', 'cart_abandoned', 'c2'),
        ('2026-03-02', 'cart_abandoned', 'c2'), ('2026-03-02', 'order_paid', 'c1')""",
    "CREATE TABLE swm_secret.salaries (name VARCHAR(20), amount INT)",
    "INSERT INTO swm_secret.salaries VALUES ('someone', 1), ('another', 2)",
    # Read by its key, MySQL reads a row of this table while planning: a plan does not name it.
    "CREATE TABLE swm_secret.people (id INT PRIMARY KEY, ssn VARCHAR(20))",
    "INSERT INTO swm_secret.people VALUES (1, '111-SECRET'), (2, '222-SECRET')",
    # A readable view over a table that is not: agents may read the total, never the rows — a view nested on it too.
    "CREATE VIEW swm_marts.payroll_total AS SELECT SUM(amount) AS total FROM swm_secret.salaries",
    "CREATE VIEW swm_marts.payroll_nested AS SELECT total * 2 AS doubled FROM swm_marts.payroll_total",
    # A view MySQL reads the secret table through as it runs (two rows: not read while planning), so a plan names it.
    "CREATE VIEW swm_marts.payroll_names AS SELECT name FROM swm_secret.salaries",
    # A function that writes: a query calling it passes the guard, and the read-only transaction stops the write.
    "SET GLOBAL log_bin_trust_function_creators = 1",
    """CREATE FUNCTION swm_marts.write_one() RETURNS INT MODIFIES SQL DATA
        BEGIN INSERT INTO swm_marts.orders (order_id) VALUES ('written'); RETURN 1; END""",
    # A user who may write — to show the server cannot, whoever it connects as.
    "CREATE USER 'swm_writer'@'%' IDENTIFIED BY 'swm_writer'",
    "GRANT ALL ON swm_marts.* TO 'swm_writer'@'%'",
    "GRANT ALL ON swm_raw.* TO 'swm_writer'@'%'",
    "GRANT ALL ON swm_secret.* TO 'swm_writer'@'%'",
    # A user who only reads what agents may.
    "CREATE USER 'swm_reader'@'%' IDENTIFIED BY 'swm_reader'",
    "GRANT SELECT ON swm_marts.* TO 'swm_reader'@'%'",
    "GRANT SELECT ON swm_raw.* TO 'swm_reader'@'%'",
    # A user who may read and write the server's files: refused at start.
    "CREATE USER 'swm_files'@'%' IDENTIFIED BY 'swm_files'",
    "GRANT FILE ON *.* TO 'swm_files'@'%'",
    "GRANT SELECT ON swm_marts.* TO 'swm_files'@'%'",
]


@dataclass(frozen=True)
class MySQL:
    host: str
    port: int
    admin: tuple[str, str]

    def run(self, sql: str, *params) -> list[tuple]:
        """A statement as root — only to build the shop, or to look at what the server did."""
        connection = pymysql.connect(host=self.host, port=self.port, user=self.admin[0], password=self.admin[1],
                                     autocommit=True)  # fmt: skip
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params or None)
                return list(cursor.fetchall()) if cursor.description else []
        finally:
            connection.close()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait(server: MySQL) -> None:
    for _ in range(180):
        try:
            server.run("SELECT 1")
            return
        except Exception:  # noqa: BLE001 — while it initialises, a connection fails in more ways than pymysql's own
            time.sleep(0.5)
    raise RuntimeError(f"MySQL at {server.host}:{server.port} did not start")


@pytest.fixture(scope="session")
def mysql():
    url = os.environ.get("SWM_TEST_MYSQL_URL")
    container = None
    if url:
        parsed = urllib.parse.urlsplit(url)
        admin = (urllib.parse.unquote(parsed.username or "root"), urllib.parse.unquote(parsed.password or ""))
        server = MySQL(parsed.hostname or "127.0.0.1", parsed.port or 3306, admin)
    else:
        if shutil.which("docker") is None:
            pytest.skip("no MySQL: set SWM_TEST_MYSQL_URL or install Docker")
        port = _free_port()
        container = f"swm-test-mysql-{uuid.uuid4().hex[:8]}"
        started = subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", container, "-p", f"127.0.0.1:{port}:3306",
             "-e", "MYSQL_ROOT_PASSWORD=admin", IMAGE],
            capture_output=True, text=True,
        )  # fmt: skip
        if started.returncode != 0:
            pytest.skip(f"no MySQL: docker run failed: {started.stderr.strip()[:200]}")
        server = MySQL("127.0.0.1", port, ("root", "admin"))
    try:
        _wait(server)
        for statement in SHOP:
            server.run(statement)
        yield server
    finally:
        if container:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
