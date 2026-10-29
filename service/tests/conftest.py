from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

from service.projects import EXAMPLE_ID, Projects
from service.service import create_app
from service.store import MemoryBlobStore, MemoryMetaStore


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


@pytest.fixture(scope="module")
def service_url():
    """A live service on in-memory storage, for tests that need real HTTP."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(MemoryMetaStore(), MemoryBlobStore())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)
