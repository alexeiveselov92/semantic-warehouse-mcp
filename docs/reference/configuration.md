# Configuration

One YAML file tells the server which databases it reads and how. Pass it with `--config <file>`, or name it in the
`SEMANTIC_WAREHOUSE_MCP_CONFIG` variable.

```yaml
instructions: Dates are UTC.
context: [context/]
skills: [skills/]
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
| `context` | | your own documents about this database: files and folders, on disk or [in a repository](#repositories) — see [Instructions and context](#instructions-and-context) |
| `skills` | | your own skills for this database: folders, on disk or [in a repository](#repositories) — see [Skills](#skills) |
| `semantic_models` | | its semantic models in Apache Ossie: files and folders — see [Semantic models and vocabularies](#semantic-models-and-vocabularies) |
| `missing_tables` | | what a semantic model's table the database does not hold does: `refuse` (the default) stops the server, naming it; `leave_out` takes that dataset (and its relationships) out of the model, named by `--check` and in the server's log — for a model made from a project ahead of the database (a dbt model not built yet) |

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
`<folder>/<path inside it>`. Documents kept in a git repository the server does not hold beside its configuration are
read [from the repository](#repositories).

## Skills

A skill is how a team does a kind of task — answers a kind of question, checks a number, puts a report together —
written for agents in the [Agent Skills](https://agentskills.io/specification) format: a folder holding a `SKILL.md`
and, beside it, whatever it refers to (`references/`, `scripts/`, `assets/`). `skills` names such folders, for the
whole server (at the top of the file) and for each connection, relative to the configuration file's folder:

```yaml
skills: [skills/]                      # a folder of skills: each folder inside it holding a SKILL.md is one

connections:
  - name: shop
    engine: duckdb
    path: data/shop.duckdb
    readable: ["marts.*"]
    skills:
      - ../analytics-repo/skills/revenue-questions   # a skill's own folder
```

A folder holding a `SKILL.md` is one skill; any other folder holds a skill in each folder inside it that holds one
(hidden folders, and folders without a `SKILL.md`, left alone). A `SKILL.md` starts with its frontmatter:

```markdown
---
name: revenue-questions
description: How to answer a question about revenue in the shop — which metric, which grouping, what to check.
---

1. Use the `revenue` metric; never sum `amount` by hand.
2. The rules of grouping are in [references/grouping.md](references/grouping.md).
```

When the server starts, every skill is checked by the format's rules, and a problem stops it, naming the file:

| What | Rule |
|---|---|
| frontmatter | the file starts with a `---` line, and a second `---` line closes it; YAML between them, a mapping |
| `name` | 1–64 lowercase letters, digits and hyphens, no hyphen at either end and none doubled; the folder's own name |
| `description` | text, 1–1024 characters: what the skill does and when to use it |
| `SKILL.md` | UTF-8 text |

The frontmatter's other keys (`license`, `metadata`, `compatibility`, `allowed-tools`, a client's own) are not checked,
and served as written with the rest. No two skills of one scope may share a name. An agent lists the skills
(`list_skills`, with each description) and reads the one that fits (`get_skill`) — its `SKILL.md`, then any file of its
folder it refers to. Everything is served **as written**, and read when an agent asks for it, so an edited or added
skill is served without a restart — and a skill broken after the start is named in the answer, until it is fixed.

## Repositories

Context documents and skills are often kept in a repository of their own, and a server deployed from another one —
its configuration's — does not hold them on disk. The server then fetches them itself: declare the repository once,
in `repositories` at the top of the file, and name a path inside it wherever a file or folder goes in `context` or
`skills`.

```yaml
repositories:
  - name: kit
    url: https://github.com/acme/analytics-kit.git
    ref: main
    token_env: KIT_TOKEN                # a private repository: the variable holding a token that may read it

context:
  - handbook/                           # on disk, as before
  - { repository: kit, path: docs/glossary.md }

connections:
  - name: shop
    engine: duckdb
    path: data/shop.duckdb
    readable: ["marts.*"]
    context: [{ repository: kit, path: docs/shop }]
    skills: [{ repository: kit, path: skills }]
```

| Key | Default | What |
|---|---|---|
| `name` | | how `context` and `skills` entries name it: lowercase letters, digits, `-` and `_` |
| `url` | | the repository's address: `https://` (`http://` only to this machine), with no credentials in it — neither a user and password nor a query |
| `ref` | the repository's default branch | a branch, a tag, or a commit by its full hash; a name that is both a tag and a branch means the tag, as git reads it |
| `token_env` | | the variable holding a token that may read the repository — a GitHub or GitLab access token, for example; left out for a public repository |
| `token_user` | `x-access-token` | the user name the token is sent with — what GitHub expects; another host may want its own (Bitbucket: `x-token-auth`) |
| `refresh_minutes` | `10` | how often the server looks for a new commit, `0`–`1440`; `0` fetches only at start |

An entry in a repository is a mapping of `repository` (the name declared) and `path` (a file or a folder inside it,
as `docs/shop` — names joined by `/`, none of them empty, `.` or `..`; `.`, the default, is the whole repository, its
documents then named `<repository name>/…`). A file or folder is named as it would be on disk: `docs/shop/rules.md` is
served as `shop/rules.md`. A file whose name is not UTF-8 is left out, as no agent could ask for it.

- **At start** every repository is fetched — a single commit: the list of its files' names, and the files themselves
  only under the paths the configuration names — and everything in it is checked as it would be on disk; a repository
  that cannot be fetched, a path it does not hold, a skill breaking the format stops the server, naming it, with every
  other problem of the file. `--check --offline` fetches nothing, and needs no git.
- **Kept fresh.** Every `refresh_minutes` the server looks for a new commit of the `ref`, in the background — no
  answer waits for it. A new commit is served only when it passes the same checks as at start; when it does not, or the
  repository cannot be reached, the commit served stays, and `list_connections` says what went wrong (`problem`), as
  the server's log does. A `ref` that is a commit never changes, so it is never looked for again. When the server is
  stopped, its copies are removed.
- **Where it came from.** `get_context` and `get_skill` answer the repository (`fetched_from`) and the `commit` of what
  they serve; `list_connections` lists each repository with the commit served, when it was fetched and last checked.
- **The token** is read from its variable and sent to the repository's own address only — in a header, never on a
  command line, in a log or an answer. No redirect is followed, to another address or the same: a repository that moved
  needs its new URL. Write the token in the URL — as a user and password, or in its query — and the server does not
  start; write it where its variable's name goes, and no error repeats it.
- **Nothing in a repository reaches outside its copy**: a symbolic link is served as a file holding its target's path,
  never followed; no hook runs. No git configuration of the machine or the user is used, and of the server's
  environment only `PATH`, the proxy variables (for `https://` only) and those naming certificates reach git
  ([running it](running.md#repositories)).

Fetching needs the `git` program, 2.31 or newer — the [image](running.md#the-image) has it.

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

Read from later milestones, each documented here when it works: `query_sets` (saved, parameterised SQL);
`semantic_models` and `vocabularies` read straight from a git repository.
