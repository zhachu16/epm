"""Shapes describe whether a value is a scalar or a series on a named axis.

A shape is ``None`` for a scalar, an axis name for a series, or :data:`UNKNOWN` while
inference is still in progress. Shapes form a small lattice: ``UNKNOWN`` < scalar < axis,
and two different axes cannot be combined.
"""

from __future__ import annotations

from ..errors import FormulaError


class _Unknown:
    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN = _Unknown()
SCALAR = None

Shape = str | None | _Unknown


class AxisMismatch(FormulaError):
    def __init__(self, a: str, b: str, col: int | None = None):
        super().__init__(
            "axis_mismatch",
            f"cannot combine a series on axis '{a}' with a series on axis '{b}'",
            col=col,
            axes=[a, b],
        )


def join(a: Shape, b: Shape, col: int | None = None) -> Shape:
    if a is UNKNOWN:
        return b
    if b is UNKNOWN:
        return a
    if a is SCALAR:
        return b
    if b is SCALAR:
        return a
    if a != b:
        raise AxisMismatch(a, b, col)  # type: ignore[arg-type]
    return a


def join_all(shapes, col: int | None = None) -> Shape:
    out: Shape = UNKNOWN
    for s in shapes:
        out = join(out, s, col)
    return out


def describe(shape: Shape) -> str:
    if shape is UNKNOWN:
        return "unknown"
    if shape is SCALAR:
        return "scalar"
    return f"series on axis '{shape}'"
