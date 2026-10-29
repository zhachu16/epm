"""Turn an edited model.json into operations, so text edits get the same validation."""

from __future__ import annotations

from typing import Any

from .apply import COLLECTIONS, State
from .schema import Op


def ops_from_model(state: State, new: dict[str, Any]) -> list[Op]:
    """Operations that turn ``state.model`` into ``new``.

    Creates and updates come first and deletes last, so a delete only fails when the
    edited model itself still references the object. Renames show up as delete + create.
    """
    old = state.model
    ops: list[Op] = []
    old_settings, new_settings = old.get("settings", {}), new.get("settings", {})
    if old_settings != new_settings:
        changed = {k: v for k, v in new_settings.items() if old_settings.get(k) != v}
        changed.update({k: None for k in old_settings if k not in new_settings})
        ops.append(Op(op="update", kind="settings", set=changed))
    deletes: list[Op] = []
    for kind, (coll, _) in COLLECTIONS.items():
        a, b = old.get(coll, {}), new.get(coll, {})
        for name, definition in b.items():
            if name not in a:
                ops.append(Op(op="create", kind=kind, name=name, definition=definition))
            elif a[name] != definition:
                update = dict(definition)
                update.update({k: None for k in a[name] if k not in definition})
                ops.append(Op(op="update", kind=kind, name=name, set=update))
        deletes += [Op(op="delete", kind=kind, name=n) for n in a if n not in b]
    unknown = set(new) - {"settings", *(c for c, _ in COLLECTIONS.values())}
    if unknown:
        raise ValueError(f"unknown top-level keys in model.json: {', '.join(sorted(unknown))}")
    return ops + deletes
