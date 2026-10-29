import { useMemo, useState } from "react";
import type { Mode } from "../api/client";
import { useSubmitFunctions, useSubmitModel } from "../api/hooks";
import type { AppState, ModelJson } from "../api/types";
import { CodeEditor } from "./CodeEditor";
import { errorsOf, useDraft } from "./common";

/** Text editing of model.json or functions.py; saving goes through the same validated ops. */
export function TextFileEditor({ state, file }: { state: AppState; file: "model.json" | "functions.py" }) {
  const source = useMemo(
    () => (file === "model.json" ? JSON.stringify(state.model, null, 2) + "\n" : state.functions_source),
    [file, state.model, state.functions_source],
  );
  const { draft, setDraft, dirty, revert, expectSave } = useDraft(source);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const submitModel = useSubmitModel();
  const submitFunctions = useSubmitFunctions();

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
      setErrors(errorsOf(result));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="full-editor">
      <div className="bar">
        <span className="mono">{file}</span>
        <button className="btn small" disabled={!dirty || busy} onClick={() => submit("dry_run")} data-testid="preview">
          Preview
        </button>
        <button className="btn small primary" disabled={!dirty || busy} onClick={() => submit("commit")} data-testid="save">
          Save
        </button>
        <button className="btn small" disabled={!dirty || busy} onClick={revert}>
          Revert
        </button>
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
