"""The team's own instructions and context documents: served as written, for the whole server and per connection."""

import json
import textwrap

import pytest
from conftest import write_config
from mcp import Client

from semantic_warehouse_mcp.config import ConfigError, load
from semantic_warehouse_mcp.server import build_server

pytestmark = pytest.mark.anyio

GLOSSARY = "# Glossary\n\nA *payer* is a customer with a paid order.\n"
RULES = "# How revenue is counted\n\nRevenue is net of refunds: `amount - refunded_amount`.\nNever add EUR to USD.\n"

CONFIG = """
instructions: |
  Dates are UTC.
context: [glossary.md]
connections:
  - name: shop
    engine: duckdb
    path: shop.duckdb
    readable: ["marts.*"]
    instructions: |
      Test orders are already removed from marts.orders.
      Ask before reading raw tables.
    context: [kitchen]
limits:
  answer_characters: {answer_characters}
"""


@pytest.fixture
def team(shop_path):
    folder = shop_path.parent
    (folder / "glossary.md").write_text(GLOSSARY)
    kitchen = folder / "kitchen"
    (kitchen / "nested").mkdir(parents=True)
    (kitchen / "rules.md").write_text(RULES)
    (kitchen / "nested" / "pipeline.txt").write_text("Orders arrive every hour.\n" * 400)
    (kitchen / ".drafts").mkdir()
    (kitchen / ".drafts" / "secret.md").write_text("not served")
    (kitchen / "diagram.png").write_bytes(b"\x89PNG")
    (folder / "outside.md").write_text("not in any context folder")

    def build(answer_characters=40_000):
        return load(write_config(folder, CONFIG.format(answer_characters=answer_characters)))

    return build


async def call(config, tool, **arguments):
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    return (text, True) if result.is_error else (json.loads(text), False)


async def test_instructions_reach_the_agent_as_written(team):
    async with Client(build_server(team())) as client:
        instructions = client.instructions
    assert "Instructions for every connection:\nDates are UTC." in instructions
    assert (
        "Instructions for the connection `shop`:\n"
        "Test orders are already removed from marts.orders.\nAsk before reading raw tables." in instructions
    )
    answer, _ = await call(team(), "list_connections")
    assert answer["instructions"] == "Dates are UTC.\n"
    shop = answer["connections"][0]
    assert (
        shop["instructions"] == "Test orders are already removed from marts.orders.\nAsk before reading raw tables.\n"
    )
    assert shop["context"] == ["kitchen/nested/pipeline.txt", "kitchen/rules.md"]
    assert answer["context"] == ["glossary.md"]


async def test_list_context(team):
    answer, _ = await call(team(), "list_context")
    assert answer["documents"] == [
        {"name": "glossary.md", "title": "Glossary", "characters": len(GLOSSARY)},
        {"name": "kitchen/nested/pipeline.txt", "title": "Orders arrive every hour.", "characters": 10400,
         "connection": "shop"},
        {"name": "kitchen/rules.md", "title": "How revenue is counted", "characters": len(RULES), "connection": "shop"},
    ]  # fmt: skip


async def test_a_document_is_served_as_written(team):
    answer, _ = await call(team(), "get_context", name="kitchen/rules.md")
    assert answer["text"] == RULES
    assert answer["connection"] == "shop"
    assert "next_offset" not in answer


async def test_a_long_document_comes_in_parts_that_add_up(team, shop_path):
    config = team(answer_characters=2000)
    parts, offset = [], 0
    while True:
        async with Client(build_server(config)) as client:
            result = await client.call_tool("get_context", {"name": "kitchen/nested/pipeline.txt", "offset": offset})
        assert len(result.content[0].text) <= 2000
        answer = json.loads(result.content[0].text)
        parts.append(answer["text"])
        if "next_offset" not in answer:
            break
        offset = answer["next_offset"]
    assert len(parts) > 1
    assert "".join(parts) == (shop_path.parent / "kitchen" / "nested" / "pipeline.txt").read_text()


async def test_an_edited_document_is_served_without_a_restart(team, shop_path):
    config = team()
    (shop_path.parent / "kitchen" / "rules.md").write_text("# Changed\n")
    (shop_path.parent / "kitchen" / "new.md").write_text("# Added\n")
    answer, _ = await call(config, "get_context", name="kitchen/rules.md")
    assert answer["text"] == "# Changed\n"
    listed, _ = await call(config, "list_context", connection="shop")
    assert "kitchen/new.md" in [document["name"] for document in listed["documents"]]


@pytest.mark.parametrize(
    "name",
    ["outside.md", "../outside.md", "kitchen/../outside.md", "kitchen/.drafts/secret.md", "kitchen/diagram.png",
     "/etc/passwd", "kitchen"],
)  # fmt: skip
async def test_only_listed_documents_are_served(team, name):
    text, is_error = await call(team(), "get_context", name=name)
    assert is_error
    assert f"No context document named {name!r}" in text


async def test_offset_out_of_range(team):
    text, is_error = await call(team(), "get_context", name="glossary.md", offset=-1)
    assert is_error
    assert f"`offset` must be between 0 and {len(GLOSSARY)}" in text


def test_a_missing_context_path_is_named(shop_path):
    body = textwrap.dedent(CONFIG).format(answer_characters=40_000)
    with pytest.raises(ConfigError) as error:
        load(write_config(shop_path.parent, body))
    assert "context: 'glossary.md' does not exist" in str(error.value)
    assert "connections[0] \"shop\" → context: 'kitchen' does not exist" in str(error.value)


def test_two_documents_with_one_name_are_named(team, shop_path):
    other = shop_path.parent / "elsewhere" / "kitchen"
    other.mkdir(parents=True)
    (other / "rules.md").write_text("another")
    body = CONFIG.format(answer_characters=40_000).replace(
        "context: [kitchen]", "context: [kitchen, elsewhere/kitchen]"
    )
    with pytest.raises(ConfigError, match="two documents are named 'kitchen/rules.md'"):
        load(write_config(shop_path.parent, body))
