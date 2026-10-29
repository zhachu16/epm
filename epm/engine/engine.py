"""Validate a model, plan its evaluation order, and evaluate it on pandas.

Planning works on the dependency graph's strongly connected components:

* a component with one metric and no self-reference is evaluated **vectorized**, the whole
  series at once;
* a component whose cycles all pass through ``prev()`` is a **recurrence** (e.g.
  ``cash = prev(cash) + fcf``) and is stepped one period at a time;
* a component with a same-period cycle is a **circular reference**: solved by iteration
  if declared under ``circularities``, rejected otherwise (see :mod:`.planner`).

Metrics can take values from CSV/XLSX sources: ``input`` binds the whole metric, while
``history`` fills historical periods from a source and leaves forecast periods to the
formula. Units are inferred alongside shapes, and mixing them is reported.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from ..errors import Diagnostic, EpmError, FormulaError
from ..formula import (
    SCALAR,
    UNKNOWN,
    AxisInfo,
    FormulaEnv,
    compile_formula,
    infer_shape,
    parse_formula,
    references,
)
from ..formula.analysis import scope_start
from ..formula.compiler import Evaluator
from ..formula.ir import Node
from ..formula.shapes import Shape, describe, join
from ..formula.units import ANY, CONFLICT, Unit, UnitIssue, infer_unit, unit_key
from ..formula.units import join as join_units
from ..model.schema import Binding, Check, Metric, Model
from ..sources import DirectoryFiles, FileProvider, SourceError, SourceLoader
from .cache import StepEntry, ValueCache
from .graph import DependencyGraph, Lag
from .planner import Step, plan
from .results import CheckResult, MetricStatus, RunResult, SolverStats

Override = float | int | Mapping[Any, float | None] | pd.Series

# Bump when evaluation semantics change, so cached values from older versions are ignored.
ENGINE_VERSION = "1"


@dataclass
class CompiledMetric:
    name: str
    definition: Metric
    kind: Literal["value", "input", "formula"]
    declared_axis: str | None
    shape: Shape = UNKNOWN
    unit: Unit = ANY
    history: np.ndarray | None = None  # source values; only historical positions are used
    start: int = 0  # first in-scope axis position (> 0 for forecast-only metrics)
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
    start: int = 0


class SolverError(Exception):
    pass


class Engine:
    """A validated, planned model ready to run.

    Construction never raises for model problems; they are collected in
    :attr:`diagnostics`, and affected metrics report ``error`` or ``blocked`` when run.
    """

    def __init__(
        self,
        model: Model,
        files: FileProvider | str | Path | None = None,
        *,
        sources: SourceLoader | None = None,
    ):
        """``files`` provides input files: a directory path or any :class:`FileProvider`.

        ``sources`` lets an engine derived from another reuse its already-read files.
        """
        self.model = model
        self.files = DirectoryFiles(files) if isinstance(files, str | Path) else files
        self.sources = sources or SourceLoader(model.sources, self.files)
        self.diagnostics: list[Diagnostic] = []
        self.starts: dict[str, int] = {}  # filled by scope inference, read by compiled formulas
        self.axes = self._build_axes()
        self._source_errors = self._validate_sources()
        self._unmatched_reported: set[tuple[str, str]] = set()
        self.metrics = {name: self._prepare(name, m) for name, m in model.metrics.items()}
        self._infer_shapes()
        self._infer_units()
        self._infer_scopes()
        for cm in self.metrics.values():
            self._compile(cm)
        self.graph = DependencyGraph({n: cm.refs for n, cm in self.metrics.items()})
        self._validate_circularities()
        self.steps = self._plan()
        self.fingerprints: dict[str, str] = {}
        self.step_keys = self._fingerprint_steps()
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

    def _warn(self, obj: str, code: str, message: str, **detail: Any) -> None:
        self.diagnostics.append(Diagnostic("warning", code, message, obj, detail))

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

    def _validate_sources(self) -> dict[str, Diagnostic]:
        errors = {}
        for name, source in self.model.sources.items():
            obj = f"source:{name}"
            if source.axis is not None and source.axis not in self.axes:
                errors[name] = self._error(obj, "unknown_axis", f"unknown axis '{source.axis}'")
                continue
            try:
                self.sources.table(name)
            except SourceError as e:
                errors[name] = self._error(obj, e.code, e.message, **e.detail)
        return errors

    def _binding_axis(self, m: Metric, binding: Binding) -> str | None:
        source = self.model.sources.get(binding.source)
        source_axis = source.axis if source is not None else None
        return m.axis or source_axis or self.default_axis

    def _env(self, metric_axis: str | None) -> FormulaEnv:
        return FormulaEnv(self.axes, metric_axis, self.default_axis, self.starts)

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
        kind = "value" if m.value is not None else "input" if m.input is not None else "formula"
        cm = CompiledMetric(name, m, kind, declared_axis=m.axis)
        if m.axis is not None and m.axis not in self.axes:
            self._fail(cm, "unknown_axis", f"unknown axis '{m.axis}'")
            cm.declared_axis = None
            return cm
        for binding in (m.input, m.history):
            if binding is not None and binding.source not in self.model.sources:
                suggestions = difflib.get_close_matches(binding.source, list(self.model.sources))
                hint = f"; did you mean {', '.join(suggestions)}?" if suggestions else ""
                self._fail(
                    cm,
                    "unknown_source",
                    f"unknown source '{binding.source}'{hint}",
                    suggestions=suggestions,
                )
                return cm
        series_binding = m.history if m.history is not None else m.input
        if series_binding is not None and series_binding.item is not None:
            # a bound series fixes the axis: the metric's, else the source's, else the default
            cm.declared_axis = self._binding_axis(m, series_binding)
            if cm.declared_axis is None:
                self._fail(
                    cm,
                    "missing_axis",
                    "a bound series needs an axis: set 'axis' on the "
                    "metric or the source, or settings.default_axis",
                )
                return cm
            if cm.declared_axis not in self.axes:  # the source's axis; reported there
                cm.error = self._source_errors[series_binding.source]
                return cm
        if kind == "input":
            cm.shape = cm.declared_axis
            return cm
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

    def _infer_scopes(self) -> None:
        """Find forecast-only metrics: declared, or reading one point by point."""

        def forecast_start(cm: CompiledMetric) -> int:
            return int(self.axes[cm.shape].historical.sum())  # type: ignore[index]

        pending = []
        for cm in self.metrics.values():
            if not isinstance(cm.shape, str) or cm.shape not in self.axes:
                continue
            scope = cm.definition.scope
            if scope == "forecast":
                cm.start = forecast_start(cm)
            elif (
                scope is None
                and cm.kind == "formula"
                and cm.definition.history is None
                and cm.node is not None
                and cm.error is None
            ):
                pending.append(cm)
        lookup = lambda n: self.metrics[n].start if n in self.metrics else 0  # noqa: E731
        for _ in range(len(pending) + 1):
            changed = False
            for cm in pending:
                new = min(scope_start(cm.node, lookup), forecast_start(cm))  # type: ignore[arg-type]
                if new > cm.start:
                    cm.start, changed = new, True
            if not changed:
                break
        self.starts.update({n: cm.start for n, cm in self.metrics.items() if cm.start})

    def _unit_of(self, name: str) -> Unit:
        cm = self.metrics.get(name)
        return ANY if cm is None else cm.unit

    def _infer_units(self) -> None:
        """Fixed-point unit inference for metrics without a declared unit, then reporting."""
        pending = []
        for cm in self.metrics.values():
            cm.unit = cm.definition.unit
            if cm.unit is None and cm.kind == "formula" and cm.error is None:
                pending.append(cm)
        for _ in range(2 * len(pending) + 1):
            changed = False
            for cm in pending:
                new = join_units(cm.unit, infer_unit(cm.node, self._unit_of))  # type: ignore[arg-type]
                if new is not cm.unit and (
                    new is CONFLICT or cm.unit is None or unit_key(new) != unit_key(cm.unit)  # type: ignore[arg-type]
                ):
                    cm.unit, changed = new, True
            if not changed:
                break

        strict = self.model.settings.strict_units
        for cm in self.metrics.values():
            if cm.kind != "formula" or cm.node is None or cm.error is not None:
                continue
            issues: list[UnitIssue] = []
            inferred = infer_unit(cm.node, self._unit_of, issues)
            declared = cm.definition.unit
            if (
                declared is not None
                and isinstance(inferred, str)
                and unit_key(inferred) != unit_key(declared)
            ):
                issues.append(
                    UnitIssue(f"formula gives '{inferred}' but the metric's unit is '{declared}'")
                )
            for issue in issues:
                detail = {} if issue.col is None else {"col": issue.col}
                if strict:
                    self._fail(cm, "unit_mismatch", issue.message, **detail)
                else:
                    self._warn(f"metric:{cm.name}", "unit_mismatch", issue.message, **detail)

    def _load(self, cm: CompiledMetric, binding: Binding) -> np.ndarray | float | None:
        """Resolve a binding to numbers on the metric's axis, or a scalar for a cell."""
        if binding.source in self._source_errors:
            cm.error = self._source_errors[binding.source]
            return None
        try:
            if binding.cell is not None:
                value, uncached = self.sources.load_cell(binding)
            else:
                loaded = self.sources.load_series(binding, self.axes[cm.declared_axis])  # type: ignore[index]
                value, uncached = loaded.data, loaded.uncached
                key = (binding.source, cm.declared_axis)
                if loaded.unmatched and key not in self._unmatched_reported:
                    self._unmatched_reported.add(key)  # type: ignore[arg-type]
                    self.diagnostics.append(
                        Diagnostic(
                            "info",
                            "unmatched_labels",
                            f"labels {', '.join(loaded.unmatched)} are not on axis "
                            f"'{cm.declared_axis}' and are ignored",
                            f"source:{binding.source}",
                            {"labels": loaded.unmatched},
                        )
                    )
        except SourceError as e:
            self._fail(cm, e.code, e.message, **e.detail)
            return None
        if uncached:
            self._warn(
                f"metric:{cm.name}",
                "uncached_formula",
                f"{', '.join(uncached)} contain formulas with no saved value; open and save the "
                "file in Excel so the values are stored",
                cells=uncached,
            )
        return value

    def _compile(self, cm: CompiledMetric) -> None:
        if cm.error is not None:
            return
        if cm.kind == "input":
            loaded = self._load(cm, cm.definition.input)  # type: ignore[arg-type]
            if loaded is None:
                return
            if isinstance(loaded, np.ndarray):
                ax = self.axes[cm.shape]  # type: ignore[index]
                cm.constant = self._mask(cm, pd.Series(loaded, index=ax.index, name=cm.name))
            else:
                cm.constant = self._mask(cm, self._broadcast(cm, np.float64(loaded)))
            return
        if cm.kind == "formula":
            if cm.definition.history is not None:
                ax = self.axes[cm.declared_axis]  # type: ignore[index]
                if not ax.historical.any():
                    self._warn(
                        f"metric:{cm.name}",
                        "unused_history",
                        f"'history' has no effect: axis '{ax.name}' has no historical periods",
                    )
                cm.history = self._load(cm, cm.definition.history)  # type: ignore[assignment]
                if cm.history is None:
                    return
            cm.evaluator = compile_formula(cm.node, self._env(cm.declared_axis))  # type: ignore[arg-type]
            return
        value = cm.definition.value
        if not isinstance(value, dict):
            cm.constant = self._mask(cm, self._broadcast(cm, np.float64(value)))
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
        cm.constant = self._mask(cm, pd.Series(data, index=ax.index, name=cm.name))

    def _validate_circularities(self) -> None:
        seen: dict[str, str] = {}
        for name, circ in self.model.circularities.items():
            obj = f"circularity:{name}"
            self._unknown_refs(obj, circ.metrics)
            for m in circ.metrics:
                if m in seen:
                    self._error(
                        obj,
                        "duplicate_member",
                        f"'{m}' is already in circularity '{seen[m]}'",
                        metric=m,
                    )
                seen.setdefault(m, name)

    def _plan(self) -> list[Step]:
        circularities = {
            name: c.model_copy(update={"metrics": [m for m in c.metrics if m in self.metrics]})
            for name, c in self.model.circularities.items()
        }

        def fail(metric: str, code: str, message: str, detail: dict) -> None:
            self._fail(self.metrics[metric], code, message, **detail)

        steps, used = plan(
            {n: cm.refs for n, cm in self.metrics.items()},
            {n: cm.shape for n, cm in self.metrics.items()},
            circularities,
            fail,
        )
        for name in self.model.circularities:
            if name not in used and not any(
                d.object == f"circularity:{name}" for d in self.diagnostics
            ):
                self._warn(
                    f"circularity:{name}",
                    "unused_circularity",
                    "these metrics do not form a same-period loop, so nothing is iterated",
                )
        return steps

    def _own_hash(self, cm: CompiledMetric) -> str:
        definition = cm.definition.model_dump(
            mode="json", include={"axis", "value", "input", "formula", "history", "scope"}
        )
        axis = self.model.axes.get(cm.shape) if isinstance(cm.shape, str) else None
        bindings = [b.source for b in (cm.definition.input, cm.definition.history) if b]
        payload = {
            "definition": definition,
            "shape": cm.shape if isinstance(cm.shape, str) else None,
            "axis": axis.model_dump(mode="json", include={"values", "historical"})
            if axis
            else None,
            "start": cm.start,
            "default_axis": self.default_axis,
            "error": cm.error.message if cm.error else None,
            "sources": {b: self.sources.fingerprint(b) for b in bindings},
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def _fingerprint_steps(self) -> list[str]:
        """Fingerprint every step from its definitions and the fingerprints it reads."""
        keys = []
        for step in self.steps:
            members = set(step.members)
            h = hashlib.sha256(f"{ENGINE_VERSION}|{step.kind}".encode())
            for name in sorted({step.circularity, *(b.circularity for b in step.blocks)} - {None}):
                circ = self.model.circularities[name]  # type: ignore[index]
                h.update(
                    json.dumps(
                        circ.model_dump(mode="json", exclude={"folder", "description"}),
                        sort_keys=True,
                    ).encode()
                )
            for m in step.members:  # order matters for Gauss-Seidel iteration
                h.update(f"|{m}={self._own_hash(self.metrics[m])}".encode())
            external = sorted({d for m in members for d in self.metrics[m].refs} - members)
            for d in external:
                # a rejected loop's members read each other; they are errors either way
                h.update(f"|<{d}={self.fingerprints.get(d, 'unresolved')}".encode())
            key = h.hexdigest()
            keys.append(key)
            for m in step.members:
                self.fingerprints[m] = hashlib.sha256(f"{key}|{m}".encode()).hexdigest()
        return keys

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
            cc.start = scope_start(node, lambda n: self.starts.get(n, 0))
        except FormulaError as e:
            cc.error = self._error(obj, e.code, e.message, **e.detail)
            return cc
        issues: list[UnitIssue] = []
        infer_unit(node, self._unit_of, issues)
        for issue in issues:
            if self.model.settings.strict_units:
                cc.error = cc.error or self._error(obj, "unit_mismatch", issue.message)
            else:
                self._warn(obj, "unit_mismatch", issue.message)
        return cc

    def _validate_tables(self) -> None:
        for name, table in self.model.tables.items():
            obj = f"table:{name}"
            if table.axis is not None and table.axis not in self.axes:
                self._error(obj, "unknown_axis", f"unknown axis '{table.axis}'")
            self._unknown_refs(obj, [r.metric for r in table.rows if r.metric is not None])
        self._unknown_refs("settings", self.model.settings.key_outputs)

    # -- queries -----------------------------------------------------------------------

    def shape(self, name: str) -> str | None:
        """The axis of a metric, or ``None`` for a scalar."""
        self._require(name)
        return self.metrics[name].shape  # type: ignore[return-value]

    def unit(self, name: str) -> str | None:
        """The declared or inferred unit of a metric, or ``None`` if unknown or mixed."""
        self._require(name)
        unit = self.metrics[name].unit
        return unit if isinstance(unit, str) else None

    def trace(self, name: str, direction: str = "upstream", depth: int | None = None) -> list[str]:
        self._require(name)
        if direction == "upstream":
            return self.graph.upstream(name, depth)
        if direction == "downstream":
            return self.graph.downstream(name, depth)
        raise EpmError("invalid_argument", "direction must be 'upstream' or 'downstream'")

    def _require(self, name: str) -> None:
        if name not in self.metrics:
            suggestions = difflib.get_close_matches(name, list(self.metrics), n=3)
            hint = f"; did you mean {', '.join(suggestions)}?" if suggestions else ""
            raise EpmError(
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
                update={
                    "value": value,
                    "input": None,
                    "formula": None,
                    "history": None,
                    "axis": axis,
                    "scope": "forecast" if cm.start else None,
                }
            )
        model = self.model.model_copy(update={"metrics": metrics})
        return Engine(model, self.files, sources=self.sources)

    def run(
        self,
        overrides: Mapping[str, Override] | None = None,
        outputs: Iterable[str] | None = None,
        checks: bool | None = None,
        cache: ValueCache | None = None,
    ) -> RunResult:
        """Evaluate the model.

        ``overrides`` temporarily replace metric definitions with values; the model itself
        is not changed. ``outputs`` limits evaluation to what those metrics need. Checks run
        by default only on a full run. Steps whose fingerprint is in ``cache`` are reused
        instead of evaluated, and newly evaluated steps are added to it.
        """
        if overrides:
            return self.with_overrides(overrides).run(outputs=outputs, checks=checks, cache=cache)
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
        solver: dict[str, SolverStats] = {}
        computed: list[str] = []
        reused: list[str] = []
        with np.errstate(all="ignore"):
            for step, key in zip(self.steps, self.step_keys, strict=True):
                if needed is not None and needed.isdisjoint(step.members):
                    continue
                entry = cache.get(key) if cache is not None else None
                if entry is not None:
                    values.update(entry.values)
                    status.update(entry.status)
                    diagnostics.extend(entry.diagnostics)
                    solver.update(entry.solver)
                    reused.extend(step.members)
                    continue
                first_diagnostic = len(diagnostics)
                if step.kind == "single":
                    self._eval_single(step.members[0], values, status, diagnostics)
                elif step.kind == "circular":
                    self._eval_circular(step, values, status, diagnostics, solver)
                else:
                    self._eval_recurrence(step, values, status, diagnostics, solver)
                computed.extend(step.members)
                if cache is not None:
                    names = {step.circularity, *(b.circularity for b in step.blocks)}
                    cache.put(
                        key,
                        StepEntry(
                            {m: values[m] for m in step.members if m in values},
                            {m: status[m] for m in step.members},
                            diagnostics[first_diagnostic:],
                            {k: v for k, v in solver.items() if k in names},
                        ),
                    )
            check_results = self._run_checks(values, status, diagnostics) if run_checks else {}

        shown = selected if selected is not None else [n for n in self.metrics if n in status]
        return RunResult(
            values={n: _copy(values[n]) for n in shown if n in values},
            status={n: status[n] for n in shown},
            checks=check_results,
            diagnostics=[*self.diagnostics, *diagnostics],
            solver=solver,
            computed=computed,
            reused=reused,
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

    def _with_history(self, cm: CompiledMetric, value: pd.Series) -> pd.Series:
        """Replace historical periods with the values bound by ``history``."""
        if cm.history is None:
            return value
        mask = self.axes[cm.shape].historical  # type: ignore[index]
        data = value.to_numpy(dtype=float, copy=True)
        data[mask] = cm.history[mask]
        return pd.Series(data, index=value.index, name=cm.name)

    @staticmethod
    def _mask(cm: CompiledMetric, value: Any) -> Any:
        """Blank out the periods before a forecast-only metric exists."""
        if not cm.start or not isinstance(value, pd.Series):
            return value
        data = value.to_numpy(dtype=float, copy=True)
        data[: cm.start] = np.nan
        return pd.Series(data, index=value.index, name=cm.name)

    def _finish(self, cm: CompiledMetric, raw: Any) -> Any:
        """Shape a formula result, then apply history and scope."""
        return self._mask(cm, self._with_history(cm, self._broadcast(cm, raw)))

    def _computes_at(self, cm: CompiledMetric, t: int, ax: AxisInfo) -> bool:
        """Whether period ``t`` comes from the formula (not history, not out of scope)."""
        if t < cm.start:
            return False
        return not (cm.history is not None and ax.historical[t])

    def _ready(self, members: list[str], status: dict[str, MetricStatus]) -> bool:
        """Mark a group errored or blocked if it, or anything it reads, has errors."""
        group = set(members)
        broken = next((m for m in members if self.metrics[m].error is not None), None)
        if broken is not None:
            for m in members:
                cm = self.metrics[m]
                status[m] = (
                    MetricStatus("error", cm.error.message)
                    if cm.error is not None
                    else MetricStatus("blocked", f"evaluated with '{broken}', which has errors")
                )
            return False
        external = {d for m in members for d in self.metrics[m].refs if d not in group}
        bad = self._failed_dep(external, status)
        if bad is not None:
            for m in members:
                status[m] = MetricStatus("blocked", f"depends on '{bad}', which has errors")
            return False
        return True

    def _group_failed(self, members, message, code, values, status, diagnostics) -> None:
        for name in members:
            values.pop(name, None)
            status[name] = MetricStatus("error", message)
        diagnostics.append(Diagnostic("error", code, message, f"metric:{members[0]}"))

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
        if cm.start:
            missing[: cm.start] = False
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
        if not self._ready([name], status):
            return
        try:
            value = self._finish(cm, cm.evaluator(values)) if cm.kind == "formula" else cm.constant
        except Exception as e:  # noqa: BLE001 - any calculation failure is reported, not raised
            message = f"{type(e).__name__}: {e}"
            status[name] = MetricStatus("error", message)
            diagnostics.append(Diagnostic("error", "calc_error", message, f"metric:{name}"))
            return
        values[name] = value
        status[name] = self._value_status(cm, value, status, diagnostics)

    def _eval_circular(self, step: Step, values, status, diagnostics, solver) -> None:
        """Solve a declared same-period loop by iterating over whole series (Gauss-Seidel)."""
        members = step.members
        if not self._ready(members, status):
            return
        name = step.circularity
        circ = self.model.circularities[name]  # type: ignore[index]
        for m in members:
            cm = self.metrics[m]
            values[m] = (
                self._finish(cm, np.float64(circ.seed)) if cm.kind == "formula" else cm.constant
            )
        residual = np.inf
        iterations = 0
        try:
            for iterations in range(1, circ.max_iter + 1):  # noqa: B007
                residual = 0.0
                for m in members:
                    cm = self.metrics[m]
                    old = values[m]
                    new = self._finish(cm, cm.evaluator(values))
                    if circ.damping < 1:
                        new = old + circ.damping * (new - old)
                    if np.isinf(np.asarray(new, dtype=float)).any():
                        raise SolverError(f"'{m}' became infinite after {iterations} iterations")
                    residual = max(residual, _max_change(new, old))
                    values[m] = new
                if residual <= circ.tol:
                    break
            else:
                raise SolverError(
                    f"did not converge in {circ.max_iter} iterations (largest change "
                    f"{residual:.3g}, tolerance {circ.tol:g})"
                )
        except Exception as e:  # noqa: BLE001
            message = (
                f"circularity '{name}': {e}"
                if isinstance(e, SolverError)
                else (f"{type(e).__name__}: {e} (solving circularity '{name}')")
            )
            solver[name] = SolverStats(name, False, iterations, float(residual), message=message)
            code = "not_converged" if isinstance(e, SolverError) else "calc_error"
            self._group_failed(members, message, code, values, status, diagnostics)
            return
        solver[name] = SolverStats(name, True, iterations, float(residual))
        for m in members:
            status[m] = self._solved_status(m, name, iterations, values, status, diagnostics)

    def _eval_recurrence(self, step: Step, values, status, diagnostics, solver) -> None:
        """Step through periods; within each period, blocks go in same-period order.

        Each formula is evaluated over the whole series and only position ``t`` is kept.
        Formulas in a recurrence are causal (no reductions or next()), so position ``t``
        only reads positions already filled. A declared circular block is iterated at each
        period, starting from the previous period's solution.
        """
        members = step.members
        if not self._ready(members, status):
            return
        ax = self.axes[self.metrics[members[0]].shape]  # type: ignore[index]
        arrays = {m: np.full(len(ax), np.nan) for m in members}

        def store(m: str) -> None:
            values[m] = pd.Series(arrays[m].copy(), index=ax.index, name=m)

        def evaluate_at(m: str, t: int) -> float:
            cm = self.metrics[m]
            return float(self._broadcast(cm, cm.evaluator(values)).iloc[t])

        for m in members:
            cm = self.metrics[m]
            if cm.history is not None:
                arrays[m][ax.historical] = cm.history[ax.historical]
            store(m)
        stats = {
            b.circularity: SolverStats(b.circularity, True, 0, 0.0, periods={})
            for b in step.blocks
            if b.circularity
        }
        t, m = 0, members[0]
        try:
            for t in range(len(ax)):
                for block in step.blocks:
                    active = [x for x in block.members if self._computes_at(self.metrics[x], t, ax)]
                    if block.circularity is None:
                        for m in active:
                            arrays[m][t] = evaluate_at(m, t)
                            store(m)
                        continue
                    circ = self.model.circularities[block.circularity]
                    for m in active:  # warm start from the previous period's solution
                        last = arrays[m][t - 1] if t else np.nan
                        arrays[m][t] = last if np.isfinite(last) else circ.seed
                        store(m)
                    residual = np.inf
                    for iterations in range(1, circ.max_iter + 1):  # noqa: B007
                        residual = 0.0
                        for m in active:
                            old = arrays[m][t]
                            new = evaluate_at(m, t)
                            if circ.damping < 1:
                                new = old + circ.damping * (new - old)
                            if np.isinf(new):
                                raise SolverError(
                                    f"'{m}' became infinite at {ax.name}={ax.index[t]}"
                                )
                            change = abs(new - old)
                            if change > residual:
                                residual = change
                            arrays[m][t] = new
                            store(m)
                        if residual <= circ.tol:
                            break
                    else:
                        raise SolverError(
                            f"did not converge at {ax.name}={ax.index[t]} in {circ.max_iter} "
                            f"iterations (largest change {residual:.3g}, tolerance {circ.tol:g})"
                        )
                    st = stats[block.circularity]
                    st.periods[str(ax.index[t])] = iterations  # type: ignore[index]
                    st.iterations = max(st.iterations, iterations)
                    st.residual = max(st.residual, float(residual))
        except Exception as e:  # noqa: BLE001
            if isinstance(e, SolverError):
                name = next(b.circularity for b in step.blocks if m in b.members)
                message, code = f"circularity '{name}': {e}", "not_converged"
                solver[name] = SolverStats(name, False, 0, np.nan, message=message)
            else:
                message = f"{type(e).__name__}: {e} (at {ax.name}={ax.index[t]}, in '{m}')"
                code = "calc_error"
            self._group_failed(members, message, code, values, status, diagnostics)
            return
        solver.update(stats)
        owner = {x: b.circularity for b in step.blocks for x in b.members if b.circularity}
        for m in members:
            if m in owner:
                it = stats[owner[m]].iterations
                status[m] = self._solved_status(m, owner[m], it, values, status, diagnostics)
            else:
                status[m] = self._value_status(self.metrics[m], values[m], status, diagnostics)

    def _solved_status(self, m, name, iterations, values, status, diagnostics) -> MetricStatus:
        result = self._value_status(self.metrics[m], values[m], status, diagnostics)
        if result.state == "ok":
            plural = "s" if iterations != 1 else ""
            result.message = f"solved by circularity '{name}' in {iterations} iteration{plural}"
        return result

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
        if isinstance(raw, pd.Series):
            failed[: cc.start] = False  # periods before forecast-only metrics exist
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


def _max_change(new: Any, old: Any) -> float:
    """Largest absolute change, ignoring positions that are missing in both."""
    diff = np.abs(np.asarray(new, dtype=float) - np.asarray(old, dtype=float))
    diff = diff[~np.isnan(diff)]
    return float(diff.max()) if diff.size else 0.0


def _copy(value: Any) -> Any:
    """Callers get their own Series, so editing a result cannot corrupt the cache."""
    return value.copy() if isinstance(value, pd.Series) else value
