# Engines

The databases semantic-warehouse-mcp reads, each through an adapter: its configuration keys, how it is kept read-only,
and what the guard allows on it. Supported now: ClickHouse and DuckDB. Next: PostgreSQL, MySQL, BigQuery, Snowflake.

Whatever the engine, give the server a database user that can read only the tables agents may read: the engine's own
read-only means and [the guard](guard.md) keep queries from writing, and the user's grants are the line the database
itself holds on what is read.

## ClickHouse

`engine: clickhouse` — a ClickHouse server, over its HTTP interface. Version 24.3 or newer (the server reads each
query's tree with ClickHouse's analyzer).

| Key | | What |
|---|---|---|
| `url` | required | the HTTP address: scheme, host and port — `https://clickhouse.example.com:8443`. No user or password in it |
| `database` | | the database an unqualified table name resolves to; `default` when not given |
| `user_env` | | the environment variable holding the user's name; ClickHouse's `default` user when not given |
| `password_env` | | the environment variable holding the password |

```yaml
connections:
  - name: warehouse
    engine: clickhouse
    url: https://clickhouse.example.com:8443
    database: marts
    user_env: WAREHOUSE_USER          # the variable's name — its value never appears in a file, a log or an answer
    password_env: WAREHOUSE_PASSWORD
    readable: ["marts.*", "dictionaries.*", "raw.events"]
    large_tables:
      - { table: raw.events, bound_by: event_date }
```

A variable that is not set stops the server at start, named — its value is never shown.

**Read-only, by ClickHouse's own means.** Every request is an HTTP `GET`, which ClickHouse runs read-only whoever the
user is: no `INSERT`, `ALTER`, `CREATE`, `DROP` or `TRUNCATE` runs, even as a user allowed to write.

**The user: `readonly = 2`, not `1`.** A user whose profile sets `readonly = 1` may change no setting, so the server
cannot give its queries their limits (below) — it stops at start, saying so. Give the read-only user `readonly = 2`
(`ALTER USER agent SETTINGS readonly = 2`): it still writes nothing — every request is a `GET`, and its grants (`GRANT
SELECT ON marts.* TO agent`) hold what it may read — and it takes the server's limits.

**Limits that never cut silently.** Every limit ClickHouse has is sent with every query, and with it the mode that
**refuses** on overflow rather than answering with part of the data: reading, sorting, grouping, joins, sets,
transfers and time. A user's own profile that would cut silently (`read_overflow_mode = 'break'`) is overridden — such a
profile answers a sum over part of a table that looks like the whole; through this server, the query is refused with
ClickHouse's own message instead. The answer's rows are the one exception, and it is safe: the server asks for one row
more than the connection's `rows`, so a cut is always seen and said (`truncated`). A query past `seconds` is stopped.

**The engine's own reading.** Before a query runs, ClickHouse explains it (`EXPLAIN QUERY TREE`): it must read one
`SELECT`, and every table ClickHouse says it reads — including a table in `x IN (db.table)` that looks like a column —
is held to `readable` and the large-table bound, like the tables the guard found.

**Names.** The default schema is `database`; names are case-sensitive, as ClickHouse resolves them. ClickHouse has no
catalogs: a three-part name is refused.

**Allowed table functions**: `numbers`, `generateSeries`. Every other — `url`, `s3`, `remote`, `file`, `mysql`,
`cluster`, `merge`, … — is refused.

**Dictionaries and Join tables.** `dictGet…`, `dictHas`, `dictIsIn` and `joinGet…` read the dictionary or table named in
their first argument: the name must be a string, and the dictionary readable (`dictionaries.*` in the example).

## DuckDB

`engine: duckdb` — a DuckDB database file.

| Key | | What |
|---|---|---|
| `path` | required | the database file, relative to the configuration file's folder (or absolute). It must exist when the server starts |

```yaml
connections:
  - name: shop
    engine: duckdb
    path: data/shop.duckdb
    readable: ["marts.*"]
```

**Read-only, by DuckDB's own means.** The file is opened read-only for every query and closed after it, so the
server holds no lock between queries. Each connection is opened with external access off (no reading or writing files
or URLs, no `ATTACH`), no extensions installed or loaded on demand, no Python variables read as tables — and its
configuration locked, so a query cannot switch any of it back. Even a query past the guard could not change the file
or reach another one.

**Names.** The default schema is `main`; names are case-insensitive, as DuckDB resolves them. A three-part name may name
the file's own catalog (`shop.marts.orders` for `shop.duckdb`) and no other.

**Allowed table functions**: `range`, `generate_series`, `unnest`. Every other table function is refused.

**The engine's own reading.** DuckDB parses every query that passed the guard; it must read one `SELECT`
(`engine_parse` otherwise).

**Limits.** Rows past `rows` are never fetched. A query past `seconds` is interrupted.
