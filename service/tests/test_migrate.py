"""Loading project records into SQLite: from Supabase, from a daily backup, from a file."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from service import migrate
from service.projects import EXAMPLE_ID, Projects
from service.store import MemoryBlobStore, MemoryMetaStore
from service.store.sqlite import SqliteMetaStore

KEY = "service-role-key"


@pytest.fixture
def old():
    """A service with some history, standing in for the one that ran on Supabase."""
    projects = Projects(MemoryMetaStore(), MemoryBlobStore())
    projects.seed_example()
    atlas = projects.duplicate(EXAMPLE_ID, "Atlas")["id"]
    tax = [{"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.3}}]
    projects.apply(atlas, tax, expected_version=1)
    projects.run(atlas, outputs=["equity_irr"], save=True)
    gone = projects.duplicate(EXAMPLE_ID, "Gone")["id"]
    projects.delete(gone)
    projects.atlas = atlas
    return projects


@pytest.fixture
def supabase(old):
    """PostgREST, as far as the migration uses it: paged reads of the three tables."""
    dump = old.meta.dump()
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the base class's name
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            table = url.path.removeprefix("/rest/v1/")
            seen.append({"table": table, **query})
            authorised = (
                self.headers.get("apikey") == KEY
                and self.headers.get("Authorization") == f"Bearer {KEY}"
            )
            if not authorised or table not in dump:
                self.send_response(401 if not authorised else 404)
                self.end_headers()
                return
            keys = query["order"].split(",")
            rows = sorted(dump[table], key=lambda r: [r[k] for k in keys])
            start = int(query["offset"])
            page = rows[start : start + min(int(query["limit"]), 2)]  # the server caps pages
            body = json.dumps(page).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", seen
    server.shutdown()


def env(tmp_path, **extra) -> dict[str, str]:
    return {"EPM_DATA": str(tmp_path / "data"), **extra}


def test_supabase_records_arrive_in_sqlite_and_the_service_runs_on_them(
    old, supabase, tmp_path, capsys
):
    url, seen = supabase
    settings = env(tmp_path, SUPABASE_URL=url + "/", SUPABASE_SERVICE_ROLE_KEY=KEY)
    assert migrate.main(["supabase"], settings) == 0
    assert "loaded Supabase" in capsys.readouterr().out
    assert {r["table"] for r in seen} == {"projects", "versions", "outputs"}
    assert max(int(r["offset"]) for r in seen) >= 2  # it read past the first page

    meta = SqliteMetaStore(tmp_path / "data" / "epm.db")
    expected = old.meta.dump()
    assert {t: len(rows) for t, rows in meta.dump().items()} == {
        t: len(rows) for t, rows in expected.items()
    }
    # the same files, so the new service opens the old projects, history and outputs
    moved = Projects(meta, old.blobs)
    moved.seed_example()
    assert [p["name"] for p in moved.list_projects()] == [p["name"] for p in old.list_projects()]
    assert [p["name"] for p in moved.list_projects(None, True)] == ["Gone"]
    assert moved.show(old.atlas, "tax_rate")["value"] == 0.3
    assert [v["number"] for v in moved.list_versions(old.atlas)] == [2, 1]
    assert len(moved.list_outputs(old.atlas)) == 1
    again = moved.apply(
        old.atlas,
        [{"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.25}}],
        expected_version=2,
    )
    assert again["committed"] and again["version"] == 3
    meta.close()


def test_supabase_needs_its_address_and_a_key_that_works(supabase, tmp_path, capsys):
    url, _ = supabase
    assert migrate.main(["supabase"], env(tmp_path)) == 1
    assert "SUPABASE_URL" in capsys.readouterr().err
    wrong = env(tmp_path, SUPABASE_URL=url, SUPABASE_SERVICE_ROLE_KEY="wrong")
    assert migrate.main(["supabase"], wrong) == 1
    assert "401" in capsys.readouterr().err
    assert not (tmp_path / "data" / "epm.db").exists()  # nothing was created on failure


def test_existing_projects_are_kept_unless_replaced(old, tmp_path, capsys):
    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps(old.meta.dump(), default=str))
    settings = env(tmp_path)

    # a database with only the example (a service that was started once) is fine to fill
    started = Projects(SqliteMetaStore(settings["EPM_DATA"] + "/epm.db"), MemoryBlobStore())
    started.seed_example()
    started.meta.close()
    assert migrate.main(["file", str(dump)], settings) == 0

    assert migrate.main(["file", str(dump)], settings) == 1  # now it holds Atlas
    assert "--replace" in capsys.readouterr().err
    assert migrate.main(["--replace", "file", str(dump)], settings) == 0


def test_the_newest_daily_backup_restores_the_records(old, tmp_path, monkeypatch, capsys):
    old.maintain(datetime(2026, 3, 1, tzinfo=UTC))
    old.rename(old.atlas, "Atlas II")
    old.maintain(datetime(2026, 3, 2, tzinfo=UTC))
    monkeypatch.setattr(migrate, "_blobs", lambda settings: old.blobs)

    assert migrate.main(["--check-files", "backup"], env(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "backups/meta/2026-03-02.json" in out and "every file is in the bucket" in out
    meta = SqliteMetaStore(tmp_path / "data" / "epm.db")
    assert meta.get_project(old.atlas).name == "Atlas II"
    meta.close()

    assert migrate.main(["--replace", "backup", "2026-03-01"], env(tmp_path)) == 0
    meta = SqliteMetaStore(tmp_path / "data" / "epm.db")
    assert meta.get_project(old.atlas).name == "Atlas"
    meta.close()
    assert migrate.main(["--replace", "backup", "2020-01-01"], env(tmp_path)) == 1
    assert "2020-01-01" in capsys.readouterr().err


def test_missing_files_are_reported(old, tmp_path, monkeypatch, capsys):
    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps(old.meta.dump(), default=str))
    lost = old.meta.get_version(old.atlas, 1).blob_key
    old.blobs.delete(lost)
    monkeypatch.setattr(migrate, "_blobs", lambda settings: old.blobs)
    assert migrate.main(["--check-files", "file", str(dump)], env(tmp_path)) == 1
    assert lost in capsys.readouterr().out


def test_a_dump_that_does_not_hang_together_is_refused(tmp_path, capsys):
    settings = env(tmp_path)
    for name, content in {
        "not-a-dump.json": {"projects": []},
        "orphan.json": {
            "projects": [],
            "versions": [{"project_id": "ghost", "number": 1}],
            "outputs": [],
        },
        "no-current.json": {
            "projects": [{"id": "p", "current_version": 3}],
            "versions": [{"project_id": "p", "number": 1}],
            "outputs": [],
        },
    }.items():
        path = tmp_path / name
        path.write_text(json.dumps(content))
        assert migrate.main(["file", str(path)], settings) == 1, name
    assert migrate.main(["file", str(tmp_path / "absent.json")], settings) == 1
    assert capsys.readouterr().err.count("FAILED") == 4
