# The dbt importer

A team whose tables are described in a dbt project should not describe them twice. `semantic-warehouse-mcp import-dbt`
reads the project's `manifest.json` and writes an [Apache Ossie](semantic-models.md) document — its tables as datasets,
their columns as fields, descriptions, `meta` and tags as written — which the server then serves like any semantic
model.

```bash
dbt parse                                                   # in the dbt project: writes target/manifest.json
semantic-warehouse-mcp import-dbt import.yaml               # writes the Ossie document import.yaml names
semantic-warehouse-mcp --config config.yaml --check         # the server checks it, with your vocabulary
```

The document is a file: read it, review its changes like any other, commit it beside your configuration. It is
written, never edited by hand — an edit there is overwritten by the next import; change the dbt project, or the
importer's configuration, and import again.

## The importer's configuration

```yaml
# import.yaml — paths relative to this file
manifest: ../analytics-dbt/target/manifest.json
output: semantic/warehouse.yaml

model:
  name: warehouse
  description: The analytics warehouse, as its dbt project describes it.
  ai_context:
    instructions: Never add amounts of different currencies.

tables: ["marts.*", "raw.events"]
vendor_name: acme
dbt_tags: dbt_tags
places:
  time_column:
    - { meta_key: tags, value: timestamp }
  primary_key: { meta_key: grain }
```

| Key | | What |
|---|---|---|
| `manifest` | required | the dbt project's `manifest.json` (`dbt parse` or any dbt command writes it to `target/`) |
| `output` | required | the Ossie document written |
| `model` | required | the Ossie model written: its `name` (required), and `description` and `ai_context` as Ossie has them, written as given |
| `tables` | | which tables, as `schema.table` patterns of the names the database knows them by (`*` and `?` within a part); every table by default. A pattern that matches no table is refused |
| `resources` | | which kinds of dbt resources: `model`, `seed`, `snapshot`, `source` — all by default |
| `vendor_name` | | the `vendor_name` the tables' and columns' `meta` is written under, in `custom_extensions` — as written. Without it, `meta` is left out (the import says how much) |
| `dbt_tags` | | the key of that vendor's data that dbt's own tags (`tags:` in dbt, its run-selection tags) are written under. Without it, they are left out. Needs `vendor_name`; a `meta` that already holds the key is refused |
| `places` | | which of your own keys stand for Ossie's own places — below |

`meta` and dbt's `tags` are different things in many projects: `meta` holds the team's categories, layers, provenance;
`tags` are what `dbt run --select tag:…` selects by. Both are written as they are; a [vocabulary](semantic-models.md#vocabularies)
for `vendor_name` checks them when the server starts — the keys you write there, the closed list of your categories.

### `places`

Some of what a team writes in `meta` means one of Ossie's own places. The importer knows none of your keys: you say
which.

| Key | What |
|---|---|
| `time_column` | the columns that are a time dimension (Ossie's `dimension: { is_time: true }`): a list of marks, any of which marks a column. A mark is `meta_key` with its `value` (the column's `meta` holds it there, or a list there holds it; `value` is `true` when not given), or `dbt_tag` — a dbt tag of the column |
| `primary_key` | the table's primary key: `meta_key`, the key of a table's `meta` naming a column or a list of columns. Where a table's `meta` has no such key, dbt's own primary key is used; a value that is not column names (prose, an empty list) is refused, naming the table |

## What is read from dbt itself

What is dbt's own, not a team's, the importer reads without being told:

| From dbt | Becomes |
|---|---|
| a model's, seed's or snapshot's `schema` and `alias`; a source table's `schema` and `identifier` | the dataset's `source`, `schema.table` — the database (catalog) part left out: the connection's own |
| a model's `name` (a versioned one: `name_v2`); a source table as `<source>__<table>` | the dataset's `name` |
| `description` of a table and of each column | the dataset's and the field's `description`, as written |
| the columns the project describes | the dataset's fields, each an expression of its column — a name SQL cannot read bare (`Order ID`, `select`, `a$b`) in double quotes. The table's other columns are served live by `describe_dataset` (`other_columns`) |
| the primary key dbt infers (from constraints, or a column tested `unique` and `not_null`) | `primary_key` — a column dbt quotes named by its own name, without the quotes dbt writes around it there |
| a model's `event_time`; a column's `granularity` | that column is a time dimension |
| a `relationships` test between two tables imported | an Ossie relationship from the tested column to the column it must be found in, named `<from>__<column>__<to>` (and `__<to's column>` when that column's name differs) — one per pair of columns, whatever order dbt wrote the tests in |

Left out, and said in the import's summary: tables and tests of other packages, disabled models, ephemeral models (no
table of their own), and tables of another database than the one the project's models are built in — a connection
reads its own database, and `raw.events` of another one would be served as the connection's `raw.events`.

Refused, naming where: a manifest not as dbt writes it (a node that is not a mapping, a source table without its
`source_name`, columns that are not a mapping, …); a table or column name holding a backslash or a control character,
which each database reads its own way — rename it or leave the table out with `tables`; and a value no Ossie document
can hold — a number that is not finite (`NaN`, infinity), text that is not Unicode (a lone surrogate), `meta` nested
deeper than 64 levels.
Metrics defined in MetricFlow (`semantic_models` and `metrics` in dbt) are not imported here: Ossie's own converter
does that — [`ossie-dbt msi-to-ossie`](https://github.com/apache/ossie-converters/tree/main/dbt) — and the import names
them when the manifest has them.

The importer reads manifests of dbt 1.6 and later (`v10`, `v11`, `v12`); another version is refused, naming it.

## The document written

```yaml
# Written by semantic-warehouse-mcp import-dbt from manifest.json (dbt 1.10.15).
# Edit the dbt project or the importer's configuration and import again: an edit here is overwritten.
version: 0.2.0.dev0
name: shop_dbt
datasets:
  - name: orders
    source: marts.orders
    primary_key: [order_id]
    description: |-
      One row per paid order; test orders are removed.
    fields:
      - name: amount
        expression:
          dialects:
            - {dialect: ANSI_SQL, expression: amount}
        description: What the customer paid, before refunds, in the order's currency.
        custom_extensions:
          - vendor_name: acme
            data:
              tags: [money]
```

The whole example — a made-up shop's dbt project, its manifest, the importer's configuration and the document it
writes — is in [`examples/dbt/`](../../examples/dbt/); [`examples/config.dbt.example.yaml`](../../examples/config.dbt.example.yaml)
serves it.

Before a document is written, it is read back the way the server reads it — it must read back exactly as built, every
value and its type, and pass every check of its Ossie version — so a document the server would refuse or read
otherwise is never written. Datasets and relationships come sorted by name, so an import's changes are the dbt
project's changes.

## The command

```text
semantic-warehouse-mcp import-dbt <configuration> [--strict] [--check]
```

| Option | What |
|---|---|
| `--strict` | writes each `custom_extensions` data as a JSON string, as strict Ossie does — for a tool that reads strict Ossie only. The server reads both |
| `--check` | writes nothing: exits `0` when the document on disk is, byte for byte, what the import writes now, `1` when it is not — for a check in CI that the committed document is current |

It exits `2` with every problem named at once, each with where it is — the configuration's key, or the dbt node
(`model.shop.orders → meta → grain: …`) — and `2` on an error of its own, never `1`, so a check in CI never reads a
failure as "not current". A value under a key that may name a secret (`password`, `token`, `…_url`) is never repeated
in a problem. `output` may not be the importer's configuration nor its manifest. On success it says what it wrote:

```text
Wrote shop-dbt.yaml — the Ossie model shop_dbt:
  4 datasets (2 models, 2 sources), 17 fields, 2 relationships
  2 primary keys, 3 time columns
  acme's data on 4 datasets and 7 fields
```
