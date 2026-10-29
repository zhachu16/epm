"""Edit operations on custom functions, applied to the text of functions.py."""

from __future__ import annotations

import ast
import re

from ..formula.builtins import RESERVED_NAMES
from ..functions.spec import function_nodes, segment
from ..model.schema import NAME_RE
from . import refs
from .apply import HANDLERS, OpFailure, State
from .schema import Op

HEADER = "from epm import function\n"


def _tree(source: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as e:
        raise OpFailure("invalid_definition", f"functions.py has a syntax error: {e.msg}") from None


def _check_code(code: str, name: str) -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise OpFailure(
            "invalid_definition", f"code has a syntax error: {e.msg} (line {e.lineno})"
        ) from None
    defs = function_nodes(tree)
    others = [n for n in tree.body if not (isinstance(n, ast.FunctionDef) and n.name in defs)]
    if list(defs) != [name] or others:
        raise OpFailure(
            "invalid_definition",
            f"code must contain exactly one function, '{name}', decorated with @function",
        )
    return code.strip("\n") + "\n"


def _check_name(state: State, name: str) -> None:
    if not NAME_RE.match(name) or name in RESERVED_NAMES:
        raise OpFailure("invalid_name", f"'{name}' cannot be used as a function name")
    if name in state.model.get("metrics", {}):
        raise OpFailure("already_exists", f"'{name}' is already a metric")


def _tidy(text: str) -> str:
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip("\n")
    return text + "\n" if text else ""


def apply_function(state: State, op: Op) -> list[Op]:
    source = state.functions or ""
    nodes = function_nodes(_tree(source)) if source else {}
    name = op.name
    assert name is not None
    action = op.op
    if action == "upsert":
        action = "update" if name in nodes else "create"
    lines = source.splitlines()

    if action == "create":
        payload = op.definition if op.op == "create" else op.set
        code = _check_code((payload or {}).get("code", ""), name)
        if name in nodes:
            raise OpFailure("already_exists", f"function '{name}' already exists")
        _check_name(state, name)
        base = source if source.strip() else HEADER
        state.functions = _tidy(base.rstrip("\n") + "\n\n\n" + code)
        return [Op(op="delete", kind="function", name=name)]

    if name not in nodes:
        raise OpFailure("not_found", f"there is no function '{name}'")
    start, end = segment(source, nodes[name])
    old_code = "\n".join(lines[start:end]) + "\n"

    if action == "update":
        code = _check_code((op.set or {}).get("code", ""), name)
        state.functions = _tidy("\n".join([*lines[:start], code.rstrip("\n"), *lines[end:]]))
        return [Op(op="update", kind="function", name=name, set={"code": old_code})]

    if action == "delete":
        users = refs.references_to(state, "function", name)
        if users:
            raise OpFailure("in_use", f"function '{name}' is still used by {', '.join(users)}")
        state.functions = _tidy("\n".join([*lines[:start], *lines[end:]]))
        return [Op(op="create", kind="function", name=name, definition={"code": old_code})]

    if action == "rename":
        new = op.to
        assert new is not None
        if new in nodes:
            raise OpFailure("already_exists", f"function '{new}' already exists")
        _check_name(state, new)
        i = nodes[name].lineno - 1
        lines[i] = re.sub(rf"\bdef\s+{re.escape(name)}\b", f"def {new}", lines[i], count=1)
        state.functions = _tidy("\n".join(lines))
        refs.rename_references(state, "function", name, new)
        return [Op(op="rename", kind="function", name=new, to=name)]

    raise OpFailure("invalid_op", f"unknown operation '{op.op}'")


HANDLERS["function"] = apply_function
