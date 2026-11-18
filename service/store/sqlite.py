"""MetaStore on a local SQLite file: the project, version and output records.

One file on the server's disk, opened by this process only. All access goes through one
connection under a lock: the tables are small and every operation takes microseconds, so
there is nothing to gain from more. The two ``*_version`` writes are single transactions.

Timestamps are stored as UTC ISO-8601 text with microseconds, so they sort as text.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import OutputRecord, ProjectRecord, VersionRecord

SCHEMA = """
create table if not exists projects (
  id text primary key,
  name text not null,
  description text not null default '',
  is_example integer not null default 0,
  current_version integer not null default 0,
  created_at text not null,
  updated_at text not null,
  deleted_at text
);
create index if not exists projects_updated_at on projects (updated_at desc);

create table if not exists versions (
  project_id text not null references projects (id) on delete cascade,
  number integer not null,
  blob_key text not null,
  label text not null default '',
  source text not null default '',
  ops integer not null default 0,
  bytes integer not null default 0,
  sha256 text not null default '',
  restored_from integer,
  created_at text not null,
  primary key (project_id, number)
);

create table if not exists outputs (
  id text primary key,
  project_id text not null references projects (id) on delete cascade,
  version integer not null,
  kind text not null,
  name text not null,
  blob_key text not null,
  content_type text not null,
  bytes integer not null default 0,
  created_at text not null
);
create index if not exists outputs_project on outputs (project_id, created_at desc);
"""

TABLES = {"projects": ProjectRecord, "versions": VersionRecord, "outputs": OutputRecord}
_TIMES = ("created_at", "updated_at", "deleted_at")
_EDITABLE = {"name", "description", "deleted_at"}
_CHUNK = 500  # ids per DELETE, well under SQLite's limit on bound values


def _stamp(value: datetime | str | None = None) -> str:
    """``value`` (default: now) as the stored text form."""
    if value is None:
        value = datetime.now(UTC)
    elif isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _record(cls, row: sqlite3.Row):
    values = dict(row)
    for k in _TIMES:
        if values.get(k) is not None:
            values[k] = datetime.fromisoformat(values[k])
    if "is_example" in values:
        values["is_example"] = bool(values["is_example"])
    return cls(**values)


class SqliteMetaStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # autocommit: transactions are opened explicitly, where one is needed
        self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # WAL keeps the file readable while it is written (and is what Litestream replicates);
        # FULL makes a saved version survive a power cut.
        self._db.execute("pragma journal_mode = wal")
        self._db.execute("pragma synchronous = full")
        self._db.execute("pragma foreign_keys = on")
        self._db.execute("pragma busy_timeout = 5000")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("begin immediate")
            try:
                yield self._db
            except BaseException:
                self._db.execute("rollback")
                raise
            self._db.execute("commit")

    def _one(self, cls, sql: str, *params: Any):
        with self._lock:
            row = self._db.execute(sql, params).fetchone()
        return _record(cls, row) if row else None

    def _many(self, cls, sql: str, *params: Any) -> list:
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [_record(cls, r) for r in rows]

    # -- projects ------------------------------------------------------------------------

    def create_project(self, project: ProjectRecord, first: VersionRecord) -> ProjectRecord:
        now = _stamp()
        try:
            with self._transaction() as db:
                db.execute(
                    "insert into projects (id, name, description, is_example, current_version,"
                    " created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?)",
                    (
                        project.id,
                        project.name,
                        project.description,
                        int(project.is_example),
                        first.number,
                        now,
                        now,
                    ),
                )
                self._insert_version(db, first, now)
        except sqlite3.IntegrityError:
            raise ValueError(f"project '{project.id}' exists") from None
        created = self.get_project(project.id)
        assert created is not None
        return created

    def get_project(self, project_id: str) -> ProjectRecord | None:
        return self._one(ProjectRecord, "select * from projects where id = ?", project_id)

    def list_projects(
        self, q: str | None = None, deleted: bool = False, limit: int = 200
    ) -> list[ProjectRecord]:
        needle = (q or "").strip().lower()
        found = self._many(
            ProjectRecord,
            "select * from projects where (deleted_at is not null) = ? order by updated_at desc",
            int(deleted),
        )
        # matched here rather than in SQL: SQLite's lower() only folds ASCII
        if needle:
            found = [
                p for p in found if needle in p.name.lower() or needle in p.description.lower()
            ]
        return found[:limit]

    def update_project(self, project_id: str, **fields_: Any) -> ProjectRecord:
        unknown = set(fields_) - _EDITABLE
        if unknown:
            raise ValueError(f"cannot set {sorted(unknown)} on a project")
        values = dict(fields_)
        if values.get("deleted_at") is not None:
            values["deleted_at"] = _stamp(values["deleted_at"])
        values["updated_at"] = _stamp()
        assignments = ", ".join(f"{name} = ?" for name in values)
        with self._transaction() as db:
            changed = db.execute(
                f"update projects set {assignments} where id = ?", (*values.values(), project_id)
            ).rowcount
        if not changed:
            raise KeyError(project_id)
        updated = self.get_project(project_id)
        assert updated is not None
        return updated

    def purge_project(self, project_id: str) -> None:
        with self._transaction() as db:  # versions and outputs go with it (on delete cascade)
            db.execute("delete from projects where id = ?", (project_id,))

    # -- versions ------------------------------------------------------------------------

    @staticmethod
    def _insert_version(db: sqlite3.Connection, v: VersionRecord, created_at: str) -> None:
        db.execute(
            "insert into versions (project_id, number, blob_key, label, source, ops, bytes,"
            " sha256, restored_from, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                v.project_id,
                v.number,
                v.blob_key,
                v.label,
                v.source,
                v.ops,
                v.bytes,
                v.sha256,
                v.restored_from,
                created_at,
            ),
        )

    def commit_version(self, version: VersionRecord, expected: int) -> bool:
        now = _stamp()
        with self._lock:
            self._db.execute("begin immediate")
            try:
                moved = self._db.execute(
                    "update projects set current_version = ?, updated_at = ?"
                    " where id = ? and current_version = ?",
                    (version.number, now, version.project_id, expected),
                ).rowcount
                if moved:
                    self._insert_version(self._db, version, now)
            except sqlite3.IntegrityError:  # that version number is taken
                moved = 0
            except BaseException:
                self._db.execute("rollback")
                raise
            self._db.execute("commit" if moved else "rollback")
        return bool(moved)

    def get_version(self, project_id: str, number: int) -> VersionRecord | None:
        return self._one(
            VersionRecord,
            "select * from versions where project_id = ? and number = ?",
            project_id,
            number,
        )

    def list_versions(self, project_id: str) -> list[VersionRecord]:
        return self._many(
            VersionRecord,
            "select * from versions where project_id = ? order by number desc",
            project_id,
        )

    def delete_versions(self, project_id: str, numbers: list[int]) -> None:
        self._delete("versions", "number", project_id, numbers)

    # -- outputs -------------------------------------------------------------------------

    def add_output(self, output: OutputRecord) -> OutputRecord:
        with self._transaction() as db:
            db.execute(
                "insert into outputs (id, project_id, version, kind, name, blob_key,"
                " content_type, bytes, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    output.id,
                    output.project_id,
                    output.version,
                    output.kind,
                    output.name,
                    output.blob_key,
                    output.content_type,
                    output.bytes,
                    _stamp(),
                ),
            )
        added = self.get_output(output.project_id, output.id)
        assert added is not None
        return added

    def get_output(self, project_id: str, output_id: str) -> OutputRecord | None:
        return self._one(
            OutputRecord,
            "select * from outputs where project_id = ? and id = ?",
            project_id,
            output_id,
        )

    def list_outputs(self, project_id: str) -> list[OutputRecord]:
        return self._many(
            OutputRecord,
            "select * from outputs where project_id = ? order by created_at desc, rowid desc",
            project_id,
        )

    def delete_outputs(self, project_id: str, ids: list[str]) -> None:
        self._delete("outputs", "id", project_id, ids)

    def _delete(self, table: str, column: str, project_id: str, values: list) -> None:
        with self._transaction() as db:
            for i in range(0, len(values), _CHUNK):
                chunk = values[i : i + _CHUNK]
                marks = ", ".join("?" * len(chunk))
                db.execute(
                    f"delete from {table} where project_id = ? and {column} in ({marks})",
                    (project_id, *chunk),
                )

    # -- backup and restore --------------------------------------------------------------

    def dump(self) -> dict[str, list[dict[str, Any]]]:
        orders = {"projects": "id", "versions": "blob_key", "outputs": "id"}
        with self._lock:
            tables = {
                name: [dict(r) for r in self._db.execute(f"select * from {name} order by {order}")]
                for name, order in orders.items()
            }
        for project in tables["projects"]:
            project["is_example"] = bool(project["is_example"])
        return tables

    def load(self, dump: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
        """Replace every record with those of ``dump`` (what :meth:`dump` returns, here or
        on the Supabase store this one replaced). All or nothing."""
        counts = {}
        with self._transaction() as db:
            db.execute("delete from projects")  # versions and outputs cascade
            for table, cls in TABLES.items():
                names = [f.name for f in fields(cls)]
                marks = ", ".join("?" * len(names))
                rows = dump.get(table) or []
                for row in rows:
                    values = [row.get(name) for name in names]
                    for i, name in enumerate(names):
                        if name in _TIMES and values[i] is not None:
                            values[i] = _stamp(values[i])
                        elif name == "is_example":
                            values[i] = int(bool(values[i]))
                    db.execute(f"insert into {table} ({', '.join(names)}) values ({marks})", values)
                counts[table] = len(rows)
        return counts
