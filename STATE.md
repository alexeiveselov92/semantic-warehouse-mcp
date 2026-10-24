# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 25 October 2026.

**Released: 0.3.0, pre-alpha** — on PyPI and GHCR (`ghcr.io/alexeiveselov92/semantic-warehouse-mcp:0.3.0`):
PostgreSQL, drivers as extras; 0.2.0 brought skills and repositories. **The site is live at https://swm.pipelab.dev**, redeployed on every push
that changes `docs/` or `website/`. Milestones 0–4 done. **Current: milestone 5, the other databases** — PostgreSQL done
and released, drivers made extras; next MySQL, then BigQuery and Snowflake, and the self-check.

## What exists

- **The server**: stdio or `--http` (a token off loopback, `/health`, stateless); `--check`, `--check --offline` (no
  database, no secret, no git, no driver). 13 read-only tools: `about`, `list_connections`, `list_tables`,
  `describe_table`, `run_query`, `list_context`, `get_context`, `list_skills`, `get_skill`, `list_datasets`,
  `describe_dataset`, `list_metrics`, `describe_metric`.
- **Engines** (`adapters/`): **ClickHouse** (HTTP GET, every limit `throw`, `readonly = 1` users within their profile;
  22.11–26.3), **PostgreSQL** (new: a read-only transaction, statement timeout and a client cancel, a server-side cursor,
  the plan's tables held to `readable` through the views a query names, a superuser / file roles / an owner refused at
  start, notes for grants beyond `readable`), **DuckDB** (read-only file, external access off).
- **Drivers are extras** (as detectkit): `[postgres]`, `[duckdb]`, `[all]`; ClickHouse needs none. A missing driver is
  named at start with its extra. The image and the dev environment hold every driver.
- **The configuration**: connections (`readable`, `large_tables`, `limits`, `instructions`, `context`, `skills`,
  `semantic_models`, `missing_tables`), `vocabularies`, `repositories`; secrets by variable name, never repeated.
- **Semantic models** (Apache Ossie), **vocabularies**, **the dbt importer**, **skills**, **repositories** — as before.
- **The guard**: one `SELECT`, `readable` everywhere, large tables bounded, denied functions per engine; 12 refusals.
- **The site** (`website/`, Astro + Starlight from `docs/`): a landing page of the server's real answers; `website.yml`
  → GHCR → pipelab's `Deploy swm`.
- **Examples**: the made-up shop in DuckDB, or loaded into ClickHouse or PostgreSQL (`make_shop.py --clickhouse` /
  `--postgres`, `examples/config.*.example.yaml`).

## How it was verified

- 629 tests pass (real ClickHouse and PostgreSQL 17 in containers; a git server on loopback); ruff clean; CI has a
  PostgreSQL service too.
- PostgreSQL refuted by a second reader: 1 high (index maintenance wrote past the read-only transaction for an
  owner), 1 medium, 2 low — all closed or noted (`CHANGELOG.md`); what held is in `dev/DECISIONS.md`.
- Every protection of the sitting held by a test that fails when it is removed (mutation runs over skills,
  repositories and PostgreSQL).
- Real Claude Code through PostgreSQL: April 2026 revenue by country (DE 18,189.05 EUR, 369 orders; US 18,071.35
  USD; FR 13,061.95 EUR), every figure checked by a direct query; `raw.events` unbounded refused.
- 0.2.0 and 0.3.0 published by their tags; the site checked live (pages 200, a missing one 404).

## Decided (`dev/DECISIONS.md`)

This sitting: drivers as extras (the author); PostgreSQL's read-only line, its plan read through the named views, the
owner refused, grants beyond `readable` noted (the agent, at the author's word); 0.2.0 released; the site at
`swm.pipelab.dev`; expressions run as written in their connection's dialect (milestone 6).

## Next step

Milestone 5 (`dev/PLAN.md`): MySQL (a container, `[mysql]` with PyMySQL), then BigQuery and Snowflake (no containers:
an emulator and fakesnow), and the self-check at start (probes with known verdicts). A release (0.4.0) when the author
wants MySQL out.

## What the author needs to do

1. Nothing blocks. A release later: bump `pyproject.toml`, tag `v<version>` (the agent did 0.2.0 and 0.3.0 at the
   author's word, so the site never promised what PyPI did not have).
2. For the container tests locally, Docker running.

## Don't

- Don't write a real company, database, schema, table or semantics anywhere here — the repository is public.
- Don't name any team's metadata keys in code — they are declared in a vocabulary (an answer key named like one is
  caught too — hence `fetched_from`).
- Don't put an engine's specifics outside its adapter; don't import a driver anywhere but its adapter, guarded.
- Don't add a tool without `read_only_hint=True`, `destructive_hint=False`, or without its entry in `docs/reference/tools.md`.
- Don't add a protection without a test that fails when it is removed.
- Don't rephrase a model's descriptions, instructions, documents or skills — serve them as written.
- Don't serve over HTTP without a token anywhere but loopback; don't let a token reach git's command line.
- Don't tag a version that is not `pyproject.toml`'s; don't publish `latest` for the server's image.
- Don't point `SWM_TEST_CLICKHOUSE_URL` or `SWM_TEST_POSTGRES_URL` at a real server: the tests rebuild `swm_*` there.
- Keep the landing page's panels true: they are the server's real answers on the made-up shop.
