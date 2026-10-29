from __future__ import annotations

import json
import urllib.request

from service import smoke


def test_smoke_check_passes_and_cleans_up(service_url):
    said: list[str] = []
    smoke.run(service_url, say=said.append)
    assert said[-1].startswith("ok  deleted")
    with urllib.request.urlopen(f"{service_url}/api/projects") as response:
        assert [p["id"] for p in json.load(response)] == ["example-lbo"]


def test_smoke_check_reports_failures():
    assert smoke.main(["http://127.0.0.1:9"]) == 1
    assert smoke.main([]) == 2
