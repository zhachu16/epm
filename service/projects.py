"""The service layer: every project operation the HTTP API and the MCP tools offer.

Requests are stateless. Each one loads the project's current version from storage into a
fresh :class:`~epm.Workspace`, works on it and, if the model changed, saves a new version.
Versions are immutable and numbered; restoring one saves its contents as a new version, so
history is never rewritten. Model problems come back as diagnostics, bad requests raise
:class:`~epm.EpmError` with a code (``unknown_project``, ``read_only_project``,
``version_conflict``, ...), as everywhere else in epm.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import string
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from epm import Workspace
from epm.cli.output import clean
from epm.engine.cache import ValueCache
from epm.errors import EpmError
from epm.formula.builtins import BUILTINS
from epm.model import Model, Settings
from epm.ops import Op, ops_from_model
from epm.package import Package, package_bytes, read_package, read_package_bytes
from epm.server.session import run_cli, workspace_snapshot
from epm.sources import SourceError

from . import config
from .store import BlobStore, MetaStore, OutputRecord, ProjectRecord, VersionRecord

EXAMPLE_ID = "example-lbo"
EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "lbo"
EPM_TYPE = "application/zip"
_ALPHABET = string.ascii_lowercase + string.digits
_LIST_KINDS = {
    "metrics",
    "inputs",
    "axes",
    "sources",
    "functions",
    "tables",
    "checks",
    "circularities",
    "analyses",
    "files",
    "builtins",
}


def new_id(prefix: str) -> str:
    return prefix + "".join(secrets.choice(_ALPHABET) for _ in range(10))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "model"


def archive_of(ws: Workspace) -> bytes:
    """The workspace as a ``.epm`` archive, without calculated values."""
    return package_bytes(Package(ws.model, ws.inputs, ws.functions), include_cache=False)


def _now() -> datetime:
    return datetime.now(UTC)


class Projects:
    """Projects, their versions, files and outputs, over a MetaStore and a BlobStore."""

    def __init__(
        self,
        meta: MetaStore,
        blobs: BlobStore,
        example: Path | None = EXAMPLE_DIR,
        blob_cache_bytes: int = 256 * 1024**2,
    ):
        self.meta = meta
        self.blobs = blobs
        self.example = example
        self._locks: dict[str, threading.RLock] = {}
        self._locks_lock = threading.Lock()
        # Version archives never change, so caching them by key is always safe.
        self._blob_cache: OrderedDict[str, bytes] = OrderedDict()
        self._blob_cache_bytes = blob_cache_bytes
        self._blob_cache_lock = threading.Lock()
        # Calculated values, per project. Step fingerprints key them, so they stay valid
        # across versions and a stale entry is simply never hit.
        self._values: OrderedDict[str, ValueCache] = OrderedDict()

    # -- plumbing ------------------------------------------------------------------------

    def _lock(self, project_id: str) -> threading.RLock:
        with self._locks_lock:
            return self._locks.setdefault(project_id, threading.RLock())

    def _blob(self, key: str) -> bytes:
        with self._blob_cache_lock:
            if key in self._blob_cache:
                self._blob_cache.move_to_end(key)
                return self._blob_cache[key]
        try:
            data = self.blobs.get(key)
        except KeyError:
            raise EpmError("missing_blob", f"stored file '{key}' is missing") from None
        self._remember(key, data)
        return data

    def _remember(self, key: str, data: bytes) -> None:
        with self._blob_cache_lock:
            self._blob_cache[key] = data
            total = sum(len(b) for b in self._blob_cache.values())
            while total > self._blob_cache_bytes and len(self._blob_cache) > 1:
                _, dropped = self._blob_cache.popitem(last=False)
                total -= len(dropped)

    def _value_cache(self, project_id: str) -> ValueCache:
        with self._locks_lock:
            cache = self._values.pop(project_id, None) or ValueCache(max_entries=20_000)
            self._values[project_id] = cache
            while len(self._values) > 32:
                self._values.popitem(last=False)
            return cache

    def _project(self, project_id: str, deleted: bool = False) -> ProjectRecord:
        project = self.meta.get_project(project_id)
        if project is None or (project.deleted_at is not None and not deleted):
            raise EpmError("unknown_project", f"there is no project '{project_id}'")
        return project

    def _version(self, project: ProjectRecord, number: int | None = None) -> VersionRecord:
        n = project.current_version if number is None else number
        version = self.meta.get_version(project.id, n)
        if version is None:
            raise EpmError("unknown_version", f"project '{project.id}' has no version {n}")
        return version

    @staticmethod
    def _writable(project: ProjectRecord) -> None:
        if project.is_example:
            raise EpmError(
                "read_only_project",
                f"'{project.name}' is the read-only example; duplicate it to make an editable copy",
            )

    @staticmethod
    def _expect(project: ProjectRecord, expected_version: int | None) -> None:
        if expected_version is not None and expected_version != project.current_version:
            raise EpmError(
                "version_conflict",
                f"the project was saved since version {expected_version} (it is now at "
                f"version {project.current_version}); reload and try again",
                current_version=project.current_version,
            )

    def _load(self, project: ProjectRecord, version: VersionRecord) -> Workspace:
        pkg = read_package_bytes(self._blob(version.blob_key))
        ws = Workspace(pkg.model, pkg.inputs, pkg.functions)
        ws.cache = self._value_cache(project.id)
        return ws

    @contextmanager
    def _open(
        self, project_id: str, number: int | None = None
    ) -> Iterator[tuple[ProjectRecord, VersionRecord, Workspace]]:
        """The project at a version (default: current), loaded into a workspace."""
        with self._lock(project_id):
            project = self._project(project_id)
            version = self._version(project, number)
            ws = self._load(project, version)
            try:
                yield project, version, ws
            finally:
                ws.close()

    def _store_version(
        self,
        project: ProjectRecord,
        data: bytes,
        label: str,
        source: str,
        ops: int = 0,
        restored_from: int | None = None,
    ) -> VersionRecord:
        """Upload ``data`` and make it the project's next version."""
        number = project.current_version + 1
        key = f"projects/{project.id}/versions/{number:06d}-{secrets.token_hex(4)}.epm"
        self.blobs.put(key, data, EPM_TYPE)
        version = VersionRecord(
            project_id=project.id,
            number=number,
            blob_key=key,
            label=label[:200],
            source=source,
            ops=ops,
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            restored_from=restored_from,
        )
        if not self.meta.commit_version(version, project.current_version):
            self.blobs.delete(key)
            current = self.meta.get_project(project.id)
            raise EpmError(
                "version_conflict",
                "another save landed first; reload and try again",
                current_version=current.current_version if current else None,
            )
        self._remember(key, data)
        return self.meta.get_version(project.id, number) or version

    def _new_project(
        self,
        data: bytes,
        name: str,
        description: str = "",
        source: str = "create",
        label: str = "created",
        project_id: str | None = None,
        is_example: bool = False,
    ) -> ProjectRecord:
        project_id = project_id or new_id("p_")
        key = f"projects/{project_id}/versions/{1:06d}-{secrets.token_hex(4)}.epm"
        self.blobs.put(key, data, EPM_TYPE)
        first = VersionRecord(
            project_id=project_id,
            number=1,
            blob_key=key,
            label=label,
            source=source,
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        record = ProjectRecord(
            id=project_id,
            name=name.strip() or "Untitled model",
            description=description,
            is_example=is_example,
        )
        try:
            created = self.meta.create_project(record, first)
        except Exception:
            self.blobs.delete(key)
            raise
        self._remember(key, data)
        return created

    # -- projects --------------------------------------------------------------------------

    def list_projects(self, q: str | None = None, deleted: bool = False) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.meta.list_projects(q, deleted)]

    def get(self, project_id: str) -> dict[str, Any]:
        return self._project(project_id).to_dict()

    def create(
        self, name: str, description: str = "", from_project: str | None = None
    ) -> dict[str, Any]:
        """A new empty model, or a copy of ``from_project`` (for example the example)."""
        if from_project:
            return self.duplicate(from_project, name or None, description or None)
        ws = Workspace(Model(settings=Settings(name=name.strip() or "Untitled model")))
        return self._new_project(archive_of(ws), name, description).to_dict()

    def import_epm(
        self, data: bytes, name: str | None = None, description: str = ""
    ) -> dict[str, Any]:
        pkg = read_package_bytes(data)
        ws = Workspace(pkg.model, pkg.inputs, pkg.functions)
        name = (name or "").strip() or pkg.model.settings.name or "Imported model"
        return self._new_project(
            archive_of(ws), name, description, source="import", label="imported"
        ).to_dict()

    def duplicate(
        self, project_id: str, name: str | None = None, description: str | None = None
    ) -> dict[str, Any]:
        project = self._project(project_id)
        data = self._blob(self._version(project).blob_key)
        if description is None:
            # the example's description says it is read-only, which the copy isn't
            description = (
                f"A copy of {project.name}." if project.is_example else project.description
            )
        return self._new_project(
            data,
            name or f"{project.name} (copy)",
            description,
            source="duplicate",
            label=f"copied from {project.name}",
        ).to_dict()

    def rename(
        self, project_id: str, name: str | None = None, description: str | None = None
    ) -> dict[str, Any]:
        project = self._project(project_id)
        self._writable(project)
        fields: dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise EpmError("invalid_name", "a project needs a name")
            fields["name"] = name.strip()
        if description is not None:
            fields["description"] = description
        if not fields:
            return project.to_dict()
        return self.meta.update_project(project.id, **fields).to_dict()

    def delete(self, project_id: str) -> dict[str, Any]:
        """Hide the project. It can be restored for ``config.KEEP_DELETED_DAYS`` days."""
        project = self._project(project_id)
        self._writable(project)
        return self.meta.update_project(project.id, deleted_at=_now()).to_dict()

    def restore_project(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id, deleted=True)
        if project.deleted_at is None:
            return project.to_dict()
        return self.meta.update_project(project.id, deleted_at=None).to_dict()

    def export_epm(self, project_id: str, version: int | None = None) -> tuple[str, bytes]:
        """``(filename, archive)`` for the current version, or an earlier one."""
        project = self._project(project_id)
        v = self._version(project, version)
        suffix = "" if version is None else f"-v{v.number}"
        return f"{slug(project.name)}{suffix}.epm", self._blob(v.blob_key)

    # -- reading the model -----------------------------------------------------------------

    def state(self, project_id: str, version: int | None = None) -> dict[str, Any]:
        """What the web UI shows: the ``fm serve`` snapshot, plus project and version."""
        with self._open(project_id, version) as (project, v, ws):
            snapshot = workspace_snapshot(ws)
        snapshot["version"] = v.number
        snapshot["project"] = project.to_dict()
        snapshot["read_only"] = project.is_example or v.number != project.current_version
        return snapshot

    def _cli(self, project_id: str, argv: list[str]) -> Any:
        """Run a read-only ``fm`` command and return its JSON ``data``."""
        with self._open(project_id) as (_, _, ws):
            result = run_cli(ws, ["--json", *argv])
        try:
            envelope = json.loads(result["stdout"])
        except json.JSONDecodeError:
            raise EpmError("internal_error", result["stderr"].strip() or "no output") from None
        if not envelope.get("ok"):
            error = (envelope.get("errors") or [{}])[0]
            raise EpmError(
                error.get("code", "error"),
                error.get("message", "the command failed"),
                **{k: v for k, v in error.items() if k not in ("code", "message")},
            )
        return envelope.get("data")

    def list_objects(
        self,
        project_id: str,
        kind: str = "metrics",
        search: str | None = None,
        folder: str | None = None,
    ) -> Any:
        if kind not in _LIST_KINDS:
            raise EpmError(
                "invalid_argument", f"kind must be one of {', '.join(sorted(_LIST_KINDS))}"
            )
        argv = ["list", kind]
        if search:
            argv += ["--search", search]
        if folder:
            argv += ["--folder", folder]
        return self._cli(project_id, argv)

    def show(self, project_id: str, name: str) -> Any:
        return self._cli(project_id, ["show", name])

    def trace(
        self,
        project_id: str,
        name: str,
        direction: str = "upstream",
        depth: int | None = None,
    ) -> Any:
        if direction not in ("upstream", "downstream"):
            raise EpmError("invalid_argument", "direction is 'upstream' or 'downstream'")
        with self._open(project_id) as (_, _, ws):
            return clean({"metrics": ws.trace(name, direction, depth)})

    def validate(self, project_id: str) -> Any:
        return self._cli(project_id, ["validate"])

    def table(self, project_id: str, name: str) -> Any:
        return self._cli(project_id, ["table", "show", name])

    @staticmethod
    def builtins() -> list[dict[str, Any]]:
        return [{"name": n, "signature": b.signature, "doc": b.doc} for n, b in BUILTINS.items()]

    def source_preview(self, project_id: str, name: str, limit: int = 50) -> dict[str, Any]:
        with self._open(project_id) as (_, _, ws):
            table = ws.engine.sources.table(name)
            rows = []
            for item in list(table.items)[:limit]:
                try:
                    entries = table.series(item)
                except SourceError:
                    continue
                rows.append(
                    {
                        "item": item,
                        "values": [
                            e.value
                            if isinstance(e.value, float)
                            else (None if e.value is None else str(e.value))
                            for e in entries
                        ],
                    }
                )
            return clean(
                {
                    "periods": table.labels,
                    "items": list(table.items),
                    "rows": rows,
                    "sheet": table.sheet.name,
                }
            )

    # -- changing the model ----------------------------------------------------------------

    def apply(
        self,
        project_id: str,
        ops: list[dict[str, Any] | Op],
        mode: str = "commit",
        expected_version: int | None = None,
        label: str | None = None,
        source: str = "api",
        allow_errors: bool = False,
        outputs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Apply a batch of operations atomically. ``commit`` saves a new version;
        ``dry_run`` previews the diff and the effect on key outputs."""
        if mode not in ("commit", "dry_run"):
            raise EpmError("invalid_argument", "mode is 'commit' or 'dry_run'")
        if not ops:
            raise EpmError("invalid_argument", "a batch needs at least one operation")
        batch = {"ops": [o.to_dict() if isinstance(o, Op) else o for o in ops], "label": label}
        # Without expected_version a write goes on top of whatever is current; if another
        # replica saves in between, apply it once more on the newer version.
        for attempt in range(2):
            with self._open(project_id) as (project, version, ws):
                if mode == "commit":
                    self._writable(project)
                    self._expect(project, expected_version)
                result = ws.apply(batch, mode, outputs, allow_errors)
                out = clean(result.to_dict())
                out["version"] = version.number
                if mode != "commit" or not result.committed:
                    return out
                try:
                    saved = self._store_version(
                        project, archive_of(ws), result.label or "", source, len(ops)
                    )
                except EpmError as e:
                    if e.code == "version_conflict" and expected_version is None and not attempt:
                        continue
                    raise
                out["version"] = saved.number
                return out
        raise AssertionError("unreachable")

    def put_model(
        self,
        project_id: str,
        model: Mapping[str, Any],
        mode: str = "commit",
        expected_version: int | None = None,
        source: str = "api",
        allow_errors: bool = False,
    ) -> dict[str, Any]:
        """Replace the whole of model.json; the difference is applied as operations."""
        with self._open(project_id) as (_, version, ws):
            ops = ops_from_model(ws.state(), dict(model))
        if not ops:
            return {
                "ok": True,
                "mode": mode,
                "committed": False,
                "changes": [],
                "errors": [],
                "label": "no changes",
                "version": version.number,
            }
        return self.apply(
            project_id, ops, mode, expected_version, "edit model.json", source, allow_errors
        )

    def put_functions(
        self,
        project_id: str,
        code: str,
        mode: str = "commit",
        expected_version: int | None = None,
        source: str = "api",
    ) -> dict[str, Any]:
        op = Op(op="update", kind="functions", set={"code": code})
        return self.apply(project_id, [op], mode, expected_version, "edit functions.py", source)

    def upload_file(
        self,
        project_id: str,
        path: str,
        data: bytes,
        expected_version: int | None = None,
        source: str = "api",
    ) -> dict[str, Any]:
        """Add or replace an input file (``inputs/...``) and save a new version."""
        path = path if path.startswith("inputs/") else f"inputs/{path.lstrip('/')}"
        op = {
            "op": "upsert",
            "kind": "file",
            "name": path,
            "data": base64.b64encode(data).decode(),
        }
        return self.apply(project_id, [op], "commit", expected_version, f"upload {path}", source)

    # -- calculating -----------------------------------------------------------------------

    def run(
        self,
        project_id: str,
        overrides: Mapping[str, Any] | None = None,
        outputs: list[str] | None = None,
        save: bool = False,
        source: str = "api",
    ) -> dict[str, Any]:
        with self._open(project_id) as (project, version, ws):
            result = clean(ws.run(overrides or {}, outputs).to_dict())
        result["version"] = version.number
        if save:
            result["output"] = self._save_output(
                project, version.number, "run", "run", json.dumps(result).encode(), "json"
            )
        return result

    def sweep(
        self,
        project_id: str,
        inputs: Mapping[str, list[float]],
        outputs: list[str],
        mode: str = "grid",
        overrides: Mapping[str, Any] | None = None,
        save: bool = False,
    ) -> dict[str, Any]:
        if mode not in ("grid", "zip"):
            raise EpmError("invalid_argument", "mode is 'grid' or 'zip'")
        with self._open(project_id) as (project, version, ws):
            result = ws.sweep(inputs, outputs, mode, overrides)  # type: ignore[arg-type]
        out = clean(result.to_dict())
        out["version"] = version.number
        if save:
            csv = result.frame().to_csv(index=False).encode()
            out["output"] = self._save_output(project, version.number, "sweep", "sweep", csv, "csv")
        return out

    def run_analysis(
        self, project_id: str, name: str, save: bool = False, plot: bool = False
    ) -> dict[str, Any]:
        """Run a saved analysis. ``save`` stores its results (and, with ``plot``, its chart)."""
        with self._open(project_id) as (project, version, ws):
            result = ws.run_analysis(name)
        out = clean(result.to_dict())
        out["version"] = version.number
        if save:
            csv = result.sweep.frame().to_csv(index=False).encode()
            saved = [self._save_output(project, version.number, "analysis", name, csv, "csv")]
            if plot:
                png = _render_png(result)
                saved.append(self._save_output(project, version.number, "plot", name, png, "png"))
            out["outputs"] = saved
        return out

    def plot(self, project_id: str, name: str) -> bytes:
        """An analysis chart as a PNG, without saving it."""
        with self._open(project_id) as (_, _, ws):
            result = ws.run_analysis(name)
        return _render_png(result)

    # -- files and outputs -----------------------------------------------------------------

    def list_files(self, project_id: str, version: int | None = None) -> list[dict[str, Any]]:
        with self._open(project_id, version) as (_, _, ws):
            return [{"name": p, "bytes": len(b)} for p, b in sorted(ws.inputs.items())]

    def download_file(self, project_id: str, path: str, version: int | None = None) -> bytes:
        with self._open(project_id, version) as (_, _, ws):
            if path not in ws.inputs and f"inputs/{path}" in ws.inputs:
                path = f"inputs/{path}"
            if path not in ws.inputs:
                raise EpmError("unknown_file", f"there is no file '{path}'")
            return ws.inputs[path]

    def _save_output(
        self, project: ProjectRecord, version: int, kind: str, name: str, data: bytes, ext: str
    ) -> dict[str, Any]:
        self._writable(project)
        output_id = new_id("o_")
        key = f"projects/{project.id}/outputs/{output_id}.{ext}"
        content_type = {"json": "application/json", "csv": "text/csv", "png": "image/png"}[ext]
        self.blobs.put(key, data, content_type)
        record = OutputRecord(
            id=output_id,
            project_id=project.id,
            version=version,
            kind=kind,
            name=f"{name}.{ext}",
            blob_key=key,
            content_type=content_type,
            bytes=len(data),
        )
        return self.meta.add_output(record).to_dict()

    def list_outputs(self, project_id: str) -> list[dict[str, Any]]:
        project = self._project(project_id)
        return [o.to_dict() for o in self.meta.list_outputs(project.id)]

    def download_output(self, project_id: str, output_id: str) -> tuple[dict[str, Any], bytes]:
        project = self._project(project_id)
        record = self.meta.get_output(project.id, output_id)
        if record is None:
            raise EpmError("unknown_output", f"project '{project_id}' has no output '{output_id}'")
        return record.to_dict(), self._blob(record.blob_key)

    def delete_output(self, project_id: str, output_id: str) -> None:
        project = self._project(project_id)
        self._writable(project)
        record = self.meta.get_output(project.id, output_id)
        if record is None:
            raise EpmError("unknown_output", f"project '{project_id}' has no output '{output_id}'")
        self.meta.delete_outputs(project.id, [output_id])
        self.blobs.delete(record.blob_key)

    # -- versions --------------------------------------------------------------------------

    def list_versions(self, project_id: str) -> list[dict[str, Any]]:
        project = self._project(project_id)
        return [
            {**v.to_dict(), "current": v.number == project.current_version}
            for v in self.meta.list_versions(project.id)
        ]

    def restore(
        self,
        project_id: str,
        number: int,
        expected_version: int | None = None,
        source: str = "api",
        label: str | None = None,
    ) -> dict[str, Any]:
        """Save version ``number``'s contents as a new version (history stays intact)."""
        with self._lock(project_id):
            project = self._project(project_id)
            self._writable(project)
            self._expect(project, expected_version)
            target = self._version(project, number)
            if number == project.current_version:
                raise EpmError("invalid_argument", f"version {number} is already current")
            saved = self._store_version(
                project,
                self._blob(target.blob_key),
                label or f"restore version {number}",
                source,
                restored_from=number,
            )
            return {"restored": number, "version": saved.to_dict()}

    def undo(
        self, project_id: str, expected_version: int | None = None, source: str = "api"
    ) -> dict[str, Any]:
        """Go back one change. Undoing again keeps going back, rather than undoing the undo:
        a version that restores another counts as the one it restored."""
        with self._lock(project_id):
            project = self._project(project_id)
            self._writable(project)
            versions = {v.number: v for v in self.meta.list_versions(project.id)}
            effective = project.current_version
            seen: set[int] = set()
            while effective in versions and versions[effective].restored_from is not None:
                if effective in seen:
                    break
                seen.add(effective)
                effective = versions[effective].restored_from  # type: ignore[assignment]
            earlier = [n for n in versions if n < effective]
            if not earlier:
                raise EpmError("nothing_to_undo", "there is no earlier version to go back to")
            target = max(earlier)
            label = f"undo: back to version {target}"
            return self.restore(project_id, target, expected_version, source, label)

    # -- example and maintenance -----------------------------------------------------------

    def seed_example(self) -> dict[str, Any] | None:
        """Create the read-only example project, or update it when examples/ changed."""
        if self.example is None or not (self.example / "model.json").is_file():
            return None
        pkg = read_package(self.example)
        ws = Workspace(pkg.model, pkg.inputs, pkg.functions)
        data = archive_of(ws)
        digest = hashlib.sha256(data).hexdigest()
        with self._lock(EXAMPLE_ID):
            project = self.meta.get_project(EXAMPLE_ID)
            if project is None:
                return self._new_project(
                    data,
                    "Example: LBO",
                    "The sample leveraged buyout, read-only. Duplicate it to make changes.",
                    source="seed",
                    label="example",
                    project_id=EXAMPLE_ID,
                    is_example=True,
                ).to_dict()
            current = self.meta.get_version(EXAMPLE_ID, project.current_version)
            if current is None or current.sha256 != digest:
                try:
                    self._store_version(project, data, "example updated", "seed")
                except EpmError as e:
                    if e.code != "version_conflict":  # another replica seeded it
                        raise
            if project.deleted_at is not None:
                self.meta.update_project(EXAMPLE_ID, deleted_at=None)
            return self._project(EXAMPLE_ID).to_dict()

    def maintain(self, now: datetime | None = None) -> dict[str, Any]:
        """Apply retention, purge long-deleted projects, and back up the metadata."""
        now = now or _now()
        pruned = 0
        purged: list[str] = []
        for project in self.meta.list_projects(deleted=True, limit=10_000):
            if project.deleted_at and now - project.deleted_at > timedelta(
                days=config.KEEP_DELETED_DAYS
            ):
                self._purge(project.id)
                purged.append(project.id)
        for project in self.meta.list_projects(limit=10_000):
            pruned += self._prune(project, now)
        stamp = now.strftime("%Y-%m-%d")
        backup = json.dumps(self.meta.dump(), indent=1, sort_keys=True, default=str).encode()
        self.blobs.put(f"backups/meta/{stamp}.json", backup, "application/json")
        return {"pruned_versions": pruned, "purged_projects": purged, "backup": stamp}

    def _prune(self, project: ProjectRecord, now: datetime) -> int:
        versions = self.meta.list_versions(project.id)  # newest first
        keep = {v.number for v in versions[: config.KEEP_VERSIONS]}
        keep.add(project.current_version)
        daily: set[str] = set()
        horizon = now - timedelta(days=config.KEEP_DAILY_DAYS)
        for v in versions:
            if v.created_at and v.created_at >= horizon:
                day = v.created_at.strftime("%Y-%m-%d")
                if day not in daily:  # the newest version of each day
                    daily.add(day)
                    keep.add(v.number)
        drop = [v for v in versions if v.number not in keep]
        if drop:
            self.meta.delete_versions(project.id, [v.number for v in drop])
            for v in drop:
                self.blobs.delete(v.blob_key)
        return len(drop)

    def _purge(self, project_id: str) -> None:
        for key in self.blobs.keys(f"projects/{project_id}/"):
            self.blobs.delete(key)
        self.meta.purge_project(project_id)


def _render_png(result) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "plot.png"
        result.save_plot(str(path))
        return path.read_bytes()
