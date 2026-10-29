"""Serve the built web app and an in-memory epm service on one port, for the end-to-end tests.

Usage: python e2e/serve.py [PORT]. Nothing is stored: every run starts from the example.
"""

import sys
from pathlib import Path

import uvicorn
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from service.service import create_app  # noqa: E402
from service.store import MemoryBlobStore, MemoryMetaStore  # noqa: E402

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8898
dist = ROOT / "frontend-web" / "dist"
app = create_app(MemoryMetaStore(), MemoryBlobStore())
app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    candidate = (dist / path).resolve()
    if path and candidate.is_file() and candidate.is_relative_to(dist):
        return FileResponse(candidate)
    return FileResponse(dist / "index.html")


uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
