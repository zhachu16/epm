"""A served model: one workspace, its session history, saving, events and outside edits."""

from __future__ import annotations

import contextlib
import secrets
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..cli.output import clean
from ..engine.results import value_to_dict
from ..errors import EpmError
from ..formula.builtins import BUILTINS
from ..workspace import Workspace
from .events import EventBus

Snapshot = tuple[int, tuple[str, ...], tuple[str, ...], tuple[str, ...]]


class Session:
    """Owns the model while ``fm serve`` runs.

    Every change, whether from the UI, a forwarded CLI command or the terminal, goes
    through here under one lock. It is saved to disk straight away and announced on the
    event bus. Undo history and pending proposals live only in memory.
    """

    def __init__(self, path: str | Path, token: str | None = None):
        self.path = Path(path).resolve()
        self.ws = Workspace.open(self.path)
        self.token = token or secrets.token_urlsafe(24)
        self.lock = threading.RLock()
        self.events = EventBus()
        self._signature = self._disk_signature()
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------------

    def start_watching(self, interval: float = 1.0) -> None:
        def loop() -> None:
            while not self._stop.wait(interval):
                try:
                    self.check_disk()
                except Exception as e:  # noqa: BLE001 - keep watching whatever happens
                    self.log("error", f"checking the model file failed: {e}")

        self._watcher = threading.Thread(target=loop, name="epm-watcher", daemon=True)
        self._watcher.start()

    def close(self, save_cache: bool = True) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        with self.lock:
            if save_cache:
                with contextlib.suppress(Exception):  # shutting down; nothing else to do
                    self.ws.save()
            self.ws.close()

    # -- events and persistence ------------------------------------------------------------

    def log(self, level: str, message: str) -> None:
        self.events.publish({"type": "log", "level": level, "message": message})

    def _disk_signature(self) -> tuple:
        p = self.path
        if p.is_dir():
            inputs = p / "inputs"
            files = [p / "model.json", p / "functions.py"]
            files += sorted(inputs.rglob("*")) if inputs.is_dir() else []
        else:
            files = [p]
        signature = []
        for f in files:
            try:
                st = f.stat()
                signature.append((str(f), st.st_mtime_ns, st.st_size))
            except FileNotFoundError:
                signature.append((str(f), None, None))
        return tuple(signature)

    def persist(self, source: str) -> None:
        """Save the model (without the cache, for speed) and announce the change."""
        self.ws.save(include_cache=False)
        self._signature = self._disk_signature()
        self.events.publish({"type": "model_changed", "version": self.ws.version, "source": source})

    def check_disk(self) -> bool:
        """Reload if something outside the session changed the model on disk."""
        with self.lock:
            signature = self._disk_signature()
            if signature == self._signature:
                return False
            self._signature = signature
            try:
                fresh = Workspace.open(self.path)
            except EpmError as e:
                self.log("warning", f"the model changed on disk but could not be read: {e.message}")
                return False
            fresh.cache = self.ws.cache  # fingerprints keep whatever still matches valid
            fresh.version = self.ws.version + 1
            self.ws.close()
            self.ws = fresh
        self.events.publish({"type": "model_changed", "version": fresh.version, "source": "file"})
        self.log("info", "reloaded the model after an outside change; undo history was cleared")
        return True

    def _snapshot(self) -> Snapshot:
        ws = self.ws
        return (
            ws.version,
            tuple(e.id for e in ws.history),
            tuple(e.id for e in ws.redo_stack),
            tuple(ws.pending),
        )

    def _announce(self, before: Snapshot, source: str) -> None:
        after = self._snapshot()
        if after[0] != before[0]:
            self.persist(source)
        if after[1:3] != before[1:3]:
            self.events.publish({"type": "history_changed"})
        if after[3] != before[3]:
            self.events.publish({"type": "pending_changed"})

    def mutate(self, action: Callable[[Workspace], Any], source: str = "ui") -> Any:
        """Run ``action`` on the workspace, then save and announce whatever it changed."""
        with self.lock:
            before = self._snapshot()
            try:
                return action(self.ws)
            finally:
                self._announce(before, source)

    # -- CLI commands --------------------------------------------------------------------

    def run_cli(self, argv: list[str], source: str = "cli") -> dict[str, Any]:
        """Run an ``fm`` command against the live workspace and capture its output."""
        import click
        import typer
        from typer.testing import CliRunner

        from ..cli.app import State, app

        out: list[str] = []
        err: list[str] = []
        with self.lock:
            before = self._snapshot()
            try:
                if any(a in ("--help", "-h") for a in argv) or not argv:
                    # help text goes straight to stdout, so capture it the slow way
                    result = CliRunner().invoke(app, argv or ["--help"])
                    out.append(result.output.rstrip("\n"))
                    code = result.exit_code
                else:
                    state = State(None, False, workspace=self.ws, out=out.append, err=err.append)
                    command = typer.main.get_command(app)
                    try:
                        rv = command.main(
                            args=list(argv), prog_name="fm", standalone_mode=False, obj=state
                        )
                        code = rv if isinstance(rv, int) else 0
                    except click.ClickException as e:
                        err.append(f"error: {e.format_message()}")
                        code = e.exit_code
                    except click.exceptions.Abort:
                        code = 1
                    except Exception as e:  # noqa: BLE001
                        err.append(f"error: {type(e).__name__}: {e}")
                        code = 1
            finally:
                self._announce(before, source)
        return {
            "stdout": "".join(f"{line}\n" for line in out),
            "stderr": "".join(f"{line}\n" for line in err),
            "exit_code": code,
        }

    # -- what the UI shows -----------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Everything the UI needs: definitions, calculated values, status and history."""
        with self.lock:
            return workspace_snapshot(self.ws, str(self.path))


def workspace_snapshot(ws: Workspace, path: str | None = None) -> dict[str, Any]:
    """The UI's view of a workspace: definitions, calculated values, status and history.

    Shared by ``fm serve`` and the cloud service, so both frontends read one shape.
    """
    engine = ws.engine
    run = ws.run()
    model = ws.model
    owner = {m: name for name, c in model.circularities.items() for m in c.metrics}
    group_of = _graph_groups(model)
    metrics = {}
    for name, cm in engine.metrics.items():
        d = cm.definition
        kind = (
            "value"
            if d.value is not None
            else "input"
            if d.input is not None
            else "formula+history"
            if d.history is not None
            else "formula"
        )
        metrics[name] = {
            "kind": kind,
            "axis": engine.shape(name),
            "unit": engine.unit(name),
            "scope": "forecast" if cm.start else "all",
            "folder": d.folder,
            "description": d.description,
            "formula": d.formula,
            "deps": cm.refs,  # name -> lag; null means the whole series
            "used_by": sorted(engine.graph.dependants.get(name, {})),
            "status": run.status[name].to_dict() if name in run.status else None,
            "value": value_to_dict(run.values[name]) if name in run.values else None,
            "fingerprint": engine.fingerprints.get(name),
            "circularity": owner.get(name),
            "group": group_of[name],
        }
    functions = [
        {
            "name": n,
            "signature": s.signature,
            "returns": s.returns,
            "causal": s.causal,
            "source": s.source,
        }
        for n, s in engine.functions.items()
    ]
    payload = {
        "version": ws.version,
        "path": path,
        "name": model.settings.name,
        "model": model.model_dump(mode="json", exclude_defaults=True),
        "functions_source": ws.functions or "",
        "functions": functions,
        "files": [{"name": p, "bytes": len(b)} for p, b in sorted(ws.inputs.items())],
        "metrics": metrics,
        "checks": {k: c.to_dict() for k, c in run.checks.items()},
        "solver": {k: s.to_dict() for k, s in run.solver.items()},
        "diagnostics": [d.to_dict() for d in run.diagnostics],
        "history": [e.to_dict() for e in ws.history],
        "redo": [e.to_dict() for e in ws.redo_stack],
        "pending": [
            {
                "id": p.id,
                "label": p.preview.label,
                "at": p.at,
                "preview": p.preview.to_dict(),
            }
            for p in ws.pending.values()
        ],
        "builtins": [
            {"name": n, "signature": b.signature, "doc": b.doc} for n, b in BUILTINS.items()
        ],
    }
    return clean(payload)


def _graph_groups(model) -> dict[str, str]:
    """Group metrics for the graph by the table that shows them.

    A metric in no table joins the table most of its folder's metrics are in, so a
    statement's helper metrics sit with the statement; failing that, its top folder.
    """
    from collections import Counter

    group_of: dict[str, str] = {}
    for table_name, table in model.tables.items():
        for row in table.rows:
            if row.metric and row.metric in model.metrics and row.metric not in group_of:
                group_of[row.metric] = table_name
    votes: dict[str, Counter] = {}
    for name, metric in model.metrics.items():
        if metric.folder and name in group_of:
            votes.setdefault(metric.folder, Counter())[group_of[name]] += 1
    for name, metric in model.metrics.items():
        if name in group_of:
            continue
        folder = metric.folder
        if folder in votes:
            group_of[name] = votes[folder].most_common(1)[0][0]
        else:
            group_of[name] = folder.split("/")[0] if folder else "Other"
    return group_of
