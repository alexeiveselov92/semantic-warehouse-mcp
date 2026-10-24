# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 24 October 2026.

**Current milestone: 3 — the semantic layer, your own metadata, context and skills** (`dev/PLAN.md`). Milestones 0–2
are done. Milestone 3 so far: semantic models in Ossie, vocabularies, the four semantic tools, the dbt importer with the
strict export; ClickHouse users with `readonly = 1` refused naming the fix. Ahead of milestones 8–9, for a company's
deployment: Streamable HTTP with a token, `/health`, the container image. Open in milestone 3: ClickHouse before 24.3
(serve or not — the author's), skills, a git source, the dialect question.

## What exists

- **The server**: `semantic-warehouse-mcp --config <file>` over stdio, or `--http` (a token by `--token-env`, required
  off loopback; `/health` without a token or a database; stateless JSON answers); `--check`, `--check --offline` (for
  CI: no database reached, no secret read). Tools: `about`, `list_connections`, `list_tables`, `describe_table`,
  `run_query`, `list_context`, `get_context`, `list_datasets`, `describe_dataset`, `list_metrics`, `describe_metric` —
  all read-only, compact JSON with provenance, capped and saying so.
- **The image** (`Dockerfile`, `.github/workflows/image.yml`): non-root (10001), read-only root filesystem, port 8000,
  configuration at `/config`; built on every change, published to GHCR for a `v*` tag, never `latest`.
- **The configuration** (`config.py`, `files.py`): connections, `readable`, `large_tables`, `limits`, `instructions`,
  `context`, `semantic_models`, `vocabularies`. Files read as written (YAML 1.2 booleans, dates as text, duplicate keys,
  aliases capped, lone surrogates refused); secrets never repeated.
- **Semantic models** (`semantic.py`), **vocabularies** (`vocabulary.py`), **the dbt importer** (`importers/dbt.py`,
  `import-dbt [--strict] [--check]`; example in `examples/dbt/`).
- **Engines**: ClickHouse 24.3+ (GET; every limit with `throw`; `EXPLAIN QUERY TREE`, trusted only when resolved; a
  `readonly = 1` user and an older server refused at start, saying why) and DuckDB.
- **The guard** (`guard.py`): one `SELECT`, `readable` everywhere, large tables bounded. 12 refusal codes.
- **The guide** (`docs/`): getting started; reference — tools, configuration, semantic models and vocabularies, the dbt
  importer, the guard, engines, running it; `tests/test_docs.py` holds every tool, refusal, key and option to its entry.

## How it was verified

- 450 tests pass (410 without ClickHouse; 40 against a real ClickHouse in a container), ruff clean.
- Every protection held by a test that fails when it is removed: this milestone's mutation script — 91 removals, then 2
  for the resolved reading and 6 for HTTP — all caught; 74 in milestones 1–2.
- Two refuting readers (the semantic layer: 9 findings; the importer: 10), all closed.
- Real Claude Code: through the hand-written model (March revenue), the imported dbt model (April revenue by country),
  and the image over HTTP with its token (May refunds: EUR 57 orders, 2,726.79; USD 27, 1,540.11) — every number checked
  by a direct query.
- Measured on real servers in containers: ClickHouse 26.3 and 25.8 resolve their trees (the `IN` table named); 22.11 and
  23.8 answer unresolved — now refused at start (before, only an unknown setting stopped them). `readonly = 1` on 26.3:
  limits and overflow modes refused (code 164), format settings pass.
- On a company's real dbt project, in its own repository (`analytics-semantic-warehouse-mcp`, private): 433 datasets,
  7,933 fields imported and checked offline against the team's own tag rules in one command.

## Decided this sitting (`dev/DECISIONS.md`)

Models belong to a connection, checked once at start; semantic answers as written, made smaller in a fixed order;
every file read as written; the dbt importer writes an Ossie file (what it names, leaves out and refuses); a
`readonly = 1` ClickHouse user refused naming `readonly = 2` (variant A, B open); an engine's reading trusted only when
resolved; HTTP and the image ahead of the release (a token required off loopback, `/health`, stateless).

## Next step

The author decides whether ClickHouse before 24.3 is served (an `EXPLAIN AST` reading in the adapter) or not (a company
may read old data through a newer cluster). Then milestone 3's rest: skills (`list_skills`, `get_skill`), a git source
for context and skills, the dialect question. A release tag (`v0.1.0`) publishes the first image — the author's to set.

## What the author needs to do

The decision above; a release tag when the first image is wanted. For the ClickHouse tests locally, Docker running.

## Don't

- Don't write a real company, database, schema, table or semantics anywhere here — the repository is public.
- Don't name any team's metadata keys in code — they are declared in a vocabulary.
- Don't put an engine's specifics outside its adapter.
- Don't add a tool without `read_only_hint=True`, `destructive_hint=False`, or without its entry in `docs/reference/tools.md`.
- Don't add a protection without a test that fails when it is removed.
- Don't rephrase a model's descriptions, instructions or documents — serve them as written.
- Don't serve over HTTP without a token anywhere but loopback.
- Don't point `SWM_TEST_CLICKHOUSE_URL` at a real server: the tests rebuild `swm_*` databases there.
