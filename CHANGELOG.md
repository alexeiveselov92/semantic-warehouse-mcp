# Changelog

One line per change, newest on top: `YYYY-MM-DD — what was done`.

---

2026-10-24 — the team's own `instructions` and `context` documents, for the whole server and per connection: instructions in the server's instructions and `list_connections`, documents by `list_context` / `get_context` (as written, read anew, in parts); real Claude Code cited each rule's source
2026-10-24 — milestone 2 done: the ClickHouse adapter (GET — read-only by ClickHouse's own rule; every overflow mode `throw`, a cutting profile overridden; the extra row that shows a cut; `EXPLAIN QUERY TREE` naming the tables read; dictionaries checked against `readable`), DuckDB's own reading naming tables, tests against a real ClickHouse (a container; CI's service), the made-up shop loadable into ClickHouse; real Claude Code answered over DuckDB and ClickHouse at once, numbers checked
2026-10-24 — your own metadata decided (option B: YAML in Ossie's `custom_extensions`, a vocabulary per vendor, nothing of one catalog in code); the plan reordered: ClickHouse, then the semantic layer with the dbt importer, then the site; a production dbt catalog's shapes studied (generalised in `dev/RESEARCH.md`)

2026-10-24 — milestone 1 done: the configuration and its check (every problem named at once, typos with the likely key, duplicate YAML keys refused, secrets never repeated), the adapter interface and DuckDB (read-only file, external access off, configuration locked, its own parser asked), the guard on sqlglot (one read-only SELECT, readable tables wherever read, large tables bounded from below, settings/table functions/denied function families refused — 12 refusal codes, each a test case), `list_connections`, `list_tables`, `describe_table`, `run_query` with provenance, exact numbers and capped answers; a made-up shop (`examples/data/make_shop.py`); the guide's reference for configuration, the guard and engines, and getting started, held by tests; real Claude Code answered over stdio, its numbers checked by a direct query
2026-10-24 — the guard refuted by a second reader: a table in parentheses (`FROM (secret.t) x`) and ClickHouse's `x IN db.table` read past it, then `dictGet*` / `joinGetOrNull` — all closed; every table node is now a table read unless proven a CTE's name

2026-10-24 — ready to start: the guide's rule with a test (every tool in `docs/reference/tools.md`), `/close` checks the guide; the site moved to milestone 2; the hosting (pipelab) and where a company's requirements come from written in `CLAUDE.md`; context and parallel-work rules
2026-10-24 — the metrics layer designed beside Ossie: populations, filters bound per population (every metric filtered through another entity), roll-up rules, templates, presets, scenarios — from a production registry studied
2026-10-24 — `docs/` is the public documentation's source (a site to come, Astro + Starlight like detectkit's, hosted through pipelab); design, plan, decisions, research moved to `dev/` ; renamed from `warehouse-mcp`
2026-10-24 — groundwork: the repository, README, design, plan, decisions (Apache Ossie as the semantic layer — the 0.2 shape, 0.1.1 read too), research, the session flow; examples (a made-up shop's Ossie model, context, a skill, a saved query set); a skeleton server (stdio, `about`) with tests, ruff and CI
