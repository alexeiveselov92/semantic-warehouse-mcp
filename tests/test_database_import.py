"""The database importer: a draft of a semantic model from a connection's own catalog — for a database no dbt project
describes. Only what the database says is written; only what `readable` lets agents read; an edited draft is kept."""

import json

import duckdb
import pytest
from conftest import SHOP_CONFIG, write_config
from mcp import Client

from semantic_warehouse_mcp import files
from semantic_warehouse_mcp.config import load
from semantic_warehouse_mcp.server import build_server, main

pytestmark = pytest.mark.anyio

CONFIG = SHOP_CONFIG.format(rows=5, seconds=10, answer_characters=40_000)


@pytest.fixture
def drafting(shop_path, capsys):
    """Run `import-database` on the shop's configuration (changed by `config`): the draft read back and what was said —
    or, with `problems`, what it said on its error output."""

    def run(*args, config=CONFIG, problems=False):
        path = write_config(shop_path.parent, config)
        args = [str(shop_path.parent / arg) if arg == "draft.yaml" else arg for arg in args]  # the shell's folder
        with pytest.raises(SystemExit) if problems else _nothing():
            main(["import-database", "--config", str(path), *args])
        said = capsys.readouterr()
        if problems:
            return said.err
        return files.read_yaml((shop_path.parent / "draft.yaml").read_text(encoding="utf-8")), said.out

    return run


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def datasets(document):
    return {dataset["name"]: dataset for dataset in document["datasets"]}


async def test_the_readable_tables_are_drafted_with_the_databases_own_words(drafting):
    document, said = drafting("--output", "draft.yaml")
    assert document["name"] == "shop" and "description" not in document  # the model's meaning is the team's to write
    found = datasets(document)
    assert list(found) == ["customers", "daily", "orders", "events"]  # marts.*, raw.events — never secret.salaries
    orders = found["orders"]
    assert orders["source"] == "marts.orders" and orders["description"] == "One row per paid order."
    fields = {field["name"]: field for field in orders["fields"]}
    assert list(fields) == ["order_id", "order_date", "customer_id", "currency", "amount", "refunded_amount"]
    assert fields["amount"]["description"] == "Paid, before refunds."
    assert fields["order_date"]["dimension"] == {"is_time": True} and "dimension" not in fields["amount"]
    assert fields["amount"]["expression"] == {"dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}]}
    assert "description" not in found["customers"]  # nothing is guessed
    assert "4 datasets (3 tables, 1 view), 13 fields, 3 time columns" in said
    assert "the database's comments on 1 datasets and 1 fields" in said


async def test_the_draft_is_served_once_named(drafting, shop_path):
    """The configuration may name the draft before it exists: only the connection is reached to write it."""
    serving = CONFIG.replace("    limits:", "    semantic_models: [draft.yaml]\n    limits:")
    drafting("--output", "draft.yaml", config=serving)
    config = load(write_config(shop_path.parent, serving))
    async with Client(build_server(config)) as client:
        answer = json.loads((await client.call_tool("describe_dataset", {"dataset": "orders"})).content[0].text)
    assert answer["description"] == "One row per paid order."


async def test_tables_and_fields_choose_what_is_drafted(drafting):
    document, _ = drafting("--output", "draft.yaml", "--tables", "marts.ord*", "--fields", "described")
    assert [(d["name"], [f["name"] for f in d.get("fields", [])]) for d in document["datasets"]] == [
        ("orders", ["amount"])
    ]


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (("--tables", "secret.*"), "--tables 'secret.*' names none of the tables readable here"),  # not readable
        (("--tables", "marts"), "--tables 'marts' is not schema.table (either part may hold * or ?)"),
        (("--connection", "elsewhere"), "no connection is named 'elsewhere' (shop)"),
    ],
)
async def test_what_cannot_be_drafted_is_named(drafting, args, said):
    assert said in drafting("--output", "draft.yaml", *args, problems=True)


async def test_an_existing_draft_is_kept_unless_told(drafting, shop_path):
    (shop_path.parent / "draft.yaml").write_text("edited by a person\n", encoding="utf-8")
    assert "exists — a draft may have been edited since: give --overwrite" in drafting(
        "--output", "draft.yaml", problems=True
    )
    assert (shop_path.parent / "draft.yaml").read_text(encoding="utf-8") == "edited by a person\n"
    document, _ = drafting("--output", "draft.yaml", "--overwrite")
    assert "orders" in datasets(document)


async def test_several_connections_need_one_named(drafting, shop_path):
    two = CONFIG.replace("connections:\n", "connections:\n  - {name: other, engine: duckdb, path: shop.duckdb, "
                         "readable: ['raw.events']}\n")  # fmt: skip
    assert "name the connection to draft: --connection (other, shop)" in drafting(
        "--output", "draft.yaml", config=two, problems=True
    )
    document, _ = drafting("--output", "draft.yaml", "--connection", "other", config=two)
    assert list(datasets(document)) == ["events"] and document["name"] == "other"


async def test_one_name_in_two_schemas_makes_two_datasets(drafting, shop_path):
    with duckdb.connect(str(shop_path)) as connection:
        connection.execute("CREATE TABLE raw.orders (id VARCHAR)")
    wider = CONFIG.replace('readable: ["marts.*", "raw.events"]', 'readable: ["marts.orders", "raw.*"]')
    wider = wider.replace("      - {{ table: raw.events, bound_by: event_date }}\n", "")
    document, _ = drafting("--output", "draft.yaml", "--name", "two", config=wider)
    assert list(datasets(document)) == ["marts__orders", "events", "logs", "raw__orders"] and document["name"] == "two"


async def test_a_name_each_database_reads_its_own_way_is_refused(drafting, shop_path):
    with duckdb.connect(str(shop_path)) as connection:
        connection.execute('CREATE TABLE raw.weird ("a\\b" VARCHAR)')
    wider = CONFIG.replace('readable: ["marts.*", "raw.events"]', 'readable: ["marts.*", "raw.*"]')
    said = drafting("--output", "draft.yaml", config=wider, problems=True)
    assert "raw.weird: the name 'a\\\\b' holds a backslash or a control character" in said
    document, _ = drafting("--output", "draft.yaml", "--tables", "raw.events", config=wider)  # left out, drafted
    assert list(datasets(document)) == ["events"]
