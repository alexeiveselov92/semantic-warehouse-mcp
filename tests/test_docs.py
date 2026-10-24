"""The guide names every tool: a tool without its entry in docs/reference/tools.md fails here."""

import pathlib

import pytest
from mcp import Client

from semantic_warehouse_mcp.server import build_server

pytestmark = pytest.mark.anyio

TOOLS_PAGE = pathlib.Path(__file__).resolve().parent.parent / "docs" / "reference" / "tools.md"


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_every_tool_has_its_entry_in_the_guide():
    async with Client(build_server()) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]
    page = TOOLS_PAGE.read_text(encoding="utf-8")
    missing = [name for name in names if f"## `{name}`" not in page]
    assert not missing, f"tools without an entry in docs/reference/tools.md: {missing}"
