from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import uvicorn
from mcp import Client

from service.projects import EXAMPLE_ID
from service.service import create_app
from service.store import MemoryBlobStore, MemoryMetaStore
from service.tools import build_mcp

TAX = {"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.3}}
OUT = ["moic"]
NO_PROJECT_ID = {"list_projects", "search_projects", "create_project", "list_builtins"}


def payload(result):
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


def call(mcp, steps):
    """Run tool calls in one client session; each step is (tool, arguments)."""

    async def go():
        async with Client(mcp) as client:
            return [await client.call_tool(name, args) for name, args in steps]

    return asyncio.run(go())


def test_every_project_tool_requires_a_project_id(projects):
    async def tools():
        async with Client(build_mcp(projects)) as client:
            return (await client.list_tools()).tools

    for tool in asyncio.run(tools()):
        required = tool.input_schema.get("required", [])
        if tool.name in NO_PROJECT_ID:
            assert "project_id" not in required, tool.name
        else:
            assert "project_id" in required, tool.name


def test_copy_preview_commit_and_undo(projects):
    mcp = build_mcp(projects)
    [created] = call(mcp, [("create_project", {"name": "Agent copy", "from_project": EXAMPLE_ID})])
    pid = payload(created)["id"]
    preview, committed, shown, undone, versions = call(
        mcp,
        [
            ("apply_batch", {"project_id": pid, "ops": [TAX]}),  # dry_run by default
            ("apply_batch", {"project_id": pid, "ops": [TAX], "mode": "commit"}),
            ("show", {"project_id": pid, "name": "tax_rate"}),
            ("undo", {"project_id": pid}),
            ("list_versions", {"project_id": pid}),
        ],
    )
    assert not payload(preview)["committed"] and payload(preview)["impact"]
    assert payload(committed)["version"] == 2
    assert payload(shown)["value"] == 0.3
    assert payload(undone)["restored"] == 1
    assert [v["source"] for v in payload(versions)] == ["mcp", "mcp", "duplicate"]
    assert projects.show(pid, "tax_rate")["value"] == 0.25
    [rolled] = call(mcp, [("rollback", {"project_id": pid, "version": 2})])
    assert payload(rolled)["restored"] == 2 and projects.show(pid, "tax_rate")["value"] == 0.3


def test_errors_name_their_code(projects):
    mcp = build_mcp(projects)
    read_only, unknown = call(
        mcp,
        [
            ("apply_batch", {"project_id": EXAMPLE_ID, "ops": [TAX], "mode": "commit"}),
            ("show", {"project_id": "p_missing", "name": "x"}),
        ],
    )
    assert read_only.is_error and "read_only_project:" in read_only.content[0].text
    assert unknown.is_error and "unknown_project:" in unknown.content[0].text


def test_reading_and_running(projects):
    mcp = build_mcp(projects)
    listed, searched, tables, trace, ran, swept, found = call(
        mcp,
        [
            ("list_projects", {}),
            ("list_objects", {"project_id": EXAMPLE_ID, "kind": "metrics", "search": "irr"}),
            ("show_table", {"project_id": EXAMPLE_ID, "name": "returns"}),
            ("trace", {"project_id": EXAMPLE_ID, "name": "equity_irr", "depth": 1}),
            (
                "run",
                {"project_id": EXAMPLE_ID, "overrides": {"tax_rate": 0.3}, "outputs": ["moic"]},
            ),
            (
                "sweep",
                {"project_id": EXAMPLE_ID, "inputs": {"tax_rate": [0.2, 0.3]}, "outputs": OUT},
            ),
            ("search_projects", {"query": "lbo"}),
        ],
    )
    assert [p["id"] for p in payload(listed)] == [EXAMPLE_ID]
    assert "equity_irr" in [m["name"] for m in payload(searched)["objects"]]
    assert payload(tables)["rows"]
    assert payload(trace)["metrics"]
    assert set(payload(ran)["values"]) == {"moic"}
    assert len(payload(swept)["rows"]) == 2
    assert payload(found)[0]["id"] == EXAMPLE_ID


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_mcp_over_http_shares_projects_with_the_api():
    meta, blobs = MemoryMetaStore(), MemoryBlobStore()
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(meta, blobs), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 30
        while not server.started and time.time() < deadline:
            time.sleep(0.05)

        async def go():
            async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                created = await client.call_tool(
                    "create_project", {"name": "Over HTTP", "from_project": EXAMPLE_ID}
                )
                pid = payload(created)["id"]
                await client.call_tool(
                    "apply_batch", {"project_id": pid, "ops": [TAX], "mode": "commit"}
                )
                exported = await client.call_tool("export_project", {"project_id": pid})
                return pid, payload(exported)

        pid, exported = asyncio.run(go())
        assert meta.get_project(pid).current_version == 2  # the API's store saw the commit
        assert exported["download_url"] == f"http://127.0.0.1:{port}/api/projects/{pid}/export"
    finally:
        server.should_exit = True
        thread.join(10)


def test_get_project_links_to_the_web_app(projects):
    [shown] = call(build_mcp(projects), [("get_project", {"project_id": EXAMPLE_ID})])
    assert payload(shown)["web_url"] == f"https://epm-alpha.nommo.cloud/p/{EXAMPLE_ID}"
