# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 25 October 2026.

**Released: 0.6.0, 0.6.1, 0.7.0** (PyPI, GHCR, the site) — 0.6.0: what a company's earlier data agent had and the
server lacked (lineage and SQL from dbt, a metadata filter and its menu, `narrowed_by`), the guard hardened by a second
reader; 0.6.1: ClickHouse before 24.3 — a `GROUP BY` over an `ALIAS` column answered, the tree as parsed read by the
rules of the interpreter that runs the query; 0.7.0: the dbt importer chooses tables as dbt selects them (`select`,
`exclude`). **Prepared: 0.8.0** — `import-database`: a draft of a semantic model from a database's own catalog, for a
database no dbt project describes. **The site is live at https://swm.pipelab.dev.** Milestones 0–4 done; **current:
milestone 5** — PostgreSQL, MySQL and the ClickHouse-before-24.3 item done, BigQuery and Snowflake put off by the
author, the self-check at start remains.

## What exists

- **The server**: stdio or `--http` (a token off loopback, `/health`, stateless); `--check`, `--check --offline`.
  14 read-only tools: `about`, `list_connections`, `list_tables`, `describe_table`, `run_query`, `list_context`,
  `get_context`, `list_skills`, `get_skill`, `list_datasets`, `describe_dataset`, `list_metrics`, `describe_metric`,
  `list_metadata`.
- **Engines** (`adapters/`): ClickHouse (HTTP, `readonly = 1` users within their profile, 22.11 and 24.3–26.3; 23.x
  refused at start unless the analyzer is on), PostgreSQL, MySQL, DuckDB; drivers are extras.
- **The guard**: one SELECT over `readable` tables; a large table read alone in its SELECT, bounded by `bound_by`,
  held to named values of `narrowed_by`; on ClickHouse an alias's name is no bound; read twice — the engine's own
  reading held to the same rules. On ClickHouse where the analyzer does not run queries (`analyzer_runs`, learnt at
  start): the resolved tree and the tree as parsed (`run_passes = 0`), the latter read as a tree every line of which
  ClickHouse could print (a quoted name's line break makes lines) and resolved by the interpreter's rules as measured
  on 22.11 (CTE scope, aliases right of `IN`); the parsed tree alone where the analyzer cannot resolve the query.
- **The semantic layer**: Ossie models, vocabularies, the dbt importer (`dbt_tags`, `dbt_depends_on`, `dbt_code`;
  tables by name and by dbt's own selection — `select`, `exclude`); the database importer (`import-database`: a
  draft from the catalog — readable tables, columns, the database's comments, time columns by type),
  `metadata` filters, `list_metadata`, `describe_dataset` cutting long own-metadata texts before a field.
- **Context and skills** from disk or git repositories; **the site** (`website/`); the start's log counts the
  datasets left out and names five.

## How it was verified

- 825 tests pass, none skipped (ClickHouse 25.8 and 22.11, PostgreSQL 17, MySQL 8.4 in containers — 22.11 now in CI
  too); ruff clean; the site builds.
- The 22.11 reading: 28 mutations of its protections, 27 caught, the survivor a redundant condition (removed). A
  second reader on 22.11 and 26.3 found no query past both layers; two false refusals (a quoted name turning off
  aliases right of `IN`; the analyzer taking a CTE inside `IN (SELECT …)` for a table) fixed, held by tests.
- A real installation (22.11): the `GROUP BY` over an `ALIAS` column answered through the server, equal to the same
  query grouped by the stored column.
- The running guide's own-image recipe built on the 0.6.0 image: a good configuration builds, a broken one stops it.
- The importer's selection: 16 mutations, all caught; on a real project (scratch copy), `select: [tag:<its daily
  run's tag>]` left out exactly the models its scheduler never builds.
- The database importer: 11 mutations, all caught; tested on DuckDB, PostgreSQL 17, ClickHouse 25.8; a real
  warehouse drafted (263 tables, 5,776 columns, 28 s) and bound to it by the server.

## Decided (`dev/DECISIONS.md`)

This sitting: what the old data agent had (lineage, SQL, a metadata filter and its menu, `narrowed_by`); a large table
read alone in its SELECT, alias names no bound on ClickHouse; ClickHouse before 24.3 — the tree as parsed read too, by
the interpreter's rules; the dbt importer choosing tables as dbt selects them — the agent, at the author's word ("do
everything"; "our wants made to work in the library, optionally").

## Next step

1. Tag `v0.8.0` with this commit (the author said push).
2. Milestone 5's rest (`dev/PLAN.md`): the self-check at start. Then milestone 6, metrics.
3. From a company's use, for the author to decide (general terms): telling agents a table stopped being refreshed (its
   last change); who is asking, carried to the database's query log behind a gateway.
4. Later, from the second readers: a `narrowed_by` / `bound_by` column the table lacks named at start; `dictGet` /
   `joinGet` and views stay the guard's alone (the engine's readings do not name what is behind them).

## What the author needs to do

1. Decide item 3 of the next step.
2. For the container tests locally, Docker running.

## Don't

- Don't write a real company, database, schema, table or semantics anywhere here — the repository is public; numbers
  measured on a real installation go here in general terms only.
- Don't name any team's metadata keys in code — they are declared in a vocabulary.
- Don't put an engine's specifics outside its adapter (an engine's behaviour the guard needs is an adapter's flag);
  don't import a driver anywhere but its adapter, guarded.
- Don't add a tool without `read_only_hint=True`, `destructive_hint=False`, or without its entry in `docs/reference/tools.md`.
- Don't add a protection without a test that fails when it is removed; don't count a mutation run whose tests skipped
  or errored (a fixture that cannot set the server up is no catch).
- Don't trust the analyzer's tree on a ClickHouse whose analyzer does not run queries as the reading of what runs.
- Don't rephrase a model's descriptions, instructions, documents or skills — serve them as written.
- Don't serve over HTTP without a token anywhere but loopback; don't let a token reach git's command line.
- Don't tag a version that is not `pyproject.toml`'s; don't publish `latest` for the server's image.
- Don't point `SWM_TEST_*_URL` at a real server: the tests rebuild `swm_*` databases there.
- Keep the landing page's panels true: they are the server's real answers on the made-up shop.
