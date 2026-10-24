# Semantic models and vocabularies

A **semantic model** says what a database's tables mean: which table holds which entity, what each column is, how
tables join, how a number is counted. semantic-warehouse-mcp reads semantic models written in
[Apache Ossie](https://ossie.apache.org/), an open format, and serves them to agents as written
(`list_datasets`, `describe_dataset`, `list_metrics`, `describe_metric` — [tools](tools.md)).

A **vocabulary** declares your own metadata — the categories, layers, links to code, owners you add to a model — so
the server can check it and tell agents what each value means.

## Where they are configured

```yaml
vocabularies: [vocabulary/acme.yaml]       # for the whole server: one per vendor

connections:
  - name: shop
    engine: duckdb
    path: data/shop.duckdb
    readable: ["marts.*", "raw.events"]
    semantic_models: [semantic/]           # this connection's models: files and folders
```

`semantic_models` belongs to a connection: each of its models describes tables of that connection. Files and folders
are relative to the configuration file's folder; a folder serves every `.yaml`, `.yml` and `.json` file in it, at any
depth (hidden files and folders left out). The same goes for `vocabularies`, at the top of the configuration.

Models and vocabularies are read and checked once, when the server starts: an edit is served after a restart.
(`semantic-warehouse-mcp --config <file> --check` checks them without serving.)

## An Ossie document

```yaml
version: "0.2.0.dev0"
name: shop
description: Orders and customers of a made-up online shop.
ai_context:
  instructions: Revenue is net of refunds. Never add revenue of different currencies.
datasets:
  - name: orders
    source: marts.orders                   # a table or view of the connection — or a query
    primary_key: [order_id]
    description: One row per paid order; test orders are removed.
    ai_context:
      synonyms: [purchases, sales]
    fields:
      - name: order_date
        expression:
          dialects:
            - { dialect: ANSI_SQL, expression: order_date }
        dimension: { is_time: true }
        datatype: Date
      - name: amount
        expression:
          dialects:
            - { dialect: ANSI_SQL, expression: amount - refunded_amount }
        description: The amount paid, net of refunds.
        datatype: Decimal
    custom_extensions:
      - vendor_name: acme
        data: { tags: [sales], layer: mart }
relationships:
  - { name: orders_to_customers, from: orders, to: customers, from_columns: [customer_id], to_columns: [customer_id] }
metrics:
  - name: revenue
    expression:
      dialects:
        - { dialect: ANSI_SQL, expression: SUM(orders.amount) }
    description: Revenue net of refunds, in the order's currency.
```

The whole example, with a vocabulary, is in [`examples/semantic/shop.yaml`](../../examples/semantic/shop.yaml) and
[`examples/vocabulary/acme.yaml`](../../examples/vocabulary/acme.yaml). Ossie's own
[specification](https://github.com/apache/ossie/blob/main/core-spec/spec.md) says what every key means.

### Versions

| `version` | Shape | Checked against |
|---|---|---|
| `0.2.0.dev0` | one model per document: `name`, `datasets`, `relationships`, `metrics` at the top | Ossie's `ossie-schema.json`, bundled with the server |
| `0.1.1` | released as OSI: models in a list under `semantic_model`, each read as a model of its own | OSI's `osi-schema.json` (tag `osi-0.1.1-rc1`), bundled |

A document without `version`, or with a version not in this table, is refused, naming it. In `0.1.1`, `vendor_name`
is one of a closed list (`COMMON`, `SNOWFLAKE`, `SALESFORCE`, `DBT`, `DATABRICKS`, `GOODDATA`): a vendor name of your
own needs `0.2.0.dev0`.

### The one difference from strict Ossie

Ossie writes a `custom_extensions` entry's `data` as a JSON **string**. semantic-warehouse-mcp also reads it written as
an ordinary YAML **mapping** — readable and checkable by hand:

```yaml
custom_extensions:
  - vendor_name: acme
    data: '{"tags": ["sales"], "layer": "mart"}'   # strict Ossie: read as it is
  - vendor_name: acme
    data: { tags: [sales], layer: mart }           # a YAML mapping: read as the JSON string it stands for
```

Both are served the same way, as data — numbers in a JSON string exact, as every answer keeps them (a decimal a JSON
number cannot hold comes as a string). A string that is not JSON is served as the string — unless its vendor has a
vocabulary, which cannot check it, and the server refuses to start.

## The checks at start

Every problem stops the server, all of them named at once, each with where it is:

```text
semantic-warehouse-mcp: the configuration config.yaml has 2 problems:
  - connections[0] "shop" → semantic_models → semantic/shop.yaml → datasets[0] "orders" → fields[4] "amount" → datatype: 'Money' is not one of ['String', 'Integer', 'Decimal', …]
  - connections[0] "shop" → semantic_models → semantic/shop.yaml → datasets[0] "orders" → custom_extensions[0] (acme) → tags: 'sale' is not one of its values — did you mean 'sales'?
```

1. **The schema of its version** — keys, types, required entries, the closed lists (`datatype`, `dialect`). The first
   20 problems of a file are named, and how many more there are. Before it, the document must hold only what JSON
   can: a YAML `!!set` or `!!binary`, a number that is not finite (`.inf`), or two keys that read as one (`1` and
   `'1'`) are refused, naming where. A file or a folder that holds no model is refused too.
2. **Ossie's rules beyond the schema**, as Ossie's own validator checks them: no two datasets, metrics or
   relationships of a model share a name, nor two fields or metrics of a dataset (a dataset's fields and metrics share
   one namespace); a relationship's `from` and `to` are datasets of the model, with as many `from_columns` as
   `to_columns`. No two models of one connection share a name.
3. **Bound to its connection** — a dataset's `source` is a table name (`schema.table`, the connection's own catalog
   allowed in front) or a query starting with `SELECT` or `WITH`; anything else is refused. A table must be readable
   on the connection (`readable`) and exist there; a query must pass [the guard](guard.md), as a query an agent sends
   would — a large table in it bounded, nothing outside `readable`.
4. **Your own metadata, against its vocabulary** — below.

## Vocabularies

Anything of your own — categories, a layer, where the code that writes a table lives, an owner — goes in Ossie's place
for it: `custom_extensions`, on the model, a dataset, a field, a metric or a relationship, under your own `vendor_name`.
The server knows no such key: a vocabulary declares them.

```yaml
# vocabulary/acme.yaml
vendor_name: acme
unknown_keys: refuse

keys:
  tags:
    description: What a table or a column is about.
    levels: [dataset, field]
    type: list
    in_lists: true
    values:
      sales: Paid orders and their refunds.
      money: An amount of money, in the currency of its row.
  layer:
    levels: [dataset]
    in_lists: true
    values: { raw: As the application sent it., mart: Business-ready. }
  written_by:
    description: Where the data of this table comes from in the application's code.
    levels: [dataset]
    type: list
```

| Key | | What |
|---|---|---|
| `vendor_name` | required | the `vendor_name` whose data it declares — one vocabulary per vendor on a server |
| `unknown_keys` | | `refuse` (the default): a key the vocabulary does not declare stops the server; `allow`: it passes unchecked |
| `keys` | required | the keys, each by its name, at least one |

Each key:

| Key | Default | What |
|---|---|---|
| `description` | | what the key means — served to agents beside every value of it |
| `levels` | every level | where it may be written: `model`, `dataset`, `field`, `metric`, `relationship` |
| `type` | `string` | `string`, `list`, `mapping`, `number`, `boolean`, or `any` |
| `values` | | a closed list: a mapping of each value to its definition, or a list of values. A `string` must be one of them; each item of a `list` too |
| `in_lists` | `false` | shown in `list_datasets` and `list_metrics`, not only in `describe_dataset` and `describe_metric` |

A `list` or `mapping` without `values` holds anything inside: lists of links, nested mappings — written as you write
them, served as written.

**Checked at start**: every `custom_extensions` entry of a vendor with a vocabulary — a key it does not declare (with
the likely one, for a typo), a key on a level it is not declared for, a value of the wrong type, a value outside its
closed list (with the likely one). A vendor without a vocabulary passes as written, unchecked — so a `vendor_name`
that differs from a declared one only in case or spaces (`ACME` for `acme`) is refused rather than let through. A value
under a key whose name suggests a secret (`…_url`, `…token`, `password`) is never repeated in a problem.

**Served with its definitions**: an answer that holds your own metadata carries a `vocabulary` beside it — for each
vendor, each key used with its `description`, and the definition of each value the answer uses (not the whole list):

```json
{"vocabulary":{"acme":{"tags":{"description":"What a table or a column is about.",
  "values":{"sales":"Paid orders and their refunds."}}}}}
```

An agent reads what a category means, not only its name.
