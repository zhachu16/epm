"""The ``@function`` decorator and static discovery of functions in ``functions.py``.

Discovery only parses the file; user code is never executed in the main process.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from typing import Any, Literal

Returns = Literal["series", "scalar"]
_OPTIONS = {"returns", "axis", "causal"}


def function(_fn=None, *, returns: Returns = "series", axis: str | None = None, causal=False):
    """Mark a function in ``functions.py`` as callable from formulas.

    ``returns`` is ``"series"`` (one value per period of ``axis``, by default the axis of
    the series arguments) or ``"scalar"``. ``causal=True`` promises the value at a period
    only depends on arguments at or before it, which allows use inside ``prev()`` loops.
    """

    def wrap(fn):
        fn.__epm_function__ = {"returns": returns, "axis": axis, "causal": causal}
        return fn

    return wrap(_fn) if callable(_fn) else wrap


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    params: tuple[str, ...]
    positional: int  # how many params may be passed positionally
    required: frozenset[str]
    returns: Returns
    axis: str | None
    causal: bool
    source: str  # the function's own source, decorators included
    lineno: int

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.source.encode("utf-8")).hexdigest()

    @property
    def signature(self) -> str:
        return f"{self.name}({', '.join(self.params)})"


@dataclass
class DiscoveryError:
    message: str
    name: str | None = None
    lineno: int | None = None


def _decorator(node: ast.FunctionDef) -> ast.expr | None:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if (isinstance(target, ast.Name) and target.id == "function") or (
            isinstance(target, ast.Attribute) and target.attr == "function"
        ):
            return dec
    return None


def segment(source: str, node: ast.FunctionDef) -> tuple[int, int]:
    """Line span (0-based, end exclusive) of a function including its decorators."""
    start = min([node.lineno, *(d.lineno for d in node.decorator_list)]) - 1
    return start, node.end_lineno  # type: ignore[return-value]


def function_nodes(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {
        n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and _decorator(n) is not None
    }


def discover(source: str | None) -> tuple[dict[str, FunctionSpec], list[DiscoveryError]]:
    if not source:
        return {}, []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {}, [DiscoveryError(f"functions.py has a syntax error: {e.msg}", lineno=e.lineno)]
    lines = source.splitlines()
    specs: dict[str, FunctionSpec] = {}
    errors: list[DiscoveryError] = []
    for name, node in function_nodes(tree).items():
        dec = _decorator(node)
        options: dict[str, Any] = {"returns": "series", "axis": None, "causal": False}
        if isinstance(dec, ast.Call):
            if dec.args:
                errors.append(
                    DiscoveryError("@function takes keyword options only", name, node.lineno)
                )
                continue
            try:
                for kw in dec.keywords:
                    if kw.arg not in _OPTIONS:
                        raise ValueError(f"unknown @function option '{kw.arg}'")
                    options[kw.arg] = ast.literal_eval(kw.value)
            except ValueError as e:
                errors.append(DiscoveryError(str(e), name, node.lineno))
                continue
        if options["returns"] not in ("series", "scalar"):
            errors.append(DiscoveryError("returns must be 'series' or 'scalar'", name, node.lineno))
            continue
        a = node.args
        if a.vararg or a.kwarg:
            errors.append(
                DiscoveryError(
                    "functions take named parameters only (no *args or **kwargs), so every "
                    "dependency is explicit",
                    name,
                    node.lineno,
                )
            )
            continue
        positional = [p.arg for p in [*a.posonlyargs, *a.args]]
        kwonly = [p.arg for p in a.kwonlyargs]
        required = set(positional[: len(positional) - len(a.defaults)])
        required |= {p for p, d in zip(kwonly, a.kw_defaults, strict=True) if d is None}
        start, end = segment(source, node)
        specs[name] = FunctionSpec(
            name=name,
            params=tuple(positional + kwonly),
            positional=len(positional),
            required=frozenset(required),
            returns=options["returns"],
            axis=options["axis"],
            causal=bool(options["causal"]),
            source="\n".join(lines[start:end]),
            lineno=node.lineno,
        )
    return specs, errors
