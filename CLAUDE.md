# CLAUDE.md — semantic-warehouse-mcp

A read-only MCP server for SQL databases with their semantics: a semantic layer in an open format (Apache
Ossie), your own metadata, metrics, context and skills — for any database and any MCP client. Open source
(Apache-2.0), the author's own project. Its sibling, `repo-reader-mcp` (code with its git history), is a separate
repository with the same rules.

**Language rule.** The author talks to Claude in Russian — reply in Russian. **Everything written to the
repository is in English**: code, comments, docs, commit messages.

## The goal — the author's; don't reword it

1. **Any database** — not shaped by one database or one company's tables; configured in YAML.
2. **A semantic layer in a universal format** (Apache Ossie), with **your own metadata** (anything — links to code,
   owners) passed through, **metrics** defined once, **your own context and skills** beside the data.
3. **Saved query sets** — a dashboard's substance as SQL and filters; how it is drawn is not this project's.
4. **Never change anything**: read-only, checked.
5. **Any MCP client** — not tied to one model vendor.
6. A company applies it in **its own repositories** (connections, its model, its documents, deployment). Nothing
   of any company lives here.

## What a session reads

At the start (`/start` does it): `STATE.md` — where we are; the current milestone's section of `dev/PLAN.md`.
When relevant, by section: `dev/DESIGN.md` (how it works), `dev/DECISIONS.md` (what is decided, grep it),
`dev/RESEARCH.md` (Ossie, what exists, lessons carried).

## Who the author is

An analyst, **not a developer**: fluent in SQL, warehouses and semantic layers, not in Python internals. Explain
through **consequences**, not pattern names; mark what the agent does and what the author does by hand; steps by
hand in small batches, then wait for "done". Propose decisions with reasoning — the author decides. No person-day
estimates — sessions. Every session ends with something visible.

## Hard rules

- **No installation in the code.** No company, product, person, database, schema, table or host of a real
  installation — in code, tests, docs or examples. Examples are made up (`acme`, `example.com`, a made-up shop).
  The repository is the public's: nothing private ever enters it — no credentials, no real query results, no
  company semantics.
- **No engine in shared code.** An engine's specifics live in its adapter; the guard, the semantic layer and the
  tools are engine-neutral.
- **Semantics are served, never re-told.** The model's descriptions, `ai_context`, `custom_extensions`, documents
  and skills reach agents as written — never rephrased into code or into the server's own words.
- **Read-only, checked.** The database's read-only user holds the line; the guard makes refusals clear
  (`dev/DESIGN.md`, "The guard"). A limit is never sent in a mode that cuts silently.
- **Secrets by name.** The configuration names the variable; the value never appears in a file, a log, an answer
  or an error.
- **Any client.** stdio and Streamable HTTP; tool annotations (`read_only_hint`, `destructive_hint`) on every tool;
  answers capped; every answer with its SQL and time.
- **`docs/` is the public documentation** — the source of the project's site. A change that alters what a user
  sees or does updates its page in the same commit. Design, plans, decisions and research live in `dev/`, never on the
  site.
- **Written fresh.** The author's earlier company platform informs the design (`dev/RESEARCH.md`); its code is not
  copied here.

## Quality

- **A wrong number looks exactly like a right one.** Every number shown in a check is checked by a direct query.
- **Verify it yourself** — tests on made-up data (DuckDB in-process; real servers in containers per engine), and a
  real MCP client answering through the server. "Should work" is not a report.
- **A protection gets a test that fails when it is removed** — break it, see the test fail, put it back.
- **Re-read your own code.** A second, refuting reader for the guard.

## Session cycle

`/start` at the beginning, `/close` at the end.

1. Read `STATE.md` and the current milestone in `dev/PLAN.md`; agree the session's plan in one paragraph.
2. Do it; show the result (a real agent's answer, numbers checked).
3. **Rewrite `STATE.md` entirely** (not append; under 100 lines); one line per change in `CHANGELOG.md`; decisions
   in `dev/DECISIONS.md`; progress marked in `dev/PLAN.md`.
4. Commit; ask about pushing — the author works from several computers.

**All context lives in this repository — nothing in Claude's local memory** (the author's rule).

## Commands

| Command | What |
|---|---|
| `uv sync` | the environment (Python 3.12, from `.python-version`) |
| `uv run pytest` | the tests |
| `uv run ruff check . && uv run ruff format --check .` | lint and format |
| `uv run semantic-warehouse-mcp` | the server over stdio |

## Shell

Write files with the Write and Edit tools, not through the shell. Temporary files go to the session's scratch dir
or `/tmp`, never into the repository tree.
