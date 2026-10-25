# The guard

Every query an agent sends through `run_query` is read before it runs. The guard passes one read-only `SELECT` over
readable tables, with large tables bounded; anything else is refused with a code, the reason, and how to fix the query:

```text
Refused by the guard (large_table_unbounded): raw.events is a large table: bound event_date from below in the WHERE
of the query that reads it, e.g. WHERE event_date >= <a date> — the column itself, not a function of it.
```

**What holds the line.** The database's own read-only means hold it: a read-only user, a read-only session, files and
the network switched off (see [engines](engines.md) for what each engine does). The guard makes refusals clear and
cheap, keeps queries to the `readable` tables, and keeps large tables bounded. For a line the database itself holds on
which tables are read, give the server a user that can read only those.

## How a query is read

1. **Parsed in the connection's dialect** (DuckDB SQL for a DuckDB connection, ClickHouse SQL for ClickHouse) with
   [sqlglot](https://github.com/tobymao/sqlglot). What it cannot parse is refused, not guessed at.
2. **One statement**, and it is a `SELECT` — with `WITH`, `UNION` / `INTERSECT` / `EXCEPT`, joins and subqueries.
3. **Nothing inside writes, locks or runs a command** — not in a CTE, not in a subquery.
4. **No settings or output format of its own** — the connection's limits are the server's.
5. **Functions**: a table function is read from only when the engine allows it (DuckDB: `range`, `generate_series`,
   `unnest`); functions that reach files, the network or other servers are refused.
6. **Every table it reads is `readable`** — wherever it is read: `FROM`, `JOIN`, a subquery, a CTE, `IN (SELECT …)`.
   A table name without a schema is in the engine's default schema. A name in another catalog is refused.
7. **Every large table is read alone in its `SELECT`, and bounded there** — see
   [`large_table_joined`](#large_table_joined) and [`large_table_unbounded`](#large_table_unbounded) — and held to
   named values of the columns it is narrowed by, see [`large_table_unnarrowed`](#large_table_unnarrowed).
8. **The engine's own parser agrees**, where the engine has one to ask: one statement, a `SELECT` — and the tables the
   engine says it reads are held to the same rules (6 and 7), so a table the guard's parser took for something else
   is still checked.

A query that passes runs **as written** — the text the agent sent, without trailing semicolons — and the answer shows it
in `sql`.

## Refusals

### `empty`

Nothing to run: the query is blank, or only comments and semicolons.

### `unparsable`

The query cannot be read in the connection's SQL dialect. The refusal names where the parser stopped. Write it in the
connection's dialect (`list_connections` shows the engine).

### `not_one_statement`

More than one statement — `SELECT 1; SELECT 2`. Send one `SELECT` at a time.

### `not_a_select`

The statement is not a `SELECT`: `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`, `ALTER`, `SET`, `ATTACH`, `COPY`,
`PRAGMA`, `EXPLAIN`, `DESCRIBE`, `SUMMARIZE`, `SHOW`, `SYSTEM` and the like. Use `describe_table` to see a table's
columns.

### `not_read_only`

A `SELECT` that holds something which writes, locks or runs a command: `WITH d AS (DELETE … RETURNING *)`,
`SELECT … INTO`, `FOR UPDATE`.

### `query_settings`

The query carries engine settings or an output format of its own — ClickHouse's `SETTINGS …` or `FORMAT …`. The
connection's limits and the answer's format are the server's; drop the clause.

### `function_denied`

The query calls a function the engine denies — one that reaches outside the database: files, the network, other
servers. Which functions are denied depends on the engine ([engines](engines.md)); a whole family is denied at once.

### `table_function`

The query reads from a table function the engine does not allow here — `read_csv(…)`, `read_parquet(…)`, `url(…)`,
`s3(…)`, `remote(…)`, `file(…)`. The refusal lists the ones allowed.

### `table_not_readable`

The query reads a table outside the connection's `readable` patterns, or in another database — in `FROM`, anywhere
in it, as a dictionary named in a function (ClickHouse's `dictGet('db.dictionary', …)`), or as the database itself
says it reads it. The refusal lists the patterns; `list_tables` shows the readable tables.

### `large_table_unbounded`

The query reads a large table without bounding its column from below. The bound must be:

- **in the query that reads the table** — the `WHERE` (or ClickHouse's `PREWHERE`) of the `SELECT` whose `FROM` names
  it, the table alone there ([`large_table_joined`](#large_table_joined)). A bound in an outer query does not count:
  `SELECT * FROM (SELECT * FROM raw.events) WHERE event_date >= …` is refused — bound it inside;
- **a top-level `AND`** of that condition — a bound under an `OR` does not count;
- **on the column itself**, not a function of it: `event_date >= '2026-03-01'`, `event_date > today() - 7`,
  `event_date = yesterday()`, `event_date BETWEEN … AND …`, `event_date IN (…)`, `NOT (event_date < …)`. An upper
  bound alone does not count;
- **against a value** — a constant or a function of constants (`today() - 7`), no column and no subquery in it;
- **not a name an alias took, on ClickHouse.** There an alias hides the column of its name, in the `WHERE` too:
  `SELECT toDate(event_time) AS event_date … WHERE event_date >= …` bounds `toDate(event_time)` and reads every
  partition. Where the query gives an alias that name — in its `SELECT` list, a `WITH <value> AS name`, an
  `ARRAY JOIN`, inside an expression — the bare name does not count; rename the alias, or qualify the column by its
  table (`e.event_date`). DuckDB reads the column first, and PostgreSQL and MySQL have no aliases in a `WHERE`.

The guard checks that the bound is there, not how wide it is: `event_date >= '1970-01-01'` passes and reads the whole
table, and a value is anything without a column or a subquery — a function's result is not judged. The rule keeps an
agent from forgetting the bound; it does not stop one that sets it wide on purpose.

**A view over a large table** — or a table that reads it, as ClickHouse's `Merge` and `Distributed` do — is read by the
database without the guard seeing the large table behind it: list it in `large_tables` itself (with the column it
exposes), or leave it out of `readable`.

### `large_table_joined`

The query reads a large table beside another table in one `SELECT` — a join, or a comma between them. Whether a
database applies a join's `ON`, or the `WHERE`, to the reading of the joined table depends on the kind of join and on the
database and its version — measured on ClickHouse: `INNER` and `LEFT JOIN … ON` read the whole joined table before
25.3, `RIGHT`, `FULL` and `ANY` joins on every version; and a `RIGHT JOIN`'s `ON` never filters the side it keeps, in
any database. So a large table is read alone in its `SELECT`, and joined as a subquery of its own, bounded there:

```sql
SELECT u.country, count()
FROM marts.users AS u
JOIN (SELECT user_id FROM raw.events WHERE event_date >= '2026-03-01') AS e ON e.user_id = u.user_id
GROUP BY u.country
```

A CTE works the same way, as do `IN (SELECT …)` and `EXISTS (SELECT …)` over the large table.

### `large_table_unnarrowed`

The query reads a large table without naming the values of a column the table is narrowed by (`narrowed_by` in
[`large_tables`](configuration.md#large_tables)) — the shape of an event log that keeps every kind of event in one
table, where a query reads one kind or a few:

```text
Refused by the guard (large_table_unnarrowed): logs.events is a large table: name the values of event_name in the WHERE
of the query that reads it, e.g. WHERE event_name = <a value>, or event_name IN (<values>) — the column itself, not a
function of it.
```

The narrowing is held to the same places as the bound — in the query that reads the table, a top-level `AND`, on the
column itself, not a name an alias took — and it must be **`=` or `IN (…)` against values**:
`event_name = 'order_paid'`, `'order_paid' = event_name`, `event_name IN ('cart_abandoned', 'order_paid')`. A range
(`>`, `BETWEEN`), `LIKE`, `!=`, `IN (SELECT …)`, a function of the column, or values joined by `OR` do not count:
write `IN (…)` for several. The table must be bounded as well; when both are missing, the refusal is
`large_table_unbounded` and says both.

### `unsupported`

The guard cannot follow the query's structure — a shape its parser reads without its sources (for instance two
tables with the same alias), ClickHouse's `x IN db.table` or `x IN (db.table)` (a readable table's name no table of the
query goes by: a table to ClickHouse, a column to the guard's parser), or a dictionary named by an expression rather
than a string. Write the query more plainly: `x IN (SELECT column FROM db.table)`; `dictGet('db.dictionary', …)`.

### `engine_parse`

The database's own parser reads the query differently from the guard — as more than one statement, or as something
other than a `SELECT`, or cannot read it at all. The refusal quotes the engine.

## Limits

Not refusals — the query runs, within them (set per connection, [configuration](configuration.md)):

- **Rows**: at most `rows` rows are read. When there are more, the answer has `truncated: true`,
  `truncated_by: ["rows"]` and a `note`; the rest were never read. A cut answer is never presented as whole.
- **Time**: a query running past `seconds` is stopped; the answer is an error saying so — narrow the query.
- **Answer size**: an answer longer than `answer_characters` holds as many rows as fit, and says so
  (`truncated_by: ["answer_characters"]`).
