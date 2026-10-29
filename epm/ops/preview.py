"""Describe what a batch changes: definition diffs and the effect on selected outputs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..engine import Engine
from ..engine.cache import ValueCache
from ..engine.results import value_to_dict
from .apply import COLLECTIONS, State
from .schema import Op


@dataclass
class Change:
    kind: str
    name: str
    change: str  # "added" | "removed" | "changed" | "renamed"
    before: Any = None
    after: Any = None
    renamed_from: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {"kind": self.kind, "name": self.name, "change": self.change}
        if self.renamed_from is not None:
            out["renamed_from"] = self.renamed_from
        if self.before is not None:
            out["before"] = self.before
        if self.after is not None:
            out["after"] = self.after
        return out


def renames_of(ops: list[Op]) -> dict[tuple[str, str], str]:
    """Map ``(kind, final name)`` → original name for everything renamed by ``ops``."""
    origin: dict[tuple[str, str], str] = {}
    for op in ops:
        if op.op == "rename" and op.name and op.to:
            original = origin.pop((op.kind, op.name), op.name)
            if original != op.to:
                origin[(op.kind, op.to)] = original
        elif op.op == "delete" and op.name:
            origin.pop((op.kind, op.name), None)
    return origin


def _file_summary(data: bytes) -> dict[str, Any]:
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()[:16]}


def diff_states(old: State, new: State, renames: dict[tuple[str, str], str]) -> list[Change]:
    changes: list[Change] = []
    collections = {kind: coll for kind, (coll, _) in COLLECTIONS.items()}
    collections["analysis"] = "analyses"
    for kind, coll in collections.items():
        a, b = old.model.get(coll, {}), new.model.get(coll, {})
        renamed_origins = set()
        for name, after in b.items():
            origin = renames.get((kind, name))
            if origin is not None and origin in a:
                renamed_origins.add(origin)
                before = a[origin]
                changes.append(
                    Change(
                        kind,
                        name,
                        "renamed",
                        before if before != after else None,
                        after if before != after else None,
                        renamed_from=origin,
                    )
                )
            elif name not in a:
                changes.append(Change(kind, name, "added", after=after))
            elif a[name] != after:
                changes.append(Change(kind, name, "changed", a[name], after))
        for name, before in a.items():
            if name not in b and name not in renamed_origins:
                changes.append(Change(kind, name, "removed", before=before))
    if old.model.get("settings", {}) != new.model.get("settings", {}):
        changes.append(
            Change(
                "settings",
                "settings",
                "changed",
                old.model.get("settings", {}),
                new.model.get("settings", {}),
            )
        )
    renamed_files = {v for (kind, _), v in renames.items() if kind == "file"}
    for path in sorted(old.inputs.keys() | new.inputs.keys()):
        a_data, b_data = old.inputs.get(path), new.inputs.get(path)
        origin = renames.get(("file", path))
        if origin is not None and origin in old.inputs and b_data is not None:
            changes.append(Change("file", path, "renamed", renamed_from=origin))
        elif a_data is None and b_data is not None:
            changes.append(Change("file", path, "added", after=_file_summary(b_data)))
        elif b_data is None and a_data is not None:
            if path not in renamed_files:
                changes.append(Change("file", path, "removed", before=_file_summary(a_data)))
        elif a_data != b_data:
            changes.append(
                Change("file", path, "changed", _file_summary(a_data), _file_summary(b_data))
            )
    if (old.functions or "") != (new.functions or ""):
        changes.append(Change("function", "functions.py", "changed"))
    return changes


def _delta(before: Any, after: Any) -> Any:
    if isinstance(before, pd.Series) and isinstance(after, pd.Series):
        if not before.index.equals(after.index):
            return None
        diff = after.astype(float) - before.astype(float)
        return value_to_dict(diff)["values"]
    if isinstance(before, pd.Series) or isinstance(after, pd.Series):
        return None
    try:
        d = float(after) - float(before)
    except (TypeError, ValueError):
        return None
    return d if np.isfinite(d) else None


@dataclass
class Impact:
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    checks: dict[str, dict[str, str | None]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"outputs": self.outputs, "checks": self.checks}


def impact(
    before: Engine,
    after: Engine,
    outputs: list[str],
    metric_renames: dict[str, str],
    cache: ValueCache | None,
) -> Impact:
    """Run both versions (sharing the cache) and compare outputs and checks."""
    new_outputs = [o for o in outputs if o in after.metrics]
    old_outputs = [metric_renames.get(o, o) for o in new_outputs]
    old_outputs = [o for o in old_outputs if o in before.metrics]
    old_run = before.run(outputs=old_outputs, checks=True, cache=cache)
    new_run = after.run(outputs=new_outputs, checks=True, cache=cache)
    out = Impact()
    for name in new_outputs:
        old_name = metric_renames.get(name, name)
        b = old_run.values.get(old_name)
        a = new_run.values.get(name)
        entry: dict[str, Any] = {
            "before": None if b is None else value_to_dict(b),
            "after": None if a is None else value_to_dict(a),
            "status": new_run.status[name].state,
        }
        if b is not None and a is not None:
            entry["delta"] = _delta(b, a)
        out.outputs[name] = entry
    for name in before.checks.keys() | after.checks.keys():
        b_state = old_run.checks[name].state if name in old_run.checks else None
        a_state = new_run.checks[name].state if name in new_run.checks else None
        if b_state != a_state or a_state not in (None, "passed"):
            out.checks[name] = {"before": b_state, "after": a_state}
    return out
