"""Run the epm service: the HTTP API under /api and the MCP endpoint at /mcp.

    python -m service.service [--host 0.0.0.0] [--port 8000] [--memory]

Storage is Supabase and R2 as set in ``service/config.py``; ``--memory`` keeps everything
in memory instead (lost on exit), for trying the service locally.
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

from . import config
from .api import install_error_handlers, router
from .projects import Projects
from .store import BlobStore, MemoryBlobStore, MemoryMetaStore, MetaStore
from .tools import build_mcp

log = logging.getLogger("epm.service")


def create_app(
    meta: MetaStore, blobs: BlobStore, maintenance: bool = False, **options: Any
) -> FastAPI:
    """The service over any stores. ``maintenance`` runs the retention and backup job daily."""
    projects = Projects(meta, blobs, **options)
    mcp = build_mcp(projects)
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
        allow_origins=config.CORS_ORIGINS,
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


async def _maintain_forever(projects: Projects) -> None:
    while True:
        try:
            summary = await run_in_threadpool(projects.maintain)
            log.info("maintenance: %s", summary)
        except Exception:  # noqa: BLE001 - try again next time
            log.exception("maintenance failed")
        await asyncio.sleep(config.MAINTENANCE_INTERVAL)


def cloud_stores() -> tuple[MetaStore, BlobStore]:
    from .store.r2 import R2BlobStore
    from .store.supabase import SupabaseMetaStore

    if not config.configured():
        raise SystemExit(
            "service/config.py still has placeholder credentials: fill in Supabase and R2, "
            "or run with --memory"
        )
    meta = SupabaseMetaStore(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)
    blobs = R2BlobStore(
        config.R2_ACCOUNT_ID, config.R2_ACCESS_KEY_ID, config.R2_SECRET_ACCESS_KEY, config.R2_BUCKET
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
    if args.memory:
        meta, blobs = MemoryMetaStore(), MemoryBlobStore()
    else:
        meta, blobs = cloud_stores()
    app = create_app(meta, blobs, maintenance=not args.memory)
    # Render terminates TLS in front of the service, so trust its forwarded headers
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
