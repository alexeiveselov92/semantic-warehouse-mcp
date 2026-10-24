# Decisions

What is decided and why. Newest at the bottom; grep it.

## 2026-10-24 · An open-source, universal, read-only MCP for databases with their semantics

**Decision** (the author). A separate open-source project under the author's own GitHub, not any company's:
an MCP server for SQL databases that takes a semantic layer in an open format, your own metadata, metrics,
context and skills — universal for any database and any MCP client (not tied to one model vendor). A company's
use of it — connections, its semantic model, its documents, deployment — lives in that company's own
repositories as configuration; nothing of any company is written here.

**Why.** What makes an agent's numbers right is semantics; every team has it, in its own shape. One server that
reads it in an open format and serves it beside any database is useful to many teams, configured rather than
rewritten. The author's earlier company-internal platform proved the parts (a guard over the database's own
parse, semantics served as they are, provenance) and showed they are general.

## 2026-10-24 · The semantic layer is Apache Ossie

The input format is [Apache Ossie](https://ossie.apache.org/) (formerly OSI): an open YAML/JSON specification
backed by Snowflake, dbt Labs, Salesforce and others, with converters from dbt, Cube, Databricks and more. It has
the places we need — `ai_context` for agents, `custom_extensions` for anything of our own — so no format of our
own is invented. Others (dbt's) come in through importers.

## 2026-10-24 · Which Ossie: the 0.2 shape, 0.1.1 read too

The format is moving. The released **0.1.1** (still named OSI, tag `osi-0.1.1-rc1`, `core-spec/osi-schema.json`)
wraps the model in a root `semantic_model` beside `dialects` and `vendors`; the draft **0.2.0.dev0** (Ossie,
`main`, `core-spec/ossie-schema.json`) is flat — `name`, `datasets`, `relationships`, `metrics`, `ai_context`,
`custom_extensions` at the root. We write and serve the **0.2 shape** (where the spec goes) and **read 0.1.1 too**
by unwrapping `semantic_model`. The schema of each supported version is bundled and pinned; a document is checked
against its own version's schema at start, and a version we don't know is refused, naming it. The example
`examples/semantic/shop.yaml` validates against 0.2.0.dev0 with no errors (checked 2026-10-24).

## 2026-10-24 · Python, the official MCP SDK, sqlglot, Apache-2.0, written fresh

- **Python 3.12 with uv**, the official MCP Python SDK (2.x: `MCPServer`, the in-process `Client` for tests);
  installed with one command (`uvx`); the same stack as its sibling, repo-reader-mcp.
- **sqlglot** parses and translates SQL of 30+ dialects: one guard for every database, and model expressions
  translated to each database's dialect.
- **Apache-2.0**, like Ossie: an explicit patent grant; the license companies take in most easily.
- **Written fresh**: the earlier platform's code belongs to the company it was written for. Its lessons are
  carried as design ([DESIGN.md](DESIGN.md)), not as code.
