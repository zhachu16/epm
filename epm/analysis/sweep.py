"""Run independent scenarios across input values and collect selected outputs."""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

from ..engine import Engine
from ..engine.cache import ValueCache
from ..errors import EpmError
from ..model.schema import Analysis, Plot

Mode = Literal["grid", "zip"]


@dataclass
class SweepResult:
    """One row per scenario: its inputs, the selected outputs, and how it went."""

    inputs: list[str]
    outputs: list[str]
    mode: str
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r["status"] == "ok" for r in self.rows)

    def frame(self) -> pd.DataFrame:
        records = [
            {**r["inputs"], **r["outputs"], "status": r["status"], "error": r["error"]}
            for r in self.rows
        ]
        return pd.DataFrame(records, columns=[*self.inputs, *self.outputs, "status", "error"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs": self.inputs,
            "outputs": self.outputs,
            "mode": self.mode,
            "rows": self.rows,
        }


def _number(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def scenarios(inputs: Mapping[str, Sequence[float]], mode: Mode) -> list[dict[str, float]]:
    names = list(inputs)
    lists = [list(inputs[n]) for n in names]
    if mode == "zip":
        if len({len(v) for v in lists}) > 1:
            raise EpmError(
                "invalid_sweep", "in 'zip' mode every input needs the same number of values"
            )
        combos = zip(*lists, strict=True)
    elif mode == "grid":
        combos = itertools.product(*lists)
    else:
        raise EpmError("invalid_sweep", "mode must be 'grid' or 'zip'")
    return [dict(zip(names, c, strict=True)) for c in combos]


def sweep(
    engine: Engine,
    inputs: Mapping[str, Sequence[float]],
    outputs: Sequence[str],
    mode: Mode = "grid",
    overrides: Mapping[str, Any] | None = None,
    cache: ValueCache | None = None,
    max_scenarios: int = 100_000,
) -> SweepResult:
    """Evaluate every scenario independently; the base assumptions are never modified.

    ``grid`` runs all combinations of input values, ``zip`` pairs them position by
    position. A failing scenario is recorded with its error and does not stop the sweep.
    """
    if not inputs:
        raise EpmError("invalid_sweep", "a sweep needs at least one input")
    for name in [*inputs, *(overrides or {})]:
        engine._require(name)
    selectors = [engine.resolve_output(o) for o in outputs]
    cases = scenarios(inputs, mode)
    if len(cases) > max_scenarios:
        raise EpmError(
            "invalid_sweep", f"{len(cases)} scenarios exceed the limit of {max_scenarios}"
        )
    needed = list(dict.fromkeys(m for m, _ in selectors))
    result = SweepResult(list(inputs), list(outputs), mode)
    for case in cases:
        row: dict[str, Any] = {
            "inputs": dict(case),
            "outputs": dict.fromkeys(outputs),
            "status": "ok",
            "error": None,
        }
        try:
            run = engine.run(
                {**(overrides or {}), **case}, outputs=needed, checks=False, cache=cache
            )
        except EpmError as e:
            row.update(status="error", error=e.message)
            result.rows.append(row)
            continue
        for selector, (metric, period) in zip(outputs, selectors, strict=True):
            status = run.status[metric]
            if status.state in ("error", "blocked"):
                if row["error"] is None:
                    row.update(status="error", error=f"{metric}: {status.message}")
                continue
            value = run.values.get(metric)
            if period is not None and isinstance(value, pd.Series):
                value = value.loc[period]
            row["outputs"][selector] = _number(value)
        result.rows.append(row)
    return result


@dataclass
class AnalysisResult:
    name: str
    analysis: Analysis
    sweep: SweepResult

    def chart(self) -> dict[str, Any] | None:
        """Data shaped for plotting: line series, or a heatmap grid."""
        plot = self.analysis.plot
        if plot is None:
            return None
        rows = self.sweep.rows
        if plot.kind == "line":
            ys = [plot.y] if isinstance(plot.y, str) else (plot.y or self.analysis.outputs)
            return {
                "kind": "line",
                "x": plot.x,
                "x_values": [r["inputs"][plot.x] for r in rows],
                "series": {y: [r["outputs"][y] for r in rows] for y in ys},
                "format": plot.format,
                "title": plot.title,
            }
        y = plot.y
        xs = list(dict.fromkeys(r["inputs"][plot.x] for r in rows))
        ys = list(dict.fromkeys(r["inputs"][y] for r in rows))  # type: ignore[index]
        lookup = {(r["inputs"][plot.x], r["inputs"][y]): r["outputs"][plot.z] for r in rows}  # type: ignore[index]
        return {
            "kind": "heatmap",
            "x": plot.x,
            "y": y,
            "z": plot.z,
            "x_values": xs,
            "y_values": ys,
            "values": [[lookup.get((xv, yv)) for xv in xs] for yv in ys],
            "format": plot.format,
            "title": plot.title,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "sweep": self.sweep.to_dict(), "chart": self.chart()}

    def save_plot(self, path: str) -> None:
        render(self, path)


def run_analysis(
    engine: Engine, name: str, analysis: Analysis, cache: ValueCache | None = None
) -> AnalysisResult:
    inputs = {i.metric: i.resolved() for i in analysis.inputs}
    result = sweep(engine, inputs, analysis.outputs, analysis.mode, analysis.overrides, cache)
    return AnalysisResult(name, analysis, result)


def _formatter(spec: str | None):
    if not spec:
        return lambda v: f"{v:,.4g}"
    return lambda v: format(v, spec)


def render(result: AnalysisResult, path: str) -> None:
    """Save the analysis chart as an image (needs matplotlib)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise EpmError(
            "missing_dependency", "plotting needs matplotlib: pip install 'epm[plot]'"
        ) from None
    chart = result.chart()
    if chart is None:
        raise EpmError("no_plot", f"analysis '{result.name}' has no plot settings")
    plot: Plot = result.analysis.plot  # type: ignore[assignment]
    fmt = _formatter(plot.format)
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    if chart["kind"] == "line":
        for label, ys in chart["series"].items():
            ax.plot(
                chart["x_values"],
                [math.nan if y is None else y for y in ys],
                marker="o",
                label=label,
            )
        ax.set_xlabel(chart["x"])
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: fmt(v)))
        ax.legend(frameon=False)
        ax.grid(alpha=0.3)
    else:
        grid = [[math.nan if v is None else v for v in row] for row in chart["values"]]
        image = ax.imshow(grid, cmap="viridis", aspect="auto", origin="lower")
        ax.set_xticks(range(len(chart["x_values"])), [f"{v:g}" for v in chart["x_values"]])
        ax.set_yticks(range(len(chart["y_values"])), [f"{v:g}" for v in chart["y_values"]])
        ax.set_xlabel(chart["x"])
        ax.set_ylabel(chart["y"])
        for i, row in enumerate(grid):
            for j, v in enumerate(row):
                if not math.isnan(v):
                    ax.text(j, i, fmt(v), ha="center", va="center", color="white", fontsize=8)
        fig.colorbar(image, ax=ax, label=chart["z"])
    ax.set_title(plot.title or result.name)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
