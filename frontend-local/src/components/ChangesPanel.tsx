import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useRefresh } from "../api/hooks";
import type { AppState } from "../api/types";
import { useStore } from "../store";
import { BatchPreview } from "./BatchPreview";

type Selected = { type: "preview" } | { type: "pending"; id: string } | null;

const time = (at: number) => new Date(at * 1000).toLocaleTimeString();

export function ChangesPanel({ state }: { state: AppState }) {
  const { preview, setPreview, log } = useStore();
  const refresh = useRefresh();
  const [selected, setSelected] = useState<Selected>(null);
  const [busy, setBusy] = useState(false);

  // show the newest preview, or the first proposal when nothing else is chosen
  useEffect(() => {
    if (preview) setSelected({ type: "preview" });
  }, [preview]);
  useEffect(() => {
    if (selected?.type === "pending" && !state.pending.some((p) => p.id === selected.id)) setSelected(null);
    if (!selected && !preview && state.pending.length) setSelected({ type: "pending", id: state.pending[0].id });
  }, [state.pending, selected, preview]);

  const act = async (action: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await action();
      log("info", done);
    } catch (e) {
      log("error", e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
      await refresh();
    }
  };

  const pending = selected?.type === "pending" ? state.pending.find((p) => p.id === selected.id) : undefined;
  const detail = selected?.type === "preview" ? preview?.result : pending?.preview;

  return (
    <div className="changes" data-testid="changes">
      <div>
        {preview && (
          <div className={`list-row clickable ${selected?.type === "preview" ? "active" : ""}`} onClick={() => setSelected({ type: "preview" })}>
            <span className={`dot ${preview.result.ok ? "ok" : "error"}`} />
            <span>
              <b>Preview:</b> {preview.result.label}
            </span>
          </div>
        )}
        <div className="panel-header" style={{ borderTop: preview ? "1px solid var(--border)" : undefined }}>
          Proposed ({state.pending.length})
        </div>
        {state.pending.length === 0 && <div className="list-row muted">Agents propose changes with `fm apply changes.json --propose`.</div>}
        {state.pending.map((p) => (
          <div
            key={p.id}
            className="list-row clickable"
            style={{ background: selected?.type === "pending" && selected.id === p.id ? "var(--accent-soft)" : undefined }}
            onClick={() => setSelected({ type: "pending", id: p.id })}
            data-testid={`pending-${p.id}`}
          >
            <span className="when">{time(p.at)}</span>
            <span>{p.label}</span>
          </div>
        ))}
        <div className="panel-header" style={{ borderTop: "1px solid var(--border)" }}>
          History (this session)
        </div>
        {state.history.length === 0 && state.redo.length === 0 && <div className="list-row muted">Nothing committed yet.</div>}
        {[...state.history].reverse().map((h, i) => (
          <div key={h.id} className="list-row">
            <span className="when">{time(h.at)}</span>
            <span style={{ flex: 1 }}>{h.label}</span>
            {i > 0 && (
              <button className="btn small" disabled={busy} onClick={() => act(() => api.rollback(h.id), `rolled back to: ${h.label}`)} title="Undo everything after this">
                Roll back to here
              </button>
            )}
          </div>
        ))}
        {state.redo.map((h) => (
          <div key={h.id} className="list-row muted">
            <span className="when">{time(h.at)}</span>
            <span>undone: {h.label}</span>
          </div>
        ))}
      </div>
      <div>
        {detail ? (
          <>
            <BatchPreview state={state} result={detail} />
            <div className="row" style={{ padding: "0 12px 12px" }}>
              {selected?.type === "preview" && preview && (
                <>
                  <button
                    className="btn primary"
                    disabled={busy || !preview.commit}
                    onClick={() => act(async () => preview.commit?.(), "committed the previewed change")}
                    data-testid="commit-preview"
                  >
                    Commit
                  </button>
                  <button className="btn" onClick={() => (setPreview(null), setSelected(null))}>
                    Dismiss
                  </button>
                </>
              )}
              {pending && (
                <>
                  <button className="btn primary" disabled={busy} onClick={() => act(() => api.commitPending(pending.id), `committed proposal: ${pending.label}`)} data-testid="commit-pending">
                    Commit
                  </button>
                  <button className="btn danger" disabled={busy} onClick={() => act(() => api.rejectPending(pending.id), `rejected proposal: ${pending.label}`)} data-testid="reject-pending">
                    Reject
                  </button>
                </>
              )}
            </div>
          </>
        ) : (
          <div className="empty">Preview an edit, or pick a proposal, to see what it changes and how it moves the key outputs.</div>
        )}
      </div>
    </div>
  );
}
