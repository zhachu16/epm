"""Static context a formula is checked and compiled against."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..errors import FormulaError


@dataclass(frozen=True)
class AxisInfo:
    name: str
    kind: str
    index: pd.Index
    historical: np.ndarray  # boolean mask aligned with ``index``

    def __len__(self) -> int:
        return len(self.index)

    def lookup(self, value) -> object:
        """Return the axis value matching ``value``, comparing by string form."""
        for v in self.index:
            if v == value or str(v) == str(value):
                return v
        raise KeyError(value)


@dataclass(frozen=True)
class FormulaEnv:
    """Axes known to the model, plus the axis that period helpers default to."""

    axes: dict[str, AxisInfo]
    metric_axis: str | None = None
    default_axis: str | None = None
    # first in-scope position of each forecast-only metric; absent means 0 (all periods)
    starts: Mapping[str, int] = field(default_factory=dict)

    def resolve_axis(self, name: str | None, func: str, col: int | None = None) -> AxisInfo:
        chosen = name or self.metric_axis or self.default_axis
        if chosen is None:
            raise FormulaError(
                "missing_axis",
                f"{func}() needs an axis: pass one, e.g. {func}('fy'), declare the metric's "
                "axis, or set settings.default_axis",
                col=col,
            )
        if chosen not in self.axes:
            raise FormulaError("unknown_axis", f"unknown axis '{chosen}'", col=col)
        return self.axes[chosen]
