"""Structured editing: operations, atomic batches, previews and history."""

from . import functions as _functions  # noqa: F401 - registers the function handler
from .apply import COLLECTIONS, OpError, State, apply_ops
from .preview import Change, Impact, diff_states, impact, renames_of
from .schema import Batch, Op

__all__ = [
    "COLLECTIONS",
    "Batch",
    "Change",
    "Impact",
    "Op",
    "OpError",
    "State",
    "apply_ops",
    "diff_states",
    "impact",
    "renames_of",
]
