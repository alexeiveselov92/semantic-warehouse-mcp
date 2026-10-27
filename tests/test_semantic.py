"""Semantic models in Apache Ossie: checked against their version's schema and bound to their connection at start,
served as written by list_datasets, describe_dataset, list_metrics and describe_metric."""

import dataclasses
import json
import pathlib
import textwrap

import pytest
from conftest import write_config
from mcp import Client

from semantic_warehouse_mcp import answers, semantic, server, vocabulary
from semantic_warehouse_mcp.config import ConfigError, load
from semantic_warehouse_mcp.server import build_server, main, start_log

pytestmark = pytest.mark.anyio

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"

MODEL = """
version: "0.2.0.dev0"
name: shop
description: A made-up shop.
ai_context: Revenue is net of refunds.
datasets:
  - name: orders
    source: marts.orders
    primary_key: [order_id]
    description: |-
      One row per paid order.
      Grain: order_id. Never use it as the reporting clock.
    ai_context: {synonyms: [purchases]}
    fields:
      - name: order_id
        expression: {dialects: [{dialect: ANSI_SQL, expression: order_id}]}
        datatype: String
        custom_extensions:
          - {vendor_name: acme, data: {tags: [identifier]}}
      - name: net_amount
        expression: {dialects: [{dialect: ANSI_SQL, expression: amount - refunded_amount}]}
        description: Paid, net of refunds.
        datatype: Decimal
    metrics:
      - name: order_count
        expression: {dialects: [{dialect: ANSI_SQL, expression: COUNT(order_id)}]}
    custom_extensions:
      - {vendor_name: acme, data: {tags: [sales], layer: mart, owner: billing}}
      - {vendor_name: OTHER_TOOL, data: '{"anything": [1, 2]}'}
  - name: customers
    source: marts.customers
    primary_key: [customer_id]
    fields:
      - name: customer_id
        expression: {dialects: [{dialect: ANSI_SQL, expression: customer_id}]}
  - name: events
    source: raw.events
    fields:
      - name: event_date
        expression: {dialects: [{dialect: ANSI_SQL, expression: event_date}]}
relationships:
  - {name: orders_to_customers, from: orders, to: customers, from_columns: [customer_id], to_columns: [customer_id]}
metrics:
  - name: revenue
    expression: {dialects: [{dialect: ANSI_SQL, expression: SUM(orders.net_amount)}]}
    description: Revenue net of refunds.
    ai_context: {synonyms: [sales]}
"""

VOCABULARY = """
vendor_name: acme
keys:
  tags:
    description: What it is about.
    levels: [dataset, field]
    type: list
    in_lists: true
    values: {sales: Paid orders and refunds., identifier: A key naming one row., money: An amount of money.}
  layer:
    levels: [dataset]
    in_lists: true
    values: {raw: As sent., mart: Business-ready.}
  owner:
    description: The team to ask.
"""

CONFIG = """
vocabularies: [acme.yaml]
connections:
  - name: shop
    engine: duckdb
    path: shop.duckdb
    readable: ["marts.*", "raw.events"]
    large_tables: [{table: raw.events, bound_by: event_date}]
    semantic_models: [shop.yaml]
"""


def write(folder: pathlib.Path, name: str, text: str) -> None:
    (folder / name).write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def shop(shop_path):
    """The shop with its model and vocabulary; call it with replacements to break one, or `problems=True` to get the
    configuration's problems instead of the configuration."""

    def build(*replacements, model=MODEL, config=CONFIG, vocabulary=VOCABULARY, problems=False):
        folder = shop_path.parent
        for old, new in replacements:
            assert old in model, old
            model = model.replace(old, new)
        write(folder, "shop.yaml", model)
        write(folder, "acme.yaml", vocabulary)
        path = write_config(folder, config)
        if not problems:
            return load(path)
        with pytest.raises(ConfigError) as error:
            load(path)
        return error.value.problems

    return build


async def call(config, tool, **arguments):
    async with Client(build_server(config)) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text
    return (text, True) if result.is_error else (json.loads(text), False)


# --- loading and checking -----------------------------------------------------------------------------------------


def test_a_model_is_read_as_written(shop):
    model = shop().connections["shop"].models[0]
    assert (model.name, model.version, model.file) == ("shop", "0.2.0.dev0", "shop.yaml")
    orders = model.document["datasets"][0]
    assert orders["description"] == "One row per paid order.\nGrain: order_id. Never use it as the reporting clock."
    assert orders["custom_extensions"] == [
        {"vendor_name": "acme", "data": {"tags": ["sales"], "layer": "mart", "owner": "billing"}},
        {"vendor_name": "OTHER_TOOL", "data": {"anything": [1, 2]}},  # Ossie's JSON string, decoded
    ]


def test_the_examples_hold_against_ossie_and_their_vocabulary():
    models, problems = semantic.read(EXAMPLES / "semantic" / "shop.yaml", "shop.yaml")
    assert problems == []
    declared, problems = vocabulary.read(EXAMPLES / "vocabulary" / "acme.yaml", "acme.yaml")
    assert problems == []
    checked = [declared.check(e["data"], level) for _, level, e in semantic.extensions(models[0].document)]
    assert len(checked) == 9 and not any(checked)


def test_a_strict_ossie_document_is_read_as_it_is(shop):
    strict = MODEL.replace("data: {tags: [identifier]}", """data: '{"tags": ["identifier"]}'""")
    config = shop(model=strict)
    field = config.connections["shop"].models[0].document["datasets"][0]["fields"][0]
    assert field["custom_extensions"] == [{"vendor_name": "acme", "data": {"tags": ["identifier"]}}]


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            "datatype: Decimal",
            "datatype: Money",
            'shop.yaml → datasets[0] "orders" → fields[1] "net_amount" → datatype: \'Money\' is not one of',
        ),
        ("    source: marts.customers\n", "", "shop.yaml → datasets[1] \"customers\": 'source' is a required property"),
        ("    primary_key: [order_id]", "    primary_key: [order_id]\n    colour: red", "('colour' was unexpected)"),
        (
            "ai_context: {synonyms: [purchases]}",
            "ai_context: [purchases]",
            'datasets[0] "orders" → ai_context: must be a string, or a mapping of `instructions`, `synonyms`',
        ),
        (
            "data: {tags: [identifier]}",
            "data: [identifier]",
            "custom_extensions[0] → data: must be a JSON string (as Ossie writes it) or a YAML mapping",
        ),
    ],
)
def test_a_model_is_checked_against_its_schema(shop, old, new, expected):
    found = shop((old, new), problems=True)
    assert len(found) == 1, found
    assert found[0].startswith('connections[0] "shop" → semantic_models → shop.yaml'), found
    assert expected in found[0], found


def test_a_version_this_server_does_not_read_is_refused(shop):
    found = shop(('version: "0.2.0.dev0"', 'version: "0.3"'), problems=True)
    assert found == [
        "connections[0] \"shop\" → semantic_models → shop.yaml → version: '0.3' is not a version this server reads "
        "(it reads 0.2.0.dev0, 0.1.1)"
    ]
    found = shop(('version: "0.2.0.dev0"\n', ""), problems=True)
    assert "shop.yaml: no `version`" in found[0]


def test_a_broken_document_names_its_first_problems(shop):
    broken = MODEL + "".join(
        f"  - name: m{i}\n    expression: {{dialects: [{{dialect: ANSI_SQL, expression: '1'}}]}}\n    datatype: X\n"
        for i in range(30)
    )
    found = shop(model=broken, problems=True)
    assert len(found) == semantic.MAX_PROBLEMS_PER_FILE + 1
    assert found[-1].endswith("shop.yaml: and 10 more problems against its schema")


OSSIE_0_1_1 = """
version: "0.1.1"
semantic_model:
  - name: sales
    datasets:
      - name: orders
        source: marts.orders
        custom_extensions:
          - {vendor_name: COMMON, data: '{"owner": "billing"}'}
  - name: people
    datasets:
      - name: customers
        source: marts.customers
"""


async def test_ossie_0_1_1_is_read_too(shop):
    config = shop(model=OSSIE_0_1_1)
    models = config.connections["shop"].models
    assert [(m.name, m.version, m.where) for m in models] == [
        ("sales", "0.1.1", 'shop.yaml → semantic_model[0] "sales"'),
        ("people", "0.1.1", 'shop.yaml → semantic_model[1] "people"'),
    ]
    answer, _ = await call(config, "list_datasets")
    assert [(d["model"], d["dataset"]) for d in answer["datasets"]] == [("sales", "orders"), ("people", "customers")]


def test_ossie_0_1_1_names_its_vendors(shop):
    """0.1.1 closes its list of vendors; a team's own vendor name needs 0.2."""
    found = shop(model=OSSIE_0_1_1.replace("vendor_name: COMMON", "vendor_name: acme"), problems=True)
    assert "custom_extensions[0] → vendor_name: 'acme' is not one of ['COMMON'" in found[0]


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("  - name: customers\n", "  - name: orders\n", "shop.yaml → two datasets are named 'orders'"),
        ("      - name: net_amount", "      - name: order_id", 'datasets[0] "orders": two fields are named'),
        (
            "      - name: order_count",
            "      - name: net_amount",
            "'net_amount' is both a field and a metric",
        ),
        (
            "  - name: revenue",
            "  - name: revenue\n" + MODEL.split("  - name: revenue")[1] + "  - name: revenue",
            "two metrics are named 'revenue'",
        ),  # fmt: skip
        (
            "      - name: order_count",
            "      - name: order_count\n        expression: {dialects: [{dialect: ANSI_SQL, expression: '1'}]}\n"
            "      - name: order_count",
            "datasets[0] \"orders\": two metrics are named 'order_count'",
        ),
        (
            "  - {name: orders_to_customers,",
            "  - {name: orders_to_customers, from: orders, to: customers, from_columns: [a], to_columns: [b]}\n"
            "  - {name: orders_to_customers,",
            "two relationships are named 'orders_to_customers'",
        ),
        ("from: orders, to: customers", "from: orders, to: clients", "→ to: no dataset named 'clients' in the model"),
        ("to_columns: [customer_id]", "to_columns: [customer_id, country]", "must name as many columns each"),
    ],
)
def test_ossie_rules_beyond_its_schema(shop, old, new, expected):
    found = shop((old, new), problems=True)
    assert any(expected in problem for problem in found), found


def test_two_models_of_a_connection_may_not_share_a_name(shop, shop_path):
    write(shop_path.parent, "again.yaml", MODEL)
    found = shop(config=CONFIG.replace("[shop.yaml]", "[shop.yaml, again.yaml]"), problems=True)
    assert found == ["connections[0] \"shop\" → semantic_models: two models are named 'shop' (shop.yaml; again.yaml)"]


def test_a_folder_of_models(shop, shop_path):
    folder = shop_path.parent / "models"
    (folder / ".drafts").mkdir(parents=True)
    write(folder, "shop.yaml", MODEL)
    write(folder, "notes.md", "not a model")
    write(folder / ".drafts", "broken.yaml", "version: nope")
    config = shop(config=CONFIG.replace("[shop.yaml]", "[models/]"))
    assert [m.file for m in config.connections["shop"].models] == ["models/shop.yaml"]


def test_a_missing_model_is_named(shop):
    found = shop(config=CONFIG.replace("[shop.yaml]", "[nowhere.yaml]"), problems=True)
    assert found[0].startswith("connections[0] \"shop\" → semantic_models: 'nowhere.yaml' does not exist")


def test_a_folder_or_a_file_without_a_model_is_named(shop, shop_path):
    (shop_path.parent / "empty").mkdir()
    found = shop(config=CONFIG.replace("[shop.yaml]", "[empty/]"), problems=True)
    assert found[0].startswith("connections[0] \"shop\" → semantic_models: 'empty/' holds no .yaml, .yml or .json file")
    found = shop(model='version: "0.1.1"\nsemantic_model: []\n', problems=True)
    assert found == ['connections[0] "shop" → semantic_models → shop.yaml: holds no model']


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("ai_context: {synonyms: [purchases]}", "ai_context: {synonyms: [purchases], at: !!set {a, b}}",
         'datasets[0] "orders" → ai_context → at: a set is a value JSON cannot hold'),
        ("ai_context: {synonyms: [purchases]}", "ai_context: {synonyms: [purchases], at: !!binary aGk=}",
         'datasets[0] "orders" → ai_context → at: a bytes is a value JSON cannot hold'),
        ("ai_context: {synonyms: [purchases]}", "ai_context: {synonyms: [purchases], at: .inf}",
         "→ ai_context → at: inf is a number JSON cannot hold"),
        ("data: {tags: [identifier]}", "data: {tags: [identifier], 1: a, '1': b}", "two keys read as '1' — write one"),
    ],
)  # fmt: skip
def test_a_value_json_cannot_hold_is_refused_where_it_is(shop, old, new, expected):
    found = shop((old, new), problems=True)
    assert len(found) == 1 and expected in found[0], found


async def test_what_yaml_reads_is_served_as_written(shop):
    config = shop(
        ("ai_context: {synonyms: [purchases]}", "ai_context: {instructions: x, since: 2026-03-01, every: on}"),
        ("{vendor_name: acme, data: {tags: [identifier]}}",
         "{vendor_name: OTHER_TOOL, data: {tags: [identifier], country: NO, at: 2026-03-01 10:00:00, 1: one}}"),
        ("""'{"anything": [1, 2]}'""", """'{"rate": 0.12345678901234567890, "big": 1e400}'"""),
    )  # fmt: skip
    answer, error = await call(config, "describe_dataset", dataset="orders")
    assert not error, answer
    assert answer["ai_context"] == {"instructions": "x", "since": "2026-03-01", "every": "on"}
    assert answer["fields"][0]["custom_extensions"][0]["data"] == {
        "tags": ["identifier"], "country": "NO", "at": "2026-03-01 10:00:00", "1": "one",
    }  # fmt: skip
    # exact: a number no float is exactly comes as its text, as the answers keep numbers
    assert answer["custom_extensions"][1]["data"] == {"rate": "0.12345678901234567890", "big": "1e400"}


async def test_a_json_strings_numbers_keep_their_type(shop):
    numbers = """'{"one": 1.0, "big": 1e16, "zero": -0.0, "tenth": 0.1, "int": 7, "huge": 123456789012345678901}'"""
    config = shop(("""'{"anything": [1, 2]}'""", numbers))
    answer, _ = await call(config, "describe_dataset", dataset="orders")
    data = answer["custom_extensions"][1]["data"]
    assert data == {"one": 1.0, "big": 1e16, "zero": -0.0, "tenth": 0.1, "int": 7, "huge": 123456789012345678901}
    assert [type(data[key]) for key in ("one", "big", "int")] == [float, float, int]


def test_a_json_string_holding_a_lone_surrogate_is_refused(shop):
    found = shop(("""'{"anything": [1, 2]}'""", """'{"a": ["x\\ud800"]}'"""), problems=True)
    assert len(found) == 1 and found[0].endswith(
        "→ data: text that is not Unicode — a lone surrogate, \\ud800: write the character meant"
    ), found


@pytest.mark.parametrize("source", ["FROM marts.orders", "VALUES (1)", "-- a note\nSELECT 1", "marts.orders o"])
def test_a_source_is_a_table_name_or_a_query(shop, source):
    found = shop(("source: marts.customers", f"source: {source!r}"), problems=True)
    assert len(found) == 1 and "is neither a table name (schema.table) nor a query starting with SELECT" in found[0]


async def test_a_source_in_the_connections_own_catalog(shop):
    """The DuckDB file is shop.duckdb: shop.marts.orders is a table of the connection's own catalog."""
    config = shop(("source: marts.customers", "source: shop.marts.customers"))
    answer, _ = await call(config, "describe_dataset", dataset="customers")
    assert answer["table"]["name"] == "marts.customers"
    answer, error = await call(config, "run_query", sql="SELECT count(*) FROM shop.marts.orders")
    assert not error and answer["rows"] == [[6]]


async def test_every_semantic_answer_stays_within_the_limit(shop):
    long = "x" * 3000
    config = shop(
        ("      Grain: order_id. Never use it as the reporting clock.", f"      {long}"),
        ("SUM(orders.net_amount)", f"SUM(orders.net_amount) + 0 * LENGTH('{long}')"),
        config=CONFIG + "limits: {answer_characters: 1000}\n",
    )
    for tool, arguments in [
        ("describe_dataset", {"dataset": "orders"}),
        ("describe_metric", {"metric": "revenue"}),
        ("list_datasets", {}),
        ("list_metrics", {}),
        ("list_connections", {}),
    ]:
        answer, error = await call(config, tool, **arguments)
        assert not error, (tool, answer)
        assert len(answers.dumps(answer)) <= 1000, tool
    answer, _ = await call(config, "describe_dataset", dataset="orders")
    assert answer["truncated"] is True and "long_values" in answer["truncated_by"]
    assert answer["description"].endswith("…")
    assert answer["note"].endswith("Long values were cut to fit the answer; each cut one ends in ….")


async def test_a_list_that_does_not_fit_names_every_item_by_its_model(shop):
    """The map of what there is, in one answer: a list too long in full names each dataset only, never a few in full."""
    vocabulary = VOCABULARY.replace("description: What it is about.", f"description: {'y' * 3000}")
    config = shop(vocabulary=vocabulary, config=CONFIG + "limits: {answer_characters: 1500}\n")
    answer, _ = await call(config, "list_datasets")
    assert answer == {
        "compact": True,
        "note": "The full list of 3 datasets does not fit in the answer (1500 characters): each is named only. Give "
        "`search` or `metadata` for their descriptions, or describe_dataset for one.",
        "datasets": [{"connection": "shop", "model": "shop", "names": ["orders", "customers", "events"]}],
    }  # orders, the first, brings its vocabulary's 3000 characters: in full, not one would fit
    synonyms = ", ".join(f"a_synonym_of_revenue_{number}" for number in range(60))
    config = shop(("ai_context: {synonyms: [sales]}", f"ai_context: {{synonyms: [{synonyms}]}}"),
                  config=CONFIG + "limits: {answer_characters: 1000}\n")  # fmt: skip
    answer, _ = await call(config, "list_metrics")
    assert answer["compact"] is True  # a dataset's own metric named with its dataset
    assert answer["metrics"] == [{"connection": "shop", "model": "shop", "names": ["revenue", "orders.order_count"]}]


async def test_names_that_do_not_fit_either_are_cut_and_said(shop):
    many = "".join(
        f"  - name: a_dataset_with_a_rather_long_name_{number:03}\n    source: marts.orders\n" for number in range(120)
    )
    config = shop(("  - name: customers\n", many + "  - name: customers\n"),
                  config=CONFIG + "limits: {answer_characters: 1000}\n")  # fmt: skip
    answer, _ = await call(config, "list_datasets")
    assert len(answers.dumps(answer)) <= 1000
    assert answer["compact"] is True and answer["truncated"] is True
    shown = len(answer["datasets"][0]["names"])
    assert 0 < shown < 123
    assert answer["note"].startswith(f"The full list of 123 datasets does not fit in the answer (1000 characters), nor "
                                     f"do all their names: only {shown} are named.")  # fmt: skip


async def test_a_search_lists_the_closest_first(shop):
    config = shop(
        ("  - name: customers\n    source: marts.customers\n",
         "  - name: customers\n    source: marts.customers\n    description: Customers, with their refund requests.\n"),
        ("  - name: events\n", "  - name: refund_events\n"),
    )  # fmt: skip
    answer, _ = await call(config, "list_datasets", search="REFUND")
    # in its name, then in its description, then in a field's description — not the model's order
    assert [item["dataset"] for item in answer["datasets"]] == ["refund_events", "customers", "orders"]
    answer, _ = await call(config, "list_metrics", search="order")
    assert [item["metric"] for item in answer["metrics"]] == ["order_count", "revenue"]


def test_how_closely_a_node_matches():
    node = {"name": "paid_orders", "description": "Orders paid by card.", "fields": [{"name": "refund_reason"}]}
    assert semantic.rank(node, []) == (0, 0)
    assert semantic.rank(node, ["PAID", "orders"]) == (0, -2)
    assert semantic.rank(node, ["card"]) == (1, 0)
    assert semantic.rank(node, ["paid", "card"]) == (1, -1)  # one word in its name: before (1, 0)
    assert semantic.rank(node, ["refund"]) == (2, 0)
    assert semantic.rank(node, ["refund", "nowhere"]) is None


def test_a_password_in_a_model_is_never_repeated(shop):
    found = shop(("source: marts.customers", "source: ['https://bot:hunter2@db.example.com/x']"), problems=True)
    assert len(found) == 1 and "https://…@db.example.com/x" in found[0] and "hunter2" not in found[0], found


def test_a_lists_description_is_its_first_line_within_200_characters():
    assert semantic.first_line("a" * 200 + "\nmore") == "a" * 199 + "…"
    assert semantic.first_line("a" * 199 + "\nmore") == "a" * 199 + "…"
    assert semantic.first_line("short\nmore") == "short…"
    assert semantic.first_line("short") == "short"


def test_own_metadata_is_shortened_by_its_own_length():
    """A text by its characters — quotes and line breaks JSON would escape do not make it long — a list or a mapping
    by its length as JSON; a vendor's data that is no mapping is one value; short values as written."""
    quoted = '"line"\n' * 142 + "abcdef"  # 1000 characters, 1290 as JSON
    assert len(quoted) == 1000
    long_list = [f"item_{i}" for i in range(200)]
    extensions = [
        {
            "vendor_name": "acme",
            "data": {"sql": quoted, "longer": quoted + "g", "built_from": long_list, "tags": ["x"]},
        },
        {"vendor_name": "OTHER", "data": "t" * 1001},
        {"vendor_name": "EMPTY"},
    ]
    made, shortened = server.shorten_metadata(extensions, 1000)
    assert shortened
    acme = made[0]["data"]
    assert acme["sql"] == quoted  # 1000 characters: as written
    assert acme["longer"] == quoted + "…"  # 1001: its first 1000, ending in …
    assert acme["built_from"] == f"… ({len(answers.dumps(long_list))} characters as JSON)"
    assert acme["tags"] == ["x"]
    assert made[1] == {"vendor_name": "OTHER", "data": "t" * 1000 + "…"}
    assert made[2] == {"vendor_name": "EMPTY"}
    assert extensions[0]["data"]["longer"] == quoted + "g"  # the model itself untouched
    assert server.shorten_metadata([], 1000) == ([], False)
    short = [{"vendor_name": "acme", "data": {"sql": quoted}}]
    assert server.shorten_metadata(short, 1000) == (short, False)


def test_a_field_names_itself_only_when_every_dialect_is_its_name():
    """Its expression is left out of describe_dataset only when nothing is lost: every dialect is the name itself."""

    def field(*expressions):
        dialects = [{"dialect": f"D{i}", "expression": text} for i, text in enumerate(expressions)]
        return {"name": "amount", "expression": {"dialects": dialects}}

    assert semantic.names_itself(field("amount"))
    assert semantic.names_itself(field("amount", "amount"))
    assert not semantic.names_itself(field("amount", "amount - refunded_amount"))
    assert not semantic.names_itself(field("Amount"))  # another name, as written
    assert not semantic.names_itself(field('"amount"'))
    assert not semantic.names_itself({"name": "amount", "expression": {"dialects": []}})


# --- bound to its connection ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("secret.salaries", "source: secret.salaries is not readable here"),
        ("marts.nothing", "source: there is no table or view marts.nothing on the connection"),
        ("SELECT * FROM secret.salaries", "is not readable here (readable: marts.*, raw.events) (table_not_readable)"),
        ("SELECT * FROM raw.events", "(large_table_unbounded)"),
        ("WITH x AS (DELETE FROM marts.orders) SELECT 1", "it holds DELETE; only reading runs here (not_read_only)"),
    ],
)
def test_a_dataset_source_is_held_to_its_connection(shop, source, expected):
    found = shop(("source: marts.customers", f"source: {source}"), problems=True)
    assert len(found) == 1, found
    assert found[0].startswith(
        'connections[0] "shop" → semantic_models → shop.yaml → datasets[1] "customers" → source: '
    ), found
    assert expected in found[0], found


async def test_a_table_the_database_does_not_hold_may_be_left_out(shop, capsys):
    """A model made from a project ahead of its database: the dataset is taken out, with its relationships, and said."""
    leaving = CONFIG.replace(
        "semantic_models: [shop.yaml]", "semantic_models: [shop.yaml]\n    missing_tables: leave_out"
    )
    config = shop(("source: marts.customers", "source: marts.not_built_yet"), config=leaving)
    connection = config.connections["shop"]
    assert connection.left_out == ("shop.customers (marts.not_built_yet)",)
    model = connection.models[0].document
    assert [d["name"] for d in model["datasets"]] == ["orders", "events"] and model.get("relationships") == []
    answer, _ = await call(config, "list_datasets")
    assert [d["dataset"] for d in answer["datasets"]] == ["orders", "events"]
    main(["--config", config.source, "--check"])
    assert "left out, the database holding no table of theirs: shop.customers (marts.not_built_yet)" in (
        capsys.readouterr().out
    )
    still = shop(("source: marts.customers", "source: secret.salaries"), config=leaving, problems=True)
    assert len(still) == 1 and "is not readable here" in still[0]  # a table not readable is refused all the same
    assert start_log(config) == [
        "semantic-warehouse-mcp: shop: 1 datasets left out, no table of theirs: shop.customers (marts.not_built_yet) "
        "(`--check` names every one)"
    ]
    many = dataclasses.replace(connection, left_out=tuple(f"shop.d{n} (marts.d{n})" for n in range(90)))
    logged = start_log(dataclasses.replace(config, connections={"shop": many}))
    assert logged[0].startswith("semantic-warehouse-mcp: shop: 90 datasets left out, no table of theirs: shop.d0 ")
    assert "shop.d4 (marts.d4), …" in logged[0] and "shop.d5 " not in logged[0]  # the log is no list of ninety


async def test_a_query_source_the_guard_passes(shop):
    config = shop(("source: marts.customers", "source: SELECT customer_id FROM marts.customers WHERE country = 'DE'"))
    answer, _ = await call(config, "describe_dataset", dataset="customers")
    assert answer["source"] == "SELECT customer_id FROM marts.customers WHERE country = 'DE'"
    assert answer["table"] == {"note": "the source is a query, not a table"}


# --- the tools ------------------------------------------------------------------------------------------------------


async def test_list_connections_names_the_models(shop):
    answer, _ = await call(shop(), "list_connections")
    assert answer["connections"][0]["semantic_models"] == ["shop"]


async def test_list_datasets(shop):
    answer, _ = await call(shop(), "list_datasets")
    assert answer == {
        "vocabulary": {
            "acme": {
                "tags": {"description": "What it is about.", "values": {"sales": "Paid orders and refunds."}},
                "layer": {"values": {"mart": "Business-ready."}},
            }
        },
        "datasets": [
            {
                "connection": "shop",
                "model": "shop",
                "dataset": "orders",
                "source": "marts.orders",
                "description": "One row per paid order.…",
                "synonyms": ["purchases"],
                "custom_extensions": [{"vendor_name": "acme", "data": {"tags": ["sales"], "layer": "mart"}}],
            },
            {"connection": "shop", "model": "shop", "dataset": "customers", "source": "marts.customers"},
            {"connection": "shop", "model": "shop", "dataset": "events", "source": "raw.events"},
        ],
    }


@pytest.mark.parametrize(
    ("search", "expected"),
    [
        ("identifier", ["orders"]),  # a field's own metadata
        ("MART order", ["orders"]),  # every word, in any case
        ("event_date", ["events"]),  # a field's name
        ("marts", ["orders", "customers"]),  # the source
        ("nothing", []),
    ],
)
async def test_list_datasets_search(shop, search, expected):
    answer, _ = await call(shop(), "list_datasets", search=search)
    assert [d["dataset"] for d in answer["datasets"]] == expected


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"tags": "sales"}, [("orders", None)]),  # the dataset's own
        ({"tags": "identifier"}, [("orders", ["order_id"])]),  # a field's: named
        ({"tags": ["money", "sales"]}, [("orders", None)]),  # any one of a list
        ({"tags": "identifier", "layer": "mart"}, [("orders", ["order_id"])]),  # every key
        ({"owner": "billing"}, [("orders", None)]),  # a value that is not a list
        ({"layer": "raw"}, []),
        ({"tags": "sales", "owner": "someone"}, []),
    ],
)
async def test_list_datasets_by_own_metadata(shop, metadata, expected):
    answer, _ = await call(shop(), "list_datasets", metadata=metadata)
    assert [(d["dataset"], d.get("matched_fields")) for d in answer["datasets"]] == expected
    assert "note" not in answer


async def test_a_metadata_filter_nothing_can_match_says_so(shop):
    answer, _ = await call(shop(), "list_datasets", metadata={"tags": ["idnetifier", "sales"], "layr": "mart"})
    assert answer["datasets"] == []  # every key must hold: `layr` holds nowhere
    assert answer["note"] == (
        "'idnetifier' is not one of the values of 'tags' — did you mean 'identifier'? list_metadata shows them. "
        "No own metadata here has the key 'layr' — did you mean 'layer'? list_metadata shows the keys and their values."
    )
    text, is_error = await call(shop(), "list_datasets", metadata={"tags": []})
    assert is_error
    assert "`metadata` → tags: give a value, or a list of values" in text


async def test_a_search_names_the_fields_it_found_in(shop):
    answer, _ = await call(shop(), "list_datasets", search="event_date")
    assert [(d["dataset"], d.get("matched_fields")) for d in answer["datasets"]] == [("events", ["event_date"])]
    answer, _ = await call(shop(), "list_datasets", search="refunds")  # its description says it, and a field's
    assert [(d["dataset"], d.get("matched_fields")) for d in answer["datasets"]] == [("orders", ["net_amount"])]
    answer, _ = await call(shop(), "list_datasets", search="order", metadata={"tags": "identifier"})
    assert [(d["dataset"], d.get("matched_fields")) for d in answer["datasets"]] == [("orders", ["order_id"])]


def test_what_own_metadata_holds():
    node = {
        "custom_extensions": [
            {"vendor_name": "a", "data": {"tags": ["x", "y"], "flag": True, "count": 1, "name": "1"}},
            {"vendor_name": "b", "data": "not a mapping"},
        ],
        "fields": [
            {"name": "f", "custom_extensions": [{"vendor_name": "a", "data": {"kind": "money", "unit": "eur"}}]},
            {"name": "g", "custom_extensions": [{"vendor_name": "a", "data": {"kind": "money"}}]},
        ],
    }
    assert semantic.held(node, {"tags": ["y"], "flag": [True], "count": [1], "name": ["1"]}) == {
        "tags",
        "flag",
        "count",
        "name",
    }
    assert semantic.held(node, {"flag": [1], "count": [True], "name": [1], "count ": [1]}) == set()
    assert semantic.metadata_match(node, {"tags": ["x"]}) == []
    assert semantic.metadata_match(node, {"kind": ["money"]}) == ["f", "g"]
    assert semantic.metadata_match(node, {"kind": ["money"], "unit": ["eur"]}) == ["f"]  # one field holding both
    assert semantic.metadata_match(node, {"kind": ["money"], "unit": ["usd"]}) is None
    assert semantic.fields_holding({"fields": [{"name": "Order_Date"}, {"name": "x"}]}, ["order", "DATE"]) == [
        "Order_Date"
    ]


async def test_list_metrics_by_own_metadata(shop):
    owned = ("    description: Revenue net of refunds.\n", "    description: Revenue net of refunds.\n"
             "    custom_extensions: [{vendor_name: acme, data: {owner: finance}}]\n")  # fmt: skip
    answer, _ = await call(shop(owned), "list_metrics", metadata={"owner": "finance"})
    assert [m["metric"] for m in answer["metrics"]] == ["revenue"]
    answer, _ = await call(shop(owned), "list_metrics", metadata={"owner": "billing"})
    assert answer["metrics"] == []


async def test_list_metadata(shop):
    answer, _ = await call(shop(), "list_metadata")
    assert answer == {
        "metadata": [
            {
                "vendor_name": "OTHER_TOOL",
                "key": "anything",
                "datasets": 1,
                "values": [{"value": 1, "datasets": 1}, {"value": 2, "datasets": 1}],
            },
            {
                "vendor_name": "acme",
                "key": "tags",
                "description": "What it is about.",
                "fields": 1,
                "datasets": 1,
                "values": [
                    {"value": "identifier", "fields": 1, "definition": "A key naming one row."},
                    {"value": "sales", "datasets": 1, "definition": "Paid orders and refunds."},
                ],
            },
            {
                "vendor_name": "acme",
                "key": "layer",
                "datasets": 1,
                "values": [{"value": "mart", "datasets": 1, "definition": "Business-ready."}],
            },
            {
                "vendor_name": "acme",
                "key": "owner",
                "description": "The team to ask.",
                "datasets": 1,
                "values": [{"value": "billing", "datasets": 1}],
            },
        ]
    }


async def test_list_metadata_names_how_many_when_the_values_are_many_or_long(shop):
    long = ("owner: billing", "owner: " + "b" * 101)
    answer, _ = await call(shop(long), "list_metadata")
    owner = next(item for item in answer["metadata"] if item["key"] == "owner")
    assert owner == {"vendor_name": "acme", "key": "owner", "description": "The team to ask.", "datasets": 1,
                     "distinct_values": 1}  # fmt: skip
    many = ("owner: billing", "owner: billing, other: [" + ", ".join(f"v{i}" for i in range(51)) + "]")
    vocabulary = VOCABULARY.replace("keys:", "unknown_keys: allow\nkeys:")
    answer, _ = await call(shop(many, vocabulary=vocabulary), "list_metadata")
    other = next(item for item in answer["metadata"] if item["key"] == "other")
    assert other == {"vendor_name": "acme", "key": "other", "datasets": 1, "distinct_values": 51}


async def test_describe_dataset_cuts_long_own_metadata_before_a_field(shop):
    long = ("owner: billing", "owner: " + "b" * 3000)
    config = shop(long, config=CONFIG + "limits: {answer_characters: 4000}\n")
    answer, _ = await call(config, "describe_dataset", dataset="orders", full=True)
    assert len(answers.dumps(answer)) <= 4000
    assert [field["name"] for field in answer["fields"]] == ["order_id", "net_amount"]
    owner = next(e for e in answer["custom_extensions"] if e["vendor_name"] == "acme")["data"]["owner"]
    assert owner == "b" * 2000 + "…"
    assert "own metadata texts longer than 2000 characters cut, each ending in …" in answer["note"]


async def test_describe_dataset_shortens_long_own_metadata_unless_asked_in_full(shop):
    """A long own metadata value — a text, a list — is made short by default and says so; `full` serves it whole."""
    built_from = [f"marts.source_{i}" for i in range(80)]
    long = (
        ("owner: billing", "owner: " + "b" * 3000 + ", built_from: [" + ", ".join(built_from) + "]"),
        ("{tags: [identifier]}", "{tags: [identifier], owner: " + "c" * 1001 + "}"),
    )
    vocabulary = VOCABULARY.replace("keys:", "unknown_keys: allow\nkeys:")
    config = shop(*long, vocabulary=vocabulary)

    answer, _ = await call(config, "describe_dataset", dataset="orders")
    acme = next(e for e in answer["custom_extensions"] if e["vendor_name"] == "acme")["data"]
    assert acme["owner"] == "b" * 1000 + "…"
    assert acme["built_from"] == f"… ({len(answers.dumps(built_from))} characters as JSON)"
    assert acme["tags"] == ["sales"] and acme["layer"] == "mart"  # short values as written
    assert answer["fields"][0]["custom_extensions"][0]["data"]["owner"] == "c" * 1000 + "…"  # a field's too
    assert answer["note"] == (
        "Own metadata values longer than 1000 characters are shortened — a text to its beginning, ending in …, a list "
        "or a mapping to a note of its length: `full: true` serves them whole."
    )

    answer, _ = await call(config, "describe_dataset", dataset="orders", full=True)
    acme = next(e for e in answer["custom_extensions"] if e["vendor_name"] == "acme")["data"]
    assert acme["owner"] == "b" * 3000 and acme["built_from"] == built_from
    assert answer["fields"][0]["custom_extensions"][0]["data"]["owner"] == "c" * 1001
    assert "note" not in answer

    answer, _ = await call(shop(), "describe_dataset", dataset="orders")
    assert "note" not in answer  # nothing long, nothing said


async def test_describe_dataset(shop):
    answer, error = await call(shop(), "describe_dataset", dataset="orders")
    assert not error, answer
    assert list(answer) == [
        "connection", "model", "dataset", "source", "primary_key", "description", "ai_context", "custom_extensions",
        "table", "metrics", "relationships", "other_columns", "vocabulary", "fields",
    ]  # fmt: skip
    assert answer["description"] == "One row per paid order.\nGrain: order_id. Never use it as the reporting clock."
    assert answer["custom_extensions"][1] == {"vendor_name": "OTHER_TOOL", "data": {"anything": [1, 2]}}
    assert answer["table"] == {"name": "marts.orders", "kind": "table", "comment": "One row per paid order."}
    order_id, net_amount = answer["fields"]
    assert order_id["column"] == {"type": "VARCHAR", "nullable": True}  # the field is the column: its type beside
    assert "expression" not in order_id  # nothing but its own name: the column of that name, said once
    assert "column" not in net_amount  # a calculation, not one column
    assert net_amount["expression"] == {"dialects": [{"dialect": "ANSI_SQL", "expression": "amount - refunded_amount"}]}
    assert [c["name"] for c in answer["other_columns"]] == [
        "order_date", "customer_id", "currency", "amount", "refunded_amount",
    ]  # fmt: skip
    assert answer["other_columns"][3] == {
        "name": "amount", "type": "DECIMAL(12,2)", "nullable": True, "comment": "Paid, before refunds.",
    }  # fmt: skip
    assert answer["metrics"][0]["name"] == "order_count"
    assert [r["name"] for r in answer["relationships"]] == ["orders_to_customers"]
    assert answer["vocabulary"] == {
        "acme": {
            "tags": {
                "description": "What it is about.",
                "values": {"sales": "Paid orders and refunds.", "identifier": "A key naming one row."},
            },
            "layer": {"values": {"mart": "Business-ready."}},
            "owner": {"description": "The team to ask."},
        }
    }


async def test_describe_dataset_of_a_large_table_shows_its_bound(shop):
    answer, _ = await call(shop(), "describe_dataset", dataset="events")
    assert answer["table"] == {"name": "raw.events", "kind": "table", "bound_by": "event_date"}


async def test_describe_dataset_keeps_its_fields_within_the_answer_limit(shop):
    """Parts of the answer are made smaller, in turn, before a field is left out; the note says which."""
    answer, _ = await call(
        shop(config=CONFIG + "limits: {answer_characters: 1500}\n"), "describe_dataset", dataset="orders"
    )
    assert len(answers.dumps(answer)) <= 1500
    assert answer["other_columns"] == ["order_date", "customer_id", "currency", "amount", "refunded_amount"]
    assert answer["note"] == "To fit the answer (1500 characters): `other_columns` holds names only."

    answer, _ = await call(
        shop(config=CONFIG + "limits: {answer_characters: 1400}\n"), "describe_dataset", dataset="orders"
    )
    assert len(answers.dumps(answer)) <= 1400
    assert [field["name"] for field in answer["fields"]] == ["order_id", "net_amount"]
    assert "other_columns" not in answer and "relationships" not in answer
    assert answer["metrics"] and answer["vocabulary"]
    assert answer["note"] == (
        "To fit the answer (1400 characters): `other_columns` holds names only; `other_columns` left out "
        "(describe_table shows them); `relationships` left out."
    )


async def test_list_metrics(shop):
    answer, _ = await call(shop(), "list_metrics")
    assert answer == {
        "metrics": [
            {
                "connection": "shop",
                "model": "shop",
                "metric": "revenue",
                "description": "Revenue net of refunds.",
                "synonyms": ["sales"],
            },
            {"connection": "shop", "model": "shop", "metric": "order_count", "dataset": "orders"},
        ]
    }
    answer, _ = await call(shop(), "list_metrics", search="count")
    assert [m["metric"] for m in answer["metrics"]] == ["order_count"]


async def test_the_metric_tools_are_offered_only_where_a_model_defines_a_metric(shop):
    """Where no model defines a metric, the metric tools are not listed and the instructions do not name them."""
    with_metrics = shop()
    async with Client(build_server(with_metrics)) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert {"list_metrics", "describe_metric"} <= names
        assert "`list_metrics` and `describe_metric`" in client.instructions

    own_metric = (
        "    metrics:\n      - name: order_count\n"
        "        expression: {dialects: [{dialect: ANSI_SQL, expression: COUNT(order_id)}]}\n"
    )
    without = shop((own_metric, ""), (MODEL[MODEL.index("metrics:\n  - name: revenue") :], ""))
    async with Client(build_server(without)) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert "list_metrics" not in names and "describe_metric" not in names
        assert {"list_datasets", "describe_dataset", "list_metadata", "run_query"} <= names
        assert "list_metrics" not in client.instructions and "describe_metric" not in client.instructions


async def test_describe_metric_names_what_it_reads(shop):
    answer, _ = await call(shop(), "describe_metric", metric="revenue")
    assert answer == {
        "connection": "shop",
        "model": "shop",
        "metric": "revenue",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.net_amount)"}]},
        "description": "Revenue net of refunds.",
        "ai_context": {"synonyms": ["sales"]},
        "datasets": [
            {
                "dataset": "orders",
                "source": "marts.orders",
                "fields": [
                    {
                        "name": "net_amount",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "amount - refunded_amount"}]},
                    }
                ],
            }
        ],
    }
    answer, _ = await call(shop(), "describe_metric", metric="order_count", dataset="orders")
    assert answer["dataset"] == "orders"
    assert answer["datasets"][0]["fields"][0]["name"] == "order_id"


async def test_a_name_in_two_places_asks_which(shop, shop_path):
    two = CONFIG + CONFIG.split("connections:\n")[1].replace("name: shop", "name: copy")
    config = shop(config=two)
    text, error = await call(config, "describe_dataset", dataset="orders")
    assert error
    assert "'orders' is a dataset of more than one place (model shop on shop; model shop on copy)" in text
    answer, _ = await call(config, "describe_dataset", dataset="orders", connection="copy")
    assert answer["connection"] == "copy"
    text, error = await call(config, "describe_metric", metric="revenue", model="nope")
    assert error and "No semantic model named 'nope'. Models: shop." in text
    text, error = await call(config, "describe_metric", metric="order_count", dataset="customers")
    assert error and "No metric named 'order_count'" in text


async def test_without_a_model_the_lists_say_so(shop_config):
    answer, _ = await call(shop_config(), "list_datasets")
    assert answer == {
        "note": "No connection has a semantic model; list_tables and describe_table show the tables.",
        "datasets": [],
    }
