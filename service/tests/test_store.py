from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from service.store import MemoryBlobStore, MemoryMetaStore, ProjectRecord, VersionRecord
from service.store.supabase import SupabaseMetaStore, _parse

SCHEMA = (Path(__file__).parents[1] / "schema.sql").read_text()


def version(project_id: str, number: int) -> VersionRecord:
    return VersionRecord(project_id=project_id, number=number, blob_key=f"k{number}")


def test_commit_version_only_on_top_of_the_expected_one():
    meta = MemoryMetaStore()
    meta.create_project(ProjectRecord(id="p", name="P"), version("p", 1))
    assert meta.commit_version(version("p", 2), expected=1)
    assert not meta.commit_version(version("p", 3), expected=1)  # someone saved in between
    assert meta.get_project("p").current_version == 2
    assert [v.number for v in meta.list_versions("p")] == [2, 1]


def test_list_searches_names_and_descriptions():
    meta = MemoryMetaStore()
    meta.create_project(ProjectRecord(id="a", name="Atlas LBO"), version("a", 1))
    meta.create_project(
        ProjectRecord(id="b", name="Budget", description="for ATLAS"), version("b", 1)
    )
    meta.create_project(ProjectRecord(id="c", name="Other"), version("c", 1))
    assert {p.id for p in meta.list_projects("atlas")} == {"a", "b"}
    meta.update_project("a", deleted_at=datetime.now(UTC))
    assert {p.id for p in meta.list_projects("atlas")} == {"b"}
    assert [p.id for p in meta.list_projects(deleted=True)] == ["a"]
    meta.update_project("c", name="Other, renamed")
    assert [p.id for p in meta.list_projects()] == ["c", "b"]  # most recently changed first


def test_records_returned_are_copies():
    meta = MemoryMetaStore()
    p = meta.create_project(ProjectRecord(id="p", name="P"), version("p", 1))
    p.name = "changed"
    assert meta.get_project("p").name == "P"


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


def test_supabase_rows_parse_timestamps():
    row = {
        "id": "p",
        "name": "P",
        "description": "",
        "is_example": False,
        "current_version": 3,
        "created_at": "2026-01-02T03:04:05.123+00:00",
        "updated_at": "2026-01-02T03:04:05+00:00",
        "deleted_at": None,
        "extra_column": 1,
    }
    p = _parse(ProjectRecord, row)
    assert p.created_at == datetime(2026, 1, 2, 3, 4, 5, 123000, tzinfo=UTC)
    assert p.deleted_at is None and p.current_version == 3


class _FakeRpc:
    def __init__(self, calls, name, params):
        calls.append((name, params))

    def execute(self):
        return type("Response", (), {"data": [{"id": "p", "name": "P"}]})()


class _FakeClient:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name, params):
        return _FakeRpc(self.calls, name, params)


def _declared_params(function: str) -> set[str]:
    match = re.search(rf"function {function}\((.*?)\)\s*returns", SCHEMA, re.S)
    assert match, function
    return {p.split()[0] for p in match.group(1).split(",")}


def test_supabase_calls_match_the_schema_functions():
    store = SupabaseMetaStore.__new__(SupabaseMetaStore)
    store.client = _FakeClient()
    store.create_project(ProjectRecord(id="p", name="P"), version("p", 1))
    store.commit_version(version("p", 2), expected=1)
    store.list_projects("x")
    for name, params in store.client.calls:
        assert set(params) == _declared_params(name), name
