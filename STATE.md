# Where we are

> Rewritten entirely at the end of every session by `/close`. Don't append. Under 100 lines.

**Updated:** 24 October 2026.

**Released: 0.1.0, pre-alpha** — on PyPI (`uvx semantic-warehouse-mcp`) and GHCR
(`ghcr.io/alexeiveselov92/semantic-warehouse-mcp:0.1.0`). **Milestone 3 — the semantic layer, your own metadata,
context and skills — is done** (`dev/PLAN.md`). Next: **milestone 4, the docs site at `swm.pipelab.dev`**. Parts of
8–9 (HTTP, the image, the release) came early, for a company's deployment. Skills and repositories are not released
yet: they are on `main`, in no published version.

## What exists

- **The server**: `semantic-warehouse-mcp --config <file>` over stdio, or `--http` (a token by `--token-env`, required
  off loopback; `/health`; stateless JSON answers); `--check`, `--check --offline` (CI: no database, no secret, no
  git). Tools: `about`, `list_connections`, `list_tables`, `describe_table`, `run_query`, `list_context`, `get_context`,
  `list_skills`, `get_skill`, `list_datasets`, `describe_dataset`, `list_metrics`, `describe_metric` — read-only,
  compact JSON with provenance, capped and saying so.
- **The configuration** (`config.py`, `files.py`): connections (`readable`, `large_tables`, `limits`, `instructions`,
  `context`, `skills`, `semantic_models`, `missing_tables`), `vocabularies`, `repositories`. Files read as written;
  secrets never repeated — a variable's name that looks like a value (a pasted token) neither.
- **Skills** (`skills.py`): folders in the Agent Skills format (`SKILL.md`: `name`, `description`), for the whole server
  and per connection; checked at start, read anew; a skill's text files served by the names its listing gives.
- **Repositories** (`repositories.py`): `context` and `skills` entries `{repository, path}` fetched by git — one commit,
  only the named paths' files; refreshed every `refresh_minutes` in the background, a commit that breaks the checks
  never served; the token as a header through git's own environment; `fetched_from` and `commit` in answers.
- **Semantic models** in Apache Ossie (`semantic.py`), **vocabularies** (`vocabulary.py`), **the dbt importer**
  (`importers/dbt.py`).
- **ClickHouse** (22.11, 25.8, 26.3; `readonly = 1` users within their profile) and **DuckDB**. **The guard**
  (`guard.py`): one `SELECT`, `readable` everywhere, large tables bounded. 12 refusal codes.
- **Releases**: `publish.yml` (PyPI, trusted publishing) and `image.yml` (GHCR) for a tag `v<version>` that is
  `pyproject.toml`'s. The image now carries git (without Perl): 455 MB, 89 MB more than 0.1.0's.
- **The guide** (`docs/`): getting started; reference — tools, configuration (skills, repositories), semantic models,
  the dbt importer, the guard, engines, running it. `tests/test_docs.py` holds every tool, key and option to its entry.

## How it was verified

- 578 tests pass (with a real ClickHouse in a container and a git server on loopback — `tests/git_server.py`, `git
  http-backend`); ruff clean.
- Every protection of this session held by a test that fails when it is removed: 30 + 22 removals, all caught (184
  before, milestones 1–3).
- A second, refuting reader on skills and repositories: 10 findings and 7 sentences of the guide, all closed, each with
  a test (`CHANGELOG.md`).
- Live: the project's own public repository fetched from GitHub over https (partial clone, 2.6 s), locally and in the
  image on a read-only root filesystem.
- Real Claude Code through the server: found the skill (`list_skills`), read it and the file it refers to
  (`get_skill`), read a context document fetched from GitHub; March revenue by currency (EUR 35,057.66 / 698 orders;
  USD 12,560.35 / 241) checked by a direct query.

## Decided this sitting (`dev/DECISIONS.md`)

Skills in the Agent Skills format, checked at start, read anew; **context and skills from a git repository** — named
`repositories`, fetched by git, refreshed every 10 minutes, context and skills only (the author chose each part); **a
model's expression runs as written in its connection's dialect, never translated** (the author; built with the metrics,
milestone 6); **the site at `swm.pipelab.dev`** (the author; the package keeps its name).

## Next step

Milestone 4 (`dev/PLAN.md`): `website/` — Astro + Starlight from `docs/`, detectkit's pattern (`../detectkit/website`);
in pipelab (its own rules), the site at `swm.pipelab.dev`. Then milestone 5, the other databases.

A company's deployment wants skills and repositories in an image: that needs a release (below).

## What the author needs to do

1. **A release for skills and repositories** when the company's deployment wants them: bump `pyproject.toml` to
   `0.2.0`, commit, tag `v0.2.0`, push the tag (or ask the agent to bump and commit; the tag stays the author's).
2. For the ClickHouse tests locally, Docker running. (`swm.pipelab.dev` needs no DNS work: pipelab's zone has a
   wildcard `*.pipelab.dev`.)

## Don't

- Don't write a real company, database, schema, table or semantics anywhere here — the repository is public.
- Don't name any team's metadata keys in code — they are declared in a vocabulary (a test holds it: an answer key
  named like one is caught too — hence `fetched_from`).
- Don't put an engine's specifics outside its adapter.
- Don't add a tool without `read_only_hint=True`, `destructive_hint=False`, or without its entry in `docs/reference/tools.md`.
- Don't add a protection without a test that fails when it is removed.
- Don't rephrase a model's descriptions, instructions, documents or skills — serve them as written.
- Don't serve over HTTP without a token anywhere but loopback.
- Don't let a token reach git's command line, a log or an answer; don't follow a redirect with it.
- Don't tag a version that is not `pyproject.toml`'s; don't publish `latest`.
- Don't point `SWM_TEST_CLICKHOUSE_URL` at a real server: the tests rebuild `swm_*` databases there.
