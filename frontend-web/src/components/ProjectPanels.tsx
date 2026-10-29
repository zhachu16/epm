import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, errorText } from "../api/client";
import { useRefresh, useSaving } from "../api/hooks";
import type { AppState } from "../api/types";
import { formatBytes, formatWhen } from "../lib/format";
import { navigate, projectPath } from "../lib/router";
import { useStore } from "../store";

const SOURCE: Record<string, string> = {
  web: "web app",
  api: "API",
  mcp: "agent (MCP)",
  create: "created",
  import: "imported",
  duplicate: "copied",
  seed: "example",
};

/** Every saved version: view, download or restore one. Restoring saves it as a new version. */
export function VersionsPanel({ state }: { state: AppState }) {
  const { data: versions, error } = useQuery({ queryKey: ["versions", state.project.id], queryFn: api.versions });
  const log = useStore((s) => s.log);
  const saving = useSaving();
  const [busy, setBusy] = useState(false);
  const viewing = state.version;
  const editable = !state.project.is_example;

  const act = async (write: () => Promise<{ restored: number; version: { number: number } }>) => {
    setBusy(true);
    try {
      const result = await saving(write, (r) => r.version.number);
      log("info", `restored version ${result.restored} as version ${result.version.number}`);
      navigate(projectPath(state.project.id), true);
    } catch {
      // the save status and the log say what went wrong
    } finally {
      setBusy(false);
    }
  };

  if (error) return <div className="empty">{errorText(error)}</div>;
  if (!versions) return <div className="empty">Loading versions…</div>;
  return (
    <div data-testid="versions">
      <div className="list-row muted">
        Every saved change is a version. Restoring one saves it again as the newest version, so nothing is lost.
        {editable && (
          <button className="btn small" style={{ marginLeft: "auto" }} disabled={busy || versions.length < 2} onClick={() => act(api.undo)} data-testid="undo">
            Undo last change
          </button>
        )}
      </div>
      {versions.map((v) => (
        <div key={v.number} className={`list-row ${v.number === viewing ? "active" : ""}`} data-testid={`version-${v.number}`}>
          <span className="mono" style={{ width: 36 }}>
            v{v.number}
          </span>
          <span className="when">{formatWhen(v.created_at)}</span>
          <span style={{ flex: 1 }}>
            {v.label || "—"}
            {v.restored_from !== null && <span className="muted"> (from v{v.restored_from})</span>}
          </span>
          <span className="muted">{SOURCE[v.source] ?? v.source}</span>
          {v.current ? (
            <span className="pill ok">current</span>
          ) : (
            <>
              <button className="btn small" onClick={() => navigate(projectPath(state.project.id, v.number))} data-testid={`view-${v.number}`}>
                View
              </button>
              {editable && (
                <button className="btn small" disabled={busy} onClick={() => act(() => api.restore(v.number))} data-testid={`restore-${v.number}`}>
                  Restore
                </button>
              )}
            </>
          )}
          <a className="btn small" href={api.exportUrl(v.number)} title="Download this version as .epm">
            .epm
          </a>
        </div>
      ))}
    </div>
  );
}

/** The project's input files: download, upload (a new version) and delete. */
export function FilesPanel({ state }: { state: AppState }) {
  const { readOnly, log } = useStore();
  const saving = useSaving();
  const picker = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const write = async (action: () => Promise<{ ok: boolean; committed: boolean; version: number; errors: { message: string }[] }>, done: string) => {
    setBusy(true);
    try {
      const result = await saving(action, (r) => (r.committed ? r.version : null));
      if (result.ok) log("info", done);
      else log("error", result.errors.map((e) => e.message).join("; "));
    } catch {
      // reported by the save status
    } finally {
      setBusy(false);
    }
  };

  const upload = async (files: FileList | null) => {
    for (const file of Array.from(files ?? [])) {
      await write(() => api.uploadFile(`inputs/${file.name}`, file), `uploaded inputs/${file.name}`);
    }
    if (picker.current) picker.current.value = "";
  };

  return (
    <div data-testid="files">
      <div className="list-row muted">
        Source files the model reads (CSV, XLSX). Uploading or deleting one saves a new version.
        {!readOnly && (
          <>
            <input ref={picker} type="file" multiple hidden onChange={(e) => upload(e.target.files)} data-testid="upload-input" />
            <button className="btn small" style={{ marginLeft: "auto" }} disabled={busy} onClick={() => picker.current?.click()}>
              Upload…
            </button>
          </>
        )}
      </div>
      {state.files.length === 0 && <div className="list-row muted">No input files.</div>}
      {state.files.map((f) => (
        <div key={f.name} className="list-row">
          <span className="mono" style={{ flex: 1 }}>
            {f.name}
          </span>
          <span className="muted">{formatBytes(f.bytes)}</span>
          <a className="btn small" href={api.fileUrl(f.name)} data-testid={`download-${f.name}`}>
            Download
          </a>
          {!readOnly && (
            <button
              className="btn small danger"
              disabled={busy}
              onClick={() => window.confirm(`Delete ${f.name}?`) && write(() => api.deleteFile(f.name), `deleted ${f.name}`)}
            >
              Delete
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

/** Saved results: runs, sweeps, analyses and their charts. */
export function OutputsPanel({ state }: { state: AppState }) {
  const { data: outputs, error } = useQuery({ queryKey: ["outputs", state.project.id], queryFn: api.outputs });
  const { readOnly, log } = useStore();
  const refresh = useRefresh();
  const [shown, setShown] = useState<string | null>(null);

  const remove = async (id: string, name: string) => {
    if (!window.confirm(`Delete output ${name}?`)) return;
    try {
      await api.deleteOutput(id);
      log("info", `deleted output ${name}`);
    } catch (e) {
      log("error", errorText(e));
    }
    await refresh();
  };

  if (error) return <div className="empty">{errorText(error)}</div>;
  if (!outputs) return <div className="empty">Loading outputs…</div>;
  if (!outputs.length) {
    return <div className="empty">No saved outputs yet. Open an analysis and use “Save results” to keep its results and chart.</div>;
  }
  return (
    <div data-testid="outputs">
      {outputs.map((o) => (
        <div key={o.id}>
          <div className="list-row">
            <span className="pill">{o.kind}</span>
            <span className="mono" style={{ flex: 1 }}>
              {o.name}
            </span>
            <span className="muted">v{o.version}</span>
            <span className="when">{formatWhen(o.created_at)}</span>
            <span className="muted">{formatBytes(o.bytes)}</span>
            {o.content_type === "image/png" && (
              <button className="btn small" onClick={() => setShown(shown === o.id ? null : o.id)}>
                {shown === o.id ? "Hide" : "Show"}
              </button>
            )}
            <a className="btn small" href={api.outputUrl(o.id)} data-testid={`output-${o.name}`}>
              Download
            </a>
            {!readOnly && (
              <button className="btn small danger" onClick={() => remove(o.id, o.name)}>
                Delete
              </button>
            )}
          </div>
          {shown === o.id && <img className="output-image" src={api.outputUrl(o.id)} alt={o.name} />}
        </div>
      ))}
    </div>
  );
}
