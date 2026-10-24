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
    semantic_models: ["semantic/"]     # Apache Ossie documents of this connection; checked at start

vocabularies: ["vocabulary/"]          # the team's own metadata keys, one vocabulary per vendor, for the whole server
context: ["context/"]                  # markdown documents an agent reads with the data
skills: ["skills/"]                    # folders holding a SKILL.md each
query_sets: ["queries/*.yaml"]         # saved, parameterised SQL
```

Built so far (milestone 1): `connections` and `limits` (`answer_characters`), checked with pydantic models; a
connection's common keys are the server's, the rest its engine's (`EngineSettings` of its adapter), so a typo in either
is named with the likely key. Every problem is reported at once, each naming its entry; a key twice in one mapping is
refused (YAML would keep the last silently); a value under a key that may hold a secret is never repeated. At start
every connection is reached (`adapter.check()`); a wrong one stops the server. The user's guide is
`docs/reference/configuration.md`.

## Adapters

An engine's specifics live in its adapter (`src/semantic_warehouse_mcp/adapters/`), nowhere else. An adapter gives:
its sqlglot dialect, its configuration keys, the default schema and its catalog, the table functions it allows and the
functions it denies, `check()` at start (reach the database, confirm it is read-only), `engine_refusal(sql)` (the
engine's own parse of a query, where it has one), `run(sql, max_rows, seconds)` (read at most `max_rows` rows and say
whether there were more; stop after `seconds`), `tables()` and `columns()`.

- **DuckDB** (built): the file opened read-only per query, external access off, extensions neither installed nor
  loaded, Python replacement scans off, configuration locked; the engine's own parse is `extract_statements`; rows
  fetched `max_rows + 1`; time by `interrupt()` from a timer.
- **ClickHouse** (built — the engine production work runs on): HTTP, every request a `GET`, which ClickHouse runs
  read-only whoever the user is (`readonly=2`: settings may be sent, nothing written); the user and password named by
  `*_env`, sent in headers, never in the URL. Every overflow mode sent as `throw` with every query — a user's profile
  with `read_overflow_mode = 'break'` answered a sum over 8,198 of 100,006 rows with no warning; through the server
  it is refused. The result's rows are the one `break`: `max_result_rows = rows + 1` with `max_result_bytes = 0`, so a
  cut always shows as the extra row (ClickHouse breaks after exceeding, at a block's end). `wait_end_of_query` so an
  error arrives as an error. The engine's own reading is `EXPLAIN QUERY TREE` (the analyzer, 24.3+): it must be a
  `QUERY` or `UNION`, and its `TABLE` nodes are the tables read — `x IN (db.table)` included — checked like the guard's.
  `dictGet…` / `dictHas` / `dictIsIn` / `joinGet…` name a dictionary or Join table in their first argument: the guard
  requires a string and checks it against `readable` (`table_reading_functions`). Table functions: `numbers`,
  `generateSeries` only.
- **DuckDB's own reading** names tables too: `json_serialize_sql` gives its parse tree, whose `BASE_TABLE` nodes are
  the tables read (an unqualified name that is one of the query's CTEs left out).

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

**Your own metadata** (`DECISIONS.md`, "Your own metadata"). Anything a team adds — categories, a layer, links to the
code that writes a table, owners — goes in `custom_extensions` under the team's `vendor_name`, its `data` written as a
YAML mapping or as Ossie's JSON string. A **vocabulary** may declare a vendor's keys:

```yaml
# vocabulary/acme.yaml — the team's, in its repository
vendor_name: acme
unknown_keys: refuse                 # or allow: undeclared keys pass unchecked
keys:
  tags:
    levels: [dataset, field]
    type: list
    values:                          # a closed list, each with its definition — served to agents
      purchase: Purchases and their lifecycle.
      timestamp: A point in time (*_at).
  layer:
    levels: [dataset]
    values: { staging: Cleaned source data., mart: Business-ready tables. }
  written_by:
    levels: [dataset]
    type: mapping                    # free inside: a topic, a repository, a link to the code
    description: Where the data of this table comes from in the application's code.
```

Checked at start (an unknown key or value stops the server, naming the dataset and field); served as written, with the
definitions of the values used. The server has no notion of any key: none is named in code.

**Built (milestone 3, first session)** — `semantic.py`, `vocabulary.py`, the binding in `config.py`; the user's page is
`docs/reference/semantic-models.md`:

- **Loading.** `semantic_models` per connection (files, folders), `vocabularies` at the top only — one vocabulary per
  vendor for the whole server, so a vendor's key means the same in every model and a list spanning connections carries
  one set of definitions. Read and checked once at start: an edit needs a restart (unlike context documents, which
  are not checked and are read anew).
- **Checks, in order**: the schema of the document's own version (bundled, pinned — `schemas/README.md`), after each
  YAML-mapping `data` is turned into the JSON string it stands for; Ossie's rules its own validator checks beyond the
  schema (unique names, relationships between datasets of the model with as many columns each side); no two models of a
  connection with one name; each dataset's `source` held to its connection — a table name resolved by the guard
  (readable) and found in the engine's catalog, a query (it starts with `SELECT`/`WITH`) passed by `guard.check`; each
  extension of a vendor with a vocabulary against it (key, level, type, closed values).
- **Serving.** Lists give the first line of a description (cut at 200, `…` when there is more), synonyms, and only the
  own metadata keys the vocabulary marks `in_lists`; `search` matches every word against every string value of the
  dataset or metric as written. `describe_dataset` puts the live column beside a field whose expression is nothing but
  that column's name (a calculation gets none), lists the table's other columns, and the source table's `bound_by`;
  when it does not fit, parts are made smaller in turn before a field is left out (`answers.render_fitting`).
  `describe_metric` names the fields its expression reads — `dataset.field` in a model metric, a bare name in a
  dataset's own — with their expressions, since a field may be a calculation. Expressions are served as written; the
  dialect question waits for its milestone item.
- **Ossie 0.1.1** closes `vendor_name` to six names; a team's own vendor needs 0.2.
- **Refuted by a second reader** (closed, each with a test): answers past the cap (now the longest values are cut,
  `long_values`, after the items; a list's `vocabulary` of the items shown only); `ai_context` holding YAML values JSON
  cannot hold (refused at start); secrets repeated by YAML errors (line and column only), schema errors (`redact` of a
  URL's password, in every `ConfigError`) and vocabulary checks (values under secret-like keys); tracebacks at start
  (unhashable keys, huge integers, non-UTF-8, `version` not a string); `ACME` slipping past `acme`'s vocabulary; the
  guard's rules built before `check()` learnt DuckDB's catalog; YAML 1.1's `no`/`on`/dates (`files.py` reads booleans
  and dates as YAML 1.2 does); aliases expanding exponentially (capped at a million values); misleading errors for a
  source that is neither a name nor a SELECT. Held: nothing outside `readable` could be read through a model. Found
  by removing protections: the cut of long values looped forever on an answer of many short values (it now knows
  beforehand whether cutting can fit the answer, and every step shortens it).

**Importers**: a dbt project's descriptions (`manifest.json` — model and column descriptions, `meta`, tags) into Ossie,
`meta` passed through as written into `custom_extensions` under a vendor name the configuration gives; which of its keys
become Ossie's own places (a column marked as the time → `dimension.is_time`; a declared grain → `primary_key`) set by
the importer's configuration — the team's — not by code. Its semantic models and metrics through Ossie's own
converters, where they do the job.

Built (milestone 3, second part; `importers/dbt.py`, `docs/reference/dbt-importer.md`):

- **A file, not a live read.** `import-dbt` writes an Ossie document; the server serves it like any model. Before it
  is written it is read back with the server's own reader and must equal what was built, value and type
  (`_difference` names the first place otherwise), then pass `semantic.check`. `--check` compares bytes, for CI.
- **What is dbt's own, read by code**: `schema` + `alias`/`identifier` → `source` (the catalog left out; a table of
  another database than the project's models' is left out and counted, since a connection reads its own); `name`,
  `<source>__<table>` for a source table, `_v<n>` for a version; descriptions as written; the documented columns as
  fields (names SQL cannot read bare double-quoted, names with a backslash or a control character refused); dbt's
  inferred `primary_key`, `event_time`, `granularity`; `relationships` tests (child from `attached_node` or the test's
  `model` argument, parent the other node it depends on), one Ossie relationship per pair of columns; dbt's quotes
  around a column it quotes removed in keys and tests. `meta` and dbt's `tags` read whole — the only dbt field named in
  code, `MANIFEST_TAGS`, is exempted by name in the no-team-key test.
- **What is the team's, configured**: `tables`, `resources`, `vendor_name`, `dbt_tags`, `places` (`time_column` marks
  by a meta key's value or a dbt tag; `primary_key` by a meta key naming columns).
- **Shape first**: the parts of the manifest read are checked against lax pydantic shapes before anything is built, so
  an odd manifest is named, never a traceback; `meta` is walked without recursion (NaN, infinity, lone surrogates,
  nesting past 64 levels refused where they are); any error of the importer's own exits 2, never 1.
- **Writing**: multi-line text as a block, text holding one of YAML's own line breaks (`\r`, NEL, U+2028/9) double
  quoted, short plain lists and mappings on one line, no anchors.
- **Checked on a real catalog** (a scratch copy of a large production dbt project, parsed with its own dbt version,
  nothing committed): 715 datasets, 8,092 fields, 1,766 time columns in 10 s; the server checked them all against a
  vocabulary built from the team's own tag rules in 6 s, and named the one misspelled tag planted, with the likely one.
- **Refuted by a second reader** (closed, each with a test): quoted columns named with their quotes in keys and
  relationships; a source of another database bound to the connection's table of the same name; two tests from one
  column collapsing into one relationship, which one depending on node order; a lone surrogate passing every check,
  then breaking every answer (now refused by `files.read_yaml`, `semantic.check` and the importer; an escaped surrogate
  pair read as its character); tracebacks on odd manifests, paths and outputs; secrets echoed in problems (a mapping
  holding a secret-like key never repeated; a URL's password redacted up to the last `@`); NaN and NEL giving "please
  report it"; `--strict` writing NaN; JSON-string numbers retyped (`1.0` served as `1`); backslashes and `$` in names.

## Instructions and context — the team's own knowledge beside its data

Built (milestone 2, at the author's request: "semantics is good, but agents must also get our own kitchen"). Two kinds,
at two levels — the whole server (top of the configuration) and each connection:

- **`instructions`** — short rules written in the configuration. Delivered at once: appended, as written, to the
  server's instructions an MCP client receives when it connects (framed only by "Instructions for every connection:" /
  "Instructions for the connection `x`:"), and in `list_connections`.
- **`context`** — documents kept where the team keeps them: files and folders, relative to the configuration
  (`.md`, `.markdown`, `.txt`; hidden files and folders left out). Listed by `list_context` (name, title, length),
  read by `get_context` (as written, in parts by `offset` when longer than an answer). Read from disk on every call — an
  edit is served without a restart. A document is reached only by a name the listing gives; no path an agent writes is
  ever opened.
- **Later**: a `context` entry naming a git repository (URL, ref, path; a token by variable name), fetched by the server
  — for a deployed server whose configuration repository does not hold the documents. Skills (`SKILL.md` folders) the
  same way, with their own tools.

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
| `list_connections` | the databases, their engine, description, readable patterns, large tables and limits — from the configuration |
| `list_tables`, `describe_table` | the readable tables and views, their columns and the database's own comments — before a semantic model exists |
| `list_datasets`, `describe_dataset` | the semantic model's datasets — fields, keys, relationships, `ai_context`, `custom_extensions` — with the live table's columns beside |
| `list_metrics`, `describe_metric` | metrics with their definitions |
| `query_metric` | a metric by dimensions, filters and a time range — SQL compiled from the model, run through the guard |
| `run_query` | one read-only `SELECT`, through the guard |
| `get_context`, `list_skills`, `get_skill` | the documents and skills, as written |
| `list_query_sets`, `run_saved_query` | saved, parameterised SQL — a dashboard's substance |

Every answer carries its provenance: the connection, the SQL as it ran, the time, the rows, whether it was cut.

## The guard

The database's own read-only user is the line that holds; the guard makes refusals clear and cheap
(`src/semantic_warehouse_mcp/guard.py`; the user's guide is `docs/reference/guard.md`, every refusal with its code):

- the query parsed with sqlglot **in the connection's dialect**; exactly one statement; a `SELECT` (with `WITH`, set
  operations) at the root; nothing anywhere inside that writes, locks or runs a command (DML in a CTE, `INTO`,
  `FOR UPDATE`); no `SETTINGS` / `FORMAT` of its own; no denied function (the engine's deny-list takes globs, so a
  family — ClickHouse's `dictGet*`, `joinGet*` — is denied whole); a table function only from the engine's allow-list;
- **every table node is a table read** — checked against `readable` wherever sqlglot placed it — unless it is proven a
  CTE's name (sqlglot resolves it to a CTE and it is unqualified). Safe by default: a table in parentheses
  (`FROM (secret.t) x`), which sqlglot wraps as a derived table, was read past an earlier version;
- a `large_tables` table is bounded **from below, on the column itself, against a value** (no column, no subquery), as a
  top-level `AND` (or `NOT (col < …)`) of the `WHERE` / `PREWHERE` of the query that reads it or of its own
  `JOIN … ON`; qualified by the
  alias it is selected under when that query reads more than one table. The guard checks the bound is there, not how
  wide it is;
- sqlglot's own failures while following a query's structure are refusals (`unsupported`), never crashes;
- **the query runs as written** (trailing semicolons dropped), not as sqlglot would regenerate it — a regenerated query
  could differ from the agent's in meaning; instead it is **read twice**: the engine's own parser must also see one
  `SELECT` (`engine_parse`), and the tables it names are held to `readable` and the large-table bound
  (`check_engine_tables`) — a large table only the engine saw has a bound the guard could not check, so it is refused;
- rows and time capped (the adapter's means; the answer says when it was cut, and why);
- the session read-only by the engine's own means (DuckDB: read-only file, external access off; ClickHouse: `readonly`;
  PostgreSQL: `READ ONLY` transactions; …);
- to come: a self-check at start — probes with known verdicts; a dialect the parser misreads is refused, not trusted.

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

Built (2026-10-24, ahead of milestones 8–9, for a company's deployment behind its gateway — `serving.py`,
`docs/reference/running.md`):

- `--http` serves the same `MCPServer` through the SDK's Streamable HTTP app, **stateless and one JSON answer per
  request** — any replica answers any request, nothing held between them.
- **The token**: `--token-env NAME`, read at start, compared in constant time against `Authorization: Bearer …` on
  every request but `GET /health`; required unless the server listens on loopback only, and checked before any database
  is reached. A WebSocket is refused. A gateway's own upstream key is the token; who is asking stays at the gateway.
- **`/health`** answers without a token and touches no database (a readiness probe; a database outage must not restart
  pods).
- **The image** (`Dockerfile`, `.github/workflows/image.yml`): the locked dependencies in a venv, user 10001, a
  read-only root filesystem with `/tmp` writable, port 8000, the configuration at `/config`. Built on every change,
  published to GHCR for a `v*` tag only, as its version and its commit, never `latest`.
- Checked: the SDK's own client and raw HTTP against a real uvicorn (bypass attempts on the path, wrong and partial
  tokens, a WebSocket); the image run read-only and non-root, real Claude Code answering through it over HTTP with
  the token, its numbers checked by a direct query.

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
