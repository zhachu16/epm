"""Check a deployed service end to end, leaving nothing behind.

    python -m service.smoke https://epm-service.onrender.com

Health, the example, a copy of it, a previewed and saved edit, a run, an export, MCP, and
finally deleting the copy. Exits non-zero at the first failure.
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.error
import urllib.request
from typing import Any

TAX = [{"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": 0.3}}]


class SmokeError(Exception):
    pass


def _request(method: str, url: str, body: Any = None) -> tuple[int, bytes, str]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raise SmokeError(f"{method} {url}: {e.code} {e.read()[:300]!r}") from None
    except urllib.error.URLError as e:
        raise SmokeError(f"{method} {url}: {e.reason}") from None


def _json(method: str, url: str, body: Any = None) -> Any:
    return json.loads(_request(method, url, body)[1])


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def run(base: str, say=print) -> None:
    base = base.rstrip("/")
    api = f"{base}/api"
    _check(_json("GET", f"{api}/health") == {"ok": True}, "health check failed")
    say("ok  health")
    ids = [p["id"] for p in _json("GET", f"{api}/projects")]
    _check("example-lbo" in ids, f"the example project is missing: {ids}")
    say("ok  example project listed")

    project = _json("POST", f"{api}/projects", {"name": "smoke test", "from": "example-lbo"})
    pid = project["id"]
    say(f"ok  copied the example as {pid}")
    try:
        preview = _json("POST", f"{api}/projects/{pid}/batches", {"ops": TAX, "mode": "dry_run"})
        _check(preview["ok"] and not preview["committed"], f"preview failed: {preview}")
        saved = _json("POST", f"{api}/projects/{pid}/batches", {"ops": TAX, "expected_version": 1})
        _check(saved["committed"] and saved["version"] == 2, f"save failed: {saved}")
        say("ok  previewed and saved an edit (version 2)")
        values = _json("POST", f"{api}/projects/{pid}/run", {"outputs": ["equity_irr"]})["values"]
        irr = values["equity_irr"]["value"]
        _check(isinstance(irr, float) and 0 < irr < 1, f"implausible equity_irr: {irr}")
        say(f"ok  ran the model (equity IRR {irr:.1%})")
        _, archive, kind = _request("GET", f"{api}/projects/{pid}/export")
        _check(archive[:2] == b"PK" and kind == "application/zip", "export is not an archive")
        say(f"ok  exported {len(archive):,} bytes")
        shown = asyncio.run(_mcp(base, pid))
        _check(shown == 0.3, f"MCP read tax_rate as {shown}")
        say("ok  MCP sees the saved edit")
    finally:
        _json("DELETE", f"{api}/projects/{pid}")
        say(f"ok  deleted {pid}")


async def _mcp(base: str, project_id: str) -> Any:
    from mcp import Client

    async with Client(f"{base}/mcp") as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        _check({"apply_batch", "undo", "rollback"} <= tools, f"MCP tools missing: {tools}")
        result = await client.call_tool("show", {"project_id": project_id, "name": "tax_rate"})
        _check(not result.is_error, f"MCP show failed: {result.content}")
        return result.structured_content["value"]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        run(args[0])
    except SmokeError as e:
        print(f"FAILED  {e}", file=sys.stderr)
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
