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
