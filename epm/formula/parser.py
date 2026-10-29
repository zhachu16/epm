"""Parse formula text into the IR, accepting only a whitelisted subset of Python."""

from __future__ import annotations

import ast
import difflib
from functools import lru_cache

from ..errors import FormulaError
from .builtins import BUILTINS
from .ir import Binary, Call, FnCall, Node, Num, Ref, Str, Unary, walk

_BINOPS: dict[type, str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.Pow: "**",
    ast.BitAnd: "and",
    ast.BitOr: "or",
}
_CMPOPS: dict[type, str] = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}
_UNARYOPS: dict[type, str] = {ast.USub: "neg", ast.UAdd: "pos", ast.Not: "not", ast.Invert: "not"}

_FRIENDLY: dict[type, str] = {
    ast.Attribute: "attribute access (a.b)",
    ast.Subscript: "indexing (a[b])",
    ast.Lambda: "lambda",
    ast.List: "lists",
    ast.Tuple: "tuples",
    ast.Dict: "dicts",
    ast.Set: "sets",
    ast.ListComp: "comprehensions",
    ast.GeneratorExp: "comprehensions",
    ast.DictComp: "comprehensions",
    ast.SetComp: "comprehensions",
    ast.NamedExpr: "assignment expressions",
    ast.JoinedStr: "f-strings",
    ast.Await: "await",
    ast.Starred: "star arguments",
}


@lru_cache(maxsize=32_768)  # every edit re-parses the whole model
def parse_formula(text: str, functions: frozenset[str] = frozenset()) -> Node:
    """Parse ``text`` into IR, raising :class:`FormulaError` on anything not allowed.

    ``functions`` are the custom functions formulas may call. The result is cached and
    must be treated as immutable.
    """
    source = text.strip()
    if not source:
        raise FormulaError("syntax_error", "formula is empty")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as e:
        raise FormulaError("syntax_error", f"invalid formula: {e.msg}", col=e.offset) from None
    node = _lower(tree.body, functions)
    for n in walk(node):
        if isinstance(n, Str):
            raise FormulaError(
                "disallowed_syntax",
                "string literals are only allowed as function options, e.g. is_forecast('fy')",
                col=n.col,
            )
    return node


def _col(node: ast.AST) -> int | None:
    offset = getattr(node, "col_offset", None)
    return None if offset is None else offset + 1


def _lower(node: ast.AST, functions: frozenset[str]) -> Node:
    col = _col(node)

    def lower(child: ast.AST) -> Node:
        return _lower(child, functions)

    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool | int | float):
            return Num(float(v), col=col)
        if isinstance(v, str):
            return Str(v, col=col)
        raise FormulaError("disallowed_syntax", f"literal {v!r} is not allowed", col=col)
    if isinstance(node, ast.Name):
        return Ref(node.id, col=col)
    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise FormulaError("disallowed_syntax", "operator is not allowed", col=col)
        return Binary(op, lower(node.left), lower(node.right), col=col)
    if isinstance(node, ast.UnaryOp):
        return Unary(_UNARYOPS[type(node.op)], lower(node.operand), col=col)
    if isinstance(node, ast.BoolOp):
        op = "and" if isinstance(node.op, ast.And) else "or"
        values = [lower(v) for v in node.values]
        out = values[0]
        for v in values[1:]:
            out = Binary(op, out, v, col=col)
        return out
    if isinstance(node, ast.Compare):
        operands = [lower(node.left), *(lower(c) for c in node.comparators)]
        parts = []
        for i, op in enumerate(node.ops):
            sym = _CMPOPS.get(type(op))
            if sym is None:
                raise FormulaError(
                    "disallowed_syntax", "only ==, !=, <, <=, >, >= comparisons", col=col
                )
            parts.append(Binary(sym, operands[i], operands[i + 1], col=col))
        out = parts[0]
        for p in parts[1:]:
            out = Binary("and", out, p, col=col)
        return out
    if isinstance(node, ast.IfExp):
        return BUILTINS["where"].bind(
            [lower(node.test), lower(node.body), lower(node.orelse)], {}, col
        )
    if isinstance(node, ast.Call):
        return _lower_call(node, col, functions)
    what = _FRIENDLY.get(type(node), type(node).__name__)
    raise FormulaError("disallowed_syntax", f"{what} is not allowed in formulas", col=col)


def _lower_call(node: ast.Call, col: int | None, functions: frozenset[str]) -> Call | FnCall:
    if not isinstance(node.func, ast.Name):
        raise FormulaError(
            "disallowed_syntax", "only calls to named functions are allowed", col=col
        )
    name = node.func.id
    builtin = BUILTINS.get(name)
    if builtin is None and name in functions:
        if any(isinstance(a, ast.Starred) for a in node.args) or any(
            k.arg is None for k in node.keywords
        ):
            raise FormulaError("disallowed_syntax", "*args and **kwargs are not allowed", col=col)
        return FnCall(
            name,
            [_lower(a, functions) for a in node.args],
            {k.arg: _lower(k.value, functions) for k in node.keywords},  # type: ignore[misc]
            col=col,
        )
    if builtin is None:
        suggestions = difflib.get_close_matches(name, [*BUILTINS, *functions], n=3)
        hint = f"; did you mean {', '.join(suggestions)}?" if suggestions else ""
        raise FormulaError(
            "unknown_function",
            f"unknown function '{name}'{hint}",
            col=col,
            name=name,
            suggestions=suggestions,
        )
    if any(isinstance(a, ast.Starred) for a in node.args) or any(
        k.arg is None for k in node.keywords
    ):
        raise FormulaError("disallowed_syntax", "*args and **kwargs are not allowed", col=col)
    pos = [_lower(a, functions) for a in node.args]
    kw = {k.arg: _lower(k.value, functions) for k in node.keywords}
    return builtin.bind(pos, kw, col)  # type: ignore[arg-type]
