"""A real PostgreSQL for the tests: the server named by SWM_TEST_POSTGRES_URL (a throwaway one — CI's service, its
user a superuser), or a container started for the session when Docker is there. Its schemas are prefixed `swm_` and
rebuilt on every run, and so are its roles."""

import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import uuid
from dataclasses import dataclass

import psycopg
import pytest

IMAGE = "postgres:17-alpine"

SHOP = [
    "DROP SCHEMA IF EXISTS swm_marts, swm_raw, swm_secret, swm_owned CASCADE",
    *[
        f"""DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            EXECUTE 'DROP OWNED BY {role}'; EXECUTE 'DROP ROLE {role}'; END IF; END $$"""
        for role in ("swm_writer", "swm_reader", "swm_files", "swm_owner", "swm_reads_all", "swm_bypass")
    ],
    "CREATE SCHEMA swm_marts",
    "CREATE SCHEMA swm_raw",
    "CREATE SCHEMA swm_secret",
    """CREATE TABLE swm_marts.orders (
        order_id text, order_date date, customer_id text, currency text, amount numeric(12, 2),
        refunded_amount numeric(12, 2), big numeric(20, 0), note text, attrs jsonb
    )""",
    "COMMENT ON TABLE swm_marts.orders IS 'One row per paid order.'",
    "COMMENT ON COLUMN swm_marts.orders.amount IS 'Paid, before refunds.'",
    """INSERT INTO swm_marts.orders VALUES
        ('o1', '2026-03-01', 'c1', 'EUR', 10.10, 0, 18446744073709551615, NULL, '{"gift": true}'),
        ('o2', '2026-03-01', 'c2', 'EUR', 20.20, 5.05, 1, 'x', NULL),
        ('o3', '2026-03-02', 'c1', 'USD', 30.30, 0, 2, NULL, NULL),
        ('o4', '2026-03-03', 'c3', 'EUR', 0.10, 0, 3, NULL, NULL),
        ('o5', '2026-03-03', 'c3', 'EUR', 0.20, 0, 4, NULL, NULL),
        ('o6', '2026-03-04', 'c2', 'USD', 99.99, 99.99, 5, '50% off {sale}', NULL)""",
    "CREATE VIEW swm_marts.daily AS SELECT order_date, count(*) AS orders FROM swm_marts.orders GROUP BY order_date",
    "CREATE SEQUENCE swm_marts.ids",
    """CREATE TABLE swm_raw.events (event_date date, event_name text, customer_id text)
        PARTITION BY RANGE (event_date)""",
    "CREATE TABLE swm_raw.events_2026_02 PARTITION OF swm_raw.events FOR VALUES FROM ('2026-02-01') TO ('2026-03-01')",
    "CREATE TABLE swm_raw.events_2026_03 PARTITION OF swm_raw.events FOR VALUES FROM ('2026-03-01') TO ('2026-04-01')",
    """INSERT INTO swm_raw.events VALUES
        ('2026-02-27', 'cart_abandoned', 'c1'), ('2026-03-01', 'cart_abandoned', 'c2'),
        ('2026-03-02', 'cart_abandoned', 'c2'), ('2026-03-02', 'order_paid', 'c1')""",
    "CREATE TABLE swm_secret.salaries (name text, amount integer)",
    "INSERT INTO swm_secret.salaries VALUES ('someone', 1)",
    # A readable view over a table that is not: agents may read the total, never the rows — a view nested on it too.
    "CREATE VIEW swm_marts.payroll_total AS SELECT sum(amount) AS total FROM swm_secret.salaries",
    "CREATE VIEW swm_marts.payroll_nested AS SELECT total * 2 AS doubled FROM swm_marts.payroll_total",
    # A function that writes: a query calling it passes the guard, and the read-only transaction stops the write.
    """CREATE FUNCTION swm_marts.write_one() RETURNS integer LANGUAGE plpgsql AS
        $$ BEGIN INSERT INTO swm_marts.orders (order_id) VALUES ('written'); RETURN 1; END $$""",
    # A user who may write — to show the server cannot, whoever it connects as.
    "CREATE ROLE swm_writer LOGIN PASSWORD 'swm_writer'",
    "GRANT USAGE, CREATE ON SCHEMA swm_marts, swm_raw, swm_secret TO swm_writer",
    "GRANT ALL ON ALL TABLES IN SCHEMA swm_marts, swm_raw, swm_secret TO swm_writer",
    "GRANT ALL ON ALL SEQUENCES IN SCHEMA swm_marts TO swm_writer",
    "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA swm_marts TO swm_writer",
    # A user who only reads what agents may: the usual read-only role.
    "CREATE ROLE swm_reader LOGIN PASSWORD 'swm_reader'",
    "GRANT USAGE ON SCHEMA swm_marts, swm_raw TO swm_reader",
    "GRANT SELECT ON ALL TABLES IN SCHEMA swm_marts, swm_raw TO swm_reader",
    # A user who may read the server's files: refused at start.
    "CREATE ROLE swm_files LOGIN PASSWORD 'swm_files' IN ROLE pg_read_server_files",
    # A user who owns a table and its index: refused at start — an owner may maintain an index past a read-only
    # transaction.
    "CREATE ROLE swm_owner LOGIN PASSWORD 'swm_owner'",
    "CREATE SCHEMA swm_owned AUTHORIZATION swm_owner",
    "CREATE TABLE swm_owned.notes (id integer)",
    "CREATE INDEX notes_brin ON swm_owned.notes USING brin (id)",
    "ALTER TABLE swm_owned.notes OWNER TO swm_owner",
    # Users who read past the database's grants: served, with a note.
    "CREATE ROLE swm_reads_all LOGIN PASSWORD 'swm_reads_all' IN ROLE pg_read_all_data",
    "CREATE ROLE swm_bypass LOGIN PASSWORD 'swm_bypass' BYPASSRLS",
    "GRANT USAGE ON SCHEMA swm_marts TO swm_bypass",
    "GRANT SELECT ON ALL TABLES IN SCHEMA swm_marts TO swm_bypass",
]


@dataclass(frozen=True)
class Postgres:
    host: str
    port: int
    database: str
    admin: tuple[str, str]

    def run(self, sql: str, *params) -> list[tuple]:
        """A statement as the superuser — only to build the shop, or to look at what the server did."""
        with psycopg.connect(
            host=self.host, port=self.port, dbname=self.database, user=self.admin[0], password=self.admin[1],
            autocommit=True,
        ) as connection:  # fmt: skip
            cursor = connection.execute(sql, params or None)
            return cursor.fetchall() if cursor.description else []


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait(server: Postgres) -> None:
    for _ in range(120):
        try:
            server.run("SELECT 1")
            return
        except psycopg.OperationalError:
            time.sleep(0.5)
    raise RuntimeError(f"PostgreSQL at {server.host}:{server.port} did not start")


@pytest.fixture(scope="session")
def postgres():
    url = os.environ.get("SWM_TEST_POSTGRES_URL")
    container = None
    if url:
        parsed = urllib.parse.urlsplit(url)
        server = Postgres(
            parsed.hostname or "127.0.0.1", parsed.port or 5432, parsed.path.lstrip("/") or "postgres",
            (urllib.parse.unquote(parsed.username or "postgres"), urllib.parse.unquote(parsed.password or "")),
        )  # fmt: skip
    else:
        if shutil.which("docker") is None:
            pytest.skip("no PostgreSQL: set SWM_TEST_POSTGRES_URL or install Docker")
        port = _free_port()
        container = f"swm-test-postgres-{uuid.uuid4().hex[:8]}"
        started = subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", container, "-p", f"127.0.0.1:{port}:5432",
             "-e", "POSTGRES_PASSWORD=admin", IMAGE],
            capture_output=True, text=True,
        )  # fmt: skip
        if started.returncode != 0:
            pytest.skip(f"no PostgreSQL: docker run failed: {started.stderr.strip()[:200]}")
        server = Postgres("127.0.0.1", port, "postgres", ("postgres", "admin"))
    try:
        _wait(server)
        for statement in SHOP:
            server.run(statement)
        yield server
    finally:
        if container:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
