from __future__ import annotations

from service.projects import EXAMPLE_ID

TAX = {"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.3}}


def copy_example(client, name="Atlas") -> str:
    r = client.post("/api/projects", json={"name": name, "from": EXAMPLE_ID})
    assert r.status_code == 201
    return r.json()["id"]


def test_health_and_listing(client):
    assert client.get("/api/health").json() == {"ok": True}
    projects = client.get("/api/projects").json()
    assert [p["id"] for p in projects] == [EXAMPLE_ID]
    assert any(b["name"] == "irr" for b in client.get("/api/builtins").json())


def test_project_lifecycle(client):
    pid = copy_example(client)
    assert client.get("/api/projects?q=atl").json()[0]["id"] == pid
    assert client.patch(f"/api/projects/{pid}", json={"name": "Atlas 2"}).json()["name"] == (
        "Atlas 2"
    )
    dup = client.post(f"/api/projects/{pid}/duplicate", json={"name": "Copy"})
    assert dup.status_code == 201 and dup.json()["name"] == "Copy"
    assert client.delete(f"/api/projects/{pid}").json()["deleted_at"]
    r = client.get(f"/api/projects/{pid}")
    assert r.status_code == 404 and r.json()["errors"][0]["code"] == "unknown_project"
    assert client.post(f"/api/projects/{pid}/restore").json()["deleted_at"] is None


def test_state_matches_the_local_ui_shape(client):
    state = client.get(f"/api/projects/{EXAMPLE_ID}/state").json()
    for key in ("model", "metrics", "checks", "diagnostics", "files", "builtins", "functions"):
        assert key in state
    assert state["version"] == 1 and state["read_only"] and state["project"]["is_example"]
    assert state["metrics"]["equity_irr"]["value"]["value"] > 0


def test_reading_endpoints(client):
    base = f"/api/projects/{EXAMPLE_ID}"
    assert client.get(f"{base}/show/tax_rate").json()["value"] == 0.25
    metrics = client.get(f"{base}/objects", params={"kind": "metrics", "search": "tax"}).json()
    assert "tax_rate" in [m["name"] for m in metrics]
    trace = client.get(f"{base}/trace/ebit", params={"direction": "upstream", "depth": 1}).json()
    assert "ebitda" in trace["metrics"]
    assert "diagnostics" in client.get(f"{base}/validate").json()
    assert client.get(f"{base}/tables/returns").json()["rows"]
    preview = client.get(f"{base}/sources/is/preview").json()
    assert preview["items"]
    r = client.get(f"{base}/show/tax_rat")
    assert r.status_code == 404 and r.json()["errors"][0]["code"] == "not_found"


def test_example_writes_are_forbidden(client):
    base = f"/api/projects/{EXAMPLE_ID}"
    r = client.post(f"{base}/batches", json={"ops": [TAX]})
    assert r.status_code == 403 and r.json()["errors"][0]["code"] == "read_only_project"
    assert client.post(f"{base}/batches", json={"ops": [TAX], "mode": "dry_run"}).json()["ok"]
    assert client.patch(base, json={"name": "x"}).status_code == 403
    assert client.delete(base).status_code == 403
    assert client.put(f"{base}/files/x.csv", content=b"a").status_code == 403
    assert client.post(f"{base}/undo").status_code == 403


def test_edits_versions_and_conflicts(client):
    pid = copy_example(client)
    base = f"/api/projects/{pid}"
    r = client.post(
        f"{base}/batches",
        json={"ops": [TAX], "expected_version": 1},
        headers={"X-EPM-Client": "web"},
    )
    assert r.json()["committed"] and r.json()["version"] == 2
    stale = client.post(f"{base}/batches", json={"ops": [TAX], "expected_version": 1})
    assert stale.status_code == 409
    assert stale.json()["errors"][0]["current_version"] == 2
    versions = client.get(f"{base}/versions").json()
    assert [(v["number"], v["source"]) for v in versions] == [(2, "web"), (1, "duplicate")]
    assert client.post(f"{base}/undo").json()["restored"] == 1
    assert (
        client.post(f"{base}/versions/2/restore", json={"expected_version": 3}).json()["version"][
            "number"
        ]
        == 4
    )
    old = client.get(f"{base}/versions/1/export")
    assert old.status_code == 200 and old.content[:2] == b"PK"
    assert "filename*=UTF-8''atlas-v1.epm" in old.headers["content-disposition"]


def test_model_and_functions_editors(client):
    pid = copy_example(client)
    base = f"/api/projects/{pid}"
    state = client.get(f"{base}/state").json()
    model = state["model"]
    model["metrics"]["tax_rate"]["value"] = 0.3
    assert client.put(f"{base}/model", json={"model": model, "mode": "dry_run"}).json()["ok"]
    assert client.put(f"{base}/model", json={"model": model}).json()["version"] == 2
    code = state["functions_source"] + "\n# note\n"
    assert client.put(f"{base}/functions", json={"code": code}).json()["version"] == 3


def test_import_export_and_files(client):
    pid = copy_example(client)
    base = f"/api/projects/{pid}"
    exported = client.get(f"{base}/export")
    assert exported.headers["content-type"] == "application/zip"
    imported = client.post("/api/projects/import?name=Again", content=exported.content)
    assert imported.status_code == 201 and imported.json()["name"] == "Again"
    bad = client.post("/api/projects/import", content=b"nope")
    assert bad.status_code == 400 and bad.json()["errors"][0]["code"] == "invalid_package"

    assert client.put(f"{base}/files/extra.csv", content=b"item,2024\nx,1\n").json()["committed"]
    names = [f["name"] for f in client.get(f"{base}/files").json()]
    assert "inputs/extra.csv" in names
    assert client.get(f"{base}/files/inputs/extra.csv").content == b"item,2024\nx,1\n"
    assert client.get(f"{base}/files/extra.csv?version=1").status_code == 404
    assert client.delete(f"{base}/files/extra.csv").json()["committed"]
    assert client.get(f"{base}/files/extra.csv").status_code == 404


def test_runs_and_outputs(client):
    pid = copy_example(client)
    base = f"/api/projects/{pid}"
    run = client.post(
        f"{base}/run", json={"overrides": {"interest_rate": 0.06}, "outputs": ["equity_irr"]}
    ).json()
    assert set(run["values"]) == {"equity_irr"}
    sweep = client.post(
        f"{base}/sweep",
        json={"inputs": {"tax_rate": [0.2, 0.3]}, "outputs": ["equity_irr"], "save": True},
    ).json()
    assert len(sweep["rows"]) == 2 and sweep["output"]["kind"] == "sweep"
    analysis = client.post(
        f"{base}/analyses/entry_exit_irr/run", json={"save": True, "plot": True}
    ).json()
    plot_id = analysis["outputs"][1]["id"]
    png = client.get(f"{base}/outputs/{plot_id}")
    assert png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"
    assert len(client.get(f"{base}/outputs").json()) == 3
    assert client.get(f"{base}/analyses/entry_exit_irr/plot.png").content[:4] == b"\x89PNG"
    assert client.delete(f"{base}/outputs/{plot_id}").json() == {"ok": True}
    assert client.get(f"{base}/outputs/{plot_id}").status_code == 404


def test_cors_allows_the_web_app(client):
    r = client.options(
        "/api/projects",
        headers={"Origin": "https://epm.example.app", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] in (
        "*",
        "https://epm.example.app",
    )
