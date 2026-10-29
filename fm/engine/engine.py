"""Validate a model, plan its evaluation order, and evaluate it on pandas.

Planning works on the dependency graph's strongly connected components:

* a component with one metric and no self-reference is evaluated **vectorized**, the whole
  series at once;
* a component whose cycles all pass through ``prev()`` is a **recurrence** (e.g.
  ``cash = prev(cash) + fcf``) and is stepped one period at a time;
* a component with a same-period cycle is a **circular reference** and is rejected.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from ..errors import Diagnostic, FmError, FormulaError
from ..formula import (
    FULL,
    SCALAR,
    UNKNOWN,
    AxisInfo,
    FormulaEnv,
    compile_formula,
    infer_shape,
    parse_formula,
    references,
)
from ..formula.compiler import Evaluator
from ..formula.ir import Node
from ..formula.shapes import Shape, describe, join
from ..model.schema import Check, Metric, Model
from .graph import DependencyGraph, Lag, find_cycle, topo_order
from .results import CheckResult, MetricStatus, RunResult

Override = float | int | Mapping[Any, float | None] | pd.Series


@dataclass
class CompiledMetric:
    name: str
    definition: Metric
    kind: Literal["value", "formula"]
    declared_axis: str | None
    shape: Shape = UNKNOWN
    node: Node | None = None
    refs: dict[str, Lag] = field(default_factory=dict)
    evaluator: Evaluator | None = None
    constant: Any = None
    error: Diagnostic | None = None


@dataclass
class CompiledCheck:
    name: str
    definition: Check
    refs: dict[str, Lag] = field(default_factory=dict)
    evaluator: Evaluator | None = None
    error: Diagnostic | None = None


@dataclass
class Step:
    kind: Literal["single", "recurrence"]
    members: list[str]  # evaluation order within the step


class Engine:
    """A validated, planned model ready to run.

    Construction never raises for model problems; they are collected in
    :attr:`diagnostics`, and affected metrics report ``error`` or ``blocked`` when run.
    """

    def __init__(self, model: Model):
        self.model = model
        self.diagnostics: list[Diagnostic] = []
        self.axes = self._build_axes()
        self.metrics = {name: self._prepare(name, m) for name, m in model.metrics.items()}
        self._infer_shapes()
        for cm in self.metrics.values():
            self._compile(cm)
        self.graph = DependencyGraph({n: cm.refs for n, cm in self.metrics.items()})
        self.steps = self._plan()
        self.checks = {name: self._prepare_check(name, c) for name, c in model.checks.items()}
        self._validate_tables()

    # -- construction ------------------------------------------------------------------

    def _error(self, obj: str, code: str, message: str, **detail: Any) -> Diagnostic:
        d = Diagnostic("error", code, message, obj, detail)
        self.diagnostics.append(d)
        return d

    def _fail(self, cm: CompiledMetric, code: str, message: str, **detail: Any) -> None:
        if cm.error is None:
            cm.error = self._error(f"metric:{cm.name}", code, message, **detail)

    def _build_axes(self) -> dict[str, AxisInfo]:
        axes = {}
        for name, ax in self.model.axes.items():
            historical = {str(v) for v in ax.historical}
            axes[name] = AxisInfo(
                name=name,
                kind=ax.kind,
                index=pd.Index(ax.values, name=name),
                historical=np.array([str(v) in historical for v in ax.values], dtype=bool),
            )
        default = self.model.settings.default_axis
        if default is not None and default not in axes:
            self._error("settings", "unknown_axis", f"default_axis '{default}' is not an axis")
        return axes

    @property
    def default_axis(self) -> str | None:
        default = self.model.settings.default_axis
        return default if default in self.axes else None

    def _env(self, metric_axis: str | None) -> FormulaEnv:
        return FormulaEnv(self.axes, metric_axis, self.default_axis)

    def _unknown_refs(self, obj: str, refs: Iterable[str]) -> list[str]:
        unknown = [r for r in refs if r not in self.model.metrics]
        for name in unknown:
            suggestions = difflib.get_close_matches(name, list(self.model.metrics), n=3)
            hint = f"; did you mean {', '.join(suggestions)}?" if suggestions else ""
            self._error(
                obj,
                "unknown_reference",
                f"unknown metric '{name}'{hint}",
                name=name,
                suggestions=suggestions,
            )
        return unknown

    def _prepare(self, name: str, m: Metric) -> CompiledMetric:
        kind = "value" if m.value is not None else "formula"
        cm = CompiledMetric(name, m, kind, declared_axis=m.axis)
        if m.axis is not None and m.axis not in self.axes:
            self._fail(cm, "unknown_axis", f"unknown axis '{m.axis}'")
            cm.declared_axis = None
        if kind == "value":
            if isinstance(m.value, dict):
                cm.shape = cm.declared_axis or self.default_axis
                if cm.shape is None:
                    self._fail(
                        cm,
                        "missing_axis",
                        "per-period values need an axis or settings.default_axis",
                    )
            else:
                cm.shape = cm.declared_axis
            return cm

        cm.shape = cm.declared_axis if cm.declared_axis is not None else UNKNOWN
        try:
            cm.node = parse_formula(m.formula)  # type: ignore[arg-type]
        except FormulaError as e:
            self._fail(cm, e.code, e.message, **e.detail)
            return cm
        cm.refs = references(cm.node)
        unknown = self._unknown_refs(f"metric:{name}", cm.refs)
        if unknown:
            cm.error = self.diagnostics[-1]
            for u in unknown:
                cm.refs.pop(u)
        return cm

    def _shape_of(self, name: str) -> Shape:
        return self.metrics[name].shape

    def _infer_shapes(self) -> None:
        """Fixed-point shape inference; needed because recurrences reference themselves."""
        pending = [
            cm
            for cm in self.metrics.values()
            if cm.kind == "formula" and cm.node is not None and cm.declared_axis is None
        ]
        env = self._env(None)
        # each metric can rise at most twice (unknown → scalar → axis)
        for _ in range(2 * len(pending) + 1):
            changed = False
            for cm in pending:
                if cm.error is not None:
                    continue
                try:
                    new = join(cm.shape, infer_shape(cm.node, self._shape_of, env))
                except FormulaError as e:
                    self._fail(cm, e.code, e.message, **e.detail)
                    continue
                if new != cm.shape:
                    cm.shape, changed = new, True
            if not changed:
                break
        for cm in self.metrics.values():
            if cm.shape is UNKNOWN:
                cm.shape = SCALAR

        for cm in self.metrics.values():
            if cm.kind != "formula" or cm.node is None or cm.error is not None:
                continue
            try:
                inferred = infer_shape(cm.node, self._shape_of, self._env(cm.declared_axis))
            except FormulaError as e:
                self._fail(cm, e.code, e.message, **e.detail)
                continue
            if cm.declared_axis is not None and inferred not in (SCALAR, cm.declared_axis):
                self._fail(
                    cm,
                    "axis_mismatch",
                    f"formula gives a {describe(inferred)} but the metric is declared on axis "
                    f"'{cm.declared_axis}'",
                )

    def _compile(self, cm: CompiledMetric) -> None:
        if cm.error is not None:
            return
        if cm.kind == "formula":
            cm.evaluator = compile_formula(cm.node, self._env(cm.declared_axis))  # type: ignore[arg-type]
            return
        value = cm.definition.value
        if not isinstance(value, dict):
            cm.constant = self._broadcast(cm, np.float64(value))
            return
        ax = self.axes[cm.shape]  # type: ignore[index]
        data = np.full(len(ax), np.nan)
        bad = []
        for key, v in value.items():
            try:
                pos = ax.index.get_loc(ax.lookup(key))
            except KeyError:
                bad.append(key)
                continue
            data[pos] = np.nan if v is None else v
        if bad:
            self._fail(
                cm, "invalid_value", f"values for {bad} are not on axis '{ax.name}'", keys=bad
            )
            return
        cm.constant = pd.Series(data, index=ax.index, name=cm.name)

    def _plan(self) -> list[Step]:
        steps: list[Step] = []
        for comp in self.graph.components():
            first = self.metrics[comp[0]]
            if len(comp) == 1 and comp[0] not in first.refs:
                steps.append(Step("single", comp))
                continue
            members = set(comp)
            same_period = {
                m: [d for d, lag in self.metrics[m].refs.items() if d in members and not lag]
                for m in comp
            }
            cycle = find_cycle(comp, same_period)
            if cycle is not None:
                message = f"circular reference: {' → '.join(cycle)}"
                if len(cycle) == 2:
                    message += f"; use prev({cycle[0]}) to refer to the previous period"
                for m in comp:
                    self._fail(self.metrics[m], "circular_reference", message, path=cycle)
                steps.extend(Step("single", [m]) for m in comp)
                continue
            noncausal = [
                (m, d)
                for m in comp
                for d, lag in self.metrics[m].refs.items()
                if d in members and lag is FULL
            ]
            if noncausal:
                m, d = noncausal[0]
                message = (
                    f"'{m}' reads every period of '{d}' (e.g. total() or next()), but '{d}' "
                    f"depends on '{m}' through prev(); this cannot be evaluated period by period"
                )
                for name in comp:
                    self._fail(self.metrics[name], "noncausal_recurrence", message, pair=[m, d])
                steps.extend(Step("single", [m]) for m in comp)
                continue
            shapes = {self.metrics[m].shape for m in comp}
            if len(shapes) != 1 or SCALAR in shapes:
                message = (
                    "metrics in a period recurrence must all be series on one axis; got "
                    + ", ".join(f"{m}: {describe(self.metrics[m].shape)}" for m in comp)
                )
                for name in comp:
                    self._fail(self.metrics[name], "recurrence_axis", message)
                steps.extend(Step("single", [m]) for m in comp)
                continue
            steps.append(Step("recurrence", topo_order(comp, same_period)))
        return steps

    def _prepare_check(self, name: str, c: Check) -> CompiledCheck:
        cc = CompiledCheck(name, c)
        obj = f"check:{name}"
        try:
            node = parse_formula(c.expr)
            cc.refs = references(node)
            if self._unknown_refs(obj, cc.refs):
                cc.error = self.diagnostics[-1]
                return cc
            env = self._env(None)
            infer_shape(node, self._shape_of, env)
            cc.evaluator = compile_formula(node, env)
        except FormulaError as e:
            cc.error = self._error(obj, e.code, e.message, **e.detail)
        return cc

    def _validate_tables(self) -> None:
        for name, table in self.model.tables.items():
            obj = f"table:{name}"
            if table.axis is not None and table.axis not in self.axes:
                self._error(obj, "unknown_axis", f"unknown axis '{table.axis}'")
            self._unknown_refs(obj, [r.metric for r in table.rows if r.metric is not None])

    # -- queries -----------------------------------------------------------------------

    def shape(self, name: str) -> str | None:
        """The axis of a metric, or ``None`` for a scalar."""
        self._require(name)
        return self.metrics[name].shape  # type: ignore[return-value]

    def trace(self, name: str, direction: str = "upstream", depth: int | None = None) -> list[str]:
        self._require(name)
        if direction == "upstream":
            return self.graph.upstream(name, depth)
        if direction == "downstream":
            return self.graph.downstream(name, depth)
        raise FmError("invalid_argument", "direction must be 'upstream' or 'downstream'")

    def _require(self, name: str) -> None:
        if name not in self.metrics:
            suggestions = difflib.get_close_matches(name, list(self.metrics), n=3)
            hint = f"; did you mean {', '.join(suggestions)}?" if suggestions else ""
            raise FmError(
                "unknown_metric", f"unknown metric '{name}'{hint}", suggestions=suggestions
            )

    # -- running -----------------------------------------------------------------------

    def with_overrides(self, overrides: Mapping[str, Override]) -> Engine:
        """A new engine where each overridden metric becomes a fixed value.

        A scalar override keeps the metric's axis, broadcasting across periods, so the
        shapes of dependants do not change.
        """
        metrics = dict(self.model.metrics)
        for name, raw in overrides.items():
            self._require(name)
            cm = self.metrics[name]
            axis = cm.declared_axis or (cm.shape if isinstance(cm.shape, str) else None)
            if isinstance(raw, pd.Series):
                value: Any = {k: (None if pd.isna(v) else float(v)) for k, v in raw.items()}
            elif isinstance(raw, Mapping):
                value = {k: (None if v is None else float(v)) for k, v in raw.items()}
            else:
                value = float(raw)
            metrics[name] = cm.definition.model_copy(
                update={"value": value, "formula": None, "axis": axis}
            )
        return Engine(self.model.model_copy(update={"metrics": metrics}))

    def run(
        self,
        overrides: Mapping[str, Override] | None = None,
        outputs: Iterable[str] | None = None,
        checks: bool | None = None,
    ) -> RunResult:
        """Evaluate the model.

        ``overrides`` temporarily replace metric definitions with values; the model itself
        is not changed. ``outputs`` limits evaluation to what those metrics need. Checks run
        by default only on a full run.
        """
        if overrides:
            return self.with_overrides(overrides).run(outputs=outputs, checks=checks)
        selected = list(outputs) if outputs is not None else None
        for name in selected or ():
            self._require(name)
        run_checks = (selected is None) if checks is None else checks

        needed: set[str] | None = None
        if selected is not None:
            roots = set(selected)
            if run_checks:
                roots.update(d for c in self.checks.values() for d in c.refs)
            needed = self.graph.closure(roots)

        values: dict[str, Any] = {}
        status: dict[str, MetricStatus] = {}
        diagnostics: list[Diagnostic] = []
        with np.errstate(all="ignore"):
            for step in self.steps:
                if needed is not None and needed.isdisjoint(step.members):
                    continue
                if step.kind == "single":
                    self._eval_single(step.members[0], values, status, diagnostics)
                else:
                    self._eval_recurrence(step.members, values, status, diagnostics)
            check_results = self._run_checks(values, status, diagnostics) if run_checks else {}

        shown = selected if selected is not None else [n for n in self.metrics if n in status]
        return RunResult(
            values={n: values[n] for n in shown if n in values},
            status={n: status[n] for n in shown},
            checks=check_results,
            diagnostics=[*self.diagnostics, *diagnostics],
        )

    @staticmethod
    def _failed_dep(names: Iterable[str], status: Mapping[str, MetricStatus]) -> str | None:
        for d in names:
            s = status.get(d)
            if s is not None and s.state in ("error", "blocked"):
                return d
        return None

    def _broadcast(self, cm: CompiledMetric, raw: Any) -> Any:
        """Coerce a raw formula result to the metric's shape."""
        if cm.shape is SCALAR:
            if isinstance(raw, pd.Series | np.ndarray) and np.ndim(raw) > 0:
                raise TypeError("formula produced a series for a scalar metric")
            return np.bool_(raw) if isinstance(raw, bool | np.bool_) else np.float64(raw)
        ax = self.axes[cm.shape]  # type: ignore[index]
        if isinstance(raw, pd.Series):
            out = raw if raw.index.equals(ax.index) else raw.reindex(ax.index)
            return out.rename(cm.name)
        if isinstance(raw, np.ndarray) and raw.ndim == 1:
            return pd.Series(raw, index=ax.index, name=cm.name)
        return pd.Series(np.full(len(ax), raw), index=ax.index, name=cm.name)

    def _value_status(
        self,
        cm: CompiledMetric,
        value: Any,
        status: Mapping[str, MetricStatus],
        diagnostics: list[Diagnostic],
    ) -> MetricStatus:
        try:
            data = np.atleast_1d(np.asarray(value, dtype=float))
        except (TypeError, ValueError):
            return MetricStatus("ok")
        missing, infinite = np.isnan(data), np.isinf(data)
        if not (missing.any() or infinite.any()):
            return MetricStatus("ok")
        labels = list(value.index) if isinstance(value, pd.Series) else None

        def where(mask) -> str:
            return (
                ""
                if labels is None
                else " in " + ", ".join(str(lbl) for lbl, m in zip(labels, mask, strict=True) if m)
            )

        parts = []
        if missing.any():
            parts.append(f"missing values{where(missing)}")
        if infinite.any():
            parts.append(f"infinite values{where(infinite)} (division by zero?)")
        message = "; ".join(parts)
        inherited = any(status.get(d) is not None and status[d].state == "warning" for d in cm.refs)
        if not inherited:
            code = "missing_value" if missing.any() else "nonfinite_value"
            diagnostics.append(Diagnostic("warning", code, message, f"metric:{cm.name}"))
        return MetricStatus("warning", message)

    def _eval_single(self, name, values, status, diagnostics) -> None:
        cm = self.metrics[name]
        if cm.error is not None:
            status[name] = MetricStatus("error", cm.error.message)
            return
        bad = self._failed_dep(cm.refs, status)
        if bad is not None:
            status[name] = MetricStatus("blocked", f"depends on '{bad}', which has errors")
            return
        try:
            value = cm.constant if cm.kind == "value" else self._broadcast(cm, cm.evaluator(values))
        except Exception as e:  # noqa: BLE001 - any calculation failure is reported, not raised
            message = f"{type(e).__name__}: {e}"
            status[name] = MetricStatus("error", message)
            diagnostics.append(Diagnostic("error", "calc_error", message, f"metric:{name}"))
            return
        values[name] = value
        status[name] = self._value_status(cm, value, status, diagnostics)

    def _eval_recurrence(self, members, values, status, diagnostics) -> None:
        """Step through periods; within each period, members go in same-period order.

        Each formula is evaluated over the whole series and only position ``t`` is kept.
        Formulas in a recurrence are causal (no reductions or next()), so position ``t``
        only reads positions already filled.
        """
        group = set(members)
        broken = next((m for m in members if self.metrics[m].error is not None), None)
        if broken is not None:
            for m in members:
                cm = self.metrics[m]
                status[m] = (
                    MetricStatus("error", cm.error.message)
                    if cm.error is not None
                    else MetricStatus(
                        "blocked", f"in a recurrence with '{broken}', which has errors"
                    )
                )
            return
        external = {d for m in members for d in self.metrics[m].refs if d not in group}
        bad = self._failed_dep(external, status)
        if bad is not None:
            for m in members:
                status[m] = MetricStatus("blocked", f"depends on '{bad}', which has errors")
            return

        ax = self.axes[self.metrics[members[0]].shape]  # type: ignore[index]
        arrays = {m: np.full(len(ax), np.nan) for m in members}
        for m in members:
            values[m] = pd.Series(arrays[m].copy(), index=ax.index, name=m)
        try:
            for t in range(len(ax)):
                for m in members:
                    cm = self.metrics[m]
                    out = self._broadcast(cm, cm.evaluator(values))
                    arrays[m][t] = float(out.iloc[t])
                    values[m] = pd.Series(arrays[m].copy(), index=ax.index, name=m)
        except Exception as e:  # noqa: BLE001
            message = f"{type(e).__name__}: {e} (at {ax.name}={ax.index[t]}, in '{m}')"
            for name in members:
                values.pop(name, None)
                status[name] = MetricStatus("error", message)
            diagnostics.append(Diagnostic("error", "calc_error", message, f"metric:{m}"))
            return
        for m in members:
            status[m] = self._value_status(self.metrics[m], values[m], status, diagnostics)

    def _run_checks(self, values, status, diagnostics) -> dict[str, CheckResult]:
        results = {}
        for name, cc in self.checks.items():
            severity = cc.definition.severity
            if cc.error is not None:
                results[name] = CheckResult(name, "error", severity, cc.error.message)
                continue
            bad = self._failed_dep(cc.refs, status)
            if bad is not None:
                results[name] = CheckResult(
                    name, "blocked", severity, f"depends on '{bad}', which has errors"
                )
                continue
            try:
                raw = cc.evaluator(values)
            except Exception as e:  # noqa: BLE001
                results[name] = CheckResult(name, "error", severity, f"{type(e).__name__}: {e}")
                continue
            results[name] = self._check_result(cc, raw, diagnostics)
        return results

    @staticmethod
    def _check_result(cc: CompiledCheck, raw: Any, diagnostics: list[Diagnostic]) -> CheckResult:
        severity = cc.definition.severity
        labels = list(raw.index) if isinstance(raw, pd.Series) else [None]
        data = np.atleast_1d(np.asarray(raw, dtype=float))
        is_bool = (isinstance(raw, pd.Series) and raw.dtype == bool) or isinstance(
            raw, bool | np.bool_
        )
        if is_bool:
            failed = np.isnan(data) | (data == 0)
        else:
            failed = ~(np.abs(data) <= cc.definition.tolerance)
        if not failed.any():
            return CheckResult(cc.name, "passed", severity)
        failing = [
            {
                "period": None if lbl is None else str(lbl),
                "value": None if np.isnan(v) else float(v),
            }
            for lbl, v, f in zip(labels, data, failed, strict=True)
            if f
        ]
        where = ", ".join(f["period"] for f in failing if f["period"] is not None)
        message = "check failed" + (f" in {where}" if where else "")
        diagnostics.append(
            Diagnostic(severity, "check_failed", message, f"check:{cc.name}", {"failing": failing})
        )
        return CheckResult(cc.name, "failed", severity, message, failing)
