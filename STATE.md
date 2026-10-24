# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 24 October 2026.

**Released: 0.1.0, pre-alpha** — on PyPI (`uvx semantic-warehouse-mcp`) and GHCR
(`ghcr.io/alexeiveselov92/semantic-warehouse-mcp:0.1.0`). **Current milestone: 3 — the semantic layer, your own
metadata, context and skills** (`dev/PLAN.md`): done but for skills, a git source and the dialect question. Milestones
0–2 are done; parts of 8–9 (HTTP, the image, the release) came early, for a company's deployment.

## What exists

- **The server**: `semantic-warehouse-mcp --config <file>` over stdio, or `--http` (a token by `--token-env`, required
  off loopback; `/health` without a token or a database; stateless JSON answers); `--check`, `--check --offline` (CI:
  no database, no secret). Tools: `about`, `list_connections`, `list_tables`, `describe_table`, `run_query`,
  `list_context`, `get_context`, `list_datasets`, `describe_dataset`, `list_metrics`, `describe_metric` — read-only,
  compact JSON with provenance, capped and saying so.
- **The configuration** (`config.py`, `files.py`): connections (`readable`, `large_tables`, `limits`, `instructions`,
  `context`, `semantic_models`, `missing_tables`), `vocabularies`. Files read as written (YAML 1.2 booleans, dates as
  text, duplicate keys, aliases capped, lone surrogates refused); secrets never repeated.
- **Semantic models** in Apache Ossie (`semantic.py`), **vocabularies** of your own metadata (`vocabulary.py`), **the dbt
  importer** (`importers/dbt.py`: `import-dbt [--strict] [--check]`; the example in `examples/dbt/`).
- **ClickHouse** (tried on 22.11, 25.8, 26.3): GET; every limit with `throw` (only those the server knows);
  `EXPLAIN QUERY TREE run_passes = 1`, trusted only when resolved; a `readonly = 1` user served within its profile
  (no limit sent; answers read no further than one row past the shown; queries stopped by their id; a profile that
  cuts silently, or a user that could not stop a query without a time limit, refused). **DuckDB** for development.
- **The guard** (`guard.py`): one `SELECT`, `readable` everywhere, large tables bounded. 12 refusal codes.
- **Releases**: `publish.yml` (PyPI, trusted publishing, the `pypi` environment) and `image.yml` (GHCR) for a tag
  `v<version>` that is `pyproject.toml`'s; the image built on every change. `Dockerfile`: non-root, read-only root fs.
- **The guide** (`docs/`): getting started; reference — tools, configuration, semantic models and vocabularies, the dbt
  importer, the guard, engines, running it. `tests/test_docs.py` holds every tool, refusal, key and option to its entry.

## How it was verified

- 468 tests pass (426 without ClickHouse, 42 against a real ClickHouse in a container); ruff clean; CI green.
- Every protection held by a test that fails when it is removed: this milestone's mutation script, 110 removals, all
  caught (74 more in milestones 1–2).
- Two refuting readers: the semantic layer (9 findings), the dbt importer (10) — all closed.
- Measured on real ClickHouse servers in containers (22.11, 23.8, 25.8, 26.3): resolved trees, what a `readonly = 1`
  user may send, how each version writes an error after rows were sent.
- Real Claude Code: through the hand-written model, the imported dbt model, the image over HTTP with its token — and,
  live, on a company's two real warehouses (ClickHouse 26.3 and 22.11) as the read-only user its employees use, from
  the company's own private repository: every number checked by a direct query; two real data problems found.
- The release: installed from PyPI and run with `uvx`; the image pulled without signing in.

## Decided this sitting (`dev/DECISIONS.md`)

Models belong to a connection, checked once at start; semantic answers as written, made smaller in a fixed order; every
file read as written; the dbt importer writes an Ossie file (what it names, leaves out and refuses); an engine's reading
trusted only when resolved; HTTP and the image early (a token off loopback, `/health`, stateless); **a `readonly = 1`
ClickHouse user served within its profile** (the author); ClickHouse before 24.3 through the resolved tree;
`missing_tables: leave_out`; **0.1.0 released by trusted publishing** (the author).

## Next step

Milestone 3's rest: skills folders (`list_skills`, `get_skill`, `SKILL.md` served as written), context and skills read
from a git repository, the dialect question (`DECISIONS.md`). Then milestone 4, the docs site. A company's deployment
builds on the image (`:0.1.0`) from its own repository.

## What the author needs to do

Nothing blocks. A release later: bump `pyproject.toml`'s version, commit, tag `v<version>`, push the tag. For the
ClickHouse tests locally, Docker running.

## Don't

- Don't write a real company, database, schema, table or semantics anywhere here — the repository is public.
- Don't name any team's metadata keys in code — they are declared in a vocabulary.
- Don't put an engine's specifics outside its adapter.
- Don't add a tool without `read_only_hint=True`, `destructive_hint=False`, or without its entry in `docs/reference/tools.md`.
- Don't add a protection without a test that fails when it is removed.
- Don't rephrase a model's descriptions, instructions or documents — serve them as written.
- Don't serve over HTTP without a token anywhere but loopback.
- Don't tag a version that is not `pyproject.toml`'s; don't publish `latest`.
- Don't point `SWM_TEST_CLICKHOUSE_URL` at a real server: the tests rebuild `swm_*` databases there.
