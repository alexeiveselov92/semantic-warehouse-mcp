# semantic-warehouse-mcp documentation

The documentation for people who use and run semantic-warehouse-mcp. This folder is the **single source**: the
site (Astro + Starlight, planned — `dev/PLAN.md`) is built from these files; edit them here, never in the site.

How the project is built and planned is not here: that is `dev/` (design, plan, decisions, research).

## Pages (planned)

- **Getting started** — install (`uvx semantic-warehouse-mcp`), connect it to an MCP client (Claude Code, Claude,
  ChatGPT, Cursor, Codex), a first question about a database.
- **Guides** — connecting databases (engines, read-only users, `readable`, large tables, secrets by name); writing a
  semantic model in Apache Ossie (datasets, fields, metrics, relationships, `ai_context`, your own
  `custom_extensions`); importing from dbt; context documents and skills; saved query sets; running it for a team.
- **Reference** — every tool, its inputs and answers; the configuration file; the guard (what it refuses and why);
  supported engines; limits.

A page is written in the same commit as the change that makes it true.
