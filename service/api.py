"""The HTTP API: ``/api/projects/...`` over :class:`~service.projects.Projects`.

Successful responses are the result itself; failures are ``{"ok": false, "errors": [{code,
message, ...}]}`` with a status that follows the code (404 for unknown things, 403 for the
read-only example, 409 for a version conflict, 400 otherwise), as ``fm serve`` does.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from epm.cli.output import clean
from epm.errors import EpmError
from epm.sources import SourceError

from .actor import get_actor
from .projects import EPM_TYPE, Projects

MAX_UPLOAD_BYTES = 50 * 1024**2

STATUS = {
    "unknown_project": 404,
    "unknown_version": 404,
    "unknown_output": 404,
    "unknown_file": 404,
    "not_found": 404,
    "read_only_project": 403,
    "version_conflict": 409,
    "payload_too_large": 413,
}


class CreateProject(BaseModel):
    name: str = ""
    description: str = ""
    from_project: str | None = Field(default=None, alias="from")


class UpdateProject(BaseModel):
    name: str | None = None
    description: str | None = None


class DuplicateProject(BaseModel):
    name: str | None = None
    description: str | None = None


class BatchRequest(BaseModel):
    ops: list[dict[str, Any]] = Field(min_length=1)
    label: str | None = None
    mode: Literal["commit", "dry_run"] = "commit"
    outputs: list[str] | None = None
    allow_errors: bool = False
    expected_version: int | None = None


class ModelRequest(BaseModel):
    model: dict[str, Any]
    mode: Literal["commit", "dry_run"] = "commit"
    allow_errors: bool = False
    expected_version: int | None = None


class FunctionsRequest(BaseModel):
    code: str
    mode: Literal["commit", "dry_run"] = "commit"
    expected_version: int | None = None


class RunRequest(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)
    outputs: list[str] | None = None
    save: bool = False


class SweepRequest(BaseModel):
    inputs: dict[str, list[float]]
    outputs: list[str]
    mode: Literal["grid", "zip"] = "grid"
    overrides: dict[str, Any] = Field(default_factory=dict)
    save: bool = False


class AnalysisRequest(BaseModel):
    save: bool = False
    plot: bool = False


class VersionedRequest(BaseModel):
    expected_version: int | None = None


def _source(client: str | None) -> str:
    return "web" if client == "web" else "api"


def _download(data: bytes, filename: str, content_type: str) -> Response:
    return Response(
        data,
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


async def _body(request: Request) -> bytes:
    data = await request.body()
    if len(data) > MAX_UPLOAD_BYTES:
        raise EpmError(
            "payload_too_large", f"uploads are limited to {MAX_UPLOAD_BYTES // 1024**2} MB"
        )
    return data


def error_response(e: EpmError) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "errors": [clean({"code": e.code, "message": e.message, **e.detail})]},
        status_code=STATUS.get(e.code, 400),
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(EpmError)
    async def epm_error(request: Request, e: EpmError):
        return error_response(e)

    @app.exception_handler(SourceError)
    async def source_error(request: Request, e: SourceError):
        return JSONResponse(
            {"ok": False, "errors": [{"code": e.code, "message": e.message}]}, status_code=400
        )


def router(projects: Projects) -> APIRouter:
    api = APIRouter(prefix="/api", dependencies=[Depends(get_actor)])
    Client = Header(default=None, alias="X-EPM-Client")

    @api.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @api.get("/builtins")
    def builtins() -> list[dict[str, Any]]:
        return projects.builtins()

    # -- projects --------------------------------------------------------------------------

    @api.get("/projects")
    def list_projects(q: str | None = None, deleted: bool = False) -> list[dict[str, Any]]:
        return projects.list_projects(q, deleted)

    @api.post("/projects", status_code=201)
    def create_project(body: CreateProject) -> dict[str, Any]:
        return projects.create(body.name, body.description, body.from_project)

    @api.post("/projects/import", status_code=201)
    async def import_project(
        request: Request, name: str | None = None, description: str = ""
    ) -> dict[str, Any]:
        data = await _body(request)
        return await run_in_threadpool(projects.import_epm, data, name, description)

    @api.get("/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        return projects.get(project_id)

    @api.patch("/projects/{project_id}")
    def update_project(
        project_id: str, body: UpdateProject, client: str | None = Client
    ) -> dict[str, Any]:
        return projects.rename(project_id, body.name, body.description, _source(client))

    @api.delete("/projects/{project_id}")
    def delete_project(project_id: str) -> dict[str, Any]:
        return projects.delete(project_id)

    @api.post("/projects/{project_id}/duplicate", status_code=201)
    def duplicate_project(project_id: str, body: DuplicateProject | None = None) -> dict[str, Any]:
        body = body or DuplicateProject()
        return projects.duplicate(project_id, body.name, body.description)

    @api.post("/projects/{project_id}/restore")
    def restore_project(project_id: str) -> dict[str, Any]:
        return projects.restore_project(project_id)

    @api.get("/projects/{project_id}/export")
    def export_project(project_id: str, version: int | None = None) -> Response:
        filename, data = projects.export_epm(project_id, version)
        return _download(data, filename, EPM_TYPE)

    # -- reading the model -----------------------------------------------------------------

    @api.get("/projects/{project_id}/state")
    def state(project_id: str, version: int | None = None) -> dict[str, Any]:
        return projects.state(project_id, version)

    @api.get("/projects/{project_id}/objects")
    def list_objects(
        project_id: str,
        kind: str = "metrics",
        search: str | None = None,
        folder: str | None = None,
    ) -> Any:
        return projects.list_objects(project_id, kind, search, folder)

    @api.get("/projects/{project_id}/show/{name}")
    def show(project_id: str, name: str) -> Any:
        return projects.show(project_id, name)

    @api.get("/projects/{project_id}/trace/{name}")
    def trace(
        project_id: str, name: str, direction: str = "upstream", depth: int | None = None
    ) -> Any:
        return projects.trace(project_id, name, direction, depth)

    @api.get("/projects/{project_id}/validate")
    def validate(project_id: str) -> Any:
        return projects.validate(project_id)

    @api.get("/projects/{project_id}/tables/{name}")
    def table(project_id: str, name: str) -> Any:
        return projects.table(project_id, name)

    @api.get("/projects/{project_id}/sources/{name}/preview")
    def source_preview(project_id: str, name: str, limit: int = 50) -> dict[str, Any]:
        return projects.source_preview(project_id, name, limit)

    # -- changing the model ----------------------------------------------------------------

    @api.post("/projects/{project_id}/batches")
    def apply_batch(
        project_id: str, body: BatchRequest, client: str | None = Client
    ) -> dict[str, Any]:
        return projects.apply(
            project_id,
            body.ops,
            body.mode,
            body.expected_version,
            body.label,
            _source(client),
            body.allow_errors,
            body.outputs,
        )

    @api.put("/projects/{project_id}/model")
    def put_model(project_id: str, body: ModelRequest, client: str | None = Client) -> Any:
        return projects.put_model(
            project_id,
            body.model,
            body.mode,
            body.expected_version,
            _source(client),
            body.allow_errors,
        )

    @api.put("/projects/{project_id}/functions")
    def put_functions(
        project_id: str, body: FunctionsRequest, client: str | None = Client
    ) -> dict[str, Any]:
        return projects.put_functions(
            project_id, body.code, body.mode, body.expected_version, _source(client)
        )

    # -- calculating -----------------------------------------------------------------------

    @api.post("/projects/{project_id}/run")
    def run(project_id: str, body: RunRequest | None = None) -> dict[str, Any]:
        body = body or RunRequest()
        return projects.run(project_id, body.overrides, body.outputs, body.save)

    @api.post("/projects/{project_id}/sweep")
    def sweep(project_id: str, body: SweepRequest) -> dict[str, Any]:
        return projects.sweep(
            project_id, body.inputs, body.outputs, body.mode, body.overrides, body.save
        )

    @api.post("/projects/{project_id}/analyses/{name}/run")
    def run_analysis(
        project_id: str, name: str, body: AnalysisRequest | None = None
    ) -> dict[str, Any]:
        body = body or AnalysisRequest()
        return projects.run_analysis(project_id, name, body.save, body.plot)

    @api.get("/projects/{project_id}/analyses/{name}/plot.png")
    def plot(project_id: str, name: str) -> Response:
        return Response(projects.plot(project_id, name), media_type="image/png")

    # -- files -----------------------------------------------------------------------------

    @api.get("/projects/{project_id}/files")
    def list_files(project_id: str, version: int | None = None) -> list[dict[str, Any]]:
        return projects.list_files(project_id, version)

    @api.get("/projects/{project_id}/files/{path:path}")
    def download_file(project_id: str, path: str, version: int | None = None) -> Response:
        data = projects.download_file(project_id, path, version)
        return _download(data, path.rsplit("/", 1)[-1], "application/octet-stream")

    @api.put("/projects/{project_id}/files/{path:path}")
    async def upload_file(
        project_id: str,
        path: str,
        request: Request,
        expected_version: int | None = None,
        client: str | None = Client,
    ) -> dict[str, Any]:
        data = await _body(request)
        return await run_in_threadpool(
            projects.upload_file, project_id, path, data, expected_version, _source(client)
        )

    @api.delete("/projects/{project_id}/files/{path:path}")
    def delete_file(
        project_id: str,
        path: str,
        expected_version: int | None = None,
        client: str | None = Client,
    ) -> dict[str, Any]:
        name = path if path.startswith("inputs/") else f"inputs/{path}"
        op = {"op": "delete", "kind": "file", "name": name}
        return projects.apply(
            project_id, [op], "commit", expected_version, f"delete {name}", _source(client)
        )

    # -- outputs ---------------------------------------------------------------------------

    @api.get("/projects/{project_id}/outputs")
    def list_outputs(project_id: str) -> list[dict[str, Any]]:
        return projects.list_outputs(project_id)

    @api.get("/projects/{project_id}/outputs/{output_id}")
    def download_output(project_id: str, output_id: str) -> Response:
        record, data = projects.download_output(project_id, output_id)
        return _download(data, record["name"], record["content_type"])

    @api.delete("/projects/{project_id}/outputs/{output_id}")
    def delete_output(project_id: str, output_id: str) -> dict[str, Any]:
        projects.delete_output(project_id, output_id)
        return {"ok": True}

    # -- versions --------------------------------------------------------------------------

    @api.get("/projects/{project_id}/versions")
    def list_versions(project_id: str) -> list[dict[str, Any]]:
        return projects.list_versions(project_id)

    @api.get("/projects/{project_id}/versions/{number}/export")
    def export_version(project_id: str, number: int) -> Response:
        filename, data = projects.export_epm(project_id, number)
        return _download(data, filename, EPM_TYPE)

    @api.post("/projects/{project_id}/versions/{number}/restore")
    def restore_version(
        project_id: str,
        number: int,
        body: VersionedRequest | None = None,
        client: str | None = Client,
    ) -> dict[str, Any]:
        expected = body.expected_version if body else None
        return projects.restore(project_id, number, expected, _source(client))

    @api.post("/projects/{project_id}/undo")
    def undo(
        project_id: str, body: VersionedRequest | None = None, client: str | None = Client
    ) -> dict[str, Any]:
        expected = body.expected_version if body else None
        return projects.undo(project_id, expected, _source(client))

    return api
