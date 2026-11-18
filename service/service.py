"""Run the epm service: the HTTP API under /api and the MCP endpoint at /mcp.

    python -m service.service [--host 0.0.0.0] [--port 8000] [--memory]

Storage is a local SQLite file for the project records and Cloudflare R2 for the files, and
the service is reachable only at URLs that carry its access token; all of it is set through
environment variables (``service/config.py``). ``--memory`` keeps everything in memory
instead (lost on exit) and needs no settings, for trying the service locally.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from . import config, deployment
from .api import install_error_handlers, router
from .auth import HideToken, PathToken, check_token
from .projects import Projects
from .store import BlobStore, MemoryBlobStore, MemoryMetaStore, MetaStore
from .tools import build_mcp

log = logging.getLogger("epm.service")


def create_app(
    meta: MetaStore, blobs: BlobStore, maintenance: bool = False, token: str = "", **options: Any
) -> FastAPI:
    """The service over any stores. ``maintenance`` runs the retention and backup job daily.
    ``token`` is the access token the app is served behind (``gated``), for links in MCP
    results."""
    projects = Projects(meta, blobs, **options)
    mcp = build_mcp(projects, token)
    # stateless: no MCP sessions to lose when the host restarts or scales the service
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, host="0.0.0.0"
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await run_in_threadpool(projects.seed_example)
        task = asyncio.create_task(_maintain_forever(projects)) if maintenance else None
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(
        title="epm service",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.projects = projects
    app.add_middleware(
        CORSMiddleware,
        allow_origins=deployment.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )
    install_error_handlers(app)
    app.include_router(router(projects))
    # the MCP route itself rather than a mount, so /mcp answers without a redirect to /mcp/;
    # its session manager runs in the lifespan above
    app.router.routes.extend(mcp_app.routes)
    return app


def gated(app: FastAPI, token: str):
    """``app`` reachable only at URLs that carry ``token`` (see auth.py)."""
    return PathToken(app, token)


async def _maintain_forever(projects: Projects) -> None:
    while True:
        try:
            summary = await run_in_threadpool(projects.maintain)
            log.info("maintenance: %s", summary)
        except Exception:  # noqa: BLE001 - try again next time
            log.exception("maintenance failed")
        await asyncio.sleep(deployment.MAINTENANCE_INTERVAL)


def stores(settings: config.Config) -> tuple[MetaStore, BlobStore]:
    """The deployment's storage: records in SQLite on this machine, files in R2."""
    from .store.r2 import R2BlobStore
    from .store.sqlite import SqliteMetaStore

    meta = SqliteMetaStore(settings.database)
    blobs = R2BlobStore(
        settings.r2_account_id,
        settings.r2_access_key_id,
        settings.r2_secret_access_key,
        settings.r2_bucket,
    )
    return meta, blobs


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(prog="python -m service.service", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--memory", action="store_true", help="keep everything in memory")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    settings = config.load()
    try:
        token = check_token(settings.api_token) if settings.api_token else ""
    except ValueError as e:
        raise SystemExit(str(e)) from None
    if args.memory:
        meta, blobs = MemoryMetaStore(), MemoryBlobStore()
    else:
        if settings.missing():
            raise SystemExit(
                f"set {', '.join(settings.missing())} in the environment (see "
                "service/config.py), or run with --memory"
            )
        meta, blobs = stores(settings)
        log.info("records in %s, files in R2 bucket '%s'", settings.database, settings.r2_bucket)
    app: Any = create_app(meta, blobs, maintenance=not args.memory, token=token)
    if token:
        app = gated(app, token)
        for name in ("uvicorn.access", "uvicorn.error"):
            logging.getLogger(name).addFilter(HideToken(token))
    else:
        log.warning("no API_TOKEN: the service is open to anyone who can reach it")
    # a reverse proxy terminates TLS in front of the service, so trust its forwarded headers
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
