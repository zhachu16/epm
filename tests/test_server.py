import json
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from epm import Workspace
from epm.cli.routing import forward, split_global
from epm.server import Session, create_app
from epm.server.lockfile import live_session, lock_path, read_lock, remove_lock, write_lock

LBO = Path(__file__).parents[1] / "examples" / "lbo"


@pytest.fixture
def model(tmp_path) -> Path:
    target = tmp_path / "lbo"
    shutil.copytree(LBO, target, ignore=shutil.ignore_patterns("cache", ".epm-session.lock"))
    return target


@pytest.fixture
def session(model):
    s = Session(model)
    yield s
    s.close(save_cache=False)


@pytest.fixture
def client(session):
    # the test client's websockets always send Host: testserver
    app = create_app(session, allowed_hosts={"testserver"})
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["X-EPM-Token"] = session.token
        yield c


def on_disk(model: Path) -> Workspace:
    return Workspace.open(model)


def test_access_control(session):
    app = create_app(session)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.get("/api/health").json()["model"] == str(session.path)
        assert c.get("/api/state").status_code == 401
        assert c.get("/api/state", headers={"X-EPM-Token": "nope"}).status_code == 401
        assert c.get("/api/state", headers={"X-EPM-Token": session.token}).status_code == 200
    with TestClient(app, base_url="http://evil.example") as c:
        assert c.get("/api/health").status_code == 403


def test_state_snapshot(client):
    state = client.get("/api/state").json()
    assert state["name"] == "Project Atlas LBO"
    irr = state["metrics"]["equity_irr"]
    assert irr["value"]["value"] == pytest.approx(0.1735, abs=1e-4)
    assert irr["unit"] == "%" and irr["status"]["state"] == "ok"
    tlb = state["metrics"]["tlb_close"]
    assert tlb["scope"] == "forecast" and tlb["circularity"] == "average_balance_interest"
    assert tlb["value"]["values"]["2022"] is None
    assert state["metrics"]["ebit"]["deps"] == {"ebitda": 0, "depreciation_amortization": 0}
    assert state["metrics"]["revenue"]["group"] == "income_statement"
    assert all(c["state"] == "passed" for c in state["checks"].values())
    assert {"name": "prev"}.items() <= next(
        b for b in state["builtins"] if b["name"] == "prev"
    ).items()
    assert state["functions"][0]["name"] == "amortization_schedule"


def test_batches_history_and_persistence(client, model):
    edit = {"ops": [{"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.3}}]}
    preview = client.post("/api/batches", json={**edit, "mode": "dry_run"}).json()
    assert preview["ok"] and not preview["committed"]
    assert preview["impact"]["outputs"]["equity_irr"]["delta"] < 0
    assert on_disk(model).model.metrics["tax_rate"].value == 0.25

    committed = client.post("/api/batches", json=edit).json()
    assert committed["committed"]
    assert on_disk(model).model.metrics["tax_rate"].value == 0.3  # saved straight away
    state = client.get("/api/state").json()
    assert [h["id"] for h in state["history"]] == [committed["id"]]

    client.post("/api/undo")
    assert on_disk(model).model.metrics["tax_rate"].value == 0.25
    client.post("/api/redo")
    assert on_disk(model).model.metrics["tax_rate"].value == 0.3
    assert client.post(f"/api/rollback/{committed['id']}").json() == {"undone": []}

    bad = client.post(
        "/api/batches", json={"ops": [{"op": "delete", "kind": "metric", "name": "tax_rate"}]}
    ).json()
    assert not bad["ok"] and bad["errors"][0]["code"] == "in_use"
    r = client.post("/api/undo")
    r = client.post("/api/undo")
    assert r.status_code == 400 and r.json()["errors"][0]["code"] == "nothing_to_undo"


def test_pending_proposals(client):
    edit = {
        "ops": [{"op": "update", "kind": "metric", "name": "exit_multiple", "set": {"value": 10}}],
        "mode": "propose",
    }
    proposed = client.post("/api/batches", json=edit).json()
    state = client.get("/api/state").json()
    assert [p["id"] for p in state["pending"]] == [proposed["id"]]
    assert state["pending"][0]["preview"]["impact"]["outputs"]["equity_irr"]["delta"] > 0
    result = client.post(f"/api/pending/{proposed['id']}/commit").json()
    assert result["committed"]
    assert client.get("/api/state").json()["pending"] == []
    other = client.post("/api/batches", json=edit).json()
    assert client.post(f"/api/pending/{other['id']}/reject").json() == {"ok": True}


def test_text_edits_of_model_and_functions(client, model):
    state = client.get("/api/state").json()
    edited = json.loads(json.dumps(state["model"]))
    edited["metrics"]["tax_rate"]["value"] = 0.2
    edited["metrics"]["new_metric"] = {"formula": "ebitda * 2"}
    r = client.put("/api/model", json={"model": edited}).json()
    assert r["committed"], r
    assert {c["name"] for c in r["changes"]} == {"tax_rate", "new_metric"}
    del edited["metrics"]["ebitda"]
    r = client.put("/api/model", json={"model": edited}).json()
    assert not r["ok"] and r["errors"][0]["code"] == "in_use"

    code = state["functions_source"].replace("principal * annual_pct", "principal * annual_pct * 2")
    r = client.put("/api/functions", json={"code": code}).json()
    assert r["committed"]
    assert "* 2" in (model / "functions.py").read_text()
    after = client.get("/api/state").json()
    assert after["metrics"]["tlb_amortization"]["value"]["values"]["2025"] == pytest.approx(18.4)
    r = client.put("/api/functions", json={"code": "def broken(:"}).json()
    assert not r["ok"] and "syntax error" in r["errors"][0]["message"]


def test_calculation_endpoints(client):
    run = client.post(
        "/api/run", json={"overrides": {"interest_rate": 0.08}, "outputs": ["equity_irr"]}
    ).json()
    assert run["values"]["equity_irr"]["value"] < 0.1735
    sweep = client.post(
        "/api/sweep", json={"inputs": {"exit_multiple": [8, 10]}, "outputs": ["moic"]}
    ).json()
    assert len(sweep["rows"]) == 2
    analysis = client.post("/api/analyses/entry_exit_irr/run").json()
    assert analysis["chart"]["kind"] == "heatmap" and len(analysis["sweep"]["rows"]) == 25
    preview = client.get("/api/sources/is/preview").json()
    assert preview["periods"] == ["2022", "2023", "2024"]
    assert preview["rows"][0] == {"item": "Revenue", "values": [820.0, 900.0, 1000.0]}
    assert client.get("/api/trace/ebit?direction=upstream&depth=1").json()["metrics"] == [
        "ebitda",
        "depreciation_amortization",
    ]
    assert client.get("/api/sources/nope/preview").status_code == 400


def test_events_and_terminal(client, session):
    with client.websocket_connect(f"/api/events?token={session.token}") as events:
        assert events.receive_json()["type"] == "hello"
        client.post(
            "/api/batches",
            json={
                "ops": [
                    {"op": "update", "kind": "metric", "name": "minimum_cash", "set": {"value": 35}}
                ]
            },
        )
        seen = []
        for _ in range(10):  # bounded, so a missing event fails instead of hanging
            seen.append(events.receive_json())
            if seen[-1]["type"] == "model_changed":
                break
        changed = next(e for e in seen if e["type"] == "model_changed")
        assert changed["source"] == "ui" and changed["version"] == session.ws.version

    with client.websocket_connect(f"/api/terminal?token={session.token}") as term:
        term.send_json({"command": "fm show minimum_cash"})
        out = term.receive_json()
        assert out["exit_code"] == 0 and "metric minimum_cash" in out["stdout"]
        term.send_json({"command": "undo"})
        assert "undid" in term.receive_json()["stdout"]
        term.send_json({"command": "serve"})
        assert term.receive_json()["exit_code"] == 2
        term.send_json({"command": "metric delete tax_rate"})
        result = term.receive_json()
        assert result["exit_code"] == 1 and "still used" in result["stderr"]
        term.send_json({"command": "--help"})
        assert "Usage" in term.receive_json()["stdout"]

    rejected = pytest.raises(Exception)  # noqa: B017 - starlette raises on a rejected socket
    with rejected, client.websocket_connect("/api/events?token=wrong") as bad:
        bad.receive_json()


def test_outside_edits_are_reloaded(session, model):
    other = Workspace.open(model)
    other.apply([{"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.22}}])
    time.sleep(0.01)
    other.save()
    assert session.check_disk()
    assert session.ws.model.metrics["tax_rate"].value == 0.22
    assert session.ws.history == []
    assert not session.check_disk()  # nothing new


# -- CLI forwarding against a real server --------------------------------------------------


class ServerThread:
    def __init__(self, model: Path):
        self.session = Session(model)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        app = create_app(self.session)
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True
        )

    def __enter__(self):
        self.thread.start()
        while not self.server.started:
            time.sleep(0.05)
        write_lock(self.session.path, "127.0.0.1", self.port, self.session.token)
        return self

    def __exit__(self, *exc):
        remove_lock(self.session.path, self.session.token)
        self.server.should_exit = True
        self.thread.join(10)
        self.session.close(save_cache=False)


def test_cli_commands_run_in_the_session(model):
    argv = ["-m", str(model), "--json"]
    assert forward([*argv, "list"]) is None  # no server: run locally
    with ServerThread(model) as srv:
        assert live_session(model)["port"] == srv.port
        r = forward([*argv, "metric", "set", "tax_rate", "--value", "0.3"])
        assert r["exit_code"] == 0 and json.loads(r["stdout"])["data"]["committed"]
        r = forward([*argv, "history"])
        assert len(json.loads(r["stdout"])["data"]["history"]) == 1
        r = forward([*argv, "apply", "-", "--propose"])  # stdin is not forwarded: empty batch
        assert r["exit_code"] == 1
        r = forward([*argv, "undo"])
        assert r["exit_code"] == 0
        assert on_disk(model).model.metrics["tax_rate"].value == 0.25
        assert forward([*argv, "metric", "--help"]) is None  # help is local
        assert forward([*argv, "pack", str(model), "x.epm"]) is None
    assert live_session(model) is None
    assert not lock_path(model).exists()


def test_lock_helpers(model):
    assert read_lock(model) is None and live_session(model) is None
    write_lock(model, "127.0.0.1", 1, "secret")
    assert (lock_path(model).stat().st_mode & 0o777) == 0o600
    assert live_session(model) is None  # nothing listens there
    remove_lock(model, "other-token")
    assert lock_path(model).exists()
    remove_lock(model, "secret")
    assert not lock_path(model).exists()
    assert split_global(["-m", "x.epm", "--json", "run", "-o", "a"]) == (
        "x.epm",
        ["run", "-o", "a"],
    )
    assert split_global(["--model=y", "list"]) == ("y", ["list"])
