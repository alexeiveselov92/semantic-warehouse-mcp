# Tools

Every tool semantic-warehouse-mcp offers to an agent. All are read-only: each says so to the client (`read_only_hint`, never
`destructive_hint`). A tool appears here in the same commit that adds it — a test fails otherwise.

Answers are JSON text. Tools that read a database answer an error when it refuses or fails — a refusal by
[the guard](guard.md) names its code and how to fix the query. Every answer is capped at `answer_characters`
([configuration](configuration.md)); a cut answer says so in `truncated`, `truncated_by` and `note`. Items of a list
(rows, tables, datasets, fields) are left out first; only when the answer is too long with none of them are its longest
values cut — each ending in `…`, `truncated_by` holding `long_values`. An answer that does not fit even so — too many
short values — holds only `truncated` and a `note` saying to ask for less.

`connection` may be left out of any tool that takes it when the server has exactly one connection.

## `about`

What this server is, its version and status. Call it to check the connection. Takes nothing; answers its name, version,
status and `read_only: true`.

## `list_connections`

The databases the server reads. Takes nothing. For each connection: `name`, `engine`, `description` (when the
configuration gives one), `instructions` (the team's, as written), `readable` (the `schema.table` patterns a query may
read), `large_tables` (each `table` with the column a query must bound, `bound_by`), `limits` (`rows` and `seconds` per
query), `semantic_models` (the names of its [semantic models](semantic-models.md)), and `context` (the names of its
context documents). Beside the list: the `instructions` and `context` for the whole server. Reads the configuration
only — it never reaches a database.

```json
{"connections":[{"name":"shop","engine":"duckdb","description":"A made-up online shop.",
  "readable":["marts.*","raw.events"],"large_tables":[{"table":"raw.events","bound_by":"event_date"}],
  "limits":{"rows":500,"seconds":30},"semantic_models":["shop"]}]}
```

## `list_datasets`

The datasets of the [semantic models](semantic-models.md): the tables the team described for agents. Takes
`connection` (optional: left out, every connection's) and `search` (optional). Answers each dataset's `connection`,
`model`, `dataset` (its name), `source` (its table, or the query it is), `description` — its first line only, cut at
200 characters, with `…` when there is more — `synonyms` (from its `ai_context`), and `custom_extensions` holding the
own metadata its vocabulary marks `in_lists`, as written. Beside the list, `vocabulary`: what each key and each value
shown means.

`search` keeps the datasets holding every one of its words, in any case, anywhere in the dataset as written — its
name, source, description, `ai_context`, its fields' names and descriptions, its own metadata. `search: "sales mart"`
finds a dataset tagged `sales` in the layer `mart`.

```json
{"vocabulary":{"acme":{"tags":{"description":"What a table or a column is about.",
  "values":{"sales":"Paid orders and their refunds."}}}},
 "datasets":[{"connection":"shop","model":"shop","dataset":"orders","source":"marts.orders",
  "description":"One row per paid order; test orders are removed.…","synonyms":["purchases","sales"],
  "custom_extensions":[{"vendor_name":"acme","data":{"tags":["sales"],"layer":"mart"}}]}]}
```

Without any semantic model, the list is empty and `note` says so.

## `describe_dataset`

One dataset in full, as the team wrote it. Takes `dataset` (its name), and `connection` and `model` (optional — needed
only when two models have a dataset of that name; the error names them). Answers:

| Field | What |
|---|---|
| `connection`, `model`, `dataset` | where it is, its name |
| `source`, `primary_key`, `unique_keys`, `description`, `ai_context`, `custom_extensions` | as written in the model (the ones it has) |
| `table` | its source table as the database holds it now: `name`, `kind`, `comment`, and `bound_by` when a query must bound it. For a query, `note` says so; for a table that is gone, `note` says that |
| `metrics` | the dataset's own metrics, as written |
| `relationships` | the model's relationships from or to it, as written |
| `other_columns` | the table's columns no field is: `name`, `type`, `nullable`, `comment` |
| `vocabulary` | what each own metadata key and value used — by the dataset, its fields, metrics and relationships — means |
| `fields` | every field as written (`expression` per dialect, `dimension`, `datatype`, `description`, `ai_context`, `custom_extensions`), with `column` — the live column's `type`, `nullable` and `comment` — when the field's expression is just that column's name |

A field's expression may be a calculation over columns (`amount - refunded_amount`): it then has no `column`, and the
columns it reads are in `other_columns`. An answer too long for `answer_characters` is made smaller before a field is
left out — `other_columns` to names only, then `other_columns`, `relationships`, `metrics` and `vocabulary` left out, in
that order — and `note` says what was.

## `list_metrics`

The metrics of the semantic models: how the team defines its numbers. Takes `connection` and `search` (both optional,
as in `list_datasets`). Answers each metric's `connection`, `model`, `metric` (its name), `dataset` (for a dataset's
own metric), `description` (its first line), `synonyms`, and the own metadata marked `in_lists` — with `vocabulary`
beside the list.

## `describe_metric`

One metric in full, as the team wrote it. Takes `metric` (its name), `dataset` (for a dataset's own metric), and
`connection` and `model` (optional — when two models define a metric of that name). Answers `connection`, `model`,
`metric`, `dataset`, then the metric as written — `expression` per dialect, `description`, `datatype`, `ai_context`,
`custom_extensions` — with `vocabulary`, and `datasets`: each dataset its expression reads, its `source`, and the
`fields` the expression names with their own `expression`. A metric is computed over fields, and a field may be a
calculation, not a column — `SUM(orders.amount)` over a field `amount` that is `amount - refunded_amount`:

```json
{"connection":"shop","model":"shop","metric":"revenue",
 "expression":{"dialects":[{"dialect":"ANSI_SQL","expression":"SUM(orders.amount)"}]},
 "description":"Revenue net of refunds, in the order's currency.",
 "datasets":[{"dataset":"orders","source":"marts.orders","fields":[{"name":"amount",
  "expression":{"dialects":[{"dialect":"ANSI_SQL","expression":"amount - refunded_amount"}]}}]}]}
```

Expressions are served as the model writes them; the SQL an agent runs is its own, through `run_query`.

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
