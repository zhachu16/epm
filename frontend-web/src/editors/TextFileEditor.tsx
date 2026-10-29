import { useEffect, useMemo, useRef, useState } from "react";
import type { Mode } from "../api/client";
import { useSubmitFunctions, useSubmitModel } from "../api/hooks";
import type { AppState, ModelJson } from "../api/types";
import { useStore } from "../store";
import { CodeEditor } from "./CodeEditor";
import { errorsOf, useDraft } from "./common";

/** How long typing has to pause before the text is saved. */
export const AUTOSAVE_DELAY = 2000;

/**
 * Text editing of model.json or functions.py; saving goes through the same validated ops.
 * Edits save themselves after a pause in typing. A draft the model rejects stays unsaved,
 * with its errors shown, until it is fixed.
 */
export function TextFileEditor({ state, file }: { state: AppState; file: "model.json" | "functions.py" }) {
  const source = useMemo(
    () => (file === "model.json" ? JSON.stringify(state.model, null, 2) + "\n" : state.functions_source),
    [file, state.model, state.functions_source],
  );
  const { draft, setDraft, dirty, revert, expectSave } = useDraft(source);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [autosave, setAutosave] = useState(true);
  const readOnly = useStore((s) => s.readOnly);
  const submitModel = useSubmitModel();
  const submitFunctions = useSubmitFunctions();
  const rejected = useRef<string | null>(null); // the draft last refused, not retried as is

  const submit = async (mode: Mode) => {
    setBusy(true);
    try {
      expectSave(mode === "commit");
      let result;
      if (file === "model.json") {
        let parsed: ModelJson;
        try {
          parsed = JSON.parse(draft);
        } catch (e) {
          setErrors([`model.json is not valid JSON: ${(e as Error).message}`]);
          expectSave(false);
          return;
        }
        result = await submitModel(parsed, mode);
      } else {
        result = await submitFunctions(draft, mode);
      }
      if (!result.committed) expectSave(false);
      if (mode === "commit" && !result.ok) rejected.current = draft;
      setErrors(errorsOf(result));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!autosave || readOnly || !dirty || busy || rejected.current === draft) return;
    const timer = window.setTimeout(() => void submit("commit"), AUTOSAVE_DELAY);
    return () => window.clearTimeout(timer);
    // submit is recreated every render; the draft and flags decide when to save
  }, [draft, autosave, readOnly, dirty, busy]);

  return (
    <div className="full-editor">
      <div className="bar">
        <span className="mono">{file}</span>
        <button className="btn small" disabled={!dirty || busy} onClick={() => submit("dry_run")} data-testid="preview">
          Preview
        </button>
        <button className="btn small primary" disabled={!dirty || busy || readOnly} onClick={() => submit("commit")} data-testid="save">
          Save
        </button>
        <button className="btn small" disabled={!dirty || busy} onClick={revert}>
          Revert
        </button>
        {!readOnly && (
          <label className="note" title="Save a new version after a pause in typing">
            <input type="checkbox" checked={autosave} onChange={(e) => setAutosave(e.target.checked)} data-testid="autosave" /> autosave
          </label>
        )}
        <span className="note">
          {file === "model.json"
            ? "Edits are checked like any other change; renames show up as delete + create."
            : "The whole file is replaced; formulas calling a removed function will fail validation."}
        </span>
      </div>
      {errors.length > 0 && (
        <div className="message error" style={{ margin: 8 }} data-testid="editor-errors">
          {errors.map((e, i) => (
            <div key={i}>{e}</div>
          ))}
        </div>
      )}
      <div className="body">
        <CodeEditor value={draft} language={file === "model.json" ? "json" : "python"} onChange={setDraft} testId={`file-${file}`} />
      </div>
    </div>
  );
}
