# Engines

The databases semantic-warehouse-mcp reads, each through an adapter: its configuration keys, how it is kept read-only,
and what the guard allows on it. Supported now: ClickHouse, PostgreSQL and DuckDB. Next: MySQL, BigQuery, Snowflake.

Whatever the engine, give the server a database user that can read only the tables agents may read: the engine's own
read-only means and [the guard](guard.md) keep queries from writing, and the user's grants are the line the database
itself holds on what is read.

## Drivers

Each engine's driver comes with an extra of its own — install the ones you use, or every one:

```sh
pip install 'semantic-warehouse-mcp[postgres]'           # PostgreSQL (psycopg)
pip install 'semantic-warehouse-mcp[duckdb]'             # DuckDB
pip install 'semantic-warehouse-mcp[all]'                # every engine's driver
uvx --from 'semantic-warehouse-mcp[postgres]' semantic-warehouse-mcp --config config.yaml
```

ClickHouse needs none: the server reaches it over HTTP (`[clickhouse]` exists, and installs nothing more). A connection
whose engine's driver is missing stops the server at start, naming the extra to install. The
[image](running.md#the-image) holds every driver.

## ClickHouse

`engine: clickhouse` — a ClickHouse server, over its HTTP interface. Tried on 22.11, 25.8 and 26.3. The server reads
each query's tree with ClickHouse's analyzer, asking for it resolved (`EXPLAIN QUERY TREE run_passes = 1` — what makes
the analyzer of a server before 24.3, where it is experimental, resolve it too); a tree it cannot resolve is refused,
never taken for a reading that found no table. A setting a server does not know (an older one lacks some limits) is
not sent to it.

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

**Limits that never cut silently.** Every limit ClickHouse has is sent with every query, and with it the mode that
**refuses** on overflow rather than answering with part of the data: reading, sorting, grouping, joins, sets,
transfers and time. A user's own profile that would cut silently (`read_overflow_mode = 'break'`) is overridden — such a
profile answers a sum over part of a table that looks like the whole; through this server, the query is refused with
ClickHouse's own message instead. The answer's rows are the one exception, and it is safe: the server asks for one row
more than the connection's `rows`, so a cut is always seen and said (`truncated`). A query past `seconds` is stopped.

**A user with `readonly = 1`** — the usual read-only user — may change no setting, so none of those limits can be
sent: its queries keep its profile's own. The server checks that profile at start and bounds the queries itself:

- a profile that would cut silently (a limit set with a mode other than `throw`) stops the server at start, naming the
  setting — set the mode to `throw`, or give the user `readonly = 2`, which takes the server's limits and still writes
  nothing;
- the answer is read as it comes, one row more than the connection's `rows` and no further — then the query is stopped;
- a query past `seconds` is stopped by the server (`KILL QUERY`, the user's own query — which needs `SELECT` on
  `system.processes`). A user that may not stop its queries, and whose profile sets no time limit, stops the server at
  start: a query past its time would run on.

**The engine's own reading.** Before a query runs, ClickHouse explains it (`EXPLAIN QUERY TREE run_passes = 1`): it must read one
`SELECT`, and every table ClickHouse says it reads — including a table in `x IN (db.table)` that looks like a column —
is held to `readable` and the large-table bound, like the tables the guard found.

**Names.** The default schema is `database`; names are case-sensitive, as ClickHouse resolves them. ClickHouse has no
catalogs: a three-part name is refused.

**Allowed table functions**: `numbers`, `generateSeries`. Every other — `url`, `s3`, `remote`, `file`, `mysql`,
`cluster`, `merge`, … — is refused.

**Dictionaries and Join tables.** `dictGet…`, `dictHas`, `dictIsIn` and `joinGet…` read the dictionary or table named in
their first argument: the name must be a string, and the dictionary readable (`dictionaries.*` in the example).

## PostgreSQL

`engine: postgres` — a PostgreSQL server (tried on 17). Needs the `postgres` extra.

| Key | | What |
|---|---|---|
| `host` | required | the server's address |
| `port` | | `5432` when not given |
| `database` | required | the database to read — its schemas are the `schema` of `schema.table` |
| `user_env` | required | the environment variable holding the user's name |
| `password_env` | | the environment variable holding the password |
| `sslmode` | | as libpq has it: `disable`, `allow`, `prefer` (when not given), `require`, `verify-ca`, `verify-full` — `require` or `verify-full` for a server across a network |

```yaml
connections:
  - name: warehouse
    engine: postgres
    host: postgres.example.com
    database: analytics
    user_env: WAREHOUSE_USER          # the variable's name — its value never appears in a file, a log or an answer
    password_env: WAREHOUSE_PASSWORD
    sslmode: verify-full
    readable: ["marts.*", "raw.events"]
    large_tables:
      - { table: raw.events, bound_by: event_date }
```

**Read-only, by PostgreSQL's own means.** Every query runs in a transaction opened read-only — and the session's
default made read-only too, from its first statement — so no table, sequence or setting is written, even by a function
that writes, even as a user allowed to. What a read-only transaction does not stop, the guard refuses by name: the
functions that maintain an index (`brin_summarize_new_values`, `gin_clean_pending_list`, … — they write to it in a
read-only transaction too), read the server's files (`pg_read_file`, `lo_import`, …), run SQL from a string past the
guard (`query_to_xml`, `crosstab`, `ts_stat`, `dblink`), signal or stop other sessions, take locks, sleep, change a
setting (`set_config`), export a snapshot, or show other sessions and the statistics of any table (`pg_stat_get_*`).

**The user.** The server stops at start for a superuser; for a member of `pg_read_server_files`,
`pg_write_server_files` or `pg_execute_server_program` — they reach the server's files and programs; and for a user
that owns anything in the database, or inherits from its owner — an owner may maintain what it owns even in a
read-only transaction. Give the server a role that owns nothing and may only read what agents may — `GRANT USAGE` on
the schemas, `GRANT SELECT` on the tables.

The guard holds every query to `readable`; the user's grants hold everything else. A function defined in the database
runs with the user's grants, where the guard cannot see inside it — so `--check` (and the server's log at start) says
when the user may read tables `readable` leaves out, naming them, and when it holds `pg_read_all_data` or `BYPASSRLS`,
which read every table and every row whatever the grants and row security say. Neither stops the server: the grants
are yours to narrow. What the catalog says of any table — its size, a view's or a function's definition — is metadata,
not its rows, and is not refused.

**Limits.** A query past `seconds` is stopped by the server (its statement timeout), and by the connection past a few
seconds more. Rows are read through a cursor on the server, one past the connection's `rows` and no further — a query
that would return millions makes only those.

**The engine's own reading.** Before a query runs, PostgreSQL plans it (`EXPLAIN`, never run, in the same read-only
transaction): a query it cannot plan is refused with its own words (`engine_parse`), and every table the plan reads is
held to `readable` and the large-table bound, like the tables the guard found. A plan reads a view's own tables in its
place: those of the views the query names — each one readable — are its own business, and are not held to `readable`
(a readable view over a table that is not serves what the view shows). A partition is read as the table it belongs to.

**Names.** The default schema is the user's own (`current_schema()`, usually `public`); names are case-insensitive
unless quoted, as PostgreSQL resolves them. A three-part name may name the connection's own database and no other.

**Allowed table functions**: `generate_series`, `generate_subscripts`, `unnest`, `regexp_split_to_table`, and the
JSON ones that unfold a value (`json_each`, `jsonb_each`, `json_array_elements`, … and their `_text` forms). Every
other table function is refused.

## DuckDB

`engine: duckdb` — a DuckDB database file. Needs the `duckdb` extra.

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
