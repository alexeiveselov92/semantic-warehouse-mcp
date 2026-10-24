# Getting started

semantic-warehouse-mcp is pre-alpha: it runs from its source, over ClickHouse and DuckDB, with the tools in
[the reference](reference/tools.md). This page takes you from nothing to an agent answering a question about a made-up
shop's data.

## 1. Get it

You need [uv](https://docs.astral.sh/uv/) and git.

```bash
git clone https://github.com/alexeiveselov92/semantic-warehouse-mcp
cd semantic-warehouse-mcp
uv sync
```

## 2. Build the example database and check the configuration

```bash
uv run python examples/data/make_shop.py
uv run semantic-warehouse-mcp --config examples/config.example.yaml --check
```

The first command builds `examples/data/shop.duckdb`: half a year of a made-up online shop — customers, paid orders,
the raw events of its site. The second reads [the example configuration](../examples/config.example.yaml), reaches the
database, and says it is fine — or names what is wrong ([configuration](reference/configuration.md)).

## 3. Connect an MCP client

**Claude Code**, from the repository's folder:

```bash
claude mcp add shop -- uv run --directory "$PWD" semantic-warehouse-mcp --config "$PWD/examples/config.example.yaml"
```

**Any other client** that runs stdio servers (Claude Desktop, Cursor, Codex, …) takes the same command in its own
configuration — for most, a JSON entry like this, with your own absolute path:

```json
{
  "mcpServers": {
    "shop": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/semantic-warehouse-mcp", "semantic-warehouse-mcp",
               "--config", "/path/to/semantic-warehouse-mcp/examples/config.example.yaml"]
    }
  }
}
```

## 4. Ask

Start a new session in the client and ask about the data, for instance:

> What was the shop's revenue net of refunds in March 2026, by currency?

The agent finds the tables (`list_tables`, `describe_table`) and runs a query (`run_query`). Every answer carries the
SQL exactly as it ran and the time it took, so you can check the number yourself:

```bash
uv run python -c "import duckdb; print(duckdb.connect('examples/data/shop.duckdb', read_only=True).sql('''
  SELECT currency, sum(amount - refunded_amount) FROM marts.orders
  WHERE order_date >= DATE '2026-03-01' AND order_date < DATE '2026-04-01' GROUP BY currency'''))"
```

Try asking for something it must not do — to delete an order, or to read every raw event at once — and see
[the guard](reference/guard.md) refuse it, saying why.

## Your own database

Point a configuration at your own ClickHouse server or DuckDB file ([engines](reference/engines.md)), list the tables
agents may read in `readable`, and name the large ones in `large_tables`. For ClickHouse, name the variables that hold
the user and password (`user_env`, `password_env`) and set them in the client's configuration — never in the file.
