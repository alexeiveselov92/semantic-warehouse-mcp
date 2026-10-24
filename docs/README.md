# semantic-warehouse-mcp documentation

The documentation for people who use and run semantic-warehouse-mcp. This folder is the **single source**: the
site (Astro + Starlight, planned — `dev/PLAN.md`) is built from these files; edit them here, never in the site.

How the project is built and planned is not here: that is `dev/` (design, plan, decisions, research).

## Pages

Written now: [Reference — tools](reference/tools.md). Planned, each written with the change that makes it true:

- **Getting started** — install (`uvx semantic-warehouse-mcp`), connect it to an MCP client (Claude Code, Claude,
  ChatGPT, Cursor, Codex), a first question about a database.
- **Guides** — connecting databases (engines, read-only users, `readable`, large tables, secrets by name); writing a
  semantic model in Apache Ossie (datasets, fields, metrics, relationships, `ai_context`, your own
  `custom_extensions`); importing from dbt; context documents and skills; saved query sets; running it for a team.
- **Reference** — every tool, its inputs and answers; the configuration file; the guard (what it refuses and why);
  supported engines; limits.

Three readers: people who use it through their agent (getting started, guides), people who run it (guides,
reference), people who contribute (a contributing page). A page is written in the same commit as the change that makes
it true; a tool without its reference entry fails the tests.
