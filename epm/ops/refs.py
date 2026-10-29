"""Who references what: used to block unsafe deletes and to carry renames through."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from .rewrite import (
    axis_literals,
    called_functions,
    referenced_names,
    rename_axis_literal,
    rename_function,
    rename_metric,
)

if TYPE_CHECKING:
    from .apply import State


def function_names(state: State) -> set[str]:
    """Top-level functions decorated with ``@function`` in functions.py."""
    if not state.functions:
        return set()
    try:
        tree = ast.parse(state.functions)
    except SyntaxError:
        return set()
    out = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if (
                    isinstance(target, ast.Name)
                    and target.id == "function"
                    or isinstance(target, ast.Attribute)
                    and target.attr == "function"
                ):
                    out.add(node.name)
    return out


def _formulas(state: State):
    """Yield ``(label, container, key)`` for every formula-like text field."""
    for name, m in state.model.get("metrics", {}).items():
        if m.get("formula"):
            yield f"metric '{name}'", m, "formula"
    for name, c in state.model.get("checks", {}).items():
        if c.get("expr"):
            yield f"check '{name}'", c, "expr"


def _analysis_metric_fields(analysis: dict[str, Any]):
    """Yield ``(container, key)`` for every metric name an analysis refers to."""
    for item in analysis.get("inputs", []):
        yield item, "metric"
    outputs = analysis.get("outputs", [])
    for i in range(len(outputs)):
        yield outputs, i
    plot = analysis.get("plot") or {}
    for key in ("x", "y", "z"):
        if key in plot:
            if isinstance(plot[key], list):
                for i in range(len(plot[key])):
                    yield plot[key], i
            else:
                yield plot, key


def _metric_of(selector: str) -> str:
    """Analysis outputs may select a period: ``equity_value@2029``."""
    return selector.split("@", 1)[0]


def references_to(state: State, kind: str, name: str) -> list[str]:
    users: list[str] = []
    model = state.model
    if kind == "metric":
        for label, obj, key in _formulas(state):
            if name in referenced_names(obj[key]):
                users.append(label)
        for t, table in model.get("tables", {}).items():
            if any(r.get("metric") == name for r in table.get("rows", [])):
                users.append(f"table '{t}'")
        for c, circ in model.get("circularities", {}).items():
            if name in circ.get("metrics", []):
                users.append(f"circularity '{c}'")
        for a, analysis in model.get("analyses", {}).items():
            fields = list(_analysis_metric_fields(analysis))
            overrides = analysis.get("overrides", {})
            if name in overrides or any(_metric_of(str(c[k])) == name for c, k in fields):
                users.append(f"analysis '{a}'")
        if name in model.get("settings", {}).get("key_outputs", []):
            users.append("settings.key_outputs")
    elif kind == "source":
        for m, metric in model.get("metrics", {}).items():
            if any((metric.get(f) or {}).get("source") == name for f in ("input", "history")):
                users.append(f"metric '{m}'")
    elif kind == "axis":
        for coll in ("metrics", "sources", "tables"):
            for n, obj in model.get(coll, {}).items():
                if obj.get("axis") == name:
                    users.append(f"{coll[:-1]} '{n}'")
        for label, obj, key in _formulas(state):
            if name in axis_literals(obj[key]) and label not in users:
                users.append(label)
        if model.get("settings", {}).get("default_axis") == name:
            users.append("settings.default_axis")
    elif kind == "file":
        for s, source in model.get("sources", {}).items():
            if source.get("file") == name:
                users.append(f"source '{s}'")
    elif kind == "function":
        for label, obj, key in _formulas(state):
            if name in called_functions(obj[key]):
                users.append(label)
    return users


def rename_references(state: State, kind: str, old: str, new: str) -> None:
    model = state.model
    if kind == "metric":
        for _, obj, key in _formulas(state):
            obj[key] = rename_metric(obj[key], old, new)
        for table in model.get("tables", {}).values():
            for row in table.get("rows", []):
                if row.get("metric") == old:
                    row["metric"] = new
        for circ in model.get("circularities", {}).values():
            circ["metrics"] = [new if m == old else m for m in circ.get("metrics", [])]
        for analysis in model.get("analyses", {}).values():
            for container, key in _analysis_metric_fields(analysis):
                value = str(container[key])
                if _metric_of(value) == old:
                    container[key] = new + value[len(old) :]
            overrides = analysis.get("overrides")
            if overrides and old in overrides:
                analysis["overrides"] = {(new if k == old else k): v for k, v in overrides.items()}
        settings = model.get("settings", {})
        if "key_outputs" in settings:
            settings["key_outputs"] = [new if m == old else m for m in settings["key_outputs"]]
    elif kind == "source":
        for metric in model.get("metrics", {}).values():
            for f in ("input", "history"):
                if (metric.get(f) or {}).get("source") == old:
                    metric[f]["source"] = new
    elif kind == "axis":
        for coll in ("metrics", "sources", "tables"):
            for obj in model.get(coll, {}).values():
                if obj.get("axis") == old:
                    obj["axis"] = new
        for _, obj, key in _formulas(state):
            obj[key] = rename_axis_literal(obj[key], old, new)
        settings = model.get("settings", {})
        if settings.get("default_axis") == old:
            settings["default_axis"] = new
    elif kind == "file":
        for source in model.get("sources", {}).values():
            if source.get("file") == old:
                source["file"] = new
    elif kind == "function":
        for _, obj, key in _formulas(state):
            obj[key] = rename_function(obj[key], old, new)
