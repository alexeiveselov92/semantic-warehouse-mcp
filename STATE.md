# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 26 October 2026.

**Released: 0.6.0 – 0.8.1** (PyPI, GHCR, the site). **0.8.1** (2026-10-26): a `GET` of the MCP endpoint answered `405`
at once — an installation's MCP gateway waited on the empty stream the SDK held open, and hung the whole route for
everyone on it; image `0.8.1` is the index `sha256:d8b1b800…ba20`; the sibling repo-reader-mcp took the same as 0.3.1.
The installation that found it runs 0.8.0 behind a wrapper of its own for now; its move to 0.8.1 is its own. 0.6.0: lineage and
SQL from dbt, a metadata filter and its menu, `narrowed_by`, the guard hardened by a second reader; 0.6.1: ClickHouse
before 24.3 — a `GROUP BY` over an `ALIAS` column answered; 0.7.0: the dbt importer chooses tables as dbt selects them;
0.8.0: `import-database`, a draft model from a database's own catalog. **The site is live at https://swm.pipelab.dev.**
Milestones 0–4 done; **current: milestone 5** — PostgreSQL, MySQL and the ClickHouse-before-24.3 item done, BigQuery
and Snowflake put off by the author, the self-check at start remains.

## What exists

- **The server**: stdio or `--http` (a token off loopback, `/health`, stateless, POST only — a `GET` of the endpoint
  `405`); `--check`, `--check --offline`. 14 read-only tools: `about`, `list_connections`, `list_tables`,
  `describe_table`, `run_query`, `list_context`, `get_context`, `list_skills`, `get_skill`, `list_datasets`,
  `describe_dataset`, `list_metrics`, `describe_metric`, `list_metadata`.
- **Engines** (`adapters/`): ClickHouse (HTTP, `readonly = 1` users within their profile, 22.11 and 24.3–26.3; 23.x
  refused at start unless the analyzer is on), PostgreSQL, MySQL, DuckDB; drivers are extras.
- **The guard**: one SELECT over `readable` tables; a large table read alone in its SELECT, bounded by `bound_by`,
  held to named values of `narrowed_by`; on ClickHouse an alias's name is no bound; read twice — the engine's own
  reading held to the same rules. On ClickHouse where the analyzer does not run queries (`analyzer_runs`, learnt at
  start): the resolved tree and the tree as parsed (`run_passes = 0`), the latter read as a tree every line of which
  ClickHouse could print and resolved by the interpreter's rules as measured on 22.11; the parsed tree alone where the
  analyzer cannot resolve the query.
- **The semantic layer**: Ossie models, vocabularies, the dbt importer (`dbt_tags`, `dbt_depends_on`, `dbt_code`;
  tables by name and by dbt's own selection — `select`, `exclude`); the database importer (`import-database`);
  `metadata` filters, `list_metadata`, `describe_dataset` cutting long own-metadata texts before a field. Metrics:
  Ossie's own only — the metrics layer of `dev/DESIGN.md` is milestone 6, not begun.
- **Context and skills** from disk or git repositories; **the site** (`website/`); the start's log counts the
  datasets left out and names five.

## How it was verified

- 0.8.1: locally 680 tests pass, 152 skipped (Docker was not running); CI green with every engine in containers;
  ruff clean; the site builds. The `GET` → `405` test removed-and-run: all 6 of its cases fail at once (not hang, not
  error). The SDK's own client, which opens that `GET` after it connects, still serves through it. The published
  package itself, served over HTTP: `GET /mcp` `405` with `Allow: POST` in 0.5 ms, `POST` `200`, `/health` 0.8.1.
- Before (0.8.0): 825 tests, none skipped, ClickHouse 25.8 and 22.11, PostgreSQL 17, MySQL 8.4 in containers; the
  22.11 reading's 28 mutations (27 caught, the survivor a redundant condition, removed), refuted by a second reader;
  the importer's selection 16 mutations, the database importer 11, all caught; a real warehouse drafted (263 tables,
  5,776 columns, 28 s) and bound by the server.

## Decided (`dev/DECISIONS.md`)

This sitting: a `GET` of the MCP endpoint answered `405` at once, inside the token check (the sibling repo-reader-mcp
takes the same). Before: what the old data agent had; a large table read alone in its SELECT; ClickHouse before 24.3
read by the interpreter's rules; the dbt importer choosing as dbt selects; a draft model from the database itself.

## Next step

1. Milestone 5's rest (`dev/PLAN.md`): the self-check at start. Then milestone 6, metrics (`dev/DESIGN.md`,
   "Populations, filters and metric definitions") — a company's registry studied as its first check.
2. From a company's use, for the author to decide (general terms): `readable` cannot leave tables out of a pattern
   (an exclusion, `!pattern`, optional); telling agents a table stopped being refreshed; who is asking, carried to the
   database's query log behind a gateway.
3. Later, from the second readers: a `narrowed_by` / `bound_by` column the table lacks named at start; `dictGet` /
   `joinGet` and views stay the guard's alone.

## What the author needs to do

1. Decide item 2 of the next step.
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
- Don't let a request be held open with nothing to send: a gateway in front waits on every backend of its route.
- Don't tag a version that is not `pyproject.toml`'s; don't publish `latest` for the server's image.
- Don't point `SWM_TEST_*_URL` at a real server: the tests rebuild `swm_*` databases there.
- Keep the landing page's panels true: they are the server's real answers on the made-up shop.
