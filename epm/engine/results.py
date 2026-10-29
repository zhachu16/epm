"""Result objects returned by :meth:`epm.engine.Engine.run`."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from ..errors import Diagnostic

MetricState = Literal["ok", "warning", "error", "blocked"]
CheckState = Literal["passed", "failed", "error", "blocked"]


@dataclass
class MetricStatus:
    """``blocked`` means the metric itself is fine but something upstream has errors."""

    state: MetricState
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "message": self.message}


@dataclass
class CheckResult:
    name: str
    state: CheckState
    severity: str = "error"
    message: str | None = None
    failing: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.state == "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "severity": self.severity,
            "message": self.message,
            "failing": self.failing,
        }


def _number(v: Any) -> Any:
    if isinstance(v, bool | np.bool_):
        return bool(v)
    if isinstance(v, str):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return None if not math.isfinite(f) else f


def value_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, pd.Series):
        return {
            "axis": value.index.name,
            "values": {str(k): _number(v) for k, v in value.items()},
        }
    return {"axis": None, "value": _number(value)}


@dataclass
class RunResult:
    values: dict[str, Any]
    status: dict[str, MetricStatus]
    checks: dict[str, CheckResult]
    diagnostics: list[Diagnostic]

    def __getitem__(self, name: str) -> Any:
        return self.values[name]

    @property
    def ok(self) -> bool:
        return (
            not any(d.severity == "error" for d in self.diagnostics)
            and all(s.state in ("ok", "warning") for s in self.status.values())
            and all(c.passed or c.severity != "error" for c in self.checks.values())
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "values": {k: value_to_dict(v) for k, v in self.values.items()},
            "status": {k: s.to_dict() for k, s in self.status.items()},
            "checks": {k: c.to_dict() for k, c in self.checks.items()},
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }
