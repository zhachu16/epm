"""MetaStore on Supabase (Postgres through PostgREST). Tables and functions: schema.sql."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from typing import Any

from .base import OutputRecord, ProjectRecord, VersionRecord

_PAGE = 1000  # PostgREST's default row limit


def _parse(cls, row: dict[str, Any]):
    names = {f.name for f in fields(cls)}
    values = {k: v for k, v in row.items() if k in names}
    for k in ("created_at", "updated_at", "deleted_at"):
        if isinstance(values.get(k), str):
            values[k] = datetime.fromisoformat(values[k])
    return cls(**values)


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class SupabaseMetaStore:
    def __init__(self, url: str, key: str):
        from supabase import create_client

        self.client = create_client(url, key)

    def _table(self, name: str):
        return self.client.table(name)

    def create_project(self, project: ProjectRecord, first: VersionRecord) -> ProjectRecord:
        rows = (
            self.client.rpc(
                "create_project",
                {
                    "p_id": project.id,
                    "p_name": project.name,
                    "p_description": project.description,
                    "p_is_example": project.is_example,
                    "p_number": first.number,
                    "p_blob_key": first.blob_key,
                    "p_label": first.label,
                    "p_source": first.source,
                    "p_ops": first.ops,
                    "p_bytes": first.bytes,
                    "p_sha256": first.sha256,
                },
            )
            .execute()
            .data
        )
        return _parse(ProjectRecord, rows[0])

    def get_project(self, project_id: str) -> ProjectRecord | None:
        rows = self._table("projects").select("*").eq("id", project_id).execute().data
        return _parse(ProjectRecord, rows[0]) if rows else None

    def list_projects(
        self, q: str | None = None, deleted: bool = False, limit: int = 200
    ) -> list[ProjectRecord]:
        # a function rather than a PostgREST filter, so q needs no escaping
        rows = (
            self.client.rpc(
                "search_projects", {"p_q": q or "", "p_deleted": deleted, "p_limit": limit}
            )
            .execute()
            .data
        )
        return [_parse(ProjectRecord, r) for r in rows or []]

    def update_project(self, project_id: str, **fields_: Any) -> ProjectRecord:
        values = {k: _iso(v) for k, v in fields_.items()}
        values["updated_at"] = datetime.now(UTC).isoformat()
        rows = self._table("projects").update(values).eq("id", project_id).execute().data
        if not rows:
            raise KeyError(project_id)
        return _parse(ProjectRecord, rows[0])

    def commit_version(self, version: VersionRecord, expected: int) -> bool:
        return bool(
            self.client.rpc(
                "commit_version",
                {
                    "p_project_id": version.project_id,
                    "p_expected": expected,
                    "p_number": version.number,
                    "p_blob_key": version.blob_key,
                    "p_label": version.label,
                    "p_source": version.source,
                    "p_ops": version.ops,
                    "p_bytes": version.bytes,
                    "p_sha256": version.sha256,
                    "p_restored_from": version.restored_from,
                },
            )
            .execute()
            .data
        )

    def get_version(self, project_id: str, number: int) -> VersionRecord | None:
        rows = (
            self._table("versions")
            .select("*")
            .eq("project_id", project_id)
            .eq("number", number)
            .execute()
            .data
        )
        return _parse(VersionRecord, rows[0]) if rows else None

    def list_versions(self, project_id: str) -> list[VersionRecord]:
        rows = self._all(
            lambda: (
                self._table("versions")
                .select("*")
                .eq("project_id", project_id)
                .order("number", desc=True)
            )
        )
        return [_parse(VersionRecord, r) for r in rows]

    def delete_versions(self, project_id: str, numbers: list[int]) -> None:
        for i in range(0, len(numbers), 200):
            (
                self._table("versions")
                .delete()
                .eq("project_id", project_id)
                .in_("number", numbers[i : i + 200])
                .execute()
            )

    def purge_project(self, project_id: str) -> None:
        # versions and outputs go with it (on delete cascade)
        self._table("projects").delete().eq("id", project_id).execute()

    def add_output(self, output: OutputRecord) -> OutputRecord:
        row = {f.name: _iso(getattr(output, f.name)) for f in fields(OutputRecord)}
        row.pop("created_at")
        rows = self._table("outputs").insert(row).execute().data
        return _parse(OutputRecord, rows[0])

    def get_output(self, project_id: str, output_id: str) -> OutputRecord | None:
        rows = (
            self._table("outputs")
            .select("*")
            .eq("project_id", project_id)
            .eq("id", output_id)
            .execute()
            .data
        )
        return _parse(OutputRecord, rows[0]) if rows else None

    def list_outputs(self, project_id: str) -> list[OutputRecord]:
        rows = self._all(
            lambda: (
                self._table("outputs")
                .select("*")
                .eq("project_id", project_id)
                .order("created_at", desc=True)
            )
        )
        return [_parse(OutputRecord, r) for r in rows]

    def delete_outputs(self, project_id: str, ids: list[str]) -> None:
        for i in range(0, len(ids), 200):
            (
                self._table("outputs")
                .delete()
                .eq("project_id", project_id)
                .in_("id", ids[i : i + 200])
                .execute()
            )

    def dump(self) -> dict[str, list[dict[str, Any]]]:
        def everything(name: str, order: str):
            return lambda: self._table(name).select("*").order(order)

        return {
            name: self._all(everything(name, order))
            for name, order in (("projects", "id"), ("versions", "blob_key"), ("outputs", "id"))
        }

    def _all(self, query) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        while True:
            page = query().range(len(rows), len(rows) + _PAGE - 1).execute().data or []
            rows.extend(page)
            if len(page) < _PAGE:
                return rows
