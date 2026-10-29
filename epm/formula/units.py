"""Lightweight unit checking.

Units are tags such as ``"USD m"``, ``"%"`` or ``"x"``. Adding, subtracting or comparing
values with different units is flagged; multiplication and division are not checked and
give an unknown unit. Literals are unitless and combine with anything.

Units form a lattice for inference: ``ANY`` (unknown) < a concrete unit < ``CONFLICT``.
``CONFLICT`` marks a value already reported as mixed, so it never produces further issues.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .ir import Binary, Call, FnCall, Node, Num, Ref, Unary


class _Conflict:
    def __repr__(self) -> str:
        return "CONFLICT"


ANY = None
CONFLICT = _Conflict()

Unit = str | None | _Conflict

# Built-ins whose listed arguments must share a unit, which is also the result's unit.
# None means every expression argument. Built-ins not listed give an unknown unit.
_SAME_UNIT: dict[str, tuple[str, ...] | None] = {
    "where": ("if_true", "if_false"),
    "abs": None,
    "round": None,
    "clip": None,
    "min": None,
    "max": None,
    "prev": None,
    "next": None,
    "cumsum": None,
    "total": None,
    "sum": None,
    "mean": None,
    "first": None,
    "last": None,
    "at": None,
    "npv": ("cashflows",),
}

_VERBS = {"+": "adds", "-": "subtracts", "==": "compares", "!=": "compares"}


def unit_key(unit: str) -> str:
    return " ".join(unit.split()).lower()


@dataclass
class UnitIssue:
    message: str
    col: int | None = None


def join(a: Unit, b: Unit, issues: list[UnitIssue] | None = None, *, what: str = "", col=None):
    if a is ANY:
        return b
    if b is ANY:
        return a
    if a is CONFLICT or b is CONFLICT:
        return CONFLICT
    if unit_key(a) == unit_key(b):  # type: ignore[arg-type]
        return a
    if issues is not None:
        issues.append(UnitIssue(f"{what or 'combines'} '{a}' and '{b}'", col))
    return CONFLICT


def infer_unit(
    node: Node, unit_of: Callable[[str], Unit], issues: list[UnitIssue] | None = None
) -> Unit:
    """Infer the unit of ``node``; record mixed-unit operations in ``issues`` if given."""
    if isinstance(node, Num):
        return ANY
    if isinstance(node, Ref):
        return unit_of(node.name)
    if isinstance(node, Unary):
        inner = infer_unit(node.operand, unit_of, issues)
        return ANY if node.op == "not" else inner
    if isinstance(node, Binary):
        left = infer_unit(node.left, unit_of, issues)
        right = infer_unit(node.right, unit_of, issues)
        if node.op in ("+", "-"):
            return join(left, right, issues, what=f"'{node.op}' {_VERBS[node.op]}", col=node.col)
        if node.op in ("==", "!=", "<", "<=", ">", ">="):
            join(left, right, issues, what=f"'{node.op}' compares", col=node.col)
            return ANY
        return ANY
    if isinstance(node, Call):
        arg_units = {k: infer_unit(v, unit_of, issues) for k, v in node.args.items()}
        var_units = [infer_unit(v, unit_of, issues) for v in node.varargs]
        if node.func not in _SAME_UNIT:
            return ANY
        keys = _SAME_UNIT[node.func]
        chosen = (
            [*arg_units.values(), *var_units]
            if keys is None
            else [arg_units[k] for k in keys if k in arg_units]
        )
        out: Unit = ANY
        for u in chosen:
            out = join(out, u, issues, what=f"{node.func}() mixes", col=node.col)
        return out
    if isinstance(node, FnCall):
        for child in [*node.args, *node.kwargs.values()]:
            infer_unit(child, unit_of, issues)
        return ANY
    raise TypeError(f"unexpected node {node!r}")
