# Running it

The server runs one of two ways: over **stdio**, started by one person's agent on their own machine, or over
**Streamable HTTP**, as a server a team shares. Either way it reads the same [configuration](configuration.md), checks
it and reaches every database before it answers anything.

It is a Python package on PyPI, each engine's driver an extra ([engines](engines.md#drivers)) — `uvx --from
'semantic-warehouse-mcp[all]' semantic-warehouse-mcp …` runs it without installing anything else, `pip install
'semantic-warehouse-mcp[postgres]'` installs it with one driver (Python 3.12 or newer: give `uvx` `--python 3.12` when
the default is older) — and a [container image](#the-image), which holds every driver. Context and skills
[read from a repository](configuration.md#repositories) need the `git` program as well (2.31 or newer), which the image
holds.

```text
semantic-warehouse-mcp --config <file> [--check [--offline]]
semantic-warehouse-mcp --config <file> --http [--host 127.0.0.1] [--port 8000] [--path /mcp] [--token-env NAME]
                       [--caller-header NAME] [--log-calls]
semantic-warehouse-mcp import-dbt <configuration> [--strict] [--check]     # the dbt importer: its own page
semantic-warehouse-mcp import-database --config <file> --output <file> …   # the database importer: its own page
```

| Option | Default | What |
|---|---|---|
| `--config` | `SEMANTIC_WAREHOUSE_MCP_CONFIG` | the configuration file |
| `--check` | | check the configuration, reach every database, say what is served, and exit — `0` when fine, `2` with every problem |
| `--offline` | | with `--check`: reach no database and read no secret — for CI. Every model, vocabulary and `readable` rule is checked; a source table is held to `readable` but not looked up in the database |
| `--http` | | serve over Streamable HTTP instead of stdio |
| `--host` | `127.0.0.1` | with `--http`: the address to listen on — `0.0.0.0` to be reached from other machines (a container) |
| `--port` | `8000` | with `--http`: the port |
| `--path` | `/mcp` | with `--http`: the MCP endpoint's path |
| `--token-env` | | with `--http`: the environment variable holding the token every request must carry. **Required** unless `--host` is `127.0.0.1`, `localhost` or `::1` |

| `--caller-header` | | with `--http` and `--token-env`: the request header in which a gateway names the caller — see [Behind a gateway](#behind-a-gateway) |
| `--log-calls` | | write a JSON line for every tool call to standard error — see [A log of calls](#a-log-of-calls) |

The [dbt importer](dbt-importer.md) and the [database importer](database-importer.md) are commands of their own,
`import-dbt` and `import-database`.

## Over stdio — one person's agent

The agent starts the server and talks to it through its standard input and output; nothing listens on the network.
[Getting started](../getting-started.md) connects Claude Code this way.

## Over HTTP — a server a team shares

```sh
export SWM_TOKEN=…                         # a long random value; agents send it, nothing else knows it
semantic-warehouse-mcp --config config.yaml --http --host 0.0.0.0 --port 8000 --token-env SWM_TOKEN
```

- **The token.** Every request carries `Authorization: Bearer <token>`; a request without it, or with another, is
  answered `401` and reaches no tool. The token is read from the variable at start — its value is never written to a
  file, a log or an answer. A server that other machines can reach does not start without one; a variable named but
  not set stops it too, before any database is reached.
- **`/health`** answers `200` with `{"status": "ok", …}` without a token, and reaches no database — a readiness probe
  that a database outage does not turn into restarts.
- **Stateless.** Each request is answered on its own, one JSON answer each: any number of copies of the server can sit
  behind one address, and a client may reach any of them.
- **POST only.** A `GET` of the MCP endpoint — a client asking for a stream of the server's own messages — is answered
  `405` at once (`Allow: POST`), as Streamable HTTP lets a server say it offers none: the server sends nothing it was
  not asked for, and a gateway gathering its backends' streams is not left waiting on an empty one.

An agent connects with the endpoint and the header — Claude Code:

```sh
claude mcp add --transport http warehouse https://mcp.example.com/mcp --header "Authorization: Bearer $SWM_TOKEN"
```

### Repositories

A server reading context or skills [from a repository](configuration.md#repositories) keeps its copies in a temporary
folder (`TMPDIR`, or `/tmp`) and removes them when it ends — at the end of its input, or stopped by `SIGTERM`. It says
in its log (standard error) when it serves a new commit, and why it does not when a look for one goes wrong. Of the
server's environment only these reach git: `PATH`; behind a proxy, the usual variables (`HTTPS_PROXY`, `HTTP_PROXY`,
`ALL_PROXY`, `NO_PROXY`, in either case) — for an `https://` repository only, so a token never crosses a proxy in clear
text; and a certificate authority's (`SSL_CERT_FILE`, `SSL_CERT_DIR`, `GIT_SSL_CAINFO`, `GIT_SSL_CAPATH`).

### Behind a gateway

An organisation's MCP gateway (signing people in, deciding who sees which servers) usually reaches its backends with
a key of its own: give that key to the server as its token. Who is asking stays at the gateway — the server answers
the gateway, not the person — unless the gateway says who, and you name the header it says it in:

```sh
semantic-warehouse-mcp --config config.yaml --http --host 0.0.0.0 --token-env SWM_TOKEN --caller-header X-Forwarded-User
```

- **What it is for.** A limit per person, and a log that says who asked. All of a gateway's people reach the database
  as the server's one user, so a database's own limit per user is one limit for everybody. With the caller's name the
  server tells an engine that can account a query to a person who asked: [ClickHouse](engines.md#clickhouse) takes it
  as the query's quota key. The other engines are told nothing yet.
- **What the server does with the name.** It decides nothing by it, puts it in no answer and holds it for that one
  request. It is written in one place only, and only when you ask: the [log of calls](#a-log-of-calls).
- **Whom it trusts.** Whoever holds the token — which is why the option is refused without `--token-env`. Have the
  gateway *set* the header from the sign-in, never pass on one a client sent.
- **A name** is a login, an e-mail address or a key's name: letters, digits and `. _ @ + -`, up to 128 characters. A
  request without the header, or with a value that is not such a name, is served as before — for nobody named.

A gateway that joins several servers under one name may pass their tools on and drop the instructions each gives when
a client connects — and with them your team's rules. Ask an agent connected through it whether it has this server's
instructions; if not, `instructions_in_run_query` ([configuration](configuration.md#instructions-and-context))
carries them in `run_query`'s description, which every gateway passes.

## A log of calls

```sh
semantic-warehouse-mcp --config config.yaml --http --host 0.0.0.0 --token-env SWM_TOKEN --caller-header X-Forwarded-User --log-calls
```

With `--log-calls` the server writes one JSON line to standard error for every tool call — over HTTP or stdio — after
the call ends. It is for the people who run the server: what agents look for, in what order, what they are refused,
how long it takes. Collect the lines the way you collect any process's log.

```json
{"event": "tool_call", "at": "2026-01-15T09:30:12.953+00:00", "caller": "ann@example.com", "tool": "run_query",
 "arguments": {"sql": "SELECT count(*) AS n FROM marts.orders", "connection": null}, "outcome": "answered",
 "answer_characters": 211, "connection": "shop", "row_count": 1, "truncated": false, "seconds": 0.043}
```

| Key | What |
|---|---|
| `event` | always `tool_call` — to tell these lines from the rest of the log |
| `at` | when the call began, UTC |
| `caller` | the name a gateway gave ([`--caller-header`](#behind-a-gateway)); `null` over stdio and where nobody is named |
| `tool`, `arguments` | the tool and what it was called with, defaults included. A text longer than 20,000 characters is cut, and says so |
| `outcome` | `answered`, or `error` — a refusal by the guard, a database's error, a wrong argument |
| `error` | with `error`: what the agent was told, its first 1,000 characters |
| `answer_characters` | with `answered`: the answer's size |
| `connection`, `row_count`, `truncated` | where the answer states them (`run_query`): copied from it |
| `seconds` | how long the call took |

- **The answer is never written** — no row of the database reaches the log. A query's text is: treat the log as you
  treat the database's own query log.
- **A caller's name is written** — the one place the server keeps it. Leave `--log-calls` out and it is kept nowhere.
- **A call is never failed by its log**: a line that cannot be written is dropped.
- **No sessions.** The server is stateless and knows no session; a person's calls close in time are one piece of work.

## The image

The server's container image holds the package and its locked dependencies and `git`, runs as user `10001` without
rights, and works on a read-only root filesystem with a writable `/tmp` (where it keeps the copies of repositories). It serves over HTTP on port 8000, reading
`/config/config.yaml` and the token from `SWM_TOKEN`:

```sh
docker run --read-only --tmpfs /tmp --cap-drop ALL \
  -v "$PWD/my-config:/config:ro" -e SWM_TOKEN="$SWM_TOKEN" -e WAREHOUSE_PASSWORD="$WAREHOUSE_PASSWORD" \
  -p 8000:8000 ghcr.io/alexeiveselov92/semantic-warehouse-mcp:<version>
```

Mount the configuration and everything it names (semantic models, vocabularies, context documents, a DuckDB file) at
`/config`, read-only; pass each secret the configuration names by its variable. Arguments after the image replace
the command — `--config /config/other.yaml --http --host 0.0.0.0 --token-env OTHER`. Images are published for each
release, tagged with its version and commit (`0.1.0`, `sha-1a2b3c4`), never `latest`.

Where nothing can be mounted — a cluster that runs images only — build your own on the released one, with the
configuration in it, and check it as it is built:

```dockerfile
FROM ghcr.io/alexeiveselov92/semantic-warehouse-mcp:<version>
COPY --chown=10001:10001 my-config/ /config/
RUN ["semantic-warehouse-mcp", "--config", "/config/config.yaml", "--check", "--offline"]
```

`--check --offline` stops the build on a wrong configuration without reaching a database. Secrets still come as
variables when the container runs — never into the image.
