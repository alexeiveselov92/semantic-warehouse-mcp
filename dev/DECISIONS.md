# Decisions

What is decided and why. Newest at the bottom; grep it.

## 2026-10-24 · An open-source, universal, read-only MCP for databases with their semantics

**Decision** (the author). A separate open-source project under the author's own GitHub, not any company's:
an MCP server for SQL databases that takes a semantic layer in an open format, your own metadata, metrics,
context and skills — universal for any database and any MCP client (not tied to one model vendor). A company's
use of it — connections, its semantic model, its documents, deployment — lives in that company's own
repositories as configuration; nothing of any company is written here.

**Why.** What makes an agent's numbers right is semantics; every team has it, in its own shape. One server that
reads it in an open format and serves it beside any database is useful to many teams, configured rather than
rewritten. The author's earlier company-internal platform proved the parts (a guard over the database's own
parse, semantics served as they are, provenance) and showed they are general.

## 2026-10-24 · The semantic layer is Apache Ossie

The input format is [Apache Ossie](https://ossie.apache.org/) (formerly OSI): an open YAML/JSON specification
backed by Snowflake, dbt Labs, Salesforce and others, with converters from dbt, Cube, Databricks and more. It has
the places we need — `ai_context` for agents, `custom_extensions` for anything of our own — so no format of our
own is invented. Others (dbt's) come in through importers.

## 2026-10-24 · Which Ossie: the 0.2 shape, 0.1.1 read too

The format is moving. The released **0.1.1** (still named OSI, tag `osi-0.1.1-rc1`, `core-spec/osi-schema.json`)
wraps the model in a root `semantic_model` beside `dialects` and `vendors`; the draft **0.2.0.dev0** (Ossie,
`main`, `core-spec/ossie-schema.json`) is flat — `name`, `datasets`, `relationships`, `metrics`, `ai_context`,
`custom_extensions` at the root. We write and serve the **0.2 shape** (where the spec goes) and **read 0.1.1 too**
by unwrapping `semantic_model`. The schema of each supported version is bundled and pinned; a document is checked
against its own version's schema at start, and a version we don't know is refused, naming it. The example
`examples/semantic/shop.yaml` validates against 0.2.0.dev0 with no errors (checked 2026-10-24).

## 2026-10-24 · The name keeps `mcp`; the documentation and hosting follow the author's own projects

- **The name is `semantic-warehouse-mcp`** — the author's rule: `mcp` in the name and a meaning plain at a glance (the `-kit` names of
  the author's other projects were weighed and set aside); free on PyPI.
- **Renamed the same day** from `warehouse-mcp`: dozens of servers are named like that for plain SQL; the
  semantics are what this one adds, and the name now says so.
- **Documentation and hosting** as in the author's detectkit and pipelab: `docs/` the single source of a site built
  with Astro + Starlight; the site and an instance of the server deployed by `alexeiveselov92/pipelab`
  (`dev/DESIGN.md`, "Documentation, distribution and hosting"). Internal design, plan, decisions and research moved to
  `dev/` the same day, so the site never publishes them.

## 2026-10-24 · A metrics layer of our own beside Ossie: populations and filters first-class

The author asked that the server be flexible enough to hold a registry where **every metric is filtered through another
entity** (the population of people present, with their attributes). A production registry of that shape was studied
(`dev/RESEARCH.md`): populations, filters bound per population with their placement, roll-up rules, templates for
several targets, presets, scenarios. Ossie 0.2 has none of it (not even filters on metrics), and its
`custom_extensions.data` is a JSON string — no place to author such a layer by hand. So the metrics layer is our own
open YAML beside the Ossie model (`dev/DESIGN.md`), able to express that registry without loss; Ossie stays the model
of the data, and can carry the layer in `custom_extensions` for interchange.

## 2026-10-24 · Python, the official MCP SDK, sqlglot, Apache-2.0, written fresh

- **Python 3.12 with uv**, the official MCP Python SDK (2.x: `MCPServer`, the in-process `Client` for tests);
  installed with one command (`uvx`); the same stack as its sibling, repo-reader-mcp.
- **sqlglot** parses and translates SQL of 30+ dialects: one guard for every database, and model expressions
  translated to each database's dialect.
- **Apache-2.0**, like Ossie: an explicit patent grant; the license companies take in most easily.
- **Written fresh**: the earlier platform's code belongs to the company it was written for. Its lessons are
  carried as design ([DESIGN.md](DESIGN.md)), not as code.

## 2026-10-24 · DuckDB for development and tests; ClickHouse is the engine production work runs on

**The author**: production work will run on ClickHouse; for development and tests the engine may not matter yet — then
DuckDB. So DuckDB is the first adapter (in-process: the tests need no server), and the guard is tested in ClickHouse's
dialect from the start (`PREWHERE`, `FINAL`, `SAMPLE`, `ARRAY JOIN`, `GLOBAL IN`, `SETTINGS`, `FORMAT`, its table
functions) — no server needed for that. The ClickHouse adapter's requirements are in `dev/DESIGN.md`, "Adapters".

## 2026-10-24 · The query runs as written, read twice

A query that passes the guard runs as the agent wrote it (trailing semicolons dropped), not as sqlglot would regenerate
it: a regenerated query can differ from the agent's in meaning, and a wrong number looks exactly like a right one. A
parser differential (sqlglot reads one thing, the engine runs another) is closed by reading it twice: the engine's own
parser must also see one `SELECT` (`engine_parse`), and the engine's own read-only means hold whatever both miss. For
ClickHouse the second reading is `EXPLAIN AST`, which also names the tables as ClickHouse sees them.

## 2026-10-24 · Every table node is a table read unless proven a CTE's name

The first version asked sqlglot's scopes where tables are and trusted what they called a CTE. A refuting pass found
`FROM (secret.t) x` read past it (sqlglot wraps a parenthesised table as a derived table), and a CTE named like a
schema-qualified table could have shadowed it. Now every table node in the query is checked against `readable`, except
one sqlglot resolves to a CTE **and** that is unqualified — as SQL resolves names. Unknown shapes are refused, not
passed. sqlglot's failures while following a query's structure are refusals, never crashes.

A second refuting pass (a separate reader, every DuckDB attack run through the server's own path against a real file)
found no DuckDB bypass; it found that a deny-list of single names cannot hold a function family — ClickHouse's
`dictGet*` and `joinGetOrNull` read a dictionary or a table by name past `joinget`. The engine's deny-list takes globs
now; the ClickHouse adapter will check a dictionary's name against `readable` rather than deny the family outright.

## 2026-10-24 · A large table's bound: from below, on the column, in the query that reads it

`bound_by >= / > / = / BETWEEN / IN` against a value (no column, no subquery), a top-level `AND` of the `WHERE` /
`PREWHERE` of the `SELECT` that reads the table, or of its own `JOIN … ON`; qualified by its alias when that `SELECT`
reads more than one table. An upper bound alone, a bound under `OR`, a function of the column, or a bound in an outer
query does not count. The guard checks the bound is there, not how wide it is (`>= '1970-01-01'` passes — a real agent
noticed it on its own); a widest window per large table is a candidate for the metrics layer's populations, which carry
"the longest window one query may scan".

## 2026-10-24 · Answers are compact JSON text, numbers exact, capped with a note

Tools that read a database answer JSON text (no duplicate structured copy), so the `answer_characters` cap measures
exactly what is sent. Rows are arrays in column order; a decimal a JSON number cannot hold exactly is a string, never
rounded; rows past the connection's limit are never read (`max_rows + 1` fetched to know there were more). Every cut —
by rows or by the answer's size — sets `truncated`, `truncated_by` and a `note`. Refusals and errors are tool errors
whose text says what to do.

## 2026-10-24 · ClickHouse next, then the semantic layer; the site after them

**The author**: production work runs on ClickHouse, and what matters is the semantics. So the ClickHouse adapter comes
next (milestone 2), then the semantic layer with your own metadata and the dbt importer (milestone 3 — the importer
moved up from metrics: a team whose semantics already live in a dbt project should not write them twice), then the
docs site (milestone 4), then the other engines and the metrics layer.

## 2026-10-24 · Your own metadata: YAML in `custom_extensions`, declared in a vocabulary; nothing of one catalog in code

**The author** (option B of three; flexibility first — "don't hard-code our current logic"). What a team adds to its
tables and columns — categories (tags from a closed list), a layer, links to the code that writes a table, owners,
anything — is **its own**, and the server must hold any of it without a line of code changed.

- **Where it is written**: in Ossie's own place for it, `custom_extensions` on the model, a dataset, a field, a metric
  or a relationship, under the team's `vendor_name`. Ossie requires `data` to be a JSON **string**; we also read it
  written as an ordinary **YAML mapping** — readable and checkable by hand. Our file differs from strict Ossie only
  there; a strict Ossie document is exported on request, and a strict one is read as it is. (Option A — JSON strings
  only — is unreadable by hand and unchecked; option C — a separate file keyed by `table.column` — splits one table's
  description in two places.)
- **A vocabulary** per `vendor_name` (optional): which keys exist, on which levels, of which type, and — for a closed
  list such as categories — which values, each with its definition. Checked at start: an unknown key or value stops the
  server, naming where it is. Without a vocabulary, a vendor's data passes unchecked.
- **Served as written**, with the vocabulary's definitions beside it — an agent reads what a category means, not only
  its name.
- **The server knows no team's keys.** There is no `tags`, `layer`, `provenance` or `dto_link` in code. Where a team's
  own metadata implies one of Ossie's own places (a column marked as the partition is the time column), that mapping
  is written in the importer's configuration — the team's, in its repository — never in code.

## 2026-10-24 · The team's own instructions and context, per connection and for the whole server

**The author**: semantics is not enough — agents must also get the team's own kitchen, either written in the
configuration or as a path to where the context lives. So `instructions` (text in the configuration) and `context`
(files and folders) at both levels; instructions delivered at connect time in the server's instructions and in
`list_connections`, documents listed and read by tools, everything as written, read anew on every call. Local paths
now (a clone of the team's repository beside the configuration); a git source fetched by the server comes with
deployment. Skills follow the same shape later.

## 2026-10-24 · ClickHouse: GET for read-only, every overflow mode `throw`, the engine names the tables

Every request is a `GET`, which ClickHouse itself runs read-only for any user. Every limit goes with `throw`, because
a profile's `break` answers a part as if it were the whole — shown live: a sum over 8,198 of 100,006 rows, no warning.
The result's rows use `break` with one row more than shown and the bytes limit off, so a cut is always visible. The
engine's own reading is `EXPLAIN QUERY TREE`, whose table nodes are held to the same rules as the guard's tables — it
sees `x IN (db.table)` as a table where sqlglot sees a column. Dictionaries are checked, not denied: the guard reads
the dictionary's name from `dictGet('db.dict', …)` and checks it against `readable`. Tested against a real ClickHouse
in a container (CI: a service container).

## 2026-10-24 · Semantic models belong to a connection, are checked once at start and held to it

`semantic_models` is a connection's key: each model describes that connection's tables. `vocabularies` sit at the top
of the configuration, one per `vendor_name` for the whole server — a team's keys mean the same on every connection. A
model is read and checked once, at start, as a whole (its schema, Ossie's rules, its connection, its own metadata), so
an edit is served after a restart — unlike a context document, which is read anew on every call: a model checked
against the database cannot be half-valid. A dataset's `source` is held to its connection: a table name must be
readable and exist there, a query (`SELECT` / `WITH`) must pass the guard as an agent's query would, anything else is
refused — a model is never a way around `readable`. Ossie 0.1.1 is read unwrapped, its closed `vendor_name` list kept:
a vendor of one's own needs 0.2.0.dev0.

## 2026-10-24 · Semantic answers: as written, lists short, made smaller in a fixed order

Lists (`list_datasets`, `list_metrics`) show a description's first line (at most 200 characters, `…` when there is
more), synonyms, and only the own metadata its vocabulary marks `in_lists` — the rest is one `describe_…` away. A
vocabulary's definitions come beside an answer for the values it shows, not the whole list. An answer too long for
`answer_characters` is made smaller in a fixed order, each step named in `note`: list items left out (from the end);
in `describe_dataset`, before any field, `other_columns` to names, then `other_columns`, `relationships`, `metrics`,
`vocabulary` left out; last, the longest values cut, each ending in `…` (`truncated_by: long_values`) — and an answer
that cannot fit even so says only that. Nothing is reworded to fit.

## 2026-10-24 · Every file read as written

A file the server reads — the configuration, a model, a vocabulary — is read the way its author meant it: only
`true`/`false` are booleans (YAML 1.2; `no`, `on`, `yes` stay text), a date stays its text, a key written twice or a
list as a key is refused, aliases may not expand past a million values, the file must be UTF-8. A model holds only
what JSON can (no sets, no `.inf`, no two keys reading as one), and a JSON string's numbers stay exact. An error names
the line and column, never the line, and a password inside a URL is redacted from every problem; a value under a
secret-like key is never repeated.

## 2026-10-24 · The dbt importer writes an Ossie file; MetricFlow's metrics come through Ossie's own converter

**The author** (the agent's three recommendations, accepted). `semantic-warehouse-mcp import-dbt` reads a dbt
project's `manifest.json` and writes an Ossie document, which the server then serves like any model — reviewable, its
changes visible, checked by `--check` — rather than the server reading the manifest at start. A dataset's fields are
the columns the dbt project describes; the rest of a table's columns are already served live (`other_columns`).
Which tables, under which `vendor_name` `meta` and tags go, and which of a team's keys stand for Ossie's own places (a
tag meaning the time column) are the importer's configuration — the team's; the code reads only what is dbt's own
(descriptions, `constraints`, `relationships` tests). Metrics defined in MetricFlow are not imported a second way:
Ossie's converter (`ossie-dbt msi-to-ossie`, apache/ossie-converters) does it, and the guide points to it.

## 2026-10-24 · What the importer names, leaves out and refuses

Within the author's decision above (the agent's, from a real catalog and a second reader). Dataset names are dbt's:
a model's `name` (`_v<n>` for a version), a source table's `<source>__<table>` — unique, stable, and never read as
`dataset.field`. A source is `schema.table`, the catalog left out; a table of another database than the one the
project's models are built in is **left out and counted**, never written as two parts that would bind to the
connection's own table of that name. A relationship is one per pair of columns (`<from>__<column>__<to>`, the target
column added when it differs), whatever order dbt wrote the tests in. **Refused, naming where**: a manifest not shaped
as dbt writes it; a name holding a backslash or a control character (each engine reads them its own way); a value no
Ossie document holds (not finite, not Unicode, nested past 64 levels). A lone surrogate is refused by every reader of
the server, an escaped surrogate pair read as its character — it passed every check before and broke every answer.
Any error of the importer's own exits 2, so `--check`'s 1 always means "not current".

## 2026-10-24 · A ClickHouse user with `readonly = 1` is refused at start, naming `readonly = 2`

**Superseded the same day** by "A ClickHouse user with `readonly = 1` is served within its profile" below.

The agent's recommendation (variant A of two), built when the author said to go on; variant B stays open. Found on a
real installation: the read-only user agents were given had `readonly = 1` and no limit in its profile
(`max_execution_time` 0). Such a user may change no setting — measured on ClickHouse 26.3: a limit or an overflow mode
sent with a query is refused (code 164), while format settings, `database` and query parameters pass — so none of the
server's limits would reach its queries, and nothing would bound them. The server stops at start with the reason and
the fix: `readonly = 2`, which takes the limits and still writes nothing (every request a GET; the grants). Variant B —
run within the profile's own limits, reading them from `system.settings` at start, refusing a profile that cuts
silently or sets no time limit, cutting the rows by reading one more than shown — would need the same change from a
DBA for that user (it sets no time limit), and is not built until someone needs it.

## 2026-10-24 · An engine's reading is trusted only when it shows it resolved the query

Measured on ClickHouse 22.11 and 23.8: `EXPLAIN QUERY TREE` exists there but answers the tree as parsed — names as
`IDENTIFIER`, no `TABLE` node — so the engine's reading would name no table, and `x IN secret.t` (the bypass the
engine's reading closes) would pass. Those servers stopped at start only because they did not know a setting the
adapter sends: a protection held by luck. Now `check()` requires the tree of `SELECT 1` to name `system.one` as a
table, and a query whose tree holds an `IDENTIFIER` is refused — a reading that cannot show it found the tables is
no reading. Whether servers before 24.3 are served at all (through `EXPLAIN AST`, read by the adapter) is open.

## 2026-10-24 · Streamable HTTP and the image now, ahead of the release: a token, `/health`, stateless

The author: a company's production deployment is wanted before the package is on PyPI. So the HTTP transport and the
image come now (from milestones 8–9); the agent's defaults, each the cautious one: **a token is required** for a
server other machines reach (`--token-env`, constant-time compared, checked before any database is reached) — a
server on loopback may go without; **`/health`** needs no token and reaches no database, as a readiness probe should;
**stateless, JSON answers** — replicas behind one address, nothing held per session; the **image** runs as a user
without rights on a read-only root filesystem, and is published only for a release tag, never as `latest`. Per-person
identity is the gateway's, not the server's (Later).

## 2026-10-24 · A ClickHouse user with `readonly = 1` is served within its profile (variant B)

**The author**: use the read-only user employees already have — `readonly = 1`, the usual read-only grant. So the
adapter learns, at start and with no setting sent, the user's own settings (`system.settings`): for a `readonly = 1`
user it sends no limit (only the answer's format, the query's id, parameters — what such a user may send, measured on
22.11 and 26.3), and bounds the query itself: the answer read as it comes and no further than one row past what is
shown, the query stopped by its id (`KILL QUERY`, the user's own) once enough is read or past its time. What it cannot
override, it checks: a profile with a limit in a mode that cuts silently is refused, naming it; a user that may not
stop its queries and whose profile sets no time limit is refused (a query past its time would run on). An error met
after rows were sent — a line of text (22.x), a row of one message (25.x), an `__exception__` block (26.x) — is an
error, never a short answer. The principle holds: no answer cut without saying so; time bounded, by the server.

## 2026-10-24 · ClickHouse before 24.3 served through its analyzer's resolved tree

The author gave a warehouse on 22.11 to serve. Measured: `EXPLAIN QUERY TREE run_passes = 1` makes 22.11's
experimental analyzer resolve the tree — 26 of 27 typical analyst queries (CTEs, joins, `ARRAY JOIN`, windows,
`GLOBAL IN`, `x IN db.table` with its table named), the one refusal a query that is an error anyway. So the adapter
always asks for the resolved tree (the default from 24.3) and keeps refusing one it cannot resolve; settings a server
does not know are not sent. `EXPLAIN AST`, read by the adapter, is not needed.

## 2026-10-24 · A model's table the database does not hold may be left out (`missing_tables: leave_out`)

Found on a real warehouse: a model imported from a dbt project's main named two models not built yet, and the server,
holding every source to its connection, would not start. A connection may now say `missing_tables: leave_out`: such a
dataset is taken out of the model with its relationships, named by `--check` and in the server's log; `refuse` stays
the default. A source that is not readable is refused either way — leaving out is only for what is not there.

## 2026-10-24 · 0.1.0 released, pre-alpha, by trusted publishing

**The author**: publish the package now, ahead of milestone 9. Version 0.1.0, marked pre-alpha (interfaces may still
change). The package goes to PyPI by trusted publishing — no token anywhere: PyPI trusts `publish.yml` in the `pypi`
environment of this repository, as the author's detectkit does — and only for a tag `v<version>` that is
`pyproject.toml`'s version, so a release is one commit and one tag. The image is published for the same tag (GHCR,
public, its version and commit, never `latest`). The sdist carries the code, the tests, the examples and the guide —
not `dev/`, the session files or CI. 1.0 stays milestone 9's: docs for agents and operators, a second MCP client.

## 2026-10-24 · Skills: folders in the Agent Skills format, checked at start, read anew

A skill is a folder holding a `SKILL.md` — the [Agent Skills](https://agentskills.io/specification) format, the one
clients already read — with whatever it refers to beside it (`references/`, `scripts/`, `assets/`). A real data
agent's skill, studied as a requirement, is such a folder: a `SKILL.md` naming dozens of rule files beside it. So
`get_skill` serves the `SKILL.md` with the list of its folder's files, and any of those files by the name the list
gives — never a path an agent writes. Like `context`: for the whole server and per connection; checked at start
(the format's own rules on `name` and `description`, the name the folder's; no two of one name) and read anew on every
call, so an edit is served without a restart and a skill broken later is named in the answer. Files that are not text
(not UTF-8, or holding NUL) are listed apart and not served; hidden files never. The frontmatter's other keys are a
client's own: served as written, not checked.

## 2026-10-24 · Context and skills straight from a git repository: named, fetched by git, kept fresh

**The author** chose each part: repositories declared once by name (`repositories:`), an entry naming a path inside one
— one fetch, one token, one refresh for every entry of a repository; fetched at start and every `refresh_minutes`
(default 10) in the background; by the `git` program; context and skills only (models and vocabularies, checked once
at start and in a company built by its CI, later if wanted). The server fetches only the named paths of one commit
(`--depth 1 --filter=blob:none`, then those paths checked out into a folder per commit). A new commit is served only
when it passes the start checks; otherwise, or when the repository cannot be reached, the commit served stays and
`list_connections` says why. git runs in an environment of the server's own — no system, user or environment
configuration, no credential helper, no prompt, no redirect, no protocol but https (http to loopback), symbolic links
written as files — and the token goes as a header through the environment, never on a command line. The image carries
git without Perl (89 MB more than 0.1.0's).

## 2026-10-24 · A model's expression runs as written, in its connection's dialect — never translated

**The author**: Ossie names no dialect of ClickHouse, DuckDB or PostgreSQL, and a model already belongs to one
connection. So an `ANSI_SQL` expression is read in the dialect of the connection its model belongs to and runs as
written — what an analyst wrote and checked on the warehouse is what runs. No translation: measured, sqlglot's
ANSI-to-ClickHouse turns `CAST(ts AS DATE)` into `CAST(ts AS Nullable(DATE))` — a translation is not neutral, and a
changed number looks like a right one. A model meant for several engines writes a variant per engine in this server's
own `custom_extensions` entry, chosen by the connection's engine before `ANSI_SQL`. Built with the metrics (milestone 6),
when the server first compiles SQL from expressions: then every expression is parsed in its connection's dialect at
start, and one that does not parse stops the server, naming it. Until then expressions are served as written.

## 2026-10-24 · The site at `swm.pipelab.dev`

**The author**: a short address without a dash, as detectkit's `dtk.pipelab.dev` — `swm`, the abbreviation the project
already uses (`SWM_TOKEN`). The package, the repository and the image keep `semantic-warehouse-mcp`: published already,
and a rename would cost every installation; only the site's address was still free to choose.

## 2026-10-25 · Each engine's driver is an extra, as in detectkit

**The author**: drivers apart, as detectkit has them — install every one, or only those you use. `[postgres]`
(psycopg), `[duckdb]`, `[all]`; `[clickhouse]` installs nothing more (HTTP through httpx, which the server has anyway).
An adapter imports its driver only when used; a connection whose driver is missing stops the server at start, naming
the extra; `--check --offline` needs none. The image holds every driver; the development environment too (the `dev`
group depends on `[all]`). DuckDB moved out of the base install with it — the getting-started works from the source,
where `uv sync` installs it.

## 2026-10-25 · PostgreSQL: a read-only transaction, a cursor, the plan read through the views a query names

Decided by the agent, at the author's word ("decide yourself"). Every query in a transaction opened `READ ONLY`, the
session's default read-only too (`-c default_transaction_read_only=on`), its `statement_timeout` the connection's
seconds and a client-side cancel a few seconds later; rows read through a server-side cursor, one past `rows`. What a
read-only transaction does not stop is refused by name (files, SQL from a string, other sessions, locks, settings,
sleep). A superuser, or a member of the roles that reach the server's files or programs, is refused at start. The
engine's reading is the plan (`EXPLAIN (VERBOSE, FORMAT JSON)`, in the same transaction, never run): a plan reads a
view's own tables in its place, so the base tables of the views the query names — which the guard held readable — are
left to them; a partition reads as its root; every other relation is held to `readable`. psycopg 3 as the driver.

Refuted by a second reader the same day: index maintenance (`brin_*`, `gin_clean_pending_list`) writes past a
read-only transaction for an owner — now refused by name, and a user that owns anything (or inherits from an owner) is
refused at start. A function defined in the database reads with the user's grants, unseen by the guard and the plan:
the grants are the line, so the server notes — never refuses — a user that may read beyond `readable`, or holds
`pg_read_all_data` or `BYPASSRLS`. Catalog metadata (sizes, definitions) is not data, and is not refused.

## 2026-10-25 · MySQL: a read-only session, text MySQL runs as comments refused, the plan where it names tables

Decided by the agent, at the author's word ("decide yourself, continue"). Measured on 8.4 before writing it:
`/*! … */` is SQL to MySQL and a comment to sqlglot — `SELECT id /*! , (SELECT name FROM secret.t) */ FROM t` passed
the guard and read the secret table — so a query holding `/*!` or `/*+` (hints: `MAX_EXECUTION_TIME`, `SET_VAR` that
lifts `sql_select_limit`) is refused before it runs, even inside a string. A read-only transaction stops writes and
`FOR UPDATE`, not `GET_LOCK` or `SLEEP`: those are refused by name, with `LOAD_FILE`, `BENCHMARK` and replication waits.
A user with `FILE` or `SUPER` on every database is refused at start. Rows: `sql_select_limit` one past `rows`, then
read as they come and the rest stopped (`KILL QUERY` from another connection — a statement's own `LIMIT` outranks
`sql_select_limit`); time: `max_execution_time`, then `KILL QUERY` past a margin. The plan (`EXPLAIN FORMAT=JSON`,
`explain_json_format_version = 2`, MySQL 8.3+) names schema and table — read through the named views
(`VIEW_TABLE_USAGE`) — but leaves out the tables MySQL reads while planning (one row, a unique lookup): a second layer
only, the guard's reading the first; before 8.3 a plan names aliases, so it only says whether MySQL can plan the
query. PyMySQL (pure Python) in the `mysql` extra; TLS on by default.

Refuted by a second reader the same day: on a server whose `sql_mode` holds `NO_BACKSLASH_ESCAPES`, a string ends at
`\'` for MySQL and not for sqlglot — `SELECT 'a\' AS x, (SELECT ssn FROM secret.people WHERE id = 1) AS leaked -- '`
read a secret row past the guard, and past the plan (a lookup by key is read while planning). Closed at the source:
every session drops the modes that change how text is read (`NO_BACKSLASH_ESCAPES`, `ANSI_QUOTES`, `ANSI`) and keeps
the rest; `check()` refuses a session that keeps one. Also: `UUID_SHORT` (a server counter) refused,
`SQL_CALC_FOUND_ROWS` refused; a stored function reading past `readable` is noted, as for PostgreSQL.
