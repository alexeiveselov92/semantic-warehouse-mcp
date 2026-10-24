# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 24 October 2026.

**Current milestone: 3 — the semantic layer, your own metadata, context and skills** (`dev/PLAN.md`). Milestones 0–2
are done. Milestone 3 so far: semantic models in Ossie, vocabularies, the four semantic tools, the dbt importer with the
strict export. Open in it: ClickHouse users with `readonly=1` (found on a real installation — a decision first), skills,
a git source, the dialect question.

## What exists

- **The server** over stdio (`uv run semantic-warehouse-mcp --config <file>`, `--check` to check and exit), tools:
  `about`, `list_connections`, `list_tables`, `describe_table`, `run_query`, `list_context`, `get_context`,
  `list_datasets`, `describe_dataset`, `list_metrics`, `describe_metric` — all read-only, every answer compact JSON
  with its provenance, capped at `answer_characters` and saying so when cut.
- **The configuration** (`config.py`, `files.py`): connections with their engine's keys, `readable`, `large_tables`,
  `limits`, `instructions`, `context`, `semantic_models`; `vocabularies` for the whole server. Every problem named at
  once; files read as written (YAML 1.2 booleans, dates as text, UTF-8, duplicate keys refused, aliases capped, lone
  surrogates refused, escaped pairs read as characters); secrets never repeated (a mapping holding a secret-like key
  included; a URL's password redacted up to the last `@`).
- **Semantic models** (`semantic.py`, `schemas/`): Ossie 0.2.0.dev0 and 0.1.1, checked against the bundled schema, Ossie's
  rules and their connection; `custom_extensions` data as a YAML mapping or a JSON string (numbers as written).
- **Vocabularies** (`vocabulary.py`): per `vendor_name` — keys, levels, types, closed values with definitions, checked at
  start, definitions served beside every answer that uses them.
- **The dbt importer** (`importers/dbt.py`): `semantic-warehouse-mcp import-dbt <configuration> [--strict] [--check]`
  writes a manifest's tables as an Ossie document, read back and checked before it is written; the team's mapping in its
  configuration (`tables`, `resources`, `vendor_name`, `dbt_tags`, `places`), dbt's own keys, `event_time` and
  `relationships` tests read by code. Example: `examples/dbt/` (a made-up dbt project, its real manifest made by
  `examples/dbt/make_manifest.py`, the output), served by `examples/config.dbt.example.yaml`.
- **Engines** (`adapters/`): ClickHouse (GET, every overflow mode `throw`, `EXPLAIN QUERY TREE`) and DuckDB.
- **The guard** (`guard.py`, sqlglot): one `SELECT`, `readable` everywhere, large tables bounded. 12 refusal codes.
- **The guide** (`docs/`): getting started; reference — tools, configuration, semantic models and vocabularies, the dbt
  importer, the guard, engines; held by `tests/test_docs.py` (every tool, refusal, key, option, version has its entry).

## How it was verified

- 426 tests pass (388 without ClickHouse; 38 against a real ClickHouse in a container), ruff clean.
- Every protection held by a test that fails when it is removed: 91 removals in this milestone's mutation script, all
  caught (a hang counted as caught); 74 in milestones 1–2.
- Two refuting readers: the semantic layer (9 findings closed), the importer (10 closed — quoted columns, another
  database, collapsing relationships, lone surrogates, tracebacks, secrets in problems, NaN, NEL, `--strict`, names).
- Real Claude Code: March revenue through the hand-written model; April revenue by customer country through the
  imported dbt model (the join from a `relationships` test, revenue from a dbt description) — every number checked by
  a direct query.
- On a real company's dbt project (a scratch copy, nothing committed): 715 datasets, 8,092 fields imported in 10 s, all
  checked by the server against a vocabulary built from the team's own tag rules; a planted misspelled tag named with
  the likely one. Its warehouse's read-only user (`readonly=1`) cannot take the adapter's limits — see the next step.

## Decided (`dev/DECISIONS.md`)

Earlier: DuckDB for development, ClickHouse for production; the query runs as written, read twice; answers compact
JSON; your own metadata — option B. Milestone 3: models belong to a connection, checked once at start and held to it;
semantic answers as written, made smaller in a fixed order; every file read as written; the dbt importer writes an Ossie
file (fields are the documented columns, MetricFlow through Ossie's converter); what the importer names, leaves out and
refuses.

## Next step

**The author decides** how the ClickHouse adapter meets a `readonly=1` user (the session's report gives the options:
refuse it at start naming the fix — `readonly=2` — or run within the profile's own limits, refusing a profile that cuts
silently or sets no time limit). Then build it, with a test user of each kind in the ClickHouse tests, and run the
server over the real warehouse with the imported model — a real agent's answer, its number checked. After it: skills
(`list_skills`, `get_skill`), a git source for context and skills, the dialect question.

## What the author needs to do

The decision above. For a run on the real warehouse either way: a read-only user whose profile sets a time limit (or
`readonly=2`) — a request to its DBA. For the ClickHouse tests locally, Docker running.

## Don't

- Don't write a real company, database, schema, table or semantics anywhere here — the repository is public.
- Don't name any team's metadata keys (tags, layers, provenance…) in code — they are declared in a vocabulary.
- Don't put an engine's specifics outside its adapter.
- Don't add a tool without `read_only_hint=True`, `destructive_hint=False`, or without its entry in `docs/reference/tools.md`.
- Don't add a protection without a test that fails when it is removed.
- Don't rephrase a model's descriptions, instructions or documents — serve them as written.
- Don't point `SWM_TEST_CLICKHOUSE_URL` at a real server: the tests rebuild `swm_*` databases there.
