# Research — what exists, and what we take from it

Read 2026-10-24. Re-check before a design decision that leans on it.

## Semantic layer formats

- **[Apache Ossie](https://ossie.apache.org/)** (incubating; formerly Open Semantic Interchange, OSI) — launched
  by Snowflake in September 2025 with Salesforce, dbt Labs, BlackRock, RelationalAI; spec 0.1 released January
  2026 under Apache-2.0, `0.2.0.dev0` in draft ([repository](https://github.com/apache/ossie): `core-spec/spec.md`,
  `spec.yaml`, `ossie-schema.json`, a validator, a Python package). Structure (from `core-spec/spec.yaml`): root
  `version`, `name`, `description`, `ai_context`, `datasets`, `relationships`, `metrics`, `custom_extensions`;
  datasets with `source`, `primary_key`, `unique_keys`, `fields` (`expression.dialects[]`, `dimension.is_time`,
  `label`, `datatype`), dataset metrics; `ai_context` a string or `instructions` / `synonyms` / `examples`;
  `custom_extensions` a list of `vendor_name` + `data`. Dialects: `ANSI_SQL`, `SNOWFLAKE`, `BIGQUERY`, `DATABRICKS`,
  `MDX`, `TABLEAU`, `MAQL`, `SIGMA`, `THOUGHTSPOT`, `DAX`, `HOLISTICS_AQL`, `OSSIE_SQL_2026` — no ClickHouse,
  PostgreSQL, MySQL or DuckDB. Converters ([apache/ossie-converters](https://github.com/apache/ossie-converters)):
  cube, databricks, dbt, gooddata, hex, holistics, honeydew, microsoft, nvidia, omni, polaris, salesforce, sigma,
  snowflake, thoughtspot and others — directions and maturity per converter to be checked.
- **Two shapes so far**: the released 0.1.1 (tag `osi-0.1.1-rc1`, `core-spec/osi-schema.json`) wraps the model
  in a root `semantic_model` with `dialects` and `vendors` beside it; the draft 0.2.0.dev0 (`main`,
  `core-spec/ossie-schema.json`, JSON Schema 2020-12) is flat, its `version` a `const`. In both,
  `custom_extensions[].data` is a string (JSON inside), not an object.
- dbt's semantic layer (MetricFlow semantic models and metrics), Cube's data model, LookML — each its own shape;
  reached through Ossie's converters.

## What a production metrics registry needs

Studied 2026-10-24: a metrics registry built for agents inside a company's dbt project (the company and its data are
not named here; only the concepts). Plain YAML beside the dbt project, rendered by scripts, published to the warehouse
for agents, verified against the company's dashboard. What it has that a semantic format must be able to hold:

- **Populations (bases)** — a table with a time column, a grain key (user and day), predicates that always apply,
  switches the dashboard turns on, a breakdown allowlist with a "partitions the population" flag per dimension, the
  longest window a single render may scan.
- **Filters defined once** — every dashboard variable — mapped to a column **per population**, with where the predicate
  goes (the population subquery, or the event side); money from a purchases table is filtered by user attributes that
  live on the population, joined per user and day; a filter not mapped to a population makes the render refuse.
- **Metric semantics** — kind (additive, distinct count, ratio, distribution), unit, roll-up rules over time and over a
  dimension, ratio components ("weekly ARPU is weekly revenue over weekly visitors, never a mean of daily values"),
  shapes (single table, population joined to events, cohort, direct from the source), grains (event time or
  registration cohort), time-zone-aware buckets, parameters, written caveats for agents, RU/EN aliases, recorded
  variants (where a dashboard panel differs and by how much), a verified stamp (date, window, the SQL's hash).
- **One definition, several targets** — one SQL template rendered for the warehouse (runnable) and for a dashboard's
  panel syntax, from the same body; presets reproduce the dashboard's saved state.
- **Scenarios** — synthetic rows and exact expected results per metric; a lint that renders every metric and checks
  tables and columns against the live warehouse; a smoke run over a short window.

Ossie 0.2 has none of populations, filter bindings, roll-up rules or scenarios — so the metrics layer is ours
(`DESIGN.md`, "Populations, filters and metric definitions").

## Database MCP servers today

- **[MCP Toolbox for Databases](https://mcpservers.org/servers/googleapis/genai-toolbox)** (Google; repository
  renamed `mcp-toolbox`) — many databases through one `tools.yaml`: sources, parameterised SQL tools, toolsets.
  No semantic layer or metrics.
- Small projects pairing YAML semantics with databases: [Atlas](https://skillselion.com/mcp/tool/io.github.AtlasDevHQ/atlas)
  (its own YAML; Postgres, MySQL, ClickHouse, Snowflake, DuckDB, BigQuery; one datasource per server),
  [querywise-mcp](https://glama.ai/mcp/servers/kosminus/querywise-mcp) (glossary, metrics, data dictionary;
  Databricks, Postgres, SQLite), [semlayer](https://glama.ai/mcp/servers/cqvdfglztg) (generates a layer),
  [Précis Finance](https://mcpmarket.com/server/pr-cis-finance) (metrics in YAML over ClickHouse). Each its own
  format; none reads an open one.

**Our place:** the open format (Ossie) as the input, any database through adapters, a guard that checks every
query in its own dialect, your own context and skills served beside the data.

## Lessons carried from the author's earlier platform (design, not code)

- A guard reads the database's own parse of the query (there: ClickHouse's `EXPLAIN AST`) and checks itself with
  probes of known verdicts at start; a server version it misreads gets its queries refused. Here sqlglot parses,
  and the probes still run against the real server where it has its own parser.
- Large raw tables must be bounded by their date column — the most common way an agent's query goes wrong.
- A limit is always sent with its overflow mode; a database profile that silently cuts answers (`break`) makes a
  wrong answer look like a right one — refuse such a connection.
- Every answer carries the SQL as it ran and the time — a wrong answer looks exactly like a right one, so it must
  be checkable.
- Semantics are the owners' — served as written, never rephrased into code or into the server's own words.
- Clients cut long answers (Claude Code near 25,000 tokens): every answer is capped and says so.
