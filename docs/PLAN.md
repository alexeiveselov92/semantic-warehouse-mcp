# Plan

Measured in sessions — one sitting of the author's, from half an hour to a few hours. Each milestone ends
with something visible: a real agent answering through the server, its numbers checked by a direct query.

## Milestone 0. Groundwork — ✅ 2026-10-24

- [x] The repository, its license (Apache-2.0), README, design, decisions, research.
- [x] The session flow: `CLAUDE.md`, `/start`, `/close`, `STATE.md`, rules.
- [x] A running skeleton: the server over stdio with one tool, its test through the SDK's in-process client,
      lint, CI.

## Milestone 1. One database, read-only, with the guard — 2 sessions

- [ ] The configuration file (YAML) and its check: a wrong entry stops the server, naming it.
- [ ] The adapter interface; DuckDB first (in-process — the tests need no server).
- [ ] The guard on sqlglot: one statement, `SELECT` / `WITH` only, `readable` tables, `large_tables` bounded,
      rows and time capped; each refusal a test case; each protection with a test that fails when removed.
- [ ] `list_connections`, `run_query`, a table's columns; provenance in every answer.
- [ ] Real Claude Code over stdio answers a question on a made-up dataset; the number checked directly.

## Milestone 2. The semantic layer, context and skills — 2 sessions

- [ ] Apache Ossie documents loaded and checked against the Ossie schema; `list_datasets`, `describe_dataset`,
      `list_metrics`, `describe_metric` — `ai_context` and `custom_extensions` served as they are.
- [ ] Context documents and skills folders; `get_context`, `list_skills`, `get_skill`.
- [ ] The dialect question settled (ANSI SQL translated by sqlglot, engine variants in `custom_extensions`) and
      written in `DECISIONS.md`.

## Milestone 3. The databases people use — 2–3 sessions

- [ ] ClickHouse, PostgreSQL, MySQL; then BigQuery, Snowflake — each an adapter with its read-only session, its
      guard deny-list and probes; tested against real servers in containers where they exist.

## Milestone 4. Metrics and importers — 2 sessions

- [ ] `query_metric`: SQL compiled from the model (metric, dimensions, filters, a time range, joins along
      relationships), run through the guard; its SQL shown.
- [ ] Importers from dbt (descriptions and `meta`, semantic models and metrics) into Ossie — Ossie's converters
      where they do the job.

## Milestone 5. Saved query sets — 1 session

- [ ] YAML sets of named, parameterised queries; `list_query_sets`, `run_saved_query`; parameters bound by the
      driver, never pasted.

## Milestone 6. Release — 1 session

- [ ] Published on PyPI (`uvx warehouse-mcp`), a container image, docs for agents and for people who run it;
      checked with a second MCP client (not Claude).

## Later

Per-person access when served to a team (or left to a gateway); saved query sets made by agents; a semantic
model written back as Ossie from what agents learn — only with a person's review.
