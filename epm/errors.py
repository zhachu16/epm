"""Exceptions and structured diagnostics shared by every layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]


@dataclass
class Diagnostic:
    """A validation or calculation finding attached to a model object.

    ``object`` names the subject as ``"<kind>:<name>"``, e.g. ``"metric:ebit"``.
    ``detail`` carries machine-readable extras such as a cycle path or name suggestions.
    """

    severity: Severity
    code: str
    message: str
    object: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.object is not None:
            out["object"] = self.object
        if self.detail:
            out["detail"] = self.detail
        return out


class EpmError(Exception):
    """Misuse of the API, e.g. asking for an output that does not exist."""

    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class FormulaError(EpmError):
    """A formula that cannot be parsed, bound or shape-checked."""

    def __init__(self, code: str, message: str, col: int | None = None, **detail: Any):
        super().__init__(code, message, **detail)
        self.col = col
        if col is not None:
            self.detail["col"] = col
