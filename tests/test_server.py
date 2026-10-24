"""The server through the SDK's own client, in-process — what any MCP client sees."""

import json

import pytest
from mcp import Client

from semantic_warehouse_mcp.server import build_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_every_tool_says_it_is_read_only():
    async with Client(build_server()) as client:
        tools = (await client.list_tools()).tools
    assert tools
    for tool in tools:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name


async def test_about_answers():
    async with Client(build_server()) as client:
        result = await client.call_tool("about", {})
    assert not result.is_error
    answer = json.loads(result.content[0].text)
    assert answer["name"] == "semantic-warehouse-mcp"
    assert answer["read_only"] is True
