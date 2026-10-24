# semantic-warehouse-mcp documentation

The documentation for people who use and run semantic-warehouse-mcp. This folder is the **single source**: the
site (Astro + Starlight, planned — `dev/PLAN.md`) is built from these files; edit them here, never in the site.

How the project is built and planned is not here: that is `dev/` (design, plan, decisions, research).

## Pages

- [Getting started](getting-started.md) — from nothing to an agent answering about a made-up shop's data.
- **Reference**
  - [Tools](reference/tools.md) — every tool, its inputs and answers.
  - [Configuration](reference/configuration.md) — every key of the configuration file, and its check.
  - [Semantic models and vocabularies](reference/semantic-models.md) — Apache Ossie documents, your own metadata
    declared in a vocabulary, every check at start.
  - [The guard](reference/guard.md) — how a query is read, every refusal and why, the limits.
  - [Engines](reference/engines.md) — the databases it reads, and how each is kept read-only.

Planned, each written with the change that makes it true:

- **Guides** — connecting databases (read-only users, `readable`, large tables, secrets by name); writing a semantic
  model step by step (the reference is [semantic models and vocabularies](reference/semantic-models.md)); importing
  from dbt; context documents and skills; saved query sets; running it for a team.
- **Contributing** — how the project is built and tested.

Three readers: people who use it through their agent (getting started, guides), people who run it (guides,
reference), people who contribute (a contributing page). A page is written in the same commit as the change that makes
it true; a tool without its reference entry fails the tests, and so does a refusal, a configuration key or an engine
without its entry.
