# Design — semantic-warehouse-mcp

The working design. A decision that changes it goes to [DECISIONS.md](DECISIONS.md) first.

## What it is

One MCP server over one or more SQL databases. A YAML configuration names the **connections**, the
**semantic models**, the **context and skills** folders, the **saved query sets** and the **limits**. It
never writes to a database and never re-tells the semantics: what the data means is the model's and the
documents' own, served as they are.

## The configuration

```yaml
connections:
  - name: shop
    engine: clickhouse                 # an adapter: clickhouse, postgres, mysql, bigquery, snowflake, duckdb, …
    url: https://clickhouse.example.com:8443
    user_env: SHOP_DB_USER             # variables holding the secrets — never the secrets
    password_env: SHOP_DB_PASSWORD
    readable: ["marts.*", "dictionaries.*"]
    large_tables:                      # a query over one of these must bound this column
      - { table: "raw.events", bound_by: event_date }
    limits: { rows: 500, seconds: 30 }

semantic_models: ["semantic/*.yaml"]   # Apache Ossie documents; checked against its schema at start
context: ["context/"]                  # markdown documents an agent reads with the data
skills: ["skills/"]                    # folders holding a SKILL.md each
query_sets: ["queries/*.yaml"]         # saved, parameterised SQL
```

## The semantic layer — Apache Ossie

[Apache Ossie](https://ossie.apache.org/) (formerly Open Semantic Interchange, OSI; spec 0.1 in January 2026,
0.2 in draft) — a vendor-neutral YAML/JSON format from Snowflake, dbt Labs, Salesforce and others:

- `datasets[]` — `name`, `source` (a table, view or query), `primary_key`, `fields[]` (`expression` with
  per-dialect variants, `dimension.is_time`, `label`, `description`, `datatype`), dataset-scoped `metrics`;
- `metrics[]` — `name`, `expression` (per dialect), `description`, `datatype`;
- `relationships[]` — `from`, `to`, `from_columns`, `to_columns`;
- **`ai_context`** on every level — a string, or `instructions`, `synonyms`, `examples`;
- **`custom_extensions`** on every level — `vendor_name` and free `data`: anything of your own (a link to the
  code that writes a table, an owner, a refresh schedule) travels to agents as it is.

Ossie's dialects are `ANSI_SQL`, Snowflake, BigQuery, Databricks and BI languages — not ClickHouse, PostgreSQL,
MySQL or DuckDB. So: an expression is written in ANSI SQL and translated to the connection's dialect with
sqlglot; where that isn't enough, an engine-specific expression rides in a `custom_extensions` entry of our own
vendor name. (An open question until the first real model is written — `DECISIONS.md` then.)

**Importers**: a dbt project's descriptions (`manifest.json`, `catalog.json` — model and column descriptions,
`meta`) and its semantic models and metrics into Ossie; Ossie's own converters first, where they do the job.

## Populations, filters and metric definitions — the metrics layer

Ossie describes datasets, fields, relationships and simple metric expressions. A production metrics registry needs
more (`RESEARCH.md`, "What a production metrics registry needs"), and Ossie 0.2 has no filters on metrics at all.
So the metrics layer is **ours — an open, documented, versioned YAML beside the Ossie model**, referring to its
datasets and fields, flexible enough to hold such a registry whole:

- **Populations** (drivers) — who a metric is computed on: a dataset, its time column, its grain key (`[user_id,
  event_date]`), the predicates that always apply, the switches a person may turn on, the longest window one query may
  scan, and the dimensions it may be broken down by — each marked whether it **partitions** the population (one group
  per member: only then may a distinct count be summed across groups).
- **Filters** — defined once (a kind: flag, date, single or multi value; how to list its values; a default), and
  **bound per population**: which column on that population, and **where the predicate goes** — on the population
  (filter the people, then join their events) or on the event side. A filter not bound to a metric's population is
  **refused**, never replaced by a look-alike column. This is how every metric — money from purchases too — is filtered
  through another entity (the people present that day, with their attributes).
- **Metrics** — a kind (additive, distinct count, ratio, distribution), a unit, **how it rolls up** over time and over a
  dimension (sum, recompute from components, sum only over a partitioning dimension, none), components for a ratio, the
  population and the event dataset, a shape (one table, a population joined to events per grain key, a cohort), a grain
  (by event time, or by cohort), parameters (grain, time zone, filters, one dimension, the metric's own — a currency),
  rules for agents to read before using it, aliases in the languages people ask in, recorded variants, and a verified
  stamp. Its SQL: compiled from the structure, **or a template with the layer's macros** (the population, the event
  side, the join keys, the grouping) rendered for the connection's dialect — for what structure can't say.
- **Presets** — named sets of parameter values ("as the dashboard has it saved"): reproducing, never a second definition.
- **Scenarios** — synthetic rows and the exact expected result per metric, run in tests: a definition change that moves
  a number fails a scenario first.

The registry described in `RESEARCH.md` must be expressible in this layer without loss — the first check of its design.
Ossie stays the model of the data; an Ossie document can carry the layer in `custom_extensions` for interchange.

## Tools (all read-only)

| Tool | What |
|---|---|
| `list_connections` | the databases, their engine, what they hold |
| `list_datasets`, `describe_dataset` | the semantic model's datasets — fields, keys, relationships, `ai_context`, `custom_extensions` — with the live table's columns beside |
| `list_metrics`, `describe_metric` | metrics with their definitions |
| `query_metric` | a metric by dimensions, filters and a time range — SQL compiled from the model, run through the guard |
| `run_query` | one read-only `SELECT`, through the guard |
| `get_context`, `list_skills`, `get_skill` | the documents and skills, as written |
| `list_query_sets`, `run_saved_query` | saved, parameterised SQL — a dashboard's substance |

Every answer carries its provenance: the connection, the SQL as it ran, the time, the rows, whether it was cut.

## The guard

The database's own read-only user is the line that holds; the guard makes refusals clear and cheap:

- the query parsed with sqlglot **in the connection's dialect**; exactly one statement; `SELECT` / `WITH` only —
  no DDL, DML, `SET`, `INTO`, no engine functions that read files or the network (a per-engine deny-list);
- every table it reads is `readable`; a `large_tables` table is bounded by its column;
- rows and time capped by the engine's own settings where it has them (and the answer says when it was cut);
- the session read-only by the engine's own means (ClickHouse over GET with `readonly`, PostgreSQL
  `READ ONLY` transactions, …);
- a self-check at start: probes with known verdicts — a dialect the parser misreads is refused, not trusted.

## Saved query sets ("dashboards")

```yaml
name: weekly-sales
parameters:
  - { name: country, type: string, default: null }
  - { name: since, type: date, default: "today - 7" }
queries:
  - name: revenue_by_day
    connection: shop
    sql: "SELECT order_date, sum(amount) AS revenue FROM marts.orders WHERE order_date >= {since} …"
```

Parameters are bound by the driver, never pasted into the text. No rendering: an agent runs the set and shows
the numbers its own way.

## Transports and clients

stdio for one person's agent; Streamable HTTP for a shared server (a bearer token, or behind the
organisation's gateway). Checked with more than one client.

## Documentation, distribution and hosting

- **Documentation**: `docs/` is the single source of the public documentation. A site is built from it with Astro +
  Starlight (`website/`, the pattern of the author's detectkit: a `sync-docs` script imports `docs/` at build time,
  served by nginx from an image) at `semantic-warehouse.pipelab.dev` (proposed).
- **Distribution**: the package on PyPI (`uvx semantic-warehouse-mcp`); a container image on GHCR
  (`ghcr.io/alexeiveselov92/semantic-warehouse-mcp`); the site's image beside it (`ghcr.io/alexeiveselov92/semantic-warehouse-mcp-web`).
- **Hosting (the author's own)**: the author's server is run as code in `alexeiveselov92/pipelab` — Docker Compose
  behind Traefik; each service a compose entry with its Traefik host rule and TLS; secrets in GitHub, prefixed per
  service; a `deploy-<name>.yml` workflow that pulls and restarts a service on a `repository_dispatch` sent by this
  project's CI. The site is one such service; an instance of the server (for the author's own use, or a public demo
  over made-up data) is another — `/mcp` routed with no redirect, as the author's fluentkit and finkit do. Any other
  installation runs the same image its own way.

## Not in scope (for now)

Writing to databases; a chart or dashboard UI; a semantic-model editor; an LLM inside the server; per-person
access inside the server (a gateway in front does that, or later).
