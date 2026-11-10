# PyInstaller recipe for epm.exe: the local app, its web UI and the example model, in one
# file that runs without Python or Node installed. Build from the repository root, after
# installing packaging/windows/requirements.txt and building frontend-local (epm itself is
# taken from this checkout):
#
#   pyinstaller --clean --noconfirm packaging/windows/epm.spec
#
# The result is dist/epm.exe (dist/epm elsewhere: the same recipe builds on Linux and macOS,
# which is how it is tried out away from Windows).
import json
import os
import platform
import subprocess
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# epm is bundled from this checkout; it need not be installed
ROOT = Path(SPECPATH).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from epm.functions.worker import ALLOWED_MODULES  # noqa: E402

UI = ROOT / "frontend-local" / "dist"
EXAMPLE = ROOT / "examples" / "lbo"
if not (UI / "index.html").is_file():
    raise SystemExit("build the web UI first: cd frontend-local && npm ci && npm run build")


def source_commit() -> str | None:
    if os.environ.get("EPM_BUILD_COMMIT"):
        return os.environ["EPM_BUILD_COMMIT"]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


# What `epm.exe --version` reports, so a copy can be traced back to its source and libraries
build_info = {
    "version": tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"],
    "commit": source_commit(),
    "python": platform.python_version(),
    "packages": {
        name: version(name)
        for name in ("numpy", "pandas", "pydantic", "fastapi", "uvicorn", "openpyxl")
    },
}
info_file = Path(workpath) / "build-info.json"
info_file.parent.mkdir(parents=True, exist_ok=True)
info_file.write_text(json.dumps(build_info, indent=1), encoding="utf-8")

example = [
    (str(EXAMPLE / "model.json"), "examples/lbo"),
    (str(EXAMPLE / "inputs"), "examples/lbo/inputs"),
]
if (EXAMPLE / "functions.py").is_file():
    example.append((str(EXAMPLE / "functions.py"), "examples/lbo"))

a = Analysis(
    [str(Path(SPECPATH) / "launch.py")],
    pathex=[str(ROOT)],
    datas=[(str(UI), "frontend"), (str(info_file), "."), *example],
    hiddenimports=[
        # uvicorn picks its protocol, loop and lifespan modules by name at run time
        *collect_submodules("uvicorn"),
        *collect_submodules("websockets"),
        # the function worker, started as `epm.exe --epm-function-worker`, and whatever a
        # model's functions.py may import there
        "epm.functions.worker",
        *sorted(ALLOWED_MODULES),
    ],
    excludes=[
        # pyarrow only saves the value cache (epm.engine.cache.parquet_available); without
        # it the program is half the size and recalculates instead
        "pyarrow",
        "matplotlib",
        "tkinter",
        "_tkinter",
        "IPython",
        "pytest",
        "PIL",
        "scipy",
        "setuptools",
        "pkg_resources",
        "pydoc_data",
    ],
    noarchive=False,
    optimize=0,  # keep asserts: the program must behave exactly like `pip install epm`
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    # UTF-8 mode: files and console text read and write the same on every Windows locale
    [("X utf8_mode=1", None, "OPTION")],
    name="epm",
    console=True,  # the window shows where the model is served; closing it stops epm
    upx=False,  # UPX-packed programs are flagged by antivirus far more often
    strip=False,
    runtime_tmpdir=None,
)
