"""The log of calls: a record for every tool call, the tools unchanged for a client, the answer never written."""

import json

import pytest
from mcp import Client

from semantic_warehouse_mcp import calls
from semantic_warehouse_mcp.adapters import CALLER
from semantic_warehouse_mcp.server import build_server, main

pytestmark = pytest.mark.anyio


async def test_the_tools_are_the_same_for_a_client_with_the_log_and_without(shop_config):
    async with Client(build_server(shop_config())) as client:
        plain = (await client.list_tools()).tools
    async with Client(build_server(shop_config(), log_calls=[].append)) as client:
        logged = (await client.list_tools()).tools
    assert [tool.model_dump() for tool in logged] == [tool.model_dump() for tool in plain]


async def test_a_call_that_answers_is_recorded_without_its_answer(shop_config):
    records = []
    sql = "SELECT order_id FROM marts.orders ORDER BY order_id"
    async with Client(build_server(shop_config(rows=2), log_calls=records.append)) as client:
        result = await client.call_tool("run_query", {"sql": sql})
    answer = result.content[0].text
    (record,) = records
    assert record["event"] == "tool_call" and record["tool"] == "run_query" and record["outcome"] == "answered"
    assert record["arguments"] == {"sql": sql, "connection": None}  # as the tool took them, defaults too
    assert record["caller"] is None  # nobody named: in-process, no gateway
    assert record["answer_characters"] == len(answer)
    assert (record["connection"], record["row_count"], record["truncated"]) == ("shop", 2, True)
    assert record["seconds"] >= 0 and record["at"].endswith("+00:00")
    assert "rows" not in record and json.loads(answer)["rows"]  # the rows went to the client, not to the log


async def test_a_call_that_is_refused_is_recorded_with_why(shop_config):
    records = []
    async with Client(build_server(shop_config(), log_calls=records.append)) as client:
        result = await client.call_tool("run_query", {"sql": "DROP TABLE marts.orders"})
    (record,) = records
    assert result.is_error and record["outcome"] == "error"
    assert record["error"].startswith("ToolError: Refused by the guard") and "answer_characters" not in record


async def test_a_tool_that_reaches_no_database_is_recorded_too():
    records = []
    async with Client(build_server(log_calls=records.append)) as client:
        await client.call_tool("about", {})
    (record,) = records
    assert record["tool"] == "about" and record["arguments"] == {} and record["answer_characters"] > 0


async def test_the_caller_a_gateway_named_is_in_the_record():
    records = []
    held = CALLER.set("ann@example.com")
    try:
        async with Client(build_server(log_calls=records.append)) as client:
            await client.call_tool("about", {})
    finally:
        CALLER.reset(held)
    assert records[0]["caller"] == "ann@example.com"


async def test_a_very_long_argument_is_cut_and_says_so(shop_config):
    records = []
    sql = "SELECT 1 -- " + "x" * calls.ARGUMENT_CHARACTERS
    async with Client(build_server(shop_config(), log_calls=records.append)) as client:
        await client.call_tool("run_query", {"sql": sql})
    kept = records[0]["arguments"]["sql"]
    assert kept.startswith("SELECT 1 -- xxx") and kept.endswith(f"({len(sql)} characters, cut)")


async def test_a_log_that_cannot_be_written_fails_no_call():
    def broken(record):
        raise OSError("no space left on device")

    async with Client(build_server(log_calls=broken)) as client:
        result = await client.call_tool("about", {})
    assert not result.is_error


async def test_without_the_option_nothing_is_recorded(shop_config, capsys):
    async with Client(build_server(shop_config())) as client:
        await client.call_tool("about", {})
    assert "tool_call" not in capsys.readouterr().err


def test_a_record_is_one_json_line_on_standard_error(capsys):
    calls.to_standard_error({"event": "tool_call", "tool": "about", "arguments": {"name": "Привет\nмир"}})
    written = capsys.readouterr()
    assert written.out == "" and written.err.count("\n") == 1
    assert json.loads(written.err)["arguments"]["name"] == "Привет\nмир"


def test_the_option_is_offered(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "--log-calls" in capsys.readouterr().out
