"""Serving over Streamable HTTP, for a server shared by a team (docs/reference/running.md).

Every request but the health check carries the token, compared in constant time; the token is read from the variable
the command names, never written anywhere. A server reachable from other machines is not started without one."""

from __future__ import annotations

import hmac
import json
import os
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__, files

LOOPBACK = ("127.0.0.1", "localhost", "::1")
HEALTH = "/health"
UNAUTHORIZED = json.dumps(
    {"error": "unauthorized", "note": "Send the server's token: Authorization: Bearer …"}
).encode()
UNAUTHORIZED_HEADERS = [
    (b"content-type", b"application/json"),
    (b"www-authenticate", b'Bearer realm="semantic-warehouse-mcp"'),
]


class HttpProblem(Exception):
    """Why the server cannot be served over HTTP as asked — never with a secret's value."""


def token_of(variable: str | None, host: str) -> str | None:
    """The token every request must carry: the value of `variable`, read here and kept in memory only."""
    if variable is None:
        if host not in LOOPBACK:
            raise HttpProblem(
                f"a server on {host} is reachable from other machines: name the variable holding the token every "
                "request must carry (--token-env NAME), or serve on 127.0.0.1"
            )
        return None
    token = os.environ.get(variable)
    if not token:
        named = files.variable(variable, "--token-env")
        raise HttpProblem(f"the variable {named} is not set (or empty): it holds the token every request must carry")
    return token


def app(server: MCPServer, *, path: str, token: str | None, host: str) -> Any:
    """The server as an ASGI application: MCP at `path`, stateless (any replica answers any request), one JSON answer
    per request; `/health` for a readiness probe, which reaches no database."""

    @server.custom_route(HEALTH, methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "name": "semantic-warehouse-mcp", "version": __version__})

    served = server.streamable_http_app(streamable_http_path=path, stateless_http=True, json_response=True, host=host)
    return _Bearer(served, token) if token else served


class _Bearer:
    """Refuses every HTTP request but the health check that does not carry `Authorization: Bearer <token>` — and every
    WebSocket, which MCP here never uses. Only the server's own start and stop pass as they are."""

    def __init__(self, inner: Any, token: str) -> None:
        self.inner = inner
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "lifespan" and scope["path"] != HEALTH:
            given = next((value for name, value in scope["headers"] if name == b"authorization"), b"")
            if not hmac.compare_digest(given, self.expected):
                await send({"type": "http.response.start", "status": 401, "headers": UNAUTHORIZED_HEADERS})
                await send({"type": "http.response.body", "body": UNAUTHORIZED})
                return
        await self.inner(scope, receive, send)


def serve(server: MCPServer, *, host: str, port: int, path: str, token_env: str | None) -> None:
    """Serve over Streamable HTTP until stopped. Raises HttpProblem before serving when it cannot be done safely."""
    if not path.startswith("/") or path == HEALTH:
        raise HttpProblem(f"--path {path!r}: a path starting with / other than {HEALTH}")
    token = token_of(token_env, host)
    uvicorn.run(app(server, path=path, token=token, host=host), host=host, port=port, log_level="warning")
