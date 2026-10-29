"""In-memory stores, for tests and for running the service without Supabase or R2."""

from __future__ import annotations

import threading
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

from .base import OutputRecord, ProjectRecord, VersionRecord, _jsonable


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryMetaStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.projects: dict[str, ProjectRecord] = {}
        self.versions: dict[str, dict[int, VersionRecord]] = {}
        self.outputs: dict[str, dict[str, OutputRecord]] = {}

    def create_project(self, project: ProjectRecord, first: VersionRecord) -> ProjectRecord:
        with self._lock:
            if project.id in self.projects:
                raise ValueError(f"project '{project.id}' exists")
            now = _now()
            stored = replace(project, current_version=first.number, created_at=now, updated_at=now)
            self.projects[project.id] = stored
            self.versions[project.id] = {first.number: replace(first, created_at=now)}
            self.outputs[project.id] = {}
            return replace(stored)

    def get_project(self, project_id: str) -> ProjectRecord | None:
        with self._lock:
            p = self.projects.get(project_id)
            return replace(p) if p else None

    def list_projects(
        self, q: str | None = None, deleted: bool = False, limit: int = 200
    ) -> list[ProjectRecord]:
        needle = (q or "").strip().lower()
        with self._lock:
            found = [
                replace(p)
                for p in self.projects.values()
                if (p.deleted_at is not None) == deleted
                and (not needle or needle in p.name.lower() or needle in p.description.lower())
            ]
        found.sort(key=lambda p: p.updated_at or _now(), reverse=True)
        return found[:limit]

    def update_project(self, project_id: str, **fields: Any) -> ProjectRecord:
        with self._lock:
            p = self.projects[project_id]
            self.projects[project_id] = replace(p, **fields, updated_at=_now())
            return replace(self.projects[project_id])

    def commit_version(self, version: VersionRecord, expected: int) -> bool:
        with self._lock:
            p = self.projects.get(version.project_id)
            if p is None or p.current_version != expected or version.number in self.versions[p.id]:
                return False
            now = _now()
            self.versions[p.id][version.number] = replace(version, created_at=now)
            self.projects[p.id] = replace(p, current_version=version.number, updated_at=now)
            return True

    def get_version(self, project_id: str, number: int) -> VersionRecord | None:
        with self._lock:
            v = self.versions.get(project_id, {}).get(number)
            return replace(v) if v else None

    def list_versions(self, project_id: str) -> list[VersionRecord]:
        with self._lock:
            found = [replace(v) for v in self.versions.get(project_id, {}).values()]
        return sorted(found, key=lambda v: v.number, reverse=True)

    def delete_versions(self, project_id: str, numbers: list[int]) -> None:
        with self._lock:
            for n in numbers:
                self.versions.get(project_id, {}).pop(n, None)

    def purge_project(self, project_id: str) -> None:
        with self._lock:
            self.projects.pop(project_id, None)
            self.versions.pop(project_id, None)
            self.outputs.pop(project_id, None)

    def add_output(self, output: OutputRecord) -> OutputRecord:
        with self._lock:
            stored = replace(output, created_at=_now())
            self.outputs.setdefault(output.project_id, {})[output.id] = stored
            return replace(stored)

    def get_output(self, project_id: str, output_id: str) -> OutputRecord | None:
        with self._lock:
            o = self.outputs.get(project_id, {}).get(output_id)
            return replace(o) if o else None

    def list_outputs(self, project_id: str) -> list[OutputRecord]:
        with self._lock:
            found = [replace(o) for o in self.outputs.get(project_id, {}).values()]
        return sorted(found, key=lambda o: o.created_at or _now(), reverse=True)

    def delete_outputs(self, project_id: str, ids: list[str]) -> None:
        with self._lock:
            for i in ids:
                self.outputs.get(project_id, {}).pop(i, None)

    def dump(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            return {
                "projects": [_jsonable(asdict(p)) for p in self.projects.values()],
                "versions": [
                    _jsonable(asdict(v)) for vs in self.versions.values() for v in vs.values()
                ],
                "outputs": [
                    _jsonable(asdict(o)) for os_ in self.outputs.values() for o in os_.values()
                ],
            }


class MemoryBlobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        with self._lock:
            self.objects[key] = (bytes(data), content_type)

    def get(self, key: str) -> bytes:
        with self._lock:
            return self.objects[key][0]

    def delete(self, key: str) -> None:
        with self._lock:
            self.objects.pop(key, None)

    def keys(self, prefix: str) -> list[str]:
        with self._lock:
            return sorted(k for k in self.objects if k.startswith(prefix))
