"""The dbt importer: a dbt project's manifest written as an Ossie document the server serves — what is the team's set by
its configuration, only what is dbt's own read by the code; the document written reads back exactly as built."""

import copy
import json
import pathlib
import shutil
import subprocess
import sys

import pytest
import yaml
from mcp import Client

from semantic_warehouse_mcp import files, semantic
from semantic_warehouse_mcp.config import load as load_config
from semantic_warehouse_mcp.importers import dbt
from semantic_warehouse_mcp.server import build_server, main

pytestmark = pytest.mark.anyio

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"
MANIFEST = json.loads((EXAMPLES / "dbt" / "manifest.json").read_text(encoding="utf-8"))
BASE = {"manifest": "manifest.json", "output": "model.yaml", "model": {"name": "shop_dbt"}}


def node(manifest, unique_id):
    return manifest["nodes"].get(unique_id) or manifest["sources"][unique_id]


@pytest.fixture
def imported(tmp_path):
    """Import a manifest (the example's, changed by `change`) with a configuration (the base one, with `entries`):
    the document as it reads back from the file written, with the import's result — or, with `problems`, those."""

    def run(change=None, problems=False, strict=False, **entries):
        manifest = copy.deepcopy(MANIFEST)
        if change:
            change(manifest)
        (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (tmp_path / "import.yaml").write_text(json.dumps({**BASE, **entries}), encoding="utf-8")
        try:
            importing = dbt.load(tmp_path / "import.yaml")
            result = dbt.build(importing, dbt.read_manifest(importing))
            text = dbt.render(importing, result, strict=strict)
        except dbt.ImportProblem as problem:
            if problems:
                return problem.problems
            raise
        assert not problems, "expected problems, the import went through"
        return files.read_yaml(text), result

    return run


def datasets(document):
    return {dataset["name"]: dataset for dataset in document["datasets"]}


def fields(dataset):
    return {item["name"]: item for item in dataset.get("fields", [])}


# --- the example -----------------------------------------------------------------------------------------------------


def test_the_examples_output_is_what_the_import_writes():
    importing = dbt.load(EXAMPLES / "dbt" / "import.yaml")
    text = dbt.render(importing, dbt.build(importing, dbt.read_manifest(importing)))
    assert (EXAMPLES / "dbt" / "shop-dbt.yaml").read_text(encoding="utf-8") == text


def test_a_manifest_becomes_datasets_fields_and_relationships(imported):
    document, result = imported(vendor_name="acme")
    found = datasets(document)
    assert list(found) == ["customers", "dictionaries__countries", "orders", "raw__events"]
    orders = found["orders"]
    assert orders["source"] == "marts.orders" and orders["primary_key"] == ["order_id"]
    assert orders["description"] == node(MANIFEST, "model.shop.orders")["description"]
    assert list(fields(orders)) == ["order_id", "order_date", "customer_id", "currency", "amount", "refunded_amount"]
    assert fields(orders)["amount"] == {
        "name": "amount",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}]},
        "description": "What the customer paid, before refunds, in the order's currency.",
        "custom_extensions": [{"vendor_name": "acme", "data": {"tags": ["money"]}}],
    }
    assert orders["custom_extensions"] == [
        {"vendor_name": "acme", "data": {"layer": "mart", "tags": ["sales"], "owner": "billing team"}}
    ]
    assert found["raw__events"]["source"] == "raw.events"
    assert found["raw__events"]["custom_extensions"][0]["data"]["written_by"] == [
        {"topic": "shop.site_events", "repository": "https://github.com/acme/shop-site"}
    ]
    assert document["relationships"] == [
        {"name": "customers__country__dictionaries__countries", "from": "customers", "to": "dictionaries__countries",
         "from_columns": ["country"], "to_columns": ["country"]},
        {"name": "orders__customer_id__customers", "from": "orders", "to": "customers",
         "from_columns": ["customer_id"], "to_columns": ["customer_id"]},
    ]  # fmt: skip
    assert result.counts["model"] == 2 and result.counts["source"] == 2 and result.counts["field"] == 17


async def test_the_imported_model_is_served(tmp_path):
    shutil.copy(EXAMPLES / "config.dbt.example.yaml", tmp_path / "config.yaml")
    shutil.copytree(EXAMPLES / "dbt", tmp_path / "dbt")
    shutil.copytree(EXAMPLES / "vocabulary", tmp_path / "vocabulary")
    (tmp_path / "data").mkdir()
    shop = tmp_path / "data" / "shop.duckdb"
    subprocess.run([sys.executable, str(EXAMPLES / "data" / "make_shop.py"), str(shop)], check=True)
    config = load_config(tmp_path / "config.yaml")
    async with Client(build_server(config)) as client:
        result = await client.call_tool("describe_dataset", {"dataset": "orders"})
    answer = json.loads(result.content[0].text)
    assert answer["model"] == "shop_dbt" and answer["table"]["name"] == "marts.orders"
    assert answer["relationships"][0]["to"] == "customers"
    assert answer["vocabulary"]["acme"]["tags"]["values"]["sales"] == "Paid orders and their refunds."
    assert {item["name"]: item["column"]["type"] for item in answer["fields"]}["amount"] == "DECIMAL(12,2)"


# --- what is the team's: the configuration ---------------------------------------------------------------------------


def test_without_a_vendor_the_meta_is_left_out_and_said(imported):
    document, result = imported()
    assert not any("custom_extensions" in dataset for dataset in document["datasets"])
    assert result.counts["meta left out"] == 11
    assert "left out: the meta of 11 tables and columns — no `vendor_name` to hold it" in dbt.summary(
        dbt.Import("x", dbt.ImportFile.model_validate(BASE), pathlib.Path(), pathlib.Path()), result
    )


def test_dbts_own_tags_go_under_the_key_named(imported):
    document, _ = imported(vendor_name="acme", dbt_tags="selected_by")
    assert datasets(document)["orders"]["custom_extensions"][0]["data"]["selected_by"] == ["daily"]
    assert "selected_by" not in datasets(document)["raw__events"]["custom_extensions"][0]["data"]  # it has none


def test_dbts_tags_never_overwrite_the_meta(imported):
    found = imported(vendor_name="acme", dbt_tags="layer", problems=True)
    assert found == [
        "model.shop.orders → meta: holds 'layer' already, the key `dbt_tags` writes dbt's tags as",
        "model.shop.customers → meta: holds 'layer' already, the key `dbt_tags` writes dbt's tags as",
    ]


def test_the_tables_a_model_reads_and_its_sql_go_under_the_keys_named(imported):
    document, result = imported(vendor_name="acme", dbt_depends_on="reads", dbt_code="sql")
    orders = datasets(document)["orders"]["custom_extensions"][0]["data"]
    assert orders["reads"] == ["raw.events"]
    assert orders["sql"] == node(MANIFEST, "model.shop.orders")["raw_code"]
    events = datasets(document)["raw__events"]["custom_extensions"][0]["data"]
    assert "reads" not in events and "sql" not in events  # a source reads nothing and has no SQL
    entry = dbt.ImportFile.model_validate({**BASE, "vendor_name": "acme", "dbt_depends_on": "reads", "dbt_code": "sql"})
    lines = dbt.summary(dbt.Import("x", entry, pathlib.Path(), pathlib.Path()), result)
    assert "2 tables with the tables they read, under 'reads'; 2 tables with their SQL, under 'sql'" in lines


def test_what_a_model_reads_through_an_ephemeral_one_and_in_another_database(imported):
    def change(manifest):
        orders = manifest["nodes"]["model.shop.orders"]
        paid = {**copy.deepcopy(orders), "unique_id": "model.shop.paid", "name": "paid", "alias": "paid"}
        paid["config"] = {**paid["config"], "materialized": "ephemeral"}
        paid["depends_on"] = {"nodes": ["source.shop.raw.events", "model.shop.customers", "model.shop.paid"]}
        lookup = {**copy.deepcopy(orders), "unique_id": "model.other.lookup", "name": "lookup", "alias": "lookup"}
        lookup |= {"package_name": "other", "schema": "ref", "database": "elsewhere"}
        orders["depends_on"] = {"nodes": ["model.shop.paid", "model.other.lookup", "model.shop.gone", "test.shop.t"]}
        manifest["nodes"] |= {"model.shop.paid": paid, "model.other.lookup": lookup}

    document, _ = imported(change, vendor_name="acme", dbt_depends_on="reads")
    found = datasets(document)
    assert "paid" not in found  # ephemeral: no table of its own
    assert found["orders"]["custom_extensions"][0]["data"]["reads"] == [
        "elsewhere.ref.lookup",  # another database's: named with it
        "marts.customers",  # through the ephemeral model, what it reads
        "raw.events",
    ]


def test_dbts_own_never_overwrites_the_meta_nor_shares_a_key(imported):
    found = imported(vendor_name="acme", dbt_code="layer", problems=True)
    assert found == [
        "model.shop.orders → meta: holds 'layer' already, the key `dbt_code` writes its SQL as",
        "model.shop.customers → meta: holds 'layer' already, the key `dbt_code` writes its SQL as",
    ]
    found = imported(vendor_name="acme", dbt_tags="dbt", dbt_code="dbt", dbt_depends_on="reads", problems=True)
    assert found == ["`dbt_tags`, `dbt_code`: each names 'dbt' — give each its own key"]
    found = imported(dbt_depends_on="reads", dbt_code="sql", problems=True)
    assert found == [
        "dbt_depends_on: needs `vendor_name` — it writes the tables it reads into that vendor's data",
        "dbt_code: needs `vendor_name` — it writes its SQL into that vendor's data",
    ]


def test_a_time_column_by_the_teams_mark_and_by_dbts_own(imported):
    def mark(manifest):
        node(manifest, "model.shop.customers")["columns"]["country"]["config"]["tags"] = ["when"]
        node(manifest, "source.shop.raw.events")["columns"]["event_time"]["config"]["meta"]["is_time"] = True
        node(manifest, "source.shop.raw.events")["columns"]["event_name"]["config"]["meta"]["is_time"] = 1

    marks = [{"meta_key": "tags", "value": "timestamp"}, {"dbt_tag": "when"}, {"meta_key": "is_time"}]
    document, _ = imported(mark, places={"time_column": marks})
    timed = {
        (dataset["name"], item["name"])
        for dataset in document["datasets"]
        for item in dataset.get("fields", [])
        if item.get("dimension") == {"is_time": True}
    }
    assert timed == {
        ("orders", "order_date"),  # dbt's own: the model's event_time
        ("customers", "signed_up_on"),  # meta tags hold timestamp
        ("raw__events", "event_date"),
        ("customers", "country"),  # dbt's tag
        ("raw__events", "event_time"),  # is_time: true — and not event_name, whose 1 is not true
    }
    document, _ = imported()
    assert [item for d in document["datasets"] for item in d.get("fields", []) if "dimension" in item] == [
        fields(datasets(document)["orders"])["order_date"]
    ]


def test_one_mark_need_not_be_a_list(imported):
    document, _ = imported(places={"time_column": {"meta_key": "tags", "value": "timestamp"}})
    assert fields(datasets(document)["raw__events"])["event_date"]["dimension"] == {"is_time": True}


def test_a_primary_key_the_teams_key_names(imported):
    def grain(manifest):
        node(manifest, "source.shop.raw.events")["config"]["meta"]["grain"] = ["event_time", "customer_id"]
        node(manifest, "model.shop.orders")["config"]["meta"]["grain"] = "order_id"

    document, _ = imported(grain, places={"primary_key": {"meta_key": "grain"}})
    assert datasets(document)["raw__events"]["primary_key"] == ["event_time", "customer_id"]
    assert datasets(document)["orders"]["primary_key"] == ["order_id"]
    assert datasets(document)["customers"]["primary_key"] == ["customer_id"]  # dbt's own, where the key is absent


def test_a_primary_key_that_is_prose_is_refused_where_it_is(imported):
    def grain(manifest):
        node(manifest, "model.shop.orders")["config"]["meta"]["grain"] = "one row per order"
        node(manifest, "model.shop.customers")["config"]["meta"]["grain"] = []

    found = imported(grain, places={"primary_key": {"meta_key": "grain"}}, problems=True)
    assert found == [
        'model.shop.orders → meta → grain: "one row per order" is not a column\'s name, nor a list of them (places → '
        "primary_key reads it)",
        "model.shop.customers → meta → grain: [] is not a column's name, nor a list of them (places → primary_key "
        "reads it)",
    ]


def test_tables_and_resources_choose_what_is_imported(imported):
    document, _ = imported(tables=["marts.*"])
    assert list(datasets(document)) == ["customers", "orders"]
    assert [r["name"] for r in document["relationships"]] == ["orders__customer_id__customers"]
    document, result = imported(resources=["source"])
    assert list(datasets(document)) == ["dictionaries__countries", "raw__events"]
    assert "relationships" not in document
    assert result.counts["relationship to a table left out"] == 2


def test_the_configuration_is_checked_every_problem_at_once(tmp_path):
    (tmp_path / "import.yaml").write_text(
        "manifest: missing.json\noutput: x.yaml\nmodel: {name: x}\ntables: [marts, 'a.b']\ndbt_tags: t\n"
        "places:\n  time_column: [{meta_key: a, dbt_tag: b}, {dbt_tag: b, value: 1}]\n  primry_key: {meta_key: g}\n",
        encoding="utf-8",
    )
    with pytest.raises(dbt.ImportProblem) as raised:
        dbt.load(tmp_path / "import.yaml")
    assert raised.value.problems == ["places → primry_key: unknown key — did you mean 'primary_key'?"]
    (tmp_path / "import.yaml").write_text(
        "manifest: missing.json\noutput: x.yaml\nmodel: {name: x}\ntables: [marts, 'a.b']\ndbt_tags: t\n"
        "places:\n  time_column: [{meta_key: a, dbt_tag: b}, {dbt_tag: b, value: 1}]\n",
        encoding="utf-8",
    )
    with pytest.raises(dbt.ImportProblem) as raised:
        dbt.load(tmp_path / "import.yaml")
    assert raised.value.problems == [
        "tables[0]: 'marts' is not schema.table (either part may hold * or ?: marts.*)",
        "places → time_column[0]: give one of `meta_key` (with its `value`) or `dbt_tag`",
        "places → time_column[1]: `value` goes with `meta_key`, not with `dbt_tag`",
        "dbt_tags: needs `vendor_name` — it writes dbt's tags into that vendor's data",
        "manifest: missing.json does not exist (dbt writes it: dbt parse → target/manifest.json)",
    ]


def test_a_pattern_that_matches_nothing_is_named(imported):
    found = imported(tables=["marts.*", "mart.orders"], problems=True)
    assert found == ["tables[1]: 'mart.orders' matches none of the manifest's models, seeds, snapshots, sources"]


# --- what is dbt's own: the manifest ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda m: m["metadata"].update(dbt_schema_version="https://schemas.getdbt.com/dbt/manifest/v9.json"),
         "manifest: 'v9' is not a manifest version read here (v10, v11, v12)"),
        (lambda m: m.pop("metadata"), "manifest: no version is not a manifest version read here (v10, v11, v12)"),
        (lambda m: m.pop("sources"), "manifest: manifest.json is not a dbt manifest (no nodes, sources)"),
    ],
)  # fmt: skip
def test_a_manifest_not_read_is_refused(imported, change, expected):
    assert imported(change, problems=True) == [expected]


def test_a_manifest_that_is_not_json_is_named(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "import.yaml").write_text(json.dumps(BASE), encoding="utf-8")
    importing = dbt.load(tmp_path / "import.yaml")
    with pytest.raises(dbt.ImportProblem) as raised:
        dbt.read_manifest(importing)
    assert raised.value.problems[0].startswith("manifest: manifest.json cannot be read: Expecting property name")


def test_only_the_projects_own_tables_that_exist_are_imported(imported):
    def more(manifest):
        orders = node(manifest, "model.shop.orders")
        for unique_id, changes in [
            ("model.shop.staging_orders", {"name": "staging_orders", "alias": "staging_orders"}),
            ("model.shop.old_orders", {"name": "old_orders", "alias": "old_orders"}),
            ("model.other.orders_of_a_package", {"name": "orders_of_a_package", "package_name": "other"}),
            ("model.shop.orders.v2", {"version": 2, "alias": "orders_v2"}),
        ]:
            manifest["nodes"][unique_id] = {**copy.deepcopy(orders), "unique_id": unique_id, **changes}
        manifest["nodes"]["model.shop.staging_orders"]["config"]["materialized"] = "ephemeral"
        manifest["nodes"]["model.shop.old_orders"]["config"]["enabled"] = False

    document, _ = imported(more)
    assert list(datasets(document)) == ["customers", "dictionaries__countries", "orders", "orders_v2", "raw__events"]
    assert datasets(document)["orders_v2"]["source"] == "marts.orders_v2"


def test_two_tables_making_one_dataset_are_refused(imported):
    def clash(manifest):
        orders = copy.deepcopy(node(manifest, "model.shop.orders"))
        manifest["nodes"]["model.shop.raw__events"] = {
            **orders, "unique_id": "model.shop.raw__events", "name": "raw__events", "alias": "raw__events"}  # fmt: skip

    found = imported(clash, problems=True)
    assert len(found) == 1 and "makes the dataset 'raw__events', as another table does — leave one out" in found[0]


def test_names_sql_cannot_read_bare_are_quoted(imported):
    def odd(manifest):
        events = node(manifest, "source.shop.raw.events")
        events["schema"] = "raw-site"
        columns = events["columns"]
        columns["Order ID"] = {**columns["order_id"], "name": "Order ID"}
        columns["select"] = {**columns["order_id"], "name": "select"}
        columns['say "hi"'] = {**columns["order_id"], "name": 'say "hi"'}
        columns["quoted"] = {**columns["order_id"], "name": "quoted", "quote": True}

    document, _ = imported(odd)
    events = datasets(document)["raw__events"]
    assert events["source"] == '"raw-site".events'
    expressions = {name: item["expression"]["dialects"][0]["expression"] for name, item in fields(events).items()}
    assert expressions["Order ID"] == '"Order ID"' and expressions["select"] == '"select"'
    assert expressions['say "hi"'] == '"say ""hi"""' and expressions["quoted"] == '"quoted"'
    assert expressions["event_date"] == "event_date"


def test_a_relationships_test_of_a_source_is_read(imported):
    def from_source(manifest):
        test = manifest["nodes"]["test.shop.relationships_orders_customer_id__customer_id__ref_customers_.c6ec7f58f2"]
        test["attached_node"] = None
        test["test_metadata"]["kwargs"]["model"] = "{{ get_where_subquery(source('raw', 'events')) }}"
        test["depends_on"]["nodes"] = ["source.shop.raw.events", "model.shop.customers"]

    document, _ = imported(from_source)
    assert "raw__events__customer_id__customers" in [r["name"] for r in document["relationships"]]


def test_metricflow_models_are_pointed_to_ossies_own_converter(imported):
    _, result = imported(lambda m: m.update(semantic_models={"semantic_model.shop.orders": {}}))
    assert result.notes == [
        "the manifest holds 1 MetricFlow semantic models: Ossie's own converter turns them and their metrics into "
        "Ossie (ossie-dbt msi-to-ossie)"
    ]


# --- the document written --------------------------------------------------------------------------------------------


TRICKY = ["no", "on", "yes", "2026-03-01", "1.0", "0x1F", "null", "~", "*star", "&anchor", "# hash", "a: b", "- dash",
          "trailing  \nspaces  \n", "tab\there", "\ttabbed", "ünïcode — …", "'quoted'", '"double"', "", " ", "a\n\nb",
          "\n", "x" * 300]  # fmt: skip


def test_the_document_reads_back_as_built_whatever_it_holds(imported):
    def tricky(manifest):
        orders = node(manifest, "model.shop.orders")
        orders["description"] = "\n".join(TRICKY)
        orders["config"]["meta"] = {"values": TRICKY, "numbers": [0, -1, 1.5, 1e-07, 12345678901234567890, True, None]}
        for index, text in enumerate(TRICKY):
            orders["columns"][f"c{index}"] = {"name": f"c{index}", "description": text, "config": {"meta": {"x": text}}}

    document, _ = imported(tricky, vendor_name="acme")
    orders = datasets(document)["orders"]
    assert orders["description"] == "\n".join(TRICKY)
    assert orders["custom_extensions"][0]["data"]["values"] == TRICKY
    assert orders["custom_extensions"][0]["data"]["numbers"] == [0, -1, 1.5, 1e-07, 12345678901234567890, True, None]
    assert [fields(orders)[f"c{index}"].get("description", "") for index in range(len(TRICKY))] == TRICKY


def test_a_document_that_would_read_back_otherwise_is_never_written(imported, monkeypatch):
    written = yaml.dump
    monkeypatch.setattr(yaml, "dump", lambda *args, **kwargs: written(*args, **kwargs).replace("'1.0'", "1.0"))
    found = imported(lambda m: node(m, "model.shop.orders").update(description="1.0"), problems=True)
    assert found == ['the document written would read back otherwise at datasets[2] "orders" → description — please '
                     "report it"]  # fmt: skip


def test_a_document_ossie_refuses_is_never_written(imported):
    found = imported(model={"name": "shop_dbt", "ai_context": {"synonyms": "not a list"}}, problems=True)
    assert len(found) == 1 and found[0].startswith("model.yaml → ai_context"), found


def test_strict_writes_the_data_as_json_strings(imported):
    document, _ = imported(vendor_name="acme", strict=True)
    data = datasets(document)["orders"]["custom_extensions"][0]["data"]
    assert data == '{"layer": "mart", "tags": ["sales"], "owner": "billing team"}'
    models, problems = semantic.check(document, "model.yaml")
    assert not problems and models[0].document["datasets"][2]["custom_extensions"][0]["data"]["layer"] == "mart"


def test_the_command_writes_checks_and_names_problems(tmp_path, capsys):
    (tmp_path / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (tmp_path / "import.yaml").write_text(json.dumps({**BASE, "vendor_name": "acme"}), encoding="utf-8")
    with pytest.raises(SystemExit) as stopped:
        main(["import-dbt", str(tmp_path / "import.yaml"), "--check"])
    assert stopped.value.code == 1 and "model.yaml is not what the import writes now" in capsys.readouterr().err
    main(["import-dbt", str(tmp_path / "import.yaml")])
    out = capsys.readouterr().out
    assert out.startswith("Wrote model.yaml — the Ossie model shop_dbt:\n  4 datasets (2 models, 2 sources), 17 fields")
    main(["import-dbt", str(tmp_path / "import.yaml"), "--check"])
    assert capsys.readouterr().out == "model.yaml is current.\n"
    assert yaml.safe_load((tmp_path / "model.yaml").read_text(encoding="utf-8"))["name"] == "shop_dbt"
    (tmp_path / "import.yaml").write_text(json.dumps({**BASE, "tables": ["nowhere.*"]}), encoding="utf-8")
    with pytest.raises(SystemExit) as stopped:
        main(["import-dbt", str(tmp_path / "import.yaml")])
    assert stopped.value.code == 2 and "matches none of the manifest's" in capsys.readouterr().err


# --- refuted by a second reader --------------------------------------------------------------------------------------

RELATIONSHIP = "test.shop.relationships_orders_customer_id__customer_id__ref_customers_.c6ec7f58f2"


def test_a_quoted_columns_key_and_relationship_name_the_column(imported):
    """dbt writes a column it quotes with its quotes in the key it infers and in a test's column_name."""

    def quoted(manifest):
        orders = node(manifest, "model.shop.orders")
        orders["columns"]["order_id"]["quote"] = True
        orders["primary_key"] = ['"order_id"']
        manifest["nodes"][RELATIONSHIP]["test_metadata"]["kwargs"]["column_name"] = '"customer_id"'
        manifest["nodes"][RELATIONSHIP]["column_name"] = '"customer_id"'

    document, _ = imported(quoted)
    assert datasets(document)["orders"]["primary_key"] == ["order_id"]
    assert fields(datasets(document)["orders"])["order_id"]["expression"]["dialects"][0]["expression"] == '"order_id"'
    assert {"name": "orders__customer_id__customers", "from": "orders", "to": "customers",
            "from_columns": ["customer_id"], "to_columns": ["customer_id"]} in document["relationships"]  # fmt: skip


def test_a_table_of_another_database_is_left_out_and_said(imported):
    document, result = imported(lambda m: node(m, "source.shop.raw.events").update(database="lake"))
    assert "raw__events" not in datasets(document) and result.counts["table of another database"] == 1
    assert "left out: 1 tables of another database than the project's — a connection reads its own" in dbt.summary(
        dbt.Import("x", dbt.ImportFile.model_validate(BASE), pathlib.Path(), pathlib.Path()), result
    )


def test_two_tests_from_one_column_are_two_relationships_whatever_the_order(imported):
    def second(manifest):
        test = copy.deepcopy(manifest["nodes"][RELATIONSHIP])
        test["test_metadata"]["kwargs"]["field"] = "signed_up_on"
        test["unique_id"] = RELATIONSHIP + "2"
        manifest["nodes"][test["unique_id"]] = test

    def second_first(manifest):
        second(manifest)
        manifest["nodes"] = dict(reversed(manifest["nodes"].items()))

    names = [r["name"] for r in imported(second)[0]["relationships"]]
    assert "orders__customer_id__customers" in names and "orders__customer_id__customers__signed_up_on" in names
    assert imported(second)[0] == imported(second_first)[0]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda m: m["nodes"].update(broken=None),
         'manifest → nodes → "broken": not a node as dbt writes one (a mapping with its resource_type)'),
        (lambda m: node(m, "source.shop.raw.events").pop("source_name"),
         'manifest → sources → "source.shop.raw.events": a source table needs its source_name and identifier, as dbt '
         "writes them"),
        (lambda m: node(m, "model.shop.orders").pop("unique_id"),
         'manifest → nodes → "model.shop.orders" → unique_id: required'),
        (lambda m: node(m, "model.shop.orders").update(config="table"),
         'manifest → nodes → "model.shop.orders" → config: input should be a valid dictionary (got \'table\')'),
        (lambda m: node(m, "model.shop.orders").update(columns=[]),
         'manifest → nodes → "model.shop.orders" → columns: input should be a valid dictionary (got [])'),
        (lambda m: node(m, "model.shop.orders")["columns"].update(amount=None),
         'manifest → nodes → "model.shop.orders" → columns → amount: input should be a valid dictionary or instance '
         "of _ColumnShape (got None)"),
        (lambda m: m["nodes"][RELATIONSHIP].update(test_metadata="relationships"),
         f'manifest → nodes → "{RELATIONSHIP}" → test_metadata: input should be a valid dictionary or instance of '
         "_TestMetadataShape (got 'relationships')"),
        (lambda m: m["nodes"][RELATIONSHIP]["test_metadata"].update(kwargs=[]),
         f'manifest → nodes → "{RELATIONSHIP}" → test_metadata → kwargs: input should be a valid dictionary (got [])'),
        (lambda m: m["nodes"][RELATIONSHIP].update(depends_on={"nodes": [["x"]]}),
         f'manifest → nodes → "{RELATIONSHIP}" → depends_on → nodes[0]: input should be a valid string (got [\'x\'])'),
        (lambda m: m.update(semantic_models=5), "manifest → semantic_models: not a mapping, as dbt writes it"),
    ],
)  # fmt: skip
def test_a_manifest_not_as_dbt_writes_it_is_named_never_a_traceback(imported, change, expected):
    assert expected in imported(change, problems=True)


def test_values_no_document_can_hold_are_named_where_they_are(imported):
    def odd(manifest):
        node(manifest, "model.shop.orders")["config"]["meta"]["share"] = float("nan")
        node(manifest, "model.shop.customers")["description"] = "paid \ud800 orders"
        deep = {}
        for _ in range(500):
            deep = {"x": deep}
        node(manifest, "source.shop.raw.events")["config"]["meta"]["deep"] = deep

    found = imported(odd, vendor_name="acme", problems=True)
    assert found == [
        "model.shop.orders → meta → share: nan is a number JSON cannot hold",
        "source.shop.raw.events → meta → deep" + " → x" * 63 + ": nested deeper than 64 levels",
    ]
    found = imported(lambda m: node(m, "model.shop.customers").update(description="a \ud800 b"), problems=True)
    assert found == [
        'datasets[0] "customers" → description: text that is not Unicode — a lone surrogate, \\ud800: write the '
        "character meant"
    ]


@pytest.mark.parametrize("text", ["one\x85two", "a b", "a b", "a\rb", "one\x85two three\r\nfour"])
def test_yamls_own_line_breaks_are_kept(imported, text):
    document, _ = imported(lambda m: node(m, "model.shop.orders").update(description=text))
    assert datasets(document)["orders"]["description"] == text


def test_names_each_database_reads_its_own_way_are_refused(imported):
    def odd(manifest):
        node(manifest, "source.shop.raw.events")["schema"] = "raw\\site"
        orders = node(manifest, "model.shop.orders")
        orders["columns"]["a\tb"] = {**orders["columns"]["amount"], "name": "a\tb"}
        orders["columns"]["a$b"] = {**orders["columns"]["amount"], "name": "a$b"}

    found = imported(odd, problems=True)
    assert found == [
        "model.shop.orders: the name 'a\\tb' holds a backslash or a control character, which each database reads its "
        "own way — rename it, or leave the table out",
        "source.shop.raw.events: the name 'raw\\\\site' holds a backslash or a control character, which each database "
        "reads its own way — rename it, or leave the table out",
    ]
    document, _ = imported(lambda m: node(m, "model.shop.orders")["columns"].update(
        {"a$b": {"name": "a$b", "description": "x"}}))  # fmt: skip
    assert fields(datasets(document)["orders"])["a$b"]["expression"]["dialects"][0]["expression"] == '"a$b"'


def test_a_key_holding_a_mapping_is_never_repeated(imported):
    grain = {"password": "hunter2"}
    found = imported(
        lambda m: node(m, "model.shop.orders")["config"]["meta"].update(grain=grain),
        places={"primary_key": {"meta_key": "grain"}},
        problems=True,
    )
    assert found == [
        "model.shop.orders → meta → grain: a mapping or a number is not a column's name, nor a list of them (places → "
        "primary_key reads it)"
    ]


def test_the_command_never_crashes_nor_says_not_current_for_a_problem(tmp_path, capsys, monkeypatch):
    (tmp_path / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (tmp_path / "import.yaml").write_text(json.dumps(BASE), encoding="utf-8")
    main(["import-dbt", str(tmp_path / "import.yaml")])
    written = (tmp_path / "model.yaml").read_bytes()
    (tmp_path / "model.yaml").write_bytes(written.replace(b"\n", b"\r\n"))
    with pytest.raises(SystemExit) as stopped:  # line ends differ: not current
        main(["import-dbt", str(tmp_path / "import.yaml"), "--check"])
    assert stopped.value.code == 1
    (tmp_path / "model.yaml").write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(SystemExit) as stopped:
        main(["import-dbt", str(tmp_path / "import.yaml"), "--check"])
    assert stopped.value.code == 1
    for output, said in [("import.yaml", "is the importer's own configuration or its manifest"), (".", "is a folder")]:
        (tmp_path / "import.yaml").write_text(json.dumps({**BASE, "output": output}), encoding="utf-8")
        with pytest.raises(SystemExit) as stopped:
            main(["import-dbt", str(tmp_path / "import.yaml")])
        assert stopped.value.code == 2 and said in capsys.readouterr().err
    (tmp_path / "import.yaml").write_text(json.dumps(BASE), encoding="utf-8")
    monkeypatch.setattr(dbt, "build", lambda *_: {}["a defect"])
    with pytest.raises(SystemExit) as stopped:
        main(["import-dbt", str(tmp_path / "import.yaml"), "--check"])
    assert stopped.value.code == 2
    assert "an unexpected error — please report it: KeyError: 'a defect'" in capsys.readouterr().err


def test_a_manifest_nested_too_deep_is_named(tmp_path):
    (tmp_path / "manifest.json").write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    (tmp_path / "import.yaml").write_text(json.dumps(BASE), encoding="utf-8")
    with pytest.raises(dbt.ImportProblem, match="manifest: manifest.json is nested too deep"):
        dbt.read_manifest(dbt.load(tmp_path / "import.yaml"))
