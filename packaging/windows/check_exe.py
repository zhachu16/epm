"""Check a built epm.exe the way someone who downloaded it would use it.

    python packaging/windows/check_exe.py dist/epm.exe

1. ``--version`` and ``--self-test``: the engine, the function worker, the server and the UI
   all work from the one file.
2. Reproducibility: ``fm run`` on the example, through the program, gives the same numbers as
   the source it was built from, run by the Python that built it.
3. Double-click use: started in a folder with a model.json (and a stale lock left by a copy
   that was killed), it serves the model; a second start points at the first; Ctrl+Break
   stops it and it removes its lock.

Runs on any platform; CI runs it on Windows against the program it is about to publish.
"""

from __future__ import annotations

import json
import math
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
EXAMPLE = ROOT / "examples" / "lbo"
WINDOWS = sys.platform == "win32"
TOLERANCE = 1e-12  # relative; the same libraries on the same machine should agree exactly


def main() -> int:
    exe = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="epm-check-") as tmp:
        failures = [
            check_basics(exe),
            check_same_numbers(exe, Path(tmp) / "a"),
            check_double_click(exe, Path(tmp) / "b"),
        ]
    failed = sum(failures)
    print("all checks passed" if not failed else f"{failed} check(s) FAILED")
    return 1 if failed else 0


def copy_example(target: Path) -> Path:
    shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns("cache", "*.lock"))
    return target


def run(argv: list, **kwargs) -> subprocess.CompletedProcess:
    print("$", " ".join(str(a) for a in argv), flush=True)
    # the program runs in UTF-8 mode, so its output is UTF-8 whatever the locale
    return subprocess.run(
        argv, capture_output=True, encoding="utf-8", errors="replace", timeout=300, **kwargs
    )


def check_basics(exe: Path) -> bool:
    failed = False
    for flag in ("--version", "--self-test"):
        result = run([exe, flag])
        print(result.stdout + result.stderr)
        failed |= result.returncode != 0
    return failed


def check_same_numbers(exe: Path, model: Path) -> bool:
    from epm import Workspace
    from epm.cli.output import clean

    copy_example(model)
    result = run([exe, "fm", "-m", model / "model.json", "--json", "run"])
    if result.returncode != 0:
        print("FAIL fm run through the program:", result.stdout, result.stderr)
        return True
    frozen = json.loads(result.stdout)["data"]["values"]
    ws = Workspace.open(model)
    try:
        source = clean(ws.run().to_dict())["values"]
    finally:
        ws.close()
    worst, where = 0.0, None
    for name in sorted(set(frozen) | set(source)):
        for period, a, b in pairs(frozen.get(name), source.get(name)):
            diff = difference(a, b)
            if diff > worst:
                worst, where = diff, f"{name} {period}: {a} vs {b}"
    ok = worst <= TOLERANCE
    print(
        f"{'ok  ' if ok else 'FAIL'} {len(source)} metrics match the source run"
        + (f" (largest difference {worst:.3g}, {where})" if worst else " exactly")
    )
    return not ok


def pairs(a: dict | None, b: dict | None):
    a, b = a or {}, b or {}
    if "values" in a or "values" in b:
        va, vb = a.get("values", {}), b.get("values", {})
        for period in sorted(set(va) | set(vb)):
            yield period, va.get(period, "missing"), vb.get(period, "missing")
    else:
        yield "", a.get("value", "missing"), b.get("value", "missing")


def difference(a, b) -> float:
    if a == b:
        return 0.0
    if not all(isinstance(v, int | float) for v in (a, b)):
        return math.inf
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


def check_double_click(exe: Path, folder: Path) -> bool:
    copy_example(folder)
    lock = folder / ".epm-session.lock"
    # a copy that was killed leaves its lock behind; its process no longer exists
    stale = {"pid": 2**22 + 12345, "host": "127.0.0.1", "port": 9, "token": "x", "model": ""}
    lock.write_text(json.dumps(stale))
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
    argv = [exe, "--no-browser", "--port", "0"]
    print("$", " ".join(str(a) for a in argv), f"(in {folder})", flush=True)
    log = folder.parent / "serve.log"
    with log.open("w", encoding="utf-8") as out:
        proc = subprocess.Popen(argv, cwd=folder, stdout=out, stderr=out, creationflags=flags)
    failed = False
    try:
        info = wait_for_lock(lock, stale, proc)
        if info is None:
            print("FAIL the program did not start serving")
            return True
        base = f"http://{info['host']}:{info['port']}"
        page = urllib.request.urlopen(base + "/", timeout=30).read().decode("utf-8")
        failed |= report("epm-token" in page, "it serves the UI for the model next to it")
        request = urllib.request.Request(
            base + "/api/state", headers={"X-EPM-Token": info["token"]}
        )
        state = json.loads(urllib.request.urlopen(request, timeout=60).read())
        failed |= report("equity_irr" in state.get("metrics", {}), "its API answers")
        again = run([exe, "--no-browser"], cwd=folder)
        failed |= report(
            again.returncode == 0 and "already open" in again.stdout,
            "a second start points at the first",
            again.stdout.strip(),
        )
    finally:
        proc.send_signal(signal.CTRL_BREAK_EVENT if WINDOWS else signal.SIGINT)
        try:
            proc.wait(30)
            stopped = True
        except subprocess.TimeoutExpired:
            proc.kill()
            stopped = False
        print(log.read_text(encoding="utf-8", errors="replace"))
    failed |= report(stopped, "it stops when asked")
    deadline = time.monotonic() + 10  # the server may finish just after the launcher does
    while lock.exists() and time.monotonic() < deadline:
        time.sleep(0.25)
    failed |= report(not lock.exists(), "it removes its lock when it stops")
    return failed


def wait_for_lock(lock: Path, stale: dict, proc: subprocess.Popen) -> dict | None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and proc.poll() is None:
        try:
            info = json.loads(lock.read_text())
            if info.get("token") != stale["token"]:
                return info
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    return None


def report(ok: bool, what: str, detail: str = "") -> bool:
    print(f"{'ok  ' if ok else 'FAIL'} {what}" + (f": {detail}" if detail and not ok else ""))
    return not ok


if __name__ == "__main__":
    raise SystemExit(main())
