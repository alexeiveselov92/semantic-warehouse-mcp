"""The guide names everything a user meets: a tool, a refusal, a configuration key or an engine without its entry in
docs/ fails here — and so does a page the guide's contents do not list."""

import pathlib
import re

import pytest
from mcp import Client
from pydantic import BaseModel

from semantic_warehouse_mcp.adapters import ENGINES, adapter_class
from semantic_warehouse_mcp.config import ConfigFile
from semantic_warehouse_mcp.guard import REFUSALS
from semantic_warehouse_mcp.server import build_server

pytestmark = pytest.mark.anyio

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"


async def test_every_tool_has_its_entry_in_the_guide():
    async with Client(build_server()) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]
    page = (DOCS / "reference" / "tools.md").read_text(encoding="utf-8")
    missing = [name for name in names if f"## `{name}`" not in page]
    assert not missing, f"tools without an entry in docs/reference/tools.md: {missing}"


def test_every_refusal_has_its_entry_in_the_guide():
    page = (DOCS / "reference" / "guard.md").read_text(encoding="utf-8")
    missing = [code for code in REFUSALS if f"### `{code}`" not in page]
    assert not missing, f"refusals without an entry in docs/reference/guard.md: {missing}"


def _keys(model: type[BaseModel]) -> set[str]:
    keys = set()
    for name, field in model.model_fields.items():
        keys.add(name)
        for inner in (field.annotation, *getattr(field.annotation, "__args__", ())):
            if isinstance(inner, type) and issubclass(inner, BaseModel):
                keys |= _keys(inner)
    return keys


def test_every_configuration_key_has_its_entry_in_the_guide():
    page = (DOCS / "reference" / "configuration.md").read_text(encoding="utf-8")
    missing = sorted(key for key in _keys(ConfigFile) if f"`{key}`" not in page)
    assert not missing, f"configuration keys without an entry in docs/reference/configuration.md: {missing}"


def test_every_engine_and_its_keys_have_their_entry_in_the_guide():
    page = (DOCS / "reference" / "engines.md").read_text(encoding="utf-8")
    for engine in ENGINES:
        assert f"`engine: {engine}`" in page, f"engine {engine} without its section in docs/reference/engines.md"
        missing = [key for key in adapter_class(engine).settings_model.model_fields if f"`{key}`" not in page]
        assert not missing, f"keys of engine {engine} without an entry in docs/reference/engines.md: {missing}"


def test_the_contents_list_every_page():
    contents = (DOCS / "README.md").read_text(encoding="utf-8")
    linked = set(re.findall(r"\]\(([^)#]+\.md)\)", contents))
    pages = {str(page.relative_to(DOCS)) for page in DOCS.rglob("*.md") if page.name != "README.md"}
    assert pages <= linked, f"pages docs/README.md does not list: {sorted(pages - linked)}"
