"""Reading and writing ``model.json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schema import Model


def load_model(source: str | Path | dict[str, Any]) -> Model:
    """Load a model from a dict, a JSON string, or a path to a ``model.json`` file."""
    if isinstance(source, dict):
        return Model.model_validate(source)
    if isinstance(source, Path) or not str(source).lstrip().startswith("{"):
        return Model.model_validate_json(Path(source).read_text(encoding="utf-8"))
    return Model.model_validate_json(source)


def dump_model(model: Model) -> str:
    """Serialize with stable formatting so diffs between versions stay readable."""
    data = model.model_dump(mode="json", exclude_defaults=True)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
