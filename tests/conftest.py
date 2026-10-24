"""A made-up shop in DuckDB, small enough that every number in a test is known by hand."""

import pathlib
import textwrap

import duckdb
import pytest

from semantic_warehouse_mcp.config import load

SHOP_SQL = """
CREATE SCHEMA marts;
CREATE SCHEMA raw;
CREATE SCHEMA secret;

CREATE TABLE marts.orders (
    order_id VARCHAR, order_date DATE, customer_id VARCHAR, currency VARCHAR,
    amount DECIMAL(12, 2), refunded_amount DECIMAL(12, 2)
);
INSERT INTO marts.orders VALUES
    ('o1', DATE '2026-03-01', 'c1', 'EUR', 10.10, 0),
    ('o2', DATE '2026-03-01', 'c2', 'EUR', 20.20, 5.05),
    ('o3', DATE '2026-03-02', 'c1', 'USD', 30.30, 0),
    ('o4', DATE '2026-03-03', 'c3', 'EUR', 0.10, 0),
    ('o5', DATE '2026-03-03', 'c3', 'EUR', 0.20, 0),
    ('o6', DATE '2026-03-04', 'c2', 'USD', 99.99, 99.99);
COMMENT ON TABLE marts.orders IS 'One row per paid order.';
COMMENT ON COLUMN marts.orders.amount IS 'Paid, before refunds.';

CREATE TABLE marts.customers (customer_id VARCHAR, country VARCHAR);
INSERT INTO marts.customers VALUES ('c1', 'DE'), ('c2', 'FR'), ('c3', 'DE');

CREATE VIEW marts.daily AS SELECT order_date, count(*) AS orders FROM marts.orders GROUP BY order_date;

CREATE TABLE raw.events (event_date DATE, event_name VARCHAR, customer_id VARCHAR);
INSERT INTO raw.events VALUES
    (DATE '2026-02-27', 'cart_abandoned', 'c1'),
    (DATE '2026-03-01', 'cart_abandoned', 'c2'),
    (DATE '2026-03-02', 'cart_abandoned', 'c2'),
    (DATE '2026-03-02', 'order_paid', 'c1');

CREATE TABLE raw.logs (line VARCHAR);
CREATE TABLE secret.salaries (name VARCHAR, amount INTEGER);
INSERT INTO secret.salaries VALUES ('someone', 1);
"""


def make_shop(path: pathlib.Path) -> pathlib.Path:
    with duckdb.connect(str(path)) as connection:
        connection.execute(SHOP_SQL)
    return path


def write_config(folder: pathlib.Path, body: str) -> pathlib.Path:
    path = folder / "config.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


SHOP_CONFIG = """
connections:
  - name: shop
    engine: duckdb
    description: A made-up shop.
    path: shop.duckdb
    readable: ["marts.*", "raw.events"]
    large_tables:
      - {{ table: raw.events, bound_by: event_date }}
    limits: {{ rows: {rows}, seconds: {seconds} }}
limits:
  answer_characters: {answer_characters}
"""


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def shop_path(tmp_path):
    return make_shop(tmp_path / "shop.duckdb")


@pytest.fixture
def shop_config(shop_path):
    """The shop's configuration; call it with other limits to change them."""

    def build(rows=5, seconds=10, answer_characters=40_000):
        body = SHOP_CONFIG.format(rows=rows, seconds=seconds, answer_characters=answer_characters)
        return load(write_config(shop_path.parent, body))

    return build
