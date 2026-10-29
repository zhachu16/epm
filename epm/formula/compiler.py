"""Compile formula IR into Python closures over a mapping of computed values."""

from __future__ import annotations

import dataclasses
import operator
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

from .analysis import scope_start
from .builtins import BUILTINS
from .env import FormulaEnv
from .ir import Binary, Call, Node, Num, Ref, Unary

Evaluator = Callable[[Mapping[str, Any]], Any]

_BINARY: dict[str, Callable[[Any, Any], Any]] = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "//": operator.floordiv,
    "%": operator.mod,
    "**": operator.pow,
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "and": np.logical_and,
    "or": np.logical_or,
}

_UNARY: dict[str, Callable[[Any], Any]] = {
    "neg": operator.neg,
    "pos": operator.pos,
    "not": np.logical_not,
}


def compile_formula(node: Node, env: FormulaEnv) -> Evaluator:
    """Return a function that evaluates ``node`` against a name → value mapping.

    Scalars are numpy float64 and series are pandas Series indexed by their axis, so the
    same closure handles both and broadcasts scalars automatically.
    """
    if isinstance(node, Num):
        value = np.float64(node.value)
        return lambda values: value
    if isinstance(node, Ref):
        name = node.name
        return lambda values: values[name]
    if isinstance(node, Unary):
        op = _UNARY[node.op]
        inner = compile_formula(node.operand, env)
        return lambda values: op(inner(values))
    if isinstance(node, Binary):
        bop = _BINARY[node.op]
        left = compile_formula(node.left, env)
        right = compile_formula(node.right, env)
        return lambda values: bop(left(values), right(values))
    if isinstance(node, Call):
        return _compile_call(node, env)
    raise TypeError(f"unexpected node {node!r}")


def _compile_call(call: Call, env: FormulaEnv) -> Evaluator:
    builtin = BUILTINS[call.func]
    if builtin.start_param is not None and env.starts:
        key = builtin.start_param
        arg = call.varargs[int(key)] if key.isdigit() else call.args.get(key)
        s = 0 if arg is None else scope_start(arg, lambda n: env.starts.get(n, 0))
        if s:
            # parsed calls are shared between metrics, so annotate a copy
            call = dataclasses.replace(call, literals={**call.literals, "_start": s})
    arg_fns = {k: compile_formula(v, env) for k, v in call.args.items()}
    var_fns = [compile_formula(v, env) for v in call.varargs]

    def run(values: Mapping[str, Any]) -> Any:
        args = {k: f(values) for k, f in arg_fns.items()}
        varargs = [f(values) for f in var_fns]
        return builtin.evaluate(call, args, varargs, env)

    return run
