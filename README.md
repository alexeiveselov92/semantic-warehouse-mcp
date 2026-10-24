# semantic-warehouse-mcp

A read-only [MCP](https://modelcontextprotocol.io) server that lets any AI agent work with SQL databases
the way an analyst does: knowing what the data means. You give it your semantic model in an open format
([Apache Ossie](https://ossie.apache.org/), formerly OSI), your metrics, your own context and skills — and
it lets agents describe, query and compute metrics on any database, safely.

> **Status: pre-alpha.** It reads ClickHouse and DuckDB so far, through read-only tools — connections, tables,
> columns, one `SELECT` at a time through the guard, and your own instructions and context documents. Try it on a made-up shop:
> [getting started](docs/getting-started.md). The plan is in [dev/PLAN.md](dev/PLAN.md), the design in
> [dev/DESIGN.md](dev/DESIGN.md).

## Why

An agent with a bare SQL connection writes plausible queries over the wrong tables. What makes its answers
right is semantics: what a table holds, which column is the date, how a metric is defined, what not to mix.
That knowledge already exists in teams — in dbt projects, semantic layers, wikis — but every tool wants it in
its own shape. This server reads it in an open, vendor-neutral format and serves it beside the data.

## What it will do

- **Any SQL database**: ClickHouse, PostgreSQL, MySQL, BigQuery, Snowflake, DuckDB and more — each through
  an adapter; configured in YAML.
- **A semantic layer in an open format**: datasets, fields, metrics and relationships in Apache Ossie YAML,
  with `ai_context` (instructions, synonyms, examples) for agents and `custom_extensions` for anything of
  your own — links to code, owners, anything — passed to agents as they are. Importers from dbt.
- **Metrics** computed from their definitions — by dimensions, filters and time ranges — not re-invented in
  every query.
- **Your own context and skills**: folders of documents and `SKILL.md` files the agent reads with the data.
- **Saved query sets**: named, parameterised SQL with filters — the substance of a dashboard, without the
  drawing.
- **Read-only, checked**: one `SELECT` at a time, parsed and checked for the database's own dialect, only the
  tables you allow, large tables bounded, rows and time capped — on top of a read-only database user. Every
  answer carries the SQL as it ran.
- **Any MCP client**: stdio or Streamable HTTP — Claude, ChatGPT, Cursor, Codex, any client that speaks MCP.

## Configuration

One YAML file names the connections (secrets by variable name, never written in it), the tables agents may read,
the large tables a query must bound, and the limits — later the semantic models, the context and skills folders.
See [the configuration reference](docs/reference/configuration.md) and [the example](examples/config.example.yaml).

## Development

Python 3.12, [uv](https://docs.astral.sh/uv/), the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk),
[sqlglot](https://github.com/tobymao/sqlglot) for parsing SQL of many dialects.

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run python examples/data/make_shop.py    # the made-up shop the examples describe
uv run semantic-warehouse-mcp --config examples/config.example.yaml   # the server over stdio
```

## License

[Apache-2.0](LICENSE).
