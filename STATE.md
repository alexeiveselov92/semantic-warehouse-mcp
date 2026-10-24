# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 24 October 2026.

**Current milestone: 3 — the semantic layer, your own metadata, context and skills** (`dev/PLAN.md`). Milestones 0–2
are done. Milestone 3 so far: semantic models in Ossie, vocabularies, the four semantic tools, the dbt importer with the
strict export; ClickHouse `readonly = 1` users served within their profile, and ClickHouse 22.11. Ahead of milestones
8–9, for a company's deployment: Streamable HTTP with a token, `/health`, the container image. Open in milestone 3:
skills, a git source, the dialect question.

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
- **Engines**: ClickHouse, tried on 22.11, 25.8, 26.3 (GET; every limit with `throw`, only those the server knows;
  `EXPLAIN QUERY TREE run_passes = 1`, trusted only when resolved; a `readonly = 1` user served within its profile —
  answers read no further than one row past the shown, queries stopped by their id; a profile that cuts silently, or a
  user that could not stop a query and has no time limit, refused) and DuckDB. `missing_tables: leave_out` for a model
  ahead of its database.
- **The guard** (`guard.py`): one `SELECT`, `readable` everywhere, large tables bounded. 12 refusal codes.
- **The guide** (`docs/`): getting started; reference — tools, configuration, semantic models and vocabularies, the dbt
  importer, the guard, engines, running it; `tests/test_docs.py` holds every tool, refusal, key and option to its entry.

## How it was verified

- 468 tests pass (426 without ClickHouse; 42 against a real ClickHouse in a container), ruff clean.
- Every protection held by a test that fails when it is removed: this milestone's mutation script — 91 removals, then 2
  for the resolved reading, 6 for HTTP, 11 for `readonly = 1`, 22.11 and `leave_out` — all caught; 74 in milestones 1–2.
- Two refuting readers (the semantic layer: 9 findings; the importer: 10), all closed.
- Real Claude Code: through the hand-written model (March revenue), the imported dbt model (April revenue by country),
  and the image over HTTP with its token (May refunds: EUR 57 orders, 2,726.79; USD 27, 1,540.11) — every number checked
  by a direct query.
- Measured on real servers in containers: 26.3 and 25.8 resolve their trees; 22.11 resolves with `run_passes = 1` (26
  of 27 analyst queries, the `IN` table named). `readonly = 1` on 22.11 and 26.3: limits, overflow modes and
  `max_block_size` refused, format settings and `query_id` pass, `KILL QUERY` of one's own needs `system.processes`; an
  error met mid-answer looks different in 22.x, 25.x and 26.x — all three read as errors.
- **Live, on a company's two real warehouses** (ClickHouse 26.3 and 22.11), as the read-only user its employees use
  (`readonly = 1`), from its own private repository: both dbt projects imported and bound (425 and 587 models found;
  the rest named, not built); real Claude Code answered one question on each, every number checked by a direct query,
  and found two real data problems on the way.

## Decided this sitting (`dev/DECISIONS.md`)

Models belong to a connection, checked once at start; semantic answers as written, made smaller in a fixed order;
every file read as written; the dbt importer writes an Ossie file (what it names, leaves out and refuses); an engine's
reading trusted only when resolved; HTTP and the image ahead of the release (a token required off loopback, `/health`,
stateless); **a `readonly = 1` ClickHouse user served within its profile** (the author: variant B); ClickHouse before
24.3 served through the resolved tree; `missing_tables: leave_out`.

## Next step

Milestone 3's rest: skills (`list_skills`, `get_skill`), a git source for context and skills, the dialect question. A
release tag (`v0.1.0`) publishes the first image — the author's to set — which a company's deployment builds on.

## What the author needs to do

A release tag when the first image is wanted. For the ClickHouse tests locally, Docker running.

## Don't

- Don't write a real company, database, schema, table or semantics anywhere here — the repository is public.
- Don't name any team's metadata keys in code — they are declared in a vocabulary.
- Don't put an engine's specifics outside its adapter.
- Don't add a tool without `read_only_hint=True`, `destructive_hint=False`, or without its entry in `docs/reference/tools.md`.
- Don't add a protection without a test that fails when it is removed.
- Don't rephrase a model's descriptions, instructions or documents — serve them as written.
- Don't serve over HTTP without a token anywhere but loopback.
- Don't point `SWM_TEST_CLICKHOUSE_URL` at a real server: the tests rebuild `swm_*` databases there.
