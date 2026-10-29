"""The shared API used by the CLI, the server and agents.

A :class:`Workspace` holds one model with its input files and custom functions, an engine
built from them, and a value cache so repeated runs only recompute what changed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import pandas as pd

from .engine import Engine, RunResult
from .engine.cache import ValueCache
from .engine.engine import Override
from .errors import Diagnostic, EpmError
from .model import Model, Settings
from .package import EXTENSION, Package, read_package, write_package
from .sources import MemoryFiles


class Workspace:
    def __init__(
        self,
        model: Model | None = None,
        inputs: Mapping[str, bytes] | None = None,
        functions: str | None = None,
        path: str | Path | None = None,
    ):
        self.model = model or Model()
        self.inputs: dict[str, bytes] = dict(inputs or {})
        self.functions = functions
        self.path = Path(path) if path is not None else None
        self.cache = ValueCache()
        self._engine: Engine | None = None

    # -- files -------------------------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path) -> Workspace:
        """Open a ``.epm`` archive or an unpacked model directory."""
        pkg = read_package(path)
        ws = cls(pkg.model, pkg.inputs, pkg.functions, path)
        if pkg.cache:
            ws.cache.load(pkg.cache, ws._axis_indexes())
        return ws

    @classmethod
    def new(cls, name: str = "Untitled model", path: str | Path | None = None) -> Workspace:
        return cls(Model(settings=Settings(name=name)), path=path)

    def save(self, path: str | Path | None = None, include_cache: bool = True) -> Path:
        """Save to ``path`` (default: where it was opened). Cache holds only current values."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise EpmError("no_path", "the workspace has no path; pass one to save()")
        if not target.is_dir() and target.suffix != EXTENSION:
            raise EpmError("invalid_path", f"model archives use the '{EXTENSION}' extension")
        cache = {}
        if include_cache:
            engine = self.engine
            cache = self.cache.dump([k for k in engine.step_keys if k in self.cache])
        write_package(Package(self.model, self.inputs, self.functions, cache), target)
        self.path = target
        return target

    def set_input(self, path: str, data: bytes) -> None:
        if not path.startswith("inputs/"):
            raise EpmError("invalid_path", "input files live under 'inputs/'")
        self.inputs[path] = data
        self.invalidate()

    def remove_input(self, path: str) -> None:
        self.inputs.pop(path, None)
        self.invalidate()

    def _axis_indexes(self) -> dict[str, pd.Index]:
        return {n: pd.Index(ax.values, name=n) for n, ax in self.model.axes.items()}

    # -- model -------------------------------------------------------------------------

    def replace(self, model: Model | None = None, functions: str | None | object = ...) -> None:
        """Swap in new definitions. Cached values stay valid wherever fingerprints match."""
        if model is not None:
            self.model = model
        if functions is not ...:
            self.functions = functions  # type: ignore[assignment]
        self.invalidate()

    def invalidate(self) -> None:
        self._engine = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = Engine(self.model, MemoryFiles(self.inputs))
        return self._engine

    # -- queries -----------------------------------------------------------------------

    def validate(self) -> list[Diagnostic]:
        return list(self.engine.diagnostics)

    def run(
        self,
        overrides: Mapping[str, Override] | None = None,
        outputs: Iterable[str] | None = None,
        checks: bool | None = None,
    ) -> RunResult:
        return self.engine.run(overrides, outputs, checks, cache=self.cache)

    def trace(self, name: str, direction: str = "upstream", depth: int | None = None):
        return self.engine.trace(name, direction, depth)
