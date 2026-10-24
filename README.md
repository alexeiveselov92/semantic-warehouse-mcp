# semantic-warehouse-mcp

A read-only [MCP](https://modelcontextprotocol.io) server that lets any AI agent work with SQL databases
the way an analyst does: knowing what the data means. You give it your semantic model in an open format
([Apache Ossie](https://ossie.apache.org/), formerly OSI), your own metadata, context and documents — and
it lets agents find the right tables, read what they mean and query them, safely.

> **Status: pre-alpha** — working, and used on real warehouses; its interfaces may still change. Metrics, saved query
> sets, skills and more databases are on their way ([the plan](https://github.com/alexeiveselov92/semantic-warehouse-mcp/blob/main/dev/PLAN.md)).

## What it does now

- **ClickHouse and DuckDB**, configured in one YAML file: the tables agents may read, the large tables a query must
  bound, limits per query; secrets by variable name, never written in it.
- **Read-only, checked twice**: one `SELECT` at a time, parsed for the database's own dialect by
  [sqlglot](https://github.com/tobymao/sqlglot) and by the database itself; only the tables you allow, wherever they
  are read; large tables bounded; rows and time capped, never cut without saying so — on top of a read-only database
  user. Every answer carries the SQL as it ran and when.
- **A semantic layer in Apache Ossie**: datasets, fields, relationships and metrics, with `ai_context` for agents and
  `custom_extensions` for anything of your own — categories, layers, links to code, owners — checked against a
  vocabulary you declare and served with what each value means.
- **An importer from dbt**: `semantic-warehouse-mcp import-dbt` writes your dbt project's tables, descriptions, `meta`
  and tags as an Ossie model — the mapping yours, in its configuration.
- **Your own context**: instructions and documents served to agents as written.
- **Any MCP client**: stdio for one person's agent, Streamable HTTP for a server a team shares (a token on every
  request, `/health`, a container image).

## Install

```bash
uvx semantic-warehouse-mcp --config config.yaml --check     # check a configuration, reach every database
uvx semantic-warehouse-mcp --config config.yaml             # serve over stdio
pip install semantic-warehouse-mcp                          # or install it
```

Connect Claude Code (any client that runs stdio servers takes the same command):

```bash
claude mcp add warehouse -- uvx semantic-warehouse-mcp --config /path/to/config.yaml
```

## Documentation

- [Getting started](https://github.com/alexeiveselov92/semantic-warehouse-mcp/blob/main/docs/getting-started.md) — from
  nothing to an agent answering about a made-up shop's data.
- [The guide](https://github.com/alexeiveselov92/semantic-warehouse-mcp/blob/main/docs/README.md): tools,
  configuration, semantic models and vocabularies, the dbt importer, the guard, engines, running it.

## Why

An agent with a bare SQL connection writes plausible queries over the wrong tables. What makes its answers
right is semantics: what a table holds, which column is the date, how a metric is defined, what not to mix.
That knowledge already exists in teams — in dbt projects, semantic layers, wikis — but every tool wants it in
its own shape. This server reads it in an open, vendor-neutral format and serves it beside the data.

## Development

Python 3.12, [uv](https://docs.astral.sh/uv/), the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run python examples/data/make_shop.py    # the made-up shop the examples describe
uv run semantic-warehouse-mcp --config examples/config.example.yaml   # the server over stdio
```

## License

[Apache-2.0](https://github.com/alexeiveselov92/semantic-warehouse-mcp/blob/main/LICENSE).
