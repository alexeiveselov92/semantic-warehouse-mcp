# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 24 October 2026.

**Current milestone: 1 — one database, read-only, with the guard** (`dev/PLAN.md`). Milestone 0 (groundwork)
is done.

## What exists

- The repository (public, Apache-2.0), README; `dev/` — design, plan, decisions, research; `docs/` — the public
  documentation's source, with `docs/reference/tools.md` held by a test (every tool has its entry); the session flow
  (`CLAUDE.md`, `/start`, `/close` — the guide checked at close, context only in the repository, pushed).
- A skeleton: the server over stdio (`uv run semantic-warehouse-mcp`) with one tool, `about`; its tests through the SDK's
  in-process client (every tool read-only; `about` answers); ruff; CI on GitHub Actions.
- Examples of what the server will read (`examples/`): a configuration draft, a made-up shop's semantic model in
  Apache Ossie (valid against Ossie's 0.2.0.dev0 schema), a context document, a skill, a saved query set.

## Decided

The semantic layer is Apache Ossie — the 0.2 shape, 0.1.1 read too — with a metrics layer of our own beside it
(populations, filters bound per population, roll-up rules, templates, presets, scenarios); sqlglot for parsing and translating SQL;
Python with the official MCP SDK 2.x (`dev/DECISIONS.md`).

## Next step

Milestone 1: the YAML configuration and its check, the adapter interface with DuckDB first, the guard on sqlglot
(each refusal a test case, each protection a test that fails when removed), `run_query` with provenance — then
real Claude Code over stdio answering on a made-up dataset, the number checked directly.

Then milestone 2: the docs site (Astro + Starlight like detectkit's, served by pipelab) — the guide on the web from
the first tools on.

## What the author needs to do

Nothing blocks milestone 1. Open Claude Code in this repository's folder and run `/start`.

## Don't

- Don't write a real company, database, schema, table or semantics anywhere here — the repository is public.
- Don't put an engine's specifics outside its adapter.
- Don't add a tool without `read_only_hint=True`, `destructive_hint=False` (a test refuses it).
- Don't rephrase a model's descriptions or documents into code or the server's own words — serve them as written.
