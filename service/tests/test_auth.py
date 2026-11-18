"""The service behind its access token: reachable only at URLs that carry it."""

from __future__ import annotations

import asyncio
import logging
import urllib.request

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from service import config
from service.auth import HideToken, PathToken, check_token
from service.projects import EXAMPLE_ID
from service.service import create_app, gated, main
from service.store import MemoryBlobStore, MemoryMetaStore

from .conftest import TOKEN


@pytest.fixture
def client():
    app = create_app(MemoryMetaStore(), MemoryBlobStore(), token=TOKEN)
    with TestClient(gated(app, TOKEN)) as c:
        yield c


def test_the_api_answers_only_under_the_token(client):
    assert client.get(f"/{TOKEN}/api/health").json() == {"ok": True}
    ids = [p["id"] for p in client.get(f"/{TOKEN}/api/projects").json()]
    assert ids == [EXAMPLE_ID]
    for path in (
        "/",
        "/api/health",
        "/api/projects",
        "/mcp",
        f"/{TOKEN[:-1]}/api/health",
        f"/{TOKEN}x/api/health",
        f"/x/{TOKEN}/api/health",
        f"/api/{TOKEN}/health",
        "/mcp/wrong",
    ):
        response = client.get(path)
        # never 401: MCP clients would start an OAuth flow
        assert response.status_code == 404 and response.content == b"", path
        assert "www-authenticate" not in response.headers


def test_browsers_can_preflight_and_call_the_api(client):
    preflight = client.options(
        f"/{TOKEN}/api/projects",
        headers={"Origin": "https://app.example", "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"


def test_a_wrong_method_under_the_token_is_not_a_404(client):
    assert client.put(f"/{TOKEN}/api/health").status_code == 405


@pytest.mark.parametrize("path", ["/{token}/mcp", "/mcp/{token}", "/mcp/{token}/"])
def test_mcp_answers_at_both_token_urls(client, path):
    response = client.post(
        path.format(token=TOKEN),
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 200
    names = {t["name"] for t in response.json()["result"]["tools"]}
    assert {"apply_batch", "list_projects"} <= names


def test_links_in_mcp_results_carry_the_token(deployed_url):
    async def export() -> str:
        async with Client(f"{deployed_url}/mcp/{TOKEN}") as c:
            result = await c.call_tool("export_project", {"project_id": EXAMPLE_ID})
            return result.structured_content["download_url"]

    url = asyncio.run(export())
    assert url == f"{deployed_url}/{TOKEN}/api/projects/{EXAMPLE_ID}/export"
    with urllib.request.urlopen(url, timeout=30) as response:
        assert response.read()[:2] == b"PK"


def test_tokens_must_be_long_and_url_safe():
    assert check_token(TOKEN) == TOKEN
    for bad in ("", "short", "a" * 31, "a" * 40 + "/", "a" * 40 + " "):
        with pytest.raises(ValueError):
            check_token(bad)
    with pytest.raises(ValueError):
        PathToken(lambda *a: None, "short")


def test_the_token_is_masked_in_log_lines():
    hide = HideToken(TOKEN)
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", f"/{TOKEN}/api/projects?q=a", "1.1", 200),
        None,
    )
    assert hide.filter(record)
    assert TOKEN not in record.getMessage() and "/***/api/projects?q=a" in record.getMessage()
    plain = logging.LogRecord("x", logging.INFO, __file__, 1, f"at /mcp/{TOKEN}", None, None)
    hide.filter(plain)
    assert plain.getMessage() == "at /mcp/***"


def test_settings_come_from_the_environment():
    settings = config.load({"API_TOKEN": f" {TOKEN} ", "R2_ACCOUNT_ID": "acc", "EPM_DATA": "/d"})
    assert settings.api_token == TOKEN and settings.r2_bucket == "epm"
    assert str(settings.database) == "/d/epm.db"
    assert settings.missing() == ["R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"]
    assert config.load({}).missing()[0] == "API_TOKEN"


def test_the_service_refuses_to_start_on_real_storage_without_its_settings(monkeypatch):
    for name in ("API_TOKEN", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as stopped:
        main([])
    assert "API_TOKEN" in str(stopped.value) and "R2_SECRET_ACCESS_KEY" in str(stopped.value)
    monkeypatch.setenv("API_TOKEN", "too-short")
    with pytest.raises(SystemExit) as stopped:
        main(["--memory"])
    assert "32" in str(stopped.value)
