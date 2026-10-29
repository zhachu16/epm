"""Intermediate representation of a parsed formula.

Formulas are written as Python expressions, then lowered from :mod:`ast` into these nodes
so that later passes (dependency extraction, shape inference, compilation) never see
arbitrary Python syntax.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class Node:
    col: int | None


@dataclass
class Num(Node):
    value: float
    col: int | None = None


@dataclass
class Str(Node):
    """A string literal, only valid as a built-in option such as ``is_forecast("fy")``."""

    value: str
    col: int | None = None


@dataclass
class Ref(Node):
    """A reference to a metric by name."""

    name: str
    col: int | None = None


@dataclass
class Unary(Node):
    op: str  # "neg" | "pos" | "not"
    operand: Node
    col: int | None = None


@dataclass
class Binary(Node):
    op: str  # arithmetic, comparison, "and", "or"
    left: Node
    right: Node
    col: int | None = None


@dataclass
class Call(Node):
    """A bound call to a built-in function.

    ``args`` maps parameter names to expression nodes, ``literals`` holds parameters that
    must be literal values (lags, axis names, periods), and ``varargs`` holds the positional
    expressions of variadic built-ins such as ``min`` and ``max``.
    """

    func: str
    args: dict[str, Node] = field(default_factory=dict)
    literals: dict[str, Any] = field(default_factory=dict)
    varargs: list[Node] = field(default_factory=list)
    col: int | None = None


def walk(node: Node):
    """Yield ``node`` and all of its descendants."""
    yield node
    if isinstance(node, Unary):
        yield from walk(node.operand)
    elif isinstance(node, Binary):
        yield from walk(node.left)
        yield from walk(node.right)
    elif isinstance(node, Call):
        for child in node.args.values():
            yield from walk(child)
        for child in node.varargs:
            yield from walk(child)
