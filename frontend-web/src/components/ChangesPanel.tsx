import { useState } from "react";
import { errorText } from "../api/client";
import type { AppState } from "../api/types";
import { useStore } from "../store";
import { BatchPreview } from "./BatchPreview";

/** The latest preview from an editor: what it changes and how it moves the key outputs. */
export function ChangesPanel({ state }: { state: AppState }) {
  const { preview, setPreview, log, readOnly } = useStore();
  const [busy, setBusy] = useState(false);

  const commit = async () => {
    if (!preview?.commit) return;
    setBusy(true);
    try {
      await preview.commit();
    } catch (e) {
      log("error", errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (!preview) {
    return <div className="empty">Preview an edit to see what it changes and how it moves the key outputs, before saving it.</div>;
  }
  return (
    <div data-testid="changes" style={{ height: "100%", overflow: "auto" }}>
      <BatchPreview state={state} result={preview.result} />
      <div className="row" style={{ padding: "0 12px 12px" }}>
        <button
          className="btn primary"
          disabled={busy || !preview.commit || readOnly}
          onClick={commit}
          title={readOnly ? "Read-only: make an editable copy to change it" : "Save this change as a new version"}
          data-testid="commit-preview"
        >
          Save
        </button>
        <button className="btn" onClick={() => setPreview(null)}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
