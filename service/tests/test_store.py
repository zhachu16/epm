from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from service.store import (
    MemoryBlobStore,
    MemoryMetaStore,
    OutputRecord,
    ProjectRecord,
    VersionRecord,
)
from service.store.sqlite import SqliteMetaStore


def version(project_id: str, number: int) -> VersionRecord:
    return VersionRecord(project_id=project_id, number=number, blob_key=f"k{number}")


def output(project_id: str, output_id: str) -> OutputRecord:
    return OutputRecord(
        id=output_id,
        project_id=project_id,
        version=1,
        kind="run",
        name=f"{output_id}.json",
        blob_key=f"o/{output_id}",
        content_type="application/json",
        bytes=2,
    )


@pytest.fixture(params=["memory", "sqlite"])
def meta(request, tmp_path):
    """Each record store: they must behave alike."""
    if request.param == "memory":
        yield MemoryMetaStore()
    else:
        store = SqliteMetaStore(tmp_path / "data" / "epm.db")
        yield store
        store.close()


def test_commit_version_only_on_top_of_the_expected_one(meta):
    meta.create_project(ProjectRecord(id="p", name="P"), version("p", 1))
    assert meta.commit_version(version("p", 2), expected=1)
    assert not meta.commit_version(version("p", 3), expected=1)  # someone saved in between
    assert not meta.commit_version(version("p", 2), expected=2)  # that number is taken
    assert not meta.commit_version(version("nobody", 2), expected=1)
    assert meta.get_project("p").current_version == 2
    assert [v.number for v in meta.list_versions("p")] == [2, 1]
    assert meta.get_version("p", 2).blob_key == "k2" and meta.get_version("p", 3) is None


def test_a_project_is_created_with_its_first_version(meta):
    created = meta.create_project(
        ProjectRecord(id="p", name="P", description="d", is_example=True), version("p", 1)
    )
    assert created.current_version == 1 and created.is_example is True
    assert created.created_at == created.updated_at and created.created_at.tzinfo is not None
    assert created.deleted_at is None
    assert meta.get_version("p", 1).created_at is not None
    assert meta.get_project("missing") is None
    with pytest.raises(ValueError):
        meta.create_project(ProjectRecord(id="p", name="again"), version("p", 1))
    assert meta.get_project("p").name == "P"


def test_list_searches_names_and_descriptions(meta):
    meta.create_project(ProjectRecord(id="a", name="Atlas LBO"), version("a", 1))
    meta.create_project(
        ProjectRecord(id="b", name="Budget", description="for ATLAS"), version("b", 1)
    )
    meta.create_project(ProjectRecord(id="c", name="Other"), version("c", 1))
    meta.create_project(ProjectRecord(id="d", name="Überblick"), version("d", 1))
    assert {p.id for p in meta.list_projects("atlas")} == {"a", "b"}
    assert [p.id for p in meta.list_projects("überblick")] == ["d"]  # not only ASCII
    meta.update_project("a", deleted_at=datetime.now(UTC))
    assert {p.id for p in meta.list_projects("atlas")} == {"b"}
    assert [p.id for p in meta.list_projects(deleted=True)] == ["a"]
    meta.update_project("c", name="Other, renamed")
    assert [p.id for p in meta.list_projects()] == ["c", "d", "b"]  # most recently changed first
    assert [p.id for p in meta.list_projects(limit=1)] == ["c"]
    restored = meta.update_project("a", deleted_at=None)
    assert restored.deleted_at is None and len(meta.list_projects()) == 4


def test_updating_a_missing_project_is_an_error(meta):
    with pytest.raises(KeyError):
        meta.update_project("missing", name="x")


def test_records_returned_are_copies(meta):
    p = meta.create_project(ProjectRecord(id="p", name="P"), version("p", 1))
    p.name = "changed"
    assert meta.get_project("p").name == "P"


def test_versions_and_outputs_are_deleted_and_purged(meta):
    meta.create_project(ProjectRecord(id="p", name="P"), version("p", 1))
    meta.create_project(ProjectRecord(id="q", name="Q"), version("q", 1))
    for n in range(2, 6):
        assert meta.commit_version(version("p", n), expected=n - 1)
    meta.delete_versions("p", [2, 3, 99])
    assert [v.number for v in meta.list_versions("p")] == [5, 4, 1]

    first = meta.add_output(output("p", "o1"))
    meta.add_output(output("p", "o2"))
    meta.add_output(output("q", "o3"))
    assert first.created_at is not None and first.bytes == 2
    assert [o.id for o in meta.list_outputs("p")] == ["o2", "o1"]  # newest first
    assert meta.get_output("p", "o1").name == "o1.json"
    assert meta.get_output("q", "o1") is None  # another project's output
    meta.delete_outputs("p", ["o1"])
    assert [o.id for o in meta.list_outputs("p")] == ["o2"]

    meta.purge_project("p")
    assert meta.get_project("p") is None
    assert meta.list_versions("p") == [] and meta.list_outputs("p") == []
    assert [v.number for v in meta.list_versions("q")] == [1]
    assert [o.id for o in meta.list_outputs("q")] == ["o3"]


def test_dump_lists_every_row(meta):
    meta.create_project(ProjectRecord(id="p", name="P", is_example=True), version("p", 1))
    meta.commit_version(version("p", 2), expected=1)
    meta.add_output(output("p", "o1"))
    dump = meta.dump()
    assert {t: len(rows) for t, rows in dump.items()} == {
        "projects": 1,
        "versions": 2,
        "outputs": 1,
    }
    assert dump["projects"][0]["is_example"] is True
    assert isinstance(dump["versions"][0]["created_at"], str)


# -- SQLite only -----------------------------------------------------------------------


def test_sqlite_records_outlive_the_process(tmp_path):
    path = tmp_path / "epm.db"
    first = SqliteMetaStore(path)
    first.create_project(ProjectRecord(id="p", name="P"), version("p", 1))
    first.commit_version(version("p", 2), expected=1)
    saved = first.get_project("p")
    first.close()

    again = SqliteMetaStore(path)  # opening an existing database changes nothing
    assert again.get_project("p") == saved
    assert [v.number for v in again.list_versions("p")] == [2, 1]
    again.close()
    with sqlite3.connect(path) as db:
        assert db.execute("pragma journal_mode").fetchone() == ("wal",)


def test_sqlite_refuses_fields_that_are_not_editable(tmp_path):
    meta = SqliteMetaStore(tmp_path / "epm.db")
    meta.create_project(ProjectRecord(id="p", name="P"), version("p", 1))
    with pytest.raises(ValueError):
        meta.update_project("p", current_version=9)
    with pytest.raises(ValueError):
        meta.update_project("p", **{"name = 'x' --": 1})


def test_sqlite_loads_a_dump_from_either_store(tmp_path):
    source = SqliteMetaStore(tmp_path / "a.db")
    source.create_project(ProjectRecord(id="p", name="P", is_example=True), version("p", 1))
    source.commit_version(version("p", 2), expected=1)
    source.add_output(output("p", "o1"))
    source.update_project("p", deleted_at=datetime(2026, 1, 2, tzinfo=UTC))
    dump = source.dump()

    target = SqliteMetaStore(tmp_path / "b.db")
    target.create_project(ProjectRecord(id="old", name="Old"), version("old", 1))
    assert target.load(dump) == {"projects": 1, "versions": 2, "outputs": 1}
    assert target.dump() == dump  # and what was there is gone
    assert target.get_project("p") == source.get_project("p")

    # Supabase wrote timestamps with other precisions and offsets; they are normalised
    rows = {
        "projects": [
            {
                "id": "s",
                "name": "S",
                "description": "",
                "is_example": False,
                "current_version": 1,
                "created_at": "2026-01-02T03:04:05.123+00:00",
                "updated_at": "2026-01-02T05:04:05+02:00",
                "deleted_at": None,
            }
        ],
        "versions": [
            {
                "project_id": "s",
                "number": 1,
                "blob_key": "k",
                "label": "",
                "source": "create",
                "ops": 0,
                "bytes": 10,
                "sha256": "",
                "restored_from": None,
                "created_at": "2026-01-02T03:04:05.123456+00:00",
            }
        ],
        "outputs": [],
    }
    target.load(rows)
    s = target.get_project("s")
    assert s.created_at == datetime(2026, 1, 2, 3, 4, 5, 123000, tzinfo=UTC)
    assert s.updated_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert target.dump()["projects"][0]["updated_at"] == "2026-01-02T03:04:05.000000+00:00"


def test_sqlite_load_is_all_or_nothing(tmp_path):
    meta = SqliteMetaStore(tmp_path / "epm.db")
    meta.create_project(ProjectRecord(id="keep", name="Keep"), version("keep", 1))
    broken = {"projects": [], "versions": [{"project_id": "ghost", "number": 1}], "outputs": []}
    with pytest.raises(sqlite3.IntegrityError):
        meta.load(broken)
    assert meta.get_project("keep") is not None


def test_blob_store():
    blobs = MemoryBlobStore()
    blobs.put("a/1", b"x")
    blobs.put("a/2", b"y")
    blobs.put("b/1", b"z")
    assert blobs.get("a/1") == b"x" and blobs.keys("a/") == ["a/1", "a/2"]
    blobs.delete("a/1")
    blobs.delete("a/1")  # deleting twice is fine
    with pytest.raises(KeyError):
        blobs.get("a/1")
