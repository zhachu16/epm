"""HTTP and websocket API over a :class:`Session`, plus the built frontend.

Requests must come from localhost (the Host header is checked against DNS rebinding) and,
except for ``/api/health``, carry the session token: the ``X-EPM-Token`` header, or a
``token`` query parameter for websockets. The served ``index.html`` embeds the token.
"""

from __future__ import annotations

import contextlib
import html
import json
import secrets
import shlex
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..cli.output import clean
from ..errors import EpmError
from ..ops import Op, ops_from_model
from ..sources import SourceError
from .session import Session

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


class BatchRequest(BaseModel):
    ops: list[dict[str, Any]] = Field(min_length=1)
    label: str | None = None
    mode: Literal["commit", "dry_run", "propose"] = "commit"
    outputs: list[str] | None = None
    allow_errors: bool = False


class ModelRequest(BaseModel):
    model: dict[str, Any]
    mode: Literal["commit", "dry_run", "propose"] = "commit"
    allow_errors: bool = False


class FunctionsRequest(BaseModel):
    code: str
    mode: Literal["commit", "dry_run", "propose"] = "commit"


class RunRequest(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)
    outputs: list[str] | None = None


class SweepRequest(BaseModel):
    inputs: dict[str, list[float]]
    outputs: list[str]
    mode: Literal["grid", "zip"] = "grid"
    overrides: dict[str, Any] = Field(default_factory=dict)


class CliRequest(BaseModel):
    argv: list[str]


def _host(value: str) -> str:
    if value.startswith("["):
        return value.split("]")[0] + "]"
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def find_frontend(explicit: Path | None = None) -> Path | None:
    """The built frontend: an explicit path, $EPM_FRONTEND, or frontend/dist in a checkout."""
    import os

    candidates = [explicit] if explicit else []
    if os.environ.get("EPM_FRONTEND"):
        candidates.append(Path(os.environ["EPM_FRONTEND"]))
    candidates.append(Path(__file__).resolve().parents[2] / "frontend" / "dist")
    for c in candidates:
        if c is not None and (c / "index.html").is_file():
            return c
    return None


def create_app(
    session: Session,
    frontend: Path | None = None,
    auth: bool = True,
    allowed_hosts: set[str] | None = None,
    on_shutdown=None,
) -> FastAPI:
    hosts = LOCAL_HOSTS | (allowed_hosts or set())

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        session.start_watching()
        try:
            yield
        finally:
            # runs before uvicorn re-raises SIGTERM/SIGINT, unlike code after server.run()
            if on_shutdown is not None:
                on_shutdown()

    app = FastAPI(
        title="epm", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )

    def authorized(token: str | None) -> bool:
        return not auth or (token is not None and secrets.compare_digest(token, session.token))

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if _host(request.headers.get("host", "")) not in hosts:
            return JSONResponse(
                {
                    "ok": False,
                    "errors": [
                        {"code": "bad_host", "message": "requests must come from localhost"}
                    ],
                },
                status_code=403,
            )
        path = request.url.path
        if path.startswith("/api/") and path != "/api/health":
            token = request.headers.get("x-epm-token") or request.query_params.get("token")
            if not authorized(token):
                return JSONResponse(
                    {
                        "ok": False,
                        "errors": [
                            {"code": "unauthorized", "message": "missing or wrong session token"}
                        ],
                    },
                    status_code=401,
                )
        return await call_next(request)

    @app.exception_handler(EpmError)
    async def epm_error(request: Request, e: EpmError):
        return JSONResponse(
            {"ok": False, "errors": [clean({"code": e.code, "message": e.message, **e.detail})]},
            status_code=400,
        )

    @app.exception_handler(SourceError)
    async def source_error(request: Request, e: SourceError):
        return JSONResponse(
            {"ok": False, "errors": [{"code": e.code, "message": e.message}]}, status_code=400
        )

    # -- reading -------------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "model": str(session.path), "version": session.ws.version}

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return session.snapshot()

    @app.get("/api/trace/{name}")
    def trace(name: str, direction: str = "upstream", depth: int | None = None) -> dict[str, Any]:
        with session.lock:
            return {"metrics": session.ws.trace(name, direction, depth)}

    @app.get("/api/sources/{name}/preview")
    def source_preview(name: str, limit: int = 50) -> dict[str, Any]:
        with session.lock:
            table = session.ws.engine.sources.table(name)
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

    # -- changing ------------------------------------------------------------------------

    def _apply(ops: list, label: str | None, mode: str, outputs=None, allow_errors=False) -> dict:
        batch = {"ops": [o.to_dict() if isinstance(o, Op) else o for o in ops], "label": label}
        result = session.mutate(lambda ws: ws.apply(batch, mode, outputs, allow_errors))
        return clean(result.to_dict())

    @app.post("/api/batches")
    def apply_batch(body: BatchRequest) -> dict[str, Any]:
        return _apply(body.ops, body.label, body.mode, body.outputs, body.allow_errors)

    @app.put("/api/model")
    def put_model(body: ModelRequest) -> dict[str, Any]:
        with session.lock:
            ops = ops_from_model(session.ws.state(), body.model)
        if not ops:
            return {
                "ok": True,
                "mode": body.mode,
                "committed": False,
                "changes": [],
                "errors": [],
                "label": "no changes",
            }
        return _apply(ops, "edit model.json", body.mode, None, body.allow_errors)

    @app.put("/api/functions")
    def put_functions(body: FunctionsRequest) -> dict[str, Any]:
        op = Op(op="update", kind="functions", set={"code": body.code})
        return _apply([op], "edit functions.py", body.mode)

    @app.post("/api/pending/{pending_id}/commit")
    def commit_pending(pending_id: str) -> dict[str, Any]:
        return clean(session.mutate(lambda ws: ws.commit_pending(pending_id)).to_dict())

    @app.post("/api/pending/{pending_id}/reject")
    def reject_pending(pending_id: str) -> dict[str, Any]:
        session.mutate(lambda ws: ws.reject_pending(pending_id))
        return {"ok": True}

    @app.post("/api/undo")
    def undo() -> dict[str, Any]:
        return session.mutate(lambda ws: ws.undo()).to_dict()

    @app.post("/api/redo")
    def redo() -> dict[str, Any]:
        return session.mutate(lambda ws: ws.redo()).to_dict()

    @app.post("/api/rollback/{entry_id}")
    def rollback(entry_id: str) -> dict[str, Any]:
        undone = session.mutate(lambda ws: ws.rollback(entry_id))
        return {"undone": [e.to_dict() for e in undone]}

    # -- calculating ---------------------------------------------------------------------

    @app.post("/api/run")
    def run(body: RunRequest) -> dict[str, Any]:
        with session.lock:
            return clean(session.ws.run(body.overrides, body.outputs).to_dict())

    @app.post("/api/sweep")
    def sweep(body: SweepRequest) -> dict[str, Any]:
        with session.lock:
            result = session.ws.sweep(body.inputs, body.outputs, body.mode, body.overrides)
        session.log("info", f"sweep: {len(result.rows)} scenarios")
        return clean(result.to_dict())

    @app.post("/api/analyses/{name}/run")
    def run_analysis(name: str) -> dict[str, Any]:
        with session.lock:
            result = session.ws.run_analysis(name)
        session.log("info", f"analysis '{name}': {len(result.sweep.rows)} scenarios")
        return clean(result.to_dict())

    @app.post("/api/cli")
    def cli(body: CliRequest) -> dict[str, Any]:
        return session.run_cli(body.argv, source="cli")

    # -- websockets ----------------------------------------------------------------------

    async def accept(ws: WebSocket) -> bool:
        if _host(ws.headers.get("host", "")) not in hosts or not authorized(
            ws.query_params.get("token")
        ):
            await ws.close(code=4401)
            return False
        await ws.accept()
        return True

    @app.websocket("/api/events")
    async def events(ws: WebSocket) -> None:
        if not await accept(ws):
            return
        queue = session.events.subscribe()
        try:
            await ws.send_json({"type": "hello", "version": session.ws.version})
            while True:
                event = await queue.get()
                await ws.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            session.events.unsubscribe(queue)

    @app.websocket("/api/terminal")
    async def terminal(ws: WebSocket) -> None:
        """Runs ``fm`` commands only; this is not a shell."""
        if not await accept(ws):
            return
        try:
            while True:
                message = await ws.receive_json()
                line = str(message.get("command", "")).strip()
                try:
                    argv = shlex.split(line)
                except ValueError as e:
                    await ws.send_json({"stdout": "", "stderr": f"error: {e}\n", "exit_code": 2})
                    continue
                if argv and argv[0] == "fm":
                    argv = argv[1:]
                if argv and argv[0] in ("serve", "init", "pack", "unpack"):
                    await ws.send_json(
                        {
                            "stdout": "",
                            "stderr": f"error: '{argv[0]}' is not available here\n",
                            "exit_code": 2,
                        }
                    )
                    continue
                result = await run_in_threadpool(session.run_cli, argv, "terminal")
                await ws.send_json(result)
        except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
            pass

    # -- frontend ------------------------------------------------------------------------

    dist = find_frontend(frontend)

    def index() -> HTMLResponse:
        if dist is None:
            return HTMLResponse(
                "<!doctype html><title>epm</title><body style='font-family:sans-serif'>"
                "<h1>epm session is running</h1><p>The web UI is not built. Run "
                "<code>npm ci &amp;&amp; npm run build</code> in <code>frontend/</code>, "
                "then reload.</p>"
                "<p>API docs: <a href='/api/docs'>/api/docs</a></p></body>"
            )
        page = (dist / "index.html").read_text(encoding="utf-8")
        meta = f'<meta name="epm-token" content="{html.escape(session.token if auth else "")}">'
        return HTMLResponse(
            page.replace("<head>", f"<head>\n    {meta}", 1), headers={"Cache-Control": "no-store"}
        )

    if dist is not None and (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        if dist is not None and path:
            candidate = (dist / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(dist.resolve()):
                return FileResponse(candidate)
        return index()

    return app
