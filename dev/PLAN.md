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

- [ ] Apache Ossie documents loaded and checked against the Ossie schema (the 0.2 shape, 0.1.1 read too), each bound
      to its connection; `list_datasets`, `describe_dataset`, `list_metrics`, `describe_metric` — descriptions,
      `ai_context` and your own metadata served as written, the live table's columns beside.
- [ ] Your own metadata (`dev/DECISIONS.md`, "Your own metadata"): `custom_extensions` written as YAML or as Ossie's JSON
      string; a vocabulary per vendor declaring keys, levels, types and closed value lists with definitions — checked
      at start, served with the definitions; a strict Ossie export. Nothing of any one catalog in code.
- [ ] The dbt importer: a dbt project's descriptions, `meta` and tags into Ossie, `meta` passed through as written;
      which of its keys become Ossie's own places (a time column, a primary key) set in the importer's configuration,
      not in code.
- [ ] Skills folders (`list_skills`, `get_skill`); context and skills read straight from a git repository.
- [ ] The dialect question settled (ANSI SQL translated by sqlglot, engine variants in `custom_extensions`) and
      written in `DECISIONS.md`.

## Milestone 4. The docs site — 1 session

- [ ] `website/` — Astro + Starlight built from `docs/`, the author's detectkit pattern (`../detectkit/website`: the
      `sync-docs` script importing `docs/`, the nginx Dockerfile, the `website.yml` workflow pushing the image to GHCR and
      sending `repository_dispatch` to pipelab).
- [ ] In pipelab (`../pipelab`, under its own rules): the site at `semantic-warehouse.pipelab.dev` — a compose service
      behind Traefik and its `deploy-*.yml`, as detectkit's site is served.
- [ ] The guide as it stands published there; from now on every milestone's pages go live with it.

## Milestone 5. The other databases people use — 2 sessions

- [ ] PostgreSQL, MySQL; then BigQuery, Snowflake — each an adapter with its read-only session, its guard deny-list
      and probes; tested against real servers in containers where they exist.
- [ ] The self-check at start: probes with known verdicts run through the guard and the engine's own parser; a
      dialect either misreads is refused, not trusted.

## Milestone 6. Metrics — 2 sessions

- [ ] The metrics layer (`dev/DESIGN.md`, "Populations, filters and metric definitions"): populations, filters bound per
      population with their placement (refused when unbound), metric kinds and roll-up rules, templates with the layer's
      macros, presets, scenarios run in tests. Its schema published; the registry of `dev/RESEARCH.md` expressed in it
      without loss as the design's first check.
- [ ] `query_metric`: SQL compiled from the model (metric, dimensions, filters, a time range, joins along
      relationships), run through the guard; its SQL shown.
- [ ] Importers of metrics from dbt's semantic layer — Ossie's converters where they do the job.

## Milestone 7. Saved query sets — 1 session

- [ ] YAML sets of named, parameterised queries; `list_query_sets`, `run_saved_query`; parameters bound by the
      driver, never pasted.

## Milestone 8. The author's server instance — 1 session

- [ ] In pipelab: an instance of the server (its image from GHCR, `/mcp` routed with no redirect, a token or the
      organisation's gateway in front), its `deploy-*.yml`, secrets prefixed in GitHub — for the author's own use and a
      public demo over made-up data.

## Milestone 9. Release — 1 session

- [ ] Published on PyPI (`uvx semantic-warehouse-mcp`), a container image, docs for agents and for people who run it;
      checked with a second MCP client (not Claude).

## Later

Per-person access when served to a team (or left to a gateway); saved query sets made by agents; a semantic
model written back as Ossie from what agents learn — only with a person's review.
