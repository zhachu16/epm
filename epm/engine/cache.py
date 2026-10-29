"""Content-addressed cache of evaluated steps.

Every evaluation step has a fingerprint covering its definitions, input files and the
fingerprints of everything it reads. A cached step can be reused whenever its fingerprint
matches, so after an edit only the edited metrics and their dependants are recomputed, and
sweeps recompute only what the swept inputs affect.
"""

from __future__ import annotations

import io
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..errors import Diagnostic
from .results import MetricStatus, SolverStats


@dataclass
class StepEntry:
    values: dict[str, Any]
    status: dict[str, MetricStatus]
    diagnostics: list[Diagnostic] = field(default_factory=list)
    solver: dict[str, SolverStats] = field(default_factory=dict)


class ValueCache:
    """An LRU mapping of step fingerprint → :class:`StepEntry`."""

    def __init__(self, max_entries: int = 50_000):
        self.max_entries = max_entries
        self._entries: OrderedDict[str, StepEntry] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def get(self, key: str) -> StepEntry | None:
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
        return entry

    def put(self, key: str, entry: StepEntry) -> None:
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def keys(self) -> list[str]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def overlay(self, max_entries: int = 20_000) -> ValueCache:
        """A cache that reads this one but keeps its own additions to itself.

        Sweeps use it so their scenarios reuse the model's cached steps without filling the
        shared cache with results nobody will ask for again.
        """
        return _Overlay(self, max_entries)

    # -- persistence -------------------------------------------------------------------

    def dump(self, keys: list[str]) -> dict[str, bytes]:
        """Serialize the given steps as ``cache/`` files: an index plus a parquet per axis."""
        index: dict[str, Any] = {}
        columns: dict[str, dict[str, np.ndarray]] = {}
        axis_labels: dict[str, list] = {}
        for key in keys:
            entry = self._entries.get(key)
            if entry is None:
                continue
            metrics = {}
            for name, st in entry.status.items():
                record: dict[str, Any] = {"status": st.to_dict()}
                value = entry.values.get(name)
                if isinstance(value, pd.Series):
                    axis = str(value.index.name)
                    column = f"{key}:{name}"
                    columns.setdefault(axis, {})[column] = value.to_numpy(dtype=float)
                    axis_labels.setdefault(axis, [str(v) for v in value.index])
                    record.update(kind="series", axis=axis, column=column)
                elif value is not None:
                    record.update(kind="scalar", value=_json_number(value))
                metrics[name] = record
            index[key] = {
                "metrics": metrics,
                "diagnostics": [d.to_dict() for d in entry.diagnostics],
                "solver": {k: v.to_dict() for k, v in entry.solver.items()},
            }
        files = {"cache/index.json": json.dumps({"steps": index}, indent=1).encode()}
        for axis, cols in columns.items():
            frame = pd.DataFrame(cols, index=pd.Index(axis_labels[axis], name="__axis__"))
            buffer = io.BytesIO()
            frame.to_parquet(buffer)
            files[f"cache/axis-{axis}.parquet"] = buffer.getvalue()
        return files

    def load(self, files: dict[str, bytes], axes: dict[str, pd.Index]) -> int:
        """Restore steps saved by :meth:`dump`; returns how many were loaded.

        Series are rebuilt on the current axis index. Anything that no longer fits (an axis
        that changed or disappeared) is skipped, since its fingerprint cannot match anyway.
        """
        raw = files.get("cache/index.json")
        if raw is None:
            return 0
        index = json.loads(raw)["steps"]
        frames = {}
        for path, data in files.items():
            if path.startswith("cache/axis-") and path.endswith(".parquet"):
                frames[path[len("cache/axis-") : -len(".parquet")]] = pd.read_parquet(
                    io.BytesIO(data)
                )
        loaded = 0
        for key, step in index.items():
            entry = _restore(step, frames, axes)
            if entry is not None:
                self.put(key, entry)
                loaded += 1
        return loaded


def _json_number(v: Any) -> Any:
    if isinstance(v, bool | np.bool_):
        return bool(v)
    f = float(v)
    return f if np.isfinite(f) else str(f)


def _restore(step: dict, frames: dict[str, pd.DataFrame], axes: dict[str, pd.Index]):
    values, status = {}, {}
    for name, record in step["metrics"].items():
        st = record["status"]
        status[name] = MetricStatus(st["state"], st.get("message"))
        kind = record.get("kind")
        if kind == "series":
            axis, column = record["axis"], record["column"]
            frame = frames.get(axis)
            if axis not in axes or frame is None or column not in frame:
                return None
            if len(frame) != len(axes[axis]):
                return None
            values[name] = pd.Series(frame[column].to_numpy(), index=axes[axis], name=name)
        elif kind == "scalar":
            v = record["value"]
            values[name] = np.bool_(v) if isinstance(v, bool) else np.float64(v)
    diagnostics = [
        Diagnostic(d["severity"], d["code"], d["message"], d.get("object"), d.get("detail", {}))
        for d in step.get("diagnostics", [])
    ]
    solver = {
        k: SolverStats(
            k,
            v["converged"],
            v["iterations"],
            np.nan if v["residual"] is None else v["residual"],
            v.get("periods"),
            v.get("message"),
        )
        for k, v in step.get("solver", {}).items()
    }
    return StepEntry(values, status, diagnostics, solver)


class _Overlay(ValueCache):
    def __init__(self, base: ValueCache, max_entries: int):
        super().__init__(max_entries)
        self._base = base

    def __contains__(self, key: str) -> bool:
        return super().__contains__(key) or key in self._base

    def get(self, key: str) -> StepEntry | None:
        entry = super().get(key)
        return entry if entry is not None else self._base.get(key)
