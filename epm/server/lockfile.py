"""The lock file that tells CLI commands a session server owns a model.

It sits next to the model (``.deal.epm.lock`` beside ``deal.epm``, or
``.epm-session.lock`` inside a model directory) and holds the server's address and token.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def lock_path(model: str | Path) -> Path:
    path = Path(model).resolve()
    if path.is_dir():
        return path / ".epm-session.lock"
    return path.with_name(f".{path.name}.lock")


def write_lock(model: str | Path, host: str, port: int, token: str) -> Path:
    target = lock_path(model)
    data = {
        "pid": os.getpid(),
        "host": host,
        "port": port,
        "token": token,
        "model": str(Path(model).resolve()),
    }
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the token is a secret
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, target)
    return target


def read_lock(model: str | Path) -> dict[str, Any] | None:
    try:
        return json.loads(lock_path(model).read_text())
    except (OSError, ValueError):
        return None


def remove_lock(model: str | Path, token: str | None = None) -> None:
    """Remove the lock, but only our own (another server may have taken over)."""
    info = read_lock(model)
    if info is not None and (token is None or info.get("token") == token):
        lock_path(model).unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_alive(pid: int) -> bool:
    # os.kill(pid, 0) would send Ctrl+C to the process group on Windows, not probe it
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    process_query_limited_information, still_active = 0x1000, 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: it exists, owned by someone else
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
            return True
        return code.value == still_active
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def base_url(info: dict[str, Any]) -> str:
    host = info["host"]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{info['port']}"


def live_session(model: str | Path, timeout: float = 1.0) -> dict[str, Any] | None:
    """The lock's contents if a server is answering for this model, else ``None``."""
    info = read_lock(model)
    if not info or not _pid_alive(int(info.get("pid", -1))):
        return None
    request = urllib.request.Request(
        base_url(info) + "/api/health", headers={"X-EPM-Token": info.get("token", "")}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local
            health = json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if health.get("model") != str(Path(model).resolve()):
        return None
    return info
