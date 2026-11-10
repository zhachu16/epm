"""``epm.exe``: the local app as one program, built for Windows by ``packaging/windows``.

Put it next to a ``model.json`` (or one ``.epm`` file) and double-click it, drop a model onto
it, or run ``epm.exe PATH``. It serves the model with the bundled web UI, opens the browser,
and runs until its window is closed. With nothing to open it asks for a file.

``epm.exe fm ...`` runs any ``fm`` command, ``epm.exe --self-test`` checks that the program
works on this machine, and ``epm.exe --version`` says exactly what it was built from.

Unfrozen, ``python -m epm.desktop`` behaves the same, which is how it is tested.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

# Kept light: the function worker starts through here too, and should not import the server.

DEFAULT_PORT = 8765
_CTRL_CLOSE_EVENT, _CTRL_LOGOFF_EVENT, _CTRL_SHUTDOWN_EVENT = 2, 5, 6


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    from .functions.runtime import WORKER_FLAG

    if argv[:1] == [WORKER_FLAG]:
        import signal

        from .functions import worker

        # Ctrl+C in the window reaches every process in it; the server stops the worker
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        worker.main()
        return 0
    if argv[:1] == ["fm"]:
        from .cli.routing import run

        run(argv[1:])
        return 0

    args = _parser().parse_args(argv)
    if args.version:
        print(version_text())
        return 0
    if args.self_test:
        return self_test()

    from .errors import EpmError

    try:
        model = resolve_model(args.model, _search_dirs())
        if model is None:
            chosen = _choose_file()
            if chosen is None:
                print("No model to open. Run epm.exe PATH, where PATH is a model.json,")
                print("a folder with one, or a .epm file; or put epm.exe next to model.json.")
                _pause()
                return 2
            model = resolve_model(chosen, [])
        return serve_model(model, args.port, not args.no_browser)
    except EpmError as e:
        print(f"error: {e.message}")
        _pause()
        return 1


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="epm",
        description="Open an epm model in the browser. "
        "'epm fm ...' runs fm commands, for example 'epm fm -m model.json validate'.",
    )
    p.add_argument("model", nargs="?", help="model.json, a model folder or a .epm file")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="port (0 picks a free one)")
    p.add_argument("--no-browser", action="store_true", help="don't open the browser")
    p.add_argument("--self-test", action="store_true", help="check that epm works here")
    p.add_argument("--version", action="store_true", help="show what this program contains")
    return p


# -- finding the model --------------------------------------------------------------------


def _search_dirs() -> list[Path]:
    """Where to look when no model is named: next to the program, then the current folder."""
    dirs = [Path.cwd()]
    if getattr(sys, "frozen", False):
        dirs.insert(0, Path(sys.executable).resolve().parent)
    return list(dict.fromkeys(dirs))


def resolve_model(arg: str | Path | None, search: list[Path]) -> Path | None:
    """The model to open, as ``Workspace.open`` takes it: a model folder or a ``.epm`` file.

    ``arg`` may name a model.json, its folder or a ``.epm`` file. Without one, the first
    folder in ``search`` that holds a model.json, or exactly one ``.epm`` file, is used.
    """
    from .errors import EpmError
    from .package import EXTENSION

    if arg is None:
        for folder in search:
            if (folder / "model.json").is_file():
                return folder.resolve()
            found = sorted(folder.glob(f"*{EXTENSION}"))
            if len(found) == 1:
                return found[0].resolve()
        return None
    path = Path(arg).expanduser()
    if not path.exists():
        raise EpmError("missing_file", f"'{path}' does not exist")
    if path.is_dir():
        if not (path / "model.json").is_file():
            raise EpmError("no_model", f"'{path}' has no model.json")
        return path.resolve()
    if path.suffix.lower() == ".json":
        if path.name.lower() != "model.json":
            # its inputs/ and functions.py are found by their place next to model.json
            raise EpmError(
                "not_model_json",
                f"'{path.name}' is not named model.json: rename it to model.json, "
                "in a folder of its own, and open that",
            )
        return path.parent.resolve()
    return path.resolve()


def _choose_file() -> str | None:
    """Ask for a model with the standard Windows Open dialog; ``None`` elsewhere or if cancelled."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class OpenFileName(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    try:
        # the filter is pairs of NUL-terminated strings, so it needs a buffer, not a str
        filters = ctypes.create_unicode_buffer(
            "epm models (model.json, *.epm)\0model.json;*.epm\0All files\0*.*\0\0"
        )
        path = ctypes.create_unicode_buffer(32768)
        ofn = OpenFileName()
        ofn.lStructSize = ctypes.sizeof(OpenFileName)
        ofn.lpstrFilter = ctypes.cast(filters, wintypes.LPCWSTR)
        ofn.lpstrFile = ctypes.cast(path, wintypes.LPWSTR)
        ofn.nMaxFile = len(path)
        ofn.lpstrTitle = "Open an epm model"
        ofn.Flags = 0x1000 | 0x800 | 0x80000  # file and path must exist; Explorer style
        if not ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
            return None
        return path.value or None
    except (AttributeError, OSError):
        return None


def _pause() -> None:
    """Keep a double-clicked program's window open long enough to read why it stopped."""
    if sys.platform == "win32" and sys.stdin is not None and sys.stdin.isatty():
        with contextlib.suppress(EOFError, KeyboardInterrupt):
            input("Press Enter to close.")


# -- serving ------------------------------------------------------------------------------


def serve_model(model: Path, port: int = DEFAULT_PORT, open_browser: bool = True) -> int:
    """Serve ``model`` until the window is closed or Ctrl+C."""
    import _thread
    import signal
    import threading
    import webbrowser

    from .errors import EpmError
    from .server import serve
    from .server.lockfile import base_url, live_session

    existing = live_session(model)
    if existing is not None:
        url = base_url(existing) + "/"
        print(f"{model.name} is already open at {url}")
        if open_browser:
            webbrowser.open(url)
        return 0

    stopped = threading.Event()

    def stop() -> None:
        # stop the server as Ctrl+C would, so the session saves and removes its lock
        _thread.interrupt_main(signal.SIGINT)
        stopped.wait(4.5)  # Windows ends the process 5 seconds after the window closes

    _on_console_close(stop)
    print(f"epm {_build_info().get('version', '')} - {model}")
    print("Close this window (or press Ctrl+C) to stop. Changes are saved as you make them.")
    try:
        try:
            serve(model, port=port, open_browser=open_browser)
        except EpmError as e:
            if e.code != "port_in_use" or port == 0:
                raise
            serve(model, port=0, open_browser=open_browser)
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
    return 0


_console_handlers: list = []  # ctypes callbacks must outlive their registration


def _on_console_close(stop: Callable[[], None]) -> None:
    """Call ``stop`` when the console window closes, or Windows logs off or shuts down.

    Ctrl+C already reaches Python as KeyboardInterrupt; closing the window does not, and
    would otherwise end the process before the session saves its cache and removes its lock.
    """
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    def handler(event: int) -> bool:
        if event in (_CTRL_CLOSE_EVENT, _CTRL_LOGOFF_EVENT, _CTRL_SHUTDOWN_EVENT):
            stop()
            return True
        return False

    try:
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)(handler)
        if ctypes.windll.kernel32.SetConsoleCtrlHandler(callback, True):
            _console_handlers.append(callback)
    except (AttributeError, OSError):
        pass


# -- what this program is ---------------------------------------------------------------


def _bundle_dir() -> Path:
    """Where a frozen program's bundled files are; the repository root when unfrozen."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def _build_info() -> dict:
    """What the build recorded (packaging/windows/epm.spec), or what is installed."""
    try:
        return json.loads((_bundle_dir() / "build-info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    import importlib.metadata
    import platform

    info = {"python": platform.python_version()}
    for name in ("epm", "numpy", "pandas"):
        with contextlib.suppress(importlib.metadata.PackageNotFoundError):
            info["version" if name == "epm" else name] = importlib.metadata.version(name)
    return info


def version_text() -> str:
    from .engine.engine import ENGINE_VERSION

    info = _build_info()
    lines = [f"epm {info.get('version', '?')} (engine {ENGINE_VERSION})"]
    if info.get("commit"):
        lines.append(f"built from {info['commit']}")
    packages = info.get("packages") or {k: info[k] for k in ("numpy", "pandas") if k in info}
    lines.append(
        f"Python {info.get('python', '?')}; "
        + ", ".join(f"{k} {v}" for k, v in sorted(packages.items()))
    )
    return "\n".join(lines)


# -- self-test ----------------------------------------------------------------------------


def self_test() -> int:
    """Open, run and serve a copy of the bundled example: the checks a new machine needs."""
    import shutil
    import tempfile

    print(version_text())
    example = _bundle_dir() / "examples" / "lbo"
    if not (example / "model.json").is_file():
        print(f"FAIL the example model is missing ({example})")
        return 1
    with tempfile.TemporaryDirectory(prefix="epm-self-test-") as tmp:
        model = Path(tmp) / "lbo"
        shutil.copytree(example, model, ignore=shutil.ignore_patterns("cache", "*.lock"))
        failures = [_check_engine(model), _check_server(model)]
    if any(failures):
        print("self-test FAILED")
        return 1
    print("self-test passed")
    return 0


def _report(ok: bool, what: str, detail: str = "") -> bool:
    print(f"{'ok  ' if ok else 'FAIL'} {what}" + (f": {detail}" if detail else ""))
    return not ok


def _check_engine(model: Path) -> bool:
    import math

    from .workspace import Workspace

    failed = False
    ws = Workspace.open(model)
    try:
        errors = [d.message for d in ws.validate() if d.severity == "error"]
        failed |= _report(not errors, "the model validates", "; ".join(errors[:3]))
        run = ws.run()
        # the example calls a custom function, so this also checks the function worker
        bad = [n for n, s in run.status.items() if s.state not in ("ok", "warning")]
        failed |= _report(not bad, f"{len(run.status)} metrics calculate", ", ".join(bad[:5]))
        checks = [n for n, c in run.checks.items() if not c.passed]
        failed |= _report(not checks, f"{len(run.checks)} checks pass", ", ".join(checks))
        irr = float(run.values.get("equity_irr", math.nan))
        failed |= _report(math.isfinite(irr), "equity IRR", f"{irr:.6%}")
    except Exception as e:  # noqa: BLE001 - report anything, it is what the test is for
        failed |= _report(False, "the model runs", f"{type(e).__name__}: {e}")
    finally:
        ws.close()
    return failed


def _check_server(model: Path) -> bool:
    import socket
    import threading
    import time
    import urllib.request

    import uvicorn

    from .server import Session, create_app, find_frontend

    failed = False
    frozen = getattr(sys, "frozen", False)
    ui = find_frontend()
    failed |= _report(ui is not None or not frozen, "the web UI is bundled", str(ui or ""))
    session = Session(model)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(session), log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        failed |= _report(server.started, "the server starts", base)
        if not server.started:
            return True
        with urllib.request.urlopen(base + "/", timeout=30) as r:  # noqa: S310 - local
            page = r.read().decode("utf-8")
        served = "epm-token" in page and "<script" in page if ui else "epm session" in page
        failed |= _report(served, "the UI page is served")
        request = urllib.request.Request(
            base + "/api/state", headers={"X-EPM-Token": session.token}
        )
        with urllib.request.urlopen(request, timeout=60) as r:  # noqa: S310 - local
            state = json.loads(r.read())
        failed |= _report("equity_irr" in json.dumps(state), "the API answers")
        from websockets.sync.client import connect

        url = f"ws://127.0.0.1:{port}/api/events?token={session.token}"
        with connect(url, open_timeout=30) as ws:
            hello = json.loads(ws.recv(timeout=30))
        failed |= _report(hello.get("type") == "hello", "live updates connect")
    except Exception as e:  # noqa: BLE001
        failed |= _report(False, "the server answers", f"{type(e).__name__}: {e}")
    finally:
        server.should_exit = True
        thread.join(30)
        sock.close()
        session.close(save_cache=False)
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
