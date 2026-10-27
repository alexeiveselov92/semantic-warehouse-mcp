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
read), `large_tables` (each `table` with the column a query must bound, `bound_by`, and the columns it must hold to
named values, `narrowed_by`, when it has them), `limits` (`rows` and `seconds` per query), `semantic_models` (the names of its [semantic models](semantic-models.md)), `context` (the names of its
context documents) and `skills` (the names of its skills). Beside the list: the `instructions`, `context` and `skills`
for the whole server, and the [repositories](configuration.md#repositories) they are read from — each one's `name`,
`url`, `ref`, the `commit` served, `fetched_at` (when that commit was fetched), `checked_at` (when the repository was
last reached), `refresh_minutes`, and `problem` when the last look for a new commit went wrong (the commit served
stays). Reads the configuration only — it never reaches a database.

```json
{"connections":[{"name":"shop","engine":"duckdb","description":"A made-up online shop.",
  "readable":["marts.*","raw.events"],"large_tables":[{"table":"raw.events","bound_by":"event_date"}],
  "limits":{"rows":500,"seconds":30},"semantic_models":["shop"]}]}
```

## `list_datasets`

The datasets of the [semantic models](semantic-models.md): the tables the team described for agents. Takes
`connection` (optional: left out, every connection's), `search` and `metadata` (both optional). Answers each dataset's
`connection`,
`model`, `dataset` (its name), `source` (its table, or the query it is), `description` — its first line only, cut at
200 characters, with `…` when there is more — `synonyms` (from its `ai_context`), and `custom_extensions` holding the
own metadata its vocabulary marks `in_lists`, as written. Beside the list, `vocabulary`: what each key and each value
shown means.

`search` keeps the datasets holding every one of its words, in any case, anywhere in the dataset as written — its
name, source, description, `ai_context`, its fields' names and descriptions, its own metadata. `search: "sales mart"`
finds a dataset tagged `sales` in the layer `mart`. The closest come first: those with every word in their name, then
those with every word in what they say of themselves (name, source, label, description, `ai_context`), then the rest —
and, within each, those whose name holds more of the words. A dataset found by its fields, not by what it says of
itself, names in `matched_fields` the fields holding every word: `search: "refund"` finds where refunds are kept.

`metadata` keeps the datasets whose own metadata holds, for each key given, its value — or one of a list of values:
`{"tags": "sales", "layer": ["mart", "dim"]}` is a dataset tagged `sales` in the layer `mart` or `dim`. A list holds a
value when one of its items is it; another value, when it is equal (a number to a number, a boolean to a boolean). The
dataset may hold it itself, or one of its fields — then `matched_fields` names the fields holding every key the
dataset does not: `{"tags": "money"}` finds where amounts of money are, field by field. `list_metadata` shows the keys
and their values; a key no model holds, or a value its vocabulary does not have, is named in `note` with the likely one
meant. With [the dbt importer](dbt-importer.md)'s `dbt_depends_on`, `{"reads": "raw.events"}` finds what is built on a
table. `search` and `metadata` together keep what both keep.

**A list too long for one answer** — a model of hundreds of tables — names every dataset only, grouped by its model,
with `compact: true` and a `note` saying so: the map of what there is, in one answer (`{"connection": "shop", "model":
"shop", "names": ["orders", "customers", …]}`). `search` then gives the descriptions of the ones that matter, and
`describe_dataset` one in full. Only when even the names do not fit are the last ones left out, the `note` saying how
many are named (`truncated`). An agent never reads a semantic model whole: the server holds it, and answers one
question about it at a time.

```json
{"vocabulary":{"acme":{"tags":{"description":"What a table or a column is about.",
  "values":{"sales":"Paid orders and their refunds."}}}},
 "datasets":[{"connection":"shop","model":"shop","dataset":"orders","source":"marts.orders",
  "description":"One row per paid order; test orders are removed.…","synonyms":["purchases","sales"],
  "custom_extensions":[{"vendor_name":"acme","data":{"tags":["sales"],"layer":"mart"}}]}]}
```

Without any semantic model, the list is empty and `note` says so.

## `describe_dataset`

One dataset in full, as the team wrote it. Takes `dataset` (its name), `connection` and `model` (optional — needed
only when two models have a dataset of that name; the error names them), and `full` (optional, `false` — see below).
Answers:

| Field | What |
|---|---|
| `connection`, `model`, `dataset` | where it is, its name |
| `source`, `primary_key`, `unique_keys`, `description`, `ai_context`, `custom_extensions` | as written in the model (the ones it has) |
| `table` | its source table as the database holds it now: `name`, `kind`, `comment`, and `bound_by` (and `narrowed_by`) when a query must bound (and narrow) it. For a query, `note` says so; for a table that is gone, `note` says that |
| `metrics` | the dataset's own metrics, as written |
| `relationships` | the model's relationships from or to it, as written |
| `other_columns` | the table's columns no field is: `name`, `type`, `nullable`, `comment` |
| `vocabulary` | what each own metadata key and value used — by the dataset, its fields, metrics and relationships — means |
| `fields` | every field as written (`expression` per dialect, `dimension`, `datatype`, `description`, `ai_context`, `custom_extensions`), with `column` — the live column's `type`, `nullable` and `comment` — when the field's expression is just that column's name |
| `note` | what was made shorter, when anything was |

A field whose expression is nothing but its own name, in every dialect written (a field `order_date` that is the
column `order_date`), comes without `expression`: it is the column of that name, and saying so again for each of a
hundred fields would only lengthen the answer. A field's expression may be a calculation over columns
(`amount - refunded_amount`): it then has no `column`, and the columns it reads are in `other_columns`.

**Own metadata is shortened unless asked for in full.** A value of the team's own metadata longer than 1000
characters as JSON — a model's SQL (the [dbt importer](dbt-importer.md)'s `dbt_code`), a long list of what a table is
built from — is made short: a text to its first 1000 characters, ending in `…`; a list or a mapping to a note of its
length, `… (4210 characters as JSON)`. `note` says so. Such values matter for how a table is built, rarely for what it
holds, and an agent rereads every answer at each step of its work — so they are served when asked for: `full: true`
serves every value whole.

An answer too long for `answer_characters` is made smaller before a field is left out — `other_columns` to names
only, own metadata texts longer than 2000 characters cut (with `full: true`), then `other_columns`, `relationships`,
`metrics` and `vocabulary` left out, in that order — and `note` says what was.

## `list_metrics`

Offered, with `describe_metric`, only when a semantic model defines a metric — its own or a dataset's. Where none does,
neither tool is listed and the server's instructions do not name them: a tool that could only answer that there are
no metrics would cost an agent a step in every conversation.

The metrics of the semantic models: how the team defines its numbers. Takes `connection`, `search` and `metadata` (all
optional, as in `list_datasets`; a metric holds own metadata itself, having no fields). Answers each metric's `connection`, `model`, `metric` (its name), `dataset` (for a dataset's
own metric), `description` (its first line), `synonyms`, and the own metadata marked `in_lists` — with `vocabulary`
beside the list. A search lists the closest first, and a list too long for one answer names every metric only, by
model (`compact`; a dataset's own metric as `dataset.metric`), as `list_datasets` does.

## `list_metadata`

The team's own metadata the semantic models hold: what `list_datasets` and `list_metrics` filter by (`metadata`) — the
menu of a team's categories, layers, owners. Takes `connection` (optional). Answers, for each vendor and key:
`vendor_name`, `key`, `description` (what the [vocabulary](semantic-models.md#vocabularies) says it is), on how many
`datasets`, `fields`, `metrics` (and `models`, `relationships`) it is — and for a key of short values (words, numbers,
or lists of them) each value used, the most used first: `value`, on how many it is, and its `definition` from the
vocabulary. A key of more than 50 values, or of texts longer than 100 characters, says how many it has
(`distinct_values`) rather than listing them; a key of mappings, neither. A key the vocabulary closes lists every value
used, however many.

```json
{"metadata":[{"vendor_name":"acme","key":"tags","description":"What a table or a column is about.","datasets":4,
  "fields":7,"values":[{"value":"identifier","fields":3,"definition":"A key naming one row of an entity."},
  {"value":"sales","datasets":1,"definition":"Paid orders and their refunds."}]},
 {"vendor_name":"acme","key":"layer","datasets":4,"values":[{"value":"mart","datasets":3},{"value":"raw","datasets":1}]},
 {"vendor_name":"acme","key":"sql","description":"The SQL the dbt project builds this table with.","datasets":2,
  "distinct_values":2}]}
```

## `describe_metric`

Offered only when a semantic model defines a metric, as `list_metrics` is.

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
(`table` or `view`), its `comment` as the database holds it (when it has one), and for a large table `bound_by` — and
`narrowed_by` when it has one ([`large_tables`](configuration.md#large_tables)).
Tables that are not readable are not listed.

## `describe_table`

A readable table's columns. Takes `table` — `schema.table`, or a bare name in the engine's default schema — and
`connection`. Answers the table's `kind` and `comment`, `bound_by` (and `narrowed_by`) when it is a large table, and
its `columns` in
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
`connection`, `fetched_from` and `commit` (the repository's name and the commit, for a document
[read from a repository](configuration.md#repositories)),
`characters` (the whole document's), `offset`, and `text`. A document longer than an answer can hold
comes in parts: `next_offset` says where the next part starts — call again with it. Only listed documents are served:
no other file can be named.

## `list_skills`

The skills the team wrote for agents ([configuration](configuration.md#skills)) — each how it does a kind of task. Takes
`connection` (optional: then only the whole server's skills and that connection's). Answers each skill's `name`,
`description` (as its `SKILL.md` writes it — what the skill does and when to use it), and `connection` — absent for a
skill of the whole server.

```json
{"skills":[{"name":"revenue-questions","description":"How to answer a question about revenue in the shop — which metric, which grouping, what to check.","connection":"shop"}]}
```

## `get_skill`

One skill, exactly as written. Takes `name` (as `list_skills` shows it), `connection` (for a connection's skill; left
out, the whole server's — or the only skill of that name), `file` (optional: a file of the skill's folder, as `files`
lists it), and `offset` (default 0). Answers `name`, `connection`, `file` (`SKILL.md` unless another is asked for),
`fetched_from` and `commit` (the repository's name and the commit, for a skill
[read from a repository](configuration.md#repositories)), `characters` (the whole file's), `offset`, and `text` — the whole file, its frontmatter too. With `SKILL.md` it also
answers `files`: every other file of the skill's folder an agent may read — its references, scripts, templates — and
`not_text`: the files that are not text (images, archives), which are listed but not served. A file longer than an
answer can hold comes in parts: `next_offset` says where the next part starts — call again with it. Only listed files
are served: no other path can be named; hidden files and folders (`.git`), and files whose name is not UTF-8, are
neither listed nor served. A skill whose lists of files take more than half an answer lists the first that fit, and its
`note` says how many there are.
