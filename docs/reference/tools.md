# Tools

Every tool semantic-warehouse-mcp offers to an agent. All are read-only: each says so to the client (`read_only_hint`, never
`destructive_hint`). A tool appears here in the same commit that adds it — a test fails otherwise.

Answers are JSON text. Tools that read a database answer an error when it refuses or fails — a refusal by
[the guard](guard.md) names its code and how to fix the query. Every answer is capped at `answer_characters`
([configuration](configuration.md)); a cut answer says so in `truncated`, `truncated_by` and `note`.

`connection` may be left out of any tool that takes it when the server has exactly one connection.

## `about`

What this server is, its version and status. Call it to check the connection. Takes nothing; answers its name, version,
status and `read_only: true`.

## `list_connections`

The databases the server reads. Takes nothing. For each connection: `name`, `engine`, `description` (when the
configuration gives one), `instructions` (the team's, as written), `readable` (the `schema.table` patterns a query may
read), `large_tables` (each `table` with the column a query must bound, `bound_by`), `limits` (`rows` and `seconds` per
query), and `context` (the names of its context documents). Beside the list: the `instructions` and `context` for the
whole server. Reads the configuration only — it never reaches a database.

```json
{"connections":[{"name":"shop","engine":"duckdb","description":"A made-up online shop.",
  "readable":["marts.*","raw.events"],"large_tables":[{"table":"raw.events","bound_by":"event_date"}],
  "limits":{"rows":500,"seconds":30}}]}
```

## `list_tables`

The readable tables and views of a connection. Takes `connection`. Answers each `table` (`schema.table`), its `kind`
(`table` or `view`), its `comment` as the database holds it (when it has one), and `bound_by` for a large table.
Tables that are not readable are not listed.

## `describe_table`

A readable table's columns. Takes `table` — `schema.table`, or a bare name in the engine's default schema — and
`connection`. Answers the table's `kind` and `comment`, `bound_by` when it is a large table, and its `columns` in
order: `name`, `type` (the engine's own), `nullable`, and the column's `comment` as the database holds it. A table that
is not readable is refused (`table_not_readable`); one that does not exist is named in the error.

## `run_query`

Runs one read-only `SELECT` — `WITH`, `UNION`, joins and subqueries allowed — in the connection's SQL dialect. Takes
`sql` and `connection`.

The query is checked by [the guard](guard.md) first; a refusal says why and how to fix the query. A query that passes
runs **as written** (trailing semicolons removed), within the connection's limits.

The answer:

| Field | What |
|---|---|
| `connection` | the connection it ran on |
| `sql` | the SQL exactly as it ran |
| `started_at` | when it started, UTC, ISO 8601 |
| `seconds` | how long it took |
| `columns` | each column's `name` and `type` (the engine's own) |
| `rows` | the rows, each an array in column order |
| `row_count` | how many rows the answer holds |
| `truncated` | `true` when rows were left out |
| `truncated_by` | why: `rows` (more rows than the connection's limit — the rest were never read), `answer_characters` (the answer would be too long) |
| `note` | when cut: how many rows and what to do — aggregate, filter, or add `LIMIT` |

Values: numbers are exact — a decimal a JSON number cannot hold exactly comes as a string (`"12345678901234567.25"`);
dates and times are ISO 8601 strings; binary values are `base64:`-prefixed strings.

A query past the connection's `seconds` is stopped and answers an error saying so; a database error is passed on in
the engine's own words.

## `list_context`

The context documents the team wrote for agents ([configuration](configuration.md#instructions-and-context)). Takes
`connection` (optional: then only the whole server's documents and that connection's). Answers each document's `name`,
`title` (its first heading), `characters`, and `connection` — absent for a document of the whole server.

## `get_context`

One context document, exactly as written. Takes `name` (as `list_context` shows it), `connection` (for a connection's
document; left out, the whole server's — or the only document of that name), and `offset` (default 0). Answers `name`,
`connection`, `characters` (the whole document's), `offset`, and `text`. A document longer than an answer can hold
comes in parts: `next_offset` says where the next part starts — call again with it. Only listed documents are served:
no other file can be named.
