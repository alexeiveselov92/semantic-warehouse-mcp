# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 25 October 2026.

**Prepared: 0.6.0, pre-alpha** — committed, not yet tagged (the author decides the push): what a company's earlier
data agent had and the server lacked — what a model reads and its SQL from dbt, lists filtered by the team's own
metadata with `list_metadata` as their menu, `narrowed_by` for an event log — and the guard hardened by a second
reader (a large table read alone in its SELECT; on ClickHouse an alias's name is no bound). **Released: 0.5.0** on PyPI
and GHCR. **The site is live at https://swm.pipelab.dev**, redeployed on every push that changes `docs/` or `website/`.
Milestones 0–4 done; **current: milestone 5** — PostgreSQL and MySQL done, BigQuery and Snowflake put off by the
author, the self-check at start remains.

## What exists

- **The server**: stdio or `--http` (a token off loopback, `/health`, stateless); `--check`, `--check --offline`.
  14 read-only tools: `about`, `list_connections`, `list_tables`, `describe_table`, `run_query`, `list_context`,
  `get_context`, `list_skills`, `get_skill`, `list_datasets`, `describe_dataset`, `list_metrics`, `describe_metric`,
  `list_metadata` (new).
- **Engines** (`adapters/`): ClickHouse (HTTP, `readonly = 1` users within their profile, 22.11–26.3), PostgreSQL,
  MySQL, DuckDB; drivers are extras (`[postgres]`, `[mysql]`, `[duckdb]`, `[all]`).
- **The guard**: one SELECT over `readable` tables; a large table read alone in its SELECT (else
  `large_table_joined` — join it as a subquery), bounded by `bound_by`, held to named values of `narrowed_by` (new,
  `large_table_unnarrowed`); on ClickHouse (`aliases_shadow_columns`, an adapter's flag) a bare name an alias took
  counts for neither; `x IN (db.table)` refused; read twice — the engine's own reading held to the same rules.
- **The semantic layer**: Ossie models, vocabularies, the dbt importer — now with `dbt_depends_on` (the tables a model
  reads, through ephemeral ones) and `dbt_code` (its SQL as written) into the vendor's data; `list_datasets` /
  `list_metrics` take `metadata: {key: value | [values]}` (the dataset's or a field's — `matched_fields` names them; a
  search found by fields names them too; a key or value nothing holds named with the likely one); `list_metadata` —
  every key and value in use, counts, definitions; `describe_dataset` cuts own-metadata texts past 2000 characters
  before a field. Large models: lists too long name every item (`compact`).
- **Context and skills** from disk or git repositories; **the site** (`website/`): a landing page of real answers
  (its `list_datasets` panel now filters by a tag).

## How it was verified

- 739 tests pass, none skipped (real ClickHouse, PostgreSQL 17, MySQL 8.4 in containers; a git server on loopback);
  ruff clean; the site builds.
- Measured on two real dbt projects in a scratch copy (nothing of them here): SQL median 1.6 thousand characters, the
  largest 37 thousand; `describe_dataset` median ~10 / ~7 thousand characters with it; `list_metadata` of both models
  16 thousand; a filter by a tag and by a layer, lineage downstream of a staging table — all within one answer.
- The guard refuted by a second reader on ClickHouse 22.11–26.3 and DuckDB: 2 high (joins, aliases), 1 medium (views —
  documented), 3 low (one fixed, two documented). 23 mutations of the new protections, 23 caught (one survivor at
  first, closed with a test).
- Real Claude Code on the made-up shop: found what is built on `raw.events` (metadata filter), the money columns
  (`matched_fields`), quoted a model's SQL, counted 444 narrowed `cart_abandoned` events — checked by a direct query.

## Decided (`dev/DECISIONS.md`)

This sitting: what the old data agent had (lineage and SQL into own metadata under the importer's keys; a metadata
filter and its menu; `narrowed_by`); a large table read alone in its SELECT, and alias names no bound on ClickHouse
(the second reader's findings) — the agent, at the author's word ("do everything").

## Next step

1. The author: push `main` and tag `v0.6.0` (publishes PyPI and the image; the site redeploys).
2. Milestone 5's rest (`dev/PLAN.md`): the self-check at start. Then milestone 6, metrics.
3. Later, from the second reader: a `narrowed_by` / `bound_by` column the table lacks could be named at start (today
   every query over it is refused, naming the column).

## What the author needs to do

1. Say whether to push and tag 0.6.0.
2. For the container tests locally, Docker running.

## Don't

- Don't write a real company, database, schema, table or semantics anywhere here — the repository is public.
- Don't name any team's metadata keys in code — they are declared in a vocabulary.
- Don't put an engine's specifics outside its adapter (an engine's behaviour the guard needs is an adapter's flag);
  don't import a driver anywhere but its adapter, guarded.
- Don't add a tool without `read_only_hint=True`, `destructive_hint=False`, or without its entry in `docs/reference/tools.md`.
- Don't add a protection without a test that fails when it is removed; don't count a mutation run whose tests skipped.
- Don't rephrase a model's descriptions, instructions, documents or skills — serve them as written.
- Don't serve over HTTP without a token anywhere but loopback; don't let a token reach git's command line.
- Don't tag a version that is not `pyproject.toml`'s; don't publish `latest` for the server's image.
- Don't point `SWM_TEST_*_URL` at a real server: the tests rebuild `swm_*` databases there.
- Keep the landing page's panels true: they are the server's real answers on the made-up shop.
