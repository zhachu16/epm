from __future__ import annotations

import contextlib
import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

from service.projects import EXAMPLE_ID, Projects
from service.service import create_app, gated
from service.store import MemoryBlobStore, MemoryMetaStore
from service.store.sqlite import SqliteMetaStore


@pytest.fixture
def stores() -> tuple[MemoryMetaStore, MemoryBlobStore]:
    return MemoryMetaStore(), MemoryBlobStore()


@pytest.fixture
def projects(stores) -> Projects:
    p = Projects(*stores)
    p.seed_example()
    return p


@pytest.fixture
def atlas(projects) -> str:
    """An editable copy of the example."""
    return projects.duplicate(EXAMPLE_ID, "Atlas")["id"]


@pytest.fixture
def client(stores):
    with TestClient(create_app(*stores)) as c:
        yield c


TOKEN = "t0ken_" + "a" * 30


@contextlib.contextmanager
def serving(app):
    """``app`` on a free local port, for tests that need real HTTP."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)


@pytest.fixture(scope="module")
def service_url():
    """A live service on in-memory storage."""
    with serving(create_app(MemoryMetaStore(), MemoryBlobStore())) as url:
        yield url


@pytest.fixture
def deployed_url(tmp_path):
    """A live service as it is deployed: records in SQLite, behind the access token ``TOKEN``.
    The address has no token in it."""
    meta = SqliteMetaStore(tmp_path / "data" / "epm.db")
    app = create_app(meta, MemoryBlobStore(), token=TOKEN)
    with serving(gated(app, TOKEN)) as url:
        yield url
    meta.close()
