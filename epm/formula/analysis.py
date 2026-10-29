"""Static passes over formula IR: dependency extraction and shape inference."""

from __future__ import annotations

from collections.abc import Callable

from ..errors import FormulaError
from .builtins import BUILTINS, FULL
from .env import FormulaEnv
from .ir import Binary, Call, FnCall, Node, Num, Ref, Unary
from .shapes import SCALAR, Shape, describe, join, join_all

Lag = int | None  # None (FULL) means the whole series is needed


def _combine(a: Lag, b: Lag) -> Lag:
    if a is FULL or b is FULL:
        return FULL
    return min(a, b)


def references(node: Node, causal: frozenset[str] = frozenset()) -> dict[str, Lag]:
    """Map each referenced metric to the smallest lag at which it is read.

    ``prev(x)`` reads ``x`` at lag 1, ``prev(prev(x))`` at lag 2, and reductions such as
    ``total(x)`` need the whole series (:data:`FULL`). If a name is read in several ways,
    ``FULL`` wins, otherwise the smallest lag does. Custom functions read their arguments
    in full unless listed in ``causal``.
    """
    refs: dict[str, Lag] = {}

    def visit(n: Node, offset: Lag) -> None:
        if isinstance(n, Ref):
            refs[n.name] = offset if n.name not in refs else _combine(refs[n.name], offset)
        elif isinstance(n, Unary):
            visit(n.operand, offset)
        elif isinstance(n, Binary):
            visit(n.left, offset)
            visit(n.right, offset)
        elif isinstance(n, Call):
            builtin = BUILTINS[n.func]
            for key, child in n.args.items():
                visit(child, _shift(offset, builtin.offset(n, key)))
            for i, child in enumerate(n.varargs):
                visit(child, _shift(offset, builtin.offset(n, i)))
        elif isinstance(n, FnCall):
            delta = 0 if n.func in causal else FULL
            for child in [*n.args, *n.kwargs.values()]:
                visit(child, _shift(offset, delta))

    visit(node, 0)
    return refs


def custom_calls(node: Node) -> set[str]:
    from .ir import walk

    return {n.func for n in walk(node) if isinstance(n, FnCall)}


def _shift(offset: Lag, delta: Lag) -> Lag:
    if offset is FULL or delta is FULL:
        return FULL
    return offset + delta


def infer_shape(node: Node, shape_of: Callable[[str], Shape], env: FormulaEnv) -> Shape:
    """Infer the shape of ``node`` given the shapes of the metrics it references."""
    if isinstance(node, Num):
        return SCALAR
    if isinstance(node, Ref):
        return shape_of(node.name)
    if isinstance(node, Unary):
        return infer_shape(node.operand, shape_of, env)
    if isinstance(node, Binary):
        return join(
            infer_shape(node.left, shape_of, env), infer_shape(node.right, shape_of, env), node.col
        )
    if isinstance(node, Call):
        args = {k: infer_shape(v, shape_of, env) for k, v in node.args.items()}
        varargs = [infer_shape(v, shape_of, env) for v in node.varargs]
        return BUILTINS[node.func].shape(node, args, varargs, env)
    if isinstance(node, FnCall):
        arg_shapes = [infer_shape(v, shape_of, env) for v in [*node.args, *node.kwargs.values()]]
        return custom_shape(node, arg_shapes, env)
    raise TypeError(f"unexpected node {node!r}")


def bind_custom(node: FnCall, spec) -> None:
    """Check a custom call against the function's signature."""
    if len(node.args) > spec.positional:
        raise FormulaError(
            "invalid_argument",
            f"{spec.signature} takes at most {spec.positional} positional argument(s)",
            col=node.col,
        )
    given = set(spec.params[: len(node.args)])
    for k in node.kwargs:
        if k not in spec.params:
            raise FormulaError(
                "invalid_argument", f"{spec.signature} has no parameter '{k}'", col=node.col
            )
        if k in given:
            raise FormulaError(
                "invalid_argument", f"{spec.name}() got '{k}' more than once", col=node.col
            )
        given.add(k)
    missing = sorted(spec.required - given)
    if missing:
        raise FormulaError(
            "invalid_argument",
            f"{spec.signature} is missing {', '.join(missing)}",
            col=node.col,
        )


def custom_axis(spec, arg_shapes: list[Shape], env: FormulaEnv, col=None) -> str:
    if spec.axis is not None:
        if spec.axis not in env.axes:
            raise FormulaError("unknown_axis", f"{spec.name}() uses unknown axis '{spec.axis}'")
        return spec.axis
    joined = join_all(arg_shapes, col)
    if isinstance(joined, str):
        return joined
    return env.resolve_axis(None, spec.name, col).name


def custom_shape(node: FnCall, arg_shapes: list[Shape], env: FormulaEnv) -> Shape:
    spec = env.functions.get(node.func)
    if spec is None:
        raise FormulaError("unknown_function", f"unknown function '{node.func}'", col=node.col)
    bind_custom(node, spec)
    if spec.returns == "scalar":
        return SCALAR
    axis = custom_axis(spec, arg_shapes, env, node.col)
    for s in arg_shapes:
        if isinstance(s, str) and s != axis:
            raise FormulaError(
                "axis_mismatch",
                f"{spec.name}() returns a series on '{axis}' but got a {describe(s)}",
                col=node.col,
            )
    return axis


def scope_start(node: Node, start_of: Callable[[str], int]) -> int:
    """First axis position at which ``node`` can have a value.

    A forecast-only metric starts at the first forecast period; anything combining it
    point by point starts there too. Reductions produce scalars, which have no start.
    """
    if isinstance(node, Num):
        return 0
    if isinstance(node, Ref):
        return start_of(node.name)
    if isinstance(node, Unary):
        return scope_start(node.operand, start_of)
    if isinstance(node, Binary):
        return max(scope_start(node.left, start_of), scope_start(node.right, start_of))
    if isinstance(node, Call):
        if BUILTINS[node.func].reduces(node):
            return 0
        children = [*node.args.values(), *node.varargs]
        return max((scope_start(c, start_of) for c in children), default=0)
    if isinstance(node, FnCall):
        children = [*node.args, *node.kwargs.values()]
        return max((scope_start(c, start_of) for c in children), default=0)
    raise TypeError(f"unexpected node {node!r}")
