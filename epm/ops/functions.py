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


def _check_code(code: str, name: str) -> tuple[str, list[str]]:
    """Validate the code of one function; returns its code and any imports to hoist.

    The code holds exactly one ``@function`` named ``name``. Module-level imports are
    allowed and moved to the top of functions.py; ``from epm import function`` is implied.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise OpFailure(
            "invalid_definition", f"code has a syntax error: {e.msg} (line {e.lineno})"
        ) from None
    defs = function_nodes(tree)
    imports, body = [], []
    lines = code.splitlines()
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            text = "\n".join(lines[node.lineno - 1 : node.end_lineno])
            if text.strip() != HEADER.strip():
                imports.append(text)
        elif not (isinstance(node, ast.FunctionDef) and node.name in defs):
            body.append(node)
    if list(defs) != [name] or body:
        raise OpFailure(
            "invalid_definition",
            f"code must contain exactly one function, '{name}', decorated with @function",
        )
    start, end = segment(code, defs[name])
    return "\n".join(lines[start:end]) + "\n", imports


def _hoist(source: str, imports: list[str]) -> str:
    """Add imports after the existing top-of-file imports, skipping ones already present."""
    lines = source.splitlines()
    existing = {line.strip() for line in lines}
    new = [i for i in imports if i.strip() not in existing]
    if not new:
        return source
    tree = _tree(source)
    last = 0
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            last = node.end_lineno or last
        else:
            break
    return "\n".join([*lines[:last], *new, *lines[last:]]) + "\n"


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
        code, imports = _check_code((payload or {}).get("code", ""), name)
        if name in nodes:
            raise OpFailure("already_exists", f"function '{name}' already exists")
        _check_name(state, name)
        base = _hoist(source if source.strip() else HEADER, imports)
        state.functions = _tidy(base.rstrip("\n") + "\n\n\n" + code)
        return [Op(op="delete", kind="function", name=name)]

    if name not in nodes:
        raise OpFailure("not_found", f"there is no function '{name}'")
    start, end = segment(source, nodes[name])
    old_code = "\n".join(lines[start:end]) + "\n"

    if action == "update":
        code, imports = _check_code((op.set or {}).get("code", ""), name)
        updated = "\n".join([*lines[:start], code.rstrip("\n"), *lines[end:]]) + "\n"
        state.functions = _tidy(_hoist(updated, imports))
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


def apply_functions_file(state: State, op: Op) -> list[Op]:
    """Replace the whole of functions.py (text editing)."""
    code = (op.set or {}).get("code")
    if code is not None and not isinstance(code, str):
        raise OpFailure("invalid_definition", "'code' must be text")
    if code:
        _tree(code)
    old = state.functions
    state.functions = code or None
    return [Op(op="update", kind="functions", set={"code": old})]


HANDLERS["function"] = apply_function
HANDLERS["functions"] = apply_functions_file
