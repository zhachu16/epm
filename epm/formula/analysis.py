"""Static passes over formula IR: dependency extraction and shape inference."""

from __future__ import annotations

from collections.abc import Callable

from .builtins import BUILTINS, FULL
from .env import FormulaEnv
from .ir import Binary, Call, Node, Num, Ref, Unary
from .shapes import SCALAR, Shape, join

Lag = int | None  # None (FULL) means the whole series is needed


def _combine(a: Lag, b: Lag) -> Lag:
    if a is FULL or b is FULL:
        return FULL
    return min(a, b)


def references(node: Node) -> dict[str, Lag]:
    """Map each referenced metric to the smallest lag at which it is read.

    ``prev(x)`` reads ``x`` at lag 1, ``prev(prev(x))`` at lag 2, and reductions such as
    ``total(x)`` need the whole series (:data:`FULL`). If a name is read in several ways,
    ``FULL`` wins, otherwise the smallest lag does.
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

    visit(node, 0)
    return refs


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
    raise TypeError(f"unexpected node {node!r}")
