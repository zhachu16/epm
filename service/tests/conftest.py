from __future__ import annotations

import pytest
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
