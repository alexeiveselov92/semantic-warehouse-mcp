# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 27 October 2026.

**Released: 0.6.0 – 0.8.1** (PyPI, GHCR, the site). **0.8.1** (2026-10-26): a `GET` of the MCP endpoint answered `405`
at once — an installation's MCP gateway waited on the empty stream the SDK held open, and hung the whole route for
everyone on it; image `0.8.1` is the index `sha256:d8b1b800…ba20`; the sibling repo-reader-mcp took the same as 0.3.1.
**Tagged now: 0.9.0** — from measuring the server against a company's own data agent on the same questions: the team's
rules through any gateway (`instructions_in_run_query`), a lighter `describe_dataset` (`full`), the metric tools only
where a model defines metrics. **The site is live at https://swm.pipelab.dev.** Milestones 0–4 done; **current:
milestone 5** — PostgreSQL, MySQL, ClickHouse before 24.3 and the measurement done, BigQuery and Snowflake put off by
the author, the self-check at start remains.

## What exists

- **The server**: stdio or `--http` (a token off loopback, `/health`, stateless, POST only — a `GET` of the endpoint
  `405`); `--check`, `--check --offline`. 14 read-only tools: `about`, `list_connections`, `list_tables`,
  `describe_table`, `run_query`, `list_context`, `get_context`, `list_skills`, `get_skill`, `list_datasets`,
  `describe_dataset`, `list_metadata`, and — only where a model defines a metric — `list_metrics`, `describe_metric`.
- **Engines** (`adapters/`): ClickHouse (HTTP, `readonly = 1` users within their profile, 22.11 and 24.3–26.3; 23.x
  refused at start unless the analyzer is on), PostgreSQL, MySQL, DuckDB; drivers are extras.
- **The guard**: one SELECT over `readable` tables; a large table read alone in its SELECT, bounded by `bound_by`,
  held to named values of `narrowed_by`; on ClickHouse an alias's name is no bound; read twice — the engine's own
  reading held to the same rules (before 24.3 the tree as parsed too, by the interpreter's rules).
- **The semantic layer**: Ossie models, vocabularies, the dbt importer (`dbt_tags`, `dbt_depends_on`, `dbt_code`;
  `select`, `exclude`), the database importer, `metadata` filters, `list_metadata`. `describe_dataset` shortens own
  metadata values over 1000 characters (a text to its beginning, a list or mapping to a note of its length) unless
  `full: true`, and serves a field whose expression is its own name without `expression`. Metrics: Ossie's own only —
  the metrics layer of `dev/DESIGN.md` is milestone 6, not begun.
- **Instructions, context, skills** from disk or git repositories; with `instructions_in_run_query` the server's
  instructions also follow `run_query`'s description (for gateways that drop the handshake's).

## How it was verified

- 0.9.0 (before the rebase onto 0.8.1): 830 tests pass, none skipped (ClickHouse 25.8 and 22.11, PostgreSQL 17, MySQL
  8.4 in containers); ruff clean; the site builds; 12 mutations, all caught (a text measured by its JSON length — a
  re-read found it, fixed). After the rebase: the suite run again before the tag.
- 0.8.1: the `GET` → `405` test removed-and-run: all 6 of its cases fail at once; the published package served over
  HTTP: `GET /mcp` `405` with `Allow: POST` in 0.5 ms, `POST` `200`.
- **Against a company's data agent** (`dev/DECISIONS.md`, 2026-10-27; the company's own record holds the numbers and
  answers): the same 8 questions × 3 runs, headless Claude Code, the same model; every answer checked by an
  independent direct query. Per question (medians): the agent 25 s, 10 turns, 1×; 0.8.0 behind the gateway 24 s, 14.5
  turns, 1.7×, 19/24 by the team's rules; 0.9.0 (working tree, stdio, instructions stripped as the gateway strips
  them) 23 s, 11.5 turns, 1.4×, 24/24. With a real client: no instructions in the prompt, the local-time rule quoted
  from `run_query`'s description, `list_metrics` absent.
- Claude Code passes a tool description of 15,000 characters whole (a probe server) — what option 1 rests on.

## Decided (`dev/DECISIONS.md`)

The last two sittings: a `GET` of the MCP endpoint answered `405` at once, inside the token check;
`instructions_in_run_query`, off by default (the author may turn it); own metadata shortened in `describe_dataset`
unless `full`; a field's expression left out when it is its own name; the metric tools only where there are metrics.
Not changed: `list_datasets`, the Ossie wrapper of a field's own metadata.

## Next step

1. **0.9.0's publish checked** (PyPI, GHCR, the site), then the company's use: its pin to 0.9.0 and
   `instructions_in_run_query: true` in its configuration (its own repository says the deploy).
2. Measured next: choosing fields in `describe_dataset` — what is left of the gap to the agent is a table answered
   whole where the agent selects columns by SQL.
3. Milestone 5's rest (`dev/PLAN.md`): the self-check at start. Then milestone 6, metrics (`dev/DESIGN.md`,
   "Populations, filters and metric definitions") — a company's registry studied as its first check.
4. From a company's use, for the author to decide (general terms): `readable` cannot leave tables out of a pattern;
   telling agents a table stopped being refreshed (its last change); who is asking, carried to the database's query
   log behind a gateway.
5. Later, from the second readers: a `narrowed_by` / `bound_by` column the table lacks named at start; `dictGet` /
   `joinGet` and views stay the guard's alone.

## What the author needs to do

1. Decide item 4 of the next step; whether `instructions_in_run_query` should be on by default.
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
- Don't rephrase a model's descriptions, instructions, documents or skills — serve them as written; a shortened value
  says it is, and the whole is one argument away.
- Don't serve over HTTP without a token anywhere but loopback; don't let a token reach git's command line.
- Don't let a request be held open with nothing to send: a gateway in front waits on every backend of its route.
- Don't tag a version that is not `pyproject.toml`'s; don't publish `latest` for the server's image.
- Don't point `SWM_TEST_*_URL` at a real server: the tests rebuild `swm_*` databases there.
- Keep the landing page's panels true: they are the server's real answers on the made-up shop.
