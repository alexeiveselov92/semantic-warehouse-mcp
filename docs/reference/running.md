# Running it

The server runs one of two ways: over **stdio**, started by one person's agent on their own machine, or over
**Streamable HTTP**, as a server a team shares. Either way it reads the same [configuration](configuration.md), checks
it and reaches every database before it answers anything.

It is a Python package on PyPI — `uvx semantic-warehouse-mcp …` runs it without installing anything else, `pip install
semantic-warehouse-mcp` installs it (Python 3.12 or newer) — and a [container image](#the-image). Context and skills
[read from a repository](configuration.md#repositories) need the `git` program as well (2.31 or newer), which the image
holds.

```text
semantic-warehouse-mcp --config <file> [--check [--offline]]
semantic-warehouse-mcp --config <file> --http [--host 127.0.0.1] [--port 8000] [--path /mcp] [--token-env NAME]
semantic-warehouse-mcp import-dbt <configuration> [--strict] [--check]     # the dbt importer: its own page
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

The [dbt importer](dbt-importer.md) is a command of its own, `import-dbt`.

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
the gateway, not the person.

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
