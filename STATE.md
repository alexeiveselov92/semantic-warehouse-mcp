# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 24 October 2026.

**Current milestone: 3 — the semantic layer, your own metadata, context and skills** (`dev/PLAN.md`). Milestones 0
(groundwork), 1 (one database, the guard) and 2 (ClickHouse) are done — 1 and 2 in one session, with the team's own
instructions and context documents built ahead of milestone 3 at the author's request.

## What exists

- **The server** over stdio (`uv run semantic-warehouse-mcp --config <file>`, `--check` to check and exit), tools:
  `about`, `list_connections`, `list_tables`, `describe_table`, `run_query`, `list_context`, `get_context` — all
  read-only, every answer compact JSON with its provenance (connection, the SQL as it ran, time, rows, cut or not).
- **The configuration** (`config.py`): several connections, each with its engine's keys, `readable` patterns,
  `large_tables` and their `bound_by`, `limits`; `instructions` and `context` for the whole server and per connection.
  Every problem named at once, typos with the likely key, duplicate YAML keys refused, secrets (and URLs) never
  repeated in an error, every connection reached at start.
- **Engines** (`adapters/`): **ClickHouse** — HTTP `GET` (read-only by ClickHouse's own rule, even for a user who may
  write), every overflow mode `throw` (a profile's silent `break` overridden), one extra row to show a cut,
  `EXPLAIN QUERY TREE` naming the tables read; **DuckDB** — read-only file, external access off, configuration locked,
  `json_serialize_sql` naming the tables read.
- **The guard** (`guard.py`, sqlglot): one `SELECT`; nothing writes, locks or sets inside; table functions by
  allow-list; denied function families by glob; every table node checked against `readable` unless proven a CTE's
  name; dictionaries named in `dictGet…` checked; large tables bounded from below in the query that reads them; the
  engine's own list of tables held to the same rules. 12 refusal codes, each in `docs/reference/guard.md`.
- **The guide** (`docs/`): getting started, reference — tools, configuration, the guard, engines; held by
  `tests/test_docs.py` (every tool, refusal, configuration key and engine key has its entry; the contents list every page).
- **Examples**: a made-up shop (`examples/data/make_shop.py` → DuckDB file or a throwaway ClickHouse), its configs
  (`config.example.yaml`, `config.clickhouse.example.yaml`), a context document; drafts of an Ossie model, a skill, a
  saved query set.

## How it was verified

- 257 tests pass (ruff clean); ClickHouse tests against a real server — a container the tests start, or CI's service.
- Every protection held by a test that fails when it is removed: 74 removals, 74 caught (a mutation script, run in a
  copy of the tree).
- A refuting reader attacked the guard: two bypasses found and closed (`FROM (secret.t) x`; ClickHouse `x IN db.table`),
  then `dictGet*` / `joinGetOrNull` — now checked against `readable`.
- Real Claude Code over stdio, four runs: March revenue on DuckDB; refusals of `DELETE` and an unbounded scan; April
  compared across DuckDB and ClickHouse at once (dialect-correct SQL for each); a question answered from a context
  document with each rule's source cited — every number checked by a direct query.

## Decided this session (`dev/DECISIONS.md`)

DuckDB for development, ClickHouse for production; the query runs as written, read twice (sqlglot, then the
engine's own parser); every table node a table read unless proven a CTE's name; the large-table bound rule (presence,
not width); answers as compact JSON, numbers exact; ClickHouse's GET + `throw` design; **your own metadata — option B**:
Ossie's `custom_extensions` written as YAML (or JSON strings), a vocabulary per vendor with definitions, checked at
start, **nothing of any one catalog in code**; the team's own instructions and context; the plan reordered
(ClickHouse → the semantic layer with the dbt importer → the site → other engines → metrics).

## Next step

Milestone 3, the semantic layer: Ossie documents loaded and checked against the Ossie schema, each bound to its
connection; your own metadata in `custom_extensions` as YAML with a vocabulary (keys, levels, closed value lists with
definitions); `list_datasets`, `describe_dataset`, `list_metrics`, `describe_metric` serving descriptions,
`ai_context` and own metadata as written; the dbt importer (descriptions, `meta`, tags → Ossie, its mapping in the
importer's configuration, not code). Requirements: `dev/DESIGN.md` ("Your own metadata", "Importers") and
`dev/RESEARCH.md` ("What a production dbt catalog holds"). First check of the design: a large dbt catalog's tags,
layers and provenance must fit without a line of code naming them.

## What the author needs to do

Nothing blocks milestone 3. Open Claude Code in this repository and run `/start`. For the ClickHouse tests locally,
Docker must be running (the tests start their own container) — or set `SWM_TEST_CLICKHOUSE_URL` to a throwaway server.

## Don't

- Don't write a real company, database, schema, table or semantics anywhere here — the repository is public.
- Don't name any team's metadata keys (tags, layers, provenance…) in code — they are declared in a vocabulary.
- Don't put an engine's specifics outside its adapter.
- Don't add a tool without `read_only_hint=True`, `destructive_hint=False` (a test refuses it), or without its entry in
  `docs/reference/tools.md` (a test refuses that too).
- Don't add a protection without a test that fails when it is removed.
- Don't rephrase a model's descriptions, instructions or documents — serve them as written.
- Don't point `SWM_TEST_CLICKHOUSE_URL` at a real server: the tests rebuild `swm_*` databases there.
