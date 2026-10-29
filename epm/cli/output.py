"""Human-readable and JSON output for the ``fm`` CLI."""

from __future__ import annotations

import json
import math
import sys
from typing import Any

import numpy as np
import pandas as pd
import typer

from ..engine.results import value_to_dict


def clean(obj: Any) -> Any:
    """Make results JSON-safe: numpy scalars to Python, NaN and inf to null."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [clean(v) for v in obj]
    if isinstance(obj, pd.Series):
        return clean(value_to_dict(obj))
    if isinstance(obj, np.bool_ | bool):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, float | np.floating):
        f = float(obj)
        return f if math.isfinite(f) else None
    return obj


def number(v: Any, unit: str | None = None) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool | np.bool_):
        return "true" if v else "false"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isnan(f):
        return "—"
    if math.isinf(f):
        return "∞" if f > 0 else "-∞"
    if unit is not None and unit.strip() == "%":
        return f"{f:.2%}"
    if unit is not None and unit.strip().lower() == "x":
        return f"{f:.2f}x"
    if abs(f) >= 1000:
        return f"{f:,.1f}"
    if abs(f) >= 1 or f == 0:
        return f"{f:,.2f}"
    return f"{f:.4g}"


def table(rows: list[list[Any]], headers: list[str] | None = None) -> str:
    """Align columns; the first column is left-aligned, the rest right-aligned."""
    data = [[str(c) for c in r] for r in rows]
    if headers:
        data.insert(0, headers)
    if not data:
        return ""
    widths = [max(len(r[i]) for r in data if i < len(r)) for i in range(max(map(len, data)))]
    lines = []
    for n, r in enumerate(data):
        cells = [c.ljust(widths[i]) if i == 0 else c.rjust(widths[i]) for i, c in enumerate(r)]
        lines.append("  ".join(cells).rstrip())
        if headers and n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def values_table(values: dict[str, Any], units: dict[str, str | None]) -> str:
    """Scalars as ``name  value`` lines, then one table per axis for series."""
    out = []
    scalars = [
        [n, number(v, units.get(n))] for n, v in values.items() if not isinstance(v, pd.Series)
    ]
    if scalars:
        out.append(table(scalars))
    by_axis: dict[str, list[tuple[str, pd.Series]]] = {}
    for n, v in values.items():
        if isinstance(v, pd.Series):
            by_axis.setdefault(str(v.index.name), []).append((n, v))
    for axis, series in by_axis.items():
        labels = [str(x) for x in series[0][1].index]
        rows = [[n, *(number(x, units.get(n)) for x in s)] for n, s in series]
        out.append(table(rows, [axis, *labels]))
    return "\n\n".join(out)


class Output:
    """Writes to the terminal, or to ``out``/``err`` callables when run inside a server."""

    def __init__(self, as_json: bool, out=None, err=None):
        self.as_json = as_json
        self._out = out or (lambda text: typer.echo(text))
        self._err = err or (lambda text: typer.echo(text, err=True))

    def emit(
        self,
        data: Any = None,
        text: str | None = None,
        ok: bool = True,
        errors: list[dict] | None = None,
        warnings: list[dict] | None = None,
    ) -> None:
        errors, warnings = errors or [], warnings or []
        if self.as_json:
            envelope = {
                "ok": ok,
                "data": clean(data),
                "errors": clean(errors),
                "warnings": clean(warnings),
            }
            self._out(json.dumps(envelope, allow_nan=False, ensure_ascii=False))
        else:
            if text:
                self._out(text)
            for w in warnings:
                self._err(f"warning: {_describe(w)}")
            for e in errors:
                self._err(f"error: {_describe(e)}")
        if not ok:
            raise typer.Exit(1)


def _describe(item: dict) -> str:
    where = item.get("object") or item.get("op")
    message = item.get("message", "")
    return f"{where}: {message}" if where and where not in ("batch",) else message


def stderr(text: str) -> None:
    print(text, file=sys.stderr)
