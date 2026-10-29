"""``fm serve``: a local session server for the web UI, the CLI and agents."""

from __future__ import annotations

import socket
import webbrowser
from collections.abc import Callable
from pathlib import Path

from ..errors import EpmError
from .app import create_app, find_frontend
from .lockfile import base_url, live_session, lock_path, remove_lock, write_lock
from .session import Session, workspace_snapshot

__all__ = ["Session", "create_app", "find_frontend", "serve", "workspace_snapshot"]


def serve(
    model: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    auth: bool = True,
    frontend: Path | None = None,
    announce: Callable[[str], None] = print,
) -> None:
    """Serve ``model`` until interrupted. While it runs, ``fm`` commands are forwarded here."""
    import uvicorn

    path = Path(model).resolve()
    existing = live_session(path)
    if existing is not None:
        raise EpmError("already_serving", f"{path.name} is already served at {base_url(existing)}")
    session = Session(path)
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as e:
        session.close(save_cache=False)
        raise EpmError("port_in_use", f"cannot listen on {host}:{port}: {e.strerror}") from None
    port = sock.getsockname()[1]

    def shutdown() -> None:
        remove_lock(path, session.token)
        session.close()

    app = create_app(session, frontend, auth, allowed_hosts={host}, on_shutdown=shutdown)
    write_lock(path, host, port, session.token)
    url = base_url({"host": host, "port": port})
    announce(f"serving {path.name} at {url}")
    if find_frontend(frontend) is None:
        announce("the web UI is not built: run `npm ci && npm run build` in frontend-local/")
    announce(f"fm commands for this model now run in this session (lock: {lock_path(path).name})")
    if open_browser:
        webbrowser.open(url + "/")
    config = uvicorn.Config(app, log_level="warning", lifespan="on")
    try:
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        shutdown()
        sock.close()
