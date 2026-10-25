# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 25 October 2026.

**Released: 0.5.0, pre-alpha** — on PyPI and GHCR (`ghcr.io/alexeiveselov92/semantic-warehouse-mcp:0.5.0`): lists for
large models (every table named when a list does not fit), ranked search; 0.4.0 brought MySQL, 0.3.0 PostgreSQL and
drivers as extras, 0.2.0 skills and repositories. **The site is live at
https://swm.pipelab.dev**, redeployed on every push that changes `docs/` or `website/`. Milestones 0–4 done.
**Current: milestone 5, the other databases** — PostgreSQL and MySQL done; BigQuery and Snowflake put off by the
author ("enough databases for now"); the self-check at start remains.

## What exists

- **The server**: stdio or `--http` (a token off loopback, `/health`, stateless); `--check`, `--check --offline` (no
  database, no secret, no git, no driver). 13 read-only tools: `about`, `list_connections`, `list_tables`,
  `describe_table`, `run_query`, `list_context`, `get_context`, `list_skills`, `get_skill`, `list_datasets`,
  `describe_dataset`, `list_metrics`, `describe_metric`.
- **Engines** (`adapters/`, each with its page section in `docs/reference/engines.md`):
  - **ClickHouse** — HTTP GET, every limit `throw`, `readonly = 1` users within their profile; 22.11–26.3.
  - **PostgreSQL** — a read-only transaction, statement timeout and a client cancel, a server-side cursor, the plan's
    tables held to `readable` through the views a query names; a superuser, file roles and owners refused.
  - **MySQL** (new) — a read-only session and transaction; the `sql_mode` modes that change how text is read
    dropped per session; `/*! */`, `/*+ */`, `SQL_CALC_FOUND_ROWS` refused; `max_execution_time` and `KILL QUERY`;
    rows read as they come and stopped one past the limit; `FILE`/`SUPER` users refused; the plan (8.3+) as a second
    layer.
  - **DuckDB** — read-only file, external access off.
- **Drivers are extras**: `[postgres]`, `[mysql]`, `[duckdb]`, `[all]`; ClickHouse needs none. The image and the dev
  environment hold every driver. A user who may read beyond `readable` is noted at start (a function in the database
  reads with its grants).
- **The configuration, semantic models (Ossie), vocabularies, the dbt importer, skills, repositories, the guard** — as
  before. **Large models**: an agent never reads a model whole; `list_datasets` / `list_metrics` too long for one
  answer name every item by model (`compact`) — measured on two real dbt-built models (427 and 715 tables, ~4 MB of
  YAML each): every table named in 15 / 21 thousand characters; a search lists the closest first. **The site** (`website/`): a landing page of the server's real answers.
- **Examples**: the made-up shop in DuckDB, or loaded into ClickHouse, PostgreSQL or MySQL (`make_shop.py --clickhouse`
  / `--postgres` / `--mysql`, `examples/config.<engine>.example.yaml`).

## How it was verified

- 678 tests pass, none skipped (real ClickHouse, PostgreSQL 17 and MySQL 8.4 in containers; a git server on loopback);
  ruff clean; CI runs all three servers as services.
- MySQL refuted by a second reader: 1 high — on a server with `NO_BACKSLASH_ESCAPES` a string ended where the guard
  did not see it end and a secret row ran past the guard and the plan — closed at the source (the session's
  `sql_mode`), with 2 minor; PostgreSQL's high (index maintenance) closed earlier the same day.
- Every protection of the sitting held by a test that fails when it is removed (mutation runs over each adapter; a run
  whose tests skipped is not counted — rerun alone).
- Real Claude Code through MySQL: May 2026 orders and revenue by currency (EUR 1,235 / 64,001.63; USD 416 /
  20,087.98) and a January signup cohort (334 of 335) — every figure checked by a direct query. Through PostgreSQL:
  April revenue by country, checked.

## Decided (`dev/DECISIONS.md`)

This sitting: drivers as extras (the author); PostgreSQL's and MySQL's read-only lines, how each plan is read, which
users are refused and which noted (the agent, at the author's word); MySQL's session `sql_mode`; releases 0.2.0–0.4.0
so the site never promises what PyPI lacks; the site at `swm.pipelab.dev`.

## Next step

Milestone 5's rest (`dev/PLAN.md`): the self-check at start (probes with known verdicts through the guard and the
engine). Then milestone 6, metrics. BigQuery and Snowflake wait in Later.

## What the author needs to do

1. Nothing blocks. For the container tests locally, Docker running.

## Don't

- Don't write a real company, database, schema, table or semantics anywhere here — the repository is public.
- Don't name any team's metadata keys in code — they are declared in a vocabulary.
- Don't put an engine's specifics outside its adapter; don't import a driver anywhere but its adapter, guarded.
- Don't add a tool without `read_only_hint=True`, `destructive_hint=False`, or without its entry in `docs/reference/tools.md`.
- Don't add a protection without a test that fails when it is removed; don't count a mutation run whose tests skipped.
- Don't rephrase a model's descriptions, instructions, documents or skills — serve them as written.
- Don't serve over HTTP without a token anywhere but loopback; don't let a token reach git's command line.
- Don't tag a version that is not `pyproject.toml`'s; don't publish `latest` for the server's image.
- Don't point `SWM_TEST_*_URL` at a real server: the tests rebuild `swm_*` databases there.
- Keep the landing page's panels true: they are the server's real answers on the made-up shop.
