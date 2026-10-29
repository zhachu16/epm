"""MCP tools over :class:`~service.projects.Projects`, served at ``/mcp``.

Every tool that works on an existing project takes its ``project_id``; only listing,
searching and creating projects don't. Failures come back as tool errors whose text starts
with the epm error code, e.g. ``read_only_project: ...``.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from epm.errors import EpmError
from epm.sources import SourceError

from . import deployment
from .projects import EXAMPLE_ID, Projects

INSTRUCTIONS = f"""\
epm models are named metrics with formulas (no cell references), evaluated as series over
axes such as fiscal years. Each project has a project_id; pass it to every tool that works
on a project. The example project '{EXAMPLE_ID}' is read-only: duplicate_project or
create_project(from_project='{EXAMPLE_ID}') makes an editable copy.

Ground rules:
1. Read before you write: list_objects, show, trace and show_table give names, units and
   structure. Never guess a metric name.
2. Change a model only with apply_batch. Related changes go in one batch; a batch is atomic.
3. Preview first: apply_batch with mode='dry_run' shows the diff and the effect on key
   outputs. Commit with mode='commit' once it looks right.
4. Name every assumption: create a metric for a number instead of burying it in a formula,
   and give metrics a unit and a folder.
5. Every commit saves a new version. undo goes back one change; rollback restores any
   version from list_versions. Neither rewrites history, so both can be undone too.
6. A project's name is its model's name: rename_project, or settings.name set in a batch,
   renames both. show(project_id, "settings") reads the settings.
"""


def _call(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Report epm errors as tool errors the model can read and act on."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except EpmError as e:
            detail = "".join(f"; {k}: {v}" for k, v in e.detail.items())
            raise ToolError(f"{e.code}: {e.message}{detail}") from None
        except SourceError as e:
            raise ToolError(f"{e.code}: {e.message}") from None

    return wrapper


def _base_url(ctx: Context | None) -> str:
    if deployment.PUBLIC_URL:
        return deployment.PUBLIC_URL.rstrip("/")
    request = getattr(ctx.request_context, "request", None) if ctx is not None else None
    headers = getattr(request, "headers", None)
    if not headers or "host" not in headers:
        return ""
    proto = headers.get("x-forwarded-proto", getattr(request.url, "scheme", "https"))
    return f"{proto}://{headers['host']}"


def _web_link(project_id: str) -> str | None:
    return f"{deployment.WEB_URL.rstrip('/')}/p/{project_id}" if deployment.WEB_URL else None


def build_mcp(projects: Projects) -> MCPServer:
    mcp = MCPServer("epm", instructions=INSTRUCTIONS)

    def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
        mcp.add_tool(_call(fn), name=fn.__name__, description=fn.__doc__)
        return fn

    # -- projects --------------------------------------------------------------------------

    @tool
    def list_projects(include_deleted: bool = False) -> list[dict[str, Any]]:
        """List projects, most recently changed first. include_deleted lists deleted
        projects instead (they can be restored for 30 days)."""
        return projects.list_projects(None, include_deleted)

    @tool
    def search_projects(query: str) -> list[dict[str, Any]]:
        """Find projects whose name or description contains query (case-insensitive)."""
        return projects.list_projects(query)

    @tool
    def create_project(
        name: str, description: str = "", from_project: str | None = None
    ) -> dict[str, Any]:
        """Create a project: an empty model, or a copy of from_project (e.g. the example,
        'example-lbo'). Returns the project with its project_id."""
        return projects.create(name, description, from_project)

    @tool
    def get_project(project_id: str) -> dict[str, Any]:
        """A project's name, description, current version and, if configured, its web link."""
        return {**projects.get(project_id), "web_url": _web_link(project_id)}

    @tool
    def rename_project(
        project_id: str, name: str | None = None, description: str | None = None
    ) -> dict[str, Any]:
        """Change a project's name or description. The name is also the model's own name
        (settings.name), so a new name saves a new version; setting settings.name with
        apply_batch renames the project the same way."""
        return projects.rename(project_id, name, description, source="mcp")

    @tool
    def duplicate_project(project_id: str, name: str | None = None) -> dict[str, Any]:
        """Copy a project (its current version) into a new, editable project."""
        return projects.duplicate(project_id, name)

    @tool
    def delete_project(project_id: str) -> dict[str, Any]:
        """Delete a project. It is kept for 30 days and can be brought back with
        undelete_project."""
        return projects.delete(project_id)

    @tool
    def undelete_project(project_id: str) -> dict[str, Any]:
        """Bring back a deleted project."""
        return projects.restore_project(project_id)

    # -- reading the model -----------------------------------------------------------------

    @tool
    def list_objects(
        project_id: str,
        kind: str = "metrics",
        search: str | None = None,
        folder: str | None = None,
    ) -> dict[str, Any]:
        """List a model's objects. kind: metrics, inputs, axes, sources, functions, tables,
        checks, circularities, analyses, files or builtins. search matches names, folders
        and descriptions; folder limits to a folder and its subfolders."""
        return {"kind": kind, "objects": projects.list_objects(project_id, kind, search, folder)}

    @tool
    def show(project_id: str, name: str) -> dict[str, Any]:
        """An object's definition and, for a metric, its value, axis, unit, status,
        dependencies (depends_on) and dependants (used_by). name="settings" shows the model's
        settings: name, currency, default_axis, strict_units and key_outputs."""
        return projects.show(project_id, name)

    @tool
    def trace(
        project_id: str,
        name: str,
        direction: Literal["upstream", "downstream"] = "upstream",
        depth: int | None = None,
    ) -> dict[str, Any]:
        """Walk dependencies: upstream (what the metric reads) or downstream (what reads it)."""
        return projects.trace(project_id, name, direction, depth)

    @tool
    def validate(project_id: str) -> dict[str, Any]:
        """Every diagnostic (errors, warnings, info) and check result for the model."""
        return projects.validate(project_id)

    @tool
    def show_table(project_id: str, name: str) -> dict[str, Any]:
        """A table's rows with their values for every period."""
        return projects.table(project_id, name)

    @tool
    def list_builtins() -> list[dict[str, Any]]:
        """The formula functions (prev, where, irr, ...) with signatures and descriptions."""
        return projects.builtins()

    # -- changing the model ----------------------------------------------------------------

    @tool
    def apply_batch(
        project_id: str,
        ops: list[dict[str, Any]],
        mode: Literal["dry_run", "commit"] = "dry_run",
        label: str | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Apply operations atomically. mode='dry_run' (the default) previews the definition
        diff and the effect on key outputs without saving; mode='commit' saves a new version.
        Each op is {"op": create|update|upsert|delete|rename, "kind": metric|axis|source|
        table|check|circularity|analysis|function|functions|file|settings, "name": ...,
        "def": {...} (create), "set": {...} (update/upsert), "to": ... (rename)}.
        Example: {"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.3}}.
        expected_version makes the commit fail if someone saved since that version."""
        return projects.apply(project_id, ops, mode, expected_version, label, "mcp")

    # -- calculating -----------------------------------------------------------------------

    @tool
    def run(
        project_id: str,
        overrides: dict[str, Any] | None = None,
        outputs: list[str] | None = None,
        save: bool = False,
    ) -> dict[str, Any]:
        """Evaluate the model, optionally with temporary overrides ({"interest_rate": 0.06}).
        The model itself never changes. outputs limits the values returned. save stores the
        result as a downloadable output."""
        return projects.run(project_id, overrides, outputs, save, "mcp")

    @tool
    def sweep(
        project_id: str,
        inputs: dict[str, list[float]],
        outputs: list[str],
        mode: Literal["grid", "zip"] = "grid",
        overrides: dict[str, Any] | None = None,
        save: bool = False,
    ) -> dict[str, Any]:
        """Run scenarios: inputs maps metrics to lists of values; grid runs every
        combination, zip pairs them by position. A series output needs a period, e.g.
        'tlb_close@2027'. save stores the results as a CSV output."""
        return projects.sweep(project_id, inputs, outputs, mode, overrides, save)

    @tool
    def run_analysis(
        project_id: str, name: str, save: bool = False, plot: bool = False
    ) -> dict[str, Any]:
        """Run a saved analysis. save stores its results as a CSV output, and with plot its
        chart as a PNG output."""
        return projects.run_analysis(project_id, name, save, plot)

    # -- files, outputs and exports --------------------------------------------------------

    @tool
    def list_files(project_id: str) -> list[dict[str, Any]]:
        """The project's input files (under inputs/)."""
        return projects.list_files(project_id)

    @tool
    def list_outputs(project_id: str, ctx: Context) -> list[dict[str, Any]]:
        """Saved outputs (run and sweep results, analysis results and plots), with download
        URLs."""
        base = _base_url(ctx)
        return [
            {**o, "download_url": f"{base}/api/projects/{project_id}/outputs/{o['id']}"}
            for o in projects.list_outputs(project_id)
        ]

    @tool
    def export_project(project_id: str, ctx: Context, version: int | None = None) -> dict[str, Any]:
        """A download URL for the project as a .epm archive (current version by default)."""
        projects.get(project_id)
        base = _base_url(ctx)
        query = f"?version={version}" if version is not None else ""
        return {"download_url": f"{base}/api/projects/{project_id}/export{query}"}

    # -- versions --------------------------------------------------------------------------

    @tool
    def list_versions(project_id: str) -> list[dict[str, Any]]:
        """Saved versions, newest first: number, label, source (web, mcp, api, restore, ...)
        and time. Every commit makes one."""
        return projects.list_versions(project_id)

    @tool
    def undo(project_id: str) -> dict[str, Any]:
        """Go back one change, by saving the previous version again as a new version.
        Calling it again keeps going back."""
        return projects.undo(project_id, source="mcp")

    @tool
    def rollback(project_id: str, version: int) -> dict[str, Any]:
        """Restore an earlier version (from list_versions) by saving it as a new version."""
        return projects.restore(project_id, version, source="mcp")

    return mcp
