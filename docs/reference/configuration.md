# Configuration

One YAML file tells the server which databases it reads and how. Pass it with `--config <file>`, or name it in the
`SEMANTIC_WAREHOUSE_MCP_CONFIG` variable.

```yaml
instructions: Dates are UTC.
context: [context/]
vocabularies: [vocabulary/acme.yaml]

connections:
  - name: shop
    engine: duckdb
    description: A made-up online shop.
    path: data/shop.duckdb
    readable: ["marts.*", "dictionaries.*", "raw.events"]
    large_tables:
      - { table: raw.events, bound_by: event_date }
    limits: { rows: 500, seconds: 30 }
    semantic_models: [semantic/]

limits:
  answer_characters: 40000
```

## The check

The server checks the whole file before it starts and stops at a wrong entry, naming it — every problem at once, each
with where it is:

```text
semantic-warehouse-mcp: the configuration config.yaml has 2 problems:
  - connections[0] "shop" → readble: unknown key — did you mean 'readable'?
  - connections[0] "shop" → readable: required
```

It refuses unknown keys (with the likely one, for a typo), a key written twice in one mapping, values of the wrong
type, and a connection it cannot reach. A value that may be a secret written by mistake (under a key ending in `_env`,
a URL, or a key naming a password, secret or token — or a mapping holding such a key) is never repeated in an error,
nor is a password written inside a URL anywhere (`https://…@host`); an error in the YAML itself names its line and
column, never the line.

Every file the server reads — this one, [semantic models and vocabularies](semantic-models.md) — is read as written:
only `true` and `false` are booleans (`no`, `on`, `yes` stay text, as in YAML 1.2), and a date stays the text written
(`2026-03-01`). A file must be UTF-8; YAML aliases may not expand it past a million values. A character escaped as a
surrogate pair (`"😀"`, as JSON writes it) reads as that one character; a lone surrogate is not text and is
refused.

`semantic-warehouse-mcp --config <file> --check` runs the check, reaches every database, and exits — `0` when the
configuration is fine, `2` with its problems.

## `connections`

Required: a list of at least one connection. Each connection:

| Key | | What |
|---|---|---|
| `name` | required | how agents name it: lower case letters, digits, `_` and `-`, starting with a letter; unique |
| `engine` | required | the database engine — one of [the supported engines](engines.md) |
| `description` | | what the database holds, in a sentence; shown to agents by `list_connections` |
| `readable` | required | the tables a query may read, as `schema.table` patterns — `*` and `?` match within a part: `marts.*`, `*.*`, `raw.events` |
| `large_tables` | | tables a query must bound, each `{ table, bound_by }` — see below |
| `limits` | | the limits of one query — see below |
| `instructions` | | your own rules for agents working with this database, as text — see [Instructions and context](#instructions-and-context) |
| `context` | | your own documents about this database: files and folders — see [Instructions and context](#instructions-and-context) |
| `semantic_models` | | its semantic models in Apache Ossie: files and folders — see [Semantic models and vocabularies](#semantic-models-and-vocabularies) |

An engine adds keys of its own — a file's `path`, a server's address, the variables holding secrets: see
[engines](engines.md).

Nothing outside `readable` can be read: an unqualified table name resolves to the engine's default schema, and that
schema is readable only when a pattern covers it. `readable` is the guard's line; for a line the database itself holds,
give the server a database user that can read only those tables.

## `large_tables`

Each entry:

| Key | | What |
|---|---|---|
| `table` | required | `schema.table`, globs allowed (`raw.*`); must be covered by `readable` |
| `bound_by` | required | the column a query over it must bound from below — usually its date |

A query that reads a large table without `bound_by >= …` (or `>`, `=`, `BETWEEN`, `IN`) in the query that reads it is
refused — [the guard](guard.md#large_table_unbounded) says exactly where the bound goes.

## `limits` (of a connection)

| Key | Default | What |
|---|---|---|
| `rows` | `500` | the most rows a query's answer holds (1–100000); rows past it are never read, and the answer says it was cut |
| `seconds` | `30` | how long a query may run (up to 3600); past it, the query is stopped and the answer says so |

## `limits` (of the server)

| Key | Default | What |
|---|---|---|
| `answer_characters` | `40000` | the longest answer the server sends (1000–1000000). Clients cut long answers silently — Claude Code near 25,000 tokens — so the server cuts first, and says so |

## Instructions and context

What a team knows about its data beyond what a table says — how it is made, what to never do, how a number is counted
— reaches agents in two ways, for the whole server (at the top of the file) and for each connection:

| Key | What |
|---|---|
| `instructions` | short rules, written in the configuration. An agent gets them at once: in the server's instructions when it connects, and in `list_connections` |
| `context` | documents, kept where you keep them: a list of files and folders, relative to the configuration file's folder. A folder serves every `.md`, `.markdown` and `.txt` file in it, at any depth (hidden files and folders left out). An agent lists them (`list_context`) and reads the ones it needs (`get_context`) |

```yaml
instructions: |                       # for every connection
  Dates are UTC. Never add amounts of different currencies.
context: [handbook/glossary.md]

connections:
  - name: shop
    engine: duckdb
    path: data/shop.duckdb
    readable: ["marts.*"]
    instructions: |                   # for this connection only
      Test orders are already removed from marts.orders.
    context:
      - ../analytics-repo/docs/shop    # a folder: every document in it
```

Both are served **as written** — the server never rewords them. A document is read when an agent asks for it, so an
edited or added document is served without a restart. Every path must exist when the server starts, and no two
documents of one scope may share a name: a file is named by its own name, a folder's files as
`<folder>/<path inside it>`.

## Semantic models and vocabularies

| Key | Where | What |
|---|---|---|
| `semantic_models` | a connection | what its tables mean, in [Apache Ossie](semantic-models.md): a list of files and folders (every `.yaml`, `.yml`, `.json` in a folder), relative to the configuration file's folder |
| `vocabularies` | the top of the file | your own metadata keys, declared: a list of vocabulary files and folders — one vocabulary per `vendor_name`, for the whole server |

```yaml
vocabularies: [vocabulary/acme.yaml]

connections:
  - name: shop
    engine: duckdb
    path: data/shop.duckdb
    readable: ["marts.*", "raw.events"]
    semantic_models: [semantic/]
```

Each model is checked when the server starts — against the schema of its Ossie version, by Ossie's rules, against its
connection (every dataset's source readable there and existing), and its own metadata against its vocabulary — and a
problem stops the server, naming it. [Semantic models and vocabularies](semantic-models.md) has every check and the
vocabulary's keys.

## Planned

Read from later milestones, each documented here when it works: `skills` (folders holding a `SKILL.md`), `query_sets`
(saved, parameterised SQL); `context`, `semantic_models` and `vocabularies` read straight from a git repository.
