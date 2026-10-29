"""The shared API used by the CLI, the server and agents.

A :class:`Workspace` holds one model with its input files and custom functions, an engine
built from them, and a value cache so repeated runs only recompute what changed.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from .engine import Engine, RunResult
from .engine.cache import ValueCache
from .engine.engine import Override
from .errors import Diagnostic, EpmError
from .formula.analysis import custom_calls
from .functions import FunctionRuntime, Limits
from .model import Model, Settings
from .ops import (
    Batch,
    Change,
    Impact,
    Op,
    OpError,
    State,
    apply_ops,
    diff_states,
    impact,
    renames_of,
)
from .package import EXTENSION, Package, read_package, write_package
from .sources import MemoryFiles

Mode = Literal["commit", "dry_run", "propose"]


@dataclass
class HistoryEntry:
    id: str
    label: str
    ops: list[Op]
    inverse: list[Op]
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "ops": len(self.ops), "at": self.at}


@dataclass
class BatchResult:
    """Outcome of applying (or previewing) a batch."""

    ok: bool
    mode: str
    id: str | None = None
    label: str | None = None
    errors: list[OpError] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    impact: Impact | None = None
    new_errors: list[Diagnostic] = field(default_factory=list)
    committed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "id": self.id,
            "label": self.label,
            "committed": self.committed,
            "errors": [e.to_dict() for e in self.errors],
            "changes": [c.to_dict() for c in self.changes],
            "impact": self.impact.to_dict() if self.impact else None,
            "new_errors": [d.to_dict() for d in self.new_errors],
        }


@dataclass
class PendingBatch:
    id: str
    batch: Batch
    preview: BatchResult
    at: float = field(default_factory=time.time)


def _as_batch(batch: Batch | Mapping[str, Any] | list) -> Batch:
    if isinstance(batch, Batch):
        return batch
    if isinstance(batch, list):
        return Batch(ops=[o if isinstance(o, Op) else Op.model_validate(o) for o in batch])
    return Batch.model_validate(batch)


def _label(batch: Batch) -> str:
    if batch.label:
        return batch.label
    first = batch.ops[0].describe()
    return first if len(batch.ops) == 1 else f"{first} and {len(batch.ops) - 1} more"


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


class Workspace:
    def __init__(
        self,
        model: Model | None = None,
        inputs: Mapping[str, bytes] | None = None,
        functions: str | None = None,
        path: str | Path | None = None,
        limits: Limits | None = None,
    ):
        self.model = model or Model()
        self.limits = limits or Limits()
        self._runtimes: dict[str, FunctionRuntime] = {}  # functions.py source → worker
        self.inputs: dict[str, bytes] = dict(inputs or {})
        self.functions = functions
        self.path = Path(path) if path is not None else None
        self.cache = ValueCache()
        self._engine: Engine | None = None
        self.history: list[HistoryEntry] = []  # session only: never saved to the archive
        self.redo_stack: list[HistoryEntry] = []
        self.pending: dict[str, PendingBatch] = {}

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
            self._prune_runtimes()
        self.invalidate()

    def invalidate(self) -> None:
        self._engine = None

    def _runtime_for(self, functions: str | None) -> FunctionRuntime | None:
        if not functions:
            return None
        if functions not in self._runtimes:
            self._runtimes[functions] = FunctionRuntime(functions, self.limits)
        return self._runtimes[functions]

    def _prune_runtimes(self) -> None:
        """Stop workers for versions of functions.py that are no longer current."""
        for source in list(self._runtimes):
            if source != self.functions:
                self._runtimes.pop(source).close()

    def close(self) -> None:
        for runtime in self._runtimes.values():
            runtime.close()
        self._runtimes.clear()

    def _build_engine(
        self, model: Model, inputs: dict[str, bytes], functions: str | None
    ) -> Engine:
        return Engine(
            model, MemoryFiles(inputs), functions=functions, runtime=self._runtime_for(functions)
        )

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = self._build_engine(self.model, self.inputs, self.functions)
        return self._engine

    def state(self) -> State:
        return State.of(self.model, self.functions, self.inputs)

    def _set_state(self, state: State, engine: Engine | None = None) -> None:
        self.model = state.to_model()
        self.functions = state.functions
        self.inputs = dict(state.inputs)
        self._engine = engine
        self._prune_runtimes()

    # -- editing -----------------------------------------------------------------------

    def apply(
        self,
        batch: Batch | Mapping[str, Any] | list,
        mode: Mode = "commit",
        outputs: Iterable[str] | None = None,
        allow_errors: bool = False,
    ) -> BatchResult:
        """Apply a batch atomically.

        Every operation is validated, then the whole model. The batch is rejected if any
        operation fails, or if it introduces new validation errors (unless
        ``allow_errors``). ``dry_run`` only previews; ``propose`` stores the preview as a
        pending batch for :meth:`commit_pending`. The preview compares ``outputs``
        (default: ``settings.key_outputs`` plus the metrics the batch touches).
        """
        batch = _as_batch(batch)
        result = BatchResult(ok=False, mode=mode, label=_label(batch))
        old_state = self.state()
        new_state, inverse, errors = apply_ops(old_state, batch.ops)
        if errors:
            result.errors = errors
            return result
        new_model = new_state.to_model()
        new_engine = self._build_engine(new_model, new_state.inputs, new_state.functions)
        old_engine = self.engine
        seen = {(d.object, d.code, d.message) for d in old_engine.diagnostics}
        result.new_errors = [
            d
            for d in new_engine.diagnostics
            if d.severity == "error" and (d.object, d.code, d.message) not in seen
        ]
        renames = renames_of(batch.ops)
        result.changes = diff_states(old_state, new_state, renames)
        metric_renames = {new: old for (kind, new), old in renames.items() if kind == "metric"}
        if outputs is None:
            touched = [
                c.name for c in result.changes if c.kind == "metric" and c.change != "removed"
            ]
            changed_functions = {c.name for c in result.changes if c.kind == "function"}
            touched += [
                n
                for n, cm in new_engine.metrics.items()
                if cm.node is not None and custom_calls(cm.node) & changed_functions
            ]
            outputs = [*self.model.settings.key_outputs, *new_model.settings.key_outputs, *touched]
        result.impact = impact(
            old_engine, new_engine, list(dict.fromkeys(outputs))[:50], metric_renames, self.cache
        )
        if result.new_errors and not allow_errors:
            result.errors = [
                OpError(-1, "validation", d.code, f"{d.object}: {d.message}")
                for d in result.new_errors
            ]
            return result
        result.ok = True
        if mode == "commit":
            self._set_state(new_state, new_engine)
            entry = HistoryEntry(_new_id(), result.label or "", list(batch.ops), inverse)
            self.history.append(entry)
            self.redo_stack.clear()
            result.id, result.committed = entry.id, True
        elif mode == "propose":
            result.id = _new_id()
            self.pending[result.id] = PendingBatch(result.id, batch, result)
        return result

    def _replay(self, ops: list[Op]) -> None:
        state, _, errors = apply_ops(self.state(), ops)
        if errors:
            raise EpmError(
                "history_conflict",
                "cannot replay history: " + "; ".join(e.message for e in errors),
            )
        self._set_state(state)

    def undo(self) -> HistoryEntry:
        if not self.history:
            raise EpmError("nothing_to_undo", "there is nothing to undo")
        entry = self.history[-1]
        self._replay(entry.inverse)
        self.history.pop()
        self.redo_stack.append(entry)
        return entry

    def redo(self) -> HistoryEntry:
        if not self.redo_stack:
            raise EpmError("nothing_to_redo", "there is nothing to redo")
        entry = self.redo_stack[-1]
        self._replay(entry.ops)
        self.redo_stack.pop()
        self.history.append(entry)
        return entry

    def rollback(self, entry_id: str) -> list[HistoryEntry]:
        """Undo every batch committed after ``entry_id``."""
        if not any(e.id == entry_id for e in self.history):
            raise EpmError("not_found", f"no committed batch '{entry_id}' in this session")
        undone = []
        while self.history[-1].id != entry_id:
            undone.append(self.undo())
        return undone

    def commit_pending(self, pending_id: str, allow_errors: bool = False) -> BatchResult:
        """Commit a proposed batch, re-validated against the current model."""
        if pending_id not in self.pending:
            raise EpmError("not_found", f"no pending batch '{pending_id}'")
        result = self.apply(self.pending[pending_id].batch, "commit", allow_errors=allow_errors)
        if result.ok:
            del self.pending[pending_id]
        return result

    def reject_pending(self, pending_id: str) -> None:
        if self.pending.pop(pending_id, None) is None:
            raise EpmError("not_found", f"no pending batch '{pending_id}'")

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

    def sweep(
        self,
        inputs: Mapping[str, Iterable[float]],
        outputs: Iterable[str],
        mode: Literal["grid", "zip"] = "grid",
        overrides: Mapping[str, Override] | None = None,
    ):
        from .analysis import sweep

        values = {k: list(v) for k, v in inputs.items()}
        return sweep(self.engine, values, list(outputs), mode, overrides, self.cache)

    def run_analysis(self, name: str):
        from .analysis import run_analysis

        if name not in self.model.analyses:
            raise EpmError("not_found", f"there is no analysis '{name}'")
        return run_analysis(self.engine, name, self.model.analyses[name], self.cache)
