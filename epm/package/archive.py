"""``.epm`` packages: ordinary ZIP archives, or the same layout unpacked in a directory.

::

    model.json      definitions (authoritative)
    inputs/         source files (authoritative)
    functions.py    optional custom functions (authoritative)
    cache/          optional calculated values, keyed by fingerprint (regenerable)
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..errors import EpmError
from ..model import Model, dump_model, load_model

EXTENSION = ".epm"
MAX_ENTRIES = 10_000
MAX_TOTAL_BYTES = 2 * 1024**3
_SKIP_NAMES = {".DS_Store", "__pycache__"}


@dataclass
class Package:
    model: Model
    inputs: dict[str, bytes] = field(default_factory=dict)  # "inputs/..." → bytes
    functions: str | None = None
    cache: dict[str, bytes] = field(default_factory=dict)  # "cache/..." → bytes


def _safe_name(name: str) -> str:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or "\\" in name or name.startswith("/"):
        raise EpmError("unsafe_archive", f"archive entry '{name}' has an unsafe path")
    return str(p)


def _route(pkg_files: dict[str, bytes]) -> Package:
    if "model.json" not in pkg_files:
        raise EpmError("invalid_package", "the package has no model.json")
    try:
        model = load_model(pkg_files["model.json"].decode("utf-8"))
    except ValueError as e:
        raise EpmError("invalid_model", f"model.json is invalid: {e}") from None
    pkg = Package(model)
    for name, data in pkg_files.items():
        if name.startswith("inputs/"):
            pkg.inputs[name] = data
        elif name.startswith("cache/"):
            pkg.cache[name] = data
        elif name == "functions.py":
            pkg.functions = data.decode("utf-8")
    return pkg


def read_package(path: str | Path) -> Package:
    """Read a ``.epm`` archive, or a directory with the same layout."""
    path = Path(path)
    if path.is_dir():
        files = {}
        for f in sorted(path.rglob("*")):
            rel = f.relative_to(path).as_posix()
            if f.is_file() and not f.is_symlink() and not set(f.parts) & _SKIP_NAMES:
                files[rel] = f.read_bytes()
        return _route(files)
    if not path.exists():
        raise EpmError("missing_file", f"'{path}' does not exist")
    try:
        with zipfile.ZipFile(path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > MAX_ENTRIES:
                raise EpmError("unsafe_archive", f"archive has more than {MAX_ENTRIES} entries")
            if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
                raise EpmError("unsafe_archive", "archive is larger than 2 GiB uncompressed")
            files = {_safe_name(i.filename): zf.read(i) for i in infos}
    except zipfile.BadZipFile:
        raise EpmError("invalid_package", f"'{path}' is not a ZIP archive") from None
    return _route(files)


def package_files(pkg: Package, include_cache: bool = True) -> dict[str, bytes]:
    files = {"model.json": dump_model(pkg.model).encode("utf-8")}
    if pkg.functions is not None:
        files["functions.py"] = pkg.functions.encode("utf-8")
    files.update(sorted(pkg.inputs.items()))
    if include_cache:
        files.update(sorted(pkg.cache.items()))
    return files


def write_package(pkg: Package, path: str | Path, include_cache: bool = True) -> None:
    """Write atomically: to a temporary file next to ``path``, then rename over it.

    If ``path`` is an existing directory, write the unpacked layout there instead.
    """
    path = Path(path)
    files = package_files(pkg, include_cache)
    if path.is_dir():
        _write_dir(files, path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            with zipfile.ZipFile(fh, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for name, data in files.items():
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    zf.writestr(info, data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_dir(files: dict[str, bytes], root: Path) -> None:
    """Write the unpacked layout, replacing cache/ and removed inputs."""
    for sub in ("cache", "inputs"):
        folder = root / sub
        if folder.is_dir():
            for f in folder.rglob("*"):
                if f.is_file() and f.relative_to(root).as_posix() not in files:
                    f.unlink()
    if "functions.py" not in files:
        (root / "functions.py").unlink(missing_ok=True)
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)


def unpack(archive: str | Path, directory: str | Path) -> None:
    target = Path(directory)
    if target.exists() and any(target.iterdir()):
        raise EpmError("not_empty", f"'{target}' is not empty")
    target.mkdir(parents=True, exist_ok=True)
    _write_dir(package_files(read_package(archive)), target)


def pack(directory: str | Path, archive: str | Path, include_cache: bool = True) -> None:
    write_package(read_package(directory), archive, include_cache)
