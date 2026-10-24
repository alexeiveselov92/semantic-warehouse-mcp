# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 24 October 2026.

**Current milestone: 3 — the semantic layer, your own metadata, context and skills** (`dev/PLAN.md`). Milestones 0
(groundwork), 1 (one database, the guard) and 2 (ClickHouse) are done. Milestone 3's first part is done: semantic models
in Ossie, vocabularies of your own metadata, the four semantic tools. The dbt importer is next, in the same sitting.

## What exists

- **The server** over stdio (`uv run semantic-warehouse-mcp --config <file>`, `--check` to check and exit), tools:
  `about`, `list_connections`, `list_tables`, `describe_table`, `run_query`, `list_context`, `get_context`,
  `list_datasets`, `describe_dataset`, `list_metrics`, `describe_metric` — all read-only, every answer compact JSON
  with its provenance, capped at `answer_characters` and saying so when cut.
- **The configuration** (`config.py`, `files.py`): several connections, each with its engine's keys, `readable`,
  `large_tables` and their `bound_by`, `limits`, `instructions`, `context`, `semantic_models`; `vocabularies` for the
  whole server. Every problem named at once with where it is; files read as written (YAML 1.2 booleans, dates as
  text, UTF-8, duplicate keys refused, aliases capped); secrets and a URL's password never repeated in an error.
- **Semantic models** (`semantic.py`, `schemas/`): Ossie 0.2.0.dev0 and 0.1.1 (unwrapped), checked against the bundled
  schema of their version, Ossie's rules beyond it, and their connection — a source table readable and existing, a
  query source through the guard, anything else refused. `custom_extensions` data as a YAML mapping or a JSON string.
- **Vocabularies** (`vocabulary.py`): per `vendor_name` — keys, levels, types, closed values with definitions,
  `in_lists`, `unknown_keys`; checked at start (the likely key or value for a typo; values under secret-like keys never
  repeated; a vendor differing only in case refused); definitions served beside every answer that uses them.
- **Engines** (`adapters/`): **ClickHouse** — HTTP `GET` (read-only by its own rule), every overflow mode `throw`,
  `EXPLAIN QUERY TREE` naming the tables read; **DuckDB** — read-only file, external access off, `json_serialize_sql`.
- **The guard** (`guard.py`, sqlglot): one `SELECT`; nothing writes, locks or sets; table functions by allow-list;
  every table node checked against `readable`; dictionaries checked; large tables bounded from below. 12 refusal codes.
- **The guide** (`docs/`): getting started; reference — tools, configuration, semantic models and vocabularies, the
  guard, engines; held by `tests/test_docs.py` (every tool, refusal, configuration key, engine key, vocabulary key and
  Ossie version has its entry).
- **Examples**: a made-up shop (`examples/data/make_shop.py`), its configs, its Ossie model
  (`examples/semantic/shop.yaml`) and vocabulary (`examples/vocabulary/acme.yaml`), a context document.

## How it was verified

- 366 tests pass (328 without ClickHouse; 38 against a real ClickHouse in a container), ruff clean.
- Every protection held by a test that fails when it is removed: 74 removals (milestones 1–2) and 54 (milestone 3's
  first part), all caught — a mutation script run in a copy of the tree, a hang counted as caught.
- A refuting reader attacked the semantic layer: 9 findings closed, each with a test; nothing outside `readable` could
  be read through a model. Removing protections found one more: a cut of long values that could loop forever — closed.
- Real Claude Code through the model: March 2026 revenue on the made-up shop (EUR 35,057.66 over 698 orders, USD
  12,560.35 over 241), counted through the metric's field expression (net, not the gross column) and the vocabulary's
  definitions — checked by a direct query.

## Decided (`dev/DECISIONS.md`)

Earlier: DuckDB for development, ClickHouse for production; the query runs as written, read twice; answers compact
JSON, numbers exact; your own metadata — option B (YAML in `custom_extensions`, a vocabulary per vendor, nothing of
one catalog in code). This sitting: models belong to a connection, checked once at start and held to it; semantic
answers as written, lists short, made smaller in a fixed order; every file read as written; **the dbt importer writes
an Ossie file** (fields are the columns dbt describes; MetricFlow metrics through Ossie's own converter).

## Next step

The dbt importer, in this sitting: `semantic-warehouse-mcp import-dbt` reads `manifest.json`, writes an Ossie document
(`--strict`: `data` as JSON strings — the strict export); the importer's configuration says which tables, the
`vendor_name` for `meta` and tags, and which of a team's keys stand for Ossie's places; the code reads only dbt's own
structure (descriptions, `constraints`, `relationships` tests). Shown on a made-up dbt project, then on a large real
catalog in a scratch copy (counts only, nothing committed). After it: skills, a git source, the dialect question.

## What the author needs to do

Nothing blocks. For the ClickHouse tests locally, Docker must be running (the tests start their own container) — or
set `SWM_TEST_CLICKHOUSE_URL` to a throwaway server.

## Don't

- Don't write a real company, database, schema, table or semantics anywhere here — the repository is public.
- Don't name any team's metadata keys (tags, layers, provenance…) in code — they are declared in a vocabulary.
- Don't put an engine's specifics outside its adapter.
- Don't add a tool without `read_only_hint=True`, `destructive_hint=False` (a test refuses it), or without its entry in
  `docs/reference/tools.md` (a test refuses that too).
- Don't add a protection without a test that fails when it is removed.
- Don't rephrase a model's descriptions, instructions or documents — serve them as written.
- Don't point `SWM_TEST_CLICKHOUSE_URL` at a real server: the tests rebuild `swm_*` databases there.
