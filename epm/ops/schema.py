"""Structured edit operations, shared by the UI, the CLI and agents."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal[
    "metric",
    "axis",
    "source",
    "table",
    "check",
    "circularity",
    "analysis",
    "function",
    "file",
    "settings",
]

OpName = Literal["create", "update", "upsert", "delete", "rename"]


class Op(BaseModel):
    """One edit.

    * ``create``: add ``name`` with definition ``def``.
    * ``update``: merge the fields in ``set`` into the definition; ``null`` clears a field.
    * ``upsert``: ``update`` if ``name`` exists, otherwise ``create`` from ``set``.
    * ``delete``: remove ``name``; fails while anything still references it.
    * ``rename``: rename ``name`` to ``to`` and update every reference.

    ``file`` operations carry base64 content in ``data``; ``function`` definitions are
    ``{"code": "<python source of one @function>"}``; ``settings`` takes ``update`` only.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    op: OpName
    kind: Kind
    name: str | None = None
    definition: dict[str, Any] | None = Field(default=None, alias="def")
    set: dict[str, Any] | None = None
    to: str | None = None
    data: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> Op:
        if self.kind == "settings":
            if self.op != "update":
                raise ValueError("settings only support 'update'")
        elif not self.name:
            raise ValueError(f"'{self.op}' needs a 'name'")
        if self.op == "create" and self.definition is None and self.kind != "file":
            raise ValueError("'create' needs 'def'")
        if self.op in ("update", "upsert") and self.set is None and self.kind != "file":
            raise ValueError(f"'{self.op}' needs 'set'")
        if self.op == "rename" and not self.to:
            raise ValueError("'rename' needs 'to'")
        if self.kind == "file" and self.op in ("create", "update", "upsert") and not self.data:
            raise ValueError("file operations need base64 'data'")
        return self

    def describe(self) -> str:
        target = self.kind if self.kind == "settings" else f"{self.kind} '{self.name}'"
        return f"{self.op} {target}" + (f" → '{self.to}'" if self.to else "")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ops: list[Op] = Field(min_length=1)
    label: str | None = None
