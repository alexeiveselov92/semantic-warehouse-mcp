"""Serving over Streamable HTTP, through a real HTTP server and the SDK's own client: the token on every request but the
health check, never served without one where other machines reach it."""

import json
import socket
import threading
import time

import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from semantic_warehouse_mcp import serving
from semantic_warehouse_mcp.server import build_server, main

pytestmark = pytest.mark.anyio

TOKEN = "made-up-token-1234"


@pytest.fixture
def served(shop_config):
    """The made-up shop served over HTTP on a free port of 127.0.0.1, with a token or without."""
    servers = []

    def serve(token=TOKEN):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        application = serving.app(build_server(shop_config()), path="/mcp", token=token, host="127.0.0.1")
        server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.02)
        servers.append(server)
        return f"http://127.0.0.1:{port}"

    yield serve
    for server in servers:
        server.should_exit = True


def connect(url, token):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return Client(streamable_http_client(f"{url}/mcp", http_client=httpx2.AsyncClient(headers=headers)))


async def test_a_client_with_the_token_is_served(served):
    url = served()
    async with connect(url, TOKEN) as client:
        tools = (await client.list_tools()).tools
        result = await client.call_tool("run_query", {"sql": "SELECT count(*) AS n FROM marts.orders"})
    assert all(tool.annotations.read_only_hint and not tool.annotations.destructive_hint for tool in tools)
    assert json.loads(result.content[0].text)["rows"] == [[6]]


@pytest.mark.parametrize("token", [None, "wrong", TOKEN + "x", TOKEN[:-1]])
def test_a_request_without_the_token_is_refused(served, token):
    url = served()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    answer = httpx.post(
        f"{url}/mcp", json=request, headers={**headers, "Accept": "application/json, text/event-stream"}
    )
    assert answer.status_code == 401 and answer.headers["www-authenticate"].startswith("Bearer")
    assert TOKEN not in answer.text


@pytest.mark.parametrize("path", ["/HEALTH", "//health", "/health/../mcp", "/health/", "/mcp/", "/elsewhere"])
def test_only_the_health_check_itself_goes_without_the_token(served, path):
    answer = httpx.get(f"{served()}{path}")
    assert answer.status_code == 401, path


def test_a_websocket_is_refused(shop_config):
    application = serving.app(build_server(shop_config()), path="/mcp", token=TOKEN, host="127.0.0.1")
    with TestClient(application) as client, pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/mcp", headers={"Authorization": f"Bearer {TOKEN}"}):
            pass
    assert closed.value.code == 1008


def test_the_health_check_needs_no_token_and_reaches_no_database(served, shop_path):
    url = served()
    shop_path.unlink()  # the database gone: the server is still up — a readiness probe must not restart it
    answer = httpx.get(f"{url}/health")
    assert answer.status_code == 200 and answer.json()["status"] == "ok"


async def test_on_loopback_a_token_may_be_left_out(served):
    async with connect(served(token=None), None) as client:
        assert (await client.call_tool("about", {})).content


def test_a_server_others_reach_is_never_served_without_a_token(shop_config, capsys, monkeypatch):
    config = shop_config()
    monkeypatch.setattr(serving.uvicorn, "run", lambda *args, **kwargs: pytest.fail("served without a token"))
    with pytest.raises(SystemExit) as stopped:
        main(["--config", config.source, "--http", "--host", "0.0.0.0"])
    assert stopped.value.code == 2 and "reachable from other machines" in capsys.readouterr().err
    monkeypatch.delenv("SWM_SERVING_TOKEN", raising=False)
    with pytest.raises(SystemExit) as stopped:
        main(["--config", config.source, "--http", "--host", "0.0.0.0", "--token-env", "SWM_SERVING_TOKEN"])
    assert stopped.value.code == 2 and "the variable SWM_SERVING_TOKEN is not set" in capsys.readouterr().err


def test_a_token_written_for_its_variable_is_never_repeated(shop_config, capsys, monkeypatch):
    config = shop_config()
    pasted = "sk_live_aB3dE5fG7hJ9kL1mN3p"
    monkeypatch.setattr(serving.uvicorn, "run", lambda *args, **kwargs: pytest.fail("served without a token"))
    with pytest.raises(SystemExit) as stopped:
        main(["--config", config.source, "--http", "--host", "0.0.0.0", "--token-env", pasted])
    said = capsys.readouterr().err
    assert stopped.value.code == 2 and "the variable named in --token-env (not repeated" in said
    assert pasted not in said


def test_a_path_that_is_not_one_is_refused():
    with pytest.raises(serving.HttpProblem, match="a path starting with /"):
        serving.serve(build_server(), host="127.0.0.1", port=1, path="/health", token_env=None)
