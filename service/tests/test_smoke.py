from __future__ import annotations

import json
import urllib.request

from service import smoke

from .conftest import TOKEN


def test_smoke_check_passes_and_cleans_up(service_url):
    said: list[str] = []
    smoke.run(service_url, say=said.append)
    assert said[-1].startswith("ok  deleted")
    with urllib.request.urlopen(f"{service_url}/api/projects") as response:
        assert [p["id"] for p in json.load(response)] == ["example-lbo"]


def test_smoke_check_passes_on_a_deployed_service(deployed_url):
    """Records in SQLite, reached through the access token, under real concurrent HTTP."""
    said: list[str] = []
    smoke.run(f"{deployed_url}/{TOKEN}", say=said.append)
    assert said[-1].startswith("ok  deleted") and "ok  MCP sees the saved edit" in said
    assert smoke.main([deployed_url]) == 1  # without the token there is nothing there


def test_smoke_check_reports_failures():
    assert smoke.main(["http://127.0.0.1:9"]) == 1
    assert smoke.main([]) == 2
