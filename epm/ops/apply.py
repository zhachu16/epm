"""Apply operations to a model state, producing the new state and an inverse batch."""

from __future__ import annotations

import base64
import binascii
import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ..model.schema import (
    Analysis,
    Axis,
    Check,
    Circularity,
    Metric,
    Model,
    Settings,
    Source,
    Table,
    check_name,
)
from ..sources.files import check_relative_path
from . import refs
from .schema import Op

COLLECTIONS: dict[str, tuple[str, type[BaseModel]]] = {
    "metric": ("metrics", Metric),
    "axis": ("axes", Axis),
    "source": ("sources", Source),
    "table": ("tables", Table),
    "check": ("checks", Check),
    "circularity": ("circularities", Circularity),
    "analysis": ("analyses", Analysis),
}

# kind-specific hooks registered by other modules (custom functions, analyses)
HANDLERS: dict[str, Callable[[State, Op], list[Op]]] = {}


@dataclass
class State:
    """Everything an operation can change: model.json (as JSON data), functions and inputs."""

    model: dict[str, Any]
    functions: str | None = None
    inputs: dict[str, bytes] = field(default_factory=dict)

    @classmethod
    def of(cls, model: Model, functions: str | None, inputs: dict[str, bytes]) -> State:
        return cls(model.model_dump(mode="json", exclude_defaults=True), functions, dict(inputs))

    def copy(self) -> State:
        return State(copy.deepcopy(self.model), self.functions, dict(self.inputs))

    def to_model(self) -> Model:
        return Model.model_validate(self.model)

    def collection(self, kind: str) -> dict[str, Any]:
        return self.model.setdefault(COLLECTIONS[kind][0], {})


class OpFailure(Exception):
    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


@dataclass
class OpError:
    index: int  # position in the batch, or -1 for whole-batch validation
    op: str
    code: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"index": self.index, "op": self.op, "code": self.code, "message": self.message}
        if self.detail:
            out["detail"] = self.detail
        return out


def validation_message(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"])
        msg = err["msg"].removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def apply_ops(state: State, ops: list[Op]) -> tuple[State, list[Op], list[OpError]]:
    """Apply ``ops`` to a copy of ``state``.

    Returns the new state, the inverse operations (apply them in order to undo) and any
    errors. Every operation is attempted so all problems are reported at once; the batch
    should only be committed if there are no errors.
    """
    new = state.copy()
    inverses: list[list[Op]] = []
    errors: list[OpError] = []
    for i, op in enumerate(ops):
        try:
            inverses.append(_apply(new, op))
        except OpFailure as e:
            errors.append(OpError(i, op.describe(), e.code, e.message, e.detail))
    if not errors:
        try:
            new.to_model()
        except ValidationError as e:
            errors.append(OpError(-1, "batch", "invalid_model", validation_message(e)))
    inverse = [inv for group in reversed(inverses) for inv in group]
    return new, inverse, errors


def _apply(state: State, op: Op) -> list[Op]:
    if op.kind in HANDLERS:
        return HANDLERS[op.kind](state, op)
    if op.kind == "settings":
        return _update_settings(state, op)
    if op.kind == "file":
        return _apply_file(state, op)
    if op.kind not in COLLECTIONS:
        raise OpFailure("unsupported", f"'{op.kind}' objects are not supported yet")
    items = state.collection(op.kind)
    name = op.name
    assert name is not None
    action = op.op
    if action == "upsert":
        action = "update" if name in items else "create"

    if action == "create":
        _check_new_name(state, op.kind, name)
        items[name] = _validate(op.kind, op.definition if op.op == "create" else op.set or {})
        return [Op(op="delete", kind=op.kind, name=name)]

    if name not in items:
        raise OpFailure("not_found", f"there is no {op.kind} '{name}'")

    if action == "update":
        old = items[name]
        merged = {**old, **(op.set or {})}
        merged = {k: v for k, v in merged.items() if v is not None}
        items[name] = _validate(op.kind, merged)
        undo = {k: old.get(k) for k in (op.set or {})}
        return [Op(op="update", kind=op.kind, name=name, set=undo)]

    if action == "delete":
        users = refs.references_to(state, op.kind, name)
        if users:
            raise OpFailure(
                "in_use",
                f"{op.kind} '{name}' is still used by {', '.join(users)}",
                used_by=users,
            )
        old = items.pop(name)
        return [Op(op="create", kind=op.kind, name=name, definition=old)]

    if action == "rename":
        new_name = op.to
        assert new_name is not None
        if new_name == name:
            return []
        _check_new_name(state, op.kind, new_name)
        state.model[COLLECTIONS[op.kind][0]] = {
            (new_name if k == name else k): v for k, v in items.items()
        }
        refs.rename_references(state, op.kind, name, new_name)
        return [Op(op="rename", kind=op.kind, name=new_name, to=name)]

    raise OpFailure("invalid_op", f"unknown operation '{op.op}'")


def _validate(kind: str, definition: dict[str, Any]) -> dict[str, Any]:
    cls = COLLECTIONS[kind][1]
    try:
        obj = cls.model_validate(definition)
    except ValidationError as e:
        raise OpFailure("invalid_definition", validation_message(e)) from None
    return obj.model_dump(mode="json", exclude_defaults=True)


def _check_new_name(state: State, kind: str, name: str) -> None:
    try:
        check_name(name, kind)
    except ValueError as e:
        raise OpFailure("invalid_name", str(e)) from None
    if name in state.collection(kind):
        raise OpFailure("already_exists", f"{kind} '{name}' already exists")
    if kind == "metric" and name in refs.function_names(state):
        raise OpFailure("already_exists", f"'{name}' is already a custom function")


def _update_settings(state: State, op: Op) -> list[Op]:
    old = state.model.get("settings", {})
    merged = {k: v for k, v in {**old, **(op.set or {})}.items() if v is not None}
    try:
        state.model["settings"] = Settings.model_validate(merged).model_dump(
            mode="json", exclude_defaults=True
        )
    except ValidationError as e:
        raise OpFailure("invalid_definition", validation_message(e)) from None
    return [Op(op="update", kind="settings", set={k: old.get(k) for k in (op.set or {})})]


def _apply_file(state: State, op: Op) -> list[Op]:
    name = op.name
    assert name is not None
    try:
        check_relative_path(name)
    except ValueError as e:
        raise OpFailure("invalid_name", str(e)) from None
    if not name.startswith("inputs/"):
        raise OpFailure("invalid_name", "input files live under 'inputs/'")
    old = state.inputs.get(name)
    encoded_old = None if old is None else base64.b64encode(old).decode()
    if op.op in ("create", "update", "upsert"):
        if op.op == "create" and old is not None:
            raise OpFailure("already_exists", f"file '{name}' already exists")
        if op.op == "update" and old is None:
            raise OpFailure("not_found", f"there is no file '{name}'")
        try:
            state.inputs[name] = base64.b64decode(op.data or "", validate=True)
        except binascii.Error:
            raise OpFailure("invalid_definition", "'data' is not valid base64") from None
        if old is None:
            return [Op(op="delete", kind="file", name=name)]
        return [Op(op="update", kind="file", name=name, data=encoded_old)]
    if old is None:
        raise OpFailure("not_found", f"there is no file '{name}'")
    if op.op == "delete":
        users = refs.references_to(state, "file", name)
        if users:
            raise OpFailure("in_use", f"file '{name}' is still used by {', '.join(users)}")
        del state.inputs[name]
        return [Op(op="create", kind="file", name=name, data=encoded_old)]
    new_name = op.to
    assert new_name is not None
    if new_name in state.inputs:
        raise OpFailure("already_exists", f"file '{new_name}' already exists")
    if not new_name.startswith("inputs/"):
        raise OpFailure("invalid_name", "input files live under 'inputs/'")
    state.inputs[new_name] = state.inputs.pop(name)
    refs.rename_references(state, "file", name, new_name)
    return [Op(op="rename", kind="file", name=new_name, to=name)]
