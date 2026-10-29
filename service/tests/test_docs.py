"""The user guide's examples must work as written.

Every ``bash`` block that uses ``$EPM`` runs, in order, as one script against a live
service on in-memory storage, with ``curl`` failing on any HTTP error. Then every ``python``
block that reads ``EPM`` from the environment runs with the project the script created.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from service.service import create_app
from service.store import MemoryBlobStore, MemoryMetaStore

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "user-guide.md"

pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("curl") and shutil.which("jq")),
    reason="needs bash, curl and jq",
)


def blocks(lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", GUIDE.read_text(), re.S)


def test_guide_examples_run(service_url, tmp_path):
    script = [b for b in blocks("bash") if "$EPM" in b]
    programs = [b for b in blocks("python") if 'os.environ["EPM"]' in b]
    assert len(script) >= 10 and programs

    prelude = (
        'set -euo pipefail\ncurl() { command curl --fail-with-body --silent --show-error "$@"; }\n'
    )
    epilogue = '\necho "PROJECT=$PROJECT"\n'
    env = {**os.environ, "EPM": service_url}
    result = subprocess.run(
        ["bash", "-c", prelude + "\n".join(script) + epilogue],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    project = re.search(r"^PROJECT=(\S+)$", result.stdout, re.M)
    assert project and project.group(1).startswith("p_"), result.stdout

    for program in programs:
        run = subprocess.run(
            [sys.executable, "-c", program],
            cwd=tmp_path,
            env={**env, "PROJECT": project.group(1)},
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert run.returncode == 0, run.stdout + run.stderr


def test_guide_lists_every_mcp_tool_and_route():
    import asyncio

    from mcp import Client

    from service.projects import Projects
    from service.tools import build_mcp

    async def tools():
        async with Client(build_mcp(Projects(MemoryMetaStore(), MemoryBlobStore()))) as c:
            return [t.name for t in (await c.list_tools()).tools]

    text = GUIDE.read_text()
    assert [t for t in asyncio.run(tools()) if f"`{t}`" not in text] == []
    app = create_app(MemoryMetaStore(), MemoryBlobStore())
    paths = {
        r.path.removeprefix("/api")
        for r in app.routes
        if getattr(r, "path", "").startswith("/api/")
        and "docs" not in r.path
        and "openapi" not in r.path
    }
    missing = [p for p in sorted(paths) if re.sub(r"\{(\w+)(:path)?\}", r"{\1}", p) not in text]
    assert missing == []
