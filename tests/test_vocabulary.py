"""A team's own metadata in `custom_extensions`, declared in a vocabulary: checked at start, served as written with the
definitions of the values it uses — and no team's key named in the server's code."""

import ast
import json
import pathlib
import textwrap

import pytest
from conftest import write_config
from mcp import Client

from semantic_warehouse_mcp.config import ConfigError, load
from semantic_warehouse_mcp.server import build_server

pytestmark = pytest.mark.anyio

SOURCE = pathlib.Path(__file__).resolve().parent.parent / "src"

VOCABULARY = """
vendor_name: acme
keys:
  tags:
    levels: [dataset, field]
    type: list
    values: {sales: Paid orders., money: An amount of money., identifier: A key naming one row.}
  layer:
    levels: [dataset]
    values: [raw, mart]
  rank: {type: number}
  internal: {type: boolean}
  written_by: {type: mapping, levels: [dataset]}
  notes: {type: any}
"""

MODEL = """
version: "0.2.0.dev0"
name: shop
datasets:
  - name: orders
    source: marts.orders
    fields:
      - name: amount
        expression: {{dialects: [{{dialect: ANSI_SQL, expression: amount}}]}}
        custom_extensions:
          - {{vendor_name: {vendor}, data: {field_data}}}
    custom_extensions:
      - {{vendor_name: {vendor}, data: {dataset_data}}}
"""

CONFIG = """
vocabularies: [{vocabularies}]
connections:
  - name: shop
    engine: duckdb
    path: shop.duckdb
    readable: ["marts.*"]
    semantic_models: [shop.yaml]
"""


@pytest.fixture
def team(shop_path):
    """Loads the shop with one dataset's and one field's own data, under a vocabulary; `problems=True` returns the
    configuration's problems instead."""
    folder = shop_path.parent

    def build(dataset_data="{tags: [sales]}", field_data="{tags: [money]}", vocabulary=VOCABULARY, vendor="acme",
              vocabularies="acme.yaml", problems=False):  # fmt: skip
        model = MODEL.format(vendor=vendor, dataset_data=dataset_data, field_data=field_data)
        (folder / "shop.yaml").write_text(textwrap.dedent(model), encoding="utf-8")
        (folder / "acme.yaml").write_text(textwrap.dedent(vocabulary), encoding="utf-8")
        path = write_config(folder, CONFIG.format(vocabularies=vocabularies))
        if not problems:
            return load(path)
        with pytest.raises(ConfigError) as error:
            load(path)
        return error.value.problems

    return build


def test_declared_metadata_passes(team):
    data = "{tags: [sales], layer: mart, rank: 2, internal: false, written_by: {topic: shop.order_paid}, notes: [1]}"
    config = team(dataset_data=data)
    assert config.connections["shop"].models[0].document["datasets"][0]["custom_extensions"][0]["data"] == {
        "tags": ["sales"],
        "layer": "mart",
        "rank": 2,
        "internal": False,
        "written_by": {"topic": "shop.order_paid"},
        "notes": [1],
    }


AT = 'connections[0] "shop" → semantic_models → shop.yaml → datasets[0] "orders" → custom_extensions[0] (acme)'
MONEY = "{tags: [money]}"  # the field's own data, when only the dataset's is wrong


@pytest.mark.parametrize(
    ("dataset_data", "field_data", "expected"),
    [
        ("{tag: [sales]}", MONEY, f"{AT} → tag: not a key of acme's vocabulary — did you mean 'tags'?"),
        ("{colour: red}", MONEY, f"{AT} → colour: not a key of acme's vocabulary (it has: tags, layer, rank, "),
        ("{tags: [sales]}", "{layer: mart}",
         AT.replace("custom_extensions", 'fields[0] "amount" → custom_extensions')
         + " → layer: not used on a field (the vocabulary allows it on: dataset)"),
        ("{tags: sales}", MONEY, f"{AT} → tags: must be a list, got a string ('sales')"),
        ("{tags: [sale]}", MONEY, f"{AT} → tags: 'sale' is not one of its values — did you mean 'sales'?"),
        ("{tags: [sales, 7]}", MONEY, f"{AT} → tags: 7 is not one of its values"),
        ("{layer: bronze}", MONEY, f"{AT} → layer: 'bronze' is not one of its values (it has: raw, mart)"),
        ("{rank: two}", MONEY, f"{AT} → rank: must be a number, got a string ('two')"),
        ("{rank: true}", MONEY, f"{AT} → rank: must be a number, got a boolean"),
        ("{internal: 1}", MONEY, f"{AT} → internal: must be a boolean, got a number"),
        ("{written_by: x}", MONEY, f"{AT} → written_by: must be a mapping, got a string ('x')"),
        ("{layer: }", MONEY, f"{AT} → layer: must be a string, got nothing"),
        ("'[1, 2]'", MONEY, f"{AT} → the data must be a mapping of the keys acme's vocabulary declares (acme.yaml)"),
        ("'not json'", MONEY, f"{AT}: the data is not JSON, so acme.yaml cannot check it"),
        ("""'{"tags": ["sale"]}'""", MONEY, f"{AT} → tags: 'sale' is not one of its values"),  # Ossie's JSON string
    ],
)  # fmt: skip
def test_undeclared_metadata_stops_the_server(team, dataset_data, field_data, expected):
    found = team(dataset_data=dataset_data, field_data=field_data, problems=True)
    assert len(found) == 1, found
    assert found[0].startswith(expected), found


def test_every_problem_is_named_at_once(team):
    found = team(dataset_data="{tags: [sale], layer: bronze}", field_data="{tags: [mony]}", problems=True)
    assert len(found) == 3


def test_unknown_keys_may_be_allowed(team):
    config = team(dataset_data="{tags: [sales], anything: {at: all}}", vocabulary=VOCABULARY + "unknown_keys: allow\n")
    data = config.connections["shop"].models[0].document["datasets"][0]["custom_extensions"][0]["data"]
    assert data == {"tags": ["sales"], "anything": {"at": "all"}}
    found = team(dataset_data="{tags: [sale]}", vocabulary=VOCABULARY + "unknown_keys: allow\n", problems=True)
    assert "'sale' is not one of its values" in found[0]  # a declared key is still checked


@pytest.mark.parametrize("vendor", ["ACME", "'acme '", "Acme"])
def test_a_vendor_written_unlike_its_vocabulary_is_refused(team, vendor):
    """A vendor differing from a declared one only in case or spaces would pass unchecked by a slip."""
    found = team(vendor=vendor, dataset_data="{tags: [nope], undeclared: 1}", problems=True)
    assert found[0].endswith("→ vendor_name: " + repr(vendor.strip("'")) + " is not 'acme', the vendor a vocabulary "
                             "declares — write it exactly, or its data passes unchecked")  # fmt: skip


def test_a_value_under_a_secret_like_key_is_never_repeated(team):
    vocabulary = VOCABULARY + "  repo_url: {type: list}\n  api_token: {values: [a, b]}\n"
    data = "{repo_url: 'https://bot:hunter2@git.example.com/x', api_token: hunter2}"
    found = team(dataset_data=data, vocabulary=vocabulary, problems=True)
    assert found == [
        f"{AT} → repo_url: must be a list, got a string",
        f"{AT} → api_token: a value is not one of its values",
    ]
    assert not any("hunter2" in problem for problem in found)


def test_a_vendor_without_a_vocabulary_passes_as_written(team):
    config = team(vendor="OTHER_TOOL", dataset_data="'not json at all'", field_data="{whatever: [1, {a: b}]}")
    orders = config.connections["shop"].models[0].document["datasets"][0]
    assert orders["custom_extensions"][0]["data"] == "not json at all"
    assert orders["fields"][0]["custom_extensions"][0]["data"] == {"whatever": [1, {"a": "b"}]}


@pytest.mark.parametrize(
    ("vocabulary", "expected"),
    [
        (VOCABULARY.replace("levels: [dataset, field]", "level: [dataset, field]"),
         "vocabularies → acme.yaml → keys → tags → level: unknown key — did you mean 'levels'?"),
        (VOCABULARY.replace("levels: [dataset, field]", "levels: [table]"),
         "vocabularies → acme.yaml → keys → tags → levels[0]: input should be 'model', 'dataset', 'field', 'metric'"),
        (VOCABULARY.replace("type: list", "type: tags"),
         "vocabularies → acme.yaml → keys → tags → type: input should be"),
        (VOCABULARY.replace("rank: {type: number}", "rank: {type: string, values: [1, 2]}"),
         "vocabularies → acme.yaml → keys → rank → values: a list of strings, or a mapping of each value to its"),
        (VOCABULARY.replace("values: [raw, mart]", "values: []"),
         "vocabularies → acme.yaml → keys → layer → values: a list of strings, or a mapping"),
        (VOCABULARY.replace("written_by: {type: mapping,", "written_by: {type: mapping, values: [a],"),
         "vocabularies → acme.yaml → keys → written_by: `values` close a string or a list, not a mapping"),
        (VOCABULARY.replace("vendor_name: acme\n", ""), "vocabularies → acme.yaml → vendor_name: required"),
        ("vendor_name: acme\nkeys: {}\n", "vocabularies → acme.yaml → keys: dictionary should have at least 1 item"),
        ("- a list", "vocabularies → acme.yaml: a vocabulary is a mapping with `vendor_name` and `keys` at the top"),
        ("vendor_name: acme\nvendor_name: other\n", "vocabularies → acme.yaml: not valid YAML: the key 'vendor_name'"),
    ],
)  # fmt: skip
def test_a_vocabulary_is_checked(team, vocabulary, expected):
    found = team(vocabulary=vocabulary, problems=True)
    assert any(problem.startswith(expected) for problem in found), found


def test_one_vocabulary_per_vendor(team, shop_path):
    (shop_path.parent / "again.yaml").write_text(textwrap.dedent(VOCABULARY), encoding="utf-8")
    found = team(vocabularies="acme.yaml, again.yaml", problems=True)
    assert found == [
        "vocabularies: again.yaml declares the vendor 'acme', as acme.yaml does — one vocabulary per vendor"
    ]


def test_a_missing_vocabulary_is_named(team):
    found = team(vocabularies="nowhere.yaml", problems=True)
    assert found[0].startswith("vocabularies: 'nowhere.yaml' does not exist")


# --- the first check of the design: a large production dbt catalog's shapes, with no line of code naming them -------

# Made up, in the shapes of a catalog of several hundred models (dev/RESEARCH.md, "What a production dbt catalog
# holds"): some sixty business areas and sixteen kinds of column as one closed list of tags, each with its definition;
# a layer per model; provenance as lists of mappings; long prose descriptions.
AREAS = {f"area_{n:02d}": f"Business area {n}: what its tables are about." for n in range(1, 61)}
KINDS = {
    kind: f"A column that is a {kind.replace('_', ' ')}."
    for kind in ["identifier", "foreign_key", "partition_key", "timestamp", "status", "flag", "enum", "financial",
                 "counter", "duration", "geo", "device", "version", "text", "json", "array"]
}  # fmt: skip
CATALOG_VOCABULARY = {
    "vendor_name": "acme_catalog",
    "keys": {
        "tags": {
            "description": "Categories from the team's closed list: business areas and kinds of column.",
            "levels": ["dataset", "field"],
            "type": "list",
            "in_lists": True,
            "values": AREAS | KINDS,
        },
        "layer": {
            "levels": ["dataset"],
            "in_lists": True,
            "values": {"staging": "Cleaned source data.", "intermediate": "Joins and logic.", "core": "Facts."},
        },
        "provenance": {
            "description": "Where the rows come from in the application: generated down the lineage, never by hand.",
            "levels": ["dataset"],
            "type": "list",
        },
    },
}
PROSE = (
    "One row per paid order event, grain: event_id.\n\n"
    "Amounts are in minor units of the order's currency. Values of `status`: paid, refunded, chargeback.\n"
    "Source payload path: payload.order.amount_minor.\n\n"
    "**Never use `ingested_at` as the reporting clock** — use `order_date`.\n"
)
PROVENANCE = [
    {
        "topic": "billing.order_paid",
        "repository": "acme/billing-service",
        "dto": "https://example.com/acme/billing-service/blob/main/dto/order_paid.go#L12",
        "api_schema": "https://example.com/acme/billing-service/blob/main/api/orders.yaml",
        "comment": None,
    },
    {"topic": "billing.order_refunded", "repository": "acme/billing-service", "dto": "https://example.com/r.go"},
]


def catalog_model(dataset_tags: list[str], field_tags: list[str]) -> dict:
    return {
        "version": "0.2.0.dev0",
        "name": "catalog",
        "datasets": [
            {
                "name": "fct_orders",
                "source": "marts.orders",
                "description": PROSE,
                "fields": [
                    {
                        "name": "order_date",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_date"}]},
                        "description": "The day the order was paid, UTC. The partition key.",
                        "custom_extensions": [{"vendor_name": "acme_catalog", "data": {"tags": field_tags}}],
                    }
                ],
                "custom_extensions": [
                    {
                        "vendor_name": "acme_catalog",
                        "data": {"tags": dataset_tags, "layer": "core", "provenance": PROVENANCE},
                    }
                ],
            }
        ],
    }


@pytest.fixture
def catalog(shop_path):
    folder = shop_path.parent

    def build(dataset_tags=("area_07", "area_42"), field_tags=("partition_key", "timestamp"), problems=False):
        (folder / "catalog.yaml").write_text(json.dumps(catalog_model(list(dataset_tags), list(field_tags))))
        (folder / "vocabulary.yaml").write_text(json.dumps(CATALOG_VOCABULARY))
        config = """
        vocabularies: [vocabulary.yaml]
        connections:
          - {name: warehouse, engine: duckdb, path: shop.duckdb, readable: ["marts.*"], semantic_models: [catalog.yaml]}
        """
        path = write_config(folder, config)
        if not problems:
            return load(path)
        with pytest.raises(ConfigError) as error:
            load(path)
        return error.value.problems

    return build


async def test_a_large_catalogs_shapes_fit_and_are_served_as_written(catalog):
    async with Client(build_server(catalog())) as client:
        listed = json.loads((await client.call_tool("list_datasets", {})).content[0].text)
        described = json.loads((await client.call_tool("describe_dataset", {"dataset": "fct_orders"})).content[0].text)
    assert listed["datasets"][0]["custom_extensions"] == [
        {"vendor_name": "acme_catalog", "data": {"tags": ["area_07", "area_42"], "layer": "core"}}
    ]  # provenance is not `in_lists`: only describe_dataset shows it
    assert described["description"] == PROSE
    assert described["custom_extensions"][0]["data"]["provenance"] == PROVENANCE
    assert described["fields"][0]["custom_extensions"][0]["data"] == {"tags": ["partition_key", "timestamp"]}
    tags = described["vocabulary"]["acme_catalog"]["tags"]
    assert tags["values"] == {  # the definitions of the values used — not all seventy-six
        "area_07": AREAS["area_07"],
        "area_42": AREAS["area_42"],
        "partition_key": "A column that is a partition key.",
        "timestamp": "A column that is a timestamp.",
    }
    assert described["vocabulary"]["acme_catalog"]["provenance"] == {
        "description": CATALOG_VOCABULARY["keys"]["provenance"]["description"]
    }


def test_a_tag_outside_the_closed_list_is_refused_where_it_is(catalog):
    found = catalog(field_tags=("partition_key", "timestmp"), problems=True)
    assert found == [
        'connections[0] "warehouse" → semantic_models → catalog.yaml → datasets[0] "fct_orders" → fields[0] '
        "\"order_date\" → custom_extensions[0] (acme_catalog) → tags: 'timestmp' is not one of its values — did you "
        "mean 'timestamp'?"
    ]


def test_no_teams_key_is_named_in_the_servers_code():
    """The server knows no team's keys: none of those a vocabulary declares here, nor the examples', is a string in
    the code — but where the dbt importer names, once, dbt's own field that holds dbt's tags."""
    keys = set(CATALOG_VOCABULARY["keys"]) | {"layer", "written_by", "owner", "dto", "topic", "repository"}
    named = set()
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        dbts_own = {
            id(node.value)
            for node in ast.walk(tree)
            if path.name == "dbt.py" and isinstance(node, ast.Assign) and ast.unparse(node.targets) == "MANIFEST_TAGS"
        }
        named |= {
            (path.name, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value in keys and id(node) not in dbts_own
        }
    assert not named, f"a team's key named in the server's code: {sorted(named)}"
