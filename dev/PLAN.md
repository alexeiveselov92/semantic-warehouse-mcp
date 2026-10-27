# Plan

Measured in sessions — one sitting of the author's, from half an hour to a few hours. Each milestone ends
with something visible: a real agent answering through the server, its numbers checked by a direct query.

## Milestone 0. Groundwork — ✅ 2026-10-24

- [x] The repository, its license (Apache-2.0), README, design, decisions, research.
- [x] The session flow: `CLAUDE.md`, `/start`, `/close`, `STATE.md`, rules.
- [x] A running skeleton: the server over stdio with one tool, its test through the SDK's in-process client,
      lint, CI.

## Milestone 1. One database, read-only, with the guard — ✅ 2026-10-24 (1 session of 2)

- [x] The configuration file (YAML) and its check: a wrong entry stops the server, naming it.
- [x] The adapter interface; DuckDB first (in-process — the tests need no server).
- [x] The guard on sqlglot: one statement, `SELECT` / `WITH` only, `readable` tables, `large_tables` bounded,
      rows and time capped; each refusal a test case; each protection with a test that fails when removed.
- [x] `list_connections`, `run_query`, a table's columns (`list_tables`, `describe_table`); provenance in every answer.
- [x] Real Claude Code over stdio answers a question on a made-up dataset; the number checked directly.
- [x] Beyond the plan: a refuting reader of the guard (two bypasses closed, a function family denied), the guide's
      pages for configuration, the guard, engines and getting started — each held by a test.

## Milestone 2. ClickHouse — ✅ 2026-10-24 (the same day as milestone 1)

The engine production work runs on (the author, 2026-10-24) — moved up from "the databases people use".

- [x] The ClickHouse adapter (`dev/DESIGN.md`, "Adapters"): HTTP `GET` (read-only by ClickHouse's own rule), user and
      password by variable name; every limit sent with an overflow mode that refuses; a profile that would cut
      silently overridden.
- [x] ClickHouse's own reading of a query (`EXPLAIN QUERY TREE`): one `SELECT`, and the tables it reads as ClickHouse
      sees them — checked against `readable` and the large-table bound; dictionaries read by `dictGet…` checked against
      `readable`. DuckDB's own reading names its tables too (`json_serialize_sql`).
- [x] Tested against a real ClickHouse in a container (a service container in CI): the read-only line holds past the
      guard for a user who may write, limits refuse, the large-table bound; real Claude Code answered over the made-up
      shop in ClickHouse and DuckDB at once, the numbers checked directly.
- [x] Beyond the plan, at the author's request: the team's own `instructions` and `context` documents, for the whole
      server and per connection (`list_context`, `get_context`).

## Milestone 3. The semantic layer, your own metadata, context and skills — 2–3 sessions

- [x] Apache Ossie documents loaded and checked against the Ossie schema (the 0.2 shape, 0.1.1 read too), each bound
      to its connection; `list_datasets`, `describe_dataset`, `list_metrics`, `describe_metric` — descriptions,
      `ai_context` and your own metadata served as written, the live table's columns beside. ✅ 2026-10-24
- [x] Your own metadata (`dev/DECISIONS.md`, "Your own metadata"): `custom_extensions` written as YAML or as Ossie's JSON
      string; a vocabulary per vendor declaring keys, levels, types and closed value lists with definitions — checked
      at start, served with the definitions; a strict Ossie export. Nothing of any one catalog in code. ✅ 2026-10-24 —
      a large dbt catalog's shapes fit with no team key in code (a test holds it); the strict export is the importer's
      `--strict`.
- [x] The dbt importer: a dbt project's descriptions, `meta` and tags into Ossie, `meta` passed through as written;
      which of its keys become Ossie's own places (a time column, a primary key) set in the importer's configuration,
      not in code. ✅ 2026-10-24 — run on a large real catalog (in a scratch copy): every table and column, checked
      against the team's own tag rules.
- [x] ClickHouse users with `readonly=1` (found on a real installation, 2026-10-24): such a user may not change a
      limit. ✅ 2026-10-24 — served within its profile (the author's choice): no limit sent, the answer read no further
      than one row past the shown, the query stopped by its id; a profile that cuts silently, or a user that could not
      stop a query and has no time limit, refused (`dev/DECISIONS.md`); a test user of each kind.
- [x] ClickHouse before 24.3 (a real installation runs 22.11, 2026-10-24): it answers `EXPLAIN QUERY TREE` unresolved —
      a reading naming no table. ✅ 2026-10-24 — the resolved tree asked for (`run_passes = 1`: 26 of 27 analyst
      queries resolve on 22.11), an unresolved one refused, settings a server does not know not sent; checked live.
- [x] Beyond the plan, from the live runs: `missing_tables: leave_out` for a model ahead of its database. ✅ 2026-10-24
- [x] Skills folders (`list_skills`, `get_skill`); context and skills read straight from a git repository.
      ✅ 2026-10-24 — the Agent Skills format checked at start, a skill's files served by listed names; repositories
      named, fetched by git (only the named paths), refreshed in the background, a commit that breaks the checks never
      served; live from GitHub, in the image too; real Claude Code followed a skill and its file.
- [x] The dialect question settled and written in `DECISIONS.md`. ✅ 2026-10-24 — the author: an expression runs as
      written in its connection's dialect, never translated (sqlglot's translation measured not neutral); variants per
      engine in our own `custom_extensions`; built with the metrics (milestone 6).
- [x] Beyond the plan, from a comparison with a company's earlier data agent (the author, 2026-10-25): what a model
      reads and its SQL from dbt (`dbt_depends_on`, `dbt_code`); lists filtered by the team's own metadata, with the
      fields that matched, and `list_metadata`, the menu of its values; `narrowed_by` for a log of every kind of event.
      ✅ 2026-10-25 — measured on two real projects; real Claude Code found lineage, money columns and a model's SQL
      through them.
- [x] Beyond the plan, from a company's use (2026-10-25): the importer chooses tables as dbt selects them (`select`,
      `exclude` — `tag:`, `path:`, `config.<key>:`, …), so a model holds what the project builds. ✅ 2026-10-25 — 0.7.0.
- [x] Beyond the plan, at the author's word (2026-10-25): a draft of a model from the database's own catalog, for a
      database no dbt project describes (`import-database`). ✅ 2026-10-25 — 0.8.0; DuckDB, PostgreSQL, ClickHouse
      tested; a real warehouse drafted and bound.

## Milestone 4. The docs site — 1 session (built the same sitting as milestone 3's end)

- [x] `website/` — Astro + Starlight built from `docs/`, the author's detectkit pattern (`../detectkit/website`: the
      `sync-docs` script importing `docs/`, the nginx Dockerfile, the `website.yml` workflow pushing the image to GHCR and
      sending `repository_dispatch` to pipelab). ✅ 2026-10-24 — on repo-reader-mcp's site (itself detectkit's pattern):
      a landing page whose every panel is an answer the server gave on the made-up shop, the guide's 10 pages, an indigo
      accent and a mark of its own; built, its image run, 428 links checked, screenshots looked at in both themes.
- [x] In pipelab (`../pipelab`, under its own rules): the site at `swm.pipelab.dev` — a compose service
      behind Traefik and its `deploy-*.yml`, as detectkit's site is served. ✅ 2026-10-24 — committed in pipelab
      (`swm-web`, `deploy-swm.yml`), not pushed: it goes after this repository's first image.
- [x] The guide as it stands published there; from now on every milestone's pages go live with it. ✅ 2026-10-24 —
      live at https://swm.pipelab.dev: a push builds the image and pipelab's `Deploy swm` redeploys it on the dispatch.

## Milestone 5. The other databases people use — 2 sessions

- [x] PostgreSQL, MySQL; then BigQuery, Snowflake — each an adapter with its read-only session, its guard deny-list
      and probes; tested against real servers in containers where they exist. PostgreSQL ✅ 2026-10-25 (a read-only
      transaction, a cursor, the plan read through the named views; tested on 17 in a container and in CI; real
      Claude Code answered April revenue by country, checked by a direct query); drivers made extras (the author).
      MySQL ✅ 2026-10-25 (a read-only session, `/*!` and `/*+` refused, rows and time stopped by the server; tested on
      8.4 in a container and in CI; real Claude Code answered May revenue and a signup cohort, checked).
      BigQuery and Snowflake put off (the author, 2026-10-25: "enough of the databases there are for now") — in Later.
- [ ] The self-check at start: probes with known verdicts run through the guard and the engine's own parser; a
      dialect either misreads is refused, not trusted.
- [x] ClickHouse before 24.3: a `GROUP BY` over an `ALIAS` column fails the resolved tree (Code 215) while the query
      runs — refused as `engine_parse`. Another reading of the tables a query reads on those servers, as strict.
      ✅ 2026-10-25 — the tree as parsed, read by the interpreter's rules (`dev/DECISIONS.md`); 22.11 in CI; refuted
      by a second reader; the real installation's query answered, checked; 0.6.1.
- [x] Measured against a company's own data agent on the same questions (`dev/DECISIONS.md`, 2026-10-27): the team's
      rules through any gateway (`instructions_in_run_query`), `describe_dataset` lighter (`full`), the metric tools
      only where a model defines metrics. ✅ 2026-10-27 — 24 of 24 answered by the team's rules (19 before), cost per
      question 1.4× the agent's (1.7× before); 0.9.0. The next lever, measured first: choosing fields in
      `describe_dataset`.

## Milestone 6. Metrics — 2 sessions

- [ ] The metrics layer (`dev/DESIGN.md`, "Populations, filters and metric definitions"): populations, filters bound per
      population with their placement (refused when unbound), metric kinds and roll-up rules, templates with the layer's
      macros, presets, scenarios run in tests. Its schema published; the registry of `dev/RESEARCH.md` expressed in it
      without loss as the design's first check.
- [ ] Expressions in their connection's dialect (`dev/DECISIONS.md`, "A model's expression runs as written"): every
      expression parsed at start in its connection's dialect, one that does not parse stopping the server; a variant
      per engine in this server's own `custom_extensions` entry, chosen before `ANSI_SQL`.
- [ ] `query_metric`: SQL compiled from the model (metric, dimensions, filters, a time range, joins along
      relationships), run through the guard; its SQL shown.
- [ ] Importers of metrics from dbt's semantic layer — Ossie's converters where they do the job.

## Milestone 7. Saved query sets — 1 session

- [ ] YAML sets of named, parameterised queries; `list_query_sets`, `run_saved_query`; parameters bound by the
      driver, never pasted.

## Milestone 8. The author's server instance — 1 session

- [x] Ahead of it, for a company's deployment (2026-10-24): Streamable HTTP (`--http`, a token by variable name,
      `/health`, stateless), the image (`Dockerfile`, non-root, read-only root filesystem) and its workflow (built on
      every change, published to GHCR for a `v*` tag) — `docs/reference/running.md`.
- [x] Behind a company's MCP gateway: a `GET` of the endpoint answered `405` at once (the gateway waited on the empty
      stream the SDK held open). ✅ 2026-10-26 — 0.8.1; the sibling repo-reader-mcp 0.3.1 the same.

- [ ] In pipelab: an instance of the server (its image from GHCR, `/mcp` routed with no redirect, a token or the
      organisation's gateway in front), its `deploy-*.yml`, secrets prefixed in GitHub — for the author's own use and a
      public demo over made-up data.

## Milestone 9. Release — 1 session

- [x] Ahead of it, at the author's word: 0.1.0, pre-alpha — the package on PyPI by trusted publishing (`publish.yml`),
      the image on GHCR (`image.yml`), for a `v*` tag that is the package's version. ✅ 2026-10-24 — published, installed
      from PyPI with `uvx`, the image pulled without signing in.
- [ ] 1.0: docs for agents and for people who run it; checked with a second MCP client (not Claude).

## Later

BigQuery and Snowflake (put off by the author on 2026-10-25: the emulator and fakesnow, a real account if one
exists); per-person access when served to a team (or left to a gateway); saved query sets made by agents; a semantic
model written back as Ossie from what agents learn — only with a person's review.
