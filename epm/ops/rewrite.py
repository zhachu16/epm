"""Find and rename references inside formula text without disturbing anything else."""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable

from ..formula.builtins import BUILTINS, AxisHelper

_AXIS_HELPERS = {name for name, b in BUILTINS.items() if isinstance(b, AxisHelper)}

Selector = Callable[[ast.Expression], Iterable[ast.AST]]


def _parse(text: str) -> ast.Expression | None:
    try:
        return ast.parse(text.strip(), mode="eval")
    except SyntaxError:
        return None


def _names(tree: ast.Expression, called: bool) -> Iterable[ast.Name]:
    """Names that are (or are not) the function part of a call."""
    funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and (id(n) in funcs) == called:
            yield n


def _axis_constants(tree: ast.Expression) -> Iterable[ast.Constant]:
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _AXIS_HELPERS:
            for arg in [*n.args, *(k.value for k in n.keywords)]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    yield arg


def _replace(text: str, select: Selector, new: str) -> str:
    tree = _parse(text)
    if tree is None:
        return text  # leave formulas that do not parse alone; they are reported elsewhere
    data = text.strip().encode("utf-8")  # ast offsets are in UTF-8 bytes
    line_starts = [0]
    for line in data.split(b"\n")[:-1]:
        line_starts.append(line_starts[-1] + len(line) + 1)
    spans = sorted(
        (
            (
                line_starts[n.lineno - 1] + n.col_offset,
                line_starts[n.end_lineno - 1] + n.end_col_offset,  # type: ignore[operator]
            )
            for n in select(tree)
        ),
        reverse=True,
    )
    if not spans:
        return text
    for start, end in spans:
        data = data[:start] + new.encode("utf-8") + data[end:]
    return data.decode("utf-8")


def rename_metric(text: str, old: str, new: str) -> str:
    """Rename references to a metric (names that are read, not called)."""
    return _replace(text, lambda t: (n for n in _names(t, False) if n.id == old), new)


def rename_function(text: str, old: str, new: str) -> str:
    """Rename calls to a custom function."""
    return _replace(text, lambda t: (n for n in _names(t, True) if n.id == old), new)


def rename_axis_literal(text: str, old: str, new: str) -> str:
    """Rename axis names passed to period helpers, e.g. ``is_forecast('fy')``."""
    return _replace(text, lambda t: (c for c in _axis_constants(t) if c.value == old), repr(new))


def referenced_names(text: str) -> set[str]:
    """Metric names a formula reads, even if the formula has other errors."""
    tree = _parse(text)
    return set() if tree is None else {n.id for n in _names(tree, False)}


def called_functions(text: str) -> set[str]:
    tree = _parse(text)
    return set() if tree is None else {n.id for n in _names(tree, True)}


def axis_literals(text: str) -> set[str]:
    tree = _parse(text)
    return set() if tree is None else {c.value for c in _axis_constants(tree)}
