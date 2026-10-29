"""Evaluate formulas one period at a time, for recurrences.

The engine steps a recurrence through its axis period by period. Evaluating each formula
over the whole series at every step would cost a full pandas evaluation per period (and per
solver iteration) to keep a single value. Instead:

* the parts of a formula that do not read the recurrence's own metrics are evaluated once
  per run, over the whole series, by the ordinary compiler;
* the parts that do are evaluated at one position with Python scalars;
* anything without a scalar form (custom functions) falls back to a whole-series evaluation
  at that position.

Results match whole-series evaluation. Arithmetic on two Python floats is IEEE like numpy's;
everything else (division by zero, ``//``, ``%``, ``**``, booleans, integers) goes through
numpy scalars, and element-wise built-ins run their own scalar code.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .builtins import BUILTINS, start
from .compiler import compile_formula, with_start
from .env import FormulaEnv
from .ir import Binary, Call, Node, Num, Ref, Unary, walk

Point = Callable[[int], Any]  # the value at position t


@dataclass
class Frame:
    """What a recurrence reads: values computed before it, and its own metrics so far."""

    values: Mapping[str, Any]
    current: dict[str, list[Any]]  # one entry per period; filled up to the current one
    index: pd.Index


Binder = Callable[[Frame], Point]

_NAN = math.nan


def _np(v: Any) -> Any:
    cls = v.__class__
    if cls is float:
        return np.float64(v)
    if cls is bool:
        return np.bool_(v)
    return np.asarray(v)[()]


def _py(v: Any) -> Any:
    return v.item() if isinstance(v, np.generic) else v


def _arith(py: Callable, numpy: Callable) -> Callable[[Any, Any], Any]:
    def op(a: Any, b: Any) -> Any:
        if a.__class__ is float and b.__class__ is float:
            try:
                return py(a, b)
            except ArithmeticError:  # division by zero or overflow: numpy gives inf or nan
                pass
        return numpy(_np(a), _np(b)).item()

    return op


def _numpy(fn: Callable) -> Callable[[Any, Any], Any]:
    return lambda a, b: fn(_np(a), _np(b)).item()


_BINARY: dict[str, Callable[[Any, Any], Any]] = {
    **{
        op: _arith(fn, fn)
        for op, fn in {
            "+": operator.add,
            "-": operator.sub,
            "*": operator.mul,
            "/": operator.truediv,
            "==": operator.eq,
            "!=": operator.ne,
            "<": operator.lt,
            "<=": operator.le,
            ">": operator.gt,
            ">=": operator.ge,
        }.items()
    },
    "//": _numpy(operator.floordiv),
    "%": _numpy(operator.mod),
    "**": _numpy(operator.pow),
    "and": lambda a, b: bool(a) and bool(b),  # nan is truthy, as in np.logical_and
    "or": lambda a, b: bool(a) or bool(b),
}


def _neg(a: Any) -> Any:
    return -a if a.__class__ is float else (-_np(a)).item()


def _pos(a: Any) -> Any:
    return a if a.__class__ is float else (+_np(a)).item()


_UNARY: dict[str, Callable[[Any], Any]] = {"neg": _neg, "pos": _pos, "not": operator.not_}

# Built-ins evaluated element-wise: their scalar code runs on numpy scalars at position t.
_ELEMENTWISE = {"where", "abs", "round", "clip", "pmt"}


def _reads(node: Node) -> set[str]:
    return {n.name for n in walk(node) if isinstance(n, Ref)}


def compile_point(node: Node, env: FormulaEnv, members: frozenset[str]) -> Binder:
    """Compile ``node`` for evaluation one position at a time.

    ``members`` are the metrics of the recurrence, read from :attr:`Frame.current`. The
    returned binder takes a :class:`Frame`, evaluates the parts of the formula that do not
    depend on ``members``, and returns a function of the position.
    """
    if not (_reads(node) & members):
        return _invariant(node, env)
    if isinstance(node, Ref):
        name = node.name
        return lambda frame: frame.current[name].__getitem__
    if isinstance(node, Unary):
        uop = _UNARY[node.op]
        inner = compile_point(node.operand, env, members)

        def bind_unary(frame: Frame) -> Point:
            f = inner(frame)
            return lambda t: uop(f(t))

        return bind_unary
    if isinstance(node, Binary):
        bop = _BINARY[node.op]
        left = compile_point(node.left, env, members)
        right = compile_point(node.right, env, members)

        def bind_binary(frame: Frame) -> Point:
            lf, rf = left(frame), right(frame)
            return lambda t: bop(lf(t), rf(t))

        return bind_binary
    if isinstance(node, Call):
        call = with_start(node, env)
        if call.func == "prev":
            return _prev(call, env, members)
        if call.func in ("cumsum", "cumprod"):
            return _cumulative(call, env, members)
        if call.func in _ELEMENTWISE or (call.func in ("min", "max") and len(call.varargs) > 1):
            return _elementwise(call, env, members)
    return _fallback(node, env)


def _invariant(node: Node, env: FormulaEnv) -> Binder:
    """Evaluate once over the whole series; positions are then just looked up."""
    if isinstance(node, Num):
        value = float(node.value)
        return lambda frame: lambda t: value
    series = compile_formula(node, env)

    def bind(frame: Frame) -> Point:
        v = series(frame.values)
        if isinstance(v, pd.Series):
            return _aligned(v, frame.index).tolist().__getitem__
        c = _py(v)
        return lambda t: c

    return bind


def _aligned(v: pd.Series, index: pd.Index) -> pd.Series:
    return v if v.index.equals(index) else v.reindex(index)


def _prev(call: Call, env: FormulaEnv, members: frozenset[str]) -> Binder:
    x = compile_point(call.args["x"], env, members)
    init = compile_point(call.args["init"], env, members) if "init" in call.args else None
    lag, s = call.literals["lag"], start(call)

    def bind(frame: Frame) -> Point:
        xf = x(frame)
        if lag == 0:
            return xf
        initf = init(frame) if init is not None else None

        def at(t: int) -> Any:
            if s <= t < s + lag:  # the first periods in scope take init
                return initf(t) if initf is not None else _NAN
            return xf(t - lag) if t >= lag else _NAN

        return at

    return bind


def _cumulative(call: Call, env: FormulaEnv, members: frozenset[str]) -> Binder:
    x = compile_point(call.args["x"], env, members)
    s, product = start(call), call.func == "cumprod"

    def bind(frame: Frame) -> Point:
        xf = x(frame)

        def at(t: int) -> Any:
            if t < s or xf(t) != xf(t):  # out of scope, or missing here
                return _NAN
            acc = 1.0 if product else 0.0
            for i in range(s, t + 1):  # missing values are skipped, as pandas does
                v = float(xf(i))
                if v == v:
                    acc = acc * v if product else acc + v
            return acc

        return at

    return bind


def _fold(pick: Callable[[float, float], float]) -> Callable[[list[Any]], Any]:
    """Pairwise min or max like numpy's: missing values propagate, ties take the right."""

    def fold(values: list[Any]) -> Any:
        acc = values[0]
        for v in values[1:]:
            if acc != acc or v != v:
                return _NAN
            acc = pick(acc, v)
        return acc

    return fold


# Plain-float versions of common element-wise built-ins; other inputs use numpy scalars.
_FAST_VARARGS = {
    "max": _fold(lambda a, b: a if a > b else b),
    "min": _fold(lambda a, b: a if a < b else b),
}


def _elementwise(call: Call, env: FormulaEnv, members: frozenset[str]) -> Binder:
    builtin = BUILTINS[call.func]
    args = {k: compile_point(v, env, members) for k, v in call.args.items()}
    varargs = [compile_point(v, env, members) for v in call.varargs]
    fast = _FAST_VARARGS.get(call.func)

    def bind(frame: Frame) -> Point:
        afs = {k: b(frame) for k, b in args.items()}
        vfs = [b(frame) for b in varargs]

        def at(t: int) -> Any:
            if fast is not None:
                v = [f(t) for f in vfs]
                if all(x.__class__ is float for x in v):
                    return fast(v)
                return _py(builtin.evaluate(call, {}, [_np(x) for x in v], env))
            a = {k: f(t) for k, f in afs.items()}
            if call.func == "where" and a["cond"].__class__ in (float, bool):
                c = a["cond"]
                return _NAN if c != c else (a["if_true"] if c else a["if_false"])
            if call.func == "abs" and a["x"].__class__ is float:
                return abs(a["x"])
            return _py(builtin.evaluate(call, {k: _np(x) for k, x in a.items()}, [], env))

        return at

    return bind


class _Live(Mapping[str, Any]):
    """Values as whole series, with the recurrence's metrics as filled so far."""

    def __init__(self, frame: Frame):
        self._frame = frame

    def __getitem__(self, name: str) -> Any:
        frame = self._frame
        if name in frame.current:
            data = np.array(frame.current[name], dtype=float)
            return pd.Series(data, index=frame.index, name=name)
        return frame.values[name]

    def __iter__(self) -> Iterator[str]:
        return iter({**self._frame.values, **self._frame.current})

    def __len__(self) -> int:
        return len({**self._frame.values, **self._frame.current})


def _fallback(node: Node, env: FormulaEnv) -> Binder:
    """Evaluate over the whole series at each position (custom functions, for instance)."""
    series = compile_formula(node, env)

    def bind(frame: Frame) -> Point:
        def at(t: int) -> Any:
            v = series(_Live(frame))
            return _py(_aligned(v, frame.index).iloc[t]) if isinstance(v, pd.Series) else _py(v)

        return at

    return bind
