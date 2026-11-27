"""`export_query`: all the rows go to a file, none of them through the agent."""

import csv
import io
import json
import pathlib

import pytest
from conftest import SHOP_CONFIG, make_shop, write_config
from mcp import Client

from semantic_warehouse_mcp import exports
from semantic_warehouse_mcp.adapters import CALLER
from semantic_warehouse_mcp.config import ConfigError, load
from semantic_warehouse_mcp.server import build_server

pytestmark = pytest.mark.anyio

DIRECTORY = """
exports:
  storage: {{ type: directory, path: out, url_prefix: "https://files.example.com/" }}
  limits: {{ rows: {export_rows}, megabytes: 1 }}
  link_minutes: 30
"""


@pytest.fixture
def export_config(shop_path):
    def build(rows=2, export_rows=100):
        body = SHOP_CONFIG.format(rows=rows, seconds=10, answer_characters=40_000) + DIRECTORY.format(
            export_rows=export_rows
        )
        return load(write_config(shop_path.parent, body))

    return build


async def call(config, tool, **arguments):
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    return (text, True) if result.is_error else (json.loads(text), False)


def written(config, answer) -> list[list[str]]:
    key = answer["url"].removeprefix("https://files.example.com/")
    root = pathlib.Path(config.export_storage.root)
    return list(csv.reader(io.StringIO((root / key).read_text(encoding="utf-8-sig"))))


async def test_the_tool_is_offered_only_where_exports_are_configured(shop_config, export_config):
    async with Client(build_server(shop_config())) as client:
        assert "export_query" not in {tool.name for tool in (await client.list_tools()).tools}
    async with Client(build_server(export_config())) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    annotations = tools["export_query"].annotations
    assert annotations.read_only_hint is False  # it writes a file, and says so
    assert annotations.destructive_hint is False


async def test_every_row_is_in_the_file_and_none_in_the_answer(export_config):
    config = export_config(rows=2)  # run_query would cut at 2 rows
    sql = "SELECT order_id, amount FROM marts.orders ORDER BY order_id"
    ran, _ = await call(config, "run_query", sql=sql)
    assert ran["truncated"] is True
    assert "export_query" in ran["note"]
    answer, is_error = await call(config, "export_query", sql=sql)
    assert not is_error
    assert answer["row_count"] == 6
    assert len(answer["preview"]) == exports.PREVIEW_ROWS  # a few rows to check by, not the data
    assert "rows" not in answer
    table = written(config, answer)
    assert table[0] == ["order_id", "amount"]
    assert [row[0] for row in table[1:]] == ["o1", "o2", "o3", "o4", "o5", "o6"]
    assert (
        answer["file"]["bytes"]
        == (pathlib.Path(config.export_storage.root) / answer["url"].split("/", 3)[3]).stat().st_size
    )
    assert answer["expires_at"] > answer["started_at"]


async def test_the_guard_holds_for_an_export(export_config):
    config = export_config()
    for sql in ("SELECT * FROM secret.salaries", "SELECT * FROM raw.events"):  # not readable / a large table unbounded
        text, is_error = await call(config, "export_query", sql=sql)
        assert is_error and "Refused by the guard" in text
    assert not pathlib.Path(config.export_storage.root).exists()  # nothing written for a refused query


async def test_more_rows_than_the_limit_is_refused_not_cut(export_config):
    config = export_config(export_rows=3)
    text, is_error = await call(config, "export_query", sql="SELECT order_id FROM marts.orders")
    assert is_error and "more than 3 rows" in text
    assert not pathlib.Path(config.export_storage.root).exists()  # a partial file would pass for the whole


async def test_a_formula_in_a_text_is_quoted_and_numbers_are_not(export_config):
    config = export_config()
    sql = (
        "SELECT '=1+1' AS a, '@x' AS b, '-5' AS c, 'plain' AS d, -3 AS e, NULL AS f, "
        "'+7' AS g, '-abc' AS h FROM marts.customers LIMIT 1"
    )
    answer, is_error = await call(config, "export_query", sql=sql)
    assert not is_error
    assert written(config, answer)[1] == ["'=1+1", "'@x", "-5", "plain", "-3", "", "+7", "'-abc"]


async def test_the_file_is_kept_apart_by_day_and_by_caller(export_config):
    config = export_config()
    token = CALLER.set("ann@example.com")
    try:
        answer, _ = await call(config, "export_query", sql="SELECT customer_id FROM marts.customers")
    finally:
        CALLER.reset(token)
    day, who, name = answer["url"].removeprefix("https://files.example.com/").split("/")
    assert who == "ann_example.com" and name.endswith(".csv") and len(name) == 36  # a name nobody can guess
    assert (
        exports.key_of("exports/", "a b/../c", __import__("datetime").datetime(2026, 1, 2)).split("/")[2] == "a_b_.._c"
    )


def test_the_size_limit_refuses_and_says_how_to_narrow(tmp_path):
    path = tmp_path / "x.csv"
    rows = [("x" * 1000,)] * 5000
    with pytest.raises(exports.ExportError, match="1 MB"):
        exports.write_csv(path, ["t"], rows, 2**20)


def test_a_configuration_with_an_unknown_storage_is_refused(shop_path):
    body = SHOP_CONFIG.format(rows=5, seconds=10, answer_characters=40_000) + "exports:\n  storage: { type: ftp }\n"
    with pytest.raises(ConfigError, match="exports"):
        load(write_config(shop_path.parent, body))


@pytest.fixture
def s3(monkeypatch):
    moto = pytest.importorskip("moto")
    boto3 = pytest.importorskip("boto3")
    monkeypatch.setenv("TEST_EXPORT_KEY", "test")
    monkeypatch.setenv("TEST_EXPORT_SECRET", "test")
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
        client.create_bucket(Bucket="acme-exports")
        yield client


async def test_an_export_to_s3_stores_the_file_and_links_it_signed(s3, shop_path):
    body = SHOP_CONFIG.format(rows=2, seconds=10, answer_characters=40_000) + (
        "exports:\n  storage:\n    type: s3\n    bucket: acme-exports\n    region: us-east-1\n"
        "    access_key_env: TEST_EXPORT_KEY\n    secret_key_env: TEST_EXPORT_SECRET\n  link_minutes: 15\n"
    )
    config = load(write_config(shop_path.parent, body))
    answer, is_error = await call(config, "export_query", sql="SELECT order_id FROM marts.orders ORDER BY order_id")
    assert not is_error
    assert (
        "acme-exports" in answer["url"] and "X-Amz-Signature" in answer["url"] and "X-Amz-Expires=900" in answer["url"]
    )
    assert "attachment" in answer["url"] or "response-content-disposition" in answer["url"]
    keys = [item["Key"] for item in s3.list_objects_v2(Bucket="acme-exports")["Contents"]]
    assert len(keys) == 1 and keys[0].startswith("exports/") and keys[0].endswith(".csv")
    stored = s3.get_object(Bucket="acme-exports", Key=keys[0])["Body"].read().decode("utf-8-sig")
    assert stored.splitlines() == ["order_id", "o1", "o2", "o3", "o4", "o5", "o6"]


def test_s3_keys_are_never_in_an_error(monkeypatch, shop_path):
    monkeypatch.delenv("TEST_EXPORT_KEY", raising=False)
    body = SHOP_CONFIG.format(rows=2, seconds=10, answer_characters=40_000) + (
        "exports:\n  storage: { type: s3, bucket: b, access_key_env: TEST_EXPORT_KEY }\n"
    )
    with pytest.raises(ConfigError, match="TEST_EXPORT_KEY"):
        load(write_config(shop_path.parent, body))


__all__ = ["make_shop"]
