"""What the service needs from storage: project metadata (MetaStore) and files (BlobStore)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass
class ProjectRecord:
    id: str
    name: str
    description: str = ""
    is_example: bool = False
    current_version: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class VersionRecord:
    project_id: str
    number: int
    blob_key: str
    label: str = ""
    source: str = ""  # create, import, duplicate, seed, ui, mcp, api, restore
    ops: int = 0
    bytes: int = 0
    sha256: str = ""
    restored_from: int | None = None  # set when this version restores an earlier one
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        del d["blob_key"]  # storage detail
        return _jsonable(d)


@dataclass
class OutputRecord:
    id: str
    project_id: str
    version: int
    kind: str  # run, sweep, analysis, plot
    name: str
    blob_key: str
    content_type: str
    bytes: int = 0
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        del d["blob_key"]
        return _jsonable(d)


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in d.items()}


class MetaStore(Protocol):
    """Project, version and output records. Implementations must make the two
    ``*_version`` writes atomic: they are how concurrent saves are kept apart."""

    def create_project(self, project: ProjectRecord, first: VersionRecord) -> ProjectRecord:
        """Insert a project together with its version 1."""
        ...

    def get_project(self, project_id: str) -> ProjectRecord | None:
        """The project, deleted or not."""
        ...

    def list_projects(
        self, q: str | None = None, deleted: bool = False, limit: int = 200
    ) -> list[ProjectRecord]:
        """Projects whose name or description contains ``q``, most recently updated first.
        ``deleted`` lists deleted projects instead of live ones."""
        ...

    def update_project(self, project_id: str, **fields: Any) -> ProjectRecord:
        """Set ``name``, ``description`` or ``deleted_at``; also touches ``updated_at``."""
        ...

    def commit_version(self, version: VersionRecord, expected: int) -> bool:
        """Add ``version`` and make it current, only if the current version is still
        ``expected``. False when another save got there first."""
        ...

    def get_version(self, project_id: str, number: int) -> VersionRecord | None: ...

    def list_versions(self, project_id: str) -> list[VersionRecord]:
        """Newest first."""
        ...

    def delete_versions(self, project_id: str, numbers: list[int]) -> None: ...

    def purge_project(self, project_id: str) -> None:
        """Remove the project and all its versions and outputs."""
        ...

    def add_output(self, output: OutputRecord) -> OutputRecord: ...

    def get_output(self, project_id: str, output_id: str) -> OutputRecord | None: ...

    def list_outputs(self, project_id: str) -> list[OutputRecord]:
        """Newest first."""
        ...

    def delete_outputs(self, project_id: str, ids: list[str]) -> None: ...

    def dump(self) -> dict[str, list[dict[str, Any]]]:
        """Every row of every table, for backups."""
        ...


class BlobStore(Protocol):
    def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None: ...

    def get(self, key: str) -> bytes:
        """Raises KeyError if there is no such object."""
        ...

    def delete(self, key: str) -> None:
        """Deleting a missing object is not an error."""
        ...

    def keys(self, prefix: str) -> list[str]: ...
