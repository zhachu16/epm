"""The ``fm`` command line: inspect, edit, run and analyse models.

Every command accepts ``--json`` for a structured envelope ``{ok, data, errors, warnings}``
that agents can parse. Edits go through the same atomic batches as the UI and API.
"""

from __future__ import annotations

import base64
import functools
import json
import sys
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
import typer

from ..errors import EpmError
from ..formula.builtins import BUILTINS
from ..model import Model, Settings
from ..package import EXTENSION, pack, unpack
from ..workspace import BatchResult, Workspace
from .output import Output, number, table, values_table

app = typer.Typer(
    name="fm",
    help="Financial models built from named metrics and formulas.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


class State:
    """Per-invocation context.

    Standalone, each command opens the model, works on it and saves. Inside ``fm serve``
    the server passes its live workspace (``in_session``), so commands share the
    session's undo history and pending batches, and the server does the saving.
    """

    def __init__(
        self,
        model: Path | None,
        as_json: bool,
        workspace: Workspace | None = None,
        out=None,
        err=None,
    ):
        self.model = model
        self.out = Output(as_json, out, err)
        self._ws = workspace
        self.in_session = workspace is not None

    def workspace(self) -> Workspace:
        if self._ws is None:
            self._ws = Workspace.open(find_model(self.model))
        return self._ws

    def save(self, ws: Workspace) -> None:
        if not self.in_session:
            ws.save()


def find_model(path: Path | None) -> Path:
    if path is not None:
        return path
    cwd = Path.cwd()
    if (cwd / "model.json").is_file():
        return cwd
    found = sorted(cwd.glob(f"*{EXTENSION}"))
    if len(found) == 1:
        return found[0]
    if not found:
        raise EpmError(
            "no_model", f"no model here: pass -m PATH, or run next to one {EXTENSION} file"
        )
    raise EpmError(
        "ambiguous_model", f"several models here ({', '.join(f.name for f in found)}); pass -m"
    )


@app.callback()
def main(
    ctx: typer.Context,
    model: Annotated[
        Path | None,
        typer.Option("--model", "-m", envvar="EPM_MODEL", help=f"A {EXTENSION} file or directory."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print a JSON envelope.")] = False,
) -> None:
    if isinstance(ctx.obj, State):  # provided by fm serve
        ctx.obj.out.as_json = as_json
        return
    ctx.obj = State(model, as_json)


def command(fn):
    """Report EpmError and bad input as structured errors instead of tracebacks."""

    @functools.wraps(fn)
    def wrapper(ctx: typer.Context, *args, **kwargs):
        state: State = ctx.obj
        try:
            return fn(ctx, *args, **kwargs)
        except EpmError as e:
            state.out.emit(ok=False, errors=[{"code": e.code, "message": e.message, **e.detail}])
        except (ValueError, OSError) as e:
            state.out.emit(ok=False, errors=[{"code": "invalid_input", "message": str(e)}])

    return wrapper


def _sub(name: str, help: str) -> typer.Typer:
    sub = typer.Typer(help=help, no_args_is_help=True)
    app.add_typer(sub, name=name)
    return sub


metric_app = _sub("metric", "Create, edit, rename and delete metrics.")
axis_app = _sub("axis", "Define axes such as fiscal years.")
source_app = _sub("source", "Map CSV/XLSX files onto series.")
file_app = _sub("file", "Manage files under inputs/.")
function_app = _sub("function", "Custom Python functions in functions.py.")
table_app = _sub("table", "Ordered views of metrics.")
check_app = _sub("check", "Validation rules such as balance checks.")
circularity_app = _sub("circularity", "Declared loops solved by iteration.")
analysis_app = _sub("analysis", "Saved sensitivities and plots.")
settings_app = _sub("settings", "Model settings.")
pending_app = _sub("pending", "Proposed batches awaiting review (needs fm serve).")


# -- parsing helpers ----------------------------------------------------------------------


def parse_scalar(text: str) -> Any:
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise EpmError("invalid_input", f"'{text}' is not a number or JSON value") from None


def parse_assignments(items: list[str] | None) -> dict[str, Any]:
    out = {}
    for item in items or []:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise EpmError("invalid_input", f"expected NAME=VALUE, got '{item}'")
        out[name.strip()] = parse_scalar(value.strip())
    return out


def parse_values(text: str) -> list[float]:
    """``0.04,0.06,0.08`` or ``start:stop:step`` (inclusive)."""
    if ":" in text and "," not in text:
        parts = [float(p) for p in text.split(":")]
        if len(parts) != 3:
            raise EpmError("invalid_input", f"expected start:stop:step, got '{text}'")
        from ..model.schema import SweepRange

        return SweepRange(start=parts[0], stop=parts[1], step=parts[2]).values()
    return [float(v) for v in text.split(",") if v.strip()]


def parse_inputs(items: list[str]) -> dict[str, list[float]]:
    out = {}
    for item in items:
        name, sep, values = item.partition("=")
        if not sep:
            raise EpmError("invalid_input", f"expected NAME=VALUES, got '{item}'")
        out[name.strip()] = parse_values(values)
    return out


def parse_axis_values(text: str) -> list[Any]:
    """``2024-2030`` for a range of years, otherwise comma-separated labels."""
    if "," not in text and "-" in text.strip("-"):
        a, b = text.split("-", 1)
        if a.strip().isdigit() and b.strip().isdigit():
            return list(range(int(a), int(b) + 1))
    items = [v.strip() for v in text.split(",") if v.strip()]
    return [int(v) if v.lstrip("-").isdigit() else v for v in items]


def parse_binding(text: str, cell: bool = False) -> dict[str, Any]:
    source, sep, rest = text.partition(":")
    if not sep or not rest:
        raise EpmError(
            "invalid_input", f"expected SOURCE:{'CELL' if cell else 'ITEM'}, got '{text}'"
        )
    return {"source": source, ("cell" if cell else "item"): rest}


def per_period(text: str) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for part in text.split(","):
        k, sep, v = part.partition("=")
        if not sep:
            raise EpmError("invalid_input", f"expected PERIOD=VALUE pairs, got '{part}'")
        out[k.strip()] = None if v.strip() in ("", "null") else float(v)
    return out


def _csv(text: str | None) -> list[str] | None:
    return None if text is None else [x.strip() for x in text.split(",") if x.strip()]


# -- shared output ------------------------------------------------------------------------


def batch_text(result: BatchResult) -> str:
    lines = []
    verb = (
        "committed" if result.committed else ("proposed" if result.mode == "propose" else "preview")
    )
    if result.ok:
        lines.append(f"{verb}: {result.label}" + (f" [{result.id}]" if result.id else ""))
    for c in result.changes:
        extra = f" (from {c.renamed_from})" if c.renamed_from else ""
        lines.append(f"  {c.change:8} {c.kind} {c.name}{extra}")
    if result.impact and result.impact.outputs:
        lines.append("effect on outputs:")
        for name, entry in result.impact.outputs.items():
            before, after = entry.get("before"), entry.get("after")
            if before is None:
                lines.append(f"  {name}: new")
            elif after and before.get("axis") is None:
                lines.append(
                    f"  {name}: {number(before.get('value'))} → {number(after.get('value'))}"
                )
            elif after and after.get("axis") is not None:
                delta = entry.get("delta") or {}
                changed = {k: v for k, v in delta.items() if v}
                note = f"{len(changed)} period(s) changed" if changed else "unchanged"
                lines.append(f"  {name}: {note}")
            else:
                lines.append(f"  {name}: new")
    if result.impact and result.impact.checks:
        for name, states in result.impact.checks.items():
            lines.append(f"  check {name}: {states['before']} → {states['after']}")
    return "\n".join(lines)


def commit(ctx: typer.Context, ops: list[dict], allow_errors: bool = False) -> None:
    state: State = ctx.obj
    ws = state.workspace()
    result = ws.apply(ops, allow_errors=allow_errors)
    if result.ok:
        state.save(ws)
    state.out.emit(
        result.to_dict(),
        batch_text(result),
        ok=result.ok,
        errors=[e.to_dict() for e in result.errors],
    )


def _upsert(kind: str, name: str, fields: dict[str, Any]) -> list[dict]:
    if not fields:
        raise EpmError("invalid_input", "nothing to set; pass at least one option")
    return [{"op": "upsert", "kind": kind, "name": name, "set": fields}]


# -- inspection ---------------------------------------------------------------------------

KINDS = {
    "metrics": "metrics",
    "inputs": "metrics",
    "axes": "axes",
    "sources": "sources",
    "tables": "tables",
    "checks": "checks",
    "circularities": "circularities",
    "analyses": "analyses",
}


def _definition_label(m) -> str:
    return "formula" if m.formula else "value" if m.value is not None else "source"


def _metric_kind(m) -> str:
    if m.value is not None:
        return "value"
    if m.input is not None:
        return "input"
    return "formula+history" if m.history is not None else "formula"


@app.command("list")
@command
def list_objects(
    ctx: typer.Context,
    kind: Annotated[
        str,
        typer.Argument(
            help="metrics, inputs, axes, sources, functions, tables, checks, "
            "circularities, analyses, files, or builtins (the formula functions)"
        ),
    ] = "metrics",
    folder: Annotated[str | None, typer.Option(help="Only objects in this folder.")] = None,
    search: Annotated[
        str | None, typer.Option(help="Substring of name, folder or description.")
    ] = None,
) -> None:
    """List model objects, or the built-in formula functions."""
    state: State = ctx.obj
    rows: list[dict[str, Any]] = []
    if kind == "builtins":  # needs no model
        rows = [{"name": n, "signature": b.signature, "doc": b.doc} for n, b in BUILTINS.items()]
    elif kind == "functions":
        ws = state.workspace()
        for name, spec in ws.engine.functions.items():
            rows.append({"name": name, "signature": spec.signature, "returns": spec.returns})
    elif kind == "files":
        ws = state.workspace()
        rows = [{"name": p, "bytes": len(b)} for p, b in sorted(ws.inputs.items())]
    elif kind in KINDS:
        ws = state.workspace()
        items = getattr(ws.model, KINDS[kind])
        for name, obj in items.items():
            if kind == "inputs" and obj.formula is not None:
                continue
            row: dict[str, Any] = {"name": name}
            if KINDS[kind] == "metrics":
                row.update(
                    kind=_metric_kind(obj),
                    axis=ws.engine.shape(name),
                    unit=ws.engine.unit(name),
                    folder=obj.folder,
                    definition=obj.formula
                    if obj.formula
                    else obj.value
                    if obj.input is None
                    else obj.input.model_dump(exclude_defaults=True),
                )
            else:
                row["folder"] = getattr(obj, "folder", None)
            row["description"] = getattr(obj, "description", None)
            rows.append(row)
    else:
        raise EpmError("invalid_input", f"unknown kind '{kind}'")
    if folder is not None:
        rows = [r for r in rows if r.get("folder") == folder]
    if search:
        q = search.lower()
        fields = ("name", "folder", "description", "doc")
        rows = [r for r in rows if any(q in str(r.get(k) or "").lower() for k in fields)]
    columns = [k for k in rows[0] if k != "description"] if rows else ["name"]
    text = (
        table([[("" if r.get(c) is None else r.get(c)) for c in columns] for r in rows], columns)
        if rows
        else f"no {kind}"
    )
    state.out.emit(rows, text)


@app.command()
@command
def show(
    ctx: typer.Context,
    name: str,
    kind: Annotated[str | None, typer.Option(help="Object kind, if the name is ambiguous.")] = None,
) -> None:
    """Show a definition, and for metrics its value, axis, unit, dependencies and status."""
    state: State = ctx.obj
    ws = state.workspace()
    model = ws.model
    if (kind in (None, "metric")) and name in model.metrics:
        engine = ws.engine
        run = ws.run(outputs=[name], checks=False)
        m = model.metrics[name]
        status = run.status[name]
        data = {
            "kind": "metric",
            "name": name,
            "definition": m.model_dump(mode="json", exclude_defaults=True),
            "axis": engine.shape(name),
            "unit": engine.unit(name),
            "scope": "forecast" if engine.metrics[name].start else "all",
            "status": status.to_dict(),
            "value": run.values.get(name),
            "depends_on": engine.trace(name, "upstream", 1),
            "used_by": engine.trace(name, "downstream", 1),
            "fingerprint": engine.fingerprints.get(name),
        }
        source = m.formula or (
            m.value if m.input is None else f"input {m.input.source}:{m.input.item or m.input.cell}"
        )
        lines = [
            f"metric {name}"
            + (f"  [{data['unit']}]" if data["unit"] else "")
            + (f"  axis {data['axis']}" if data["axis"] else ""),
            f"  {_definition_label(m)}: {source}",
        ]
        if m.history:
            lines.append(f"  history: {m.history.source}:{m.history.item}")
        lines.append(
            f"  status: {status.state}" + (f" ({status.message})" if status.message else "")
        )
        lines.append(f"  depends on: {', '.join(data['depends_on']) or '—'}")
        lines.append(f"  used by: {', '.join(data['used_by']) or '—'}")
        if name in run.values:
            lines.append("")
            lines.append(values_table({name: run.values[name]}, {name: data["unit"]}))
        state.out.emit(data, "\n".join(lines))
        return
    collections = {
        "axis": "axes",
        "source": "sources",
        "table": "tables",
        "check": "checks",
        "circularity": "circularities",
        "analysis": "analyses",
    }
    for k, coll in collections.items():
        if kind in (None, k) and name in getattr(model, coll):
            definition = getattr(model, coll)[name].model_dump(mode="json", exclude_defaults=True)
            state.out.emit(
                {"kind": k, "name": name, "definition": definition},
                f"{k} {name}\n{json.dumps(definition, indent=2)}",
            )
            return
    if kind in (None, "function") and name in ws.engine.functions:
        spec = ws.engine.functions[name]
        state.out.emit(
            {"kind": "function", "name": name, "signature": spec.signature, "code": spec.source},
            spec.source,
        )
        return
    raise EpmError("not_found", f"nothing named '{name}'")


@app.command()
@command
def validate(ctx: typer.Context) -> None:
    """Check references, axes, units, sources and functions."""
    state: State = ctx.obj
    diagnostics = [d.to_dict() for d in state.workspace().validate()]
    errors = [d for d in diagnostics if d["severity"] == "error"]
    warnings = [d for d in diagnostics if d["severity"] == "warning"]
    text = "model is valid" if not errors else f"{len(errors)} error(s)"
    if warnings:
        text += f", {len(warnings)} warning(s)"
    state.out.emit(
        {"diagnostics": diagnostics}, text, ok=not errors, errors=errors, warnings=warnings
    )


@app.command()
@command
def run(
    ctx: typer.Context,
    set_: Annotated[
        list[str] | None, typer.Option("--set", help="Temporary assumption NAME=VALUE.")
    ] = None,
    output: Annotated[
        list[str] | None,
        typer.Option("--output", "-o", help="Metric(s) to report; comma-separated or repeated."),
    ] = None,
    save_cache: Annotated[
        bool, typer.Option(help="Store calculated values in the model's cache/.")
    ] = False,
) -> None:
    """Calculate the model, optionally with temporary assumptions."""
    state: State = ctx.obj
    ws = state.workspace()
    outputs = [o for item in output or [] for o in _csv(item) or []] or None
    result = ws.run(parse_assignments(set_), outputs)
    if save_cache:
        state.save(ws)
    units = {n: ws.engine.unit(n) for n in result.values}
    lines = [values_table(result.values, units)]
    if result.checks:
        failed = [c for c in result.checks.values() if not c.passed]
        lines.append(f"\nchecks: {len(result.checks) - len(failed)} passed, {len(failed)} failed")
        lines += [f"  {c.name}: {c.message}" for c in failed]
    problems = [d.to_dict() for d in result.diagnostics]
    errors = [d for d in problems if d["severity"] == "error"]
    warnings = [d for d in problems if d["severity"] == "warning"]
    state.out.emit(
        result.to_dict(), "\n".join(lines), ok=result.ok, errors=errors, warnings=warnings
    )


@app.command()
@command
def sweep(
    ctx: typer.Context,
    input_: Annotated[
        list[str], typer.Option("--input", "-i", help="NAME=v1,v2,... or NAME=start:stop:step")
    ],
    output: Annotated[
        list[str], typer.Option("--output", "-o", help="Output metric, or metric@period.")
    ],
    mode: Annotated[
        str, typer.Option(help="grid (all combinations) or zip (paired values).")
    ] = "grid",
    set_: Annotated[
        list[str] | None, typer.Option("--set", help="Fixed assumption NAME=VALUE.")
    ] = None,
) -> None:
    """Run independent scenarios across input values."""
    state: State = ctx.obj
    ws = state.workspace()
    outputs = [o for item in output for o in _csv(item) or []]
    result = ws.sweep(parse_inputs(input_), outputs, mode, parse_assignments(set_))  # type: ignore[arg-type]
    frame = result.frame()
    rows = [
        [*(number(v) if not isinstance(v, str) else v for v in r[:-1]), r[-1] or ""]
        for r in frame.itertuples(index=False)
    ]
    state.out.emit(result.to_dict(), table(rows, list(frame.columns)), ok=True)


@app.command()
@command
def trace(
    ctx: typer.Context,
    name: str,
    upstream: Annotated[bool, typer.Option("--upstream/--downstream", help="Direction.")] = True,
    depth: Annotated[int | None, typer.Option(help="Levels to follow (default: all).")] = None,
) -> None:
    """List what a metric depends on, or what depends on it."""
    state: State = ctx.obj
    ws = state.workspace()
    direction = "upstream" if upstream else "downstream"
    names = ws.trace(name, direction, depth)
    rows = [{"name": n, "formula": ws.model.metrics[n].formula} for n in names]
    text = table(
        [[r["name"], r["formula"] or ""] for r in rows], [f"{direction} of {name}", "formula"]
    )
    state.out.emit({"metric": name, "direction": direction, "metrics": rows}, text)


@app.command("apply")
@command
def apply_batch(
    ctx: typer.Context,
    path: Annotated[str, typer.Argument(help="JSON batch file, or - for stdin.")],
    dry_run: Annotated[bool, typer.Option(help="Preview only.")] = False,
    propose: Annotated[
        bool, typer.Option(help="Queue for review in a running fm serve session.")
    ] = False,
    allow_errors: Annotated[
        bool, typer.Option(help="Commit even if validation errors appear.")
    ] = False,
) -> None:
    """Apply a batch of operations atomically."""
    state: State = ctx.obj
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    batch = json.loads(text)
    ws = state.workspace()
    warnings = []
    if propose and not state.in_session:
        # without a session to hold it, a proposal is only a preview
        warnings.append(
            {"code": "no_session", "message": "no fm serve session; showing a preview instead"}
        )
        propose, dry_run = False, True
    mode = "propose" if propose else "dry_run" if dry_run else "commit"
    result = ws.apply(batch, mode=mode, allow_errors=allow_errors)
    if result.ok and result.committed:
        state.save(ws)
    state.out.emit(
        result.to_dict(),
        batch_text(result),
        ok=result.ok,
        errors=[e.to_dict() for e in result.errors],
        warnings=warnings,
    )


@app.command()
@command
def init(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help=f"New {EXTENSION} file (or an empty directory).")],
    name: Annotated[str | None, typer.Option(help="Model name.")] = None,
    axis: Annotated[str, typer.Option(help="Name of the period axis.")] = "fy",
    years: Annotated[str | None, typer.Option(help="Axis values, e.g. 2024-2030.")] = None,
    historical: Annotated[
        str | None, typer.Option(help="Historical values, e.g. 2024-2025.")
    ] = None,
) -> None:
    """Create a new model."""
    state: State = ctx.obj
    if path.exists() and not (path.is_dir() and not any(path.iterdir())):
        raise EpmError("exists", f"'{path}' already exists")
    if path.suffix != EXTENSION and not path.is_dir():
        path = path.with_suffix(EXTENSION)
    model = Model(settings=Settings(name=name or path.stem))
    if years:
        data = model.model_dump(mode="json", exclude_defaults=True)
        data["axes"] = {
            axis: {
                "values": parse_axis_values(years),
                "historical": parse_axis_values(historical) if historical else [],
            }
        }
        data["settings"]["default_axis"] = axis
        model = Model.model_validate(data)
    ws = Workspace(model, path=path)
    state.save(ws)
    state.out.emit({"path": str(path)}, f"created {path}")


@app.command("pack")
@command
def pack_cmd(ctx: typer.Context, directory: Path, archive: Path) -> None:
    """Zip a model directory into an archive."""
    pack(directory, archive)
    ctx.obj.out.emit({"path": str(archive)}, f"wrote {archive}")


@app.command("unpack")
@command
def unpack_cmd(ctx: typer.Context, archive: Path, directory: Path) -> None:
    """Extract an archive into an empty directory."""
    unpack(archive, directory)
    ctx.obj.out.emit({"path": str(directory)}, f"unpacked into {directory}")


def _session(ctx: typer.Context, what: str) -> tuple[State, Workspace]:
    state: State = ctx.obj
    if not state.in_session:
        raise EpmError(
            "no_session",
            f"{what} needs a running `fm serve` session: history and pending batches are kept "
            "in memory only, and each CLI command is its own session",
        )
    return state, state.workspace()


@app.command()
@command
def history(ctx: typer.Context) -> None:
    """Show this session's committed batches (needs fm serve)."""
    state, ws = _session(ctx, "history")
    rows = [[e.id, e.label] for e in ws.history]
    text = table(rows, ["id", "committed"]) if rows else "no committed batches in this session"
    if ws.redo_stack:
        text += f"\n{len(ws.redo_stack)} batch(es) can be redone"
    data = {
        "history": [e.to_dict() for e in ws.history],
        "redo": [e.to_dict() for e in ws.redo_stack],
    }
    state.out.emit(data, text)


@app.command()
@command
def undo(ctx: typer.Context) -> None:
    """Undo the last committed batch (needs fm serve)."""
    state, ws = _session(ctx, "undo")
    entry = ws.undo()
    state.out.emit(entry.to_dict(), f"undid: {entry.label}")


@app.command()
@command
def redo(ctx: typer.Context) -> None:
    """Redo the last undone batch (needs fm serve)."""
    state, ws = _session(ctx, "redo")
    entry = ws.redo()
    state.out.emit(entry.to_dict(), f"redid: {entry.label}")


@app.command()
@command
def rollback(ctx: typer.Context, batch_id: str) -> None:
    """Undo everything after a committed batch (needs fm serve)."""
    state, ws = _session(ctx, "rollback")
    undone = ws.rollback(batch_id)
    state.out.emit(
        {"undone": [e.to_dict() for e in undone]},
        "\n".join(f"undid: {e.label}" for e in undone) or "nothing to undo",
    )


@pending_app.command("list")
@command
def pending_list(ctx: typer.Context) -> None:
    """List proposed batches (needs fm serve)."""
    state, ws = _session(ctx, "pending batches")
    rows = [{"id": p.id, "label": p.preview.label, "at": p.at} for p in ws.pending.values()]
    text = table([[r["id"], r["label"]] for r in rows], ["id", "proposed"]) if rows else "none"
    state.out.emit(rows, text)


@pending_app.command("show")
@command
def pending_show(ctx: typer.Context, batch_id: str) -> None:
    """Show a proposed batch's changes and effect (needs fm serve)."""
    state, ws = _session(ctx, "pending batches")
    if batch_id not in ws.pending:
        raise EpmError("not_found", f"no pending batch '{batch_id}'")
    preview = ws.pending[batch_id].preview
    state.out.emit(preview.to_dict(), batch_text(preview))


@pending_app.command("commit")
@command
def pending_commit(ctx: typer.Context, batch_id: str) -> None:
    """Commit a proposed batch (needs fm serve)."""
    state, ws = _session(ctx, "pending batches")
    result = ws.commit_pending(batch_id)
    state.out.emit(
        result.to_dict(),
        batch_text(result),
        ok=result.ok,
        errors=[e.to_dict() for e in result.errors],
    )


@pending_app.command("reject")
@command
def pending_reject(ctx: typer.Context, batch_id: str) -> None:
    """Reject a proposed batch (needs fm serve)."""
    state, ws = _session(ctx, "pending batches")
    ws.reject_pending(batch_id)
    state.out.emit({"rejected": batch_id}, f"rejected {batch_id}")


@app.command()
@command
def serve(
    ctx: typer.Context,
    port: Annotated[int, typer.Option(help="Port (0 picks a free one).")] = 8765,
    host: Annotated[str, typer.Option(help="Interface to listen on.")] = "127.0.0.1",
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="Open the UI.")] = False,
    no_auth: Annotated[
        bool, typer.Option("--no-auth", help="Skip the session token (development).")
    ] = False,
    frontend: Annotated[Path | None, typer.Option(help="Built frontend directory.")] = None,
) -> None:
    """Serve the web UI and a session that keeps undo history and pending proposals."""
    from ..server import serve as run_server

    state: State = ctx.obj
    run_server(
        find_model(state.model),
        host=host,
        port=port,
        open_browser=open_browser,
        auth=not no_auth,
        frontend=frontend,
        announce=lambda text: state.out.emit(None, text) if not state.out.as_json else None,
    )


# -- metrics ------------------------------------------------------------------------------


@metric_app.command("set")
@command
def metric_set(
    ctx: typer.Context,
    name: str,
    value: Annotated[str | None, typer.Option(help="A number.")] = None,
    values: Annotated[str | None, typer.Option(help="Per period: 2024=1.0,2025=2.0")] = None,
    formula: Annotated[
        str | None, typer.Option(help="e.g. 'ebitda - depreciation_amortization'")
    ] = None,
    input_: Annotated[
        str | None, typer.Option("--input", help="SOURCE:ITEM series binding.")
    ] = None,
    cell: Annotated[
        str | None, typer.Option(help="SOURCE:CELL single-value binding, e.g. deal:B4")
    ] = None,
    history: Annotated[str | None, typer.Option(help="SOURCE:ITEM for historical periods.")] = None,
    no_history: Annotated[
        bool, typer.Option("--no-history", help="Remove the history binding.")
    ] = False,
    scale: Annotated[float | None, typer.Option(help="Multiply bound values, e.g. 0.001.")] = None,
    unit: Annotated[str | None, typer.Option()] = None,
    axis: Annotated[str | None, typer.Option()] = None,
    scope: Annotated[str | None, typer.Option(help="all or forecast")] = None,
    folder: Annotated[str | None, typer.Option()] = None,
    description: Annotated[str | None, typer.Option()] = None,
    allow_errors: Annotated[
        bool, typer.Option(help="Commit even if validation errors appear.")
    ] = False,
) -> None:
    """Create a metric, or change the given fields of an existing one."""
    definitions = [x for x in (value, values, formula, input_, cell) if x is not None]
    if len(definitions) > 1:
        raise EpmError(
            "invalid_input", "pass only one of --value, --values, --formula, --input, --cell"
        )
    fields: dict[str, Any] = {}
    if definitions:
        fields.update(value=None, formula=None, input=None)
        if value is not None:
            fields["value"] = float(value)
        elif values is not None:
            fields["value"] = per_period(values)
        elif formula is not None:
            fields["formula"] = formula
        else:
            binding = parse_binding(input_ or cell, cell=cell is not None)  # type: ignore[arg-type]
            if scale is not None:
                binding["scale"] = scale
            fields["input"] = binding
        if formula is None:
            fields["history"] = None
    if history is not None:
        binding = parse_binding(history)
        if scale is not None:
            binding["scale"] = scale
        fields["history"] = binding
    if no_history:
        fields["history"] = None
    for key, v in (
        ("unit", unit),
        ("axis", axis),
        ("scope", scope),
        ("folder", folder),
        ("description", description),
    ):
        if v is not None:
            fields[key] = v or None
    commit(ctx, _upsert("metric", name, fields), allow_errors)


def _rename_delete(sub: typer.Typer, kind: str) -> None:
    @sub.command("rename")
    @command
    def rename(ctx: typer.Context, old: str, new: str) -> None:
        """Rename and update every reference."""
        commit(ctx, [{"op": "rename", "kind": kind, "name": old, "to": new}])

    @sub.command("delete")
    @command
    def delete(ctx: typer.Context, name: str) -> None:
        """Delete; fails while anything still references it."""
        commit(ctx, [{"op": "delete", "kind": kind, "name": name}])


for _sub_app, _kind in (
    (metric_app, "metric"),
    (axis_app, "axis"),
    (source_app, "source"),
    (file_app, "file"),
    (function_app, "function"),
    (table_app, "table"),
    (check_app, "check"),
    (circularity_app, "circularity"),
    (analysis_app, "analysis"),
):
    _rename_delete(_sub_app, _kind)


# -- axes, sources, files ---------------------------------------------------------------------


@axis_app.command("set")
@command
def axis_set(
    ctx: typer.Context,
    name: str,
    values: Annotated[str | None, typer.Option(help="2024-2030 or a,b,c")] = None,
    historical: Annotated[
        str | None, typer.Option(help="Historical values, e.g. 2024-2025.")
    ] = None,
    kind: Annotated[str | None, typer.Option(help="period or category")] = None,
    label: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Create or change an axis."""
    fields: dict[str, Any] = {}
    if values is not None:
        fields["values"] = parse_axis_values(values)
    if historical is not None:
        fields["historical"] = parse_axis_values(historical) if historical else []
    if kind is not None:
        fields["kind"] = kind
    if label is not None:
        fields["label"] = label
    commit(ctx, _upsert("axis", name, fields))


def _file_op(local: Path, target: str | None) -> tuple[str, dict]:
    data = local.read_bytes()
    path = target or f"inputs/{local.name}"
    return path, {
        "op": "upsert",
        "kind": "file",
        "name": path,
        "data": base64.b64encode(data).decode(),
    }


@source_app.command("set")
@command
def source_set(
    ctx: typer.Context,
    name: str,
    file: Annotated[Path | None, typer.Option(help="Local CSV/XLSX to copy into inputs/.")] = None,
    as_: Annotated[
        str | None, typer.Option("--as", help="Path inside the model, e.g. inputs/is.xlsx")
    ] = None,
    path: Annotated[str | None, typer.Option(help="Use a file already under inputs/.")] = None,
    sheet: Annotated[str | None, typer.Option()] = None,
    range_: Annotated[str | None, typer.Option("--range", help="A1 range, e.g. A1:H40")] = None,
    orientation: Annotated[
        str | None, typer.Option(help="rows (series in rows) or columns")
    ] = None,
    index: Annotated[
        str | None,
        typer.Option(help="Row number (rows) or column letter (columns) of axis labels."),
    ] = None,
    labels: Annotated[
        str | None, typer.Option(help="Column letter (rows) or row number (columns) of item names.")
    ] = None,
    axis: Annotated[str | None, typer.Option()] = None,
    delimiter: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Create or change a source; --file also copies the file into the model."""
    ops: list[dict] = []
    fields: dict[str, Any] = {}
    if file is not None:
        target, op = _file_op(file, as_)
        ops.append(op)
        fields["file"] = target
    elif path is not None:
        fields["file"] = path
    for key, v in (
        ("sheet", sheet),
        ("range", range_),
        ("orientation", orientation),
        ("axis", axis),
        ("delimiter", delimiter),
    ):
        if v is not None:
            fields[key] = v or None
    for key, v in (("index", index), ("labels", labels)):
        if v is not None:
            fields[key] = int(v) if v.isdigit() else (v or None)
    commit(ctx, [*ops, *_upsert("source", name, fields)])


@source_app.command("add")
@command
def source_add(
    ctx: typer.Context,
    name: str,
    file: Annotated[Path, typer.Option(help="Local CSV/XLSX to copy into inputs/.")],
    sheet: Annotated[str | None, typer.Option()] = None,
    range_: Annotated[str | None, typer.Option("--range")] = None,
    orientation: Annotated[str, typer.Option(help="rows or columns")] = "rows",
    axis: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Add a source from a local file."""
    target, op = _file_op(file, None)
    fields = {
        "file": target,
        "sheet": sheet,
        "range": range_,
        "orientation": orientation,
        "axis": axis,
    }
    commit(
        ctx,
        [
            op,
            {
                "op": "create",
                "kind": "source",
                "name": name,
                "def": {k: v for k, v in fields.items() if v is not None},
            },
        ],
    )


@file_app.command("add")
@command
def file_add(
    ctx: typer.Context,
    local: Path,
    as_: Annotated[str | None, typer.Option("--as", help="Path inside the model.")] = None,
) -> None:
    """Copy a local file into inputs/ (replacing any file at that path)."""
    _, op = _file_op(local, as_)
    commit(ctx, [op])


# -- functions ------------------------------------------------------------------------------


@function_app.command("set")
@command
def function_set(
    ctx: typer.Context,
    name: str,
    file: Annotated[Path | None, typer.Option(help="Python file containing the function.")] = None,
    code: Annotated[str | None, typer.Option(help="Source code of the function.")] = None,
) -> None:
    """Add or replace one @function in functions.py."""
    if (file is None) == (code is None):
        raise EpmError("invalid_input", "pass exactly one of --file or --code")
    text = file.read_text(encoding="utf-8") if file is not None else code
    commit(ctx, [{"op": "upsert", "kind": "function", "name": name, "set": {"code": text}}])


function_app.command("add")(function_set)


@function_app.command("show")
@command
def function_show(ctx: typer.Context, name: str) -> None:
    """Print a function's source."""
    show(ctx, name, "function")


# -- tables, checks, circularities, settings ----------------------------------------------


@table_app.command("set")
@command
def table_set(
    ctx: typer.Context,
    name: str,
    rows: Annotated[
        str | None, typer.Option(help="revenue,ebitda:subtotal,#Below EBITDA,ebit")
    ] = None,
    axis: Annotated[str | None, typer.Option()] = None,
    folder: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Create or change a table (rows reference metrics; #text adds a header)."""
    fields: dict[str, Any] = {}
    if rows is not None:
        parsed = []
        for item in _csv(rows) or []:
            if item.startswith("#"):
                parsed.append({"header": item[1:].strip()})
            else:
                metric, _, style = item.partition(":")
                parsed.append({"metric": metric, **({"style": style} if style else {})})
        fields["rows"] = parsed
    if axis is not None:
        fields["axis"] = axis or None
    if folder is not None:
        fields["folder"] = folder or None
    commit(ctx, _upsert("table", name, fields))


@table_app.command("show")
@command
def table_show(ctx: typer.Context, name: str) -> None:
    """Show a table with calculated values."""
    state: State = ctx.obj
    ws = state.workspace()
    if name not in ws.model.tables:
        raise EpmError("not_found", f"there is no table '{name}'")
    tbl = ws.model.tables[name]
    metrics = [r.metric for r in tbl.rows if r.metric]
    result = ws.run(outputs=[m for m in metrics if m in ws.model.metrics], checks=False)
    labels: list[str] = []
    for m in metrics:
        v = result.values.get(m)
        if isinstance(v, pd.Series):
            labels = [str(x) for x in v.index]
            break
    rows, data_rows = [], []
    for r in tbl.rows:
        if r.header:
            rows.append([r.header.upper()])
            data_rows.append({"header": r.header})
            continue
        v = result.values.get(r.metric)
        unit = ws.engine.unit(r.metric) if r.metric in ws.model.metrics else None
        cells = [number(x, unit) for x in v] if isinstance(v, pd.Series) else [number(v, unit)]
        rows.append([("  " if r.style is None else "") + (r.label or r.metric), *cells])
        data_rows.append({"metric": r.metric, "label": r.label, "style": r.style, "value": v})
    state.out.emit(
        {"table": name, "rows": data_rows}, table(rows, ["", *labels] if labels else None)
    )


@check_app.command("set")
@command
def check_set(
    ctx: typer.Context,
    name: str,
    expr: Annotated[
        str | None, typer.Option(help="Passes when true, or within tolerance of zero.")
    ] = None,
    tolerance: Annotated[float | None, typer.Option()] = None,
    severity: Annotated[str | None, typer.Option(help="error or warning")] = None,
) -> None:
    """Create or change a check."""
    fields = {
        k: v
        for k, v in (("expr", expr), ("tolerance", tolerance), ("severity", severity))
        if v is not None
    }
    commit(ctx, _upsert("check", name, fields))


@circularity_app.command("set")
@command
def circularity_set(
    ctx: typer.Context,
    name: str,
    metrics: Annotated[str | None, typer.Option(help="Comma-separated loop members.")] = None,
    tol: Annotated[float | None, typer.Option()] = None,
    max_iter: Annotated[int | None, typer.Option()] = None,
    damping: Annotated[float | None, typer.Option()] = None,
    seed: Annotated[float | None, typer.Option()] = None,
) -> None:
    """Declare a same-period loop to be solved by iteration."""
    fields: dict[str, Any] = {
        k: v
        for k, v in (("tol", tol), ("max_iter", max_iter), ("damping", damping), ("seed", seed))
        if v is not None
    }
    if metrics is not None:
        fields["metrics"] = _csv(metrics)
    commit(ctx, _upsert("circularity", name, fields))


@settings_app.command("set")
@command
def settings_set(
    ctx: typer.Context,
    name: Annotated[str | None, typer.Option()] = None,
    currency: Annotated[str | None, typer.Option()] = None,
    default_axis: Annotated[str | None, typer.Option()] = None,
    strict_units: Annotated[bool | None, typer.Option("--strict-units/--no-strict-units")] = None,
    key_outputs: Annotated[
        str | None, typer.Option(help="Comma-separated metrics shown in previews.")
    ] = None,
) -> None:
    """Change model settings."""
    fields: dict[str, Any] = {
        k: v
        for k, v in (
            ("name", name),
            ("currency", currency),
            ("default_axis", default_axis),
            ("strict_units", strict_units),
        )
        if v is not None
    }
    if key_outputs is not None:
        fields["key_outputs"] = _csv(key_outputs)
    if not fields:
        raise EpmError("invalid_input", "nothing to set; pass at least one option")
    commit(ctx, [{"op": "update", "kind": "settings", "set": fields}])


# -- analyses -------------------------------------------------------------------------------


@analysis_app.command("set")
@command
def analysis_set(
    ctx: typer.Context,
    name: str,
    input_: Annotated[
        list[str] | None, typer.Option("--input", "-i", help="NAME=v1,v2 or NAME=start:stop:step")
    ] = None,
    output: Annotated[
        list[str] | None, typer.Option("--output", "-o", help="Metric or metric@period.")
    ] = None,
    mode: Annotated[str | None, typer.Option(help="grid or zip")] = None,
    set_: Annotated[
        list[str] | None, typer.Option("--set", help="Fixed assumption NAME=VALUE.")
    ] = None,
    plot: Annotated[str | None, typer.Option(help="line or heatmap")] = None,
    x: Annotated[str | None, typer.Option(help="Plot x (an input).")] = None,
    y: Annotated[
        str | None, typer.Option(help="Line: outputs (comma-separated); heatmap: the other input.")
    ] = None,
    z: Annotated[str | None, typer.Option(help="Heatmap colour (an output).")] = None,
    format_: Annotated[str | None, typer.Option("--format", help="Value format, e.g. .1%")] = None,
) -> None:
    """Create or change a saved analysis."""
    fields: dict[str, Any] = {}
    if input_:
        fields["inputs"] = [{"metric": k, "values": v} for k, v in parse_inputs(input_).items()]
    if output:
        fields["outputs"] = [o for item in output for o in _csv(item) or []]
    if mode is not None:
        fields["mode"] = mode
    if set_:
        fields["overrides"] = parse_assignments(set_)
    if plot is not None:
        inputs = fields.get("inputs") or []
        spec: dict[str, Any] = {"kind": plot, "x": x or (inputs[0]["metric"] if inputs else None)}
        if plot == "heatmap":
            spec["y"] = y or (inputs[1]["metric"] if len(inputs) > 1 else None)
            spec["z"] = z or (fields.get("outputs") or [None])[0]
        elif y:
            spec["y"] = _csv(y)
        if format_:
            spec["format"] = format_
        fields["plot"] = spec
    commit(ctx, _upsert("analysis", name, fields))


@analysis_app.command("run")
@command
def analysis_run(
    ctx: typer.Context,
    name: str,
    plot: Annotated[
        Path | None, typer.Option(help="Save the chart as an image, e.g. out.png")
    ] = None,
) -> None:
    """Run a saved analysis against the current model."""
    state: State = ctx.obj
    ws = state.workspace()
    result = ws.run_analysis(name)
    if plot is not None:
        result.save_plot(str(plot))
    frame = result.sweep.frame()
    rows = [
        [*(number(v) if not isinstance(v, str) else v for v in r[:-1]), r[-1] or ""]
        for r in frame.itertuples(index=False)
    ]
    text = table(rows, list(frame.columns)) + (f"\nsaved plot to {plot}" if plot else "")
    state.out.emit(result.to_dict(), text)


def run_cli() -> None:
    """Entry point: forwards to a running ``fm serve`` session when there is one."""
    from .routing import run

    run()
